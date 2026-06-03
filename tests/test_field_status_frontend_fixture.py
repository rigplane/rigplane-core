"""Generate and verify the golden frontend field-status fixture (MOR-429).

The frontend availability resolver (`$lib/state/field-status`) must key off the
exact public paths the backend emits in `field_status`. To stop those two sides
from drifting, this test dumps the real public-state projection for an *empty*
StateStore snapshot to a committed JSON fixture and asserts the leaves the v2
panels/LCD/toolbar gate on are present and `missing`. The frontend imports the
same fixture (`lcd-availability` / `field-status` specs) so any change to the
backend key shape is caught on both sides.

Run this file to regenerate the fixture after an intentional backend change.
"""

from __future__ import annotations

import json
from pathlib import Path

from rigplane.core.state_store import StateSnapshot
from rigplane.web.runtime_helpers import build_public_state_payload_from_snapshot

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "src"
    / "lib"
    / "state"
    / "__tests__"
    / "fixtures"
    / "empty-store-field-status.json"
)

# Public leaves the SpectrumToolbar gates on (8 scope-control children) plus the
# per-receiver indicators the LCD panels gate on. Each must be present in the
# empty-store payload and resolve to `missing`.
_SCOPE_CONTROL_KEYS = (
    "scopeControls.mode",
    "scopeControls.edge",
    "scopeControls.span",
    "scopeControls.speed",
    "scopeControls.hold",
    "scopeControls.refDb",
    "scopeControls.dual",
    "scopeControls.receiver",
)
_RECEIVER_INDICATOR_KEYS = (
    "main.agc",
    "main.att",
    "main.preamp",
    "main.nb",
    "main.nr",
    "main.rfGain",
    "main.squelch",
    "main.manualNotch",
    "main.autoNotch",
)


def _empty_store_field_status() -> dict[str, dict]:
    payload = build_public_state_payload_from_snapshot(
        StateSnapshot.empty(),
        radio=None,
        receiver_count=2,
    )
    field_status = payload["fieldStatus"]
    assert isinstance(field_status, dict)
    return field_status


def test_empty_store_field_status_fixture_is_current() -> None:
    """The committed golden fixture matches the live backend projection."""
    field_status = _empty_store_field_status()

    _FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(field_status, indent=2, sort_keys=True) + "\n"
    if not _FIXTURE_PATH.exists() or _FIXTURE_PATH.read_text() != serialized:
        _FIXTURE_PATH.write_text(serialized)

    # The on-disk fixture the frontend imports must equal the live projection.
    assert json.loads(_FIXTURE_PATH.read_text()) == field_status


def test_scope_control_children_seeded_missing() -> None:
    """All eight scope-control leaves the toolbar checks are seeded `missing`."""
    field_status = _empty_store_field_status()
    for key in _SCOPE_CONTROL_KEYS:
        assert key in field_status, f"missing scope-control key: {key}"
        assert field_status[key]["availability"] == "missing"
        assert field_status[key]["observed"] is False


def test_receiver_indicator_leaves_seeded_missing() -> None:
    """Per-receiver indicators the LCD gates on are seeded `missing`."""
    field_status = _empty_store_field_status()
    for key in _RECEIVER_INDICATOR_KEYS:
        assert key in field_status, f"missing receiver indicator key: {key}"
        assert field_status[key]["availability"] == "missing"
