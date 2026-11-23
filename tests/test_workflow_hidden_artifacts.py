"""Every artifact upload of a dot-path asks for hidden files.

actions/upload-artifact >= v4.4 skips hidden files and directories unless
`include-hidden-files: true` is set. The Playwright output directory is
`.playwright-output`, so the journey and FTX-1 RIT failure reports were never
uploaded ("No files were found with the provided path", run 44754002530) and
a failing journey left no trace to read (MOR-4214).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _upload_steps() -> list[tuple[str, str, dict[str, Any]]]:
    found = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(workflow.read_text())
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                    found.append((workflow.name, job_name, step))
    return found


def _hidden(path: str) -> bool:
    return any(
        part.startswith(".") and part not in {".", ".."}
        for line in path.splitlines()
        for part in Path(line.strip()).parts
    )


def test_dot_path_uploads_include_hidden_files() -> None:
    offenders = [
        f"{name}:{job}:{step.get('name')}"
        for name, job, step in _upload_steps()
        if _hidden(str((step.get("with") or {}).get("path", "")))
        and (step.get("with") or {}).get("include-hidden-files") is not True
    ]
    assert offenders == []


def test_the_journey_report_upload_is_among_the_checked_steps() -> None:
    names = {(name, step.get("name")) for name, _, step in _upload_steps()}
    assert ("quick.yml", "Upload operator-journey E2E report on failure") in names


def test_quick_runs_this_contract_on_ci_only_changes() -> None:
    """MOR-4609: a PR that changes only workflows classifies as ``ci``, which
    never reached ``uv run pytest tests/`` -- so this file's own regression
    could not catch a dropped ``include-hidden-files`` on the PR class that
    edits the uploads. quick.yml's ci-gated steps must name this test."""
    quick = (WORKFLOWS / "quick.yml").read_text()
    section = quick.split("- name: Workflow artifact hidden-files contract tests", 1)[
        1
    ].split("- name:", 1)[0]
    assert "needs.classify.outputs.ci == 'true'" in section
    assert "test_workflow_hidden_artifacts.py" in section
