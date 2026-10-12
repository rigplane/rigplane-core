"""Pins how ``.github/workflows/soak-nightly.yml`` launches and reports a soak.

The workflow has two jobs: ``soak`` (nightly schedule and plain dispatch,
75 minutes) and ``soak-24h`` (only a dispatch with ``certification`` set,
``--duration 86400``). Both write the verdict to ``$GITHUB_STEP_SUMMARY``
from the ``soak-report.json`` in the workspace, and upload that file with
``continue-on-error``.

The workflow is read with ``yaml.safe_load``; ``pyproject.toml`` does not
declare PyYAML, so the import fails loudly (collection error) if it ever
leaves the environment. The summary script is extracted from the workflow
text and run as written against criteria blocks produced by the driver's own
``evaluate_report``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.soak import soak_driver

WORKFLOW = (
    Path(__file__).resolve().parent.parent
    / ".github"
    / "workflows"
    / "soak-nightly.yml"
)
NIGHTLY, CERTIFICATION = "soak", "soak-24h"
JOBS = (NIGHTLY, CERTIFICATION)
MIB = 1024 * 1024

# Job-level ``if`` expressions this file knows how to evaluate, as a function
# of the ``certification`` input (None: the schedule trigger, which defines no
# inputs in this workflow).
_IF_EXPRESSIONS = {
    "inputs.certification": lambda certification: bool(certification),
    "!inputs.certification": lambda certification: not certification,
}


def _workflow() -> Any:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job(name: str) -> Any:
    jobs = _workflow()["jobs"]
    assert name in jobs, f"{WORKFLOW.name} has no job {name!r} (jobs: {sorted(jobs)})"
    return jobs[name]


def _steps(job: Any) -> Any:
    return job["steps"]


def _step_named(job: Any, prefix: str) -> Any:
    matches = [s for s in _steps(job) if str(s.get("name", "")).startswith(prefix)]
    assert len(matches) == 1, f"expected one step named {prefix!r}*, got {matches}"
    return matches[0]


def _upload_step(job: Any) -> Any:
    matches = [
        s
        for s in _steps(job)
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert len(matches) == 1, f"expected one upload-artifact step, got {matches}"
    return matches[0]


def _duration_argument(job: dict[str, Any]) -> str:
    run = " ".join(_step_named(job, "Run soak driver")["run"].split())
    match = re.search(r"--duration (.+?) --report", run)
    assert match, f"no --duration argument in {run!r}"
    return match.group(1)


def _job_runs(job: dict[str, Any], certification: bool | None) -> bool:
    raw = job.get("if")
    if raw is None:
        return True
    expression = re.sub(r"^\$\{\{\s*|\s*\}\}$", "", str(raw).strip())
    assert expression in _IF_EXPRESSIONS, (
        f"job condition {raw!r} is not one this test evaluates; extend _IF_EXPRESSIONS"
    )
    return _IF_EXPRESSIONS[expression](certification)


def test_certification_is_a_boolean_dispatch_input_defaulting_to_false() -> None:
    # PyYAML reads the bare key ``on`` as the boolean True.
    document = _workflow()
    triggers = document.get("on", document.get(True))
    certification = triggers["workflow_dispatch"]["inputs"]["certification"]
    assert certification["type"] == "boolean"
    assert certification["default"] is False
    assert "duration" in triggers["workflow_dispatch"]["inputs"]
    assert "schedule" in triggers


@pytest.mark.parametrize("certification", [None, False, True])
def test_exactly_one_job_is_selected_by_the_certification_input(
    certification: bool | None,
) -> None:
    assert _job_runs(_job(CERTIFICATION), certification) is (certification is True)
    assert _job_runs(_job(NIGHTLY), certification) is (certification is not True)


def test_certification_job_runs_24_hours_and_outlives_them() -> None:
    job = _job(CERTIFICATION)
    assert _duration_argument(job) == "86400"
    assert job["timeout-minutes"] >= 1500
    assert job["timeout-minutes"] * 60 > 86400


def test_nightly_job_keeps_its_duration_input_and_75_minute_timeout() -> None:
    job = _job(NIGHTLY)
    assert job["timeout-minutes"] == 75
    assert _duration_argument(job) == "${{ inputs.duration || '3600' }}"


def test_both_jobs_stay_in_the_one_workflow_concurrency_group() -> None:
    concurrency = _workflow()["concurrency"]
    assert concurrency["cancel-in-progress"] is False
    assert "github.workflow" in concurrency["group"]
    for name in JOBS:
        assert "concurrency" not in _job(name)


@pytest.mark.parametrize("name", JOBS)
def test_summary_step_runs_after_the_driver_even_when_it_failed(name: str) -> None:
    job = _job(name)
    summary = _step_named(job, "Write soak verdict")
    assert summary["if"] == "always()"
    assert '>> "$GITHUB_STEP_SUMMARY"' in summary["run"]
    steps = _steps(job)
    assert steps.index(summary) > steps.index(_step_named(job, "Run soak driver"))


@pytest.mark.parametrize("name", JOBS)
def test_report_upload_cannot_fail_the_job(name: str) -> None:
    upload = _upload_step(_job(name))
    assert upload["if"] == "always()"
    assert upload["continue-on-error"] is True


def test_both_jobs_use_the_same_summary_script() -> None:
    scripts = {_step_named(_job(n), "Write soak verdict")["run"] for n in JOBS}
    assert len(scripts) == 1


def _summary_script() -> str:
    run = _step_named(_job(NIGHTLY), "Write soak verdict")["run"]
    match = re.search(r"<<'PY'\n(.*)\nPY\s*$", run, re.DOTALL)
    assert match, "summary step has no quoted PY heredoc"
    return match.group(1)


def _reconnect(*, recovered: bool, fd_delta: int, task_delta: int) -> dict[str, Any]:
    return {
        "startedAtS": 600.0,
        "windowStartS": 600.0,
        "windowEndS": 660.0,
        "recovered": recovered,
        "disconnectObserved": True,
        "fdsPre": 20,
        "fdsPost": 20 + fd_delta,
        "tasksPre": 25,
        "tasksPost": 25 + task_delta,
    }


def _report(
    *,
    slope_mib_per_h: float,
    fd_delta: int = 0,
    task_delta: int = 0,
    unrecovered: int = 0,
    verdict: str = "PASS",
    error: str | None = None,
) -> dict[str, Any]:
    config = soak_driver.SoakConfig(duration_s=3600.0)
    samples = [
        soak_driver.ProcessSample(
            t_s=t,
            rss_bytes=int(80 * MIB + slope_mib_per_h * MIB * t / 3600.0),
            fds=20,
            asyncio_tasks=25,
            connection="connected",
        )
        for t in range(0, 3601, 30)
    ]
    reconnects = [_reconnect(recovered=True, fd_delta=fd_delta, task_delta=task_delta)]
    reconnects += [_reconnect(recovered=False, fd_delta=0, task_delta=0)] * unrecovered
    return {
        "verdict": verdict,
        "durationConfiguredS": 3600.0,
        "durationActualS": 3600.4,
        "error": error,
        "criteria": soak_driver.evaluate_report(config, samples, reconnects),
    }


def _run_summary(
    tmp_path: Path, report_text: str | None
) -> subprocess.CompletedProcess[str]:
    if report_text is not None:
        (tmp_path / "soak-report.json").write_text(report_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-"],
        input=_summary_script(),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_summary_script_prints_the_verdict_and_the_measured_numbers(
    tmp_path: Path,
) -> None:
    report = _report(
        slope_mib_per_h=12.0, fd_delta=3, task_delta=-2, unrecovered=1, verdict="FAIL"
    )
    done = _run_summary(tmp_path, json.dumps(report))
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert "## Soak FAIL: 3600 s of 3600 s" in lines
    assert "- RSS growth PASS: +12.0 MiB/h (limit 50 MiB/h)" in lines
    assert "- FDs PASS: max abs delta 3 (1 checked, tolerance 5)" in lines
    assert "- Tasks PASS: max abs delta 2 (1 checked, tolerance 5)" in lines
    assert "- Unrecovered disconnects FAIL: 1 of 2 (budget 30 s)" in lines


def test_summary_script_reports_an_unmeasured_rss_slope_as_such(tmp_path: Path) -> None:
    report = _report(slope_mib_per_h=0.0)
    report["criteria"]["rssGrowth"].update(
        status="NOT_EVALUATED", slopeBytesPerHour=None
    )
    done = _run_summary(tmp_path, json.dumps(report))
    assert done.returncode == 0, done.stderr
    assert (
        "- RSS growth NOT_EVALUATED: n/a (limit 50 MiB/h)" in done.stdout.splitlines()
    )


@pytest.mark.parametrize("report_text", [None, "{not json", "[]"])
def test_summary_script_fails_and_says_so_without_a_usable_report(
    tmp_path: Path, report_text: str | None
) -> None:
    done = _run_summary(tmp_path, report_text)
    assert done.returncode != 0
    assert "Soak: NO REPORT" in done.stdout
