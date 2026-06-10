"""End-to-end audio-path smoke tests (MOR-583, epic MOR-562 regression guard).

One thin layer that exercises the REALIZED audio spine end-to-end on fakes —
no hardware, no mocks of the components under test:

    radio transport (real serial class + real UsbAudioDriver on a
    FakeAudioBackend) → AudioBus fan-out + RX heartbeat (MOR-564)
    → AudioBroadcaster web relay + FFT-scope tap → WS clients
    → AudioBridge loopback pump → radio-owned AudioSession singleton
    (MOR-579) with TX leases (MOR-580) + health watchdog (MOR-581)
    → /api/v1/runtime payload blocks.

Distinct from the per-component suites (``tests/contracts/
test_audio_lifecycle_conformance.py`` pins arming-order graphs;
``tests/test_audio_session*.py`` pins session unit semantics): every test
here drives the INTEGRATED path through the real WebServer / bridge / bus
wiring and asserts flow, lifecycle, and non-silence.

Codec-agnostic by design (so later egress/decode epic steps don't churn
this file): assertions check that frames FLOW and are NON-SILENT (some
non-zero payload byte), never specific codec bytes or frame sizes.

Determinism: no fixed multi-second sleeps. All waits are condition polls
(``_wait_for``, 5 ms cadence, bounded deadline); RX frames are injected
through the fakes' synchronous callbacks; the health watchdog runs with
per-instance low thresholds (MOR-581 kwargs) seeded into the radio's lazy
``_audio_session`` slot.

Backend shapes:

- ``usb-serial-full`` — real :class:`Icom7610SerialRadio` over a fake CI-V
  link with the real :class:`UsbAudioDriver` on a separate-device
  :class:`FakeAudioBackend` (``audio_setup_order == "rx_first"``).
- ``lan-graph-stub`` / ``exclusive-graph-stub`` — the shared MOR-566
  order-sensitive stubs (declared LAN single-state and same-device
  exclusive/atomic transition graphs) for the bridge round-trip.
- The REAL same-device exclusive USB row is hardware-only (see the skip
  marker at the bottom for why fakes cannot model it faithfully).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import numpy as np
import pytest
from _order_sensitive_radios import ExclusiveUsbRadio, LanLikeRadio
from test_icom7610_serial_radio import _FakeSerialCivLink

from rigplane.audio import AudioPacket
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.bridge import AudioBridge, BridgeState
from rigplane.audio.session import AudioSession, AudioSessionState
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.web.protocol import (
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_RX,
    MSG_TYPE_AUDIO_TX,
    MSG_TYPE_SCOPE,
    SCOPE_HEADER_SIZE,
    encode_audio_frame,
)
from rigplane.web.server import WebConfig, WebServer

# ── Devices ──────────────────────────────────────────────────────────────────

_RX_DEV = AudioDeviceInfo(id=AudioDeviceId(11), name="Rig CODEC In", input_channels=2)
_TX_DEV = AudioDeviceInfo(id=AudioDeviceId(12), name="Rig CODEC Out", output_channels=2)
_LOOPBACK = AudioDeviceInfo(
    id=AudioDeviceId(1), name="BlackHole 2ch", input_channels=2, output_channels=2
)

# One 20 ms s16le frame @ 48 kHz with a clearly non-silent constant pattern.
_NONSILENT_PCM = b"\x10\x20" * 960
# Peak 0x6400 = 25600 — far above the bridge TX silence gate (peak < 10).
_LOUD_PCM = bytes([0x00, 0x64]) * 960

# Fast watchdog for deterministic RECOVERING tests (MOR-581 per-instance
# kwargs) — mirrors tests/test_audio_session_health.py.
_WD_INTERVAL = 0.02
_WD_TIMEOUT = 0.06

_WS_KEY = "dGhlIHNhbXBsZSBub25jZQ=="
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ── Polling / WS helpers ─────────────────────────────────────────────────────


async def _wait_for(predicate: Callable[[], bool], deadline_s: float = 3.0) -> bool:
    """Await *predicate* with a bounded poll — never a fixed long sleep."""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


def _addr(server: WebServer) -> tuple[str, int]:
    assert server._server is not None
    return server._server.sockets[0].getsockname()


async def _ws_connect(
    host: str, port: int, path: str
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection(host, port)
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {_WS_KEY}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    writer.write(req.encode("ascii"))
    await writer.drain()
    resp = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5.0)
    accept = base64.b64encode(
        hashlib.sha1((_WS_KEY + _WS_MAGIC).encode("ascii")).digest()
    ).decode("ascii")
    assert b"101" in resp
    assert accept.encode("ascii") in resp
    return reader, writer


def _mask_frame(opcode: int, payload: bytes) -> bytes:
    mask = b"\x11\x22\x33\x44"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    length = len(payload)
    if length <= 125:
        header = bytes([0x80 | opcode, 0x80 | length]) + mask
    elif length <= 65535:
        header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length) + mask
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length) + mask
    return header + masked


async def _ws_send_text(writer: asyncio.StreamWriter, text: str) -> None:
    writer.write(_mask_frame(0x1, text.encode("utf-8")))
    await writer.drain()


async def _ws_send_binary(writer: asyncio.StreamWriter, payload: bytes) -> None:
    writer.write(_mask_frame(0x2, payload))
    await writer.drain()


async def _ws_recv_frame(
    reader: asyncio.StreamReader, timeout: float = 5.0
) -> tuple[int, bytes]:
    """Read one server→client frame, skipping ping/pong control frames."""
    while True:
        header = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        opcode = header[0] & 0x0F
        payload_len = header[1] & 0x7F
        if payload_len == 126:
            payload_len = struct.unpack("!H", await reader.readexactly(2))[0]
        elif payload_len == 127:
            payload_len = struct.unpack("!Q", await reader.readexactly(8))[0]
        payload = await reader.readexactly(payload_len)
        if opcode in (0x9, 0xA):
            continue
        return opcode, payload


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


async def _http_get_json(host: str, port: int, path: str) -> dict[str, Any]:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        req = f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        writer.write(req.encode("ascii"))
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=2.0)
    finally:
        await _close_writer(writer)
    status = int(raw.split(b" ", 2)[1])
    assert status == 200, f"GET {path} -> {status}"
    return json.loads(raw[raw.find(b"\r\n\r\n") + 4 :])


def _noise_pcm(num_samples: int, seed: int = 583, amplitude: int = 5000) -> bytes:
    """PCM16 mono white noise (radio RX shape) for FFT-scope feeding."""
    rng = np.random.default_rng(seed)
    return (rng.uniform(-1, 1, num_samples) * amplitude).astype(np.int16).tobytes()


# ── Backend-shape harnesses ──────────────────────────────────────────────────


@dataclass
class _UsbSerialRig:
    """Real serial radio + real USB driver on a separate-device fake backend."""

    radio: Icom7610SerialRadio
    backend: FakeAudioBackend

    def inject_rx(self, pcm: bytes) -> None:
        """Push one capture frame through the radio's open RX stream."""
        assert self.backend.rx_streams, "radio RX stream not open"
        self.backend.rx_streams[-1].inject_frame(pcm)

    def radio_tx_frames(self) -> int:
        return sum(len(s.written_frames) for s in self.backend.tx_streams)

    def open_streams(self) -> int:
        return sum(
            s.running
            for s in self.backend.rx_streams
            + self.backend.tx_streams
            + self.backend.duplex_streams
        )


def _make_usb_serial_rig() -> _UsbSerialRig:
    backend = FakeAudioBackend([_RX_DEV, _TX_DEV])
    driver = UsbAudioDriver(
        rx_device="Rig CODEC In", tx_device="Rig CODEC Out", backend=backend
    )
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB-fake",
        civ_link=_FakeSerialCivLink(),
        audio_driver=driver,
        timeout=0.2,  # keep background poller CI-V timeouts short on the fake link
    )
    return _UsbSerialRig(radio=radio, backend=backend)


@pytest.fixture
async def usb_rig() -> Any:
    rig = _make_usb_serial_rig()
    await rig.radio.connect()
    yield rig
    await rig.radio.disconnect()


@pytest.fixture
async def web_server(usb_rig: _UsbSerialRig) -> Any:
    server = WebServer(
        usb_rig.radio,
        WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0),
    )
    await server.start()
    yield server
    await server.stop()  # radio disconnect is owned by the usb_rig fixture


class _RecordingLanRadio(LanLikeRadio):
    """LAN-graph stub recording radio-bound TX pushes (smoke flow probe)."""

    def __init__(self) -> None:
        super().__init__()
        self.tx_pushed: list[bytes] = []

    async def push_tx(self, audio_data: bytes) -> None:
        await super().push_tx(audio_data)
        self.tx_pushed.append(bytes(audio_data))


class _RecordingExclusiveRadio(ExclusiveUsbRadio):
    """Exclusive/atomic-graph stub recording radio-bound TX pushes."""

    def __init__(self) -> None:
        super().__init__()
        self.tx_pushed: list[bytes] = []

    async def push_tx(self, audio_data: bytes) -> None:
        await super().push_tx(audio_data)
        self.tx_pushed.append(bytes(audio_data))


@dataclass
class _BridgeShape:
    """Uniform probes for one bridge backend shape."""

    radio: Any
    inject_rx: Callable[[bytes], None]
    radio_tx_frames: Callable[[], int]
    rx_live: Callable[[], bool]
    tx_live: Callable[[], bool]
    open_streams: Callable[[], int]
    close: Callable[[], Awaitable[None]] | None = None


async def _make_bridge_shape(case: str) -> _BridgeShape:
    if case == "usb-serial-full":
        rig = _make_usb_serial_rig()
        await rig.radio.connect()
        driver = rig.radio._serial_audio_driver
        return _BridgeShape(
            radio=rig.radio,
            inject_rx=rig.inject_rx,
            radio_tx_frames=rig.radio_tx_frames,
            rx_live=lambda: driver.rx_running,
            tx_live=lambda: driver.tx_running,
            open_streams=rig.open_streams,
            close=rig.radio.disconnect,
        )
    if case == "lan-graph-stub":
        lan = _RecordingLanRadio()
        return _BridgeShape(
            radio=lan,
            inject_rx=lambda pcm: lan.rx_callback(  # type: ignore[misc]
                AudioPacket(ident=0x0080, send_seq=1, data=pcm)
            ),
            radio_tx_frames=lambda: len(lan.tx_pushed),
            rx_live=lambda: lan.rx_callback is not None,
            tx_live=lambda: lan.state == "transmitting",
            open_streams=lambda: int(lan.state != "idle"),
        )
    assert case == "exclusive-graph-stub"
    excl = _RecordingExclusiveRadio()
    return _BridgeShape(
        radio=excl,
        inject_rx=lambda pcm: excl.rx_callback(  # type: ignore[misc]
            AudioPacket(ident=0x0080, send_seq=1, data=pcm)
        ),
        radio_tx_frames=lambda: len(excl.tx_pushed),
        rx_live=lambda: excl.rx_running,
        tx_live=lambda: excl.tx_running,
        open_streams=lambda: int(excl.rx_running) + int(excl.tx_running),
    )


# ── (a) End-to-end web RX relay: radio → bus → broadcaster → WS client ───────


async def test_web_audio_ws_relays_nonsilent_rx_frames(usb_rig: _UsbSerialRig) -> None:
    """A `/api/v1/audio` WS client receives non-silent RX frames end-to-end.

    Chain under test: ``audio_start`` → AudioBroadcaster → AudioBus
    refcount → radio ``start_rx`` → UsbAudioDriver → FakeRxStream; injected
    capture frames travel back the full relay to the WS client.
    """
    server = WebServer(
        usb_rig.radio,
        WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0),
    )
    await server.start()
    try:
        host, port = _addr(server)
        reader, writer = await _ws_connect(host, port, "/api/v1/audio")
        try:
            await _ws_send_text(
                writer, json.dumps({"type": "audio_start", "direction": "rx"})
            )
            # First bus subscriber arms radio RX → the fake capture stream opens.
            assert await _wait_for(
                lambda: any(s.running for s in usb_rig.backend.rx_streams)
            ), "audio_start(rx) never armed the radio RX capture"

            for _ in range(5):
                usb_rig.inject_rx(_NONSILENT_PCM)
                await asyncio.sleep(0)

            opcode, payload = await _ws_recv_frame(reader)
            assert opcode == 0x2, "audio frames must arrive as binary WS frames"
            assert payload[0] == MSG_TYPE_AUDIO_RX
            audio = payload[AUDIO_HEADER_SIZE:]
            # Codec-agnostic non-silence: injected non-silent PCM must never
            # relay as an empty or all-zero payload, whatever the egress codec.
            assert audio, "relayed audio payload is empty"
            assert any(audio), "relayed audio payload is silent (all zero bytes)"

            # RX heartbeat stamped at the bus fan-out (MOR-564).
            assert usb_rig.radio.audio_bus.last_rx_frame_monotonic is not None
        finally:
            # NOTE: after the last WS client leaves, the broadcaster keeps the
            # relay (and the bus subscription) alive on purpose — the always-on
            # audio-analyzer tap registered for audio radios holds it. The
            # leak-free invariant therefore belongs to server stop, below.
            await _close_writer(writer)
    finally:
        await server.stop()

    # Server stopped → relay torn down → zero bus subscribers and the radio
    # RX capture stream closed: nothing leaked (MOR-560 class).
    assert await _wait_for(lambda: usb_rig.radio.audio_bus.subscriber_count == 0)
    assert await _wait_for(lambda: usb_rig.open_streams() == 0)


# ── (b) FFT scope WS receives frames from the same RX spine ─────────────────


async def test_audio_scope_ws_receives_fft_frames(
    usb_rig: _UsbSerialRig, web_server: WebServer
) -> None:
    """A `/api/v1/audio-scope` WS client receives FFT scope frames.

    Chain under test: scope WS connect → ``ensure_audio_scope_enabled`` →
    broadcaster PCM tap + relay → AudioBus → radio RX; injected white-noise
    PCM drives AudioFftScope → dispatch → ScopeHandler → binary WS frame.
    """
    host, port = _addr(web_server)
    reader, writer = await _ws_connect(host, port, "/api/v1/audio-scope")
    try:
        # ensure_relay (first scope client) arms radio RX via the bus.
        assert await _wait_for(
            lambda: any(s.running for s in usb_rig.backend.rx_streams)
        ), "audio-scope connect never armed the RX relay"

        scope = web_server._audio_fft_scope
        assert scope is not None, "audio FFT scope not wired for an audio radio"
        # feed_audio gates on center_freq > 0; no live poller seeds the
        # StateStore on the fake CI-V link, so set it directly (the
        # established MOR-241 test pattern) and bypass the 20 fps limiter.
        scope.set_center_freq(14_074_000)
        scope._last_frame_time = 0.0

        # Several FFT windows (2048 samples each) of white noise, injected
        # as 20 ms radio capture frames.
        for chunk in range(12):
            usb_rig.inject_rx(_noise_pcm(960, seed=chunk + 1))
            await asyncio.sleep(0)

        opcode, payload = await _ws_recv_frame(reader)
        assert opcode == 0x2
        assert payload[0] == MSG_TYPE_SCOPE
        assert len(payload) > SCOPE_HEADER_SIZE, "scope frame carries no pixels"
        assert any(payload[SCOPE_HEADER_SIZE:]), "FFT pixels are all zero"
    finally:
        await _close_writer(writer)


# ── (c) AudioBridge end-to-end round trip on every fake-drivable shape ───────


@pytest.mark.parametrize(
    "case", ["usb-serial-full", "lan-graph-stub", "exclusive-graph-stub"]
)
async def test_bridge_end_to_end_roundtrip_and_clean_stop(case: str) -> None:
    """Bridge pumps RX radio→loopback and TX loopback→radio, then stops clean.

    Beyond the MOR-567 conformance lifecycle rows, this asserts actual FRAME
    FLOW through the started bridge in both directions, then the
    MOR-560/574 invariants: zero leaked bus subscribers and zero open
    device streams after ``stop()``.
    """
    shape = await _make_bridge_shape(case)
    bridge_backend = FakeAudioBackend([_LOOPBACK])
    bridge = AudioBridge(
        shape.radio, device_name="BlackHole", tx_enabled=True, backend=bridge_backend
    )
    try:
        await bridge.start()
        assert bridge.bridge_state is BridgeState.RUNNING
        assert shape.radio.audio_bus.rx_active, "bridge started with dead radio RX"
        assert shape.rx_live(), "bridge start left RX dead (MOR-556/559 class)"
        assert shape.tx_live(), "bridge start left radio TX unarmed"

        session = getattr(shape.radio, "audio_session", None)
        if session is not None:
            # Radio-owned singleton (MOR-579): the bridge's demand must land
            # on the SHARED session, visible through the public property.
            assert session.state is AudioSessionState.RX_TX
            assert session.rx_demand == 1 and session.tx_demand == 1

        # RX: radio-native frame → bus → bridge RX loop → loopback playback.
        shape.inject_rx(_NONSILENT_PCM)
        assert await _wait_for(
            lambda: any(
                any(frame)
                for s in bridge_backend.tx_streams + bridge_backend.duplex_streams
                for frame in s.written_frames
            )
        ), "non-silent radio RX frame never reached the loopback output"

        # TX: loopback capture → bridge TX loop → session lease → radio.
        bridge_backend.rx_streams[0].inject_frame(_LOUD_PCM)
        assert await _wait_for(lambda: shape.radio_tx_frames() > 0), (
            "captured loopback frame never reached the radio TX path"
        )

        await bridge.stop()
        assert bridge.bridge_state is BridgeState.IDLE
        assert shape.radio.audio_bus.subscriber_count == 0, "leaked bus subscriber"
        assert not shape.rx_live() and not shape.tx_live()
        assert shape.open_streams() == 0, "leaked radio-side device stream"
        if session is not None:
            assert session.state is AudioSessionState.IDLE
            assert session.rx_demand == 0 and session.tx_demand == 0
        leaked_bridge_streams = sum(
            s.running
            for s in bridge_backend.rx_streams
            + bridge_backend.tx_streams
            + bridge_backend.duplex_streams
        )
        assert leaked_bridge_streams == 0, "leaked loopback device stream"
    finally:
        if shape.close is not None:
            await shape.close()


# ── (d) AudioSession lifecycle through real consumers (bridge + web TX) ──────


async def test_session_lifecycle_via_bridge_and_web_tx_lease(
    usb_rig: _UsbSerialRig, web_server: WebServer
) -> None:
    """IDLE→RX_ONLY→RX_TX→RX_ONLY→IDLE driven by the real consumers.

    The bridge declares RX demand and the web ``/api/v1/audio`` TX handler
    acquires a TX lease — both on the SAME radio-owned AudioSession
    singleton (MOR-579/580), never on private per-consumer sessions.
    """
    radio = usb_rig.radio
    session = radio.audio_session
    assert session.state is AudioSessionState.IDLE

    bridge = AudioBridge(
        radio,
        device_name="BlackHole",
        tx_enabled=False,  # RX-only demand → session settles at RX_ONLY
        backend=FakeAudioBackend([_LOOPBACK]),
    )
    await bridge.start()
    try:
        # Bridge demand landed on the radio-owned singleton: RX_ONLY.
        assert session.state is AudioSessionState.RX_ONLY
        assert session.rx_demand == 1 and session.tx_demand == 0

        host, port = _addr(web_server)
        reader, writer = await _ws_connect(host, port, "/api/v1/audio")
        try:
            await _ws_send_text(
                writer, json.dumps({"type": "audio_start", "direction": "tx"})
            )
            # Web TX lease on the SHARED session → RX_TX.
            assert await _wait_for(lambda: session.tx_demand == 1), (
                "web TX start never acquired a lease on the shared session"
            )
            assert await _wait_for(lambda: session.state is AudioSessionState.RX_TX)

            # A browser TX frame reaches the radio through the lease.
            tx_frame = encode_audio_frame(
                MSG_TYPE_AUDIO_TX, AUDIO_CODEC_PCM16, 0, 480, 1, 20, _LOUD_PCM
            )
            await _ws_send_binary(writer, tx_frame)
            assert await _wait_for(lambda: usb_rig.radio_tx_frames() > 0), (
                "web TX audio never reached the radio TX stream"
            )

            await _ws_send_text(
                writer, json.dumps({"type": "audio_stop", "direction": "tx"})
            )
            # Lease released → TX disarmed, RX demand (bridge) keeps RX alive.
            assert await _wait_for(lambda: session.tx_demand == 0)
            assert await _wait_for(lambda: session.state is AudioSessionState.RX_ONLY)
            assert radio.audio_bus.rx_active, "TX stop killed the bridge RX"
        finally:
            await _close_writer(writer)
    finally:
        await bridge.stop()

    assert session.state is AudioSessionState.IDLE
    assert session.rx_demand == 0 and session.tx_demand == 0
    assert radio.audio_bus.subscriber_count == 0
    assert usb_rig.open_streams() == 0


# ── (d/e) Health watchdog surfaces RECOVERING + runtime payload blocks ───────


async def test_watchdog_recovering_surfaces_in_runtime_payload() -> None:
    """Silent RX flips the session to RECOVERING; frames resume it — and the
    `/api/v1/runtime` payload exposes both ``audioSession`` and ``audioBus``.

    The fast watchdog session is seeded into the radio's lazy
    ``_audio_session`` slot (the MOR-579 singleton seam), so the bridge, the
    web server, and the watchdog all observe ONE session — no real
    multi-second waits (per-instance MOR-581 thresholds).
    """
    rig = _make_usb_serial_rig()
    await rig.radio.connect()
    rig.radio._audio_session = AudioSession(
        rig.radio, watchdog_interval=_WD_INTERVAL, rx_liveness_timeout=_WD_TIMEOUT
    )
    session = rig.radio.audio_session

    server = WebServer(
        rig.radio, WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
    )
    await server.start()
    bridge = AudioBridge(
        rig.radio,
        device_name="BlackHole",
        tx_enabled=False,
        backend=FakeAudioBackend([_LOOPBACK]),
    )
    try:
        await bridge.start()
        assert session.state is AudioSessionState.RX_ONLY

        # No RX frame ever arrives — the silent-death shape (AUHAL -50
        # class): the watchdog must surface RECOVERING, not stay silent.
        assert await _wait_for(lambda: session.state is AudioSessionState.RECOVERING), (
            "watchdog never surfaced the silent RX death"
        )

        host, port = _addr(server)
        runtime = await _http_get_json(host, port, "/api/v1/runtime")
        assert runtime["audioSession"]["enabled"] is True
        assert runtime["audioSession"]["state"] == "recovering"
        assert runtime["audioSession"]["lastEvent"]["reason"] == "rx_silent"
        assert runtime["audioBus"]["enabled"] is True

        # Frames resume (keep the heartbeat advancing while polling) → the
        # session returns to its demand-derived state.
        deadline = time.monotonic() + 3.0
        while (
            session.state is not AudioSessionState.RX_ONLY
            and time.monotonic() < deadline
        ):
            rig.inject_rx(_NONSILENT_PCM)
            await asyncio.sleep(0.005)
        assert session.state is AudioSessionState.RX_ONLY
        assert session.last_event is not None
        assert session.last_event.reason == "rx_resumed"

        runtime = await _http_get_json(host, port, "/api/v1/runtime")
        assert runtime["audioSession"]["state"] == "rx_only"
        # RX heartbeat (MOR-564) exposed next to the session block.
        assert isinstance(runtime["audioBus"]["lastRxFrameMonotonic"], float)
    finally:
        await bridge.stop()
        await server.stop()
        await rig.radio.disconnect()

    assert session.state is AudioSessionState.IDLE
    assert rig.open_streams() == 0


# ── Hardware-only variant (documented, runnable live later) ──────────────────


@pytest.mark.skip(
    reason=(
        "same-device exclusive USB CODEC (FTX-1 shape) is hardware-only: "
        "live CoreAudio kills asymmetrically (TX onto running RX dies with "
        "AUHAL -50, RX onto running TX is clean) while the strict fake "
        "rejects any second stream symmetrically, and the same-device duplex "
        "arming seam (UsbAudioDriver.ensure, MOR-546) is still hw-gated. "
        "The declared transition graph is covered by the "
        "exclusive-graph-stub row above; run this live via "
        "tests/integration with real hardware."
    )
)
async def test_bridge_roundtrip_same_device_exclusive_usb_hardware() -> None:
    """Placeholder for the live same-device exclusive USB bridge round trip."""
    raise AssertionError("hardware-only — must be skipped")
