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
