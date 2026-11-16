"""Production serial exchanges with scripted serial boundaries (MOR-4464 P2).

EOF is a transport failure, not silence; echoes stay filtered across
continuation reads. The serial boundary (reader/writer) is faked; the
exchange implementations are the ones the CLI wires to the port.
"""

from __future__ import annotations

import asyncio

import pytest

from rigplane.cli._probe import _civ_exchange, _yaesu_exchange
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame

ADDR = 0x94
TIMEOUT = 1.0


class _ScriptReader:
    """StreamReader double playing back chunks or raising them."""

    def __init__(self, chunks: list) -> None:
        self._chunks = list(chunks)

    async def readuntil(self, separator: bytes) -> bytes:
        item = self._chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        assert item.endswith(separator), item
        return item


class _RecWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(bytes(data))

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _civ_reply(command: int, data: bytes = b"") -> bytes:
    return build_civ_frame(CONTROLLER_ADDR, ADDR, command, data=data or None)


async def test_civ_timeout_is_silence_not_a_break() -> None:
    async def never(_separator: bytes) -> bytes:
        raise asyncio.TimeoutError

    reader = _ScriptReader([])
    reader.readuntil = never  # type: ignore[method-assign]
    exchange = await _civ_exchange(reader, _RecWriter(), ADDR)
    assert await exchange(bytes((0x19, 0x00)), TIMEOUT) is None


async def test_civ_eof_without_partial_is_a_transport_failure() -> None:
    # P2: a disconnected stream must not read as silence.
    reader = _ScriptReader([asyncio.IncompleteReadError(b"", 64)])
    exchange = await _civ_exchange(reader, _RecWriter(), ADDR)
    with pytest.raises((OSError, asyncio.IncompleteReadError)):
        await exchange(bytes((0x19, 0x00)), TIMEOUT)


async def test_civ_eof_keeps_partial_bytes() -> None:
    # P2: bytes received before the disconnect are retained.
    partial = bytes.fromhex("fefefe94e00301")
    reader = _ScriptReader([asyncio.IncompleteReadError(partial, 64)])
    exchange = await _civ_exchange(reader, _RecWriter(), ADDR)
    with pytest.raises(asyncio.IncompleteReadError) as exc_info:
        await exchange(bytes((0x03,)), TIMEOUT)
    assert bytes(exc_info.value.partial or b"") == partial


async def test_yaesu_timeout_is_silence_not_a_break() -> None:
    async def never(_separator: bytes) -> bytes:
        raise asyncio.TimeoutError

    reader = _ScriptReader([])
    reader.readuntil = never  # type: ignore[method-assign]
    exchange = await _yaesu_exchange(reader, _RecWriter())
    assert await exchange(b"FA;", TIMEOUT) is None


async def test_yaesu_eof_without_partial_is_a_transport_failure() -> None:
    reader = _ScriptReader([asyncio.IncompleteReadError(b"", 64)])
    exchange = await _yaesu_exchange(reader, _RecWriter())
    with pytest.raises((OSError, asyncio.IncompleteReadError)):
        await exchange(b"FA;", TIMEOUT)


async def test_yaesu_eof_keeps_partial_bytes() -> None:
    reader = _ScriptReader([asyncio.IncompleteReadError(b"FA0707", 64)])
    exchange = await _yaesu_exchange(reader, _RecWriter())
    with pytest.raises(asyncio.IncompleteReadError) as exc_info:
        await exchange(b"FA;", TIMEOUT)
    assert bytes(exc_info.value.partial or b"") == b"FA0707"


async def test_civ_echo_stays_filtered_across_continuation_reads() -> None:
    # P2: unsolicited frame, then the USB echo of our own request, then
    # the answer. The echo must not be taken as the answer on the
    # continuation read that follows the unsolicited frame.
    body = bytes((0x19, 0x00))
    sent = build_civ_frame(ADDR, CONTROLLER_ADDR, 0x19, sub=0x00)
    stray = _civ_reply(0xFA)
    answer = _civ_reply(0x19, bytes((ADDR,)))
    assert stray != sent and answer != sent
    reader = _ScriptReader([stray, sent, answer])
    writer = _RecWriter()
    exchange = await _civ_exchange(reader, writer, ADDR)
    assert await exchange(body, TIMEOUT) == stray
    assert await exchange(None, TIMEOUT) == answer
    assert writer.written == [sent]


async def test_yaesu_echo_stays_filtered_across_continuation_reads() -> None:
    # P2: the same sequence over CAT: unsolicited line, echoed request,
    # then the answer.
    reader = _ScriptReader([b"ID0840;", b"FA;", b"FA07074000;"])
    writer = _RecWriter()
    exchange = await _yaesu_exchange(reader, writer)
    assert await exchange(b"FA;", TIMEOUT) == b"ID0840;"
    assert await exchange(None, TIMEOUT) == b"FA07074000;"
    assert writer.written == [b"FA;"]
