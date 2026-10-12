"""Pin the honest verdict of the bench-nightly radio jobs.

`.github/workflows/bench-nightly.yml` runs one matrix job per bench radio.
Run 38218526102 concluded success while its IC-7300 and FTX-1 jobs failed,
because the `radio` job carried `continue-on-error: true`; "skipped: busy"
and "skipped: not configured" only printed a line and ended green, as did a
dry run.

These tests read the workflow as plain text with indentation-anchored
regular expressions instead of importing `yaml`, like the sibling
`tests/test_ci_pytest_invocation.py`, so no new dependency was added.
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

GUARDED_STEP_NAMES = ("Validate", "Hardware pytest subset", "rigplane diagnose")

_JOB_START_RE = re.compile(r"^  radio:\n", re.MULTILINE)
_NEXT_JOB_RE = re.compile(r"^  [A-Za-z_][\w-]*:", re.MULTILINE)
_STEP_START_RE = re.compile(r"^      - ", re.MULTILINE)
_NAME_RE = re.compile(r"^      (?:- |  )name: (.*)$", re.MULTILINE)
_CONTINUE_RE = re.compile(r"^      (?:- |  )continue-on-error: (.*)$", re.MULTILINE)
_IF_RE = re.compile(r"^      (?:- |  )if: (.*)$", re.MULTILINE)
_RUN_KEY_RE = re.compile(r"^      (?:- |  )run: ?(.*)$", re.MULTILINE)
_RUN_BLOCK_HEADER_RE = re.compile(r"^[|>][-+]?[0-9]?(?:\s+#.*)?$")
_INVOCATION_RE = re.compile(
    r"uv\s+run(?P<opts>(?:\s+-\S+(?:\s+[^\s]+)?)*)\s+"
    r"(?P<tool>rigplane|pytest|python\s+-m\s+rigplane)\b"
    r"|(?P<bare_python>(?<![\w/.-])python(?:3(?:\.\d+)?)?\s+-m\s+rigplane)\b"
    r"|uvx(?P<uvx_opts>(?:\s+-\S+(?:\s+[^\s]+)?)*)\s+(?P<uvx_tool>rigplane)\b"
    r"|(?P<venv_tool>\.venv/bin/(?:rigplane|pytest))\b"
)
_CONTINUATION_RE = re.compile(r"\\\n[ \t]*")


def _radio_job_text() -> str:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    start = _JOB_START_RE.search(text)
    assert start is not None, "the radio job is missing from bench-nightly.yml"
    rest = text[start.end() :]
    following = _NEXT_JOB_RE.search(rest)
    return rest[: following.start()] if following else rest


def _step_blocks(job_text: str) -> list[str]:
    starts = [match.start() for match in _STEP_START_RE.finditer(job_text)]
    assert starts, "the radio job has no steps"
    ends = [*starts[1:], len(job_text)]
    return [job_text[start:end] for start, end in zip(starts, ends)]


def _workflow_step_blocks() -> list[str]:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    starts = [match.start() for match in _STEP_START_RE.finditer(text)]
    assert starts, "bench-nightly.yml has no steps"
    ends = [*starts[1:], len(text)]
    return [text[start:end] for start, end in zip(starts, ends)]


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


def _fold_block(body: list[str]) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in body:
        if line == "":
            if current:
                paragraphs.append(" ".join(current))
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return "\n".join(paragraphs)


def _run_text(block: str) -> str | None:
    match = _RUN_KEY_RE.search(block)
    if match is None:
        return None
    value = match.group(1).strip()
    name = _step_name(block)
    if not value:
        raise AssertionError(
            f"{name!r}: empty `run:` value in bench-nightly.yml — the parser "
            "understands only a single-line command or a `|`/`>` block"
        )
    if value[0] not in "|>":
        return value
    if not _RUN_BLOCK_HEADER_RE.match(value):
        raise AssertionError(
            f"{name!r}: unrecognized `run:` block header {value!r} in "
            "bench-nightly.yml — teach _run_text this header"
        )
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
    if value[0] == ">":
        return _fold_block(body)
    return "\n".join(body)


def _logical_shell_text(run: str) -> str:
    kept = [line for line in run.splitlines() if line.strip()[:1] != "#"]
    return _CONTINUATION_RE.sub("", "\n".join(kept))


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
    """Every radio-capable invocation in every job's step `run` text goes
    through `bash scripts/bench/guard.sh run -- `.

    Radio-capable shapes: `uv run` of `rigplane`, `pytest`, or
    `python -m rigplane`; bare `python -m rigplane`; `uvx` of `rigplane`;
    `.venv/bin/rigplane` and `.venv/bin/pytest`. Every job in the workflow
    is scanned, not just `radio`. A `run:` block with any `|`/`>` header is
    parsed (`>` folds lines with spaces); backslash-newline continuations
    are joined before matching; full-line `#` comments are ignored. A `run:`
    value the parser does not understand fails loudly, and the check fails
    unless guarded invocations were seen in the Validate, Hardware pytest
    subset, and rigplane diagnose steps, so a parser that reads nothing
    cannot pass.
    """
    unguarded: list[str] = []
    guarded_steps: set[str] = set()
    for block in _workflow_step_blocks():
        run = _run_text(block)
        if not run:
            continue
        logical = _logical_shell_text(run)
        for match in _INVOCATION_RE.finditer(logical):
            invocation = match.group(0).strip()
            if logical[: match.start()].endswith(GUARD_PREFIX):
                guarded_steps.add(_step_name(block) or "<unnamed>")
            else:
                unguarded.append(f"{_step_name(block)!r}: {invocation!r} bypasses the guard")
    assert not unguarded, (
        "radio-touching commands bypassing guard.sh run -- : " + "; ".join(unguarded)
    )
    missing = [name for name in GUARDED_STEP_NAMES if name not in guarded_steps]
    assert not missing, (
        f"no guarded invocation seen in {missing} — a parser that reads "
        "nothing would pass silently"
    )
