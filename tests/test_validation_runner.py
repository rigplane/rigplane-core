"""Runner tests for the validation matrix dry-run path."""

from __future__ import annotations

from pathlib import Path

from rigplane.validation import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckStatus,
    FailureDomain,
    MatrixTemplate,
    OperatorSafetyBlock,
    RadioTarget,
    TransportInfo,
    ValidationLevel,
    build_validation_artifact,
    dry_run_results,
    human_summary,
    load_template,
    validate_artifact_dict,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
_TEMPLATE = _FIXTURES / "validation_template_ic7300.json"


def _checks_by_id(levels: list) -> dict[str, object]:
    return {check.check_id: check for level in levels for check in level.checks}


def test_load_template_round_trips_fixture() -> None:
    template = load_template(_TEMPLATE)
    assert template.radio.profile_id == "ic7300"
    assert {entry.check_id for entry in template.entries} >= {
        "discovery.identify",
        "freq.write",
        "freq.reverse_sync",
    }


def test_dry_run_declaration_mapping() -> None:
    template = load_template(_TEMPLATE)
    levels = dry_run_results(template, OperatorSafetyBlock())
    checks = _checks_by_id(levels)
    # supported -> skip
    assert checks["discovery.identify"].status is CheckStatus.SKIP
    # unsupported_pending_evidence -> unsupported (none here for ic7300 scope;
    # audio.rx is manual_required -> manual_required)
    assert checks["audio.rx"].status is CheckStatus.MANUAL_REQUIRED
    # scope.capture is supported on ic7300 -> skip
    assert checks["scope.capture"].status is CheckStatus.SKIP


def test_dry_run_presence_check_resolves_pass() -> None:
    """MOR-660: a synthetic ``<cap>.presence`` entry (declared capability, no
    registry check) resolves PASS in dry-run — presence confirms the cap."""
    template = MatrixTemplate(
        radio=RadioTarget(model="IC-7300", profile_id="ic7300"),
        entries=[
            CapabilityDeclarationEntry(
                check_id="scan.presence",
                capability="scan",
                level=ValidationLevel.STATIC_PROFILE,
                declaration=CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE,
                summary="presence",
            ),
            CapabilityDeclarationEntry(
                check_id="bsr.select",
                capability="bsr",
                level=ValidationLevel.CAPABILITY_MATRIX,
                declaration=CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE,
                summary="undeclared functional",
            ),
        ],
    )
    levels = dry_run_results(template, OperatorSafetyBlock())
    checks = _checks_by_id(levels)
    # presence entry for a declared cap → PASS
    assert checks["scan.presence"].status is CheckStatus.PASS
    # a non-presence pending-evidence entry (undeclared cap) stays UNSUPPORTED
    assert checks["bsr.select"].status is CheckStatus.UNSUPPORTED


def test_tx_adjacent_blocked_without_authorization() -> None:
    template = load_template(_TEMPLATE)
    levels = dry_run_results(template, OperatorSafetyBlock())
    checks = _checks_by_id(levels)
    assert checks["tuner.tune"].status is CheckStatus.BLOCKED
    assert checks["tx.ptt"].status is CheckStatus.BLOCKED


def test_tx_adjacent_unblocked_with_authorization() -> None:
    template = load_template(_TEMPLATE)
    safety = OperatorSafetyBlock(tx_allowed=True, tuner_allowed=True)
    levels = dry_run_results(template, safety)
    checks = _checks_by_id(levels)
    # Authorized but still dry-run: never PASS, stays at the declaration status.
    assert checks["tuner.tune"].status is CheckStatus.MANUAL_REQUIRED
    assert checks["tx.ptt"].status is CheckStatus.MANUAL_REQUIRED
    assert checks["tuner.tune"].status is not CheckStatus.PASS
    assert checks["tx.ptt"].status is not CheckStatus.PASS


def test_build_artifact_round_trips_through_validator() -> None:
    template = load_template(_TEMPLATE)
    levels = dry_run_results(template, OperatorSafetyBlock())
    artifact = build_validation_artifact(
        template=template,
        levels=levels,
        transport=TransportInfo(backend="fixture"),
        safety=OperatorSafetyBlock(),
        core_version="2.5.1",
    )
    reparsed = validate_artifact_dict(artifact.to_dict())
    assert reparsed.radio.profile_id == "ic7300"
    assert reparsed.metadata["summary"] == artifact.metadata["summary"]


def test_human_summary_lists_blocked_items() -> None:
    template = load_template(_TEMPLATE)
    levels = dry_run_results(template, OperatorSafetyBlock())
    artifact = build_validation_artifact(
        template=template,
        levels=levels,
        transport=TransportInfo(backend="fixture"),
        safety=OperatorSafetyBlock(),
        core_version="2.5.1",
    )
    text = human_summary(artifact)
    assert text
    assert "tuner.tune" in text
    assert "tx.ptt" in text


def _make_split_template() -> MatrixTemplate:
    """Build an in-memory template with a generic tx_adjacent entry (capability='split').

    Constructed directly from dataclasses — no validate_template_dict — so
    capability membership is not checked.
    """
    entry = CapabilityDeclarationEntry(
        check_id="split.enable",
        capability="split",
        level=ValidationLevel.CAPABILITY_MATRIX,
        declaration=CapabilityDeclaration.MANUAL_REQUIRED,
        summary="Split TX",
        tx_adjacent=True,
    )
    return MatrixTemplate(
        radio=RadioTarget(model="Test Radio", profile_id="test"),
        entries=[entry],
    )


def test_generic_tx_adjacent_blocked_by_default() -> None:
    """Fail-closed: a tx_adjacent entry with capability other than 'tuner'/'tx'
    must be BLOCKED under the default OperatorSafetyBlock (both flags False).
    """
    template = _make_split_template()
    levels = dry_run_results(template, OperatorSafetyBlock())
    checks = _checks_by_id(levels)
    result = checks["split.enable"]
    assert result.status is CheckStatus.BLOCKED
    assert result.failure_domain is FailureDomain.COMMAND_EXECUTION


def test_generic_tx_adjacent_unblocked_by_tx_allowed() -> None:
    """tx_allowed=True must unblock a generic tx_adjacent entry (e.g. 'split')."""
    template = _make_split_template()
    levels = dry_run_results(template, OperatorSafetyBlock(tx_allowed=True))
    checks = _checks_by_id(levels)
    result = checks["split.enable"]
    # Authorized in dry-run: stays at declaration status, never PASS or BLOCKED.
    assert result.status is not CheckStatus.BLOCKED
    assert result.status is not CheckStatus.PASS
    assert result.status is CheckStatus.MANUAL_REQUIRED


def test_generic_tx_adjacent_still_blocked_by_tuner_only() -> None:
    """tuner_allowed=True must NOT unblock a non-tuner tx_adjacent entry.

    Proves the tuner flag is scoped exclusively to capability='tuner' and does
    not open the gate for any other tx_adjacent check.
    """
    template = _make_split_template()
    levels = dry_run_results(template, OperatorSafetyBlock(tuner_allowed=True))
    checks = _checks_by_id(levels)
    result = checks["split.enable"]
    assert result.status is CheckStatus.BLOCKED
    assert result.failure_domain is FailureDomain.COMMAND_EXECUTION
