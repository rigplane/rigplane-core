"""The committed browser copy of the ``api`` wire contract cannot drift.

``api-channel-wire.json`` is the single source the browser ``api``
client imports; this fails when it stops matching the definition, when
a payload contract stops conforming to the real parsers and encoders,
or when a committed golden document violates its payload contract.
Regenerate from the repo root:

    uv run python -c "import dataclasses, json; from rigplane.web.api_channel import api_channel_wire_definition as d; print(json.dumps(dataclasses.asdict(d())))" \
      > frontend/src/lib/transport/api-channel-wire.json
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from rigplane.web.api_channel import (
    ApiErrorFrame,
    ApiRequestHead,
    ApiResponseHead,
    GoldenErrorsDocument,
    GoldenExchangeDocument,
    GoldenTextDocument,
    api_channel_golden_documents_from_json,
    api_channel_wire_definition,
)
from rigplane.web.channel_attach import (
    ATTACH_ACK_VERSION,
    ChannelAttachedAck,
    ClientChannelParams,
)
from rigplane.web.handlers.audio import audio_tx_format_message
from rigplane.web.handlers.control import control_hello_message

_WIRE_JSON = (
    Path(__file__).resolve().parents[1]
    / "frontend/src/lib/transport/api-channel-wire.json"
)
_GOLDEN_JSON = (
    Path(__file__).resolve().parents[1]
    / "frontend/src/lib/transport/api-channel-golden.json"
)


def test_committed_wire_json_matches_api_channel_definition() -> None:
    current = json.loads(json.dumps(dataclasses.asdict(api_channel_wire_definition())))
    committed = json.loads(_WIRE_JSON.read_text(encoding="utf-8"))
    assert committed == current, "api-channel-wire.json drifted; regenerate it"


def _conform(payload: str, obj: Any) -> None:
    """One decoded payload must satisfy its exported contract rows."""
    rows = api_channel_wire_definition().payloads[payload].fields
    assert set(obj) <= {row[0] for row in rows}, (payload, sorted(obj))
    for name, wire_type, required, _note in rows:
        value = obj.get(name, ...)
        assert not required or value is not ..., (payload, name, required)
        if value is ...:
            continue
        assert (
            (wire_type == "string | null" and (value is None or isinstance(value, str)))
            or (wire_type.startswith('"') and value == wire_type.strip('"'))
            or (wire_type == "integer" and type(value) is int)
            or (wire_type == "boolean" and isinstance(value, bool))
            or (
                wire_type == "array of strings"
                and isinstance(value, list)
                and all(isinstance(item, str) for item in value)
            )
            or (wire_type.startswith("object") and isinstance(value, dict))
            or isinstance(value, str)
        ), (payload, name, wire_type, value)


def test_payload_contracts_conform_to_real_parsers_and_encoders() -> None:
    """The exported rows describe what the producer types really accept
    and emit — keyed and keyless attach variants included."""
    payloads = api_channel_wire_definition().payloads
    assert set(payloads) == {
        "request_head",
        "response_head",
        "error",
        "attach_frame",
        "attach_ack",
        "control_hello",
    }, sorted(payloads)
    assert payloads["error"].codes == (
        "bad_version",
        "malformed",
        "stale_id",
        "too_large",
        "busy",
        "timeout",
        "internal",
    )
    # The real encoders' own output conforms: response, error, ack.
    _conform("response_head", json.loads(ApiResponseHead(200, {"a": "b"}).encode()))
    _conform("error", json.loads(ApiErrorFrame("stale_id", "m").encode()))
    ack = json.loads(ChannelAttachedAck("scope").encode())
    _conform("attach_ack", ack)
    assert ack == {"type": "attached", "v": ATTACH_ACK_VERSION, "channel": "scope"}
    # The real parser accepts exactly the documented request_head shape.
    _conform("request_head", {"method": "GET", "path": "/api/v1/info"})
    assert ApiRequestHead.parse(b'{"method": "GET", "path": "/"}').path == "/"
    with pytest.raises(ValueError):  # the required origin-form path
        ApiRequestHead.parse(b'{"method": "GET", "path": "api"}')
    # attach_frame: keyed channels demand a key; the keyless "api"
    # channel also accepts null or omitted — the row must say that.
    keyed = {
        "type": "attach",
        "controller_key": "a-session-key",
        "state_delta": 2,
        "role": "primary",
    }
    for require_key in (True, False):
        assert ClientChannelParams.from_attach_frame(
            json.dumps(keyed).encode(), require_key=require_key
        ) == ClientChannelParams("a-session-key", "primary", 2)
    _conform("attach_frame", keyed)
    for keyless in ({"type": "attach", "controller_key": None}, {"type": "attach"}):
        ClientChannelParams.from_attach_frame(
            json.dumps(keyless).encode(), require_key=False
        )
        _conform("attach_frame", keyless)
        with pytest.raises(ValueError):  # keyed channels refuse keyless
            ClientChannelParams.from_attach_frame(json.dumps(keyless).encode())
    key_row = next(
        row for row in payloads["attach_frame"].fields if row[0] == "controller_key"
    )
    assert (key_row[1], key_row[2]) == ("string | null", False), key_row
    # control_hello: the control handler's own typed producer conforms to
    # the exported rows — the declaration order is the wire key order and
    # the builder owns the capability sort — and a hello missing a
    # required field fails them (MOR-4489).
    hello = control_hello_message(
        version="3.1.2",
        radio="IC-7610",
        connected=True,
        radio_ready=True,
        capabilities={"tx", "audio"},
    )
    _conform("control_hello", hello)
    assert list(hello) == [
        "type",
        "proto",
        "server",
        "version",
        "radio",
        "connected",
        "radio_ready",
        "capabilities",
    ], hello
    assert hello["capabilities"] == ["audio", "tx"]
    assert hello["type"] == "hello" and hello["proto"] == 1
    assert hello["server"] == "rigplane"
    with pytest.raises(AssertionError):
        _conform("control_hello", {k: v for k, v in hello.items() if k != "version"})


def test_committed_golden_documents_validate_against_payload_contracts() -> None:
    """Every committed golden document is a concrete frame its payload
    contract describes, read through the producer's typed loader (text
    documents re-parse from their own text)."""
    golden = api_channel_golden_documents_from_json(
        json.loads(_GOLDEN_JSON.read_text(encoding="utf-8"))
    )
    for name, doc in golden.documents.items():
        match doc:
            case GoldenTextDocument(decoded={"type": "attach"}):
                _conform("attach_frame", doc.decoded)
            case GoldenTextDocument(decoded={"type": "attached"}):
                _conform("attach_ack", doc.decoded)
            case GoldenTextDocument(decoded={"type": "audio_tx_format"}):
                # Not an api-channel payload: the audio channel's TX
                # format ack, pinned to the audio handler's own typed
                # producer — the codec follows opus_decode, and no key
                # may appear or disappear beside the producer's own.
                assert type(doc.decoded["sample_rate"]) is int, (name, doc.decoded)
                assert doc.decoded == audio_tx_format_message(
                    doc.decoded["opus_decode"], doc.decoded["sample_rate"]
                ), (name, doc.decoded)
            case GoldenTextDocument(decoded={"type": "hello"}):
                # The control channel's hello: a payload contract of its
                # own, built by the control handler's typed producer —
                # the keys are exactly the producer's, in its order.
                _conform("control_hello", doc.decoded)
            case GoldenTextDocument():
                raise AssertionError(f"{name}: unknown text document {doc.decoded!r}")
            case GoldenErrorsDocument():
                for code, trigger in doc.triggers.items():
                    assert isinstance(trigger, str) and code in (
                        api_channel_wire_definition().payloads["error"].codes
                    ), code
                for frame in doc.frames:
                    _conform("error", frame.payload)
            case GoldenExchangeDocument():
                for frame in doc.request + doc.reply:
                    match frame.type:
                        case "REQ_HEAD":
                            _conform("request_head", frame.payload)
                        case "RES_HEAD":
                            _conform("response_head", frame.payload)
            case _:
                raise AssertionError(f"{name}: unknown golden document {doc!r}")
        if isinstance(doc, GoldenTextDocument):
            assert doc.decoded == json.loads(doc.text), name
