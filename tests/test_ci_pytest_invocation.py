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
"""

from __future__ import annotations

import re
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
