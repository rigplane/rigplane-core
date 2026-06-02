"""WebRTC ``RTCDataChannel`` adapter satisfying the :class:`Connection` seam.

aiortc DataChannels are *push* (``channel.on("message")`` fires for each
inbound message), but the web handlers are *pull* (``await recv()``). This
module bridges the two with an internal :class:`asyncio.Queue`:

* the ``message`` event handler enqueues ``(opcode, payload)`` tuples;
* :meth:`WebRtcDataChannelConnection.recv` dequeues them;
* a close sentinel makes ``recv`` raise :class:`EOFError` on clean close,
  mirroring ``WebSocketConnection``.

A single ordered/reliable ``control`` DataChannel is created per peer via
:func:`add_control_channel`; the resulting connection plugs into the
unchanged ``ControlHandler`` because it structurally satisfies the
``Connection`` protocol.

``aiortc`` is an optional dependency behind the ``[webrtc]`` extra. This
module therefore imports it lazily: importing the module never fails, and
:func:`webrtc_available` reports availability so callers can degrade
gracefully instead of crashing.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from ..websocket import WS_OP_BINARY, WS_OP_TEXT  # noqa: TID251

if TYPE_CHECKING:
    from aiortc import (  # type: ignore[import-not-found]
        RTCDataChannel,
        RTCPeerConnection,
    )

__all__ = [
    "WebRtcDataChannelConnection",
    "WebRtcUnavailableError",
    "add_control_channel",
    "webrtc_available",
]

_INSTALL_HINT: Final = "WebRTC backend unavailable; install rigplane[webrtc]."

# Sentinel enqueued on channel/PC close so a blocked recv() wakes and raises.
_CLOSE_SENTINEL: Final = object()


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
    """A :class:`Connection` backed by one ``aiortc.RTCDataChannel``.

    Wraps a *single* DataChannel and bridges its push-style ``message``
    events into the pull-style ``recv()`` the handlers expect. Inbound text
    maps to :data:`WS_OP_TEXT` and bytes to :data:`WS_OP_BINARY`, so the wire
    format is identical to the WebSocket transport.

    The channel must already be created on its ``RTCPeerConnection`` (its
    ``readyState`` may still be ``connecting``); inbound messages are queued
    until ``recv()`` consumes them.
    """

    def __init__(
        self,
        channel: RTCDataChannel,
        pc: RTCPeerConnection,
    ) -> None:
        self._channel = channel
        self._pc = pc
        self._queue: asyncio.Queue[tuple[int, bytes] | object] = asyncio.Queue()
        self._closed = False

        def _on_message(message: str | bytes) -> None:
            if isinstance(message, str):
                self._queue.put_nowait((WS_OP_TEXT, message.encode("utf-8")))
            else:
                self._queue.put_nowait((WS_OP_BINARY, bytes(message)))

        def _on_close() -> None:
            self._mark_closed()

        # aiortc's pyee emitter accepts (event, listener); register
        # imperatively so mypy keeps the handler signatures typed.
        channel.on("message", _on_message)  # type: ignore[no-untyped-call]
        channel.on("close", _on_close)  # type: ignore[no-untyped-call]

    def _mark_closed(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(_CLOSE_SENTINEL)

    async def recv(self) -> tuple[int, bytes]:
        """Receive the next message as ``(opcode, payload)``.

        Blocks until a message is available. Raises :class:`EOFError` on
        clean close (channel/PC closed), matching ``WebSocketConnection``.
        """
        if self._closed and self._queue.empty():
            raise EOFError("data channel closed")
        item = await self._queue.get()
        if item is _CLOSE_SENTINEL:
            raise EOFError("data channel closed")
        opcode, payload = item  # type: ignore[misc]
        return opcode, payload

    async def send_text(self, text: str) -> None:
        """Send a text message over the DataChannel."""
        self._channel.send(text)

    async def send_binary(self, data: bytes) -> None:
        """Send a binary message over the DataChannel."""
        self._channel.send(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the DataChannel and its peer connection.

        ``code`` and ``reason`` are accepted for protocol parity with the
        WebSocket transport; WebRTC has no equivalent close frame.
        """
        self._mark_closed()
        self._channel.close()
        await self._pc.close()

    def is_alive(self) -> bool:
        """True while the channel is open and the PC is not closed/failed."""
        if self._closed:
            return False
        return self._channel.readyState == "open" and self._pc.connectionState not in (
            "closed",
            "failed",
        )


def add_control_channel(pc: RTCPeerConnection) -> WebRtcDataChannelConnection:
    """Create the single ordered/reliable ``control`` channel on ``pc``.

    Returns a :class:`WebRtcDataChannelConnection` ready to hand to
    ``ControlHandler`` (or any consumer of the ``Connection`` seam).

    Raises :class:`WebRtcUnavailableError` if ``aiortc`` is not installed.
    """
    if not webrtc_available():
        raise WebRtcUnavailableError(_INSTALL_HINT)
    channel = pc.createDataChannel("control", ordered=True)
    return WebRtcDataChannelConnection(channel, pc)
