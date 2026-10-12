"""Pin the honest verdict of the bench-nightly radio jobs.

`.github/workflows/bench-nightly.yml` runs one matrix job per bench radio.
Run 38218526102 concluded success while its IC-7300 and FTX-1 jobs failed,
because the `radio` job carried `continue-on-error: true`; "skipped: busy"
and "skipped: not configured" only printed a line and ended green, as did a
dry run.

These tests read the workflow as plain text. PyYAML is not a declared test
dependency: it reaches the test environment only transitively, through the
docs tool in the dev dependency group, so like the sibling
`tests/test_ci_pytest_invocation.py` this file parses the workflow with
indentation-anchored regular expressions instead of importing `yaml`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "bench-nightly.yml"

EXPECTED_OUTCOMES = {
    "Outcome: not configured": "steps.config.outputs.configured == 'false'",
    "Outcome: busy": "steps.lock.outputs.acquired == 'false'",
    "Outcome: dry run": "steps.mode.outputs.dry_run == 'true'",
}

EVIDENCE_STEP_NAMES = ("rigplane diagnose", "Upload normalized results")

GUARD_PREFIX = "bash scripts/bench/guard.sh run -- "

_JOB_START_RE = re.compile(r"^  radio:\n", re.MULTILINE)
_NEXT_JOB_RE = re.compile(r"^  [A-Za-z_][\w-]*:", re.MULTILINE)
_STEP_START_RE = re.compile(r"^      - ", re.MULTILINE)
_NAME_RE = re.compile(r"^      (?:- |  )name: (.*)$", re.MULTILINE)
_CONTINUE_RE = re.compile(r"^      (?:- |  )continue-on-error: (.*)$", re.MULTILINE)
_IF_RE = re.compile(r"^      (?:- |  )if: (.*)$", re.MULTILINE)
_RUN_KEY_RE = re.compile(r"^      (?:- |  )run: (.*)$", re.MULTILINE)
_UV_RUN_RE = re.compile(
    r"uv\s+run(?P<opts>(?:\s+-\S+(?:\s+[^\s]+)?)*)\s+(?P<cmd>rigplane|pytest)\b"
)


def _radio_job_text() -> str:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    start = _JOB_START_RE.search(text)
    assert start is not None, "the radio job is missing from bench-nightly.yml"
    rest = text[start.end() :]
    following = _NEXT_JOB_RE.search(rest)
    return rest[:following.start()] if following else rest


def _step_blocks(job_text: str) -> list[str]:
    starts = [match.start() for match in _STEP_START_RE.finditer(job_text)]
    assert starts, "the radio job has no steps"
    ends = [*starts[1:], len(job_text)]
    return [job_text[start:end] for start, end in zip(starts, ends)]


def _step_name(block: str) -> str | None:
    match = _NAME_RE.search(block)
    if match is None:
        return None
    return match.group(1).strip().strip("'\"")


def _continue_on_error(block: str) -> str | None:
    match = _CONTINUE_RE.search(block)
    return match.group(1).strip() if match else None


def _named_steps(blocks: list[str]) -> dict[str, str]:
    named: dict[str, str] = {}
    for block in blocks:
        name = _step_name(block)
        if name is None:
            continue
        assert name not in named, f"duplicate step name in the radio job: {name!r}"
        named[name] = block
    return named


def _run_text(block: str) -> str | None:
    match = _RUN_KEY_RE.search(block)
    if match is None:
        return None
    if match.group(1).strip() != "|":
        return match.group(1)
    lines = block.splitlines()
    index = next(i for i, line in enumerate(lines) if _RUN_KEY_RE.match(line))
    body: list[str] = []
    for line in lines[index + 1 :]:
        if line.strip() == "":
            body.append("")
            continue
        if len(line) - len(line.lstrip(" ")) < 10:
            break
        body.append(line[10:])
    return "\n".join(body)


def test_radio_job_has_no_continue_on_error() -> None:
    job_text = _radio_job_text()
    job_level = re.findall(r"^    continue-on-error:.*$", job_text, re.MULTILINE)
    assert not job_level, (
        "the radio job carries job-level continue-on-error again, so a "
        "failed radio still concludes success as in run 38218526102"
    )


def test_radio_matrix_stays_independent() -> None:
    job_text = _radio_job_text()
    assert re.search(r"^      fail-fast: false$", job_text, re.MULTILINE), (
        "fail-fast must stay false so one radio's failure never cancels the others"
    )
    assert re.search(r"^      max-parallel: 1$", job_text, re.MULTILINE), (
        "max-parallel must stay 1 so the bench radios are probed one at a time"
    )


def test_outcome_steps_fail_the_job() -> None:
    steps = _named_steps(_step_blocks(_radio_job_text()))
    missing = [name for name in EXPECTED_OUTCOMES if name not in steps]
    assert not missing, (
        f"outcome steps missing from the radio job: {missing} — a "
        "not-configured, busy or dry-run night would end green again"
    )
    for name, output_condition in EXPECTED_OUTCOMES.items():
        block = steps[name]
        assert _continue_on_error(block) is None, (
            f"{name!r} carries continue-on-error, so its night would still pass"
        )
        condition_match = _IF_RE.search(block)
        condition = condition_match.group(1) if condition_match else ""
        assert "always()" in condition and output_condition in condition, (
            f"{name!r} has condition {condition!r}, expected always() gated on "
            f"{output_condition!r}"
        )
        run = _run_text(block) or ""
        assert "::error title=" in run, f"{name!r} does not emit a ::error annotation"
        assert "$GITHUB_STEP_SUMMARY" in run, (
            f"{name!r} does not report to the job summary"
        )
        assert "exit 1" in run, (
            f"{name!r} does not exit non-zero, so its night would still pass"
        )


def test_evidence_steps_do_not_decide_the_verdict() -> None:
    blocks = _step_blocks(_radio_job_text())
    steps = _named_steps(blocks)
    for name in EVIDENCE_STEP_NAMES:
        assert name in steps, f"evidence step {name!r} is missing from the radio job"
        assert _continue_on_error(steps[name]) == "true", (
            f"evidence step {name!r} must stay continue-on-error so artifact "
            "storage or diagnose failures cannot decide the night"
        )
    release = steps.get("Release bench lock")
    assert release is not None, "the Release bench lock step is missing"
    assert _continue_on_error(release) is None, (
        "Release bench lock must fail the job when it fails: a leaked lock matters"
    )
    for block in blocks:
        name = _step_name(block)
        if name in EVIDENCE_STEP_NAMES:
            continue
        assert _continue_on_error(block) is None, (
            f"step {name!r} carries continue-on-error, so a failure there "
            "would still conclude success"
        )


def test_radio_commands_go_through_the_guard() -> None:
    """Every `uv run` invocation of `rigplane` or `pytest` in a step's `run`
    text is immediately preceded by `bash scripts/bench/guard.sh run -- `."""
    unguarded: list[str] = []
    for block in _step_blocks(_radio_job_text()):
        run = _run_text(block)
        if not run:
            continue
        for match in _UV_RUN_RE.finditer(run):
            if not run[: match.start()].endswith(GUARD_PREFIX):
                unguarded.append(
                    f"{_step_name(block)!r}: {match.group('cmd')} "
                    f"invocation not guarded: {match.group(0)!r}"
                )
    detail = "; ".join(unguarded)
    assert not unguarded, (
        f"radio-touching commands bypassing guard.sh run -- : {detail}"
    )
