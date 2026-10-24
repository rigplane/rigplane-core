"""Pin Chromium's startup environment once per browser-launching workflow job.

Text parsing follows test_soak_workflow.py without requiring a YAML parser.
Pin the browser job set, exact quoted values and absence of step overrides.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
WORKFLOW_NAMES = ("quick.yml", "full.yml", "visual.yml")
BROWSER_ENV = {
    "DBUS_SESSION_BUS_ADDRESS": "'disabled:'",
    "FC_FONTATIONS": "'1'",
    "VK_ICD_FILENAMES": "''",
}


def _jobs(workflow: str) -> dict[str, tuple[str, list[str]]]:
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    parts = re.split(r"(?m)^  ([\w-]+):\n", text.split("\njobs:\n", 1)[1])
    blocks = dict(zip(parts[1::2], parts[2::2], strict=True))
    jobs = {}
    for name, block in blocks.items():
        settings, steps = block.split("    steps:\n", 1)
        jobs[name] = settings, re.split(r"(?m)^      - ", steps)[1:]
    return jobs


def _run(step: str) -> str:
    match = re.search(r"(?m)^(?:        )?run:[ \t]*(.*)$", step)
    if match is None:
        return ""
    value = match.group(1).strip()
    if value not in {"|", "|-", "|+", ">", ">-", ">+"}:
        return value
    body = re.split(r"(?m)^        \S", step[match.end() :], maxsplit=1)[0]
    return " ".join(
        line.strip() for line in body.splitlines() if not line.lstrip().startswith("#")
    )


def _launches_browser(step: str) -> bool:
    run = _run(step)
    return any(
        command in run for command in ("playwright test", "capture.mjs", "test:e2e")
    )


def test_browser_jobs_have_the_exact_job_level_environment() -> None:
    browser_jobs = {
        (workflow, name): settings
        for workflow in WORKFLOW_NAMES
        for name, (settings, steps) in _jobs(workflow).items()
        if any(_launches_browser(step) for step in steps)
    }
    expected = {
        ("quick.yml", "quick"),
        ("full.yml", "capture-harness"),
        ("visual.yml", "visual"),
    }
    assert set(browser_jobs) == expected, (
        f"Browser jobs changed: found {sorted(browser_jobs)}, "
        f"expected {sorted(expected)}"
    )
    for (workflow, job), settings in browser_jobs.items():
        env_blocks = re.findall(r"(?m)^    env:\n((?:      [^\n]*\n|\n)+)", settings)
        assert len(env_blocks) == 1, f"{workflow}:{job}: expected one job-level env"
        for name, expected_value in BROWSER_ENV.items():
            values = re.findall(rf"(?m)^      {name}:[ \t]*(.*)$", env_blocks[0])
            assert values == [expected_value], (
                f"{workflow}:{job}: {name} must appear once as {expected_value}, "
                f"found {values}"
            )


def test_steps_do_not_override_the_browser_environment() -> None:
    for workflow in WORKFLOW_NAMES:
        for job, (_, steps) in _jobs(workflow).items():
            for step in steps:
                for name in BROWSER_ENV:
                    assert not re.search(rf"(?m)^\s*{name}:", step), (
                        f"{workflow}:{job}: step overrides {name}: "
                        f"{step.splitlines()[0]}"
                    )
