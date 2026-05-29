"""Tests for the profile-driven template generator (MOR-198).

Plain pytest functions — no class wrapper needed.
Mirrors the style of tests/validation/test_registry.py.
"""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from rigplane.cli import _validate
from rigplane.profiles import get_radio_profile
from rigplane.validation.registry import build_template_from_capabilities
from rigplane.validation.schema import (
    CapabilityDeclaration,
    ValidationLevel,
    validate_template_dict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURE_TEMPLATE = (
    Path(__file__).parent.parent / "fixtures" / "validation_template_ic7300.json"
)


def _make_args(**kwargs) -> argparse.Namespace:
    """Build a Namespace with safe defaults for _validate.run()."""
    defaults = dict(
        template=None,
        model=None,
        hardware=False,
        allow_hardware=False,
        tx_allowed=False,
        tuner_allowed=False,
        read_only=False,
        provider="native",
        compare=None,
        operator_id=None,
        output=None,
        json=True,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# Generator unit tests
# ---------------------------------------------------------------------------


def test_generated_template_validates():
    """build_template_from_capabilities returns a schema-valid MatrixTemplate."""
    tpl = build_template_from_capabilities(
        frozenset({"rf_gain", "scope"}),
        model="IC-7300",
        profile_id="ic7300",
    )
    # validate_template_dict must not raise SchemaValidationError
    validate_template_dict(tpl.to_dict())


def test_structural_always_supported():
    """Structural checks (capability == '') are always SUPPORTED regardless of caps."""
    tpl = build_template_from_capabilities(
        frozenset(),
        model="IC-7300",
        profile_id="ic7300",
    )
    structural_ids = {
        "discovery.identify",
        "freq.write",
        "freq.reverse_sync",
        "mode.set",
    }
    entries_by_id = {e.check_id: e for e in tpl.entries}
    for cid in structural_ids:
        assert cid in entries_by_id, f"Structural check {cid!r} missing from template"
        entry = entries_by_id[cid]
        assert entry.capability == "", (
            f"{cid!r}: expected capability='', got {entry.capability!r}"
        )
        assert entry.declaration == CapabilityDeclaration.SUPPORTED, (
            f"{cid!r}: expected SUPPORTED, got {entry.declaration!r}"
        )


def test_declared_functional_supported():
    """A declared capability maps to SUPPORTED."""
    tpl = build_template_from_capabilities(
        frozenset({"rf_gain"}),
        model="IC-7300",
        profile_id="ic7300",
    )
    entries_by_id = {e.check_id: e for e in tpl.entries}
    assert "rf_gain.set" in entries_by_id
    assert entries_by_id["rf_gain.set"].declaration == CapabilityDeclaration.SUPPORTED


def test_undeclared_functional_pending():
    """A functional check whose capability is absent maps to UNSUPPORTED_PENDING_EVIDENCE."""
    tpl = build_template_from_capabilities(
        frozenset(),
        model="IC-7300",
        profile_id="ic7300",
    )
    entries_by_id = {e.check_id: e for e in tpl.entries}
    assert "rf_gain.set" in entries_by_id
    assert (
        entries_by_id["rf_gain.set"].declaration
        == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE
    )


def test_manual_kind_manual_required():
    """MANUAL checks map to CapabilityDeclaration.MANUAL_REQUIRED when capability declared.

    Note: the generated baseline intentionally differs from the shipped ic7300 template
    (where scope=supported). Override reconciliation is handled in MOR-202.
    """
    tpl = build_template_from_capabilities(
        frozenset({"audio", "scope"}),
        model="IC-7300",
        profile_id="ic7300",
    )
    entries_by_id = {e.check_id: e for e in tpl.entries}
    assert (
        entries_by_id["audio.rx"].declaration == CapabilityDeclaration.MANUAL_REQUIRED
    )
    assert (
        entries_by_id["scope.capture"].declaration
        == CapabilityDeclaration.MANUAL_REQUIRED
    )


def test_tx_adjacent_blocked():
    """TX_ADJACENT_BLOCKED checks map to MANUAL_REQUIRED and preserve tx_adjacent=True."""
    tpl = build_template_from_capabilities(
        frozenset({"tuner", "tx"}),
        model="IC-7300",
        profile_id="ic7300",
    )
    entries_by_id = {e.check_id: e for e in tpl.entries}
    tuner = entries_by_id["tuner.tune"]
    ptt = entries_by_id["tx.ptt"]
    assert tuner.declaration == CapabilityDeclaration.MANUAL_REQUIRED
    assert tuner.tx_adjacent is True
    assert ptt.declaration == CapabilityDeclaration.MANUAL_REQUIRED
    assert ptt.tx_adjacent is True


def test_presence_entries():
    """Capabilities with no registry check get a <cap>.presence entry at STATIC_PROFILE."""
    tpl = build_template_from_capabilities(
        frozenset({"split", "vox", "cw"}),
        model="IC-7300",
        profile_id="ic7300",
    )
    entries_by_id = {e.check_id: e for e in tpl.entries}
    for cap in ("split", "vox", "cw"):
        cid = f"{cap}.presence"
        assert cid in entries_by_id, f"Expected {cid!r} in template entries"
        entry = entries_by_id[cid]
        assert entry.level == ValidationLevel.STATIC_PROFILE
        assert entry.declaration == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE


def test_entries_sorted_by_level():
    """Level sequence is non-decreasing; presence entries (level 0) sort first."""
    tpl = build_template_from_capabilities(
        frozenset({"split", "rf_gain"}),
        model="IC-7300",
        profile_id="ic7300",
    )
    levels = [int(e.level) for e in tpl.entries]
    assert levels == sorted(levels), "Entries are not sorted by level"
    # Presence entries are level 0 — should appear before DISCOVERY (level 1)
    first = tpl.entries[0]
    assert first.level == ValidationLevel.STATIC_PROFILE


def test_check_ids_unique():
    """No duplicate check_ids in the generated template."""
    tpl = build_template_from_capabilities(
        frozenset({"rf_gain", "scope", "tuner", "tx"}),
        model="IC-7300",
        profile_id="ic7300",
    )
    ids = [e.check_id for e in tpl.entries]
    assert len(ids) == len(set(ids)), "Duplicate check_ids found"


def test_probe_unused():
    """probe parameter does not affect output (v1 — probe is accepted but unused)."""
    caps = frozenset({"rf_gain", "scope"})
    tpl_none = build_template_from_capabilities(
        caps, model="IC-7300", profile_id="ic7300", probe=None
    )
    tpl_probe = build_template_from_capabilities(
        caps,
        model="IC-7300",
        profile_id="ic7300",
        probe=lambda _: True,
    )
    assert tpl_none.to_dict() == tpl_probe.to_dict()


def test_realistic_ic7300():
    """End-to-end: IC-7300 profile builds a schema-valid template."""
    profile = get_radio_profile("IC-7300")
    tpl = build_template_from_capabilities(
        profile.capabilities,
        model=profile.model,
        profile_id=profile.id,
    )
    # Schema validation must pass
    validate_template_dict(tpl.to_dict())
    # filter_width.set declaration depends on whether filter_width is in capabilities
    entries_by_id = {e.check_id: e for e in tpl.entries}
    if "filter_width" in profile.capabilities:
        assert entries_by_id["filter_width.set"].declaration in (
            CapabilityDeclaration.SUPPORTED,
            CapabilityDeclaration.MANUAL_REQUIRED,
        )
    else:
        assert (
            entries_by_id["filter_width.set"].declaration
            == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE
        )


# ---------------------------------------------------------------------------
# CLI tests (dry-run, no hardware)
# ---------------------------------------------------------------------------


def test_cli_template_optional_in_parser():
    """--template must NOT be required (i.e. parse_args(['validate']) must not raise)."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    _validate.add_subparser(sub)
    # parse_args(['validate']) must not raise SystemExit
    args = parser.parse_args(["validate"])
    # If we got here, --template was not required
    assert args.template is None


def test_cli_generates_from_model():
    """Namespace(model='IC-7300', template=None) → rc==0, stdout is valid JSON with 'radio'."""
    args = _make_args(model="IC-7300")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _validate.run(args)
    assert rc == 0, f"Expected rc=0, got {rc}"
    payload = json.loads(buf.getvalue())
    assert "radio" in payload, (
        f"'radio' key missing from artifact: {list(payload.keys())}"
    )


def test_cli_error_no_template_no_model():
    """Both template and model absent → rc==2."""
    args = _make_args(template=None, model=None)
    rc = _validate.run(args)
    assert rc == 2


def test_cli_error_unknown_model():
    """Unknown model → rc==2."""
    args = _make_args(template=None, model="NOPE-9000")
    rc = _validate.run(args)
    assert rc == 2


def test_cli_template_path_still_works():
    """Existing --template path still works (regression guard)."""
    if not _FIXTURE_TEMPLATE.exists():
        pytest.skip(f"Fixture {_FIXTURE_TEMPLATE} not found")
    args = _make_args(template=str(_FIXTURE_TEMPLATE))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _validate.run(args)
    assert rc == 0
