"""Extra coverage tests for web/websocket.py.

Covers:
- _read_one_frame: non-zero RSV bits → WebSocketError (line 110)
- _read_one_frame: 16-bit extended length (lines 117-118)
- _read_one_frame: 64-bit extended length (lines 120-121)
- _read_one_frame: unmasked payload (line 132)
- recv(): asyncio.IncompleteReadError → EOFError (line 178)
- recv(): WS_OP_PING → auto pong (lines 181-182)
- recv(): WS_OP_PONG → ignored (line 185)
- recv(): WS_OP_CLOSE → echo + raise EOFError (lines 187-196)
- recv(): WS_OP_CONTINUATION without prior fragment → WebSocketError (line 200)
- recv(): fragmented continuation sequence (lines 199-209)
- recv(): first fragment of multi-part message (lines 214-216)
- send_binary() (line 234)
- close() (lines 243-249)
- closed property (line 254)
- keepalive_loop() exception exit (lines 273-274)
"""

from __future__ import annotations

import asyncio
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.web.websocket import (
    WS_OP_BINARY,
    WS_OP_CLOSE,
    WS_OP_CONTINUATION,
    WS_OP_PING,
    WS_OP_PONG,
    WS_OP_TEXT,
    WebSocketConnection,
    WebSocketError,
    _read_one_frame,
)


# ---------------------------------------------------------------------------
# Helper: build a minimal client→server frame (masked)
# ---------------------------------------------------------------------------


def _client_frame(
    opcode: int,
    payload: bytes,
    *,
    fin: bool = True,
    mask: bytes = b"\x37\xfa\x21\x3d",
    rsv: int = 0,
) -> bytes:
    """Build a masked client→server WebSocket frame."""
    masked_payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    byte0 = (0x80 if fin else 0x00) | (rsv << 4) | (opcode & 0x0F)
    length = len(payload)
    if length <= 125:
        byte1 = 0x80 | length
        header = bytes([byte0, byte1]) + mask
    elif length <= 65535:
        byte1 = 0x80 | 126
        header = bytes([byte0, byte1]) + struct.pack("!H", length) + mask
    else:
        byte1 = 0x80 | 127
        header = bytes([byte0, byte1]) + struct.pack("!Q", length) + mask
    return header + masked_payload


def _unmasked_frame(opcode: int, payload: bytes, *, fin: bool = True) -> bytes:
    """Build an unmasked server→client-style frame (no mask bit)."""
    byte0 = (0x80 if fin else 0x00) | (opcode & 0x0F)
    length = len(payload)
    header = bytes([byte0, 0x00 | length])  # MASK=0
    return header + payload


def _make_reader(data: bytes) -> asyncio.StreamReader:
    r = asyncio.StreamReader()
    r.feed_data(data)
    r.feed_eof()
    return r


def _make_writer(is_closing: bool = False) -> MagicMock:
    w = MagicMock()
    w.is_closing.return_value = is_closing
    w.write = MagicMock()
    w.drain = AsyncMock()
    return w


# ---------------------------------------------------------------------------
# _read_one_frame
# ---------------------------------------------------------------------------


async def test_read_one_frame_non_zero_rsv_raises() -> None:
    """Non-zero RSV bits must raise WebSocketError."""
    frame = _client_frame(WS_OP_TEXT, b"hi", rsv=1)
    reader = _make_reader(frame)
    with pytest.raises(WebSocketError, match="non-zero RSV"):
        await _read_one_frame(reader)


async def test_read_one_frame_16bit_extended_length() -> None:
    """Payload longer than 125 bytes uses 2-byte extended length encoding."""
    payload = b"x" * 200  # > 125 → uses 16-bit length
    frame = _client_frame(WS_OP_BINARY, payload)
    reader = _make_reader(frame)
    opcode, data, fin, _ = await _read_one_frame(reader)
    assert opcode == WS_OP_BINARY
    assert data == payload
    assert fin is True


async def test_read_one_frame_64bit_extended_length_rejected() -> None:
    """Payload >64 KiB (which requires 64-bit length) is rejected (S03).

    Before reading any payload bytes, the frame parser must bail out to
    prevent multi-GB allocations from a malicious length field.
    """
    # Craft a header that advertises a huge payload but don't supply any data.
    # byte0 = 0x82 (FIN | BINARY), byte1 = 0x80|127 (mask, 64-bit length),
    # followed by 8 bytes of length = 1 GiB, then 4-byte mask key.
    header = (
        bytes([0x82, 0x80 | 127]) + struct.pack("!Q", 1 << 30) + b"\x00\x00\x00\x00"
    )
    reader = _make_reader(header)
    with pytest.raises(WebSocketError, match="exceeds max"):
        await _read_one_frame(reader)


async def test_recv_oversize_frame_sends_close_1009() -> None:
    """recv() must send a 1009 (Message Too Big) close frame and raise EOFError."""
    header = (
        bytes([0x82, 0x80 | 127]) + struct.pack("!Q", 1 << 30) + b"\x00\x00\x00\x00"
    )
    reader = _make_reader(header)
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    with pytest.raises(EOFError, match="frame too large"):
        await ws.recv()
    # Verify a close frame with code 1009 was written.
    assert writer.write.called
    sent = b"".join(call.args[0] for call in writer.write.call_args_list)
    # Close frame: opcode 0x8, payload starts with 2-byte close code.
    # Find a close frame (byte0 == 0x88) and check its code.
    assert b"\x88" in sent
    idx = sent.index(b"\x88")
    # byte after 0x88 is payload length (no mask on server→client)
    code = struct.unpack("!H", sent[idx + 2 : idx + 4])[0]
    assert code == 1009


async def test_read_one_frame_unmasked_payload() -> None:
    """Unmasked frames (server→client style) should have payload returned as-is."""
    frame = _unmasked_frame(WS_OP_TEXT, b"hello")
    reader = _make_reader(frame)
    opcode, data, fin, _ = await _read_one_frame(reader)
    assert opcode == WS_OP_TEXT
    assert data == b"hello"


# ---------------------------------------------------------------------------
# WebSocketConnection.recv()
# ---------------------------------------------------------------------------


async def test_recv_incomplete_read_raises_eof_error() -> None:
    """asyncio.IncompleteReadError during recv() should become EOFError."""
    reader = asyncio.StreamReader()
    reader.feed_data(b"\x81")  # only 1 byte, reader expects 2 for header
    reader.feed_eof()
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    with pytest.raises(EOFError):
        await ws.recv()


async def test_recv_auto_responds_to_ping() -> None:
    """PING frame must be answered with PONG then loop continues."""
    # PING frame followed by a TEXT frame (so recv() returns TEXT)
    ping_frame = _client_frame(WS_OP_PING, b"probe")
    text_frame = _client_frame(WS_OP_TEXT, b"hello")
    reader = _make_reader(ping_frame + text_frame)
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    opcode, payload = await ws.recv()
    assert opcode == WS_OP_TEXT
    assert payload == b"hello"
    # writer.write should have been called with a PONG frame
    writer.write.assert_called_once()
    pong_data = writer.write.call_args[0][0]
    assert pong_data[0] & 0x0F == WS_OP_PONG  # opcode = PONG


async def test_recv_ignores_pong() -> None:
    """Unsolicited PONG frame must be silently ignored."""
    pong_frame = _client_frame(WS_OP_PONG, b"")
    text_frame = _client_frame(WS_OP_TEXT, b"world")
    reader = _make_reader(pong_frame + text_frame)
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    opcode, payload = await ws.recv()
    assert opcode == WS_OP_TEXT
    assert payload == b"world"


async def test_recv_close_echoes_and_raises_eoferror() -> None:
    """CLOSE frame must be echoed and then EOFError raised."""
    close_payload = struct.pack("!H", 1000) + b"normal"
    close_frame = _client_frame(WS_OP_CLOSE, close_payload)
    reader = _make_reader(close_frame)
    writer = _make_writer(is_closing=False)
    ws = WebSocketConnection(reader, writer)
    with pytest.raises(EOFError, match="WebSocket closed"):
        await ws.recv()
    assert ws.closed is True
    # Echo should have been written
    writer.write.assert_called_once()


async def test_recv_close_when_writer_is_closing_skips_echo() -> None:
    """If writer is already closing, CLOSE echo must be skipped."""
    close_payload = struct.pack("!H", 1000)
    close_frame = _client_frame(WS_OP_CLOSE, close_payload)
    reader = _make_reader(close_frame)
    writer = _make_writer(is_closing=True)
    ws = WebSocketConnection(reader, writer)
    with pytest.raises(EOFError):
        await ws.recv()
    writer.write.assert_not_called()


async def test_recv_continuation_without_prior_fragment_raises() -> None:
    """CONTINUATION frame without a preceding data frame raises WebSocketError."""
    cont_frame = _client_frame(WS_OP_CONTINUATION, b"orphan")
    reader = _make_reader(cont_frame)
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    with pytest.raises(WebSocketError, match="unexpected continuation frame"):
        await ws.recv()


async def test_recv_fragmented_message_assembled() -> None:
    """Fragmented text message should be assembled across continuation frames."""
    # First fragment: fin=False, opcode=TEXT, payload="hel"
    first = _client_frame(WS_OP_TEXT, b"hel", fin=False)
    # Middle continuation: fin=False
    middle = _client_frame(WS_OP_CONTINUATION, b"lo ", fin=False)
    # Final continuation: fin=True
    last = _client_frame(WS_OP_CONTINUATION, b"world", fin=True)
    reader = _make_reader(first + middle + last)
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    opcode, payload = await ws.recv()
    assert opcode == WS_OP_TEXT
    assert payload == b"hello world"


async def test_recv_two_fragment_message() -> None:
    """Two-part fragmented binary message."""
    first = _client_frame(WS_OP_BINARY, b"part1", fin=False)
    last = _client_frame(WS_OP_CONTINUATION, b"part2", fin=True)
    reader = _make_reader(first + last)
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    opcode, payload = await ws.recv()
    assert opcode == WS_OP_BINARY
    assert payload == b"part1part2"


# ---------------------------------------------------------------------------
# send_binary() — line 234
# ---------------------------------------------------------------------------


async def test_send_binary() -> None:
    """send_binary() must write a BINARY frame."""
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    await ws.send_binary(b"\x00\x01\x02")
    writer.write.assert_called_once()
    frame = writer.write.call_args[0][0]
    assert frame[0] & 0x0F == WS_OP_BINARY


# ---------------------------------------------------------------------------
# close() — lines 243-249
# ---------------------------------------------------------------------------


async def test_close_sends_close_frame() -> None:
    """close() should send a CLOSE frame with the status code."""
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    await ws.close(code=1001, reason="going away")
    writer.write.assert_called_once()
    frame = writer.write.call_args[0][0]
    assert frame[0] & 0x0F == WS_OP_CLOSE
    assert ws.closed is True


async def test_close_idempotent() -> None:
    """close() called twice must only write the frame once."""
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    await ws.close()
    await ws.close()
    assert writer.write.call_count == 1


async def test_close_oserror_suppressed() -> None:
    """OSError during CLOSE frame write must be silently suppressed."""
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = _make_writer()
    writer.drain.side_effect = OSError("broken pipe")
    ws = WebSocketConnection(reader, writer)
    # Must not raise
    await ws.close()
    assert ws.closed is True


async def test_close_closes_writer_transport() -> None:
    """close() must actually tear down the transport (MOR-1429), not just
    write a close frame -- a reader blocked in recv()/_read_one_frame on a
    half-open peer only unblocks once the local transport is closed; the
    peer's TCP stack can't be relied on to ever send a FIN."""
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    await ws.close()
    writer.close.assert_called_once()


async def test_close_idempotent_still_closes_writer_each_call() -> None:
    """A second close() call must still attempt real transport teardown,
    not just skip out early because the logical _closed flag is set."""
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    await ws.close()
    await ws.close()
    assert writer.close.call_count == 2


async def test_keepalive_loop_pong_timeout_forces_real_teardown() -> None:
    """MOR-1429: keepalive's pong-timeout branch must force real teardown
    (writer.close()), not just flip the logical _closed flag -- otherwise a
    reader parked in recv() on a half-open peer never unblocks even though
    the pong timeout correctly detected it as stale."""
    reader = asyncio.StreamReader()
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    ws._last_pong = time.monotonic() - 1000.0
    ws._pong_timeout = 0.01

    await ws.keepalive_loop(interval=0.001)

    assert ws.closed is True
    writer.close.assert_called_once()


# ---------------------------------------------------------------------------
# closed property — line 254
# ---------------------------------------------------------------------------


async def test_closed_property_false_initially() -> None:
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    assert ws.closed is False


async def test_closed_property_true_after_close() -> None:
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)
    await ws.close()
    assert ws.closed is True


# ---------------------------------------------------------------------------
# keepalive_loop() — lines 256-274
# ---------------------------------------------------------------------------


async def test_keepalive_loop_sends_ping() -> None:
    """keepalive_loop() should send PING frames at the configured interval."""
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)

    # Patch asyncio.sleep to fire quickly
    call_count = 0

    async def fast_sleep(interval: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            ws._closed = True  # stop loop after 2 pings

    with patch("rigplane.web.websocket.asyncio.sleep", fast_sleep):
        await ws.keepalive_loop(interval=0.001)

    assert writer.write.call_count >= 1
    frame = writer.write.call_args_list[0][0][0]
    assert frame[0] & 0x0F == WS_OP_PING


async def test_keepalive_loop_exits_on_cancellation() -> None:
    """keepalive_loop() must exit cleanly when cancelled."""
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)

    task = asyncio.create_task(ws.keepalive_loop(interval=100.0))
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.done()


async def test_keepalive_loop_suppresses_non_cancelled_exception() -> None:
    """Any non-CancelledError in keepalive_loop() should be swallowed silently."""
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = _make_writer()
    ws = WebSocketConnection(reader, writer)

    async def raise_once(interval: float) -> None:
        ws._closed = False
        raise OSError("pipe broken")

    with patch("rigplane.web.websocket.asyncio.sleep", raise_once):
        # Must not propagate
        await ws.keepalive_loop(interval=0.001)
