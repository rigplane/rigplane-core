"""Verify the golden frontend field-status fixture (MOR-429).

The frontend availability resolver (`$lib/state/field-status`) must key off the
exact public paths the backend emits in `field_status`. To stop those two sides
from drifting, a committed JSON fixture holds the real public-state projection
for an *empty* StateStore snapshot; this test asserts the live projection still
matches it, and that the leaves the v2 panels/LCD/toolbar gate on are present
and `missing`. The frontend imports the same fixture (`field-status` spec) so
any change to the backend key shape is caught on both sides.

These tests are read-only: they never rewrite the fixture, so a backend key
change fails CI instead of being silently absorbed. Regenerate deliberately
after an intentional backend change with ``_REGEN_COMMAND`` below and commit
the diff.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from rigplane.core.state_store import StateSnapshot
from rigplane.web.runtime_helpers import build_public_state_payload_from_snapshot

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_PATH = (
    _ROOT
    / "frontend"
    / "src"
    / "lib"
    / "state"
    / "__tests__"
    / "fixtures"
    / "empty-store-field-status.json"
)
_GENERATOR = "scripts/gen_field_status_fixture.py"
_REGEN_COMMAND = f"uv run python {_GENERATOR}"

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

    assert _FIXTURE_PATH.exists(), (
        f"Golden fixture is missing; generate it: {_REGEN_COMMAND}"
    )
    # The on-disk fixture the frontend imports must equal the live projection.
    committed = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert committed == field_status, (
        f"Backend field_status drifted from the committed frontend fixture. "
        f"If the change is intentional, regenerate and commit it: {_REGEN_COMMAND}"
    )


def test_fixture_generator_reports_no_drift() -> None:
    """``--check`` agrees the committed fixture is byte-for-byte canonical."""
    result = subprocess.run(
        [sys.executable, _GENERATOR, "--check"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"{_GENERATOR} --check reported drift; regenerate and commit it: "
        f"{_REGEN_COMMAND}\n{result.stdout}{result.stderr}"
    )


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
