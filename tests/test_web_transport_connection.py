"""Tests for the ``Connection`` transport protocol (MOR-274).

Pure-typing seam: these tests prove that

1. the concrete ``WebSocketConnection`` structurally satisfies ``Connection``;
2. a *minimal* fake implementing only the protocol surface satisfies it; and
3. a real handler (``ScopeHandler``) drives correctly against that minimal
   fake — i.e. the handlers truly depend only on the protocol surface.

No ``MagicMock`` is used for the connection: per the project rule
"MagicMock hides signature bugs", the fake is a real class whose method
signatures match the protocol exactly.
"""

from __future__ import annotations

import asyncio

import pytest

from rigplane.web.handlers.scope import ScopeHandler
from rigplane.web.transport import Connection
from rigplane.web.websocket import WS_OP_TEXT, WebSocketConnection


class FakeConnection:
    """Minimal in-memory connection implementing exactly the protocol.

    Signatures mirror ``Connection`` precisely (including ``close`` defaults).
    ``recv`` yields scripted ``(opcode, payload)`` tuples, then raises
    ``EOFError`` to signal a clean close — exactly like ``WebSocketConnection``.
    """

    def __init__(self, incoming: list[tuple[int, bytes]] | None = None) -> None:
        self._incoming = list(incoming or [])
        self.sent_text: list[str] = []
        self.sent_binary: list[bytes] = []
        self.close_calls: list[tuple[int, str]] = []
        self._alive = True

    async def recv(self) -> tuple[int, bytes]:
        if self._incoming:
            return self._incoming.pop(0)
        raise EOFError("connection closed")

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_binary(self, data: bytes) -> None:
        self.sent_binary.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive


def _takes(conn: Connection) -> Connection:
    """mypy-level structural check: anything passed must satisfy Connection."""
    return conn


def test_websocket_connection_satisfies_connection_protocol() -> None:
    # Structural (runtime) check: the concrete impl satisfies the protocol
    # without any subclassing or registration.
    assert issubclass(WebSocketConnection, Connection)


def test_fake_connection_satisfies_connection_protocol() -> None:
    fake = FakeConnection()
    assert isinstance(fake, Connection)
    # And it flows through a Connection-typed callable (mypy + runtime).
    assert _takes(fake) is fake


@pytest.mark.asyncio
async def test_scope_handler_recv_loop_runs_against_minimal_fake() -> None:
    """ScopeHandler.run() drives recv() on the minimal fake and exits cleanly.

    Feed one text control frame, then let ``recv`` raise ``EOFError`` to end
    the loop — proving the receive side needs nothing beyond ``recv``.
    """
    fake = FakeConnection(incoming=[(WS_OP_TEXT, b'{"type":"hello"}')])
    handler = ScopeHandler(fake, radio=None, server=None)

    await asyncio.wait_for(handler.run(), timeout=2.0)

    # Loop consumed the scripted frame and exited on EOFError without error.
    assert fake._incoming == []


@pytest.mark.asyncio
async def test_scope_handler_sender_uses_only_send_binary() -> None:
    """ScopeHandler._sender delivers queued frames via send_binary only.

    Drive the sender coroutine directly against the minimal fake: enqueue a
    pre-encoded frame, let it flush, then cancel. The frame must arrive via
    ``send_binary`` — proving the send side depends only on the protocol.
    """
    fake = FakeConnection()
    handler = ScopeHandler(fake, radio=None, server=None)
    handler._frame_queue.put_nowait(b"\x00\x01scope-bytes")

    sender = asyncio.create_task(handler._sender())
    # Yield until the frame has been dequeued and sent.
    for _ in range(100):
        await asyncio.sleep(0)
        if fake.sent_binary:
            break
    sender.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sender

    assert fake.sent_binary == [b"\x00\x01scope-bytes"]
