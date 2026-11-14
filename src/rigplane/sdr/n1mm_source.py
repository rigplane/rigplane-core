"""N1MM+ spectrum UDP receiver and passive scope source (MOR-4394).

Listens for N1MM+ spectrum datagrams; exposes the latest accepted packet.
Passive and radio-independent: never writes to a radio. Matches the
``ScopeSourcePlugin`` shape (PR #4565) by method names and signatures, not
by ``SourceKind`` membership: ``SourceKind`` is frozen without
``"n1mm_spectrum"``, so hub registration follows in a later MOR-4394 piece.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from typing import Literal

from rigplane.core._optional_deps import _require_numpy
from rigplane.scope.levels import AdaptiveLevelMapper
from rigplane.scope.models import (
    Coverage,
    FollowView,
    GrantedView,
    NativeFrame,
    SourceCapabilities,
    View,
    ViewRequest,
)
from rigplane.scope.n1mm_spectrum import (
    N1mmSpectrumPacket,
    N1mmSpectrumRejected,
    parse_n1mm_spectrum,
)

__all__ = ["N1mmSpectrumReceiver", "N1mmSpectrumSource"]

logger = logging.getLogger(__name__)

DEFAULT_PORT = 13064

# Latest-wins coalescing bound: while a burst is still being read, _flush
# defers delivery to let queued datagrams arrive; after this many deferrals
# it delivers anyway so continuous traffic still makes progress (delivery at
# least once every _MAX_COALESCE_TURNS + 1 turns).
_MAX_COALESCE_TURNS = 16

# N1MM levels × ScalingFactor are relative dB with no absolute reference,
# unlike the dBFS-tuned mapper defaults: open the absolute clamps wide
# (finite bounds for levels.window()'s min()/max()) and keep every default.
_RELATIVE_DB_MAPPER_KWARGS: dict[str, float] = {
    "abs_floor_min_db": -1e6,
    "abs_floor_max_db": 1e6,
    "abs_ceil_max_db": 1e6,
}

ReceiverFactory = Callable[
    [Callable[[N1mmSpectrumPacket], None]], "N1mmSpectrumReceiver"
]


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, receiver: N1mmSpectrumReceiver) -> None:
        self._receiver = receiver

    def datagram_received(self, data: bytes, addr: object) -> None:
        self._receiver._datagram(bytes(data))

    def error_received(self, exc: Exception) -> None:
        logger.debug("n1mm.spectrum.socket_error error=%r", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        self._receiver._connection_lost()


class N1mmSpectrumReceiver:
    """Asyncio UDP endpoint for N1MM+ spectrum datagrams with latest-wins."""

    def __init__(
        self,
        *,
        port: int = DEFAULT_PORT,
        host: str = "127.0.0.1",
        source_name: str | None = None,
        on_packet: Callable[[N1mmSpectrumPacket], None],
    ) -> None:
        self._port = port
        self._host = host
        self._source_name = source_name
        self._on_packet = on_packet
        self._transport: asyncio.DatagramTransport | None = None
        self._latest: N1mmSpectrumPacket | None = None
        self._flush_scheduled = False
        self._arrivals = 0
        self._flush_seen = 0
        self._flush_defers = 0
        self._lifecycle = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed_future: asyncio.Future[None] | None = None
        self.rejected_count = 0
        self.filtered_count = 0
        self.last_rejection: N1mmSpectrumRejected | None = None

    @property
    def port(self) -> int:
        """Actual bound port (tests bind port 0)."""
        if self._transport is not None:
            sock = self._transport.get_extra_info("socket")
            if sock is not None:
                return int(sock.getsockname()[1])
        return self._port

    async def start(self) -> None:
        await self._run(self._locked_start)

    async def stop(self) -> None:
        await self._run(self._locked_stop)

    async def _run(self, op: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.ensure_future(op())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await asyncio.shield(task)

    async def _locked_start(self) -> None:
        async with self._lifecycle:
            if self._transport is None:
                await self._do_start()

    async def _locked_stop(self) -> None:
        async with self._lifecycle:
            if self._transport is not None:
                await self._do_stop()

    async def _do_start(self) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self), local_addr=(self._host, self._port)
        )
        self._transport = transport
        self._closed_future = loop.create_future()

    async def _do_stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            if self._closed_future is not None:
                await self._closed_future
        self._transport = None
        self._closed_future = None
        self._flush_scheduled = False
        self._latest = None
        self._flush_defers = 0
        self._flush_seen = self._arrivals

    def _connection_lost(self) -> None:
        future, self._closed_future = self._closed_future, None
        if future is not None and not future.done():
            future.set_result(None)

    def _datagram(self, data: bytes) -> None:
        # Count every datagram, even rejected/filtered ones, so a gap cannot
        # make _flush treat the turn as quiet and deliver an earlier packet.
        self._arrivals += 1
        parsed = parse_n1mm_spectrum(data)
        if isinstance(parsed, N1mmSpectrumRejected):
            self.rejected_count += 1
            self.last_rejection = parsed
            logger.debug(
                "n1mm.spectrum.rejected reason=%s detail=%s",
                parsed.reason,
                parsed.detail,
            )
            return
        if self._source_name is not None and parsed.name != self._source_name:
            self.filtered_count += 1
            return
        self._latest = parsed
        if not self._flush_scheduled:
            self._flush_scheduled = True
            asyncio.get_running_loop().call_soon(self._flush)

    def _flush(self) -> None:
        current = self._arrivals
        if self._flush_defers == 0 or current != self._flush_seen:
            if self._flush_defers < _MAX_COALESCE_TURNS:
                self._flush_seen = current
                self._flush_defers += 1
                asyncio.get_running_loop().call_soon(self._flush)
                return
        self._flush_scheduled = False
        self._flush_defers = 0
        self._flush_seen = current
        packet, self._latest = self._latest, None
        if packet is None or self._transport is None:
            return
        try:
            self._on_packet(packet)
        except Exception:  # noqa: BLE001 — loop callbacks must not raise
            logger.exception("n1mm.spectrum.on_packet_failed")


class N1mmSpectrumSource:
    """Passive scope source over the latest N1MM+ spectrum packet."""

    kind: Literal["n1mm_spectrum"] = "n1mm_spectrum"

    def __init__(
        self,
        receiver_factory: ReceiverFactory | None = None,
        *,
        port: int = DEFAULT_PORT,
        host: str = "127.0.0.1",
        source_name: str | None = None,
        fps_limit: float = 10.0,
    ) -> None:
        if not math.isfinite(fps_limit) or fps_limit <= 0:
            raise ValueError("fps_limit must be a finite number > 0")
        _require_numpy()
        import numpy as np

        self._np = np
        self._fps_limit = float(fps_limit)
        if receiver_factory is None:
            receiver_factory = lambda on_packet: N1mmSpectrumReceiver(  # noqa: E731
                port=port, host=host, source_name=source_name, on_packet=on_packet
            )
        self._receiver_factory = receiver_factory
        self._receiver: N1mmSpectrumReceiver | None = None
        self._mapper = AdaptiveLevelMapper(**_RELATIVE_DB_MAPPER_KWARGS)
        self._coverage: Coverage | None = None
        self._native_width: int | None = None
        self._epoch = 0
        self._last_key: tuple[float, float, int] | None = None
        self._last_emit_s: float | None = None
        self._callbacks: list[Callable[[NativeFrame], None]] = []
        self._lifecycle = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._active = False
        self.dropped_nonfinite = 0

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            receivers=(None,),
            view_modes=("free",),
            span_choices_hz=(),
            native_width=self._native_width,
            max_fps=self._fps_limit,
            coverage_rule="sender-defined (N1MM spectrum packet edges)",
        )

    def resolve_view(self, view: ViewRequest, receiver: int | None = None) -> View:
        if receiver is not None:
            raise ValueError("n1mm spectrum has no receiver selector")
        if isinstance(view, FollowView):
            if self._coverage is None:
                raise ValueError("no spectrum received yet")
            middle = (self._coverage.start_hz + self._coverage.end_hz) / 2
            return View(middle - view.span_hz / 2, middle + view.span_hz / 2)
        return view

    def resolve_follow(
        self, view: FollowView, native: Coverage, receiver: int | None = None
    ) -> View:
        if receiver is not None:
            raise ValueError("n1mm spectrum has no receiver selector")
        if isinstance(view, View):
            return view
        middle = (native.start_hz + native.end_hz) / 2
        return View(middle - view.span_hz / 2, middle + view.span_hz / 2)

    def coverage_for(
        self, view: ViewRequest, receiver: int | None = None
    ) -> Coverage | None:
        if receiver is not None:
            raise ValueError("n1mm spectrum has no receiver selector")
        return self._coverage

    async def set_view(
        self, view: ViewRequest, receiver: int | None = None
    ) -> GrantedView:
        resolved = self.resolve_view(view, receiver)
        return GrantedView(resolved, self.coverage_for(resolved, receiver))

    async def start(self) -> None:
        await self._run(self._locked_start)

    async def stop(self, *, release: bool = True) -> None:
        await self._run(self._locked_stop)

    async def _run(self, op: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.ensure_future(op())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await asyncio.shield(task)

    async def _locked_start(self) -> None:
        async with self._lifecycle:
            if not self._active:
                await self._do_start()

    async def _locked_stop(self) -> None:
        async with self._lifecycle:
            if self._receiver is not None:
                await self._do_stop()

    async def _do_start(self) -> None:
        receiver = self._receiver_factory(self.handle_packet)
        await receiver.start()
        self._receiver = receiver
        self._active = True

    async def _do_stop(self) -> None:
        receiver = self._receiver
        if receiver is not None:
            await receiver.stop()
        self._receiver = None
        self._active = False

    def on_frame(self, callback: Callable[[NativeFrame], None]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def handle_packet(self, packet: N1mmSpectrumPacket) -> None:
        """Receiver callback seam; tests drive packets directly through it."""
        db = self._np.asarray(packet.levels, dtype=float) * packet.scaling_factor
        if not bool(self._np.all(self._np.isfinite(db))):
            self.dropped_nonfinite += 1
            logger.debug(
                "n1mm.spectrum.nonfinite scaling_factor=%r",
                packet.scaling_factor,
            )
            return
        key = (packet.low_hz, packet.high_hz, len(packet.levels))
        if key != self._last_key:
            if self._last_key is not None:
                self._epoch += 1
            self._last_key = key
            self._mapper.reset()
        self._coverage = Coverage(packet.low_hz, packet.high_hz)
        self._native_width = len(packet.levels)
        now = asyncio.get_running_loop().time()
        period = 1.0 / self._fps_limit
        if self._last_emit_s is not None and now - self._last_emit_s < period:
            return
        self._last_emit_s = now
        pixels = self._mapper.map(db)
        frame = NativeFrame(
            None,
            self._coverage,
            pixels,
            epoch=self._epoch,
            native_width=len(packet.levels),
        )
        for callback in tuple(self._callbacks):
            callback(frame)
