"""Smoke coverage for web/rigctld consumers with serial mock backend."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
from unittest.mock import patch

import pytest

from icom_lan.audio_bridge import AudioBridge
from icom_lan.backends.icom7610 import Icom7610SerialRadio
from icom_lan.backends.icom7610.drivers.serial_stub import SerialMockRadio
from icom_lan.rigctld.contract import RigctldConfig
from icom_lan.rigctld.server import RigctldServer
from icom_lan.web.handlers import AudioBroadcaster
from icom_lan.web.protocol import AUDIO_CODEC_PCM16, AUDIO_HEADER_SIZE
from icom_lan.web.server import WebConfig, WebServer


_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class _FakeSerialCivLink:
    def __init__(self) -> None:
        self.connected = False
        self.ready = False
        self.healthy = False

    async def connect(self) -> None:
        self.connected = True
        self.ready = True
        self.healthy = True

    async def disconnect(self) -> None:
        self.connected = False
        self.ready = False
        self.healthy = False

    async def send(self, frame: bytes) -> None:
        _ = frame
        return None

    async def receive(self, timeout: float | None = None) -> bytes | None:
        await asyncio.sleep(0.02 if timeout is None else min(timeout, 0.02))
        return None


class _FakeUsbAudioDriver:
    def __init__(self) -> None:
        self.rx_running = False
        self.tx_running = False
        self.rx_callback = None
        self.tx_frames: list[bytes] = []

    async def start_rx(self, callback, **kwargs) -> None:  # type: ignore[no-untyped-def]
        _ = kwargs
        self.rx_callback = callback
        self.rx_running = True

    async def stop_rx(self) -> None:
        self.rx_running = False
        self.rx_callback = None

    async def start_tx(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        _ = kwargs
        self.tx_running = True

    async def stop_tx(self) -> None:
        self.tx_running = False

    async def _push_tx_pcm(self, frame: bytes) -> None:
        self.tx_frames.append(bytes(frame))

    def emit_rx_pcm(self, frame: bytes) -> None:
        if self.rx_callback is not None:
            self.rx_callback(frame)


class _BridgeOutputStream:
    def __init__(self) -> None:
        self.active = False
        self.writes: list[bytes] = []

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        return None

    def write(self, data) -> None:  # type: ignore[no-untyped-def]
        if hasattr(data, "tobytes"):
            self.writes.append(bytes(data.tobytes()))
            return
        self.writes.append(bytes(data))


class _BridgeInputStream:
    def __init__(self) -> None:
        self.active = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        return None

    def read(self, frames: int):  # type: ignore[no-untyped-def]
        import numpy as np

        return np.full((frames, 1), 100, dtype=np.int16), False


class _BridgeSoundDevice:
    def __init__(self) -> None:
        self.output_stream = _BridgeOutputStream()
        self.input_stream = _BridgeInputStream()

    def query_devices(self):  # type: ignore[no-untyped-def]
        return [{"name": "BlackHole 2ch", "index": 1}]

    def OutputStream(self, **kwargs):  # noqa: N802 # type: ignore[no-untyped-def]
        _ = kwargs
        return self.output_stream

    def InputStream(self, **kwargs):  # noqa: N802 # type: ignore[no-untyped-def]
        _ = kwargs
        return self.input_stream


def _addr_from_asyncio_server(server: asyncio.Server) -> tuple[str, int]:
    return server.sockets[0].getsockname()


async def _http_get(
    host: str, port: int, path: str
) -> tuple[int, dict[str, str], bytes]:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        req = f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        writer.write(req.encode("ascii"))
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=2.0)
    finally:
        writer.close()
        await writer.wait_closed()
    header_end = raw.find(b"\r\n\r\n")
    header_bytes = raw[:header_end].decode("ascii", errors="replace").split("\r\n")
    status = int(header_bytes[0].split(" ", 2)[1])
    headers: dict[str, str] = {}
    for line in header_bytes[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    return status, headers, raw[header_end + 4 :]


async def _ws_connect(
    host: str, port: int, path: str
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection(host, port)
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    writer.write(req.encode("ascii"))
    await writer.drain()
    resp = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2.0)
    accept = base64.b64encode(
        hashlib.sha1((key + _WS_MAGIC).encode("ascii")).digest()
    ).decode("ascii")
    assert b"101" in resp
    assert accept.encode("ascii") in resp
    return reader, writer


async def _ws_send_text(writer: asyncio.StreamWriter, text: str) -> None:
    payload = text.encode("utf-8")
    mask = b"\x11\x22\x33\x44"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    header = bytes([0x81, 0x80 | len(payload)]) + mask
    writer.write(header + masked)
    await writer.drain()


async def _ws_recv_frame(
    reader: asyncio.StreamReader,
) -> tuple[int, bytes]:
    header = await asyncio.wait_for(reader.readexactly(2), timeout=2.0)
    op = header[0] & 0x0F
    payload_len = header[1] & 0x7F
    if payload_len == 126:
        payload_len = struct.unpack("!H", await reader.readexactly(2))[0]
    elif payload_len == 127:
        payload_len = struct.unpack("!Q", await reader.readexactly(8))[0]
    payload = await reader.readexactly(payload_len)
    return op, payload


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


@pytest.mark.asyncio
async def test_web_server_smoke_with_serial_mock_backend() -> None:
    radio = SerialMockRadio()
    await radio.connect()  # WebServer requires a ready radio before start()
    server = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0),
    )
    await server.start()
    try:
        assert server._server is not None
        host, port = _addr_from_asyncio_server(server._server)

        status, _, body = await _http_get(host, port, "/api/v1/state")
        assert status == 200
        state = json.loads(body.decode("utf-8"))
        assert state["connection"]["rigConnected"] is True

        reader, writer = await _ws_connect(host, port, "/api/v1/ws")
        try:
            _op, payload = await _ws_recv_frame(reader)
            hello = json.loads(payload.decode("utf-8"))
            assert hello["type"] == "hello"
            assert hello["connected"] is True
            assert hello["radio_ready"] is True

            # Skip initial state_update pushed after hello
            _op, _payload = await _ws_recv_frame(reader)
            _msg = json.loads(_payload.decode("utf-8"))
            if _msg.get("type") == "state_update":
                pass  # consumed initial state

            # Verify radio_connect WS command works (re-connect while already connected)
            await _ws_send_text(
                writer,
                json.dumps({"type": "radio_connect", "id": "connect-1"}),
            )
            _op, payload = await _ws_recv_frame(reader)
            connect_resp = json.loads(payload.decode("utf-8"))
            assert connect_resp["type"] == "response"
            assert connect_resp["id"] == "connect-1"
            assert connect_resp["ok"] is True

            status, _, body = await _http_get(host, port, "/api/v1/state")
            assert status == 200
            connected_state = json.loads(body.decode("utf-8"))
            assert connected_state["connection"]["rigConnected"] is True
            assert connected_state["connection"]["radioReady"] is True
        finally:
            await _close_writer(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_rigctld_smoke_with_serial_mock_backend() -> None:
    radio = SerialMockRadio()
    await radio.connect()
    server = RigctldServer(
        radio,
        RigctldConfig(
            host="127.0.0.1",
            port=0,
            client_timeout=1.0,
            command_timeout=1.0,
            poll_interval=0.5,
        ),
    )
    await server.start()
    try:
        assert server._server is not None
        host, port = _addr_from_asyncio_server(server._server)
        reader, writer = await asyncio.open_connection(host, port)
        try:
            writer.write(b"f\n")
            await writer.drain()
            freq_resp = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=2.0)
            assert freq_resp.strip() == b"14074000"

            writer.write(b"F 7074000\n")
            await writer.drain()
            set_freq_resp = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=2.0)
            assert set_freq_resp.strip() == b"RPRT 0"

            writer.write(b"f\n")
            await writer.drain()
            new_freq_resp = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=2.0)
            assert new_freq_resp.strip() == b"7074000"

            writer.write(b"m\n")
            await writer.drain()
            mode_line = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=2.0)
            passband_line = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=2.0)
            assert mode_line.strip() == b"USB"
            assert passband_line.strip() in {b"3000", b"2400", b"1800", b"0"}

            writer.write(b"M PKTUSB 2400\n")
            await writer.drain()
            set_mode_resp = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=2.0)
            assert set_mode_resp.strip() == b"RPRT 0"

            writer.write(b"m\n")
            await writer.drain()
            pkt_mode_line = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=2.0)
            pkt_passband_line = await asyncio.wait_for(
                reader.readuntil(b"\n"), timeout=2.0
            )
            assert pkt_mode_line.strip() == b"PKTUSB"
            assert pkt_passband_line.strip() == b"2400"

            writer.write(b"T 1\n")
            await writer.drain()
            set_ptt_resp = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=2.0)
            assert set_ptt_resp.strip() == b"RPRT 0"

            writer.write(b"t\n")
            await writer.drain()
            ptt_resp = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=2.0)
            assert ptt_resp.strip() == b"1"

            # Issue #1189: legacy backends (no ReceiverBankCapable) must
            # still see ``set_vfo`` actually invoked on ``V VFOA`` rather
            # than the silent ``RPRT 0`` regression introduced by #1187.
            # Spy on the radio's legacy ``set_vfo`` to verify dispatch
            # reached the radio (not the rigctld success code alone,
            # which would be returned even by the broken silent no-op).
            calls: list[str] = []
            original_set_vfo = radio.set_vfo

            async def _spy(vfo: str) -> None:
                calls.append(vfo)
                await original_set_vfo(vfo)

            radio.set_vfo = _spy  # type: ignore[method-assign]
            writer.write(b"V VFOA\n")
            await writer.drain()
            set_vfo_resp = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=2.0)
            assert set_vfo_resp.strip() == b"RPRT 0"
            assert calls == ["MAIN"], (
                f"V VFOA must reach legacy set_vfo (got calls={calls!r})"
            )
        finally:
            await _close_writer(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_web_audio_broadcaster_smoke_with_serial_backend_audio_driver() -> None:
    serial_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/tty.usbmodem-IC7610",
        civ_link=_FakeSerialCivLink(),
        audio_driver=serial_audio,
    )
    await radio.connect()
    broadcaster = AudioBroadcaster(radio)
    queue = await broadcaster.subscribe()
    pcm_frame = b"\xab\xcd" * 960
    try:
        serial_audio.emit_rx_pcm(pcm_frame)
        web_frame = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert web_frame[1] == AUDIO_CODEC_PCM16
        assert web_frame[AUDIO_HEADER_SIZE:] == pcm_frame
    finally:
        await broadcaster.unsubscribe(queue)
        await radio.disconnect()


@pytest.mark.asyncio
@pytest.mark.skip(
    reason="Flaky in CI: race condition in audio frame propagation timing (#801)"
)
async def test_audio_bridge_smoke_with_serial_backend_audio_driver() -> None:
    serial_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/tty.usbmodem-IC7610",
        civ_link=_FakeSerialCivLink(),
        audio_driver=serial_audio,
    )
    await radio.connect()
    fake_sd = _BridgeSoundDevice()
    bridge = AudioBridge(radio, device_name="BlackHole", tx_enabled=True)
    try:
        with patch.dict("sys.modules", {"sounddevice": fake_sd}):
            await bridge.start()
            serial_audio.emit_rx_pcm(b"\x10\x20" * 960)
            deadline = asyncio.get_running_loop().time() + 1.0
            while (
                not serial_audio.tx_frames
                and asyncio.get_running_loop().time() < deadline
            ):
                await asyncio.sleep(0.02)
            assert fake_sd.output_stream.writes
            assert serial_audio.tx_frames
            await bridge.stop()
    finally:
        await radio.disconnect()
