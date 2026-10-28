"""Browser audio start; Linux uses synthetic sysfs, not hardware-verified."""

import asyncio
import json
import math
import struct
import sys
from dataclasses import replace
from unittest.mock import Mock

import pytest
from test_icom7610_serial_radio import _FakeSerialCivLink
from test_usb_pairing_windows import _inject_windows, _w1_session
from test_usb_pairing_macos import (
    MAC_PORT,
    MAC_RX_ID,
    MAC_TX_ID,
    _inject_macos,
    _macos_session,
)
from test_usb_pairing_linux import LINUX_ID, LINUX_NAME, LINUX_PORT, _inject_linux
from test_web_server import (
    _close_ws,
    _http_get,
    _read_http_response,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)

from rigplane.audio import usb_driver
from rigplane.audio.backend import FakeAudioBackend
from rigplane.audio.candidate_levels import CandidateLevelProbe
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.core.types import AudioCodec
from rigplane.core.usb_identity import UsbAudioIdentity, UsbFunctionId
from rigplane.profiles import get_radio_profile
from rigplane.web.protocol import (
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_RX,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.websocket import WS_OP_BINARY, WS_OP_TEXT


W1_RX_ID = r"MME|SWD\MMDEVAPI\{0.0.1.00000000}.{963D478C-4D8C-412E-A32C-387169AFB3A0}"
W1_TX_ID = r"MME|SWD\MMDEVAPI\{0.0.0.00000000}.{9082644E-6A88-4638-B416-E1788AAC0FDF}"


async def _http_post(
    host: str, port: int, path: str
) -> tuple[int, dict[str, str], bytes]:
    """Minimal bodyless HTTP POST over asyncio (mirrors ``_http_get``)."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Length: 0\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(request.encode())
        await writer.drain()
        return await asyncio.wait_for(_read_http_response(reader), timeout=15.0)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _sine_frame(amplitude: float = 0.25, samples: int = 960) -> bytes:
    return struct.pack(
        f"<{samples}h",
        *(
            round(amplitude * 32767 * math.sin(2 * math.pi * 440 * i / 48_000))
            for i in range(samples)
        ),
    )


@pytest.mark.parametrize(
    "os_name", ["windows", "macos", "macos-frozen", "linux", "linux-frozen"]
)
@pytest.mark.parametrize(
    "codec_matches,explicit_choice",
    [
        (True, False),
        (False, False),
        (False, True),
    ],
    ids=["w1-mme", "wrong-codec", "stable-choice-overrides-wrong-codec"],
)
async def test_w1_owner_browser_audio_start_replays_windows_usb_pairing(
    tmp_path, monkeypatch, codec_matches, explicit_choice, os_name
):
    """Real web/driver seam; Linux fixtures are not hardware-verified."""
    from rigplane.audio import _usb_pairing

    if os_name.startswith("linux"):
        if os_name == "linux-frozen":
            monkeypatch.setattr(sys, "frozen", True, raising=False)
            monkeypatch.setattr(sys, "executable", "frozen-sidecar-must-not-relaunch")
            monkeypatch.setattr(
                "subprocess.Popen",
                Mock(side_effect=AssertionError("Linux pairing must stay in process")),
            )
        backend, _, _, _ = _inject_linux(tmp_path, monkeypatch)
        rx_id = tx_id = LINUX_ID
        indices = (0, 0)
        radio_class, port, model, address = Ic705SerialRadio, LINUX_PORT, "IC-705", 0xA4
        expected_candidates = (
            (rx_id, 0, LINUX_NAME, "capture"),
            (tx_id, 0, LINUX_NAME, "render"),
        )
    elif os_name.startswith("macos"):
        frozen = os_name == "macos-frozen"
        if frozen:
            monkeypatch.setattr(sys, "frozen", True, raising=False)
            monkeypatch.setattr(sys, "executable", "frozen-sidecar-must-not-relaunch")
        backend, _ = _inject_macos(monkeypatch, _macos_session(), real_reader=frozen)
        rx_id, tx_id, indices = MAC_RX_ID, MAC_TX_ID, (2, 1)
        radio_class, port, model, address = Ic705SerialRadio, MAC_PORT, "IC-705", 0xA4
        expected_candidates = (
            (rx_id, 2, "USB Audio CODEC", "capture"),
            (tx_id, 1, "USB Audio CODEC", "render"),
        )
    else:
        backend, _ = _inject_windows(monkeypatch, _w1_session())
        rx_id, tx_id, indices = W1_RX_ID, W1_TX_ID, (1, 5)
        radio_class, port, model, address = Ic7300SerialRadio, "COM3", "IC-7300", 0x94
        expected_candidates = (
            (rx_id, 1, "Microphone (USB Audio CODEC )", "capture"),
            (tx_id, 5, "Speakers (USB Audio CODEC )", "render"),
        )
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    pairing = Mock(wraps=_usb_pairing.pair)
    monkeypatch.setattr(_usb_pairing, "pair", pairing)
    profile = get_radio_profile(model)
    if not codec_matches:
        profile = replace(
            profile,
            usb_identity=UsbAudioIdentity(frozenset({UsbFunctionId("0D8C", "0016")})),
        )
    radio = radio_class(
        device=port,
        profile=profile,
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        rx_device=rx_id if explicit_choice else None,
        tx_device=tx_id if explicit_choice else None,
        civ_link=_FakeSerialCivLink(model_id=address),
        _enumerate_serial_ports_fn=lambda: [],
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = None
    try:
        await radio.connect()
        # Bind the real HTTP/WS dispatcher without the unrelated CAT poller.
        server._server = await asyncio.start_server(
            server._accept_client, "127.0.0.1", 0
        )
        reader, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/audio")
        await _ws_send_text(
            writer,
            json.dumps(
                {
                    "type": "audio_start",
                    "direction": "rx",
                    "preferred_rx_codec": "pcm16",
                }
            ),
        )
        messages = []
        async with asyncio.timeout(5):
            while True:
                opcode, payload = await _ws_recv_frame(reader)
                assert opcode == WS_OP_TEXT
                message = json.loads(payload)
                messages.append(message)
                if message["type"] == "audio_format":
                    break

        status, _, body = await _http_get("127.0.0.1", server.port, "/api/v1/info")
        assert status == 200
        audio_receive = json.loads(body)["audioReceive"]
        if not codec_matches and not explicit_choice:
            assert audio_receive["schemaVersion"] == 1
            failure = audio_receive["failure"]
            assert {
                key: value for key, value in failure.items() if key != "candidates"
            } == {
                "stage": "rx_start",
                "code": "core_rx_start_failed",
                "reason": "usb_topology_unavailable",
            }
            candidates = failure["candidates"]
            for stable_id, index, name, direction in expected_candidates:
                assert {
                    "stableId": stable_id,
                    "displayName": name,
                    "direction": direction,
                    "hostApi": "ALSA"
                    if os_name.startswith("linux")
                    else "Core Audio"
                    if os_name.startswith("macos")
                    else "MME",
                    "portAudioIndex": index,
                    "nearRadio": True,
                } in candidates
            assert all(
                set(candidate)
                == {
                    "stableId",
                    "displayName",
                    "direction",
                    "hostApi",
                    "portAudioIndex",
                    "nearRadio",
                }
                for candidate in candidates
            )
            assert any(message["type"] == "error" for message in messages)
            assert radio.audio_session.rx_demand == 0
            assert not (
                backend.rx_streams or backend.tx_streams or backend.duplex_streams
            )
            return

        assert audio_receive == {"schemaVersion": 1, "failure": None}
        if explicit_choice:
            pairing.assert_not_called()
        assert not any(message["type"] == "error" for message in messages)
        assert radio.audio_session.rx_demand == 1
        driver = radio._serial_audio_driver
        assert (
            driver.selected_rx_device.index,
            driver.selected_tx_device.index,
        ) == indices
        assert len(backend.rx_streams) == 1
        assert backend.rx_streams[0].running
        pcm = b"\x01\x00" * 960
        backend.rx_streams[0].inject_frame(pcm)
        opcode, frame = await _ws_recv_frame(reader, timeout=5)
        assert opcode == WS_OP_BINARY
        assert frame[:2] == bytes([MSG_TYPE_AUDIO_RX, AUDIO_CODEC_PCM16])
        assert frame[AUDIO_HEADER_SIZE:] == pcm
    finally:
        try:
            if writer is not None:
                await _close_ws(writer)
        finally:
            try:
                await server.stop()
            finally:
                await radio.disconnect()


async def test_serial_ambiguous_refusal_candidate_levels_over_http(
    tmp_path, monkeypatch
):
    """Real S3 refusal → info candidates → live level probe (MOR-3823).

    Drives the real seam: pairing refusal latches candidates on the
    broadcaster, ``/api/v1/info`` exposes them, and a real HTTP POST to
    ``/api/v1/audio/candidate-levels`` measures each capture candidate on
    the fake backend. macOS fixture replay; not hardware-verified.
    """
    session = _macos_session()
    raw = session["raw"]["macos"]
    # A second, undeclared serial function under the radio's own hub forces
    # the S3 rule (serial_ambiguous) while keeping the codec pairable.
    serial_row = next(
        row for row in raw["usb_devices"] if row["registry_id"] == 4295496601
    )
    raw["usb_devices"].append(
        {**serial_row, "registry_id": 100, "vendor_id": 0xFFFF, "product_id": 0x0001}
    )
    raw["serial_devices"].append({"device": "/dev/cu.extra0", "usb_registry_id": 100})
    backend, _ = _inject_macos(monkeypatch, session)
    assert isinstance(backend, FakeAudioBackend)
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    radio = Ic705SerialRadio(
        device=MAC_PORT,
        profile=get_radio_profile("IC-705"),
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        civ_link=_FakeSerialCivLink(model_id=0xA4),
        _enumerate_serial_ports_fn=lambda: [],
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    server._candidate_level_probe = CandidateLevelProbe(capture_window_s=0.1)
    writer = None
    try:
        await radio.connect()
        server._server = await asyncio.start_server(
            server._accept_client, "127.0.0.1", 0
        )
        _, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/audio")
        await _ws_send_text(
            writer, json.dumps({"type": "audio_start", "direction": "rx"})
        )
        failure = None
        async with asyncio.timeout(5):
            while failure is None:
                status, _, body = await _http_get(
                    "127.0.0.1", server.port, "/api/v1/info"
                )
                assert status == 200
                failure = json.loads(body)["audioReceive"]["failure"]
                await asyncio.sleep(0.02)
        assert failure["reason"] == "usb_ambiguous"
        candidates = failure["candidates"]
        capture = [c for c in candidates if c["direction"] == "capture"]
        assert capture

        post = asyncio.create_task(
            _http_post("127.0.0.1", server.port, "/api/v1/audio/candidate-levels")
        )
        sine = _sine_frame()
        async with asyncio.timeout(15):
            while not post.done():
                for stream in backend.rx_streams:
                    if stream.running:
                        stream.inject_frame(sine)
                await asyncio.sleep(0.01)
        status, _, body = post.result()
        assert status == 200
        payload = json.loads(body)
        assert payload["schemaVersion"] == 1
        assert [level["stableId"] for level in payload["levels"]] == [
            candidate["stableId"] for candidate in capture
        ]
        for level in payload["levels"]:
            assert level["state"] == "ok"
            assert -13.0 < level["peakDbfs"] < -11.0

        # The probe changed nothing: the latch still reports the same refusal
        # and the relay never started.
        status, _, body = await _http_get("127.0.0.1", server.port, "/api/v1/info")
        assert status == 200
        assert json.loads(body)["audioReceive"]["failure"] == failure
        assert radio.audio_session.rx_demand == 0
    finally:
        try:
            if writer is not None:
                await _close_ws(writer)
        finally:
            try:
                await server.stop()
            finally:
                await radio.disconnect()
