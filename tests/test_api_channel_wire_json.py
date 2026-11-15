"""The committed browser copy of the ``api`` wire contract cannot drift.

``api-channel-wire.json`` is the single source the browser ``api``
client imports; this fails when it stops matching the definition.
Regenerate from the repo root:

    uv run python -c "import dataclasses, json; from rigplane.web.api_channel import api_channel_wire_definition as d; print(json.dumps(dataclasses.asdict(d())))" \
      > frontend/src/lib/transport/api-channel-wire.json
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from rigplane.web.api_channel import api_channel_wire_definition

_WIRE_JSON = (
    Path(__file__).resolve().parents[1]
    / "frontend/src/lib/transport/api-channel-wire.json"
)


def test_committed_wire_json_matches_api_channel_definition() -> None:
    current = json.loads(json.dumps(dataclasses.asdict(api_channel_wire_definition())))
    committed = json.loads(_WIRE_JSON.read_text(encoding="utf-8"))
    assert committed == current, "api-channel-wire.json drifted; regenerate it"


def test_wire_json_carries_every_producer_payload_contract() -> None:
    """The committed JSON also owns the JSON payload contracts.

    The binary layout alone does not pin what the frames carry: the
    request/response/error payloads and the attach frame/ack are typed
    contracts exported next to it, versioned, in a stable key order.
    """
    committed = json.loads(_WIRE_JSON.read_text(encoding="utf-8"))
    payloads = committed["payloads"]
    assert set(payloads) == {
        "request_head",
        "response_head",
        "error",
        "attach_frame",
        "attach_ack",
    }, sorted(payloads)
    for name, contract in payloads.items():
        assert contract["version"] == 1, name
        assert contract["carried_by"], name
        assert contract["fields"], name
        for field in contract["fields"]:
            assert set(field) == {"name", "type", "required", "note"}, (name, field)
    # The failure codes the server may emit, from the producer's own list.
    codes = payloads["error"]["codes"]
    for code in (
        "bad_version",
        "malformed",
        "stale_id",
        "too_large",
        "busy",
        "timeout",
        "internal",
    ):
        assert code in codes, code
