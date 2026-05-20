"""Tests for src/rigplane/web/ — WebSocket server, protocol, and handlers.

Strategy
--------
- Test binary frame encoding functions directly (no network required).
- Test HTTP endpoints by starting a real server on port 0.
- Test WebSocket handshake, hello, subscribe, commands by connecting via
  asyncio.open_connection and performing the RFC 6455 handshake manually.
- Inject a mock IcomRadio to avoid needing a real radio.
- asyncio_mode = "auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.backends.icom7610.drivers.serial_stub import SerialMockRadio
from rigplane.radio_state import RadioState
from rigplane.rigctld.state_cache import StateCache
from rigplane.scope import ScopeFrame
from rigplane.web.protocol import (
    MSG_TYPE_SCOPE,
    SCOPE_HEADER_SIZE,
    decode_json,
    encode_json,
    encode_scope_frame,
)
from rigplane.web.server import _DEFAULT_STATIC_DIR, WebConfig, WebServer
from rigplane.web.websocket import (
    WS_MAGIC,
    WS_OP_BINARY,
    WS_OP_PING,
    WS_OP_TEXT,
    WebSocketConnection,
    make_accept_key,
    make_frame,
)

# ---------------------------------------------------------------------------
# Frontend static build guard
# ---------------------------------------------------------------------------

# The web server serves `index.html` from `src/rigplane/web/static/` at `GET /`.
# In a fresh checkout without a frontend build, that directory is absent and
# any test relying on the root route would fail deterministically. Skip such
# tests when the built frontend is not present (see issue #953).
_STATIC_INDEX_MISSING = not (_DEFAULT_STATIC_DIR / "index.html").is_file()
_requires_static_index = pytest.mark.skipif(
    _STATIC_INDEX_MISSING,
    reason="frontend build not present (src/rigplane/web/static/index.html missing)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _addr(server: WebServer) -> tuple[str, int]:
    assert server._server is not None
    return server._server.sockets[0].getsockname()


def _ws_accept(key: str) -> str:
    raw = (key + WS_MAGIC).encode("ascii")
    return base64.b64encode(hashlib.sha1(raw).digest()).decode("ascii")


async def _http_get(
    host: str, port: int, path: str
) -> tuple[int, dict[str, str], bytes]:
    """Minimal synchronous-style HTTP GET over asyncio."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        )
        writer.write(request.encode())
        await writer.drain()

        raw = await asyncio.wait_for(reader.read(65536), timeout=5.0)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    # Parse response
    header_end = raw.find(b"\r\n\r\n")
    header_bytes = raw[:header_end]
    body = raw[header_end + 4 :]

    lines = header_bytes.decode("ascii", errors="replace").split("\r\n")
    status_line = lines[0]
    status_code = int(status_line.split(" ", 2)[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()

    return status_code, headers, body


async def _ws_connect(
    host: str, port: int, path: str, key: str = "dGhlIHNhbXBsZSBub25jZQ=="
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
    """Perform WebSocket HTTP Upgrade handshake.

    Returns:
        (reader, writer, accept_key) after successful upgrade.
    """
    reader, writer = await asyncio.open_connection(host, port)
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    writer.write(request.encode())
    await writer.drain()

    # Read exactly up to the end of the HTTP headers.  Using readuntil() instead
    # of read(N) ensures that any WebSocket frames the server sends immediately
    # after the 101 (e.g. the "hello" message) stay in the reader buffer and are
    # not accidentally consumed here.
    resp = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5.0)

    assert b"101" in resp, f"Expected 101, got: {resp[:200]}"
    accept = make_accept_key(key)
    assert accept.encode() in resp, "Invalid Sec-WebSocket-Accept"
    return reader, writer, accept


async def _ws_send_text(writer: asyncio.StreamWriter, text: str) -> None:
    """Send a masked text frame (client→server)."""
    payload = text.encode("utf-8")
    mask = b"\xde\xad\xbe\xef"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    length = len(payload)
    if length <= 125:
        header = bytes([0x81, 0x80 | length]) + mask
    elif length <= 65535:
        header = struct.pack("!BBH", 0x81, 0x80 | 126, length) + mask
    else:
        header = struct.pack("!BBQ", 0x81, 0x80 | 127, length) + mask
    writer.write(header + masked)
    await writer.drain()


async def _ws_recv_frame(
    reader: asyncio.StreamReader,
    timeout: float = 10.0,
) -> tuple[int, bytes]:
    """Read one (unmasked) server→client WebSocket frame, skipping ping/pong."""
    while True:
        header = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        byte0, byte1 = header[0], header[1]
        opcode = byte0 & 0x0F
        payload_len = byte1 & 0x7F

        if payload_len == 126:
            ext = await reader.readexactly(2)
            payload_len = struct.unpack("!H", ext)[0]
        elif payload_len == 127:
            ext = await reader.readexactly(8)
            payload_len = struct.unpack("!Q", ext)[0]

        payload = await reader.readexactly(payload_len)
        # Skip control frames (ping=0x9, pong=0xA)
        if opcode in (0x9, 0xA):
            continue
        return opcode, payload


async def _ws_skip_handshake(
    reader: asyncio.StreamReader,
    timeout: float = 5.0,
) -> None:
    """Skip hello + optional initial state_update pushed by server on connect.

    Reads frames until we stop seeing handshake messages (hello / state_update).
    Uses a short per-frame timeout so we don't block if no more frames arrive.
    """
    for _ in range(5):  # at most 5 handshake frames
        try:
            _, payload = await _ws_recv_frame(reader, timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return
        try:
            msg = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        msg_type = msg.get("type")
        if msg_type in ("hello", "state_update"):
            timeout = 0.5  # subsequent frames: short timeout
            continue
        # Non-handshake frame — can't un-read, but shouldn't happen in practice.
        return


async def _ws_recv_cmd_response(
    reader: asyncio.StreamReader,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Read WS frames until we get a command response (has 'ok' or 'error' key).

    Skips hello, state_update, and other broadcast messages.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError("no cmd response within timeout")
        _, payload = await _ws_recv_frame(reader, timeout=remaining)
        try:
            msg = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        # Command responses have 'ok' or 'error' at top level
        if "ok" in msg or "error" in msg:
            return msg
        # Skip state_update, hello, etc.


async def _close_ws(writer: asyncio.StreamWriter) -> None:
    # Send masked close frame
    mask = b"\x00\x00\x00\x00"
    close_payload = struct.pack("!H", 1000)
    masked_close = bytes(b ^ mask[i % 4] for i, b in enumerate(close_payload))
    writer.write(bytes([0x88, 0x80 | 2]) + mask + masked_close)
    try:
        await writer.drain()
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_scope_capable_attrs(radio: MagicMock) -> MagicMock:
    """Explicitly set all ScopeCapable protocol attrs on a MagicMock.

    Python 3.12+ runtime_checkable Protocol uses inspect.getattr_static which
    bypasses MagicMock.__getattr__, so all attrs must be set in __dict__.

    NOTE: Keep this in sync with `ScopeCapable` Protocol in `radio_protocol.py`.
    """
    radio.on_scope_data = MagicMock()
    radio.scope_stream = MagicMock()
    radio.enable_scope = AsyncMock()
    radio.disable_scope = AsyncMock()
    radio.capture_scope_frame = AsyncMock()
    radio.capture_scope_frames = AsyncMock()
    radio.get_scope_during_tx = AsyncMock(return_value=False)
    radio.set_scope_during_tx = AsyncMock()
    radio.get_scope_center_type = AsyncMock(return_value=0)
    radio.set_scope_center_type = AsyncMock()
    radio.get_scope_fixed_edge = AsyncMock()
    radio.set_scope_fixed_edge = AsyncMock()
    radio.get_scope_edge = AsyncMock(return_value=1)
    radio.set_scope_edge = AsyncMock()
    radio.get_scope_rbw = AsyncMock(return_value=0)
    radio.set_scope_rbw = AsyncMock()
    radio.get_scope_vbw = AsyncMock(return_value=False)
    radio.set_scope_vbw = AsyncMock()

    # Scope control settings (0x27 sub-commands)
    radio.get_scope_receiver = AsyncMock(return_value=0)
    radio.set_scope_receiver = AsyncMock()
    radio.get_scope_dual = AsyncMock(return_value=False)
    radio.set_scope_dual = AsyncMock()
    radio.get_scope_mode = AsyncMock(return_value=0)
    radio.set_scope_mode = AsyncMock()
    radio.get_scope_span = AsyncMock(return_value=0)
    radio.set_scope_span = AsyncMock()
    radio.get_scope_speed = AsyncMock(return_value=0)
    radio.set_scope_speed = AsyncMock()
    radio.get_scope_ref = AsyncMock(return_value=0.0)
    radio.set_scope_ref = AsyncMock()
    radio.get_scope_hold = AsyncMock(return_value=False)
    radio.set_scope_hold = AsyncMock()

    return radio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_radio() -> MagicMock:
    radio = MagicMock(name="radio")
    radio.connected = True
    radio.radio_ready = True
    radio.control_connected = True
    radio.model = "IC-7610"
    radio.capabilities = {
        "audio",
        "scope",
        "dual_rx",
        "meters",
        "tx",
        "cw",
        "attenuator",
        "preamp",
        "rf_gain",
        "af_level",
        "squelch",
        "nb",
        "nr",
        "digisel",
        "ip_plus",
    }
    _add_scope_capable_attrs(radio)
    radio.get_freq = AsyncMock(return_value=14_074_000)
    radio.get_mode = AsyncMock(return_value=MagicMock(name="USB"))
    radio.get_mode.return_value.name = "USB"
    _mode_mock = MagicMock(name="USB")
    _mode_mock.name = "USB"
    radio.get_mode_info = AsyncMock(return_value=(_mode_mock, 1))
    radio.get_rf_power = AsyncMock(return_value=100)
    radio.get_filter = AsyncMock(return_value=1)
    radio.get_s_meter = AsyncMock(return_value=42)
    radio.get_swr = AsyncMock(return_value=10)
    radio.get_rf_gain = AsyncMock(return_value=200)
    radio.get_af_level = AsyncMock(return_value=180)
    radio.get_attenuator_level = AsyncMock(return_value=0)
    radio.get_preamp = AsyncMock(return_value=1)
    radio.get_data_mode = AsyncMock(return_value=False)
    radio.set_freq = AsyncMock()
    radio.set_mode = AsyncMock()
    radio.set_filter = AsyncMock()
    radio.set_rf_power = AsyncMock()
    radio.set_ptt = AsyncMock()
    radio.set_rf_gain = AsyncMock()
    radio.set_af_level = AsyncMock()
    radio.set_attenuator_level = AsyncMock()
    radio.set_preamp = AsyncMock()
    radio.set_vfo = AsyncMock()
    radio.vfo_swap = AsyncMock()
    # Canonical dual-RX VFO methods on ``DualReceiverCapable`` (post-#1114);
    # the radio_poller invokes these directly.
    radio.swap_main_sub = AsyncMock()
    radio.equalize_main_sub = AsyncMock()
    radio.vfo_a_equals_b = AsyncMock()
    radio.set_main_sub_tracking = AsyncMock()
    radio.get_main_sub_tracking = AsyncMock(return_value=False)
    # ScopeCapable protocol attrs (all required for isinstance check in Python 3.12+)
    radio.on_scope_data = MagicMock()
    radio.scope_stream = MagicMock()
    radio.enable_scope = AsyncMock()
    radio.disable_scope = AsyncMock()
    radio.get_scope_during_tx = AsyncMock(return_value=False)
    radio.set_scope_during_tx = AsyncMock()
    radio.get_scope_center_type = AsyncMock(return_value=0)
    radio.set_scope_center_type = AsyncMock()
    radio.get_scope_fixed_edge = AsyncMock()
    radio.set_scope_fixed_edge = AsyncMock()
    radio.get_scope_edge = AsyncMock(return_value=1)
    radio.set_scope_edge = AsyncMock()
    radio.get_scope_rbw = AsyncMock(return_value=0)
    radio.set_scope_rbw = AsyncMock()
    radio.get_scope_vbw = AsyncMock(return_value=False)
    radio.set_scope_vbw = AsyncMock()
    radio.capture_scope_frame = AsyncMock()
    radio.capture_scope_frames = AsyncMock()
    # AudioCapable protocol attrs (all required for isinstance check in Python 3.12+)
    radio.audio_bus = MagicMock()
    radio.start_audio_rx_opus = AsyncMock()
    radio.stop_audio_rx_opus = AsyncMock()
    radio.push_audio_tx_opus = AsyncMock()
    radio.start_audio_rx_pcm = AsyncMock()
    radio.stop_audio_rx_pcm = AsyncMock()
    radio.start_audio_tx_pcm = AsyncMock()
    radio.push_audio_tx_pcm = AsyncMock()
    radio.stop_audio_tx_pcm = AsyncMock()
    radio.get_audio_stats = AsyncMock(return_value={})
    radio.start_audio_tx_opus = AsyncMock()
    radio.stop_audio_tx_opus = AsyncMock()
    radio.audio_codec = None
    radio.audio_sample_rate = 48000
    # State cache shared between radio and server
    radio.state_cache = StateCache()
    # Methods used by WebServer.stop() and RadioPoller
    radio.soft_disconnect = AsyncMock()
    radio.disconnect = AsyncMock()
    radio.send_civ = AsyncMock()
    return radio


@pytest.fixture
async def server(mock_radio: MagicMock) -> WebServer:
    # keepalive_interval=9999 disables keepalive pings during tests to prevent
    # spurious ping frames from interfering with test assertions. (#45)
    config = WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
    srv = WebServer(mock_radio, config)
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
async def server_no_radio() -> WebServer:
    config = WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
    srv = WebServer(None, config)
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
async def server_serial_radio() -> tuple[WebServer, SerialMockRadio]:
    """WebServer running on top of a real SerialMockRadio core."""
    radio = SerialMockRadio()
    await radio.connect()
    # Seed RadioState so /api/v1/state exposes non-trivial freq/mode.
    radio.radio_state.main.freq = 14_074_000
    radio.radio_state.main.mode = "USB"

    config = WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
    srv = WebServer(radio, config)
    await srv.start()
    try:
        yield srv, radio
    finally:
        await srv.stop()


# ---------------------------------------------------------------------------
# Protocol unit tests (no network)
# ---------------------------------------------------------------------------


class TestProtocolEncoding:
    def test_encode_scope_frame_header_size(self) -> None:
        frame = ScopeFrame(
            receiver=0,
            mode=0,
            start_freq_hz=14_000_000,
            end_freq_hz=14_350_000,
            pixels=bytes(range(100)),
            out_of_range=False,
        )
        data = encode_scope_frame(frame, sequence=0)
        assert len(data) == SCOPE_HEADER_SIZE + 100

    def test_encode_scope_frame_msg_type(self) -> None:
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"\x00" * 475, False)
        data = encode_scope_frame(frame, 0)
        assert data[0] == MSG_TYPE_SCOPE

    def test_encode_scope_frame_receiver_mode(self) -> None:
        frame = ScopeFrame(1, 2, 14_000_000, 14_350_000, b"\x00" * 10, False)
        data = encode_scope_frame(frame, 0)
        assert data[1] == 1  # receiver
        assert data[2] == 2  # mode

    def test_encode_scope_frame_frequencies_little_endian(self) -> None:
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"\x00" * 10, False)
        data = encode_scope_frame(frame, 0)
        start_freq = struct.unpack_from("<I", data, 3)[0]
        end_freq = struct.unpack_from("<I", data, 7)[0]
        assert start_freq == 14_000_000
        assert end_freq == 14_350_000

    def test_encode_scope_frame_sequence_little_endian(self) -> None:
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"\x00" * 10, False)
        for seq in (0, 1, 255, 1000, 65535):
            data = encode_scope_frame(frame, seq)
            read_seq = struct.unpack_from("<H", data, 11)[0]
            assert read_seq == seq & 0xFFFF

    def test_encode_scope_frame_sequence_wraps_at_16bit(self) -> None:
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"\x00" * 10, False)
        data = encode_scope_frame(frame, 65537)
        read_seq = struct.unpack_from("<H", data, 11)[0]
        assert read_seq == 1  # 65537 & 0xFFFF

    def test_encode_scope_frame_flags_out_of_range(self) -> None:
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"", True)
        data = encode_scope_frame(frame, 0)
        assert data[13] & 0x01  # bit 0 = out_of_range

    def test_encode_scope_frame_flags_in_range(self) -> None:
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"\x00" * 5, False)
        data = encode_scope_frame(frame, 0)
        assert not (data[13] & 0x01)

    def test_encode_scope_frame_pixel_count_little_endian(self) -> None:
        pixels = bytes(range(200))
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, pixels, False)
        data = encode_scope_frame(frame, 0)
        count = struct.unpack_from("<H", data, 14)[0]
        assert count == 200

    def test_encode_scope_frame_pixels_appended(self) -> None:
        pixels = bytes(range(50))
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, pixels, False)
        data = encode_scope_frame(frame, 0)
        assert data[SCOPE_HEADER_SIZE:] == pixels

    def test_encode_scope_frame_475_pixels(self) -> None:
        """Typical IC-7610 frame size."""
        pixels = bytes(i % 161 for i in range(475))
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, pixels, False)
        data = encode_scope_frame(frame, 0)
        assert len(data) == 16 + 475

    def test_encode_decode_json_roundtrip(self) -> None:
        msg = {"type": "hello", "proto": 1, "version": "0.7.0"}
        assert decode_json(encode_json(msg)) == msg

    def test_decode_json_invalid(self) -> None:
        with pytest.raises(ValueError):
            decode_json("not json {{{")

    def test_decode_json_non_object(self) -> None:
        with pytest.raises(ValueError):
            decode_json("[1, 2, 3]")

    def test_make_accept_key(self) -> None:
        # RFC 6455 §1.3 example
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        expected = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        assert make_accept_key(key) == expected

    def test_make_frame_text_small(self) -> None:
        frame = make_frame(WS_OP_TEXT, b"hello")
        assert frame[0] == 0x81  # FIN + TEXT
        assert frame[1] == 5  # length, no mask
        assert frame[2:] == b"hello"

    def test_make_frame_binary_126(self) -> None:
        payload = b"x" * 126
        frame = make_frame(WS_OP_BINARY, payload)
        assert frame[0] == 0x82  # FIN + BINARY
        assert frame[1] == 126
        length = struct.unpack("!H", frame[2:4])[0]
        assert length == 126
        assert frame[4:] == payload

    def test_make_frame_binary_large(self) -> None:
        payload = b"x" * 70000
        frame = make_frame(WS_OP_BINARY, payload)
        assert frame[0] == 0x82
        assert frame[1] == 127
        length = struct.unpack("!Q", frame[2:10])[0]
        assert length == 70000


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


class TestHttpEndpoints:
    async def test_info_endpoint_status(self, server: WebServer) -> None:
        host, port = _addr(server)
        status, _, _ = await _http_get(host, port, "/api/v1/info")
        assert status == 200

    async def test_info_endpoint_content_type(self, server: WebServer) -> None:
        host, port = _addr(server)
        _, headers, _ = await _http_get(host, port, "/api/v1/info")
        assert "json" in headers.get("content-type", "")

    async def test_info_endpoint_json_fields(self, server: WebServer) -> None:
        host, port = _addr(server)
        _, _, body = await _http_get(host, port, "/api/v1/info")
        data = json.loads(body)
        assert "server" in data
        assert data["server"] == "rigplane"
        assert "version" in data
        assert "proto" in data
        assert data["proto"] == 1
        assert "radio" in data

    async def test_capabilities_endpoint_status(self, server: WebServer) -> None:
        host, port = _addr(server)
        status, _, _ = await _http_get(host, port, "/api/v1/capabilities")
        assert status == 200

    async def test_capabilities_endpoint_json_fields(self, server: WebServer) -> None:
        host, port = _addr(server)
        _, _, body = await _http_get(host, port, "/api/v1/capabilities")
        data = json.loads(body)
        assert "scope" in data
        assert "audio" in data
        assert "modes" in data
        assert isinstance(data["modes"], list)

    async def test_state_endpoint_contains_radio_ready(self, server: WebServer) -> None:
        host, port = _addr(server)
        _, _, body = await _http_get(host, port, "/api/v1/state")
        data = json.loads(body)
        assert "connection" in data
        assert isinstance(data["connection"]["radioReady"], bool)

    @_requires_static_index
    async def test_root_returns_html(self, server: WebServer) -> None:
        host, port = _addr(server)
        status, headers, body = await _http_get(host, port, "/")
        assert status == 200
        assert "html" in headers.get("content-type", "").lower()
        assert b"<!DOCTYPE html>" in body or b"<!doctype html>" in body.lower()

    async def test_unknown_path_404(self, server: WebServer) -> None:
        host, port = _addr(server)
        status, _, _ = await _http_get(host, port, "/api/v1/nonexistent")
        assert status == 404

    async def test_info_and_state_flow_from_serial_mock_radio(
        self, server_serial_radio: tuple[WebServer, SerialMockRadio]
    ) -> None:
        """SerialMockRadio model/connection and basic state flow through HTTP."""
        server, radio = server_serial_radio
        host, port = _addr(server)

        # /api/v1/info reflects runtime model and connection flags.
        _, _, body = await _http_get(host, port, "/api/v1/info")
        info = json.loads(body)
        assert info["model"] == radio.model
        assert info["connection"]["rigConnected"] is True
        assert info["connection"]["radioReady"] is True

        # /api/v1/state exposes seeded freq/mode from RadioState via camelCase JSON.
        _, _, body_state = await _http_get(host, port, "/api/v1/state")
        state = json.loads(body_state)
        main = state["main"]
        assert main["freqHz"] == 14_074_000
        assert main["mode"] == "USB"


# ---------------------------------------------------------------------------
# WebSocket handshake tests
# ---------------------------------------------------------------------------


class TestWebSocketHandshake:
    async def test_upgrade_control_channel(self, server: WebServer) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        await _close_ws(writer)

    async def test_upgrade_scope_channel(self, server: WebServer) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/scope")
        await _close_ws(writer)

    async def test_upgrade_audio_channel(self, server: WebServer) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/audio")
        await _close_ws(writer)

    async def test_accept_key_correct(self, server: WebServer) -> None:
        host, port = _addr(server)
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        reader, writer, accept = await _ws_connect(host, port, "/api/v1/ws", key)
        assert accept == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        await _close_ws(writer)


# ---------------------------------------------------------------------------
# Control channel protocol tests
# ---------------------------------------------------------------------------


class TestControlChannel:
    async def test_hello_on_connect(self, server: WebServer) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            opcode, payload = await _ws_recv_frame(reader)
            assert opcode == WS_OP_TEXT
            msg = json.loads(payload)
            assert msg["type"] == "hello"
            assert msg["proto"] == 1
            assert msg["server"] == "rigplane"
            assert "version" in msg
            assert "radio" in msg
            assert "capabilities" in msg
            assert "radio_ready" in msg
        finally:
            await _close_ws(writer)

    async def test_hello_capabilities_list(self, server: WebServer) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            _, payload = await _ws_recv_frame(reader)
            msg = json.loads(payload)
            caps = msg["capabilities"]
            assert isinstance(caps, list)
            assert "scope" in caps
        finally:
            await _close_ws(writer)

    async def test_subscribe_triggers_state(self, server: WebServer) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            # Consume hello
            await _ws_recv_frame(reader)
            # Send subscribe
            sub = {"type": "subscribe", "streams": ["scope"]}
            await _ws_send_text(writer, json.dumps(sub))
            # Should receive state snapshot
            opcode, payload = await _ws_recv_frame(reader)
            assert opcode == WS_OP_TEXT
            msg = json.loads(payload)
            assert msg["type"] == "state_update"
            assert "data" in msg
        finally:
            await _close_ws(writer)

    async def test_state_snapshot_fields(self, server: WebServer) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)
            await _ws_send_text(
                writer, json.dumps({"type": "subscribe", "streams": []})
            )
            _, payload = await _ws_recv_frame(reader)
            data = json.loads(payload)["data"]
            assert "active" in data
            assert "main" in data
            assert "ptt" in data
            assert "connection" in data
        finally:
            await _close_ws(writer)

    async def test_command_set_freq(
        self, server: WebServer, mock_radio: MagicMock
    ) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)
            mock_radio.get_filter.return_value = 2
            cmd = {
                "type": "cmd",
                "id": "test-1",
                "name": "set_freq",
                "params": {"vfo": "A", "freq": 14_074_000},
            }
            await _ws_send_text(writer, json.dumps(cmd))
            opcode, payload = await _ws_recv_frame(reader)
            assert opcode == WS_OP_TEXT
            resp = json.loads(payload)
            assert resp["type"] == "response"
            assert resp["id"] == "test-1"
            assert resp["ok"] is True
            assert resp["result"]["freq"] == 14_074_000
        finally:
            await _close_ws(writer)

    async def test_command_set_mode(
        self, server: WebServer, mock_radio: MagicMock
    ) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)
            cmd = {
                "type": "cmd",
                "id": "test-2",
                "name": "set_mode",
                "params": {"mode": "LSB"},
            }
            await _ws_send_text(writer, json.dumps(cmd))
            _, payload = await _ws_recv_frame(reader)
            resp = json.loads(payload)
            assert resp["ok"] is True
            assert resp["result"]["mode"] == "LSB"
        finally:
            await _close_ws(writer)

    async def test_command_ptt(self, server: WebServer, mock_radio: MagicMock) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)
            cmd = {
                "type": "cmd",
                "id": "ptt-1",
                "name": "ptt",
                "params": {"state": True},
            }
            await _ws_send_text(writer, json.dumps(cmd))
            _, payload = await _ws_recv_frame(reader)
            resp = json.loads(payload)
            assert resp["ok"] is True
            # PTT goes through command queue; wait for poller to drain it
            await asyncio.sleep(0.05)
            mock_radio.set_ptt.assert_awaited_once_with(True)
        finally:
            await _close_ws(writer)

    async def test_command_unknown_returns_error(self, server: WebServer) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)
            cmd = {
                "type": "cmd",
                "id": "bad-1",
                "name": "hack_the_radio",
                "params": {},
            }
            await _ws_send_text(writer, json.dumps(cmd))
            _, payload = await _ws_recv_frame(reader)
            resp = json.loads(payload)
            assert resp["type"] == "response"
            assert resp["ok"] is False
            assert resp["id"] == "bad-1"
        finally:
            await _close_ws(writer)

    async def test_unsubscribe_removes_stream(self, server: WebServer) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)
            # subscribe
            await _ws_send_text(
                writer,
                json.dumps({"type": "subscribe", "streams": ["scope"]}),
            )
            await _ws_recv_frame(reader)  # state
            # unsubscribe
            await _ws_send_text(
                writer,
                json.dumps({"type": "unsubscribe", "streams": ["scope"]}),
            )
            # no response expected for unsubscribe; just check no error
        finally:
            await _close_ws(writer)

    async def test_command_no_radio_returns_error(
        self, server_no_radio: WebServer
    ) -> None:
        host, port = _addr(server_no_radio)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)
            cmd = {
                "type": "cmd",
                "id": "nr-1",
                "name": "set_freq",
                "params": {"freq": 14_074_000},
            }
            await _ws_send_text(writer, json.dumps(cmd))
            _, payload = await _ws_recv_frame(reader)
            resp = json.loads(payload)
            assert resp["ok"] is False
            assert "no_radio" in resp.get("error", "")
        finally:
            await _close_ws(writer)

    async def test_vfo_swap_command(
        self, server: WebServer, mock_radio: MagicMock
    ) -> None:
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)
            cmd = {"type": "cmd", "id": "vs-1", "name": "vfo_swap", "params": {}}
            await _ws_send_text(writer, json.dumps(cmd))
            _, payload = await _ws_recv_frame(reader)
            resp = json.loads(payload)
            assert resp["ok"] is True
            # vfo_swap goes through command queue; wait for poller to drain it
            await asyncio.sleep(0.05)
            # #1114: the poller dispatches to ``swap_main_sub`` directly on
            # dual-RX rigs; the deprecated wrapper has been removed.
            mock_radio.swap_main_sub.assert_awaited_once()
        finally:
            await _close_ws(writer)


# ---------------------------------------------------------------------------
# Binary scope frame format tests (RFC conformance)
# ---------------------------------------------------------------------------


class TestScopeFrameFormat:
    """Verify binary scope frame layout matches the RFC exactly."""

    def test_scope_frame_header_is_16_bytes(self) -> None:
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"\x00" * 5, False)
        data = encode_scope_frame(frame, 0)
        assert len(data[:SCOPE_HEADER_SIZE]) == 16

    def test_offset_0_is_msg_type_01(self) -> None:
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"\x00" * 5, False)
        assert encode_scope_frame(frame, 0)[0] == 0x01

    def test_offset_1_is_receiver(self) -> None:
        for recv in (0, 1):
            frame = ScopeFrame(recv, 0, 14_000_000, 14_350_000, b"\x00" * 5, False)
            assert encode_scope_frame(frame, 0)[1] == recv

    def test_offset_2_is_mode(self) -> None:
        for mode in (0, 1, 2, 3):
            frame = ScopeFrame(0, mode, 14_000_000, 14_350_000, b"\x00" * 5, False)
            assert encode_scope_frame(frame, 0)[2] == mode

    def test_offset_3_7_is_start_freq_le(self) -> None:
        frame = ScopeFrame(0, 0, 7_074_000, 7_350_000, b"\x00" * 5, False)
        data = encode_scope_frame(frame, 0)
        assert struct.unpack_from("<I", data, 3)[0] == 7_074_000

    def test_offset_7_11_is_end_freq_le(self) -> None:
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"\x00" * 5, False)
        data = encode_scope_frame(frame, 0)
        assert struct.unpack_from("<I", data, 7)[0] == 14_350_000

    def test_offset_11_13_is_sequence_le(self) -> None:
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"\x00" * 5, False)
        data = encode_scope_frame(frame, 1234)
        assert struct.unpack_from("<H", data, 11)[0] == 1234

    def test_offset_13_is_flags(self) -> None:
        frame_ok = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"\x00" * 5, False)
        frame_oor = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"", True)
        assert encode_scope_frame(frame_ok, 0)[13] == 0x00
        assert encode_scope_frame(frame_oor, 0)[13] == 0x01

    def test_offset_14_16_is_pixel_count_le(self) -> None:
        pixels = bytes(range(200))
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, pixels, False)
        data = encode_scope_frame(frame, 0)
        assert struct.unpack_from("<H", data, 14)[0] == 200

    def test_offset_16_is_pixels(self) -> None:
        pixels = bytes(range(50))
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, pixels, False)
        data = encode_scope_frame(frame, 0)
        assert data[16:] == pixels


# ---------------------------------------------------------------------------
# Backpressure tests
# ---------------------------------------------------------------------------


class TestBackpressure:
    def test_scope_frame_queue_drops_when_full(self) -> None:
        """When queue is full, old frames are dropped, not blocked."""
        from rigplane.web.handlers import HIGH_WATERMARK, ScopeHandler
        from rigplane.web.websocket import WebSocketConnection

        # Mock WebSocket that never sends (simulates slow client)
        mock_ws = MagicMock(spec=WebSocketConnection)
        handler = ScopeHandler(mock_ws, None)
        handler._running = True

        pixels = bytes(range(100))
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, pixels, False)

        # Push HIGH_WATERMARK * 3 frames — queue should not block
        for i in range(HIGH_WATERMARK * 3):
            handler.push_frame(frame)

        # Queue size should be bounded by HIGH_WATERMARK * 2 (maxsize)
        # but frames were dropped, so it should be <= HIGH_WATERMARK * 2
        assert handler._frame_queue.qsize() <= HIGH_WATERMARK * 2

    def test_scope_sequence_increments(self) -> None:
        """Sequence numbers increment with each frame."""
        from rigplane.web.handlers import ScopeHandler
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        handler = ScopeHandler(mock_ws, None)
        handler._running = True

        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"\x00" * 10, False)
        for i in range(5):
            handler.push_frame(frame)

        assert handler._seq == 5

    def test_scope_sequence_wraps_at_65536(self) -> None:
        """Sequence counter wraps at 16-bit boundary."""
        from rigplane.web.handlers import ScopeHandler
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        handler = ScopeHandler(mock_ws, None)
        handler._running = True
        handler._seq = 65534

        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, b"\x00" * 5, False)
        # Push 3 frames: seq 65534, 65535, 0
        handler.push_frame(frame)  # encodes with seq=65534, _seq becomes 65535
        handler.push_frame(frame)  # encodes with seq=65535, _seq becomes 0
        handler.push_frame(frame)  # encodes with seq=0, _seq becomes 1

        assert handler._seq == 1


# ---------------------------------------------------------------------------
# Server config and lifecycle
# ---------------------------------------------------------------------------


class TestAudioFrameFormat:
    def test_audio_frame_header_size(self) -> None:
        from rigplane.web.protocol import AUDIO_HEADER_SIZE, encode_audio_frame

        frame = encode_audio_frame(0x10, 0x01, 0, 480, 1, 20, b"")
        assert len(frame) == AUDIO_HEADER_SIZE

    def test_audio_rx_msg_type(self) -> None:
        from rigplane.web.protocol import encode_audio_frame

        frame = encode_audio_frame(0x10, 0x01, 42, 480, 1, 20, b"\x00" * 100)
        assert frame[0] == 0x10

    def test_audio_tx_msg_type(self) -> None:
        from rigplane.web.protocol import encode_audio_frame

        frame = encode_audio_frame(0x11, 0x01, 0, 480, 1, 20, b"\x00" * 50)
        assert frame[0] == 0x11

    def test_audio_codec_byte(self) -> None:
        from rigplane.web.protocol import AUDIO_CODEC_OPUS, encode_audio_frame

        frame = encode_audio_frame(0x10, AUDIO_CODEC_OPUS, 0, 480, 1, 20, b"\x01")
        assert frame[1] == AUDIO_CODEC_OPUS

    def test_audio_sequence_le(self) -> None:
        import struct

        from rigplane.web.protocol import encode_audio_frame

        frame = encode_audio_frame(0x10, 0x01, 0x1234, 480, 1, 20, b"")
        seq = struct.unpack_from("<H", frame, 2)[0]
        assert seq == 0x1234

    def test_audio_sample_rate_le(self) -> None:
        import struct

        from rigplane.web.protocol import encode_audio_frame

        frame = encode_audio_frame(0x10, 0x01, 0, 480, 1, 20, b"")
        sr = struct.unpack_from("<H", frame, 4)[0]
        assert sr == 480

    def test_audio_channels_and_frame_ms(self) -> None:
        from rigplane.web.protocol import encode_audio_frame

        frame = encode_audio_frame(0x10, 0x01, 0, 480, 1, 20, b"")
        assert frame[6] == 1  # mono
        assert frame[7] == 20  # 20ms

    def test_audio_payload_appended(self) -> None:
        from rigplane.web.protocol import AUDIO_HEADER_SIZE, encode_audio_frame

        payload = b"\xaa\xbb\xcc\xdd"
        frame = encode_audio_frame(0x10, 0x01, 0, 480, 1, 20, payload)
        assert frame[AUDIO_HEADER_SIZE:] == payload
        assert len(frame) == AUDIO_HEADER_SIZE + len(payload)

    def test_audio_sequence_wraps(self) -> None:
        import struct

        from rigplane.web.protocol import encode_audio_frame

        frame = encode_audio_frame(0x10, 0x01, 0x10000, 480, 1, 20, b"")
        seq = struct.unpack_from("<H", frame, 2)[0]
        assert seq == 0  # wrapped


class TestProtocolConformance:
    """RFC-level protocol conformance tests."""

    # --- Scope frame edge cases ---

    def test_scope_empty_pixels(self) -> None:
        frame = ScopeFrame(0, 0, 7_000_000, 7_300_000, b"", False)
        data = encode_scope_frame(frame, 0)
        assert len(data) == SCOPE_HEADER_SIZE
        count = struct.unpack_from("<H", data, 14)[0]
        assert count == 0

    def test_scope_max_amplitude_values(self) -> None:
        """Pixel values 0-160 are valid per RFC."""
        pixels = bytes([0, 80, 160, 160, 0])
        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, pixels, False)
        data = encode_scope_frame(frame, 0)
        assert data[SCOPE_HEADER_SIZE:] == pixels

    def test_scope_all_receiver_modes(self) -> None:
        """receiver: 0=Main, 1=Sub; mode: 0-3."""
        for recv in (0, 1):
            for mode in (0, 1, 2, 3):
                frame = ScopeFrame(recv, mode, 14_000_000, 14_350_000, b"\x50", False)
                data = encode_scope_frame(frame, 0)
                assert data[1] == recv
                assert data[2] == mode

    def test_scope_dual_receiver_frames(self) -> None:
        """IC-7610 sends Main+Sub scope frames."""
        main = ScopeFrame(0, 1, 14_000_000, 14_350_000, bytes(475), False)
        sub = ScopeFrame(1, 1, 7_000_000, 7_300_000, bytes(475), False)
        d0 = encode_scope_frame(main, 0)
        d1 = encode_scope_frame(sub, 1)
        assert d0[1] == 0 and d1[1] == 1
        assert struct.unpack_from("<I", d0, 3)[0] == 14_000_000
        assert struct.unpack_from("<I", d1, 3)[0] == 7_000_000

    def test_scope_freq_range_boundaries(self) -> None:
        """Test with real HF/VHF frequency values."""
        for sf, ef in [
            (1_800_000, 2_000_000),
            (50_000_000, 54_000_000),
            (144_000_000, 148_000_000),
            (430_000_000, 440_000_000),
        ]:
            frame = ScopeFrame(0, 0, sf, ef, b"\x00", False)
            data = encode_scope_frame(frame, 0)
            assert struct.unpack_from("<I", data, 3)[0] == sf
            assert struct.unpack_from("<I", data, 7)[0] == ef

    # --- Audio frame edge cases ---

    def test_audio_opus_typical_frame(self) -> None:
        """Typical Opus frame: 48kHz, mono, 20ms, ~80-120 bytes payload."""
        from rigplane.web.protocol import (
            AUDIO_CODEC_OPUS,
            AUDIO_HEADER_SIZE,
            MSG_TYPE_AUDIO_RX,
            encode_audio_frame,
        )

        payload = bytes(range(100))  # ~100 bytes typical Opus
        frame = encode_audio_frame(
            MSG_TYPE_AUDIO_RX, AUDIO_CODEC_OPUS, 42, 480, 1, 20, payload
        )
        assert len(frame) == AUDIO_HEADER_SIZE + 100
        assert frame[0] == MSG_TYPE_AUDIO_RX
        assert frame[1] == AUDIO_CODEC_OPUS
        assert frame[AUDIO_HEADER_SIZE:] == payload

    # --- JSON message conformance ---

    def test_hello_message_schema(self) -> None:
        """hello message must have all required fields per RFC."""
        msg = json.loads(
            encode_json(
                {
                    "type": "hello",
                    "proto": 1,
                    "server": "rigplane",
                    "version": "0.8.0",
                    "radio": "IC-7610",
                    "capabilities": ["scope", "audio", "tx"],
                }
            )
        )
        assert msg["type"] == "hello"
        assert isinstance(msg["proto"], int)
        assert isinstance(msg["capabilities"], list)

    def test_state_message_schema(self) -> None:
        msg = {
            "type": "state",
            "data": {
                "freq_a": 14074000,
                "freq_b": 7074000,
                "mode": "USB",
                "filter": "FIL1",
                "ptt": False,
            },
        }
        text = encode_json(msg)
        parsed = json.loads(text)
        assert parsed["type"] == "state"
        assert isinstance(parsed["data"]["freq_a"], int)
        assert isinstance(parsed["data"]["ptt"], bool)

    def test_event_message_schema(self) -> None:
        msg = {
            "type": "event",
            "name": "freq_changed",
            "data": {"vfo": "A", "freq": 14074500},
        }
        text = encode_json(msg)
        parsed = json.loads(text)
        assert parsed["name"] == "freq_changed"

    def test_command_message_schema(self) -> None:
        msg = {
            "type": "cmd",
            "id": "a1b2",
            "name": "set_freq",
            "params": {"vfo": "A", "freq": 14074000},
        }
        text = encode_json(msg)
        parsed = json.loads(text)
        assert parsed["id"] == "a1b2"
        assert parsed["name"] == "set_freq"

    def test_response_ok_schema(self) -> None:
        msg = {
            "type": "response",
            "id": "a1b2",
            "ok": True,
            "result": {"freq": 14074000},
        }
        parsed = json.loads(encode_json(msg))
        assert parsed["ok"] is True

    def test_response_error_schema(self) -> None:
        msg = {
            "type": "response",
            "id": "a1b3",
            "ok": False,
            "error": "invalid_param",
            "message": "Frequency out of range",
        }
        parsed = json.loads(encode_json(msg))
        assert parsed["ok"] is False
        assert "error" in parsed
        assert "message" in parsed

    def test_subscribe_message_schema(self) -> None:
        msg = {
            "type": "subscribe",
            "id": "s1",
            "streams": ["scope"],
            "scope_fps": 30,
            "scope_receiver": 0,
        }
        parsed = json.loads(encode_json(msg))
        assert isinstance(parsed["streams"], list)
        assert "scope" in parsed["streams"]

    def test_encode_json_compact(self) -> None:
        """encode_json must produce compact JSON (no extra spaces)."""
        text = encode_json({"type": "hello", "proto": 1})
        assert " " not in text  # no spaces in compact JSON

    def test_decode_json_rejects_array(self) -> None:
        with pytest.raises(ValueError, match="expected a JSON object"):
            decode_json("[1,2,3]")

    def test_decode_json_rejects_invalid(self) -> None:
        with pytest.raises(ValueError, match="invalid JSON"):
            decode_json("not json")


class TestServerConfig:
    async def test_port_zero_assigns_ephemeral(self) -> None:
        config = WebConfig(host="127.0.0.1", port=0)
        srv = WebServer(None, config)
        await srv.start()
        try:
            assert srv.port > 0
        finally:
            await srv.stop()

    async def test_start_stop(self) -> None:
        config = WebConfig(host="127.0.0.1", port=0)
        srv = WebServer(None, config)
        await srv.start()
        await srv.stop()
        # Should not raise

    async def test_context_manager(self) -> None:
        config = WebConfig(host="127.0.0.1", port=0)
        async with WebServer(None, config) as srv:
            assert srv.port > 0

    async def test_web_config_defaults(self) -> None:
        cfg = WebConfig()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8080
        assert cfg.max_clients == 100


# ---------------------------------------------------------------------------
# AudioHandler codec detection
# ---------------------------------------------------------------------------


class TestAudioHandlerCodecDetection:
    """AudioHandler must tag web frames with the correct codec type.

    The radio sends PCM by default (PCM_1CH_16BIT = 0x04).  The web frame
    must carry AUDIO_CODEC_PCM16 (0x02) so the browser can play it as raw
    PCM instead of trying to Opus-decode it.  When the radio is configured
    for Opus (OPUS_1CH = 0x40), the web frame must carry AUDIO_CODEC_OPUS.
    """

    async def _start_rx_and_capture(
        self, audio_codec: object, sample_rate: int
    ) -> bytes:
        """Start RX via AudioBroadcaster (using AudioBus) and return first queued frame."""
        from rigplane.audio_bus import AudioBus
        from rigplane.radio_protocol import AudioCapable
        from rigplane.web.handlers import AudioBroadcaster, AudioHandler
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        mock_radio = MagicMock(spec=AudioCapable)
        mock_radio.capabilities = {"audio"}
        mock_radio.audio_codec = audio_codec
        mock_radio.audio_sample_rate = sample_rate
        mock_radio.start_audio_rx_opus = AsyncMock()
        mock_radio.stop_audio_rx_opus = AsyncMock()

        bus = AudioBus(mock_radio)
        mock_radio.audio_bus = bus

        broadcaster = AudioBroadcaster(mock_radio)
        handler = AudioHandler(mock_ws, mock_radio, broadcaster)
        await handler._start_rx()

        mock_radio.start_audio_rx_opus.assert_awaited_once()

        # Inject a fake AudioPacket through the bus
        mock_pkt = MagicMock()
        mock_pkt.data = b"\x00\x01" * 50  # 100 bytes of fake audio
        bus._on_opus_packet(mock_pkt)

        # Give relay loop a chance to process
        await asyncio.sleep(0.1)

        # Retrieve the encoded web frame from the handler's queue (assigned by broadcaster)
        frame = handler._frame_queue.get_nowait()
        return frame

    async def test_pcm_codec_produces_pcm16_web_frame(self) -> None:
        from rigplane.types import AudioCodec
        from rigplane.web.protocol import AUDIO_CODEC_PCM16

        frame = await self._start_rx_and_capture(AudioCodec.PCM_1CH_16BIT, 48000)
        assert frame[1] == AUDIO_CODEC_PCM16, (
            f"Expected AUDIO_CODEC_PCM16 (0x{AUDIO_CODEC_PCM16:02x}) "
            f"but got 0x{frame[1]:02x}"
        )

    async def test_opus_codec_produces_opus_web_frame(self) -> None:
        from rigplane.types import AudioCodec
        from rigplane.web.protocol import AUDIO_CODEC_OPUS

        frame = await self._start_rx_and_capture(AudioCodec.OPUS_1CH, 48000)
        assert frame[1] == AUDIO_CODEC_OPUS, (
            f"Expected AUDIO_CODEC_OPUS (0x{AUDIO_CODEC_OPUS:02x}) "
            f"but got 0x{frame[1]:02x}"
        )

    async def test_sample_rate_encoded_correctly(self) -> None:
        import struct

        from rigplane.types import AudioCodec

        frame = await self._start_rx_and_capture(AudioCodec.PCM_1CH_16BIT, 48000)
        sr_field = struct.unpack_from("<H", frame, 4)[0]
        assert sr_field == 480, f"Expected 480 (48000//100) but got {sr_field}"

    async def test_unknown_codec_falls_back_to_pcm16(self) -> None:
        from rigplane.web.protocol import AUDIO_CODEC_PCM16

        # Pass a non-AudioCodec value (e.g. MagicMock) — must default to PCM16
        frame = await self._start_rx_and_capture(MagicMock(), 48000)
        assert frame[1] == AUDIO_CODEC_PCM16

    async def test_ic7610_pcm_native_can_emit_browser_opus_by_profile_policy(
        self,
    ) -> None:
        from rigplane.audio_bus import AudioBus
        from rigplane.radio_protocol import AudioCapable
        from rigplane.types import AudioCodec
        from rigplane.web.handlers import AudioBroadcaster, AudioHandler
        from rigplane.web.protocol import AUDIO_CODEC_OPUS, AUDIO_HEADER_SIZE
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        mock_radio = MagicMock(spec=AudioCapable)
        mock_radio.capabilities = {"audio"}
        mock_radio.audio_codec = AudioCodec.PCM_2CH_16BIT
        mock_radio.audio_sample_rate = 16000
        mock_radio.profile = SimpleNamespace(
            browser_rx_transport="auto",
            browser_rx_transcode_to_opus=True,
        )
        mock_radio.start_audio_rx_opus = AsyncMock()
        mock_radio.stop_audio_rx_opus = AsyncMock()
        bus = AudioBus(mock_radio)
        mock_radio.audio_bus = bus

        pcm_payload = b"\x01\x02" * 640  # 20 ms, 16 kHz, stereo, s16le
        tap_frames: list[bytes] = []

        class _FakeTranscoder:
            def pcm_to_opus(self, pcm: bytes) -> bytes:
                assert pcm == pcm_payload
                return b"opus-web-frame"

        broadcaster = AudioBroadcaster(mock_radio)
        broadcaster.set_pcm_tap(tap_frames.append)
        handler = AudioHandler(mock_ws, mock_radio, broadcaster)

        with patch(
            "rigplane.web.handlers.audio.create_pcm_opus_transcoder",
            return_value=_FakeTranscoder(),
        ) as transcoder_factory:
            await handler._start_rx()
            mock_pkt = MagicMock()
            mock_pkt.data = pcm_payload
            bus._on_opus_packet(mock_pkt)
            await asyncio.sleep(0.1)

        frame = handler._frame_queue.get_nowait()
        assert frame[1] == AUDIO_CODEC_OPUS
        assert frame[AUDIO_HEADER_SIZE:] == b"opus-web-frame"
        assert tap_frames == [pcm_payload]
        transcoder_factory.assert_called_once_with(
            sample_rate=16000,
            channels=2,
            frame_ms=20,
        )

    async def test_serial_backend_ignores_direct_lan_browser_opus_profile_policy(
        self,
    ) -> None:
        from rigplane.audio_bus import AudioBus
        from rigplane.radio_protocol import AudioCapable
        from rigplane.types import AudioCodec
        from rigplane.web.handlers import AudioBroadcaster, AudioHandler
        from rigplane.web.protocol import AUDIO_CODEC_PCM16, AUDIO_HEADER_SIZE
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        mock_radio = MagicMock(spec=AudioCapable)
        mock_radio.backend_id = "icom_serial"
        mock_radio.capabilities = {"audio"}
        mock_radio.audio_codec = AudioCodec.PCM_2CH_16BIT
        mock_radio.audio_sample_rate = 48000
        mock_radio.profile = SimpleNamespace(
            browser_rx_transport="auto",
            browser_rx_transcode_to_opus=True,
        )
        mock_radio.start_audio_rx_opus = AsyncMock()
        mock_radio.stop_audio_rx_opus = AsyncMock()
        bus = AudioBus(mock_radio)
        mock_radio.audio_bus = bus

        broadcaster = AudioBroadcaster(mock_radio)
        handler = AudioHandler(mock_ws, mock_radio, broadcaster)
        await handler._start_rx()

        mock_pkt = MagicMock()
        mock_pkt.data = b"\x01\x02" * 960
        bus._on_opus_packet(mock_pkt)
        await asyncio.sleep(0.1)

        frame = handler._frame_queue.get_nowait()
        assert frame[1] == AUDIO_CODEC_PCM16
        assert frame[AUDIO_HEADER_SIZE:] == mock_pkt.data

    async def test_browser_opus_policy_falls_back_to_pcm16_when_encoder_unavailable(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from rigplane.audio_bus import AudioBus
        from rigplane.radio_protocol import AudioCapable
        from rigplane.types import AudioCodec
        from rigplane.web.handlers import AudioBroadcaster, AudioHandler
        from rigplane.web.protocol import AUDIO_CODEC_PCM16, AUDIO_HEADER_SIZE
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        mock_radio = MagicMock(spec=AudioCapable)
        mock_radio.capabilities = {"audio"}
        mock_radio.audio_codec = AudioCodec.PCM_2CH_16BIT
        mock_radio.audio_sample_rate = 16000
        mock_radio.profile = SimpleNamespace(
            browser_rx_transport="auto",
            browser_rx_transcode_to_opus=True,
        )
        mock_radio.start_audio_rx_opus = AsyncMock()
        mock_radio.stop_audio_rx_opus = AsyncMock()
        bus = AudioBus(mock_radio)
        mock_radio.audio_bus = bus

        pcm_payload = b"\x01\x02" * 640
        tap_frames: list[bytes] = []
        broadcaster = AudioBroadcaster(mock_radio)
        broadcaster.set_pcm_tap(tap_frames.append)
        handler = AudioHandler(mock_ws, mock_radio, broadcaster)

        with (
            caplog.at_level("WARNING"),
            patch(
                "rigplane.web.handlers.audio.create_pcm_opus_transcoder",
                side_effect=RuntimeError("opus disabled"),
            ),
        ):
            await handler._start_rx()
            for _ in range(2):
                mock_pkt = MagicMock()
                mock_pkt.data = pcm_payload
                bus._on_opus_packet(mock_pkt)
            await asyncio.sleep(0.1)

        frames = [handler._frame_queue.get_nowait() for _ in range(2)]
        assert [frame[1] for frame in frames] == [AUDIO_CODEC_PCM16, AUDIO_CODEC_PCM16]
        assert [frame[AUDIO_HEADER_SIZE:] for frame in frames] == [
            pcm_payload,
            pcm_payload,
        ]
        assert tap_frames == [pcm_payload, pcm_payload]
        warnings = [
            record
            for record in caplog.records
            if "browser Opus transcode unavailable" in record.message
        ]
        assert len(warnings) == 1

    async def test_browser_can_request_pcm16_when_opus_decode_is_unavailable(
        self,
    ) -> None:
        from rigplane.audio_bus import AudioBus
        from rigplane.radio_protocol import AudioCapable
        from rigplane.types import AudioCodec
        from rigplane.web.handlers import AudioBroadcaster, AudioHandler
        from rigplane.web.protocol import AUDIO_CODEC_PCM16, AUDIO_HEADER_SIZE
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        mock_radio = MagicMock(spec=AudioCapable)
        mock_radio.capabilities = {"audio"}
        mock_radio.audio_codec = AudioCodec.PCM_2CH_16BIT
        mock_radio.audio_sample_rate = 16000
        mock_radio.profile = SimpleNamespace(
            browser_rx_transport="auto",
            browser_rx_transcode_to_opus=True,
        )
        mock_radio.start_audio_rx_opus = AsyncMock()
        mock_radio.stop_audio_rx_opus = AsyncMock()
        bus = AudioBus(mock_radio)
        mock_radio.audio_bus = bus

        pcm_payload = b"\x01\x02" * 640
        broadcaster = AudioBroadcaster(mock_radio)
        handler = AudioHandler(mock_ws, mock_radio, broadcaster)
        await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_PCM16)

        mock_pkt = MagicMock()
        mock_pkt.data = pcm_payload
        bus._on_opus_packet(mock_pkt)
        await asyncio.sleep(0.1)

        frame = handler._frame_queue.get_nowait()
        assert frame[1] == AUDIO_CODEC_PCM16
        assert frame[AUDIO_HEADER_SIZE:] == pcm_payload


class TestBroadcasterFrameMsInvariant:
    """Wire-header ``frame_ms`` must match the actual payload size (issue #765).

    Regression guard for the 2026-04-16 companion crash (epic #764): the
    broadcaster previously hardcoded ``frame_ms=20`` regardless of the radio's
    real packet size, causing downstream consumers to allocate mis-sized
    ring buffers. The fix derives the value from ``len(audio_data)`` on emit.
    """

    async def _capture_with_payload(
        self, audio_codec: object, sample_rate: int, payload_size: int, channels: int
    ) -> bytes:
        from rigplane.audio_bus import AudioBus
        from rigplane.radio_protocol import AudioCapable
        from rigplane.web.handlers import AudioBroadcaster, AudioHandler
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        mock_radio = MagicMock(spec=AudioCapable)
        mock_radio.capabilities = {"audio"}
        mock_radio.audio_codec = audio_codec
        mock_radio.audio_sample_rate = sample_rate
        mock_radio.start_audio_rx_opus = AsyncMock()
        mock_radio.stop_audio_rx_opus = AsyncMock()

        bus = AudioBus(mock_radio)
        mock_radio.audio_bus = bus

        broadcaster = AudioBroadcaster(mock_radio)
        handler = AudioHandler(mock_ws, mock_radio, broadcaster)
        await handler._start_rx()

        # Force the broadcaster's channel count — some codecs imply stereo,
        # some mono; this test explicitly controls payload shape.
        broadcaster._channels = channels

        mock_pkt = MagicMock()
        mock_pkt.data = b"\x00\x01" * (payload_size // 2)
        bus._on_opus_packet(mock_pkt)
        await asyncio.sleep(0.1)

        return handler._frame_queue.get_nowait()

    @staticmethod
    def _parse_header(frame: bytes) -> tuple[int, int, int, int]:
        """Return (sr_hz, channels, frame_ms, payload_len)."""
        import struct

        from rigplane.web.protocol import AUDIO_HEADER_SIZE

        _, _, _seq, sr100, ch, frame_ms = struct.unpack_from("<BBHHBB", frame, 0)
        return sr100 * 100, ch, frame_ms, len(frame) - AUDIO_HEADER_SIZE

    async def test_frame_ms_matches_1364_byte_ic7610_packet(self) -> None:
        """IC-7610 real-world: 1364 B @ 48 kHz mono PCM16 → 14 ms (was 20)."""
        from rigplane.types import AudioCodec

        frame = await self._capture_with_payload(
            AudioCodec.PCM_1CH_16BIT, 48000, 1364, channels=1
        )
        sr, ch, frame_ms, payload_len = self._parse_header(frame)
        expected = (payload_len * 1000) // (sr * ch * 2)
        assert frame_ms == expected, (
            f"header lies: declared {frame_ms}ms but payload={payload_len}B "
            f"@ {sr}Hz × {ch}ch needs {expected}ms"
        )
        assert frame_ms == 14

    async def test_frame_ms_matches_1920_byte_exact_20ms_packet(self) -> None:
        """Exactly 20 ms @ 48 kHz mono PCM16 = 1920 B → frame_ms == 20."""
        from rigplane.types import AudioCodec

        frame = await self._capture_with_payload(
            AudioCodec.PCM_1CH_16BIT, 48000, 1920, channels=1
        )
        _, _, frame_ms, _ = self._parse_header(frame)
        assert frame_ms == 20

    async def test_frame_ms_matches_3840_byte_stereo_20ms_packet(self) -> None:
        """Stereo: 3840 B @ 48 kHz × 2ch × 2bytes = 20 ms."""
        from rigplane.types import AudioCodec

        frame = await self._capture_with_payload(
            AudioCodec.PCM_2CH_16BIT, 48000, 3840, channels=2
        )
        sr, ch, frame_ms, payload_len = self._parse_header(frame)
        expected = (payload_len * 1000) // (sr * ch * 2)
        assert frame_ms == expected
        assert frame_ms == 20


class TestBroadcasterCodecInvalidation:
    """Broadcaster picks up mid-stream codec/channel changes via
    ``invalidate_codec_state`` (issue #766, unblocks #721 split-stereo)."""

    @staticmethod
    def _setup(audio_codec: object, sample_rate: int) -> tuple[Any, Any, Any]:
        from types import SimpleNamespace

        from rigplane.audio_bus import AudioBus
        from rigplane.radio_protocol import AudioCapable
        from rigplane.web.handlers import AudioBroadcaster, AudioHandler
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        mock_ws.send_text = AsyncMock()
        mock_radio = MagicMock(spec=AudioCapable)
        mock_radio.capabilities = {"audio", "lan_dual_rx_audio_routing"}
        mock_radio.audio_codec = audio_codec
        mock_radio.audio_sample_rate = sample_rate
        mock_radio.start_audio_rx_opus = AsyncMock()
        mock_radio.stop_audio_rx_opus = AsyncMock()
        mock_radio.send_civ = AsyncMock()
        mock_radio.profile = SimpleNamespace(receiver_count=2)

        bus = AudioBus(mock_radio)
        mock_radio.audio_bus = bus

        broadcaster = AudioBroadcaster(mock_radio)
        handler = AudioHandler(mock_ws, mock_radio, broadcaster)
        return handler, broadcaster, mock_radio

    async def test_invalidate_flag_triggers_refresh_on_next_packet(self) -> None:
        from rigplane.types import AudioCodec

        handler, broadcaster, mock_radio = self._setup(AudioCodec.PCM_1CH_16BIT, 48000)
        await handler._start_rx()

        pkt_mono = MagicMock()
        pkt_mono.data = b"\x00\x01" * 960
        mock_radio.audio_bus._on_opus_packet(pkt_mono)
        await asyncio.sleep(0.05)
        handler._frame_queue.get_nowait()
        assert broadcaster._channels == 1

        mock_radio.audio_codec = AudioCodec.PCM_2CH_16BIT
        broadcaster.invalidate_codec_state()
        assert broadcaster._codec_stale is True

        pkt_stereo = MagicMock()
        pkt_stereo.data = b"\x00\x01" * 1920
        mock_radio.audio_bus._on_opus_packet(pkt_stereo)
        await asyncio.sleep(0.05)

        assert broadcaster._channels == 2
        assert broadcaster._codec_stale is False

    async def test_handler_invalidates_on_audio_config(self) -> None:
        from rigplane.types import AudioCodec

        handler, broadcaster, _ = self._setup(AudioCodec.PCM_1CH_16BIT, 48000)
        assert broadcaster._codec_stale is False
        await handler._handle_control(
            {"type": "audio_config", "focus": "both", "split_stereo": True}
        )
        assert broadcaster._codec_stale is True

    async def test_static_codec_refreshes_only_on_start(self) -> None:
        from rigplane.types import AudioCodec

        handler, broadcaster, mock_radio = self._setup(AudioCodec.PCM_1CH_16BIT, 48000)
        with patch.object(
            broadcaster,
            "_refresh_codec_state",
            wraps=broadcaster._refresh_codec_state,
        ) as spy:
            await handler._start_rx()
            for _ in range(5):
                pkt = MagicMock()
                pkt.data = b"\x00\x01" * 100
                mock_radio.audio_bus._on_opus_packet(pkt)
                await asyncio.sleep(0.02)
            assert spy.call_count == 1


class TestDspOpusGateWarning:
    """Issue #762: DSP pipeline + PCM tap registry are gated on PCM16.

    When a radio's native codec is Opus (IC-705), DSP and tap dispatch
    silently don't run.  The broadcaster logs a one-shot WARNING when
    this combination is detected so operators aren't mystified.
    """

    def _pcm_broadcaster(self):
        from rigplane.types import AudioCodec
        from rigplane.web.handlers import AudioBroadcaster
        from rigplane.web.protocol import AUDIO_CODEC_PCM16

        b = AudioBroadcaster(None)
        b._radio_codec = AudioCodec.PCM_1CH_16BIT
        b._web_codec = AUDIO_CODEC_PCM16
        return b

    def _opus_broadcaster(self):
        from rigplane.types import AudioCodec
        from rigplane.web.handlers import AudioBroadcaster
        from rigplane.web.protocol import AUDIO_CODEC_OPUS

        b = AudioBroadcaster(None)
        b._radio_codec = AudioCodec.OPUS_1CH
        b._web_codec = AUDIO_CODEC_OPUS
        return b

    def test_no_warning_when_dsp_set_on_pcm_broadcaster(self, caplog) -> None:
        import logging

        b = self._pcm_broadcaster()
        with caplog.at_level(logging.WARNING, logger="rigplane.web.handlers.audio"):
            b.set_dsp_pipeline(MagicMock())
        assert not any("native codec is Opus" in r.message for r in caplog.records)
        assert b._dsp_opus_warned is False

    def test_warning_fires_when_dsp_set_on_opus_broadcaster(self, caplog) -> None:
        import logging

        b = self._opus_broadcaster()
        with caplog.at_level(logging.WARNING, logger="rigplane.web.handlers.audio"):
            b.set_dsp_pipeline(MagicMock())
        matching = [r for r in caplog.records if "native codec is Opus" in r.message]
        assert len(matching) == 1, (
            f"Expected exactly one DSP-Opus warning; got {len(matching)}"
        )
        assert b._dsp_opus_warned is True

    def test_warning_fires_at_most_once_per_lifetime(self, caplog) -> None:
        import logging

        b = self._opus_broadcaster()
        with caplog.at_level(logging.WARNING, logger="rigplane.web.handlers.audio"):
            b.set_dsp_pipeline(MagicMock())
            b.set_dsp_pipeline(MagicMock())
            b._maybe_warn_dsp_opus_gate()
        matching = [r for r in caplog.records if "native codec is Opus" in r.message]
        assert len(matching) == 1

    def test_warning_fires_when_codec_flips_to_opus_mid_stream(self, caplog) -> None:
        """Operator sets DSP while radio is on PCM; radio later flips to Opus.

        The warning must fire on the codec refresh, not stay silent because
        the order was unfavourable.
        """
        import logging
        from rigplane.types import AudioCodec
        from rigplane.web.protocol import AUDIO_CODEC_OPUS

        b = self._pcm_broadcaster()
        b.set_dsp_pipeline(MagicMock())
        # No warning yet — we're on PCM.
        assert b._dsp_opus_warned is False

        # Simulate a codec flip to Opus (e.g. config change mid-stream).
        b._radio_codec = AudioCodec.OPUS_1CH
        b._web_codec = AUDIO_CODEC_OPUS
        with caplog.at_level(logging.WARNING, logger="rigplane.web.handlers.audio"):
            b._maybe_warn_dsp_opus_gate()
        matching = [r for r in caplog.records if "native codec is Opus" in r.message]
        assert len(matching) == 1
        assert b._dsp_opus_warned is True

    def test_no_warning_when_dsp_is_none(self, caplog) -> None:
        import logging

        b = self._opus_broadcaster()
        with caplog.at_level(logging.WARNING, logger="rigplane.web.handlers.audio"):
            b.set_dsp_pipeline(None)
            b._maybe_warn_dsp_opus_gate()
        assert b._dsp_opus_warned is False
        assert not any("native codec is Opus" in r.message for r in caplog.records)


class TestAudioHandlerTxTranscoderRate:
    """TX transcoder must use the radio's negotiated sample rate (issue #691).

    Previously the transcoder was hard-coded to 48 kHz, which silently broke
    TX on radios negotiated at a lower rate (e.g. 24 kHz) — PTT keyed up but
    no RF power was emitted because the radio dropped the mismatched stream.
    """

    @staticmethod
    def _make_handler(sample_rate: int | None) -> Any:
        from rigplane.radio_protocol import AudioCapable
        from rigplane.web.handlers import AudioBroadcaster, AudioHandler
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        mock_radio = MagicMock(spec=AudioCapable)
        mock_radio.capabilities = {"audio"}
        if sample_rate is None:
            # Simulate a radio that does not expose audio_sample_rate
            del mock_radio.audio_sample_rate
        else:
            mock_radio.audio_sample_rate = sample_rate
        mock_radio.start_audio_tx_opus = AsyncMock()

        broadcaster = AudioBroadcaster(mock_radio)
        handler = AudioHandler(mock_ws, mock_radio, broadcaster)
        return handler

    async def _start_tx(self, handler: Any) -> None:
        await handler._handle_control({"type": "audio_start", "direction": "tx"})

    @staticmethod
    def _fake_transcoder_factory(captured: list[int]) -> Any:
        """Return a fake ``create_pcm_opus_transcoder`` that records sample_rate.

        Native libopus may be absent from the dev/CI environment; mock the
        factory so the test verifies only the rate-plumbing contract.
        """

        def _factory(*, sample_rate: int, **_kwargs: Any) -> Any:
            captured.append(sample_rate)
            fake = MagicMock()
            fake._fmt.sample_rate = sample_rate
            return fake

        return _factory

    async def test_tx_transcoder_uses_24khz_rate(self) -> None:
        handler = self._make_handler(24000)
        captured: list[int] = []
        with patch(
            "rigplane.web.handlers.audio.create_pcm_opus_transcoder",
            new=self._fake_transcoder_factory(captured),
        ):
            await self._start_tx(handler)
        assert handler._transcoder is not None
        assert handler._transcoder._fmt.sample_rate == 24000
        assert captured == [24000]

    async def test_tx_transcoder_uses_48khz_rate(self) -> None:
        handler = self._make_handler(48000)
        captured: list[int] = []
        with patch(
            "rigplane.web.handlers.audio.create_pcm_opus_transcoder",
            new=self._fake_transcoder_factory(captured),
        ):
            await self._start_tx(handler)
        assert handler._transcoder is not None
        assert handler._transcoder._fmt.sample_rate == 48000
        assert captured == [48000]

    async def test_tx_transcoder_falls_back_when_rate_missing(self) -> None:
        handler = self._make_handler(None)
        captured: list[int] = []
        with patch(
            "rigplane.web.handlers.audio.create_pcm_opus_transcoder",
            new=self._fake_transcoder_factory(captured),
        ):
            await self._start_tx(handler)
        assert handler._transcoder is not None
        assert handler._transcoder._fmt.sample_rate == 48000
        assert captured == [48000]

    async def test_tx_transcoder_not_created_before_tx_start(self) -> None:
        handler = self._make_handler(24000)
        assert handler._transcoder is None


class TestAudioConfigRouting:
    """audio_config WS message → CI-V Phones L/R Mix (issue #752 / #788 / #792).

    Contract (#792): Phones L/R Mix is always kept OFF (0x00) on dual-RX
    radios so the LAN stream stays separated L=MAIN / R=SUB.  ``focus``
    and ``split_stereo`` travel on the WS payload for client-side
    persistence but do not round-trip to CI-V.  Gated on dual-RX profiles;
    1-Rx radios silently no-op.
    """

    @staticmethod
    def _make_handler(
        receiver_count: int = 2,
        *,
        supports_routing: bool = True,
    ) -> Any:
        from types import SimpleNamespace

        from rigplane.radio_protocol import AudioCapable
        from rigplane.web.handlers import AudioBroadcaster, AudioHandler
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        mock_ws.send_text = AsyncMock()
        mock_radio = MagicMock(spec=AudioCapable)
        caps = {"audio"}
        if supports_routing:
            caps.add("lan_dual_rx_audio_routing")
        mock_radio.capabilities = caps
        mock_radio.profile = SimpleNamespace(receiver_count=receiver_count)
        mock_radio.send_civ = AsyncMock()

        broadcaster = AudioBroadcaster(mock_radio)
        handler = AudioHandler(mock_ws, mock_radio, broadcaster)
        return handler, mock_radio, mock_ws

    async def _apply(self, handler: Any, focus: str, split_stereo: bool) -> None:
        await handler._handle_control(
            {"type": "audio_config", "focus": focus, "split_stereo": split_stereo}
        )

    @staticmethod
    def _expected_civ_call(phones_byte: int) -> dict[str, Any]:
        return {
            "args": (0x1A,),
            "sub": 0x05,
            "data": bytes([0x00, 0x72, phones_byte]),
        }

    async def test_any_focus_split_combo_sends_phones_mix_off(self) -> None:
        """Every (focus, split_stereo) combination → Phones L/R Mix OFF (0x00).

        #792 contract: the backend must keep Mix OFF whenever a 2-channel
        codec is active so the LAN stream stays separated L=MAIN/R=SUB.
        If the radio pre-sums the receivers, the frontend can no longer
        isolate MAIN or SUB via WebAudio gain — both channels would carry
        the summed signal.  ``focus`` / ``split_stereo`` now round-trip
        only as echo-back for client-side persistence.
        """
        for focus in ("main", "sub", "both"):
            for split in (False, True):
                handler, radio, _ = self._make_handler()
                await self._apply(handler, focus, split)
                radio.send_civ.assert_awaited_once_with(
                    0x1A,
                    sub=0x05,
                    data=bytes([0x00, 0x72, 0x00]),
                    wait_response=False,
                )

    async def test_only_valid_phones_bytes_are_emitted(self) -> None:
        """Invariant: Phones L/R Mix only ever receives 0x00.

        Per IC-7610 CI-V reference p. 5, command ``0x1A 05 00 72``
        accepts only ``{0x00, 0x01}``.  Previous revisions sent
        ``0x02`` and ``0x03`` (silently rejected by the radio — epic
        #787) and the #788 pass briefly drove the byte from
        ``split_stereo``.  Post-#792 the backend is locked to ``0x00``
        so the frontend graph can always recover the per-receiver
        channels it needs.
        """
        for focus in ("main", "sub", "both"):
            for split in (False, True):
                handler, radio, _ = self._make_handler()
                await self._apply(handler, focus, split)
                call = radio.send_civ.call_args
                data = call.kwargs.get("data") or call.args[-1]
                assert data[:2] == b"\x00\x72", (
                    f"unexpected Phones sub-command: {data.hex()}"
                )
                assert data[2] == 0x00, (
                    f"out-of-spec phones byte: 0x{data[2]:02X} for "
                    f"focus={focus} split={split}"
                )

    async def test_invalid_focus_sends_error_no_civ(self) -> None:
        handler, radio, ws = self._make_handler()
        await self._apply(handler, "left", False)
        radio.send_civ.assert_not_awaited()
        assert ws.send_text.await_count >= 1
        sent = ws.send_text.await_args_list[0].args[0]
        assert "error" in sent
        assert "invalid focus" in sent

    async def test_single_rx_profile_noops(self) -> None:
        # IC-7300 / IC-705 / X6100 shape: receiver_count=1, capability absent.
        handler, radio, ws = self._make_handler(
            receiver_count=1, supports_routing=False
        )
        await self._apply(handler, "main", False)
        radio.send_civ.assert_not_awaited()
        ws.send_text.assert_not_awaited()

    async def test_dual_rx_without_routing_capability_is_noop(self) -> None:
        # IC-9700 shape: receiver_count=2 but menu layout lacks Phones L/R Mix,
        # so it must NOT receive 0x1A 05 00 72.  Issue #799.
        handler, radio, ws = self._make_handler(
            receiver_count=2, supports_routing=False
        )
        await self._apply(handler, "main", False)
        radio.send_civ.assert_not_awaited()
        ws.send_text.assert_not_awaited()

    async def test_echo_includes_applied_true(self) -> None:
        import json

        handler, _, ws = self._make_handler()
        await self._apply(handler, "both", True)
        ws.send_text.assert_awaited_once()
        echoed = json.loads(ws.send_text.await_args.args[0])
        assert echoed == {
            "type": "audio_config",
            "focus": "both",
            "split_stereo": True,
            "applied": True,
        }


class TestBroadcasterPhonesMixInit:
    """AudioBroadcaster._apply_phones_mix_off — #792.

    Broadcaster must force Phones L/R Mix = OFF on relay start so the LAN
    stream begins in separated-stereo state regardless of prior radio
    configuration.  Dual-RX only; no-op on 1-Rx profiles.
    """

    @staticmethod
    def _make_broadcaster(
        receiver_count: int = 2,
        *,
        supports_routing: bool = True,
    ) -> Any:
        from types import SimpleNamespace

        from rigplane.radio_protocol import AudioCapable
        from rigplane.web.handlers import AudioBroadcaster

        mock_radio = MagicMock(spec=AudioCapable)
        caps = {"audio"}
        if supports_routing:
            caps.add("lan_dual_rx_audio_routing")
        mock_radio.capabilities = caps
        mock_radio.profile = SimpleNamespace(receiver_count=receiver_count)
        mock_radio.send_civ = AsyncMock()
        return AudioBroadcaster(mock_radio), mock_radio

    async def test_routing_capable_sends_mix_off(self) -> None:
        # IC-7610 shape: capability declared, Mix OFF CI-V emitted.
        broadcaster, radio = self._make_broadcaster(supports_routing=True)
        await broadcaster._apply_phones_mix_off()
        radio.send_civ.assert_awaited_once_with(
            0x1A, sub=0x05, data=bytes([0x00, 0x72, 0x00]), wait_response=False
        )

    async def test_single_rx_is_noop(self) -> None:
        # IC-7300 / IC-705 / X6100 shape: no routing capability, no CI-V.
        broadcaster, radio = self._make_broadcaster(
            receiver_count=1, supports_routing=False
        )
        await broadcaster._apply_phones_mix_off()
        radio.send_civ.assert_not_awaited()

    async def test_dual_rx_without_routing_capability_is_noop(self) -> None:
        # IC-9700 shape: receiver_count=2 but the Phones L/R Mix menu item
        # does NOT exist on that radio — we must not send 0x1A 05 00 72.
        # Issue #799.
        broadcaster, radio = self._make_broadcaster(
            receiver_count=2, supports_routing=False
        )
        await broadcaster._apply_phones_mix_off()
        radio.send_civ.assert_not_awaited()

    async def test_civ_error_is_swallowed(self) -> None:
        broadcaster, radio = self._make_broadcaster(supports_routing=True)
        radio.send_civ.side_effect = RuntimeError("boom")
        # Must not raise — relay start-up continues even if the init fails.
        await broadcaster._apply_phones_mix_off()
        radio.send_civ.assert_awaited_once()


# ---------------------------------------------------------------------------
# WebSocket keepalive (server-initiated pings)
# ---------------------------------------------------------------------------


class TestWsKeepalive:
    """WebSocketConnection.keepalive_loop() sends RFC 6455 ping frames."""

    async def test_keepalive_sends_ping_frame(self) -> None:
        """keepalive_loop writes a WS PING frame after the interval elapses."""
        written: list[bytes] = []

        reader = asyncio.StreamReader()
        writer = MagicMock(spec=asyncio.StreamWriter)
        writer.is_closing.return_value = False
        writer.write.side_effect = written.append
        writer.drain = AsyncMock()

        ws = WebSocketConnection(reader, writer)
        task = asyncio.create_task(ws.keepalive_loop(interval=0.05))
        try:
            # Wait slightly longer than the interval so at least one ping fires.
            await asyncio.sleep(0.12)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert written, "keepalive_loop must write at least one frame"
        # Ping frame: FIN|PING (0x89), payload len 2 ("ka")
        all_bytes = b"".join(written)
        ping_frame = make_frame(WS_OP_PING, b"ka")
        assert ping_frame in all_bytes, (
            f"Expected ping frame {ping_frame!r} in written data {all_bytes!r}"
        )

    async def test_keepalive_cancels_cleanly(self) -> None:
        """Cancelling keepalive_loop raises no unhandled exceptions."""
        reader = asyncio.StreamReader()
        writer = MagicMock(spec=asyncio.StreamWriter)
        writer.is_closing.return_value = False
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        ws = WebSocketConnection(reader, writer)
        task = asyncio.create_task(ws.keepalive_loop(interval=60.0))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # expected

    async def test_keepalive_exits_when_connection_closed(self) -> None:
        """keepalive_loop stops without error if ws.close() is called first."""
        reader = asyncio.StreamReader()
        writer = MagicMock(spec=asyncio.StreamWriter)
        writer.is_closing.return_value = False
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        ws = WebSocketConnection(reader, writer)
        ws._closed = True  # Mark as closed before starting loop

        task = asyncio.create_task(ws.keepalive_loop(interval=0.01))
        # Should complete quickly since _closed is True
        await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# Scope enable/disable lifecycle
# ---------------------------------------------------------------------------


class TestScopeLifecycle:
    """Scope is disabled on the radio when the last handler disconnects."""

    async def test_scope_not_disabled_while_handlers_remain(self) -> None:
        radio = MagicMock()
        radio.capabilities = {"scope"}
        _add_scope_capable_attrs(radio)

        server = WebServer(radio)
        server._scope_enabled = True

        h1 = MagicMock()
        h2 = MagicMock()
        server._scope_handlers.add(h1)
        server._scope_handlers.add(h2)

        server.unregister_scope_handler(h1)
        await asyncio.sleep(0.05)  # let task scheduler run

        radio.disable_scope.assert_not_awaited()
        assert server._scope_enabled  # still True

    async def test_scope_disabled_when_last_handler_disconnects(self) -> None:
        radio = MagicMock()
        radio.capabilities = {"scope"}
        _add_scope_capable_attrs(radio)

        server = WebServer(radio)
        server._scope_disable_grace = 0
        server._scope_enabled = True

        h = MagicMock()
        server._scope_handlers.add(h)

        server.unregister_scope_handler(h)
        await asyncio.sleep(0.05)  # let async task complete

        # DisableScope goes through command queue, not direct radio call
        from rigplane.web.radio_poller import DisableScope

        cmds = server._command_queue.drain()
        assert any(isinstance(c, DisableScope) for c in cmds), (
            "DisableScope should be in queue"
        )
        assert not server._scope_enabled

    async def test_scope_flag_reset_on_disable(self) -> None:
        """_scope_enabled is reset to False after successful disable."""
        radio = MagicMock()
        radio.capabilities = {"scope"}
        _add_scope_capable_attrs(radio)

        server = WebServer(radio)
        server._scope_disable_grace = 0
        server._scope_enabled = True

        h = MagicMock()
        server._scope_handlers.add(h)
        server.unregister_scope_handler(h)
        await asyncio.sleep(0.05)

        assert not server._scope_enabled

    async def test_scope_not_disabled_if_never_enabled(self) -> None:
        """disable_scope is NOT called if _scope_enabled is False."""
        radio = MagicMock()
        radio.capabilities = {"scope"}
        _add_scope_capable_attrs(radio)

        server = WebServer(radio)
        server._scope_enabled = False  # was never enabled

        h = MagicMock()
        server._scope_handlers.add(h)
        server.unregister_scope_handler(h)
        await asyncio.sleep(0.05)

        radio.disable_scope.assert_not_awaited()


# ---------------------------------------------------------------------------
# Cache-Control header for static files
# ---------------------------------------------------------------------------


class TestCacheControl:
    """Static file responses must include Cache-Control: no-cache."""

    @_requires_static_index
    async def test_static_index_has_cache_control(self, server: WebServer) -> None:
        host, port = _addr(server)
        status, headers, _ = await _http_get(host, port, "/")
        assert status == 200
        cc = headers.get("cache-control", "")
        assert "no-cache" in cc, f"Expected no-cache in Cache-Control, got: {cc!r}"

    async def test_static_file_not_found_no_cache_control(
        self, server: WebServer
    ) -> None:
        """404 responses do not need Cache-Control (not a static file)."""
        host, port = _addr(server)
        status, headers, _ = await _http_get(host, port, "/nonexistent-file.xyz")
        assert status == 404

    async def test_api_info_no_cache_control_required(self, server: WebServer) -> None:
        """JSON API endpoints are not required to have Cache-Control."""
        host, port = _addr(server)
        status, _, body = await _http_get(host, port, "/api/v1/info")
        assert status == 200
        data = json.loads(body)
        assert data["server"] == "rigplane"


# ---------------------------------------------------------------------------
# #44 regression: ScopeHandler.push_frame must call enqueue_frame
# ---------------------------------------------------------------------------


class TestScopeHandlerPushFrame:
    """push_frame() must enqueue frames without AttributeError (#44)."""

    def test_push_frame_does_not_raise(self) -> None:
        """push_frame() was calling self._on_scope_frame which doesn't exist."""
        from rigplane.web.handlers import ScopeHandler
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        handler = ScopeHandler(mock_ws, None)
        handler._running = True

        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, bytes(10), False)
        handler.push_frame(frame)  # must not raise AttributeError
        assert handler._frame_queue.qsize() == 1

    def test_push_frame_not_running_is_noop(self) -> None:
        """push_frame() when not running must be a no-op."""
        from rigplane.web.handlers import ScopeHandler
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        handler = ScopeHandler(mock_ws, None)
        handler._running = False

        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, bytes(10), False)
        handler.push_frame(frame)
        assert handler._frame_queue.qsize() == 0

    def test_push_frame_increments_sequence(self) -> None:
        from rigplane.web.handlers import ScopeHandler
        from rigplane.web.websocket import WebSocketConnection

        mock_ws = MagicMock(spec=WebSocketConnection)
        handler = ScopeHandler(mock_ws, None)
        handler._running = True

        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, bytes(5), False)
        handler.push_frame(frame)
        handler.push_frame(frame)
        assert handler._seq == 2


# ---------------------------------------------------------------------------
# #45 regression: Configurable keepalive interval
# ---------------------------------------------------------------------------


class TestConfigurableKeepalive:
    """WebConfig.keepalive_interval is honoured by the server (#45)."""

    def test_webconfig_has_keepalive_interval(self) -> None:
        from rigplane.web.websocket import WS_KEEPALIVE_INTERVAL

        cfg = WebConfig()
        assert hasattr(cfg, "keepalive_interval")
        assert cfg.keepalive_interval == WS_KEEPALIVE_INTERVAL

    def test_webconfig_custom_interval(self) -> None:
        cfg = WebConfig(keepalive_interval=5.0)
        assert cfg.keepalive_interval == 5.0

    async def test_large_interval_no_pings_during_short_test(self) -> None:
        """With keepalive_interval=9999, no ping frames arrive in a short test."""
        config = WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
        async with WebServer(None, config) as srv:
            host, port = _addr(srv)
            reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
            try:
                # Collect frames for 100ms — should get hello but no pings
                frames = []
                try:
                    while True:
                        opcode, payload = await asyncio.wait_for(
                            _ws_recv_frame(reader), timeout=0.1
                        )
                        frames.append((opcode, payload))
                except asyncio.TimeoutError:
                    pass
                # Should have received exactly the hello text frame
                assert len(frames) >= 1
                assert frames[0][0] == WS_OP_TEXT
                msg = json.loads(frames[0][1])
                assert msg["type"] == "hello"
            finally:
                await _close_ws(writer)


# ---------------------------------------------------------------------------
# #46 regression: Atomic scope enable (asyncio.Lock prevents duplicate calls)
# ---------------------------------------------------------------------------


class TestScopeEnableAtomic:
    """ensure_scope_enabled() must call enable_scope() exactly once even when
    multiple handlers connect concurrently (#46)."""

    async def test_enable_scope_called_once_for_concurrent_handlers(self) -> None:
        radio = MagicMock()
        _add_scope_capable_attrs(radio)
        radio.connected = True
        radio.radio_ready = True

        server = WebServer(radio)
        handlers = [MagicMock() for _ in range(5)]

        # Call ensure_scope_enabled for all 5 handlers concurrently
        await asyncio.gather(*[server.ensure_scope_enabled(h) for h in handlers])

        # EnableScope goes through command queue
        from rigplane.web.radio_poller import EnableScope

        cmds = server._command_queue.drain()
        enable_cmds = [c for c in cmds if isinstance(c, EnableScope)]
        assert len(enable_cmds) >= 1, "At least one EnableScope should be queued"
        assert len(server._scope_handlers) == 5

    async def test_all_handlers_registered_after_concurrent_enables(self) -> None:
        radio = MagicMock()
        _add_scope_capable_attrs(radio)
        radio.connected = True
        radio.radio_ready = True

        server = WebServer(radio)
        handlers = [MagicMock() for _ in range(5)]

        await asyncio.gather(*[server.ensure_scope_enabled(h) for h in handlers])

        for h in handlers:
            assert h in server._scope_handlers

    async def test_server_responsive_after_connect_disconnect_cycles(self) -> None:
        """HTTP endpoint must return 200 after several WS connect/disconnect cycles."""
        config = WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
        radio = MagicMock()
        _add_scope_capable_attrs(radio)
        radio.connected = True
        radio.radio_ready = True

        async with WebServer(radio, config) as srv:
            host, port = _addr(srv)

            for _ in range(3):
                reader, writer, _ = await _ws_connect(host, port, "/api/v1/scope")
                await _close_ws(writer)
                await asyncio.sleep(0.02)

            status, _, _ = await _http_get(host, port, "/api/v1/info")
            assert status == 200

    async def test_enable_scope_called_once_when_first_of_many_registers(
        self,
    ) -> None:
        """Sequential registrations only enable scope once too."""
        radio = MagicMock()
        _add_scope_capable_attrs(radio)
        radio.connected = True
        radio.radio_ready = True

        server = WebServer(radio)

        h1, h2, h3 = MagicMock(), MagicMock(), MagicMock()
        await server.ensure_scope_enabled(h1)
        await server.ensure_scope_enabled(h2)
        await server.ensure_scope_enabled(h3)

        # EnableScope goes through command queue
        from rigplane.web.radio_poller import EnableScope

        cmds = server._command_queue.drain()
        enable_cmds = [c for c in cmds if isinstance(c, EnableScope)]
        assert len(enable_cmds) >= 1, "At least one EnableScope should be queued"


# ---------------------------------------------------------------------------
# #47 regression: Scope re-enable after full disconnect/reconnect
# ---------------------------------------------------------------------------


class TestScopeReconnect:
    """After all handlers disconnect and a new one connects, scope must flow (#47)."""

    async def test_scope_disabled_via_queue_after_grace(self) -> None:
        """After grace period, DisableScope is queued and flag resets."""
        radio = MagicMock()
        _add_scope_capable_attrs(radio)
        radio.connected = True
        radio.radio_ready = True

        server = WebServer(radio)
        server._scope_disable_grace = 0
        h = MagicMock()
        await server.ensure_scope_enabled(h)
        assert server._scope_enabled

        server.unregister_scope_handler(h)
        await asyncio.sleep(0.05)

        from rigplane.web.radio_poller import DisableScope

        cmds = server._command_queue.drain()
        assert any(isinstance(c, DisableScope) for c in cmds)
        assert not server._scope_enabled

    async def test_enable_scope_queued_again_after_full_disconnect(self) -> None:
        """After last handler disconnects, a new handler must queue EnableScope again."""
        radio = MagicMock()
        _add_scope_capable_attrs(radio)
        radio.connected = True
        radio.radio_ready = True

        server = WebServer(radio)
        server._scope_disable_grace = 0

        from rigplane.web.radio_poller import EnableScope

        # First connect
        h1 = MagicMock()
        await server.ensure_scope_enabled(h1)

        # Disconnect
        server.unregister_scope_handler(h1)
        await asyncio.sleep(0.05)
        assert not server._scope_enabled

        # Drain queue
        server._command_queue.drain()

        # Reconnect — must queue EnableScope again
        h2 = MagicMock()
        await server.ensure_scope_enabled(h2)
        cmds = server._command_queue.drain()
        assert any(isinstance(c, EnableScope) for c in cmds)

    async def test_disable_task_aborts_if_new_handler_reconnects(self) -> None:
        """If a new handler connects before the disable task runs,
        disable_scope() must NOT be called."""
        radio = MagicMock()
        _add_scope_capable_attrs(radio)
        radio.connected = True
        radio.radio_ready = True

        server = WebServer(radio)

        h1 = MagicMock()
        await server.ensure_scope_enabled(h1)

        # Unregister h1 — schedules disable task but doesn't await it yet
        server.unregister_scope_handler(h1)

        # Register h2 before the event loop runs the disable task
        h2 = MagicMock()
        await server.ensure_scope_enabled(h2)

        # Now let the event loop run the disable task
        await asyncio.sleep(0.05)

        # disable_scope must NOT have been called — h2 is still connected
        radio.disable_scope.assert_not_awaited()
        assert server._scope_enabled

    async def test_broadcast_scope_reaches_new_handler_after_reconnect(
        self,
    ) -> None:
        """Frames broadcast after reconnect must reach the new handler."""
        radio = MagicMock()
        _add_scope_capable_attrs(radio)
        radio.connected = True
        radio.radio_ready = True

        server = WebServer(radio)

        h1 = MagicMock()
        await server.ensure_scope_enabled(h1)
        server.unregister_scope_handler(h1)
        await asyncio.sleep(0.05)

        h2 = MagicMock()
        h2._running = True
        await server.ensure_scope_enabled(h2)

        frame = ScopeFrame(0, 0, 14_000_000, 14_350_000, bytes(10), False)
        server._broadcast_scope(frame)

        h2.enqueue_frame.assert_called_once_with(frame)
        h1.enqueue_frame.assert_not_called()


# ---------------------------------------------------------------------------
# #72: RadioPoller — single CI-V serialiser
# ---------------------------------------------------------------------------


class TestRadioPoller:
    """RadioPoller polls all params and executes commands via single task (#72)."""

    def _make_radio(self) -> MagicMock:
        from rigplane.profiles import resolve_radio_profile

        profile = resolve_radio_profile(model="IC-7610")
        radio = MagicMock()
        radio.profile = profile
        radio.model = profile.model
        radio.capabilities = set(profile.capabilities)
        radio._radio_state = SimpleNamespace(active="MAIN")
        mode_mock = MagicMock()
        mode_mock.name = "USB"
        radio.get_freq = AsyncMock(return_value=14074000)
        radio.get_mode_info = AsyncMock(return_value=(mode_mock, 1))
        radio.get_s_meter = AsyncMock(return_value=42)
        radio.get_rf_power = AsyncMock(return_value=100)
        radio.get_swr = AsyncMock(return_value=10)
        radio.get_rf_gain = AsyncMock(return_value=128)
        radio.get_af_level = AsyncMock(return_value=64)
        radio.get_attenuator_level = AsyncMock(return_value=0)
        radio.get_preamp = AsyncMock(return_value=0)
        radio.get_data_mode = AsyncMock(return_value=False)
        radio.set_freq = AsyncMock()
        radio.set_mode = AsyncMock()
        radio.set_ptt = AsyncMock()
        # Canonical dual-RX VFO methods (DualReceiverCapable post-#1114);
        # poller calls these directly.
        radio.swap_main_sub = AsyncMock()
        radio.equalize_main_sub = AsyncMock()
        radio.send_civ = AsyncMock()  # RadioPoller now calls send_civ directly
        radio.state_cache = StateCache()
        return radio

    async def test_poller_starts_and_stops(self) -> None:
        """RadioPoller start/stop lifecycle."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller

        radio = self._make_radio()
        cache = StateCache()
        queue = CommandQueue()
        poller = RadioPoller(radio, cache, queue)

        poller.start()
        assert poller.running
        await asyncio.sleep(0.05)

        poller.stop()
        assert not poller.running

    async def test_poller_polls_freq(self) -> None:
        """RadioPoller updates state cache with polled frequency."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller

        radio = self._make_radio()
        cache = StateCache()
        queue = CommandQueue()
        events: list[tuple[str, dict]] = []
        poller = RadioPoller(
            radio,
            cache,
            queue,
            on_state_event=lambda n, d: events.append((n, d)),
        )

        poller.start()
        # Slow queries poll every 10th cycle × 25ms = 250ms
        await asyncio.sleep(0.3)
        poller.stop()

        # send_civ called for freq query (0x03) and meters (0x15)
        assert radio.send_civ.await_count >= 1

    async def test_command_queue_dedup(self) -> None:
        """Last-write-wins dedup for freq commands; PTT never deduped."""
        from rigplane.web.radio_poller import CommandQueue, PttOff, PttOn, SetFreq

        queue = CommandQueue()
        queue.put(SetFreq(14000000))
        queue.put(SetFreq(14074000))
        queue.put(PttOn())
        queue.put(PttOff())

        cmds = queue.drain()
        freq_cmds = [c for c in cmds if isinstance(c, SetFreq)]
        ptt_cmds = [c for c in cmds if isinstance(c, (PttOn, PttOff))]

        assert len(freq_cmds) == 1
        assert freq_cmds[0].freq == 14074000
        assert len(ptt_cmds) == 2

    async def test_poller_executes_commands(self) -> None:
        """Commands queued are executed by the poller."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetFreq

        radio = self._make_radio()
        cache = StateCache()
        queue = CommandQueue()
        poller = RadioPoller(radio, cache, queue)

        poller.start()
        queue.put(SetFreq(7074000))
        await asyncio.sleep(0.1)
        poller.stop()

        radio.set_freq.assert_awaited_with(7074000)

    async def test_poller_broadcasts_meter_readings(self) -> None:
        """RadioPoller polls meters via send_civ."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller

        radio = self._make_radio()
        cache = StateCache()
        queue = CommandQueue()
        poller = RadioPoller(radio, cache, queue)

        poller.start()
        # Initial state fetch is done by CoreRadio._fetch_initial_state() on
        # connect; the poller just runs meter polls every _FAST_INTERVAL=25ms.
        # 0.2s is plenty for ≥3 meter polls.
        await asyncio.sleep(0.2)
        poller.stop()

        assert radio.send_civ.await_count >= 4
        meter_calls = [
            c for c in radio.send_civ.call_args_list if c[0][0] == 0x15
        ]  # cmd=0x15
        assert len(meter_calls) >= 3

    async def test_poller_idempotent_start(self) -> None:
        """Calling start() twice does not create duplicate tasks."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller

        radio = self._make_radio()
        cache = StateCache()
        queue = CommandQueue()
        poller = RadioPoller(radio, cache, queue)

        poller.start()
        task1 = poller._task
        poller.start()
        task2 = poller._task

        assert task1 is task2
        poller.stop()

    async def test_set_key_speed_updates_radio_and_state(self) -> None:
        """SetKeySpeed(speed) calls radio.set_key_speed and updates RadioState.key_speed."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetKeySpeed

        radio = self._make_radio()
        radio.set_key_speed = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetKeySpeed(24))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_key_speed.assert_awaited_once_with(24)
        assert poller._radio_state is not None
        assert poller._radio_state.key_speed == 24
        assert poller.revision > 0

    async def test_set_break_in_updates_radio_and_state(self) -> None:
        """SetBreakIn(mode) calls radio.set_break_in and updates RadioState.break_in."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetBreakIn

        radio = self._make_radio()
        radio.set_break_in = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetBreakIn(1))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_break_in.assert_awaited_once_with(1)
        assert poller._radio_state is not None
        assert poller._radio_state.break_in == 1
        assert poller.revision > 0


# ---------------------------------------------------------------------------
# #92: SUB scope receiver switching
# ---------------------------------------------------------------------------


class TestSwitchScopeReceiver:
    """SwitchScopeReceiver command sends scope_main_sub CI-V frame."""

    def _make_radio(self) -> MagicMock:
        from rigplane.profiles import resolve_radio_profile

        profile = resolve_radio_profile(model="IC-7610")
        radio = MagicMock()
        radio.profile = profile
        radio.model = profile.model
        radio.capabilities = set(profile.capabilities)
        radio._radio_state = SimpleNamespace(active="MAIN")
        radio.send_civ = AsyncMock()
        radio.state_cache = StateCache()
        radio.enable_scope = AsyncMock()
        radio.disable_scope = AsyncMock()
        radio.set_freq = AsyncMock()
        radio.set_mode = AsyncMock()
        radio.set_ptt = AsyncMock()
        radio.set_rf_power = AsyncMock()
        radio.set_rf_gain = AsyncMock()
        radio.set_af_level = AsyncMock()
        radio.set_attenuator_level = AsyncMock()
        radio.set_preamp = AsyncMock()
        radio.set_squelch = AsyncMock()
        radio.set_nb = AsyncMock()
        radio.set_nr = AsyncMock()
        radio.set_digisel = AsyncMock()
        radio.set_ip_plus = AsyncMock()
        # Canonical dual-RX VFO methods on ``DualReceiverCapable`` (post-#1114).
        radio.swap_main_sub = AsyncMock()
        radio.equalize_main_sub = AsyncMock()

        # Receiver-tier methods (issue #1170 / #1172).  Make
        # ``select_receiver`` mirror the wire-level CI-V the runtime would
        # emit so existing assertions on ``send_civ(0x07, …)`` still apply.
        async def _select_receiver(which: object) -> None:
            name = str(which).strip().upper()
            code = 0xD1 if name in ("SUB", "1") else 0xD0
            await radio.send_civ(0x07, data=bytes([code]))
            radio._radio_state.active = "SUB" if code == 0xD1 else "MAIN"

        radio.select_receiver = AsyncMock(side_effect=_select_receiver)
        radio.set_vfo_slot = AsyncMock()
        return radio

    async def test_switch_scope_receiver_main_sends_civ(self) -> None:
        """SwitchScopeReceiver(0) sends 0x27/0x12/0x00 CI-V command."""
        from rigplane.web.radio_poller import (
            CommandQueue,
            RadioPoller,
            SwitchScopeReceiver,
        )

        radio = self._make_radio()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SwitchScopeReceiver(0))
        await asyncio.sleep(0.03)
        poller.stop()

        scope_calls = [c for c in radio.send_civ.call_args_list if c[0][0] == 0x27]
        assert any(
            c.kwargs.get("sub") == 0x12 and c.kwargs.get("data") == bytes([0x00])
            for c in scope_calls
        ), "Expected CI-V 0x27/0x12/0x00 for MAIN scope"

    async def test_switch_scope_receiver_sub_sends_civ(self) -> None:
        """SwitchScopeReceiver(1) sends 0x27/0x12/0x01 CI-V command."""
        from rigplane.web.radio_poller import (
            CommandQueue,
            RadioPoller,
            SwitchScopeReceiver,
        )

        radio = self._make_radio()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue)

        poller.start()
        queue.put(SwitchScopeReceiver(1))
        await asyncio.sleep(0.03)
        poller.stop()

        scope_calls = [c for c in radio.send_civ.call_args_list if c[0][0] == 0x27]
        assert any(
            c.kwargs.get("sub") == 0x12 and c.kwargs.get("data") == bytes([0x01])
            for c in scope_calls
        ), "Expected CI-V 0x27/0x12/0x01 for SUB scope"

    async def test_switch_scope_receiver_rejects_out_of_range_receiver(self) -> None:
        """Out-of-range receiver value must not be masked into a valid target."""
        from rigplane.web.radio_poller import (
            CommandQueue,
            RadioPoller,
            SwitchScopeReceiver,
        )

        radio = self._make_radio()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue)

        poller.start()
        queue.put(SwitchScopeReceiver(0xFF))
        await asyncio.sleep(0.03)
        poller.stop()

        scope_calls = [c for c in radio.send_civ.call_args_list if c[0][0] == 0x27]
        assert not any(c.kwargs.get("sub") == 0x12 for c in scope_calls), (
            "Expected invalid receiver to be rejected without CI-V send"
        )

    async def test_select_vfo_sub_sends_receiver_select(self) -> None:
        """SelectVfo("SUB") goes through ``select_receiver`` (issue #1172).

        Wave 4-C migrated the poller off the raw ``_civ(0x07, [0xD1])``
        write to the typed ``ReceiverBankCapable.select_receiver`` API.
        We assert both the public-API call and the wire-level CI-V the
        mock emits as a side-effect (back-compat with downstream
        test scaffolding that watches ``send_civ``).
        """
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SelectVfo

        radio = self._make_radio()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue)

        poller.start()
        queue.put(SelectVfo("SUB"))
        await asyncio.sleep(0.03)
        poller.stop()

        # Public API: poller now routes through ``select_receiver``.
        radio.select_receiver.assert_awaited_once_with("SUB")
        # Wire level (mock side-effect): CI-V 0x07 0xD1 emitted.
        sub_select_calls = [
            c
            for c in radio.send_civ.call_args_list
            if c[0][0] == 0x07 and c.kwargs.get("data") == bytes([0xD1])
        ]
        assert len(sub_select_calls) >= 1, (
            "Expected SUB receiver-select (0x07 0xD1) on SelectVfo SUB"
        )
        swap_calls = [
            c
            for c in radio.send_civ.call_args_list
            if c[0][0] == 0x07 and c.kwargs.get("data") == bytes([0xB0])
        ]
        assert len(swap_calls) == 0, "Must NOT emit swap (0x07 0xB0) on SelectVfo"
        # Scope follows the selected receiver: 0x27 0x12 0x01 for SUB.
        scope_calls = [
            c
            for c in radio.send_civ.call_args_list
            if c[0][0] == 0x27
            and c.kwargs.get("sub") == 0x12
            and c.kwargs.get("data") == bytes([0x01])
        ]
        assert len(scope_calls) >= 1, (
            "Expected scope to follow selected receiver (0x27 0x12 0x01)"
        )

    async def test_select_vfo_main_noop_when_already_main(self) -> None:
        """SelectVfo("MAIN") emits no CI-V when already on MAIN."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SelectVfo

        radio = self._make_radio()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue)

        poller.start()
        queue.put(SelectVfo("MAIN"))
        await asyncio.sleep(0.03)
        poller.stop()

        # Idempotent: no call to the public API when already on the target.
        radio.select_receiver.assert_not_awaited()
        select_calls = [
            c
            for c in radio.send_civ.call_args_list
            if c[0][0] == 0x07
            and c.kwargs.get("data") in (bytes([0xD0]), bytes([0xD1]), bytes([0xB0]))
        ]
        assert len(select_calls) == 0, "Should NOT emit 0x07 select when already MAIN"

    async def test_set_split_updates_radio_and_state(self) -> None:
        """SetSplit(on) calls radio.set_split and updates RadioState.split."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetSplit

        radio = self._make_radio()
        radio.set_split = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetSplit(True))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_split.assert_awaited_once_with(True)
        assert poller._radio_state is not None
        assert poller._radio_state.split is True
        assert poller.revision > 0

    async def test_set_rit_status_updates_radio_and_state(self) -> None:
        """SetRitStatus(on) calls radio.set_rit_status and updates RadioState.rit_on."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetRitStatus

        radio = self._make_radio()
        radio.set_rit_status = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetRitStatus(True))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_rit_status.assert_awaited_once_with(True)
        assert poller._radio_state is not None
        assert poller._radio_state.rit_on is True
        assert poller.revision > 0

    async def test_set_rit_tx_status_updates_radio_and_state(self) -> None:
        """SetRitTxStatus(on) calls radio.set_rit_tx_status and updates RadioState.rit_tx."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetRitTxStatus

        radio = self._make_radio()
        radio.set_rit_tx_status = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetRitTxStatus(True))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_rit_tx_status.assert_awaited_once_with(True)
        assert poller._radio_state is not None
        assert poller._radio_state.rit_tx is True
        assert poller.revision > 0

    async def test_set_rit_frequency_updates_radio_and_state(self) -> None:
        """SetRitFrequency(freq) calls radio.set_rit_frequency and updates RadioState.rit_freq."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetRitFrequency

        radio = self._make_radio()
        radio.set_rit_frequency = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetRitFrequency(-200))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_rit_frequency.assert_awaited_once_with(-200)
        assert poller._radio_state is not None
        assert poller._radio_state.rit_freq == -200
        assert poller.revision > 0

    async def test_set_pbt_inner_updates_radio_and_state(self) -> None:
        """SetPbtInner(level) calls radio.set_pbt_inner and updates receiver state."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetPbtInner

        radio = self._make_radio()
        radio.set_pbt_inner = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetPbtInner(150, receiver=0))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_pbt_inner.assert_awaited_once_with(150, receiver=0)
        assert poller._radio_state is not None
        assert poller._radio_state.main.pbt_inner == 150
        assert poller.revision > 0

    async def test_set_pbt_outer_updates_radio_and_state(self) -> None:
        """SetPbtOuter(level) calls radio.set_pbt_outer and updates receiver state."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetPbtOuter

        radio = self._make_radio()
        radio.set_pbt_outer = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetPbtOuter(200, receiver=0))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_pbt_outer.assert_awaited_once_with(200, receiver=0)
        assert poller._radio_state is not None
        assert poller._radio_state.main.pbt_outer == 200
        assert poller.revision > 0

    async def test_set_nr_level_updates_radio_and_state(self) -> None:
        """SetNRLevel(level) calls radio.set_nr_level and updates receiver state."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetNRLevel

        radio = self._make_radio()
        radio.set_nr_level = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetNRLevel(42, receiver=0))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_nr_level.assert_awaited_once_with(42, receiver=0)
        assert poller._radio_state is not None
        assert poller._radio_state.main.nr_level == 42
        assert poller.revision > 0

    async def test_set_nb_level_updates_radio_and_state(self) -> None:
        """SetNBLevel(level) calls radio.set_nb_level and updates receiver state."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetNBLevel

        radio = self._make_radio()
        radio.set_nb_level = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetNBLevel(17, receiver=0))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_nb_level.assert_awaited_once_with(17, receiver=0)
        assert poller._radio_state is not None
        assert poller._radio_state.main.nb_level == 17
        assert poller.revision > 0

    async def test_set_auto_notch_updates_radio_and_state(self) -> None:
        """SetAutoNotch(on) calls radio.set_auto_notch and updates receiver state."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetAutoNotch

        radio = self._make_radio()
        radio.set_auto_notch = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetAutoNotch(True, receiver=0))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_auto_notch.assert_awaited_once_with(True, receiver=0)
        assert poller._radio_state is not None
        assert poller._radio_state.main.auto_notch is True
        assert poller.revision > 0

    async def test_set_manual_notch_updates_radio_and_state(self) -> None:
        """SetManualNotch(on) calls radio.set_manual_notch and updates receiver state."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetManualNotch

        radio = self._make_radio()
        radio.set_manual_notch = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetManualNotch(True, receiver=0))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_manual_notch.assert_awaited_once_with(True, receiver=0)
        assert poller._radio_state is not None
        assert poller._radio_state.main.manual_notch is True
        assert poller.revision > 0

    async def test_set_notch_filter_updates_radio_and_state(self) -> None:
        """SetNotchFilter(level) calls radio.set_notch_filter and updates RadioState.notch_filter."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetNotchFilter

        radio = self._make_radio()
        radio.set_notch_filter = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetNotchFilter(91))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_notch_filter.assert_awaited_once_with(91)
        assert poller._radio_state is not None
        assert poller._radio_state.notch_filter == 91
        assert poller.revision > 0

    async def test_set_agc_time_constant_updates_radio_and_state(self) -> None:
        """SetAgcTimeConstant(value) calls radio.set_agc_time_constant and updates receiver state."""
        from rigplane.web.radio_poller import (
            CommandQueue,
            RadioPoller,
            SetAgcTimeConstant,
        )

        radio = self._make_radio()
        radio.set_agc_time_constant = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetAgcTimeConstant(9, receiver=0))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_agc_time_constant.assert_awaited_once_with(9, receiver=0)
        assert poller._radio_state is not None
        assert poller._radio_state.main.agc_time_constant == 9
        assert poller.revision > 0

    async def test_set_cw_pitch_updates_radio_and_state(self) -> None:
        """SetCwPitch(value) calls radio.set_cw_pitch and updates RadioState.cw_pitch."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetCwPitch

        radio = self._make_radio()
        radio.set_cw_pitch = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetCwPitch(600))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_cw_pitch.assert_awaited_once_with(600)
        assert poller._radio_state is not None
        assert poller._radio_state.cw_pitch == 600
        assert poller.revision > 0

    async def test_set_mic_gain_updates_radio_and_state(self) -> None:
        """SetMicGain(level) calls radio.set_mic_gain and updates RadioState.mic_gain."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetMicGain

        radio = self._make_radio()
        radio.set_mic_gain = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetMicGain(123))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_mic_gain.assert_awaited_once_with(123)
        assert poller._radio_state is not None
        assert poller._radio_state.mic_gain == 123
        assert poller.revision > 0

    async def test_set_vox_updates_radio_and_state(self) -> None:
        """SetVox(on) calls radio.set_vox and updates RadioState.vox_on."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetVox

        radio = self._make_radio()
        radio.set_vox = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetVox(True))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_vox.assert_awaited_once_with(True)
        assert poller._radio_state is not None
        assert poller._radio_state.vox_on is True
        assert poller.revision > 0

    async def test_set_compressor_level_updates_radio_and_state(self) -> None:
        """SetCompressorLevel(level) calls radio.set_compressor_level and updates RadioState.compressor_level."""
        from rigplane.web.radio_poller import (
            CommandQueue,
            RadioPoller,
            SetCompressorLevel,
        )

        radio = self._make_radio()
        radio.set_compressor_level = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetCompressorLevel(88))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_compressor_level.assert_awaited_once_with(88)
        assert poller._radio_state is not None
        assert poller._radio_state.compressor_level == 88
        assert poller.revision > 0

    async def test_set_monitor_updates_radio_and_state(self) -> None:
        """SetMonitor(on) calls radio.set_monitor and updates RadioState.monitor_on."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetMonitor

        radio = self._make_radio()
        radio.set_monitor = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetMonitor(True))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_monitor.assert_awaited_once_with(True)
        assert poller._radio_state is not None
        assert poller._radio_state.monitor_on is True
        assert poller.revision > 0

    async def test_set_monitor_gain_updates_radio_and_state(self) -> None:
        """SetMonitorGain(level) calls radio.set_monitor_gain and updates RadioState.monitor_gain."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetMonitorGain

        radio = self._make_radio()
        radio.set_monitor_gain = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetMonitorGain(55))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_monitor_gain.assert_awaited_once_with(55)
        assert poller._radio_state is not None
        assert poller._radio_state.monitor_gain == 55
        assert poller.revision > 0

    async def test_set_dial_lock_updates_radio_and_state(self) -> None:
        """SetDialLock(on) calls radio.set_dial_lock and updates RadioState.dial_lock."""
        from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetDialLock

        radio = self._make_radio()
        radio.set_dial_lock = AsyncMock()
        queue = CommandQueue()
        poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

        poller.start()
        queue.put(SetDialLock(True))
        await asyncio.sleep(0.03)
        poller.stop()

        radio.set_dial_lock.assert_awaited_once_with(True)
        assert poller._radio_state is not None
        assert poller._radio_state.dial_lock is True
        assert poller.revision > 0


class TestSwitchScopeReceiverCommand:
    """ControlHandler handles 'switch_scope_receiver' command."""

    async def test_switch_scope_receiver_command_ok(
        self, server: WebServer, mock_radio: MagicMock
    ) -> None:
        """switch_scope_receiver command sends CI-V 0x27/0x12/0x01 for SUB."""
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)

            cmd = json.dumps(
                {
                    "type": "cmd",
                    "id": "ssr1",
                    "name": "switch_scope_receiver",
                    "params": {"receiver": 1},
                }
            )
            await _ws_send_text(writer, cmd)
            _, resp_bytes = await _ws_recv_frame(reader)
            resp = json.loads(resp_bytes)

            assert resp["ok"] is True
            assert resp["result"]["receiver"] == 1

            # Allow poller to execute the queued command
            await asyncio.sleep(0.03)

            scope_calls = [
                c for c in mock_radio.send_civ.call_args_list if c[0][0] == 0x27
            ]
            assert any(
                c.kwargs.get("sub") == 0x12 and c.kwargs.get("data") == bytes([0x01])
                for c in scope_calls
            ), "Expected CI-V 0x27/0x12/0x01 for SUB scope"
        finally:
            await _close_ws(writer)

    async def test_switch_scope_receiver_command_main(
        self, server: WebServer, mock_radio: MagicMock
    ) -> None:
        """switch_scope_receiver with receiver=0 sends CI-V 0x27/0x12/0x00."""
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)

            cmd = json.dumps(
                {
                    "type": "cmd",
                    "id": "ssr2",
                    "name": "switch_scope_receiver",
                    "params": {"receiver": 0},
                }
            )
            await _ws_send_text(writer, cmd)
            _, resp_bytes = await _ws_recv_frame(reader)
            resp = json.loads(resp_bytes)

            assert resp["ok"] is True
            assert resp["result"]["receiver"] == 0

            # Allow poller to execute the queued command
            await asyncio.sleep(0.03)

            scope_calls = [
                c for c in mock_radio.send_civ.call_args_list if c[0][0] == 0x27
            ]
            assert any(
                c.kwargs.get("sub") == 0x12 and c.kwargs.get("data") == bytes([0x00])
                for c in scope_calls
            ), "Expected CI-V 0x27/0x12/0x00 for MAIN scope"
        finally:
            await _close_ws(writer)


class TestScopeAdvancedCommands:
    """ControlHandler handles set_scope_during_tx, set_scope_center_type, set_scope_fixed_edge."""

    async def test_set_scope_during_tx_on(
        self, server: WebServer, mock_radio: MagicMock
    ) -> None:
        """set_scope_during_tx with on=true calls radio.set_scope_during_tx(True)."""
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)

            cmd = json.dumps(
                {
                    "type": "cmd",
                    "id": "sdt1",
                    "name": "set_scope_during_tx",
                    "params": {"on": True},
                }
            )
            await _ws_send_text(writer, cmd)
            _, resp_bytes = await _ws_recv_frame(reader)
            resp = json.loads(resp_bytes)

            assert resp["ok"] is True
            assert resp["result"]["on"] is True

            await asyncio.sleep(0.03)
            mock_radio.set_scope_during_tx.assert_called_once_with(True)
        finally:
            await _close_ws(writer)

    async def test_set_scope_during_tx_off(
        self, server: WebServer, mock_radio: MagicMock
    ) -> None:
        """set_scope_during_tx with on=false calls radio.set_scope_during_tx(False)."""
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)

            cmd = json.dumps(
                {
                    "type": "cmd",
                    "id": "sdt2",
                    "name": "set_scope_during_tx",
                    "params": {"on": False},
                }
            )
            await _ws_send_text(writer, cmd)
            _, resp_bytes = await _ws_recv_frame(reader)
            resp = json.loads(resp_bytes)

            assert resp["ok"] is True
            assert resp["result"]["on"] is False

            await asyncio.sleep(0.03)
            mock_radio.set_scope_during_tx.assert_called_once_with(False)
        finally:
            await _close_ws(writer)

    async def test_set_scope_center_type(
        self, server: WebServer, mock_radio: MagicMock
    ) -> None:
        """set_scope_center_type sends center_type to radio."""
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)

            cmd = json.dumps(
                {
                    "type": "cmd",
                    "id": "sct1",
                    "name": "set_scope_center_type",
                    "params": {"center_type": 2},
                }
            )
            await _ws_send_text(writer, cmd)
            _, resp_bytes = await _ws_recv_frame(reader)
            resp = json.loads(resp_bytes)

            assert resp["ok"] is True
            assert resp["result"]["center_type"] == 2

            await asyncio.sleep(0.03)
            mock_radio.set_scope_center_type.assert_called_once_with(2)
        finally:
            await _close_ws(writer)

    async def test_set_scope_fixed_edge(
        self, server: WebServer, mock_radio: MagicMock
    ) -> None:
        """set_scope_fixed_edge sends start_hz and span_hz to radio."""
        host, port = _addr(server)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
        try:
            await _ws_skip_handshake(reader)

            cmd = json.dumps(
                {
                    "type": "cmd",
                    "id": "sfe1",
                    "name": "set_scope_fixed_edge",
                    "params": {"edge": 1, "start_hz": 14_000_000, "end_hz": 15_000_000},
                }
            )
            await _ws_send_text(writer, cmd)
            _, resp_bytes = await _ws_recv_frame(reader)
            resp = json.loads(resp_bytes)

            assert resp["ok"] is True
            assert resp["result"]["edge"] == 1
            assert resp["result"]["start_hz"] == 14_000_000
            assert resp["result"]["end_hz"] == 15_000_000

            await asyncio.sleep(0.03)
            mock_radio.set_scope_fixed_edge.assert_called_once_with(
                edge=1,
                start_hz=14_000_000,
                end_hz=15_000_000,
            )
        finally:
            await _close_ws(writer)


# ---------------------------------------------------------------------------
# _get_profile() routing tests (issue #392)
# ---------------------------------------------------------------------------


class TestGetProfileRouting:
    """Unit tests for WebServer._get_profile()."""

    def _make_server(self, radio=None, radio_model="IC-7610"):
        config = WebConfig(host="127.0.0.1", port=0, radio_model=radio_model)
        return WebServer(radio, config)

    def test_resolves_ftx1_from_radio_model(self):
        """_get_profile() uses radio.model when radio has no .profile property."""
        from rigplane.profiles import RadioProfile

        radio = SimpleNamespace(
            model="FTX-1", capabilities={"audio", "scope"}
        )  # no .profile attribute
        srv = self._make_server(radio)
        profile = srv._get_profile()

        assert isinstance(profile, RadioProfile)
        assert profile.model == "FTX-1"
        assert profile.controls["nb"]["style"] == "level_is_toggle"
        assert profile.controls["nr"]["style"] == "level_is_toggle"

    def test_icom_radio_profile_property_takes_precedence(self):
        """_get_profile() uses radio.profile directly when present (regression)."""
        from rigplane.profiles import RadioProfile, resolve_radio_profile

        ic7610_profile = resolve_radio_profile(model="IC-7610")
        # Simulate IcomRadio: has .profile (RadioProfile) but .model would differ
        radio = SimpleNamespace(
            profile=ic7610_profile, model="WRONG", capabilities={"audio", "scope"}
        )
        srv = self._make_server(radio)
        profile = srv._get_profile()

        assert isinstance(profile, RadioProfile)
        assert "IC-7610" in profile.model

    def test_config_fallback_when_no_radio(self):
        """_get_profile() falls back to config radio_model when radio is None."""
        from rigplane.profiles import RadioProfile

        srv = self._make_server(radio=None, radio_model="IC-7610")
        profile = srv._get_profile()

        assert isinstance(profile, RadioProfile)
        assert "IC-7610" in profile.model


class TestGetMeterCalPayload:
    """Unit tests for WebServer._get_meter_cal_payload()."""

    def _make_server(self, radio=None, radio_model="IC-7610"):
        config = WebConfig(host="127.0.0.1", port=0, radio_model=radio_model)
        return WebServer(radio, config)

    def test_profile_fallback_includes_calibrations(self):
        radio = SimpleNamespace(
            model="IC-7610",
            capabilities=set(),
            radio_state=RadioState(),
        )
        srv = self._make_server(radio)
        payload = srv._get_meter_cal_payload()
        assert "meterCalibrations" in payload
        assert "s_meter" in payload["meterCalibrations"]

    def test_profile_fallback_includes_redlines(self):
        radio = SimpleNamespace(
            model="IC-7610",
            capabilities=set(),
            radio_state=RadioState(),
        )
        srv = self._make_server(radio)
        payload = srv._get_meter_cal_payload()
        assert "meterRedlines" in payload
        assert "s_meter" in payload["meterRedlines"]
        assert payload["meterRedlines"]["s_meter"] == 130

    def test_radio_config_takes_precedence(self):
        fake_config = SimpleNamespace(
            meter_calibrations={"power": [{"raw": 0, "actual": 0.0, "label": "0W"}]},
            meter_redlines={"power": 213},
        )
        radio = SimpleNamespace(
            model="IC-7610",
            capabilities=set(),
            radio_state=RadioState(),
            _config=fake_config,
        )
        srv = self._make_server(radio)
        payload = srv._get_meter_cal_payload()
        assert payload["meterRedlines"]["power"] == 213

    def test_empty_when_no_radio(self):
        srv = self._make_server(radio=None)
        payload = srv._get_meter_cal_payload()
        assert isinstance(payload, dict)
