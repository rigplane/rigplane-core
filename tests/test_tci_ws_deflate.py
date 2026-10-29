"""TCI upgrade ignores deflate; fragmented reassembly keeps the first RSV1.

Design 4.2: per-message deflate stays off for TCI (clients do not
negotiate it). The upgrade answers 101 without ``Sec-WebSocket-Extensions``
even when the client offers it, so no TCI frame is ever compressed; a
compressed frame from a client that was not granted the extension is
refused. The reassembly half is the ``web`` path this PR now owns:
RFC 7692 sets RSV1 on the FIRST fragment, so the flag must survive
until the complete message is reassembled.
"""

from __future__ import annotations

import asyncio
import struct
import zlib

import pytest

from rigplane.tci.server import TciServer
from rigplane.tci.ws import (
    WS_OP_BINARY,
    WS_OP_CONTINUATION,
    WS_OP_PING,
    WS_OP_PONG,
    WS_OP_TEXT,
    WebSocketConnection,
    WebSocketError,
    make_upgrade_response,
    upgrade_response,
)

BURST = ["protocol:tciserver,1;", "ready;"]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeHandler:
    def __init__(self) -> None:
        self.disconnect_calls = 0

    async def on_connect(self) -> list[str]:
        return list(BURST)

    async def on_command(self, name: str, args: tuple[str, ...]) -> object:
        from rigplane.tci.handler import CommandOutcome

        return CommandOutcome(to_sender=[f"{name}:{','.join(args)};"])

    async def on_binary(self, payload: bytes) -> None:
        pass

    async def on_disconnect(self) -> None:
        self.disconnect_calls += 1


class FakeWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self._closing = False

    def write(self, data: bytes) -> None:
        self.buffer += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self._closing = True

    def is_closing(self) -> bool:
        return self._closing


def client_frame(opcode: int, payload: bytes, *, fin: bool = True, rsv1: bool = False) -> bytes:
    """One masked client->server frame with explicit FIN/RSV1."""
    mask = b"\x11\x22\x33\x44"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    byte0 = (0x80 if fin else 0x00) | (0x40 if rsv1 else 0x00) | (opcode & 0x0F)
    length = len(payload)
    if length <= 125:
        header = struct.pack("!BB", byte0, 0x80 | length)
    elif length <= 65535:
        header = struct.pack("!BBH", byte0, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", byte0, 0x80 | 127, length)
    return header + mask + masked


def deflate_raw(data: bytes) -> bytes:
    """Compress one message per RFC 7692 S7.2.1 (no context takeover)."""
    compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
    out = compressor.compress(data) + compressor.flush(zlib.Z_SYNC_FLUSH)
    assert out.endswith(b"\x00\x00\xff\xff")
    return out[: -len(b"\x00\x00\xff\xff")]


def parse_server_frames(buffer: bytes) -> list[tuple[int, bytes]]:
    frames: list[tuple[int, bytes]] = []
    view = bytes(buffer)
    while view:
        first, second = view[0], view[1]
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", view[2:4])
            head = 4
        elif length == 127:
            (length,) = struct.unpack("!Q", view[2:10])
            head = 10
        else:
            head = 2
        frames.append((opcode, view[head : head + length]))
        view = view[head + length :]
    return frames


async def wait_for_frames(
    writer: FakeWriter, count: int, timeout: float = 2.0
) -> list[tuple[int, bytes]]:
    async with asyncio.timeout(timeout):
        while len(parse_server_frames(writer.buffer)) < count:
            await asyncio.sleep(0.005)
    return parse_server_frames(writer.buffer)


def offer_headers() -> dict[str, str]:
    return {
        "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
        "sec-websocket-extensions": "permessage-deflate; client_max_window_bits",
    }


async def test_tci_upgrade_ignores_deflate_offer() -> None:
    """A deflate offer gets a plain 101: no extension header, no deflate."""
    result = make_upgrade_response(offer_headers(), allow_deflate=False)
    assert not hasattr(result, "status")
    assert result.deflate is False  # type: ignore[union-attr]
    head = result.response.split(b"\r\n\r\n")[0].lower()  # type: ignore[union-attr]
    assert b"sec-websocket-extensions" not in head


async def test_tci_upgrade_response_ignores_deflate_offer() -> None:
    """The full TCI upgrade entry point also ignores the offer."""
    result = upgrade_response(
        offer_headers(), allow=lambda headers: None, allow_deflate=False
    )
    assert not hasattr(result, "status")
    assert result.deflate is False  # type: ignore[union-attr]


async def test_web_upgrade_default_still_negotiates() -> None:
    """The ``web`` default path is byte-identical: an offer is accepted."""
    result = make_upgrade_response(offer_headers())
    assert result.deflate is True  # type: ignore[union-attr]
    assert b"Sec-WebSocket-Extensions: permessage-deflate" in result.response  # type: ignore[union-attr]


async def test_tci_accept_ignores_deflate_offer() -> None:
    """Over a session: 101 without the extension, uncompressed I/O works."""
    clock = FakeClock()
    handler = FakeHandler()
    server = TciServer(
        lambda: handler,  # type: ignore[return-value]
        host="127.0.0.1",
        port=0,
        allow=lambda headers: None,
        clock=clock,
    )
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    reader.feed_data(
        b"GET / HTTP/1.1\r\n"
        b"Host: 127.0.0.1:50001\r\n"
        b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        b"Sec-WebSocket-Extensions: permessage-deflate\r\n"
        b"\r\n"
    )
    await server._accept_request(reader, writer)
    assert writer.buffer.startswith(b"HTTP/1.1 101 ")
    head_end = bytes(writer.buffer).index(b"\r\n\r\n") + 4
    assert b"sec-websocket-extensions" not in bytes(writer.buffer[:head_end]).lower()
    del writer.buffer[:head_end]
    try:
        session = server.sessions[0]
        assert session._conn.deflate_enabled is False
        await wait_for_frames(writer, len(BURST))
        reader.feed_data(client_frame(WS_OP_TEXT, b"vfo:0,0,14074000;"))
        frames = await wait_for_frames(writer, len(BURST) + 1)
        assert frames[-1] == (WS_OP_TEXT, b"vfo:0,0,14074000;")
    finally:
        await server.stop()
    assert handler.disconnect_calls == 1


async def test_compressed_frame_without_grant_is_refused() -> None:
    """RSV1 without a negotiated deflate is a protocol violation.

    The connection raises ``WebSocketError``; the session reader loop
    turns that into a clean teardown (pinned separately).
    """
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    conn = WebSocketConnection(reader, writer, clock=FakeClock())
    reader.feed_data(client_frame(WS_OP_TEXT, b"vfo:0,0,1;", rsv1=True))
    with pytest.raises(WebSocketError, match="permessage-deflate not negotiated"):
        await conn.recv()


async def test_single_compressed_frame_still_decompresses() -> None:
    """Control: one compressed frame on a deflate connection works."""
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    conn = WebSocketConnection(reader, writer, clock=FakeClock(), deflate=True)
    reader.feed_data(client_frame(WS_OP_TEXT, deflate_raw(b"vfo:0,0,14074000;"), rsv1=True))
    opcode, payload = await conn.recv()
    assert (opcode, payload) == (WS_OP_TEXT, b"vfo:0,0,14074000;")


async def test_fragmented_compressed_text_reassembled() -> None:
    """RSV1 lives on the first fragment; an interleaved ping is answered."""
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    conn = WebSocketConnection(reader, writer, clock=FakeClock(), deflate=True)
    blob = deflate_raw(b"vfo:0,0,14074000;")
    first, rest = blob[:5], blob[5:]
    reader.feed_data(client_frame(WS_OP_TEXT, first, fin=False, rsv1=True))
    reader.feed_data(client_frame(WS_OP_PING, b"hi"))
    reader.feed_data(client_frame(WS_OP_CONTINUATION, rest, fin=True, rsv1=False))
    opcode, payload = await conn.recv()
    assert (opcode, payload) == (WS_OP_TEXT, b"vfo:0,0,14074000;")
    frames = parse_server_frames(writer.buffer)
    assert (WS_OP_PONG, b"hi") in frames


async def test_fragmented_compressed_binary_reassembled() -> None:
    """Same reassembly rule for binary messages."""
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    conn = WebSocketConnection(reader, writer, clock=FakeClock(), deflate=True)
    blob = deflate_raw(b"\x00\x01\x02\x03" * 32)
    first, rest = blob[:7], blob[7:]
    reader.feed_data(client_frame(WS_OP_BINARY, first, fin=False, rsv1=True))
    reader.feed_data(client_frame(WS_OP_CONTINUATION, rest, fin=True, rsv1=False))
    opcode, payload = await conn.recv()
    assert (opcode, payload) == (WS_OP_BINARY, b"\x00\x01\x02\x03" * 32)
