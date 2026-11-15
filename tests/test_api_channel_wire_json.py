"""The committed browser copy of the ``api`` wire contract cannot drift.

``frontend/src/lib/transport/api-channel-wire.json`` is the single source
the browser ``api`` client imports (no re-typed constants). This test
fails when the committed JSON stops matching
:func:`rigplane.web.api_channel.api_channel_wire_definition`.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from rigplane.web.api_channel import api_channel_wire_definition

_WIRE_JSON = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "lib"
    / "transport"
    / "api-channel-wire.json"
)

#: How to regenerate (from the repo root):
#:   uv run python -c "import dataclasses, json; from rigplane.web.api_channel import api_channel_wire_definition as d; print(json.dumps(dataclasses.asdict(d())))" \
#:     > frontend/src/lib/transport/api-channel-wire.json
_REGENERATE = (
    "regenerate from the repo root: uv run python -c "
    '"import dataclasses, json; from rigplane.web.api_channel import '
    'api_channel_wire_definition as d; '
    "print(json.dumps(dataclasses.asdict(d())))\" "
    "> frontend/src/lib/transport/api-channel-wire.json"
)


def test_committed_wire_json_matches_api_channel_definition() -> None:
    current = dataclasses.asdict(api_channel_wire_definition())
    committed = json.loads(_WIRE_JSON.read_text(encoding="utf-8"))
    assert committed == current, (
        "frontend/src/lib/transport/api-channel-wire.json drifted from "
        f"api_channel_wire_definition(); {_REGENERATE}"
    )
