"""Pins that no workflow's pytest invocation excludes `tests/integration`.

Nine tests under `tests/integration/` were red for nine days after commit
`6bdb5846` and nobody was told, because `--ignore=tests/integration` on the
pytest invocation in all three workflows (`quick.yml`, `full.yml`,
`publish.yml`) meant nothing in CI ever collected that directory. #2808
fixed the tests; #2810 removed the flag from all three workflows. That fixes
the immediate incident but leaves nothing that turns red if the flag is ever
reintroduced — this test is that guard.

Same regex-extraction approach as `test_ci_path_filters.py`: this repo has
no YAML-parsing dependency declared for tests to use (PyYAML is present only
transitively, via a docs tool), and the `run:` step bodies here are shell
script block scalars, not structure a workflow schema exposes as data. A
regex anchored on the known `uv run pytest tests/` invocation is simpler and
does not risk that dependency disappearing.

`quick.yml` and `full.yml` write this invocation as a shell line
continuation (`... \\` then `| tee pytest-output.txt` on the next physical
line) — exactly where a flag would naturally land if someone wrapped the
command further. Backslash-newline continuations are folded to a single
space before matching so a flag placed on a continuation line is not
invisible to the pin.

Honest limit: this proves the flag string is absent from the pytest
invocation text in the three tracked workflow files, continuation lines
included. It says nothing about a future workflow file this repo might add,
a different invocation shape (e.g. `python -m pytest`, a different working
directory, or an invocation built from a shell variable), or a flag with
equivalent effect expressed a different way (e.g. `-k 'not integration'`, a
marker deselect, or a `pyproject.toml` `addopts` change) — those would need
a matching assertion added here, or they would drift the same way this one
did.

Also pins that the `Run tests` job in each of those three workflows checks
out with `fetch-depth` of at least 2.

Also pins the `-m` marker selection. `pyproject.toml` `addopts` deselects the
`slow` and `benchmark` tests, and a `-m` on the command line replaces that
expression. `publish.yml`, which runs when a release is published, passes
`-m 'not benchmark'`; `quick.yml` passes no `-m`; `full.yml` passes one only
when `inputs.rc` is true. `full.yml` writes that choice as one
`${{ inputs.rc && '...' || '' }}` expression, which `_marker_selection`
evaluates for `rc` true, false and unset instead of matching its text. These
pins read the `uv run pytest tests/` line, so a `-m` on a later physical line
of a `run: >` block, such as `quick.yml`'s web-subset step, is not seen.
"""

from __future__ import annotations

import re
import shlex
import tomllib
from pathlib import Path

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"

WORKFLOW_FILES = ("quick.yml", "full.yml", "publish.yml")

_PYTEST_INVOCATION_RE = re.compile(r"uv run pytest tests/[^\n]*")

# All three workflows name the pytest step "Run tests" at the same 6-space
# step indent. Captures everything from that step's `name:` line up to (not
# including) the next step at the same indent, or end of file.
_RUN_TESTS_STEP_RE = re.compile(
    r"^      - name: Run tests\n(.*?)(?=^      - name:|\Z)",
    re.DOTALL | re.MULTILINE,
)

# Job entries are `  name:` keys whose bodies run to the next sibling key.
# Top-level `on:` keys share that shape but never contain `Run tests`.
_JOB_RE = re.compile(
    r"^  ([A-Za-z_][\w-]*):\n(.*?)(?=^  [A-Za-z_][\w-]*:|\Z)",
    re.DOTALL | re.MULTILINE,
)

# The checkout step inside a job, from its `uses:` line to the next step.
_CHECKOUT_STEP_RE = re.compile(
    r"^      - uses: actions/checkout@.*?(?=^      - |\Z)",
    re.DOTALL | re.MULTILINE,
)

_FETCH_DEPTH_RE = re.compile(r"fetch-depth:\s*(\d+)")

PYPROJECT = WORKFLOWS_DIR.parent.parent / "pyproject.toml"

# The one expression shape `full.yml` uses to choose the marker flags:
# `${{ inputs.rc && '<flags when true>' || '<flags otherwise>' }}`.
_RC_GATE_RE = re.compile(
    r"\$\{\{\s*inputs\.rc\s*&&\s*'(?P<on>(?:[^']|'')*)'"
    r"\s*\|\|\s*'(?P<off>(?:[^']|'')*)'\s*\}\}"
)

# The body of the `rc` input under `workflow_dispatch:` in full.yml.
_RC_INPUT_RE = re.compile(
    r"^  workflow_dispatch:\n    inputs:\n      rc:\n((?:        .*\n)+)",
    re.MULTILINE,
)


def _pytest_invocations(workflow_name: str) -> list[str]:
    text = (WORKFLOWS_DIR / workflow_name).read_text()
    # Fold shell line continuations first: `... \` + newline is one physical
    # command, and a flag added on the continuation line would otherwise be
    # past the `[^\n]*` boundary and invisible to this pin.
    folded_text = text.replace("\\\n", " ")
    invocations = _PYTEST_INVOCATION_RE.findall(folded_text)
    assert invocations, (
        f"no `uv run pytest tests/` invocation found in {workflow_name} — "
        "update this pin if the workflow's test step was restructured"
    )
    return invocations


def _run_tests_step_body(workflow_name: str) -> str:
    text = (WORKFLOWS_DIR / workflow_name).read_text()
    match = _RUN_TESTS_STEP_RE.search(text)
    assert match, (
        f"no `Run tests` step found in {workflow_name} — update this pin if "
        "the step was renamed or restructured"
    )
    return match.group(1)


def _marker_selection(command: str, *, rc: bool | None = None) -> list[str]:
    """The `-m` expressions `command` passes when `inputs.rc` is `rc`."""
    rendered = _RC_GATE_RE.sub(
        lambda m: (m["on"] if rc else m["off"]).replace("''", "'"), command
    )
    assert "${{" not in rendered, (
        f"an expression this pin does not evaluate is in {command!r} — "
        "extend `_RC_GATE_RE` if the workflow gates the selection another way"
    )
    tokens = shlex.split(rendered)
    selected = []
    for index, token in enumerate(tokens):
        if token == "-m":
            selected.append(tokens[index + 1])
        elif token.startswith("-m") and not token.startswith("--"):
            selected.append(token[2:])
    return selected


def test_no_workflow_excludes_integration_tests() -> None:
    for workflow_name in WORKFLOW_FILES:
        for invocation in _pytest_invocations(workflow_name):
            assert "--ignore=tests/integration" not in invocation, (
                f"{workflow_name} excludes tests/integration from its pytest "
                "invocation again — this is the same shape of incident that "
                "left tests/integration/ uncollected and red for nine days "
                "(commit 6bdb5846, fixed by #2808/#2810)"
            )
        # Checked separately, over the whole `Run tests` step body rather
        # than only the matched invocation line: a YAML folded block scalar
        # (`run: >`) puts the flag on its own physical line and folds it
        # into the shell command only at YAML-parse time, so it would never
        # appear inside `[^\n]*` above yet would still reach the shell.
        step_body = _run_tests_step_body(workflow_name)
        assert "--ignore=tests/integration" not in step_body, (
            f"{workflow_name}'s `Run tests` step body contains "
            "--ignore=tests/integration outside the matched invocation "
            "line — likely a folded (`run: >`) or otherwise reshaped "
            "block scalar smuggling the flag back in"
        )


def _run_tests_job_body(workflow_name: str) -> str:
    text = (WORKFLOWS_DIR / workflow_name).read_text()
    bodies = [body for _, body in _JOB_RE.findall(text) if "name: Run tests" in body]
    assert bodies, (
        f"no job with a `Run tests` step found in {workflow_name} — update "
        "this pin if the workflow was restructured"
    )
    return bodies[0]


def test_run_tests_job_checks_out_at_least_two_commits() -> None:
    for workflow_name in WORKFLOW_FILES:
        job_body = _run_tests_job_body(workflow_name)
        checkouts = _CHECKOUT_STEP_RE.findall(job_body)
        assert checkouts, (
            f"no actions/checkout step in {workflow_name}'s `Run tests` job"
        )
        for checkout in checkouts:
            depth_match = _FETCH_DEPTH_RE.search(checkout)
            depth = int(depth_match.group(1)) if depth_match else None
            assert depth is not None and depth >= 2, (
                f"{workflow_name}'s `Run tests` job checkout has fetch-depth "
                f"{depth!r} (None is the checkout default of 1) — the suite "
                "includes a monotonic comparison against the parent commit, "
                "which needs the parent in the checkout"
            )


def test_pyproject_deselects_slow_tests_by_default() -> None:
    options = tomllib.loads(PYPROJECT.read_text())["tool"]["pytest"]["ini_options"]
    assert any("not slow" in m for m in _marker_selection(options["addopts"])), (
        "pyproject.toml addopts no longer deselects `slow` tests, so every "
        "build that passes no `-m` would run them"
    )


def test_quick_passes_no_marker_selection() -> None:
    for invocation in _pytest_invocations("quick.yml"):
        assert _marker_selection(invocation) == [], (
            "quick.yml passes its own `-m`, replacing the `addopts` default "
            f"that deselects `slow` tests: {invocation!r}"
        )


def test_publish_validate_runs_the_slow_tests() -> None:
    for invocation in _pytest_invocations("publish.yml"):
        assert _marker_selection(invocation) == ["not benchmark"], (
            "publish.yml must pass `-m 'not benchmark'`, which replaces the "
            f"`addopts` default that deselects `slow` tests: {invocation!r}"
        )


def test_full_runs_the_slow_tests_only_for_an_rc_dispatch() -> None:
    for invocation in _pytest_invocations("full.yml"):
        assert _marker_selection(invocation, rc=True) == ["not benchmark"], (
            f"an RC dispatch of full.yml does not select `-m 'not benchmark'`: {invocation!r}"
        )
        for rc in (False, None):
            assert _marker_selection(invocation, rc=rc) == [], (
                f"full.yml selects markers when `rc` is {rc!r}: {invocation!r}"
            )


def test_full_declares_a_boolean_rc_input_defaulting_to_false() -> None:
    match = _RC_INPUT_RE.search((WORKFLOWS_DIR / "full.yml").read_text())
    assert match, "no `rc` input under `workflow_dispatch` in full.yml"
    body = match.group(1)
    assert re.search(r"^\s+type:\s*boolean\s*$", body, re.MULTILINE), body
    assert re.search(r"^\s+default:\s*false\s*$", body, re.MULTILINE), body
