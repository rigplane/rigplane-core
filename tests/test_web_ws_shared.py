"""Web-side pins for the shared ``rigplane.ws`` module (MOR-3821 round 7).

The RFC 6455 implementation ``web`` uses now lives in the stdlib-only
lowest layer ``rigplane.ws`` (moved from the TCI leaf; the old
``websocket`` / ``host_guard`` shims under ``rigplane.web`` are
deleted). Every test here goes through ``WebSocketConnection`` exactly
as ``WebServer._handle_websocket`` constructs it
(``WebSocketConnection(reader, writer, deflate=...)``) or through the
upgrade function as that handler calls it — never through TCI.

Covered kept differences (old ``web/websocket.py`` vs the shared
module; the full numbered table is in the result note):

- fragmented retention is bounded (count and bytes) with close 1009;
- the first fragment's RSV1 decides decompression of a fragmented
  deflated message (web negotiates deflate);
- ``abort()`` releases the transport even after a protocol close;
- the upgrade entry point answers 101 with web's defaults and passes
  the caller's 421/403 decline through untouched;
- keepalive interval (20 s) and pong timeout (60 s) values web relies on;
- ``make_frame`` byte vectors (server-to-client wire stability).
"""

from __future__ import annotations

import asyncio
import struct
import zlib
from pathlib import Path

import pytest

from rigplane.ws import (
    WS_KEEPALIVE_INTERVAL,
    WS_OP_BINARY,
    WS_OP_CLOSE,
    WS_OP_CONTINUATION,
    WS_OP_TEXT,
    UpgradeAccept,
    UpgradeDecline,
    WebSocketConnection,
    WebSocketError,
    make_accept_key,
    make_frame,
    upgrade_response,
)

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "rigplane"


def test_legacy_shim_files_are_gone() -> None:
    """The ``web/websocket.py`` and ``web/host_guard.py`` shims are deleted."""
    assert not (SRC_DIR / "web" / "websocket.py").exists()
    assert not (SRC_DIR / "web" / "host_guard.py").exists()


class _FakeClock:
    """Injectable monotonic clock."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeTransport:
    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class _FakeWriter:
    """Memory-backed writer with an observable transport."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.transport = _FakeTransport()
        self._closing = False

    def write(self, data: bytes) -> None:
        self.buffer += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self._closing = True

    def is_closing(self) -> bool:
        return self._closing


def _reader(data: bytes) -> asyncio.StreamReader:
    stream = asyncio.StreamReader()
    stream.feed_data(data)
    stream.feed_eof()
    return stream


def _client_frame(
    opcode: int,
    payload: bytes,
    *,
    fin: bool = True,
    rsv1: bool = False,
    mask: bytes = b"\x37\xfa\x21\x3d",
) -> bytes:
    """Build one masked client-to-server frame."""
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    byte0 = (0x80 if fin else 0x00) | (0x40 if rsv1 else 0x00) | (opcode & 0x0F)
    length = len(payload)
    if length <= 125:
        header = bytes([byte0, 0x80 | length]) + mask
    elif length <= 65535:
        header = struct.pack("!BBH", byte0, 0x80 | 126, length) + mask
    else:
        header = struct.pack("!BBQ", byte0, 0x80 | 127, length) + mask
    return header + masked


def _deflate_raw(data: bytes) -> bytes:
    """Compress like the peer does (RFC 7692 section 7.2.1, no tail)."""
    compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
    out = compressor.compress(data) + compressor.flush(zlib.Z_SYNC_FLUSH)
    assert out.endswith(b"\x00\x00\xff\xff")
    return out[: -len(b"\x00\x00\xff\xff")]


def _close_code(buffer: bytes) -> int:
    """Read the close code of the first server-to-client frame."""
    assert buffer[0] & 0x0F == WS_OP_CLOSE
    (code,) = struct.unpack("!H", bytes(buffer[2:4]))
    return code


async def test_fragment_count_overflow_rejected_with_close_1009() -> None:
    """More than 64 retained fragments ends the message with close 1009."""
    frames = _client_frame(WS_OP_TEXT, b"", fin=False)
    frames += _client_frame(WS_OP_CONTINUATION, b"", fin=False) * 64
    conn = WebSocketConnection(_reader(frames), _FakeWriter())
    with pytest.raises(EOFError, match="message too large"):
        async with asyncio.timeout(5.0):
            await conn.recv()
    assert conn.closed


async def test_fragment_bytes_overflow_rejected_with_close_1009() -> None:
    """More than 256 KiB retained across fragments ends with close 1009."""
    chunk = b"x" * (64 * 1024)
    frames = _client_frame(WS_OP_TEXT, chunk, fin=False)
    frames += _client_frame(WS_OP_CONTINUATION, chunk, fin=False) * 4
    writer = _FakeWriter()
    conn = WebSocketConnection(_reader(frames), writer)
    with pytest.raises(EOFError, match="message too large"):
        async with asyncio.timeout(5.0):
            await conn.recv()
    assert _close_code(writer.buffer) == 1009
    assert conn.closed


async def test_first_fragment_rsv1_decides_deflate() -> None:
    """A fragmented deflated message decompresses by the first fragment's RSV1."""
    original = b'{"type":"state_update","revision":42}'
    frames = _client_frame(WS_OP_TEXT, _deflate_raw(original), fin=False, rsv1=True)
    frames += _client_frame(WS_OP_CONTINUATION, b"", fin=True)
    writer = _FakeWriter()
    conn = WebSocketConnection(_reader(frames), writer, deflate=True)
    async with asyncio.timeout(5.0):
        opcode, payload = await conn.recv()
    assert opcode == WS_OP_TEXT
    assert payload == original


async def test_abort_after_protocol_close_releases_transport() -> None:
    """``abort()`` reaches the transport even after a received CLOSE."""
    writer = _FakeWriter()
    conn = WebSocketConnection(_reader(_client_frame(WS_OP_CLOSE, b"")), writer)
    with pytest.raises(EOFError, match="closed"):
        async with asyncio.timeout(5.0):
            await conn.recv()
    assert conn.closed
    conn.abort()
    assert writer.transport.aborted


def test_upgrade_accept_web_defaults() -> None:
    """The upgrade entry point answers 101 with web's call shape."""
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    headers = {
        "host": "127.0.0.1:18080",
        "upgrade": "websocket",
        "connection": "Upgrade",
        "sec-websocket-key": key,
        "sec-websocket-version": "13",
        "sec-websocket-extensions": "permessage-deflate; client_max_window_bits",
    }
    accepted = upgrade_response(headers, allow=lambda _h: None)
    assert isinstance(accepted, UpgradeAccept)
    assert accepted.response.startswith(b"HTTP/1.1 101")
    assert make_accept_key(key).encode("ascii") in accepted.response
    assert b"permessage-deflate" in accepted.response
    assert accepted.deflate is True


def test_upgrade_decline_passes_through_untouched() -> None:
    """A caller policy decline (web's 421/403) is returned as-is."""
    decline = UpgradeDecline(421, "Misdirected Request", b"nope")
    headers = {"host": "evil.example", "upgrade": "websocket"}
    assert upgrade_response(headers, allow=lambda _h: decline) is decline


def test_upgrade_missing_key_refused_400() -> None:
    """A keyless handshake is refused without reaching accept computation."""
    refused = upgrade_response({"upgrade": "websocket"}, allow=lambda _h: None)
    assert isinstance(refused, UpgradeDecline)
    assert refused.status == 400


def test_keepalive_constants_web_relies_on() -> None:
    """Web's 20 s pings and 60 s pong timeout are unchanged."""
    assert WS_KEEPALIVE_INTERVAL == 20.0
    clock = _FakeClock()
    conn = WebSocketConnection(_reader(b""), _FakeWriter(), clock=clock)
    assert conn.is_alive()
    clock.advance(59.9)
    assert conn.is_alive()
    clock.advance(0.2)
    assert not conn.is_alive()


@pytest.mark.parametrize(
    ("opcode", "payload", "rsv1", "expected_head"),
    [
        (WS_OP_TEXT, b"hello", False, b"\x81\x05"),
        (WS_OP_BINARY, b"x" * 200, False, b"\x82\x7e\x00\xc8"),
        (WS_OP_TEXT, b"y" * 70000, False, b"\x81\x7f" + struct.pack("!Q", 70000)),
        (WS_OP_TEXT, b"hi", True, b"\xc1\x02"),
    ],
)
def test_make_frame_vectors_unchanged(
    opcode: int, payload: bytes, rsv1: bool, expected_head: bytes
) -> None:
    """Server-to-client frame bytes are exactly what web has always sent."""
    frame = make_frame(opcode, payload, rsv1=rsv1)
    assert frame[: len(expected_head)] == expected_head
    assert frame[len(expected_head) :] == payload


async def test_envelope_violation_still_fails_connection() -> None:
    """Non-zero RSV2/RSV3 bits surface from recv (unchanged envelope rule)."""
    frame = bytearray(_client_frame(WS_OP_TEXT, b"hi"))
    frame[0] |= 0x20  # RSV2
    conn = WebSocketConnection(_reader(bytes(frame)), _FakeWriter())
    with pytest.raises(WebSocketError, match="non-zero RSV"):
        async with asyncio.timeout(5.0):
            await conn.recv()
