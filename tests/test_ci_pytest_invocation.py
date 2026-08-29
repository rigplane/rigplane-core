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

Honest limit: this only proves the flag string is absent from the pytest
invocation text in the three tracked workflow files. It says nothing about
a future workflow file this repo might add, or about a flag with equivalent
effect expressed a different way (e.g. `-k 'not integration'`, a marker
deselect, or a `pyproject.toml` `addopts` change) — those would need a
matching assertion added here, or they would drift the same way this one
did.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"

WORKFLOW_FILES = ("quick.yml", "full.yml", "publish.yml")

_PYTEST_INVOCATION_RE = re.compile(r"uv run pytest tests/[^\n]*")


def _pytest_invocations(workflow_name: str) -> list[str]:
    text = (WORKFLOWS_DIR / workflow_name).read_text()
    invocations = _PYTEST_INVOCATION_RE.findall(text)
    assert invocations, (
        f"no `uv run pytest tests/` invocation found in {workflow_name} — "
        "update this pin if the workflow's test step was restructured"
    )
    return invocations


def test_no_workflow_excludes_integration_tests() -> None:
    for workflow_name in WORKFLOW_FILES:
        for invocation in _pytest_invocations(workflow_name):
            assert "--ignore=tests/integration" not in invocation, (
                f"{workflow_name} excludes tests/integration from its pytest "
                "invocation again — this is the same shape of incident that "
                "left tests/integration/ uncollected and red for nine days "
                "(commit 6bdb5846, fixed by #2808/#2810)"
            )
