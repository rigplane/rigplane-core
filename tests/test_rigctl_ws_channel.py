# mypy: disable-error-code=untyped-decorator
"""MOR-3536: the rigctl WebSocket channel — framing and the rigctld seam.

The channel's transport (``web/handlers/rigctl.py``) runs against the real
``RigctldServer`` connection loop with an injected protocol and handler, the
way ``tests/test_rigctld_server.py`` does: only the radio side is faked.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import RigctldCommand, RigctldConfig, RigctldResponse
from rigplane.rigctld.server import RigctldServer
from rigplane.web.handlers.rigctl import RigctlChannelHandler, _LineReader
from rigplane.web.transport import Connection
from rigplane.ws import WS_OP_BINARY, WS_OP_TEXT

_OWNER = "controller-session-0123456789abcdef"
_PEER = ("203.0.113.5", 40000)


class _FakeWs:
    """A WebSocket: ``inbox`` feeds ``recv``; ``None`` there is the close."""

    def __init__(self) -> None:
        self.inbox: asyncio.Queue[tuple[int, bytes] | None] = asyncio.Queue()
        self.sent: list[str] = []
        self.close_code: int | None = None

    async def recv(self) -> tuple[int, bytes]:
        item = await self.inbox.get()
        if item is None:
            raise EOFError("WebSocket closed")
        return item

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.close_code is None:
            self.close_code = code
        self.inbox.put_nowait(None)


async def _until(predicate: Any, timeout: float = 2.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < end, "condition never held"
        await asyncio.sleep(0.01)


@pytest.fixture
async def rigctld() -> AsyncIterator[tuple[RigctldServer, MagicMock]]:
    radio = MagicMock(spec=IcomRadio, name="radio")
    radio.connected = True
    radio.radio_ready = True
    radio.control_connected = True
    proto = MagicMock(name="protocol")
    proto.parse_line.return_value = RigctldCommand("f", "get_freq", is_set=False)
    proto.format_response.return_value = b"14074000\n"
    proto.format_error.return_value = b"RPRT -8\n"
    handler = MagicMock(name="handler")
    handler.execute = AsyncMock(return_value=RigctldResponse(["14074000"], 0))
    handler.release_session_tx = AsyncMock(return_value=None)
    server = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0, max_clients=2),
        _protocol=proto,
        _handler=handler,
    )
    await server.start()
    try:
        yield server, handler
    finally:
        await server.stop()


def _channel(server: RigctldServer, ws: _FakeWs, owner: str = _OWNER) -> Any:
    handler = RigctlChannelHandler(
        cast(Connection, ws), server.open_channel, owner, _PEER
    )
    return asyncio.create_task(handler.run())


async def test_lines_run_as_the_session_and_each_reply_is_one_message(
    rigctld: tuple[RigctldServer, MagicMock],
) -> None:
    server, handler = rigctld
    ws = _FakeWs()
    run = _channel(server, ws)
    # One message, several lines: CRLF, a blank line, no final newline.
    ws.inbox.put_nowait((WS_OP_TEXT, b"f\r\n\nf"))
    await _until(lambda: len(ws.sent) == 2)
    assert ws.sent == ["14074000\n", "14074000\n"]
    owners = [call.kwargs["session_id"] for call in handler.execute.call_args_list]
    assert owners == [_OWNER, _OWNER]

    ws.inbox.put_nowait(None)  # the client closes the WebSocket
    await asyncio.wait_for(run, 2.0)
    # MOR-3562: it never keyed, so its close leaves the session's TX alone.
    handler.release_session_tx.assert_not_awaited()
    assert ws.close_code == 1000


def _parse_ptt(line: bytes, _session: Any) -> RigctldCommand:
    if line.startswith(b"T "):
        return RigctldCommand("T", "set_ptt", (line[2:].decode(),), is_set=True)
    return RigctldCommand("f", "get_freq", is_set=False)


@pytest.mark.parametrize(
    ("lines", "released"),
    [("T 1", True), ("T 1\nT 0", False), ("T 0\nT 1\nf", True)],
)
async def test_close_releases_the_session_tx_only_after_the_channels_own_key(
    rigctld: tuple[RigctldServer, MagicMock], lines: str, released: bool
) -> None:
    # MOR-3562: the channel's last T decides; the session ending releases the rest.
    server, handler = rigctld
    cast(MagicMock, server._protocol).parse_line.side_effect = _parse_ptt  # noqa: SLF001
    ws = _FakeWs()
    run = _channel(server, ws)
    ws.inbox.put_nowait((WS_OP_TEXT, lines.encode()))
    await _until(lambda: len(ws.sent) == len(lines.split("\n")))
    ws.inbox.put_nowait(None)
    await asyncio.wait_for(run, 2.0)
    if released:
        handler.release_session_tx.assert_awaited_once_with(_OWNER)
    else:
        handler.release_session_tx.assert_not_awaited()


async def test_binary_message_closes_the_channel(
    rigctld: tuple[RigctldServer, MagicMock],
) -> None:
    server, handler = rigctld
    ws = _FakeWs()
    run = _channel(server, ws)
    ws.inbox.put_nowait((WS_OP_BINARY, b"T 1\n"))
    await asyncio.wait_for(run, 2.0)
    assert ws.close_code == 1003
    handler.execute.assert_not_called()
    handler.release_session_tx.assert_not_awaited()


async def test_one_channel_per_session_and_max_clients_refuse(
    rigctld: tuple[RigctldServer, MagicMock],
) -> None:
    server, _handler = rigctld
    first = _FakeWs()
    first_run = _channel(server, first)
    await _until(lambda: _OWNER in server._channel_owners)  # noqa: SLF001

    duplicate = _FakeWs()
    await asyncio.wait_for(_channel(server, duplicate), 2.0)
    assert duplicate.close_code == 1013

    other = _FakeWs()
    other_run = _channel(server, other, "controller-session-fedcba9876543210")
    await _until(lambda: len(server._client_tasks) == 2)  # noqa: SLF001
    over_cap = _FakeWs()
    await asyncio.wait_for(_channel(server, over_cap, "controller-session-x"), 2.0)
    assert over_cap.close_code == 1013  # max_clients=2

    for ws in (first, other):
        ws.inbox.put_nowait(None)
    await asyncio.wait_for(asyncio.gather(first_run, other_run), 2.0)
    assert not server._channel_owners  # noqa: SLF001


async def test_cancelled_channel_releases_and_stays_cancelled(
    rigctld: tuple[RigctldServer, MagicMock],
) -> None:
    # Server shutdown cancels the channel: the TX release still runs, and the
    # cancel is not swallowed by rigctld's own teardown.
    server, handler = rigctld
    cast(MagicMock, server._protocol).parse_line.side_effect = _parse_ptt  # noqa: SLF001
    ws = _FakeWs()
    run = _channel(server, ws)
    ws.inbox.put_nowait((WS_OP_TEXT, b"T 1"))
    await _until(lambda: len(ws.sent) == 1)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    handler.release_session_tx.assert_awaited_once_with(_OWNER)


async def test_line_reader_holds_a_fast_sender_until_lines_are_read() -> None:
    reader = _LineReader(limit=1)
    feed = asyncio.create_task(reader.feed("a\nb"))
    await asyncio.sleep(0.05)
    assert not feed.done()
    assert await reader.readline() == b"a\n"
    await asyncio.wait_for(feed, 1.0)
    assert await reader.readline() == b"b\n"
    reader.feed_eof()
    assert await reader.readline() == b""
