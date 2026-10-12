"""Pins how ``.github/workflows/soak-nightly.yml`` launches and reports a soak.

Job ``soak`` runs for the schedule and a plain dispatch (75 minutes); job
``soak-24h`` only for a dispatch with ``certification`` (``--duration 86400``).
Both run ``scripts/soak/report_summary.py`` after the driver and upload the
report with ``continue-on-error``. The workflow is read as text, as
``tests/test_ci_pytest_invocation.py`` reads its workflows: ``pyproject.toml``
declares no YAML parser.
"""

from __future__ import annotations

import importlib.util
import re
import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / ".github" / "workflows" / "soak-nightly.yml").read_text(encoding="utf-8")
NIGHTLY, CERTIFICATION = "soak", "soak-24h"
JOBS = (NIGHTLY, CERTIFICATION)


def _job(name: str) -> tuple[str, list[str]]:
    """The job's own settings as text, and its steps as separate texts."""
    parts = re.split(r"(?m)^  ([\w-]+):\n", TEXT.split("\njobs:\n", 1)[1])
    blocks = dict(zip(parts[1::2], parts[2::2], strict=True))
    assert name in blocks, f"no job {name!r} (jobs: {sorted(blocks)})"
    settings, steps = blocks[name].split("    steps:\n")
    return settings, re.split(r"(?m)^      - ", steps)[1:]


def _setting(job: str, key: str) -> str | None:
    match = re.search(rf"(?m)^    {re.escape(key)}: (.*)$", _job(job)[0])
    return match.group(1).strip() if match else None


def _step(job: str, name_prefix: str) -> str:
    matches = [s for s in _job(job)[1] if s.startswith(f"name: {name_prefix}")]
    assert len(matches) == 1, f"{job}: expected one step {name_prefix!r}*, {matches}"
    return matches[0]


def _lines(step: str) -> set[str]:
    return {line.strip() for line in step.splitlines()}


def _run(step: str) -> str:
    """The step's ``run`` text on one line; ``run`` is its last key."""
    return " ".join(step.split("run:", 1)[1].split()).removeprefix(">-").strip()


def _driver_arguments(job: str) -> re.Match[str]:
    match = re.search(r"--duration (.+?) --report (\S+)", _run(_step(job, "Run soak")))
    assert match, f"{job}: no --duration/--report in the driver step"
    return match


def test_certification_is_a_boolean_dispatch_input_defaulting_to_false() -> None:
    match = re.search(r"(?m)^      certification:\n((?:        .*\n)+)", TEXT)
    assert match, "no certification input"
    assert {"type: boolean", "default: false"} <= _lines(match.group(1))


def test_the_certification_input_selects_exactly_one_job() -> None:
    assert _setting(CERTIFICATION, "if") == "${{ inputs.certification }}"
    assert _setting(NIGHTLY, "if") == "${{ !inputs.certification }}"


def test_certification_job_runs_24_hours_and_outlives_them() -> None:
    assert _driver_arguments(CERTIFICATION)[1] == "86400"
    timeout = int(_setting(CERTIFICATION, "timeout-minutes") or 0)
    assert timeout >= 1500
    assert timeout * 60 > 86400


def test_nightly_job_keeps_its_duration_input_and_75_minute_timeout() -> None:
    assert _setting(NIGHTLY, "timeout-minutes") == "75"
    assert _driver_arguments(NIGHTLY)[1] == "${{ inputs.duration || '3600' }}"


def test_both_jobs_stay_in_the_one_workflow_concurrency_group() -> None:
    group = re.search(r"(?m)^concurrency:\n((?:  .*\n)+)", TEXT)
    assert group and "  cancel-in-progress: false" in group.group(1).splitlines()
    assert all(_setting(name, "concurrency") is None for name in JOBS)


@pytest.mark.parametrize("name", JOBS)
def test_summary_step_runs_the_script_on_the_drivers_report_even_after_a_failure(
    name: str,
) -> None:
    summary = _step(name, "Write soak verdict")
    assert "if: always()" in _lines(summary)
    command = shlex.split(_run(summary))
    assert "-m" in command, f"{name}: the summary step does not run a module"
    module, *arguments = command[command.index("-m") + 1 :]
    assert arguments == [_driver_arguments(name)[2]]
    assert importlib.util.find_spec(module) is not None
    steps = _job(name)[1]
    assert steps.index(summary) > steps.index(_step(name, "Run soak"))


@pytest.mark.parametrize("name", JOBS)
def test_report_upload_cannot_fail_the_job(name: str) -> None:
    uploads = [s for s in _job(name)[1] if "uses: actions/upload-artifact@" in s]
    assert len(uploads) == 1
    assert {"if: always()", "continue-on-error: true"} <= _lines(uploads[0])
