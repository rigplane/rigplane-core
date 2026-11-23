"""WebRTC ``RTCDataChannel`` adapter satisfying the :class:`Connection` seam.

aiortc DataChannels are *push* (``channel.on("message")`` fires for each
inbound message), but the web handlers are *pull* (``await recv()``). This
module bridges the two with an internal :class:`asyncio.Queue`:

* the ``message`` event handler enqueues ``(opcode, payload)`` tuples;
* :meth:`WebRtcDataChannelConnection.recv` dequeues them;
* a close sentinel makes ``recv`` raise :class:`EOFError` on clean close,
  mirroring ``WebSocketConnection``.

The channel set is the typed K5 contract
(:data:`rigplane.web.channel_attach.DATA_CHANNELS`). ``control`` and ``api``
are one ordered/reliable channel each (:func:`add_control_channel`,
:func:`add_api_channel`). ``scope`` and ``audio`` are SPLIT: binary frames
ride an unordered ``maxRetransmits=0`` channel (time-sensitive, they
tolerate loss) and text frames ride a reliable ordered ``*-text``
companion (commands such as ``audio_start`` and the attach frame must
never be lost). :class:`WebRtcDataChannelConnection` merges the pair into
the ONE ``Connection`` the unchanged ``ScopeHandler`` / ``AudioHandler``
see: ``send_text`` goes to the companion, ``send_binary`` to the lossy
channel, and ``recv`` yields from both (:func:`add_scope_channel` /
:func:`add_audio_channel` build the pair on the offerer side; the answerer
pairs the two inbound channels, one pending half per kind and carriage).

``aiortc`` is an optional dependency behind the ``[webrtc]`` extra. This
module therefore imports it lazily: importing the module never fails, and
:func:`webrtc_available` reports availability so callers can degrade
gracefully instead of crashing.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Final

from ...ws import WS_OP_BINARY, WS_OP_TEXT  # noqa: TID251
from ..channel_attach import (  # noqa: TID251
    RIGCTL_MAX_FRAME_BYTES,
    DataChannelSpec,
    data_channels_for,
)

if TYPE_CHECKING:
    from aiortc import (  # type: ignore[import-not-found]
        RTCDataChannel,
        RTCPeerConnection,
    )

    from .webrtc_contract import WebRtcTransportStatus  # noqa: TID251

__all__ = [
    "WebRtcDataChannelConnection",
    "WebRtcUnavailableError",
    "add_api_channel",
    "add_audio_channel",
    "add_control_channel",
    "add_data_channel",
    "add_scope_channel",
    "webrtc_available",
]

_INSTALL_HINT: Final = "WebRTC backend unavailable; install rigplane[webrtc]."

# Sentinel enqueued on channel/PC close so a blocked recv() wakes and raises.
_CLOSE_SENTINEL: Final = object()

#: Frames a connection queues before it is served (waiting for its attach,
#: its split partner or its session's control attach). A well-behaved
#: client sends one attach and waits for the ack; more than this many
#: unread frames ends the connection instead of growing the queue.
MAX_FRAMES_BEFORE_SERVE: Final = 64
_RIGCTL_SEND_TIMEOUT_S: Final = 1.0

logger = logging.getLogger(__name__)


class WebRtcUnavailableError(RuntimeError):
    """Raised when WebRTC is requested but ``aiortc`` is not installed."""


def webrtc_available() -> bool:
    """Return True if the ``aiortc`` optional dependency is importable."""
    try:
        import aiortc  # noqa: F401
    except ImportError:
        return False
    return True


class WebRtcDataChannelConnection:
    """A :class:`Connection` backed by one data channel, or a split pair.

    Bridges push-style ``message`` events into the pull-style ``recv()``
    the handlers expect. Inbound text maps to :data:`WS_OP_TEXT` and bytes
    to :data:`WS_OP_BINARY`, so the wire format is identical to the
    WebSocket transport.

    With ``text_channel`` (a split ``scope``/``audio`` kind), ``channel``
    is the lossy binary channel and ``text_channel`` its reliable ordered
    companion: ``send_text`` goes to the companion, ``send_binary`` to the
    lossy channel, ``recv`` yields frames from both in arrival order, and
    the connection ends when either channel closes. The two channels keep
    no order between each other; only frames on one channel stay ordered.

    The channels must already be created on their ``RTCPeerConnection``
    (``readyState`` may still be ``connecting``); inbound messages are
    queued until ``recv()`` consumes them.
    """

    def __init__(
        self,
        channel: RTCDataChannel,
        pc: RTCPeerConnection,
        *,
        text_channel: RTCDataChannel | None = None,
    ) -> None:
        self._channel = channel
        self._text_channel = channel if text_channel is None else text_channel
        self._pc = pc
        self._queue: asyncio.Queue[tuple[int, bytes] | object] = asyncio.Queue()
        self._closed = False
        self._rigctl_attach_pending = False
        self._rigctl_send_ready = asyncio.Event() if channel.label == "rigctl" else None
        if self._rigctl_send_ready is not None:
            channel.on("bufferedamountlow", self._rigctl_send_ready.set)
        # Until served, unread frames are bounded (MAX_FRAMES_BEFORE_SERVE).
        self.serving = False
        for source in self._channels():
            self._listen(source)

    def _listen(self, source: RTCDataChannel) -> None:
        """Queue every message of ``source``; its close ends the connection."""

        def _on_message(message: str | bytes) -> None:
            if self._closed:
                return
            rigctl = self._rigctl_send_ready is not None
            if rigctl and (
                self._rigctl_attach_pending or len(message) > RIGCTL_MAX_FRAME_BYTES
            ):
                self.abort()
                return
            if (
                rigctl or not self.serving
            ) and self._queue.qsize() >= MAX_FRAMES_BEFORE_SERVE:
                logger.info(
                    "WebRTC: closing %r with a full receive queue", source.label
                )
                self.abort()
                return
            opcode = WS_OP_TEXT if isinstance(message, str) else WS_OP_BINARY
            payload = (
                message.encode("utf-8") if isinstance(message, str) else bytes(message)
            )
            if rigctl and len(payload) > RIGCTL_MAX_FRAME_BYTES:
                self.abort()
                return
            self._queue.put_nowait((opcode, payload))

        def _on_close() -> None:
            self._mark_closed()

        # aiortc's pyee emitter accepts (event, listener); register
        # imperatively so mypy keeps the handler signatures typed.
        source.on("message", _on_message)  # type: ignore[no-untyped-call]
        source.on("close", _on_close)  # type: ignore[no-untyped-call]

    def begin_rigctl_admission(self) -> bool:
        """The offerer must wait for the attach result before sending CAT."""
        self._rigctl_attach_pending = True
        return self._queue.empty()

    def add_partner(self, partner: RTCDataChannel, *, partner_is_text: bool) -> None:
        """Complete a split pair on the answerer side.

        The connection was built from the half that arrived first, so its
        messages (the attach frame included) were queued from the moment
        it arrived; ``partner`` is the other half of the same pair.
        """
        if partner_is_text:
            self._text_channel = partner
        else:
            self._channel = partner
        self._listen(partner)

    def _channels(self) -> tuple[RTCDataChannel, ...]:
        """The underlying channel(s): one, or the binary + text pair."""
        if self._text_channel is self._channel:
            return (self._channel,)
        return (self._channel, self._text_channel)

    def _mark_closed(self) -> None:
        if not self._closed:
            self._closed = True
            if self._rigctl_send_ready is not None:
                self._rigctl_send_ready.set()
                while not self._queue.empty():
                    self._queue.get_nowait()
            self._queue.put_nowait(_CLOSE_SENTINEL)

    async def recv(self) -> tuple[int, bytes]:
        """Receive the next message as ``(opcode, payload)``.

        Blocks until a message is available. Raises :class:`EOFError` on
        clean close (channel/PC closed), matching ``WebSocketConnection``.
        """
        if self._closed and self._queue.empty():
            raise EOFError("data channel closed")
        item = await self._queue.get()
        if item is _CLOSE_SENTINEL or (
            self._closed and self._rigctl_send_ready is not None
        ):
            raise EOFError("data channel closed")
        opcode, payload = item  # type: ignore[misc]
        return opcode, payload

    async def send_text(self, text: str) -> None:
        """Send a text message (on the reliable companion when split)."""
        ready = self._rigctl_send_ready
        if ready is not None:
            if len(text.encode("utf-8")) > RIGCTL_MAX_FRAME_BYTES:
                self.abort()
                raise OSError("oversize rigctl reply")
            try:
                await self._drain_rigctl(ready, RIGCTL_MAX_FRAME_BYTES)
            except TimeoutError as exc:
                self.abort()
                raise OSError("rigctl send buffer stalled") from exc
            if not self.is_alive():
                raise EOFError("rigctl data channel closed")
        self._text_channel.send(text)
        self._rigctl_attach_pending = False

    async def _drain_rigctl(
        self, ready: asyncio.Event, limit: int, timeout: float = _RIGCTL_SEND_TIMEOUT_S
    ) -> None:
        self._channel.bufferedAmountLowThreshold = limit
        async with asyncio.timeout(timeout):
            while self._channel.readyState == "open" and self.buffered_bytes() > limit:
                ready.clear()
                await ready.wait()

    async def send_binary(self, data: bytes) -> None:
        """Send a binary message (on the lossy channel when split)."""
        self._channel.send(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close ONLY this connection's DataChannel (MOR-4498).

        The peer connection outlives each of its channels: its single
        owner — the WebRTC session manager — closes it (``_Session.close``,
        where ``pc.close()`` stays shielded per MOR-1431) when the peer
        itself ends. Closing one channel closes exactly its own stream,
        like ``abort()`` and like a WebSocket ``close()`` closes one
        socket; sibling channels on the same peer keep working.

        ``code`` and ``reason`` are accepted for protocol parity with the
        WebSocket transport; WebRTC has no equivalent close frame.
        """
        self._mark_closed()
        try:
            ready = self._rigctl_send_ready
            if ready is not None:
                # Put the final attach refusal on SCTP before stream reset.
                with suppress(TimeoutError):
                    await self._drain_rigctl(ready, 0, 0.25)
        finally:
            for source in self._channels():
                source.close()

    def abort(self) -> None:
        """Synchronously close just this connection, without waiting for sends."""
        self._mark_closed()
        for source in self._channels():
            try:
                source.close()
            except Exception:
                pass

    def is_alive(self) -> bool:
        """True while the channel is open and the PC is not closed/failed."""
        if self._closed:
            return False
        return all(
            source.readyState == "open" for source in self._channels()
        ) and self._pc.connectionState not in ("closed", "failed")

    def buffered_bytes(self) -> int:
        """Unsent bytes the data channel still buffers (backpressure source).

        Reports aiortc's ``bufferedAmount``; anything unrecognized (a
        channel without that surface) reads as 0, i.e. no backpressure.
        """
        amount = getattr(self._channel, "bufferedAmount", 0)
        return amount if isinstance(amount, int) else 0

    def set_buffer_low_watermark(self, value: int) -> None:
        """Request the drain-resume mark (best effort, aiortc may ignore it)."""
        try:
            self._channel.bufferedAmountLowThreshold = value
        except (AttributeError, TypeError):
            pass


def _create_channel(pc: RTCPeerConnection, spec: DataChannelSpec) -> RTCDataChannel:
    """Create one channel of the K5 set exactly as its spec configures it."""
    if spec.max_retransmits is None:
        return pc.createDataChannel(spec.label, ordered=spec.ordered)
    return pc.createDataChannel(
        spec.label, ordered=spec.ordered, maxRetransmits=spec.max_retransmits
    )


def add_data_channel(
    pc: RTCPeerConnection,
    kind: str,
    *,
    transport_status: WebRtcTransportStatus | None = None,
) -> WebRtcDataChannelConnection:
    """Create the channel(s) of one kind on ``pc`` (the offerer side).

    ``control``/``api`` get their one ordered/reliable channel; a split
    kind (``scope``/``audio``) gets its lossy binary channel plus its
    reliable text companion, merged into one connection. Raises
    :class:`WebRtcUnavailableError` if ``aiortc`` is not installed and
    :class:`ValueError` for a kind outside the channel set. Rigctl requires
    capabilities from this peer and opens after the primary control attach.
    """
    if kind == "rigctl" and (
        transport_status is None or transport_status.select_rigctl_version() is None
    ):
        raise WebRtcUnavailableError("peer has no compatible rigctl version")
    if not webrtc_available():
        raise WebRtcUnavailableError(_INSTALL_HINT)
    specs = {spec.carries: spec for spec in data_channels_for(kind)}
    if "all" in specs:
        return WebRtcDataChannelConnection(_create_channel(pc, specs["all"]), pc)
    if "binary" not in specs or "text" not in specs:
        raise ValueError(f"no data channel for kind {kind!r}")
    binary = _create_channel(pc, specs["binary"])
    text = _create_channel(pc, specs["text"])
    return WebRtcDataChannelConnection(binary, pc, text_channel=text)


def add_control_channel(pc: RTCPeerConnection) -> WebRtcDataChannelConnection:
    """Create the single ordered/reliable ``control`` channel on ``pc``."""
    return add_data_channel(pc, "control")


def add_api_channel(pc: RTCPeerConnection) -> WebRtcDataChannelConnection:
    """Create the single ordered/reliable ``api`` channel on ``pc``.

    REST over the channel multiplexes independent requests, so (like
    ``control``) it is ordered and fully reliable.
    """
    return add_data_channel(pc, "api")


def add_scope_channel(pc: RTCPeerConnection) -> WebRtcDataChannelConnection:
    """Create the split ``scope`` pair on ``pc``: lossy ``scope`` for the
    binary frames, reliable ``scope-text`` for the text frames."""
    return add_data_channel(pc, "scope")


def add_audio_channel(pc: RTCPeerConnection) -> WebRtcDataChannelConnection:
    """Create the split ``audio`` pair on ``pc``: lossy ``audio`` for the
    binary frames, reliable ``audio-text`` for the text frames."""
    return add_data_channel(pc, "audio")
