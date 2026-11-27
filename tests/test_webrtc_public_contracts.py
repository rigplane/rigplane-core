"""Public WebRTC wire contracts for an out-of-process offerer (MOR-272).

A WebRTC offerer or station agent running in another process (Pro, a
station agent) may import only the public ``rigplane`` surface. These
tests pin that the attach frame (K5), the ICE gathering policy, the
answerer HTTP contract (K6) and the capabilities ``webrtc`` block are
reachable from ``rigplane`` as typed objects, that each contract
round-trips, and that the server's own wire bytes go through the same
types (the producer's parser is the consumer's parser).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest

from test_webrtc_attach_path import RIGCTL_ATTACH_ERROR_V1

import rigplane
from rigplane.web.server import WebConfig, WebServer


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer = b""

    def write(self, data: bytes) -> None:
        self.buffer += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    def get_extra_info(self, *_a: Any, **_k: Any) -> tuple[str, int]:
        return ("127.0.0.1", 0)


def _parse_response(writer: _FakeWriter) -> tuple[int, dict[str, Any]]:
    head, _, body = writer.buffer.partition(b"\r\n\r\n")
    code = int(head.split(b"\r\n", 1)[0].decode().split(" ", 2)[1])
    return code, json.loads(body.decode()) if body else {}


def _reader(payload: object) -> tuple[dict[str, str], asyncio.StreamReader]:
    body = json.dumps(payload).encode()
    reader = asyncio.StreamReader()
    reader.feed_data(body)
    reader.feed_eof()
    return {"content-length": str(len(body))}, reader


# --- public reachability ----------------------------------------------------

_PUBLIC = (
    ("ClientChannelParams", "rigplane.web.channel_attach"),
    ("ChannelAttachedAck", "rigplane.web.channel_attach"),
    ("ChannelKind", "rigplane.web.channel_attach"),
    ("DataChannelSpec", "rigplane.web.channel_attach"),
    ("DataChannelLabel", "rigplane.web.channel_attach"),
    ("DATA_CHANNELS", "rigplane.web.channel_attach"),
    ("ATTACH_ACK_VERSION", "rigplane.web.channel_attach"),
    ("RIGCTL_MAX_FRAME_BYTES", "rigplane.web.channel_attach"),
    ("RIGCTL_VERSIONS", "rigplane.web.channel_attach"),
    ("RigctlAttachErrorCode", "rigplane.web.channel_attach"),
    ("RigctlAttachErrorV1", "rigplane.web.channel_attach"),
    ("decode_rigctl_attach_result", "rigplane.web.channel_attach"),
    ("IceServer", "rigplane.web.transport.ice_gathering"),
    ("IceGatheringPolicy", "rigplane.web.transport.ice_gathering"),
    ("GATHER_TIMEOUT_S", "rigplane.web.transport.ice_gathering"),
    ("install_ice_gathering_policy", "rigplane.web.transport.ice_gathering"),
    ("apply_ice_gathering_policy", "rigplane.web.transport.ice_gathering"),
    ("WebRtcAudioSender", "rigplane.web.transport.webrtc"),
    ("WebRtcOffer", "rigplane.web.transport.webrtc_contract"),
    ("WebRtcAnswer", "rigplane.web.transport.webrtc_contract"),
    ("WebRtcClose", "rigplane.web.transport.webrtc_contract"),
    ("WebRtcCloseResult", "rigplane.web.transport.webrtc_contract"),
    ("WebRtcIceCandidate", "rigplane.web.transport.webrtc_contract"),
    ("WebRtcRequestError", "rigplane.web.transport.webrtc_contract"),
    ("WebRtcTransportStatus", "rigplane.web.transport.webrtc_contract"),
    ("IceCandidateInit", "rigplane.web.transport.webrtc_contract"),
)


@pytest.mark.parametrize(("name", "module"), _PUBLIC)
def test_contract_is_public_and_is_the_producer_object(name: str, module: str) -> None:
    import importlib

    assert name in rigplane.__all__
    assert getattr(rigplane, name) is getattr(importlib.import_module(module), name)


# --- K5: attach frame + attach ack -------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        rigplane.ClientChannelParams("key-1"),
        rigplane.ClientChannelParams(
            "key-2", controller_role="auxiliary", state_delta=1
        ),
    ],
)
def test_attach_frame_round_trips_through_the_answerer_parser(params: Any) -> None:
    frame = params.to_attach_frame()
    assert json.loads(frame)["type"] == "attach"
    assert rigplane.ClientChannelParams.from_attach_frame(frame.encode()) == params


def test_keyless_api_attach_frame_round_trips() -> None:
    params = rigplane.ClientChannelParams(None)
    frame = params.to_attach_frame().encode()
    with pytest.raises(ValueError):
        rigplane.ClientChannelParams.from_attach_frame(frame)
    assert (
        rigplane.ClientChannelParams.from_attach_frame(frame, require_key=False)
        == params
    )


def test_attach_ack_round_trips() -> None:
    ack = rigplane.ChannelAttachedAck("scope")
    assert rigplane.ChannelAttachedAck.from_frame(ack.encode()) == ack
    assert rigplane.ChannelAttachedAck.from_frame(ack.encode().encode()) == ack


def test_rigctl_refusal_decodes_through_the_public_package() -> None:
    from rigplane import (
        ATTACH_ACK_VERSION,
        RIGCTL_MAX_FRAME_BYTES,
        RIGCTL_VERSIONS,
        RigctlAttachErrorCode,
        RigctlAttachErrorV1,
        decode_rigctl_attach_result,
    )

    code: RigctlAttachErrorCode = "rigctl_busy"
    result = decode_rigctl_attach_result(RIGCTL_ATTACH_ERROR_V1)
    assert result == RigctlAttachErrorV1(code, ATTACH_ACK_VERSION, "rigctl")
    assert ATTACH_ACK_VERSION == 1
    assert RIGCTL_VERSIONS == (1,)
    assert RIGCTL_MAX_FRAME_BYTES == 16 * 1024


@pytest.mark.parametrize(
    "frame",
    [
        "not json",
        "[]",
        '{"type": "attach", "v": 1, "channel": "scope"}',
        '{"type": "attached", "v": "1", "channel": "scope"}',
        '{"type": "attached", "v": 1}',
    ],
)
def test_attach_ack_rejects_other_frames(frame: str) -> None:
    with pytest.raises(ValueError):
        rigplane.ChannelAttachedAck.from_frame(frame)


# --- K6: answerer HTTP contract ------------------------------------------------


def test_offer_round_trips_with_ice_servers() -> None:
    offer = rigplane.WebRtcOffer(
        sdp="v=0\r\n",
        ice_servers=[
            rigplane.IceServer(
                urls=["turn:t.example:3478"], username="u", credential="c"
            )
        ],
    )
    assert rigplane.WebRtcOffer.from_json(offer.to_json()) == offer
    assert rigplane.WebRtcOffer.from_json({"sdp": "v=0"}).type == "offer"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"type": "offer"}, "missing_sdp"),
        ({"sdp": "   "}, "missing_sdp"),
        ({"sdp": "v=0", "ice_servers": "stun:x"}, "invalid_ice_servers"),
        ({"sdp": "v=0", "ice_servers": [{"urls": []}]}, "invalid_ice_servers"),
        ({"sdp": "v=0", "type": 3}, "sdp_error"),
    ],
)
def test_offer_parser_error_codes(body: dict[str, Any], code: str) -> None:
    with pytest.raises(rigplane.WebRtcRequestError) as info:
        rigplane.WebRtcOffer.from_json(body)
    assert info.value.code == code


def test_answer_round_trips() -> None:
    answer = rigplane.WebRtcAnswer(session_id="abc", sdp="v=0\r\n", type="answer")
    wire = answer.to_json()
    assert wire["status"] == "ok"
    assert rigplane.WebRtcAnswer.from_json(wire) == answer
    with pytest.raises(ValueError):
        rigplane.WebRtcAnswer.from_json({"status": "ok", "sessionId": "abc"})


_HOST = "candidate:1 1 udp 1 1.2.3.4 5 typ host"


def test_ice_candidate_round_trips_and_codes() -> None:
    cand = rigplane.WebRtcIceCandidate(
        session_id="abc",
        candidate=rigplane.IceCandidateInit(
            candidate=_HOST, sdpMid="0", sdpMLineIndex=0
        ),
    )
    assert rigplane.WebRtcIceCandidate.from_json(cand.to_json()) == cand
    end = rigplane.WebRtcIceCandidate(session_id="abc", candidate=None)
    assert rigplane.WebRtcIceCandidate.from_json(end.to_json()) == end
    for body, code in (
        ({"candidate": None}, "missing_session"),
        ({"sessionId": ""}, "missing_session"),
        ({"sessionId": "abc", "candidate": "x"}, "invalid_candidate"),
    ):
        with pytest.raises(rigplane.WebRtcRequestError) as info:
            rigplane.WebRtcIceCandidate.from_json(body)
        assert info.value.code == code


def test_ice_candidate_init_wire_matches_k1_shape() -> None:
    """The wire form carries exactly the three RTCIceCandidateInit fields."""
    init = rigplane.IceCandidateInit(candidate=_HOST, sdpMid=None, sdpMLineIndex=None)
    assert init.to_json() == {"candidate": _HOST, "sdpMid": None, "sdpMLineIndex": None}


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"candidate": ""},
        {"candidate": "", "sdpMid": "0", "sdpMLineIndex": 0},
    ],
)
def test_ice_candidate_end_of_candidates_forms_parse_to_none(raw: Any) -> None:
    parsed = rigplane.WebRtcIceCandidate.from_json({"sessionId": "s", "candidate": raw})
    assert parsed.candidate is None


def test_ice_candidate_init_tolerates_browser_extra_keys() -> None:
    raw = {
        "candidate": _HOST,
        "sdpMid": "0",
        "sdpMLineIndex": 0,
        "usernameFragment": "u",
    }
    parsed = rigplane.WebRtcIceCandidate.from_json({"sessionId": "s", "candidate": raw})
    assert parsed.candidate == rigplane.IceCandidateInit(_HOST, "0", 0)


@pytest.mark.parametrize(
    "raw",
    [
        {"candidate": 5},
        {"candidate": _HOST, "sdpMid": 0},
        {"candidate": _HOST, "sdpMLineIndex": "0"},
        {"candidate": _HOST, "sdpMLineIndex": -1},
        {"candidate": _HOST, "sdpMLineIndex": True},
    ],
)
def test_ice_candidate_init_rejects_mistyped_fields(raw: dict[str, Any]) -> None:
    with pytest.raises(rigplane.WebRtcRequestError) as info:
        rigplane.WebRtcIceCandidate.from_json({"sessionId": "s", "candidate": raw})
    assert info.value.code == "invalid_candidate"


def test_close_round_trips() -> None:
    close_all = rigplane.WebRtcClose()
    assert rigplane.WebRtcClose.from_json(close_all.to_json()) == close_all
    assert rigplane.WebRtcClose.from_json({}) == close_all
    assert rigplane.WebRtcClose.from_json({"sessionId": None}) == close_all
    by_id = rigplane.WebRtcClose(session_id="abc")
    assert rigplane.WebRtcClose.from_json(by_id.to_json()) == by_id
    for body in ({"sessionId": 5}, {"sessionId": ""}):
        with pytest.raises(rigplane.WebRtcRequestError) as info:
            rigplane.WebRtcClose.from_json(body)
        assert info.value.code == "invalid_session"


def test_close_result_round_trips() -> None:
    result = rigplane.WebRtcCloseResult(closed=("a", "b"))
    wire = result.to_json()
    assert wire["status"] == "ok"
    assert rigplane.WebRtcCloseResult.from_json(wire) == result
    assert (
        rigplane.WebRtcCloseResult.from_json({"status": "ok", "closed": []})
        == rigplane.WebRtcCloseResult()
    )
    for bad in (
        {"status": "ok", "closed": "a"},
        {"status": "ok", "closed": [1]},
        {"status": "error", "closed": []},
    ):
        with pytest.raises(ValueError):
            rigplane.WebRtcCloseResult.from_json(bad)


def test_request_error_round_trips() -> None:
    err = rigplane.WebRtcRequestError("missing_sdp", "Field 'sdp' required.")
    back = rigplane.WebRtcRequestError.from_json(err.to_json())
    assert (back.code, back.message) == (err.code, err.message)


def test_transport_status_round_trips() -> None:
    status = rigplane.WebRtcTransportStatus(available=True, enabled=False)
    assert rigplane.WebRtcTransportStatus.from_json(status.to_json()) == status
    with pytest.raises(ValueError):
        rigplane.WebRtcTransportStatus.from_json({"available": 1, "enabled": True})


# --- the server speaks through the same types -------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"type": "offer"}, "missing_sdp"),
        ({"sdp": "v=0", "ice_servers": 5}, "invalid_ice_servers"),
    ],
)
async def test_server_offer_errors_parse_as_request_error(
    body: dict[str, Any], code: str
) -> None:
    server = WebServer(None, WebConfig(webrtc_enabled=True))
    headers, reader = _reader(body)
    writer = _FakeWriter()
    with patch("rigplane.web.server.webrtc_available", return_value=True):
        await server._handle_webrtc_offer(writer, headers, reader)  # noqa: SLF001
    status, payload = _parse_response(writer)
    assert status == 400
    assert rigplane.WebRtcRequestError.from_json(payload).code == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"candidate": None}, 400, "missing_session"),
        ({"sessionId": "abc", "candidate": 1}, 400, "invalid_candidate"),
        ({"sessionId": "abc", "candidate": {"candidate": 7}}, 400, "invalid_candidate"),
        ({"sessionId": "nope", "candidate": None}, 404, "ice_error"),
    ],
)
async def test_server_ice_errors_parse_as_request_error(
    body: dict[str, Any], status: int, code: str
) -> None:
    server = WebServer(None, WebConfig(webrtc_enabled=True))
    headers, reader = _reader(body)
    writer = _FakeWriter()
    with patch("rigplane.web.server.webrtc_available", return_value=True):
        await server._handle_webrtc_ice(writer, headers, reader)  # noqa: SLF001
    got_status, payload = _parse_response(writer)
    assert got_status == status
    assert rigplane.WebRtcRequestError.from_json(payload).code == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"sessionId": 5}, 400, "invalid_session"),
        ({"sessionId": ""}, 400, "invalid_session"),
        ({"sessionId": "nope"}, 404, "close_error"),
    ],
)
async def test_server_close_errors_parse_as_request_error(
    body: dict[str, Any], status: int, code: str
) -> None:
    server = WebServer(None, WebConfig(webrtc_enabled=True))
    headers, reader = _reader(body)
    writer = _FakeWriter()
    with patch("rigplane.web.server.webrtc_available", return_value=True):
        await server._handle_webrtc_close(writer, headers, reader)  # noqa: SLF001
    got_status, payload = _parse_response(writer)
    assert got_status == status
    assert rigplane.WebRtcRequestError.from_json(payload).code == code


@pytest.mark.asyncio
async def test_server_close_with_nothing_open_succeeds() -> None:
    """Closing when nothing is open is a no-op success (the K6 contract)."""
    server = WebServer(None, WebConfig(webrtc_enabled=True))
    headers, reader = _reader({})
    writer = _FakeWriter()
    with patch("rigplane.web.server.webrtc_available", return_value=True):
        await server._handle_webrtc_close(writer, headers, reader)  # noqa: SLF001
    status, payload = _parse_response(writer)
    assert status == 200
    assert rigplane.WebRtcCloseResult.from_json(payload) == rigplane.WebRtcCloseResult()


@pytest.mark.asyncio
async def test_server_unavailable_body_parses_as_request_error() -> None:
    server = WebServer(None, WebConfig())
    writer = _FakeWriter()
    await server._handle_webrtc_offer(writer, {"content-length": "2"}, None)  # noqa: SLF001
    status, payload = _parse_response(writer)
    assert status == 503
    assert rigplane.WebRtcRequestError.from_json(payload).code == "webrtc_unavailable"


@pytest.mark.asyncio
async def test_capabilities_webrtc_block_parses_as_transport_status() -> None:
    server = WebServer(None, WebConfig(webrtc_enabled=True, radio_model="IC-7610"))
    writer = _FakeWriter()
    with patch("rigplane.web.server.webrtc_available", return_value=True):
        await server._serve_capabilities(writer)  # noqa: SLF001
    _, body = _parse_response(writer)
    status = rigplane.WebRtcTransportStatus.from_json(body["webrtc"])
    assert status == rigplane.WebRtcTransportStatus(available=True, enabled=True)


def test_audio_sender_counts_duration_for_variable_size_opus_packets() -> None:
    from rigplane.web.protocol import (
        AUDIO_CODEC_OPUS,
        MSG_TYPE_AUDIO_RX,
        encode_audio_frame,
    )

    class BufferedChannel:
        bufferedAmount = 0

        def send(self, data: bytes) -> None:
            self.bufferedAmount += len(data)

    port = BufferedChannel()
    sender = rigplane.WebRtcAudioSender(port)

    def packet(n: int) -> bytes:
        # Configuration 31: one 20 ms Opus frame. The wire duration is advisory.
        return encode_audio_frame(
            MSG_TYPE_AUDIO_RX,
            AUDIO_CODEC_OPUS,
            0,
            480,
            1,
            1,
            bytes([31 << 3]) + bytes(n),
        )

    frames = [packet(50), packet(500)] * 5
    assert all(sender.try_send(frame) for frame in frames)
    assert not sender.try_send(packet(12))
    port.bufferedAmount -= len(frames[0])
    assert sender.try_send(packet(12))
    assert not sender.try_send(packet(400))
    port.bufferedAmount = 0
    assert sender.try_send(packet(400))
