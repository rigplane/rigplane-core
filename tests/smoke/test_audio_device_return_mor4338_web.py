"""MOR-4338: RX and TX through the real web audio WebSocket after the radio returns.

The station keeps running while the radio is powered off and on; the PortAudio
device scan the driver holds predates the return. The browser then opens
``/api/v1/audio`` the way the web UI does. The real WebServer, real serial
radio and real ``UsbAudioDriver`` run on a backend that only lists the codec
after a re-scan, so removing the re-scan and retry leaves RX silent and TX
undelivered.
"""

from __future__ import annotations

import json

from smoke.test_audio_path_smoke import (
    _LOUD_PCM,
    _NONSILENT_PCM,
    _RX_DEV,
    _TX_DEV,
    _addr,
    _close_writer,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_binary,
    _ws_send_text,
    _wait_for_identity_hold_clear,
)
from test_icom7610_serial_radio import _FakeSerialCivLink
from test_managed_tx_web_projection import _GatePort, _gate_authority
from test_usb_audio_device_return_mor4338 import _ReturnedRadioBackend

from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.testing.timing import await_until
from rigplane.web.protocol import (
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_RX,
    MSG_TYPE_AUDIO_TX,
    encode_audio_frame,
)
from rigplane.web.server import WebConfig, WebServer


async def test_web_rx_and_tx_work_after_the_radio_returns() -> None:
    backend = _ReturnedRadioBackend(stale=[], fresh=[_RX_DEV, _TX_DEV])
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB-fake",
        civ_link=_FakeSerialCivLink(),
        audio_driver=UsbAudioDriver(
            rx_device="Rig CODEC In", tx_device="Rig CODEC Out", backend=backend
        ),
        timeout=0.2,
    )
    await radio.connect()
    await _wait_for_identity_hold_clear(radio)
    server = WebServer(
        radio, WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
    )
    await server.start()
    managed = _gate_authority()
    server._production_managed_tx_port = _GatePort(managed)  # type: ignore[assignment]
    try:
        host, port = _addr(server)
        reader, writer = await _ws_connect(host, port, "/api/v1/audio")
        try:
            await _ws_send_text(
                writer, json.dumps({"type": "audio_start", "direction": "rx"})
            )
            assert await await_until(
                lambda: any(s.running for s in backend.rx_streams)
            ), "RX capture never opened after the radio returned"
            assert backend.refreshes == 1
            backend.rx_streams[-1].inject_frame(_NONSILENT_PCM)
            while True:
                opcode, payload = await _ws_recv_frame(reader)
                if opcode == 0x2:
                    break
            assert payload[0] == MSG_TYPE_AUDIO_RX
            assert any(payload[AUDIO_HEADER_SIZE:])

            await _ws_send_text(
                writer, json.dumps({"type": "audio_start", "direction": "tx"})
            )
            assert await await_until(lambda: radio.audio_session.tx_demand == 1)
            await managed.ptt_down("web")
            await _ws_send_binary(
                writer,
                encode_audio_frame(
                    MSG_TYPE_AUDIO_TX, AUDIO_CODEC_PCM16, 0, 480, 1, 20, _LOUD_PCM
                ),
            )
            assert await await_until(
                lambda: sum(len(s.written_frames) for s in backend.tx_streams) > 0
            ), "web TX audio never reached the radio after it returned"
            await managed.ptt_up("web")
        finally:
            await _close_writer(writer)
    finally:
        await managed.close()
        await server.stop()
        await radio.disconnect()
