"""Tests for Generator B: build_hamlib_template_from_capabilities (MOR-201a).

Plain pytest functions — mirrors the style of test_generator.py.
All cases use frozensets of tokens/capabilities, never HamlibCaps.
"""

from __future__ import annotations

from rigplane.validation.registry import build_hamlib_template_from_capabilities
from rigplane.validation.schema import (
    CapabilityDeclaration,
    ValidationLevel,
    validate_template_dict,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _entry(tpl, check_id):
    """Return the CapabilityDeclarationEntry with the given check_id, or raise."""
    by_id = {e.check_id: e for e in tpl.entries}
    assert check_id in by_id, f"check_id {check_id!r} not found in template"
    return by_id[check_id]


def _build(caps=frozenset(), tokens=frozenset()):
    return build_hamlib_template_from_capabilities(
        frozenset(caps),
        frozenset(tokens),
        model="IC-TEST",
        profile_id="ic_test",
    )


# ---------------------------------------------------------------------------
# Core token-gating tests
# ---------------------------------------------------------------------------


def test_token_present_and_declared_supported():
    """caps={'rf_gain'}, tokens={'RF'} → rf_gain.set == SUPPORTED."""
    tpl = _build(caps={"rf_gain"}, tokens={"RF"})
    entry = _entry(tpl, "rf_gain.set")
    assert entry.declaration == CapabilityDeclaration.SUPPORTED


def test_token_none_unsupported():
    """agc.hamlib_token is None → agc.set == UNSUPPORTED_PENDING_EVIDENCE regardless of caps."""
    tpl = _build(caps={"agc"}, tokens=frozenset())
    entry = _entry(tpl, "agc.set")
    assert entry.declaration == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE


def test_token_absent_from_available_unsupported():
    """caps={'rf_gain'} but 'RF' not in tokens → rf_gain.set == UNSUPPORTED_PENDING_EVIDENCE."""
    tpl = _build(caps={"rf_gain"}, tokens=frozenset())
    entry = _entry(tpl, "rf_gain.set")
    assert entry.declaration == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE


def test_declared_cap_with_missing_hamlib_token_stays_pending():
    """Declared cap without Hamlib evidence remains pending, not firm unsupported."""
    tpl = _build(caps={"rf_gain"}, tokens={"AF"})
    entry = _entry(tpl, "rf_gain.set")
    assert entry.declaration == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE


def test_undeclared_but_token_present_unsupported():
    """'RF' token present but rf_gain not in caps → rf_gain.set == UNSUPPORTED."""
    tpl = _build(caps=frozenset(), tokens={"RF"})
    entry = _entry(tpl, "rf_gain.set")
    assert entry.declaration == CapabilityDeclaration.UNSUPPORTED


# ---------------------------------------------------------------------------
# Structural (capability == '') checks gated by their hamlib_token
# ---------------------------------------------------------------------------


def test_structural_freq_mode_gated_by_token():
    """freq.write and mode.set are SUPPORTED only when their tokens are present.

    discovery.identify and freq.reverse_sync have hamlib_token=None so they
    are always UNSUPPORTED_PENDING_EVIDENCE.
    """
    # tokens present → structural freq/mode SUPPORTED
    tpl_with = _build(tokens={"f", "m"})
    assert _entry(tpl_with, "freq.write").declaration == CapabilityDeclaration.SUPPORTED
    assert _entry(tpl_with, "mode.set").declaration == CapabilityDeclaration.SUPPORTED

    # no tokens → freq.write and mode.set UNSUPPORTED_PENDING_EVIDENCE
    tpl_without = _build(tokens=frozenset())
    assert (
        _entry(tpl_without, "freq.write").declaration
        == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE
    )
    assert (
        _entry(tpl_without, "mode.set").declaration
        == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE
    )

    # discovery.identify and freq.reverse_sync are always UNSUPPORTED_PENDING_EVIDENCE
    # because hamlib_token is None for both
    for check_id in ("discovery.identify", "freq.reverse_sync"):
        assert (
            _entry(tpl_with, check_id).declaration
            == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE
        ), f"{check_id} should be UNSUPPORTED_PENDING_EVIDENCE (hamlib_token=None)"


# ---------------------------------------------------------------------------
# TX-adjacent checks
# ---------------------------------------------------------------------------


def test_tx_manual_required_tuner_unsupported_both_tx_adjacent():
    """tx.ptt: token='t' present + cap declared → MANUAL_REQUIRED, tx_adjacent=True.
    tuner.tune: hamlib_token=None → UNSUPPORTED_PENDING_EVIDENCE, tx_adjacent=True.
    """
    tpl = _build(caps={"tx", "tuner"}, tokens={"t"})
    ptt = _entry(tpl, "tx.ptt")
    tuner = _entry(tpl, "tuner.tune")

    assert ptt.declaration == CapabilityDeclaration.MANUAL_REQUIRED
    assert ptt.tx_adjacent is True

    assert tuner.declaration == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE
    assert tuner.tx_adjacent is True


# ---------------------------------------------------------------------------
# MANUAL checks
# ---------------------------------------------------------------------------


def test_manual_audio_scope():
    """audio.rx and scope.capture have hamlib_token=None → always UNSUPPORTED_PENDING_EVIDENCE."""
    tpl = _build(caps={"audio", "scope"}, tokens=frozenset())
    assert (
        _entry(tpl, "audio.rx").declaration
        == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE
    )
    assert (
        _entry(tpl, "scope.capture").declaration
        == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE
    )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_template_validates():
    """build_hamlib_template_from_capabilities returns a schema-valid MatrixTemplate."""
    tpl = _build(
        caps={"rf_gain", "af_level", "preamp", "attenuator", "tx"},
        tokens={"RF", "AF", "PREAMP", "ATT", "t", "f", "m"},
    )
    # Must not raise SchemaValidationError
    validate_template_dict(tpl.to_dict())


# ---------------------------------------------------------------------------
# Sort / structural invariants
# ---------------------------------------------------------------------------


def test_entries_sorted_by_level():
    """Level sequence is non-decreasing; presence entries (level 0) sort first."""
    # NOTE (MOR-643): "split" gained a registry check, so "scan" provides the
    # check-less capability that yields the level-0 presence entry.
    tpl = _build(caps={"scan", "rf_gain"}, tokens={"RF"})
    levels = [int(e.level) for e in tpl.entries]
    assert levels == sorted(levels), "Entries are not sorted by level"
    # 'scan' has no registry check → presence entry at STATIC_PROFILE (0)
    first = tpl.entries[0]
    assert first.level == ValidationLevel.STATIC_PROFILE


def test_presence_entries():
    """A declared capability with no registry check gets a <cap>.presence entry."""
    tpl = _build(caps={"scan"}, tokens=frozenset())
    entry = _entry(tpl, "scan.presence")
    assert entry.level == ValidationLevel.STATIC_PROFILE
    assert entry.declaration == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE


def test_check_ids_unique():
    """No duplicate check_ids in the generated template."""
    tpl = _build(
        caps={"rf_gain", "scope", "tuner", "tx", "split"},
        tokens={"RF", "t"},
    )
    ids = [e.check_id for e in tpl.entries]
    assert len(ids) == len(set(ids)), "Duplicate check_ids found"
