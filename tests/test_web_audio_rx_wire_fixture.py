"""Shared RX wire fixture against the REAL /api/v1/audio handler (MOR-3965).

Paired handler test for
``frontend/src/lib/audio/__tests__/audio-manager-hidden-burst.isolated.test.ts``:
both files consume ``tests/fixtures/audio_rx_session_wire.json``. This side
drives a REAL WebServer on a fake audio-capable radio (``_SessionLanRadio``)
over a REAL WebSocket with the fixture's ``audio_start`` request (variable
keys filled from ``exampleVariables``), then asserts the server accepts it
(RX demand registered, no error message) and the FIRST emitted binary frame
matches the fixture header/payload byte-for-byte (the broadcaster numbers
frames from sequence 0, so the first frame is deterministic). A header-format
regression on the server side fails HERE, not silently in the JS spec —
that is the owner's paired-handler test rule.

Follows tests/test_web_usb_pairing_integration.py (real WS over a directly
bound ``_accept_client`` listener) with the lightest fake radio available
(tests/test_web_audio_session_rx.py ``_SessionLanRadio``).
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path

from test_web_audio_session_rx import _SessionLanRadio
from test_web_server import (
    _close_ws,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)

from rigplane.audio import AudioPacket
from rigplane.web.protocol import (
    AUDIO_HEADER_SIZE,
    encode_audio_frame,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.ws import WS_OP_BINARY, WS_OP_TEXT

_FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "audio_rx_session_wire.json"
)
_FIXTURE = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _request_from_fixture() -> dict[str, object]:
    """Fixture request with the per-browser/session variable keys filled in."""
    section = _FIXTURE["audio_start_rx"]
    request = dict(section["stable"])
    request.update(section["exampleVariables"])
    assert set(request) == set(section["stable"]) | set(section["variableKeys"])
    return request


def _payload_from_fixture() -> bytes:
    """Payload bytes generated from the fixture's payload rule."""
    section = _FIXTURE["rx_frame_pcm16_mono_20ms"]
    payload = section["payload"]
    assert payload["encoding"] == "int16le"
    assert payload["rule"] == "sample[i] = i % 100"
    count = int(payload["samples"])
    return struct.pack(f"<{count}h", *(i % 100 for i in range(count)))


def _expected_header() -> bytes:
    section = _FIXTURE["rx_frame_pcm16_mono_20ms"]
    header = bytes.fromhex(section["headerHexWithSequence0"])
    assert len(header) == AUDIO_HEADER_SIZE
    return header


def test_encode_audio_frame_matches_shared_wire_fixture() -> None:
    """``encode_audio_frame`` with the fixture fields emits the fixture header."""
    section = _FIXTURE["rx_frame_pcm16_mono_20ms"]
    pcm = _payload_from_fixture()
    assert len(pcm) == 960 * 2
    frame = encode_audio_frame(
        section["msg_type"],
        section["codec"],
        section["sequence"],
        section["sample_rate_div100"],
        section["channels"],
        section["frame_ms"],
        pcm,
    )
    assert frame[:AUDIO_HEADER_SIZE] == _expected_header()
    assert frame[AUDIO_HEADER_SIZE:] == pcm


async def test_audio_start_rx_and_first_frame_match_shared_wire_fixture() -> None:
    """Real /api/v1/audio round trip: fixture request in, fixture frame out."""
    radio = _SessionLanRadio()
    server = WebServer(
        radio, WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
    )
    # Bind only the HTTP/WS dispatcher (no unrelated CAT poller), mirroring
    # tests/test_web_usb_pairing_integration.py.
    server._server = await asyncio.start_server(server._accept_client, "127.0.0.1", 0)
    writer = None
    try:
        reader, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/audio")
        await _ws_send_text(writer, json.dumps(_request_from_fixture()))

        # The server accepts the subscription: an audio_format ack arrives
        # with no error message on the way.
        async with asyncio.timeout(5):
            while True:
                opcode, payload = await _ws_recv_frame(reader)
                assert opcode == WS_OP_TEXT
                message = json.loads(payload)
                assert message.get("type") != "error", message
                if message.get("type") == "audio_format":
                    break
        assert radio.audio_session.rx_demand == 1

        # The first emitted binary frame matches the fixture byte-for-byte
        # (fresh broadcaster → sequence 0, deterministic).
        pcm = _payload_from_fixture()
        assert radio.rx_callback is not None
        radio.rx_callback(AudioPacket(ident=0x0080, send_seq=1, data=pcm))
        async with asyncio.timeout(5):
            opcode, frame = await _ws_recv_frame(reader)
        assert opcode == WS_OP_BINARY
        assert frame[:AUDIO_HEADER_SIZE] == _expected_header()
        assert frame[AUDIO_HEADER_SIZE:] == pcm
    finally:
        try:
            if writer is not None:
                await _close_ws(writer)
        finally:
            await server._audio_broadcaster._stop_relay()
            await server.stop()
