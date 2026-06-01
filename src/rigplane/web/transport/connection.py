"""The :class:`Connection` transport protocol.

A structural (PEP 544) protocol describing the minimal full-duplex message
surface the web WebSocket handlers (``control`` / ``scope`` / ``audio``)
depend on. It is intentionally minimal: it declares *only* the methods the
handlers actually call, so any concrete transport that implements these
satisfies it with no edits.

``WebSocketConnection`` (``rigplane.web.websocket``) already exposes exactly
this surface and therefore satisfies ``Connection`` structurally, without any
explicit subclassing or registration. This is a pure typing seam — there is
no behaviour here.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Connection(Protocol):
    """Transport-agnostic full-duplex message connection.

    The surface mirrors ``WebSocketConnection`` exactly for the members the
    handlers use. Signatures (including defaults) match the concrete
    implementation so it structurally satisfies this protocol unchanged.
    """

    async def recv(self) -> tuple[int, bytes]:
        """Receive the next complete message as ``(opcode, payload)``.

        Raises ``EOFError`` on clean close.
        """
        ...

    async def send_text(self, text: str) -> None:
        """Send a text message."""
        ...

    async def send_binary(self, data: bytes) -> None:
        """Send a binary message."""
        ...

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the connection with the given status code and reason."""
        ...

    def is_alive(self) -> bool:
        """True if the connection is open and considered healthy."""
        ...
