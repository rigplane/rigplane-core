"""Mock integration: full validation dry-run matrix per owned profile (MOR-647).

Runs the COMPLETE registry-driven validation matrix through the native dry-run
provider (``rigplane validate --model <M> --dry-run``) for every radio profile
shipped in ``rigs/`` — fully offline, no hardware. A global change that breaks
the validation pipeline (registry assembly, template generation, dry-run
gating, artifact schema) for ANY owned profile fails here.

Invariants asserted per profile:

* the dry-run exits 0 and emits a schema-valid ``ValidationArtifact``;
* every ``REGISTRY`` check appears in the artifact (iterated dynamically —
  never a hardcoded count or id list, so concurrent registry edits stay safe);
* no check carries an ``error`` and every status is a legal *planned* dry-run
  status (dry-run executes nothing, so ``pass``/``fail`` must not appear);
* TX-adjacent / registry-safety-gated checks are BLOCKED without operator
  authorization; manual + audio-probe checks are MANUAL_REQUIRED;
* the dry-run never constructs a radio backend (no connection attempt).

Marker policy: these tests are ``mock_integration`` — they RUN in the
integration job without hardware (see ``conftest.pytest_collection_modifyitems``).
Genuinely hardware-requiring validation tests carry the ``validation_hardware``
marker (registered in ``pyproject.toml``) instead, so the two sets can be
selected independently via ``-m validation_hardware`` / ``-m "not
validation_hardware"``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from rigplane.cli import _build_parser, _validate
from rigplane.profiles import get_radio_profile
from rigplane.validation.registry import REGISTRY, CheckKind
from rigplane.validation.schema import CheckStatus, validate_artifact_dict

pytestmark = [pytest.mark.integration, pytest.mark.mock_integration]

# Dry-run plans checks; it never executes them. Anything outside this set
# (notably pass/fail) means the dry-run path actually ran a check.
_DRY_RUN_LEGAL_STATUSES = {
    CheckStatus.SKIP.value,
    CheckStatus.UNSUPPORTED.value,
    CheckStatus.MANUAL_REQUIRED.value,
    CheckStatus.BLOCKED.value,
}


@pytest.fixture(autouse=True)
def _forbid_real_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if the dry-run path ever tries to build a radio backend."""
    import rigplane.backends.factory as factory

    def _fail(config: Any) -> Any:
        raise AssertionError(
            "dry-run validation must never construct a radio backend "
            f"(create_radio called with {config!r})"
        )

    monkeypatch.setattr(factory, "create_radio", _fail)


def _dry_run_artifact(
    model: str, capsys: pytest.CaptureFixture[str], *extra: str
) -> dict[str, Any]:
    """Run ``rigplane validate --dry-run --json`` for *model*, return the artifact."""
    argv = ["--model", model, "validate", "--dry-run", "--json", *extra]
    rc = _validate.run(_build_parser().parse_args(argv))
    out, err = capsys.readouterr()
    assert rc == 0, f"dry-run for {model} exited {rc}; stderr:\n{err}"
    artifact = json.loads(out)
    # Schema round-trip: the emitted JSON must be a valid ValidationArtifact.
    validate_artifact_dict(artifact)
    return artifact


def _checks_by_id(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flatten artifact levels into a ``check_id`` → check-dict map."""
    return {
        check["check_id"]: check
        for level in artifact["levels"]
        for check in level["checks"]
    }


def _is_safety_gated(spec: Any) -> bool:
    """Registry-side safety classification mirrored by the dry-run runner."""
    return spec.tx_adjacent or spec.kind is CheckKind.TX_ADJACENT_BLOCKED


def test_owned_profile_set_is_sane(all_owned_profile_models: list[str]) -> None:
    """Discovery yields a non-empty owned set including the reference rig."""
    assert all_owned_profile_models, "no owned rig profiles discovered in rigs/"
    assert "IC-7610" in all_owned_profile_models
    # Guard against vacuous per-spec loops below: the registry must contain
    # checks, including at least one safety-gated one.
    assert REGISTRY
    assert any(_is_safety_gated(spec) for spec in REGISTRY)


def test_full_registry_matrix_covers_every_check(
    owned_profile_model: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every REGISTRY check appears in the dry-run artifact; nothing leaks in."""
    artifact = _dry_run_artifact(owned_profile_model, capsys, "--no-overrides")
    checks = _checks_by_id(artifact)

    missing = [spec.check_id for spec in REGISTRY if spec.check_id not in checks]
    assert not missing, f"{owned_profile_model}: registry checks missing: {missing}"

    # Anything beyond the registry must be a synthetic <cap>.presence entry.
    registry_ids = {spec.check_id for spec in REGISTRY}
    unexpected = [
        check_id
        for check_id in checks
        if check_id not in registry_ids and not check_id.endswith(".presence")
    ]
    assert not unexpected, (
        f"{owned_profile_model}: unexpected non-registry checks: {unexpected}"
    )


def test_dry_run_statuses_are_planned_and_error_free(
    owned_profile_model: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """No check errors out and no check claims execution in a dry-run."""
    artifact = _dry_run_artifact(owned_profile_model, capsys, "--no-overrides")
    registry_ids = {spec.check_id for spec in REGISTRY}
    for check_id, check in _checks_by_id(artifact).items():
        assert check.get("error") is None, (
            f"{owned_profile_model}: {check_id} carries an error: {check['error']}"
        )
        if (
            check_id.endswith(".presence")
            and check_id not in registry_ids
            and check["status"] == CheckStatus.PASS.value
        ):
            # MOR-660: synthetic capability-presence entries are static profile
            # evidence, not executed probes. Registry-backed checks still must
            # not PASS/FAIL in dry-run.
            continue
        assert check["status"] in _DRY_RUN_LEGAL_STATUSES, (
            f"{owned_profile_model}: {check_id} has non-dry-run status "
            f"{check['status']!r}"
        )


def test_safety_gated_checks_blocked_without_authorization(
    owned_profile_model: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """TX-adjacent / registry-gated checks are BLOCKED; manual checks are
    MANUAL_REQUIRED for declared capabilities."""
    artifact = _dry_run_artifact(owned_profile_model, capsys, "--no-overrides")
    checks = _checks_by_id(artifact)
    capabilities = get_radio_profile(owned_profile_model).capabilities

    for spec in REGISTRY:
        status = checks[spec.check_id]["status"]
        if _is_safety_gated(spec):
            # Fail-closed: blocked regardless of capability declaration.
            assert status == CheckStatus.BLOCKED.value, (
                f"{owned_profile_model}: safety-gated {spec.check_id} is "
                f"{status!r}, expected blocked"
            )
        elif (
            spec.kind in (CheckKind.MANUAL, CheckKind.AUDIO_PROBE)
            and spec.capability
            and spec.capability in capabilities
        ):
            assert status == CheckStatus.MANUAL_REQUIRED.value, (
                f"{owned_profile_model}: manual {spec.check_id} is "
                f"{status!r}, expected manual_required"
            )


def test_default_pipeline_with_overrides_completes(
    owned_profile_model: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The DEFAULT pipeline (per-profile overrides auto-applied) stays green.

    Override files may legitimately exclude or append checks, so registry
    coverage here is asserted modulo the artifact's own override audit.
    """
    artifact = _dry_run_artifact(owned_profile_model, capsys)
    checks = _checks_by_id(artifact)
    audit = artifact.get("metadata", {}).get("overrides") or {}
    excluded = set(audit.get("excluded", []))

    missing = [
        spec.check_id
        for spec in REGISTRY
        if spec.check_id not in checks and spec.check_id not in excluded
    ]
    assert not missing, (
        f"{owned_profile_model}: registry checks neither present nor "
        f"override-excluded: {missing}"
    )

    # The summary in metadata must agree with a recount of the levels.
    recount: dict[str, int] = {}
    for check in checks.values():
        recount[check["status"]] = recount.get(check["status"], 0) + 1
    summary = artifact["metadata"]["summary"]
    nonzero_summary = {status: n for status, n in summary.items() if n}
    assert nonzero_summary == recount
