"""Smoke coverage for web/rigctld consumers with serial mock backend."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct

import pytest

from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio_bridge import AudioBridge
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.icom7610.drivers.serial_stub import SerialMockRadio
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.web.handlers import AudioBroadcaster
from rigplane.web.protocol import AUDIO_CODEC_PCM16, AUDIO_HEADER_SIZE
from rigplane.web.server import WebConfig, WebServer


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


async def _yield_until(predicate, timeout: float = 1.0) -> None:  # type: ignore[no-untyped-def]
    """Yield to the event loop until ``predicate()`` is truthy (or timeout).

    A fixed number of ``asyncio.sleep(0)`` yields is interpreter-sensitive:
    pre-3.12 ``asyncio.wait_for`` wraps its awaitable in an extra Task
    (gh-96764), so frame propagation through AudioBus → bridge loops needs
    more event-loop iterations on 3.11 than on 3.12+.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0)


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
async def test_audio_bridge_smoke_with_serial_backend_audio_driver() -> None:
    serial_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/tty.usbmodem-IC7610",
        civ_link=_FakeSerialCivLink(),
        audio_driver=serial_audio,
    )
    await radio.connect()

    # Use FakeAudioBackend to eliminate asyncio.to_thread races that made
    # this test flaky in CI (#801). FakeRxStream/FakeTxStream are fully
    # in-loop — no thread pool scheduling, no call_soon_threadsafe delays.
    device = AudioDeviceInfo(
        id=AudioDeviceId(1),
        name="BlackHole 2ch",
        input_channels=2,
        output_channels=2,
    )
    backend = FakeAudioBackend(devices=[device])
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=True, backend=backend
    )
    try:
        await bridge.start()
        # Yield once so _rx_loop and _tx_loop tasks actually start running.
        await asyncio.sleep(0)

        # RX path: serial driver → AudioBus → _rx_loop → FakeTxStream.
        # _yield_until: the number of event-loop hops differs across Python
        # versions (see helper docstring), so poll instead of a fixed yield.
        serial_audio.emit_rx_pcm(b"\x10\x20" * 960)
        await _yield_until(lambda: backend.tx_streams[0].written_frames)
        assert backend.tx_streams[0].written_frames, (
            "RX frame did not reach bridge output"
        )

        # TX path: FakeRxStream.inject_frame → _on_tx_capture → _tx_loop → radio
        # PCM value 100 (> silence threshold of 10) so it is not filtered.
        pcm_tx = bytes([0, 100] * 960)  # 960 int16 samples, each = 0x6400 = 25600
        backend.rx_streams[0].inject_frame(pcm_tx)
        # inject_frame calls _on_tx_capture via call_soon_threadsafe, then
        # _tx_loop must process the queued frame — again hop-count-sensitive.
        await _yield_until(lambda: serial_audio.tx_frames)
        assert serial_audio.tx_frames, "TX frame did not reach radio"

        await bridge.stop()
    finally:
        await radio.disconnect()


# Public ``fieldStatus`` keys the v2 desktop skin gates on for control
# rendering. With MOR-429 availability gating, a key that is not
# ``available`` strips its control from the DOM, hanging the live audit.
# Every key here must be observation-backed by the mock (MOR-437).
_V2_RECEIVER_FIELDS = (
    "freqHz",
    "mode",
    "dataMode",
    "rfGain",
    "squelch",
    "att",
    "preamp",
    "agc",
    "agcTimeConstant",
    "nrLevel",
    "nbLevel",
    "autoNotch",
    "manualNotch",
    "filterWidth",
    "afLevel",
    "nr",
    "nb",
)
_V2_GLOBAL_FIELDS = (
    "micGain",
    "compressorLevel",
    "monitorGain",
    "cwPitch",
    "tunerStatus",
    "split",
    "compressorOn",
    "monitorOn",
    "voxOn",
    "dualWatch",
    "txFreqMonitor",
)


def _required_v2_field_keys(receiver_count: int) -> list[str]:
    keys: list[str] = []
    receivers = ["main"] + (["sub"] if receiver_count >= 2 else [])
    for receiver in receivers:
        keys.extend(f"{receiver}.{field}" for field in _V2_RECEIVER_FIELDS)
    keys.extend(_V2_GLOBAL_FIELDS)
    return keys


@pytest.mark.asyncio
async def test_serial_mock_observation_backs_all_v2_fields() -> None:
    """Gate-8 regression: ``WebServer(SerialMockRadio())`` must observation-back
    every v2-rendered field so the desktop-v2 availability gate (MOR-429)
    renders the controls the live Playwright audit exercises (MOR-437).
    """
    radio = SerialMockRadio()  # dual-RX IC-7610 profile by default
    await radio.connect()
    server = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0),
    )
    await server.start()
    try:
        # Let the observation poller seed the StateStore baseline.
        await asyncio.sleep(0.1)
        state = server.build_public_state()
        field_status = state["fieldStatus"]
        receiver_count = server._get_profile().receiver_count
        assert receiver_count >= 2, "IC-7610 mock is dual-RX"

        for key in _required_v2_field_keys(receiver_count):
            status = field_status.get(key)
            assert status is not None, f"{key} missing from fieldStatus"
            assert status["observed"] is True, f"{key} not observed"
            assert status["availability"] == "available", (
                f"{key} availability={status.get('availability')!r}, expected available"
            )
            # max_age=None means the field never decays to stale/missing.
            assert status.get("maxAge") is None, f"{key} must not expire (maxAge=None)"

        # Sensible IC-7610 defaults reach the public state (not RadioState
        # defaults masquerading as missing).
        assert state["main"]["rfGain"] == 200 / 255
        assert state["main"]["agc"] == 2  # MID
        assert state["main"]["preamp"] == 1
        assert state["main"]["filterWidth"] == 2400
        assert state["micGain"] == 128
        assert state["cwPitch"] == 600
        assert state["monitorGain"] == 128

        # Command execution still works alongside the observation poller: a
        # queued set_* drains, executes on the mock, and the observed value
        # updates without snapping back to the baseline.
        from rigplane.runtime._poller_types import SetRfGain

        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        server.command_queue.put_ordered(SetRfGain(level=42, receiver=0), future=future)
        await asyncio.wait_for(future, timeout=2.0)
        await asyncio.sleep(0.1)
        updated = server.build_public_state()
        assert updated["main"]["rfGain"] == 42 / 255
        assert updated["fieldStatus"]["main.rfGain"]["availability"] == "available"
    finally:
        await server.stop()
