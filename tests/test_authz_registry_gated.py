"""Defense-in-depth: TX/tuner authorization derives from the IMMUTABLE registry.

These tests prove that a template whose ``tx.ptt`` / ``tuner.tune`` entry has
been mutated to ``tx_adjacent=False`` (e.g. a hand-edited template) still cannot
BYPASS the operator authorization gate, because the gate consults the registry's
``CheckKind.TX_ADJACENT_BLOCKED`` safety class — which is not part of the
mutable template — rather than trusting ``entry.tx_adjacent`` alone. They also
verify that a mutated ``entry.capability`` cannot dodge the tuner/tx split.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from rigplane.core.radio_protocol import Radio
from rigplane.core.radio_state import RadioState
from rigplane.validation.hardware import execute_hardware_checks
from rigplane.validation.runner import (
    _is_authorized,
    _is_safety_gated,
    dry_run_results,
)
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckStatus,
    MatrixTemplate,
    OperatorSafetyBlock,
    RadioTarget,
    ValidationLevel,
)


def _entry(
    *,
    check_id: str,
    capability: str,
    tx_adjacent: bool,
    declaration: CapabilityDeclaration = CapabilityDeclaration.SUPPORTED,
) -> CapabilityDeclarationEntry:
    return CapabilityDeclarationEntry(
        check_id=check_id,
        capability=capability,
        level=ValidationLevel.STRESS_RECOVERY,
        declaration=declaration,
        summary="single",
        tx_adjacent=tx_adjacent,
    )


def _template(entry: CapabilityDeclarationEntry) -> MatrixTemplate:
    return MatrixTemplate(
        radio=RadioTarget(model="X6200", profile_id="x6200"),
        entries=[entry],
    )


def _flatten(levels):
    return {check.check_id: check for level in levels for check in level.checks}


# ---------------------------------------------------------------------------
# 1. dry-run bypass closed: mutated tx.ptt (tx_adjacent=False) still BLOCKED
# ---------------------------------------------------------------------------


def test_dry_run_mutated_tx_ptt_still_blocked_without_authorization():
    entry = _entry(check_id="tx.ptt", capability="tx", tx_adjacent=False)
    template = _template(entry)

    blocked = dry_run_results(
        template, OperatorSafetyBlock(tx_allowed=False, tuner_allowed=False)
    )
    check = _flatten(blocked)["tx.ptt"]
    assert check.status is CheckStatus.BLOCKED
    assert check.status is not CheckStatus.SKIP

    authorized = dry_run_results(
        template, OperatorSafetyBlock(tx_allowed=True, tuner_allowed=False)
    )
    assert _flatten(authorized)["tx.ptt"].status is not CheckStatus.BLOCKED


# ---------------------------------------------------------------------------
# 2. tuner mutated: tx_adjacent=False still gated by tuner_allowed (own gate)
# ---------------------------------------------------------------------------


def test_dry_run_mutated_tuner_uses_tuner_gate_not_tx():
    entry = _entry(check_id="tuner.tune", capability="tuner", tx_adjacent=False)
    template = _template(entry)

    # Neither flag -> blocked.
    none = dry_run_results(template, OperatorSafetyBlock())
    assert _flatten(none)["tuner.tune"].status is CheckStatus.BLOCKED

    # tx_allowed alone does NOT authorize the tuner -> still blocked.
    tx_only = dry_run_results(
        template, OperatorSafetyBlock(tx_allowed=True, tuner_allowed=False)
    )
    assert _flatten(tx_only)["tuner.tune"].status is CheckStatus.BLOCKED

    # tuner_allowed authorizes it -> not blocked.
    tuner_ok = dry_run_results(
        template, OperatorSafetyBlock(tx_allowed=False, tuner_allowed=True)
    )
    assert _flatten(tuner_ok)["tuner.tune"].status is not CheckStatus.BLOCKED


# ---------------------------------------------------------------------------
# 3. mutated capability can't dodge the split: tuner.tune + capability="tx"
# ---------------------------------------------------------------------------


def test_mutated_capability_cannot_dodge_tuner_tx_split():
    # Both flags mutated: tx_adjacent=False AND capability="tx" (wrong).
    entry = _entry(check_id="tuner.tune", capability="tx", tx_adjacent=False)

    # Registry capability ("tuner") wins: tx_allowed alone must NOT authorize.
    assert (
        _is_authorized(entry, OperatorSafetyBlock(tx_allowed=True, tuner_allowed=False))
        is False
    )
    # tuner_allowed authorizes it.
    assert (
        _is_authorized(entry, OperatorSafetyBlock(tx_allowed=False, tuner_allowed=True))
        is True
    )

    template = _template(entry)
    tx_only = dry_run_results(
        template, OperatorSafetyBlock(tx_allowed=True, tuner_allowed=False)
    )
    assert _flatten(tx_only)["tuner.tune"].status is CheckStatus.BLOCKED


# ---------------------------------------------------------------------------
# 4. non-gated entry unaffected: rf_gain.set is never safety-gated
# ---------------------------------------------------------------------------


def test_non_gated_entry_unaffected():
    entry = _entry(check_id="rf_gain.set", capability="rf_gain", tx_adjacent=False)
    assert _is_safety_gated(entry) is False
    assert _is_authorized(entry, OperatorSafetyBlock()) is True

    template = _template(entry)
    levels = dry_run_results(template, OperatorSafetyBlock())
    check = _flatten(levels)["rf_gain.set"]
    # SUPPORTED maps to SKIP in dry-run; never BLOCKED.
    assert check.status is CheckStatus.SKIP


# ---------------------------------------------------------------------------
# 5. hardware pre-gate: mutated tx.ptt is gated (no actuation) on hardware path
# ---------------------------------------------------------------------------


def _make_mock_radio():
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"audio", "scope", "tuner", "tx"}
    radio.set_ptt = AsyncMock(return_value=None)
    radio.radio_state = RadioState()
    return radio


async def test_hardware_pregate_blocks_mutated_tx_ptt():
    radio = _make_mock_radio()
    # tx.ptt mutated to tx_adjacent=False; declared SUPPORTED to bypass the
    # MANUAL_REQUIRED pre-gate, isolating the authorization pre-gate.
    entry = _entry(check_id="tx.ptt", capability="tx", tx_adjacent=False)
    template = _template(entry)

    levels = await execute_hardware_checks(
        radio,
        template,
        OperatorSafetyBlock(tx_allowed=False, tuner_allowed=False),
        allow_writes=True,
    )
    check = _flatten(levels)["tx.ptt"]
    assert check.status is CheckStatus.BLOCKED
    assert check.evidence.get("reason") == "operator authorization required"
    radio.set_ptt.assert_not_called()


# ---------------------------------------------------------------------------
# 6. regression: canonical tx_adjacent=True entries gate exactly as before
# ---------------------------------------------------------------------------


def test_regression_canonical_tx_adjacent_entries_gate_as_before():
    tx_entry = _entry(check_id="tx.ptt", capability="tx", tx_adjacent=True)
    tuner_entry = _entry(check_id="tuner.tune", capability="tuner", tx_adjacent=True)

    assert _is_safety_gated(tx_entry) is True
    assert _is_safety_gated(tuner_entry) is True

    none = OperatorSafetyBlock()
    assert _is_authorized(tx_entry, none) is False
    assert _is_authorized(tuner_entry, none) is False

    assert _is_authorized(tx_entry, OperatorSafetyBlock(tx_allowed=True)) is True
    assert _is_authorized(tuner_entry, OperatorSafetyBlock(tuner_allowed=True)) is True
    # tx_allowed does not authorize the tuner.
    assert _is_authorized(tuner_entry, OperatorSafetyBlock(tx_allowed=True)) is False
