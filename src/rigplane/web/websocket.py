"""RFC 6455 WebSocket + RFC 7692 permessage-deflate (stdlib only).

Provides:
- HTTP Upgrade handshake key computation
- Frame serialization (server→client, unmasked)
- Frame parsing (client→server, masked)
- WebSocketConnection class for full-duplex messaging
- Optional per-message deflate compression
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
import time
import zlib

__all__ = [
    "WS_MAGIC",
    "WS_OP_CONTINUATION",
    "WS_OP_TEXT",
    "WS_OP_BINARY",
    "WS_OP_CLOSE",
    "WS_OP_PING",
    "WS_OP_PONG",
    "WS_KEEPALIVE_INTERVAL",
    "WebSocketError",
    "make_accept_key",
    "make_frame",
    "negotiate_deflate",
    "WebSocketConnection",
]

WS_KEEPALIVE_INTERVAL = 20.0  # seconds between server-initiated pings

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

WS_OP_CONTINUATION = 0x0
WS_OP_TEXT = 0x1
WS_OP_BINARY = 0x2
WS_OP_CLOSE = 0x8
WS_OP_PING = 0x9
WS_OP_PONG = 0xA

_RSV1 = 0x40  # per-message deflate flag

# Maximum accepted incoming frame payload (bytes). Frames larger than this
# are rejected with close code 1009 (Message Too Big) before any allocation
# to prevent memory exhaustion from a malicious/malformed length field.
_MAX_WS_FRAME = 64 * 1024

# zlib flush marker appended by deflate, stripped per RFC 7692 §7.2.1
_DEFLATE_TAIL = b"\x00\x00\xff\xff"


class WebSocketError(Exception):
    """Raised when a WebSocket protocol violation is detected."""


class _FrameTooLargeError(WebSocketError):
    """Raised when an incoming frame exceeds ``_MAX_WS_FRAME``."""


def make_accept_key(client_key: str) -> str:
    """Compute the Sec-WebSocket-Accept value from the client's key."""
    raw = (client_key + WS_MAGIC).encode("ascii")
    return base64.b64encode(hashlib.sha1(raw).digest()).decode("ascii")


def negotiate_deflate(extensions_header: str) -> str | None:
    """Parse Sec-WebSocket-Extensions and return response value if deflate ok.

    Uses server_no_context_takeover + client_no_context_takeover for
    simplicity (no per-connection zlib state to manage).

    Returns:
        Extension response string for the 101 header, or None if client
        did not offer permessage-deflate.
    """
    for ext in extensions_header.split(","):
        name = ext.strip().split(";")[0].strip().lower()
        if name == "permessage-deflate":
            return (
                "permessage-deflate; "
                "server_no_context_takeover; "
                "client_no_context_takeover"
            )
    return None


def make_frame(
    opcode: int,
    payload: bytes,
    *,
    fin: bool = True,
    rsv1: bool = False,
) -> bytes:
    """Serialize a WebSocket frame (server→client, no masking).

    Args:
        opcode: Frame opcode (WS_OP_TEXT, WS_OP_BINARY, etc.).
        payload: Frame payload bytes.
        fin: Whether this is the final fragment.
        rsv1: Set RSV1 bit (used for permessage-deflate).

    Returns:
        Serialized frame bytes.
    """
    first_byte = (0x80 if fin else 0x00) | (opcode & 0x0F)
    if rsv1:
        first_byte |= _RSV1
    length = len(payload)

    if length <= 125:
        header = bytes([first_byte, length])
    elif length <= 65535:
        header = struct.pack("!BBH", first_byte, 126, length)
    else:
        header = struct.pack("!BBQ", first_byte, 127, length)

    return header + payload


async def _read_one_frame(
    reader: asyncio.StreamReader,
    *,
    deflate: bool = False,
) -> tuple[int, bytes, bool, bool]:
    """Read one WebSocket frame from the stream.

    Returns:
        Tuple of (opcode, payload, fin, rsv1).
    """
    header = await reader.readexactly(2)
    byte0, byte1 = header[0], header[1]

    fin = bool(byte0 & 0x80)
    rsv1 = bool(byte0 & _RSV1)
    rsv23 = (byte0 >> 4) & 0x03  # RSV2 and RSV3

    if rsv23 != 0:
        raise WebSocketError(f"non-zero RSV2/RSV3 bits: {rsv23:#x}")
    if rsv1 and not deflate:
        raise WebSocketError("RSV1 set but permessage-deflate not negotiated")

    opcode = byte0 & 0x0F
    masked = bool(byte1 & 0x80)
    payload_len = byte1 & 0x7F

    if payload_len == 126:
        ext = await reader.readexactly(2)
        payload_len = struct.unpack("!H", ext)[0]
    elif payload_len == 127:
        ext = await reader.readexactly(8)
        payload_len = struct.unpack("!Q", ext)[0]

    if payload_len > _MAX_WS_FRAME:
        raise _FrameTooLargeError(
            f"frame payload {payload_len} exceeds max {_MAX_WS_FRAME}"
        )

    mask_key = b""
    if masked:
        mask_key = await reader.readexactly(4)

    raw = await reader.readexactly(payload_len)

    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(raw))
    else:
        payload = raw

    return opcode, payload, fin, rsv1


class WebSocketConnection:
    """An established WebSocket connection (post-handshake).

    Supports optional permessage-deflate compression (RFC 7692).

    Args:
        reader: asyncio StreamReader (post-HTTP handshake).
        writer: asyncio StreamWriter (post-HTTP handshake).
        deflate: Enable permessage-deflate compression.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        deflate: bool = False,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._closed = False
        self._deflate = deflate
        self._fragmented_opcode: int = 0
        self._fragments: list[bytes] = []
        self._last_pong: float = time.monotonic()
        self._pong_timeout: float = 60.0

    @property
    def deflate_enabled(self) -> bool:
        """True if permessage-deflate was negotiated."""
        return self._deflate

    def is_alive(self) -> bool:
        """True if connection is open and a pong was received recently enough."""
        if self._closed:
            return False
        return (time.monotonic() - self._last_pong) < self._pong_timeout

    def _compress(self, data: bytes) -> bytes:
        """Compress payload per RFC 7692 §7.2.1."""
        # Use raw deflate (wbits=-15), flush with Z_SYNC_FLUSH,
        # then strip the trailing 0x00 0x00 0xff 0xff marker.
        c = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
        out = c.compress(data) + c.flush(zlib.Z_SYNC_FLUSH)
        if out.endswith(_DEFLATE_TAIL):
            out = out[: -len(_DEFLATE_TAIL)]
        return out

    def _decompress(self, data: bytes) -> bytes:
        """Decompress payload per RFC 7692 §7.2.2."""
        d = zlib.decompressobj(-15)
        return d.decompress(data + _DEFLATE_TAIL)

    async def recv(self) -> tuple[int, bytes]:
        """Receive the next complete message.

        Handles fragmented frames, ping/pong, and deflate decompression
        internally. Raises EOFError on clean close.

        Returns:
            Tuple of (opcode, payload).
        """
        while True:
            try:
                opcode, payload, fin, rsv1 = await _read_one_frame(
                    self._reader, deflate=self._deflate
                )
            except asyncio.IncompleteReadError as exc:
                raise EOFError("connection closed") from exc
            except _FrameTooLargeError as exc:
                # Oversize frame: respond with close 1009 and abort.
                await self.close(1009, "frame too large")
                raise EOFError("frame too large") from exc

            if opcode == WS_OP_PING:
                await self._send_raw(make_frame(WS_OP_PONG, payload))
                continue

            if opcode == WS_OP_PONG:
                self._last_pong = time.monotonic()
                continue

            if opcode == WS_OP_CLOSE:
                self._closed = True
                if not self._writer.is_closing():
                    try:
                        self._writer.write(make_frame(WS_OP_CLOSE, payload))
                        await self._writer.drain()
                    except OSError:
                        pass
                raise EOFError("WebSocket closed")

            if opcode == WS_OP_CONTINUATION:
                if not self._fragments:
                    raise WebSocketError("unexpected continuation frame")
                self._fragments.append(payload)
                if fin:
                    data = b"".join(self._fragments)
                    op = self._fragmented_opcode
                    self._fragments = []
                    self._fragmented_opcode = 0
                    # RSV1 is set on the FIRST fragment only (per RFC 7692)
                    # Decompression was deferred to reassembly
                    if rsv1 and self._deflate:
                        data = self._decompress(data)
                    return op, data
                continue

            # Data frame (text or binary)
            if not fin:
                self._fragmented_opcode = opcode
                self._fragments = [payload]
                continue

            # Complete single-frame message
            if rsv1 and self._deflate:
                payload = self._decompress(payload)
            return opcode, payload

    async def send_text(self, text: str) -> None:
        """Send a text frame, compressed if deflate is enabled."""
        data = text.encode("utf-8")
        if self._deflate:
            compressed = self._compress(data)
            await self._send_raw(make_frame(WS_OP_TEXT, compressed, rsv1=True))
        else:
            await self._send_raw(make_frame(WS_OP_TEXT, data))

    async def send_binary(self, data: bytes) -> None:
        """Send a binary frame, compressed if deflate is enabled."""
        if self._deflate:
            compressed = self._compress(data)
            # Only compress if it actually saves space
            if len(compressed) < len(data):
                await self._send_raw(make_frame(WS_OP_BINARY, compressed, rsv1=True))
                return
        await self._send_raw(make_frame(WS_OP_BINARY, data))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Send a WebSocket close frame (never compressed)."""
        if not self._closed:
            payload = struct.pack("!H", code) + reason.encode("utf-8")
            try:
                await self._send_raw(make_frame(WS_OP_CLOSE, payload))
            except OSError:
                pass
            self._closed = True

    @property
    def closed(self) -> bool:
        """True if the connection has been closed."""
        return self._closed

    async def keepalive_loop(self, interval: float = 20.0) -> None:
        """Send periodic ping frames to detect dead connections."""
        try:
            while not self._closed:
                await asyncio.sleep(interval)
                if not self._closed:
                    if not self.is_alive():
                        await self.close(1001, "pong timeout")
                        break
                    await self._send_raw(make_frame(WS_OP_PING, b"ka"))
                    # DO NOT reset _last_pong here — only on actual PONG receipt
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _send_raw(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()
