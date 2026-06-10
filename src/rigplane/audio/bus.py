"""AudioBus — pub/sub distribution for radio audio streams.

Provides a fan-out mechanism so multiple consumers (WebSocket broadcaster,
virtual audio bridge, WAV recorder, etc.) can independently subscribe to
the same audio stream from the radio.

Architecture::

    Radio ──(opus packets)──► AudioBus ──► subscriber 1 (browser)
                                       ──► subscriber 2 (BlackHole)
                                       ──► subscriber 3 (recorder)
                                       ──► ...

Usage::

    bus = AudioBus(radio)

    # Subscribe — returns an async context manager
    sub = bus.subscribe(name="wsjt-bridge")
    await sub.start()
    async for packet in sub:
        process(packet)
    sub.stop()

    # Or with async context manager:
    async with bus.subscribe(name="recorder") as sub:
        async for packet in sub:
            save(packet)

Lifecycle:
    - First subscriber triggers ``radio.start_rx()`` (or the legacy
      ``radio.start_audio_rx_opus()`` for radios without the neutral
      AudioTransport surface)
    - Last subscriber removal triggers ``radio.stop_rx()`` (or the legacy
      ``radio.stop_audio_rx_opus()``)
    - Subscribers can join/leave at any time
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import TYPE_CHECKING, Any

from rigplane.audio.usb_driver import AudioAlreadyStartedError
from rigplane.dsp.tap_registry import TapRegistry

if TYPE_CHECKING:
    from rigplane.audio import AudioPacket

logger = logging.getLogger(__name__)

__all__ = ["STAGE_RX_PCM", "STAGE_RX_POST_DSP", "AudioBus", "AudioSubscription"]

# ── Named RX tap stages (MOR-565, ADR §3.7 "observable by construction") ─────
#
# Uniform stage-naming scheme for passive PCM tap points along the RX tract.
# Only stages with a live frame source today have an instantiated
# :class:`~rigplane.dsp.tap_registry.TapRegistry`:
#
#   ``rx.pcm``      — radio-native RX frames at the AudioBus fan-out
#                     (post jitter-buffer/decode by the radio transport,
#                     pre-DSP). Hosted on :class:`AudioBus`.
#   ``rx.post_dsp`` — decoded PCM16 after the broadcaster's optional DSP
#                     pipeline. Hosted on ``AudioBroadcaster``
#                     (``rigplane.web.handlers.audio``) — this is the
#                     pre-existing ``_tap_registry``, renamed into the scheme.
#
# Reserved stage names (documented, NOT instantiated — no producer yet):
# ``rx.raw`` (pre jitter-buffer wire payload), ``rx.egress`` (per-consumer
# transport frames), ``tx.pcm`` / ``tx.egress`` (TX-side mirror stages).
# Asking a host for a reserved stage raises ``KeyError`` by design.
STAGE_RX_PCM = "rx.pcm"
STAGE_RX_POST_DSP = "rx.post_dsp"

_DEFAULT_QUEUE_SIZE = 64
_DEFAULT_CLOSE_TIMEOUT = 2.0


def _accepts_jitter_depth(start_rx: Any) -> bool:
    """True when *start_rx* accepts a ``jitter_depth`` keyword argument.

    The neutral ``AudioTransport.start_rx`` is minimal (callback only); the
    LAN mixin widens it with a keyword-optional ``jitter_depth`` (MOR-539)
    while the serial and Yaesu implementations do not (MOR-540/541).
    """
    try:
        parameters = inspect.signature(start_rx).parameters
    except (TypeError, ValueError):  # pragma: no cover — exotic callables
        return False
    if "jitter_depth" in parameters:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())


class AudioSubscription:
    """A single subscriber to the audio bus.

    Receives copies of every audio packet via an internal asyncio.Queue.
    Can be iterated with ``async for`` or read manually with :meth:`get`.

    Args:
        bus: Parent AudioBus.
        name: Human-readable subscriber name (for logging).
        queue_size: Maximum buffered packets before dropping.
    """

    def __init__(
        self,
        bus: AudioBus,
        name: str = "",
        queue_size: int = _DEFAULT_QUEUE_SIZE,
    ) -> None:
        self._bus = bus
        self.name = name or f"sub-{id(self):x}"
        self._queue: asyncio.Queue[AudioPacket | None] = asyncio.Queue(
            maxsize=queue_size,
        )
        self._active = False
        self._close_task: asyncio.Task[None] | None = None
        self._dropped = 0
        self._received = 0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "active": self._active,
            "received": self._received,
            "dropped": self._dropped,
            "queued": self._queue.qsize(),
        }

    async def start(self) -> None:
        """Activate this subscription (registers with the bus).

        Raises if this subscription's demand triggers the radio RX start
        and the start fails (MOR-582): the subscription is left inactive
        and unregistered — never "attached" to a bus that is not receiving.
        """
        if self._active:
            return
        if self._close_task is not None and not self._close_task.done():
            await self._close_task
        self._active = True
        try:
            await self._bus._add_subscriber(self)
        except BaseException:
            self._active = False
            raise

    def stop(self) -> None:
        """Deactivate this subscription and schedule bus removal.

        This method is intentionally synchronous for backward compatibility.
        Prefer :meth:`aclose` in async teardown paths when callers need to know
        removal has completed.
        """
        if not self._active:
            return
        self._active = False
        self._schedule_close()

    async def aclose(self, timeout: float | None = _DEFAULT_CLOSE_TIMEOUT) -> None:
        """Deactivate this subscription and await bus removal.

        Args:
            timeout: Maximum seconds to wait for teardown. ``None`` disables
                the timeout for callers that intentionally want unbounded
                cleanup.
        """
        if not self._active and self._close_task is None:
            return
        self._active = False

        task = self._close_task
        if task is None:
            close_coro = self._bus._remove_subscriber(self)
            if timeout is None:
                await close_coro
            else:
                try:
                    await asyncio.wait_for(close_coro, timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        "audio-bus: timed out closing subscriber %r",
                        self.name,
                    )
            return

        try:
            if timeout is None:
                await task
            else:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "audio-bus: timed out closing subscriber %r",
                self.name,
            )
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _schedule_close(self) -> None:
        """Schedule async bus removal for synchronous ``stop()`` callers."""
        if self._close_task is not None and not self._close_task.done():
            return
        task = asyncio.create_task(self._bus._remove_subscriber(self))
        self._close_task = task
        task.add_done_callback(self._on_close_done)

    def _on_close_done(self, task: asyncio.Task[None]) -> None:
        if self._close_task is task:
            self._close_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug(
                "audio-bus: subscriber close task failed",
                exc_info=True,
            )

    def deliver(self, packet: AudioPacket | None) -> None:
        """Called by the bus to deliver a packet (non-blocking)."""
        if not self._active:
            return
        self._received += 1
        try:
            self._queue.put_nowait(packet)
        except asyncio.QueueFull:
            # Drop oldest, enqueue newest (sliding window)
            self._dropped += 1
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(packet)
            except asyncio.QueueFull:
                pass

    async def get(self, timeout: float | None = None) -> AudioPacket | None:
        """Get the next packet (blocks until available or timeout)."""
        if timeout is not None:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        return await self._queue.get()

    def get_nowait(self) -> AudioPacket | None:
        """Get a packet without blocking (raises QueueEmpty)."""
        return self._queue.get_nowait()

    def __aiter__(self) -> AudioSubscription:
        return self

    async def __anext__(self) -> AudioPacket | None:
        if not self._active and self._queue.empty():
            raise StopAsyncIteration
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=1.0)
        except asyncio.CancelledError:
            raise StopAsyncIteration
        except asyncio.TimeoutError:
            if not self._active:
                raise StopAsyncIteration
            # Still active — just no data yet, keep iterating
            return None

    async def __aenter__(self) -> AudioSubscription:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


class AudioBus:
    """Fan-out distribution bus for radio audio packets.

    Args:
        radio: A radio instance implementing AudioCapable.
        jitter_depth: Jitter buffer depth passed to radio RX start.
    """

    def __init__(self, radio: Any, *, jitter_depth: int = 5) -> None:
        self._radio = radio
        self._jitter_depth = jitter_depth
        self._subscribers: list[AudioSubscription] = []
        self._rx_active = False
        self._lock = asyncio.Lock()
        self._last_rx_frame_monotonic: float | None = None
        # Named RX tap stages (MOR-565): the bus hosts ``rx.pcm`` — a passive
        # observer of radio-native frames, fed alongside (never instead of)
        # the subscriber fan-out. Local-only, no telemetry (open-core).
        self._rx_pcm_taps = TapRegistry()
        self._stage_taps: dict[str, TapRegistry] = {STAGE_RX_PCM: self._rx_pcm_taps}

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def rx_active(self) -> bool:
        return self._rx_active

    @property
    def last_rx_frame_monotonic(self) -> float | None:
        """``time.monotonic()`` of the most recent RX fan-out, or None.

        RX liveness heartbeat (MOR-564): observability only, no watchdog.
        """
        return self._last_rx_frame_monotonic

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "rx_active": self._rx_active,
            "subscriber_count": len(self._subscribers),
            "last_rx_frame_monotonic": self._last_rx_frame_monotonic,
            "subscribers": [s.stats for s in self._subscribers],
        }

    def subscribe(
        self,
        name: str = "",
        queue_size: int = _DEFAULT_QUEUE_SIZE,
    ) -> AudioSubscription:
        """Create a new subscription (not yet active — call start() or use as context manager)."""
        return AudioSubscription(self, name=name, queue_size=queue_size)

    def taps(self, stage: str) -> TapRegistry:
        """Return the :class:`TapRegistry` for a named RX stage on this bus.

        Only ``STAGE_RX_PCM`` is hosted here; reserved stage names raise
        ``KeyError``. Taps are attachable/detachable at runtime and add no
        cost when empty (the registry no-ops without subscribers).
        """
        return self._stage_taps[stage]

    def _on_opus_packet(self, packet: "AudioPacket | None") -> None:
        """Internal callback — distributes packet to all active subscribers."""
        self._last_rx_frame_monotonic = time.monotonic()
        for sub in self._subscribers:
            sub.deliver(packet)
        # ``rx.pcm`` stage tap (MOR-565): passive observer of radio-native
        # frames, fed after subscriber delivery so the hot path is untouched.
        # The empty-registry check keeps the no-tap cost to one attribute read.
        if packet is not None and self._rx_pcm_taps.active:
            self._rx_pcm_taps.feed(packet.data)

    async def _add_subscriber(self, sub: AudioSubscription) -> None:
        """Register a subscriber and start RX if this is the first one.

        When this subscriber's demand triggers the RX start and the start
        fails, the registration is rolled back before the error propagates
        (MOR-582, ADR §3.4 rule 3): a subscriber must never be left
        registered on a bus that is not actually receiving.
        """
        async with self._lock:
            added = sub not in self._subscribers
            if added:
                self._subscribers.append(sub)
                logger.info(
                    "audio-bus: +subscriber %r (%d total)",
                    sub.name,
                    len(self._subscribers),
                )
            if not self._rx_active and len(self._subscribers) > 0:
                try:
                    await self._start_rx()
                except BaseException:
                    if added:
                        self._subscribers.remove(sub)
                    raise

    async def _remove_subscriber(self, sub: AudioSubscription) -> None:
        """Unregister a subscriber. Stops RX if no subscribers remain."""
        async with self._lock:
            try:
                self._subscribers.remove(sub)
            except ValueError:
                pass
            logger.info(
                "audio-bus: -subscriber %r (%d remaining)",
                sub.name,
                len(self._subscribers),
            )
            if self._rx_active and len(self._subscribers) == 0:
                # Stop RX synchronously while holding the lock
                # to prevent new subscribers from seeing a dying stream
                await self._stop_rx()

    async def _begin_rx(self) -> None:
        """Invoke radio RX start, preferring the neutral AudioTransport surface.

        Falls back to the legacy ``start_audio_rx_opus`` for radios that only
        implement the Opus-specific surface (third-party/Pro radios, MOR-542).
        ``jitter_depth`` is threaded through only when the implementation
        accepts it — the minimal ``AudioTransport.start_rx`` takes the
        callback alone.
        """
        start_rx = getattr(self._radio, "start_rx", None)
        if start_rx is None:
            await self._radio.start_audio_rx_opus(
                self._on_opus_packet,
                jitter_depth=self._jitter_depth,
            )
        elif _accepts_jitter_depth(start_rx):
            await start_rx(self._on_opus_packet, jitter_depth=self._jitter_depth)
        else:
            await start_rx(self._on_opus_packet)

    async def _start_rx(self) -> None:
        """Start receiving audio from the radio.

        A start failure is NOT swallowed (MOR-582, ADR §3.4 rule 3): the
        demanding caller must never believe it is attached to a live
        stream. The radio error is chained into a ``RuntimeError`` so every
        backend surfaces the same exception type to subscribers;
        ``rx_active`` stays False, reflecting reality.
        """
        if self._rx_active:
            return
        try:
            await self._begin_rx()
        except Exception as exc:
            logger.exception("audio-bus: failed to start RX")
            raise RuntimeError(f"radio RX failed to start: {exc}") from exc
        self._rx_active = True
        logger.info("audio-bus: RX started")

    async def restart_rx(self) -> None:
        """Re-establish RX on the radio using the bus's own callback.

        Used after a half-duplex TX cycle (e.g. the web poller's PTT-off
        transition on Icom CI-V backends): the radio's single-slot RX
        callback must be restored to :meth:`_on_opus_packet` so subscribers
        keep receiving frames. No-op when the bus has no active subscribers.

        A re-arm failure is non-fatal for the established session (a hiccup
        must not crash healthy subscribers) but never masked as success:
        ``rx_active`` drops to False so stats and the recovery watchdog see
        the dead RX leg (MOR-582). The typed already-started case is benign
        — RX stayed live through the TX cycle (LAN) and remains wired to
        this bus, so it stays marked live.
        """
        async with self._lock:
            if not self._subscribers:
                return
            try:
                await self._begin_rx()
            except AudioAlreadyStartedError:
                self._rx_active = True
                logger.debug("audio-bus: RX already live on re-arm", exc_info=True)
                return
            except Exception:
                self._rx_active = False
                logger.exception("audio-bus: failed to re-arm RX")
                return
            self._rx_active = True
            logger.info("audio-bus: RX re-armed after TX")

    async def _stop_rx(self) -> None:
        """Stop receiving audio from the radio."""
        if not self._rx_active:
            return
        self._rx_active = False
        try:
            stop_rx = getattr(self._radio, "stop_rx", None)
            if stop_rx is not None:
                await stop_rx()
            else:
                await self._radio.stop_audio_rx_opus()
            logger.info("audio-bus: RX stopped")
        except Exception:
            logger.debug("audio-bus: stop RX error", exc_info=True)

    async def stop(self) -> None:
        """Stop the bus and all subscribers."""
        for sub in list(self._subscribers):
            await sub.aclose()
        if self._rx_active:
            await self._stop_rx()
