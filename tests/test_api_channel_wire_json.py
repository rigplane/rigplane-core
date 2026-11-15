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
    api_channel_wire_definition,
)
from rigplane.web.channel_attach import (
    ATTACH_ACK_VERSION,
    ChannelAttachedAck,
    ClientChannelParams,
)

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
    # request_head: the real parser, full and minimal shapes.
    full = {
        "method": "GET",
        "path": "/api/v1/info",
        "query": {"a": ["1"]},
        "headers": {"x": "y"},
    }
    assert ApiRequestHead.parse(json.dumps(full).encode()).path == "/api/v1/info"
    _conform("request_head", full)
    _conform("request_head", {"method": "GET", "path": "/api/v1/info"})
    with pytest.raises(ValueError):  # required method
        ApiRequestHead.parse(b'{"path": "/api/v1/info"}')
    with pytest.raises(ValueError):  # required origin-form path
        ApiRequestHead.parse(b'{"method": "GET", "path": "api"}')
    # response_head / error / attach_ack: the real encoders' own output.
    _conform("response_head", json.loads(ApiResponseHead(200, {"a": "b"}).encode()))
    _conform(
        "error", json.loads(ApiErrorFrame("stale_id", "request id reused").encode())
    )
    ack = json.loads(ChannelAttachedAck("scope").encode())
    _conform("attach_ack", ack)
    assert ack == {"type": "attached", "v": ATTACH_ACK_VERSION, "channel": "scope"}
    # attach_frame: keyed channels demand a key; the keyless "api"
    # channel also accepts a null or omitted one. The exported row must
    # say exactly that.
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
    keyless = {"type": "attach", "controller_key": None}
    omitted = {"type": "attach"}
    for frame in (keyless, omitted):
        ClientChannelParams.from_attach_frame(
            json.dumps(frame).encode(), require_key=False
        )
        _conform("attach_frame", frame)
        with pytest.raises(ValueError):  # keyed channels refuse keyless
            ClientChannelParams.from_attach_frame(json.dumps(frame).encode())
    key_row = next(
        row for row in payloads["attach_frame"].fields if row[0] == "controller_key"
    )
    assert (key_row[1], key_row[2]) == ("string | null", False), key_row


def test_committed_golden_documents_validate_against_payload_contracts() -> None:
    """Every committed golden document is a concrete frame its payload
    contract describes (text documents re-parse from their own text)."""
    golden = json.loads(_GOLDEN_JSON.read_text(encoding="utf-8"))
    for name, doc in golden["documents"].items():
        if name.startswith("attach_ack_"):
            _conform("attach_ack", doc["decoded"])
        elif name.startswith("attach_"):
            _conform("attach_frame", doc["decoded"])
        elif name == "api_errors":
            for code, trigger in doc["triggers"].items():
                assert isinstance(trigger, str) and code in (
                    api_channel_wire_definition().payloads["error"].codes
                ), code
            for frame in doc["frames"]:
                _conform("error", frame["payload"])
        else:
            for frame in doc["request"] + doc["reply"]:
                if frame["type"] == "REQ_HEAD":
                    _conform("request_head", frame["payload"])
                elif frame["type"] == "RES_HEAD":
                    _conform("response_head", frame["payload"])
        if "text" in doc:
            assert doc["decoded"] == json.loads(doc["text"]), name
