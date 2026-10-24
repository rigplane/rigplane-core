"""Twin of owner E2E: W1 GTR7 + IC-7300 Windows browser audio start."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_icom7610_serial_radio import _FakeSerialCivLink
from test_usb_pairing_windows import _inject_windows, _w1_session
from test_web_server import (
    _close_ws,
    _http_get,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)

from rigplane.audio import usb_driver
from rigplane.backends.ic7300 import Ic7300SerialRadio
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


@pytest.mark.parametrize("codec_matches", [True, False], ids=["w1-mme", "wrong-codec"])
async def test_w1_owner_browser_audio_start_replays_windows_usb_pairing(
    monkeypatch, codec_matches
):
    backend, _ = _inject_windows(monkeypatch, _w1_session())
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    profile = get_radio_profile("IC-7300")
    if not codec_matches:
        profile = replace(
            profile,
            usb_identity=UsbAudioIdentity(frozenset({UsbFunctionId("0D8C", "0016")})),
        )
    radio = Ic7300SerialRadio(
        device="COM3",
        profile=profile,
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        civ_link=_FakeSerialCivLink(model_id=0x94),
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
        if not codec_matches:
            assert audio_receive == {
                "schemaVersion": 1,
                "failure": {
                    "stage": "rx_start",
                    "code": "core_rx_start_failed",
                    "reason": "usb_topology_unavailable",
                },
            }
            assert any(message["type"] == "error" for message in messages)
            assert radio.audio_session.rx_demand == 0
            assert not (
                backend.rx_streams or backend.tx_streams or backend.duplex_streams
            )
            return

        assert audio_receive == {"schemaVersion": 1, "failure": None}
        assert not any(message["type"] == "error" for message in messages)
        assert radio.audio_session.rx_demand == 1
        driver = radio._serial_audio_driver
        assert (driver.selected_rx_device.index, driver.selected_tx_device.index) == (
            1,
            5,
        )
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
