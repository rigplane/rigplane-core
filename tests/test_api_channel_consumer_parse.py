"""Consumer-side parsers of the ``api`` channel, driven by the golden file.

An out-of-process peer (the Pro client) reads RES_HEAD and ERROR frames
with the producer's own parsers. The frames come from the committed golden
documents, which the server's encoders build, so a parser that stops
reading what the producer writes fails here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import rigplane
from rigplane.web.api_channel import (
    ApiErrorFrame,
    ApiFrame,
    ApiMessageType,
    ApiPayloadError,
    ApiResponseHead,
    GoldenDocuments,
    GoldenFrame,
    api_channel_golden_documents_from_json,
    parse_api_server_frame,
)

_GOLDEN_JSON = (
    Path(__file__).resolve().parents[1]
    / "frontend/src/lib/transport/api-channel-golden.json"
)


def _golden() -> GoldenDocuments:
    return api_channel_golden_documents_from_json(
        json.loads(_GOLDEN_JSON.read_text(encoding="utf-8"))
    )


def _wire(frame: GoldenFrame) -> ApiFrame:
    return ApiFrame.parse(bytes.fromhex(frame.hex))


def test_public_exports_are_the_producer_types() -> None:
    assert rigplane.ApiResponseHead is ApiResponseHead
    assert rigplane.ApiErrorFrame is ApiErrorFrame
    assert rigplane.ApiPayloadError is ApiPayloadError
    assert rigplane.parse_api_server_frame is parse_api_server_frame


def test_golden_response_head_parses_to_the_typed_head() -> None:
    doc = _golden().documents["api_ok"]
    frame = _wire(doc.reply[0])
    assert frame.type == ApiMessageType.RES_HEAD
    head = ApiResponseHead.parse(frame.payload)
    assert head.status == doc.reply[0].payload["status"] == 200
    assert head.headers == doc.reply[0].payload["headers"]
    assert parse_api_server_frame(frame) == head


def test_every_golden_error_frame_parses_to_the_typed_error() -> None:
    errors = _golden().documents["api_errors"]
    assert errors.frames
    for golden_frame in errors.frames:
        frame = _wire(golden_frame)
        parsed = parse_api_server_frame(frame)
        assert isinstance(parsed, ApiErrorFrame)
        assert parsed == ApiErrorFrame.parse(frame.payload)
        assert parsed.code == golden_frame.payload["code"]
        assert parsed.message == golden_frame.payload["message"]
        assert parsed.code in errors.triggers


def test_encode_then_parse_round_trips() -> None:
    head = ApiResponseHead(404, {"Content-Type": "application/json"})
    assert ApiResponseHead.parse(head.encode()) == head
    err = ApiErrorFrame("busy", "too many requests")
    assert ApiErrorFrame.parse(err.encode()) == err


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\xff\xfe",
        b"not json",
        b"[1, 2]",
        b'{"headers": {}}',
        b'{"status": 204}',
        b'{"status": 204, "headers": null}',
        b'{"status": "200", "headers": {}}',
        b'{"status": true, "headers": {}}',
        b'{"status": 200, "headers": []}',
        b'{"status": 200, "headers": {"a": 1}}',
        b"[" * 100000,
    ],
)
def test_malformed_response_head_raises_the_typed_error(payload: bytes) -> None:
    with pytest.raises(ApiPayloadError):
        ApiResponseHead.parse(payload)


def _server_frame(payload: bytes) -> ApiResponseHead | None:
    """A head read the way an out-of-process consumer reads it: frame, then payload."""
    return parse_api_server_frame(ApiFrame(int(ApiMessageType.RES_HEAD), 1, payload))


@pytest.mark.parametrize(
    "parse",
    [ApiResponseHead.parse, _server_frame],
    ids=["response_head", "server_frame"],
)
def test_response_head_headers_is_required_by_the_wire_contract(
    parse: Callable[[bytes], ApiResponseHead | None],
) -> None:
    """The wire contract marks ``headers`` required — the browser decoder
    rejects a head without it — so through either consumer parser a missing
    key is the typed contract error, not an empty mapping. A present-but-empty
    dict stays valid."""
    with pytest.raises(ApiPayloadError, match="'headers' must be an object"):
        parse(b'{"status": 204}')
    with pytest.raises(ApiPayloadError, match="'headers' must be an object"):
        parse(b'{"status": 204, "headers": null}')
    assert parse(b'{"status": 204, "headers": {}}') == ApiResponseHead(204, {})


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not json",
        b'"text"',
        b'{"code": "busy"}',
        b'{"message": "m"}',
        b'{"code": 7, "message": "m"}',
        b'{"code": "busy", "message": null}',
    ],
)
def test_malformed_error_frame_raises_the_typed_error(payload: bytes) -> None:
    with pytest.raises(ApiPayloadError):
        ApiErrorFrame.parse(payload)


def test_the_typed_error_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        parse_api_server_frame(ApiFrame(int(ApiMessageType.RES_HEAD), 1, b"x"))


def test_unknown_extra_fields_are_ignored() -> None:
    head = ApiResponseHead.parse(
        b'{"status": 200, "headers": {"a": "b"}, "added_later": [1, 2]}'
    )
    assert head == ApiResponseHead(200, {"a": "b"})
    err = ApiErrorFrame.parse(b'{"code": "busy", "message": "m", "retry_ms": 5}')
    assert err == ApiErrorFrame("busy", "m")


def test_an_error_code_added_later_still_parses() -> None:
    err = ApiErrorFrame.parse(b'{"code": "a_new_code", "message": "m"}')
    assert err.code == "a_new_code"


def test_other_and_unknown_frame_types_are_skipped() -> None:
    doc = _golden().documents["api_ok"]
    for golden_frame in doc.reply[1:]:  # RES_BODY, END
        assert parse_api_server_frame(_wire(golden_frame)) is None
    assert parse_api_server_frame(ApiFrame(0x7F, 3, b"\x00 not json")) is None
