"""Pin the honest verdict of the bench-nightly radio jobs.

`.github/workflows/bench-nightly.yml` runs one matrix job per bench radio.
Run 38218526102 concluded success while its IC-7300 and FTX-1 jobs failed,
because the `radio` job carried `continue-on-error: true`; "skipped: busy"
and "skipped: not configured" only printed a line and ended green, as did a
dry run. These tests pin the mechanism that keeps that from recurring: the
`radio` job has no job-level `continue-on-error`, so a night passes for a
radio only when its job concludes success, while the matrix strategy still
keeps radios independent of each other.

The three `Outcome:` steps at the end of the job turn the silent green
endings red with their own step names; `rigplane diagnose` and the artifact
upload stay evidence-only. The guard pin requires every step that invokes
`uv run rigplane` or the hardware pytest subset to go through
`scripts/bench/guard.sh run --`, which refuses TX and tuner actuation, so a
future edit cannot route a radio-touching command around the guard while
reshaping the verdict steps.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "bench-nightly.yml"

EXPECTED_OUTCOMES = {
    "Outcome: not configured": "steps.config.outputs.configured == 'false'",
    "Outcome: busy": "steps.lock.outputs.acquired == 'false'",
    "Outcome: dry run": "steps.mode.outputs.dry_run == 'true'",
}

EVIDENCE_STEP_NAMES = ("rigplane diagnose", "Upload normalized results")


def _radio_job() -> dict:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return workflow["jobs"]["radio"]


def _steps_by_name(radio: dict) -> dict[str, dict]:
    steps = radio["steps"]
    names = [step.get("name", "") for step in steps]
    assert len(set(names)) == len(names), (
        f"duplicate step names in the radio job: {sorted(names)}"
    )
    return {step.get("name", ""): step for step in steps}


def test_radio_job_has_no_continue_on_error() -> None:
    radio = _radio_job()
    assert "continue-on-error" not in radio, (
        "the radio job carries job-level continue-on-error again, so a "
        "failed radio still concludes success as in run 38218526102"
    )


def test_radio_matrix_stays_independent() -> None:
    strategy = _radio_job()["strategy"]
    assert strategy["fail-fast"] is False, (
        "fail-fast must stay false so one radio's failure never cancels the others"
    )
    assert strategy["max-parallel"] == 1, (
        "max-parallel must stay 1 so the bench radios are probed one at a time"
    )


def test_outcome_steps_fail_the_job() -> None:
    steps = _steps_by_name(_radio_job())
    missing = [name for name in EXPECTED_OUTCOMES if name not in steps]
    assert not missing, (
        f"outcome steps missing from the radio job: {missing} — a "
        "not-configured, busy or dry-run night would end green again"
    )
    for name, output_condition in EXPECTED_OUTCOMES.items():
        step = steps[name]
        condition = step.get("if", "")
        assert "always()" in condition and output_condition in condition, (
            f"{name!r} has condition {condition!r}, expected always() gated on "
            f"{output_condition!r}"
        )
        run = step.get("run", "")
        assert "::error title=" in run, (
            f"{name!r} does not emit a ::error annotation"
        )
        assert "$GITHUB_STEP_SUMMARY" in run, (
            f"{name!r} does not report to the job summary"
        )
        assert "exit 1" in run, (
            f"{name!r} does not exit non-zero, so its night would still pass"
        )


def test_evidence_steps_do_not_decide_the_verdict() -> None:
    steps = _steps_by_name(_radio_job())
    for name in EVIDENCE_STEP_NAMES:
        assert name in steps, (
            f"evidence step {name!r} is missing from the radio job"
        )
        assert steps[name].get("continue-on-error") is True, (
            f"evidence step {name!r} must stay continue-on-error so artifact "
            "storage or diagnose failures cannot decide the night"
        )
    release = steps["Release bench lock"]
    assert release.get("continue-on-error") is not True, (
        "Release bench lock must fail the job when it fails: a leaked lock matters"
    )


def test_radio_commands_go_through_the_guard() -> None:
    steps = _steps_by_name(_radio_job())
    unguarded: list[str] = []
    for name, step in steps.items():
        run = step.get("run")
        if not isinstance(run, str):
            continue
        if "uv run rigplane" not in run and "uv run pytest" not in run:
            continue
        if "scripts/bench/guard.sh run --" not in run:
            unguarded.append(name)
    assert not unguarded, (
        f"steps invoking rigplane or the hardware pytest subset outside "
        f"guard.sh run --: {unguarded} — TX and the tuner are never actuated"
    )
