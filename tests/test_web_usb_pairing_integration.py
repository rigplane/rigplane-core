"""Recorded USB topology through the real browser audio-start dispatcher."""

import asyncio
import json
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
from test_web_server import (
    _close_ws,
    _http_get,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)

from rigplane.audio import usb_driver
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


@pytest.mark.parametrize("os_name", ["windows", "macos", "macos-frozen"])
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
    monkeypatch, codec_matches, explicit_choice, os_name
):
    from rigplane.audio import _usb_pairing

    if os_name.startswith("macos"):
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
                    "hostApi": "Core Audio" if os_name.startswith("macos") else "MME",
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
