"""Pin the radio-smoke receipt gate in ``publish.yml`` (MOR-3839).

Live radio smoke is a mechanical condition of a PyPI publish: the
``validate`` job must download ``radio-smoke-rx.json`` and
``radio-smoke-tx.json`` from the triggering release with the job's
``GITHUB_TOKEN`` and run ``scripts/release/check_radio_smoke.py`` against the
tag and the tagged commit *before* the expensive validation steps, so a
missing or red receipt turns the job red and the ``needs`` chain keeps the
PyPI upload from ever starting.

Text/regex pins like ``tests/test_ci_pytest_invocation.py`` and
``tests/test_bench_nightly_workflow.py``: this repo declares no YAML test
dependency, and the step bodies here are shell block scalars.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "publish.yml"
)

_VALIDATE_JOB_RE = re.compile(
    r"^  validate:\n(.*?)(?=^  [A-Za-z_][\w-]*:|\Z)", re.DOTALL | re.MULTILINE
)
_PUBLISH_JOB_RE = re.compile(
    r"^  publish:\n(.*?)(?=^  [A-Za-z_][\w-]*:|\Z)", re.DOTALL | re.MULTILINE
)
_STEP_START_RE = re.compile(r"^      - ", re.MULTILINE)
_PERMISSIONS_RE = re.compile(r"^permissions:\n((?:  .*\n)+)", re.MULTILINE)
_CONTINUATION_RE = re.compile(r"\\\n[ \t]*")


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _job_body(pattern: re.Pattern[str], name: str) -> str:
    text = _workflow_text()
    match = pattern.search(text)
    assert match is not None, f"publish.yml has no `{name}` job"
    return match.group(1)


def _validate_job() -> str:
    return _job_body(_VALIDATE_JOB_RE, "validate")


def _step_blocks(job_text: str) -> list[str]:
    starts = [match.start() for match in _STEP_START_RE.finditer(job_text)]
    assert starts, "the validate job has no steps"
    ends = [*starts[1:], len(job_text)]
    return [job_text[start:end] for start, end in zip(starts, ends)]


def _receipt_step() -> str:
    blocks = [
        block
        for block in _step_blocks(_validate_job())
        if "check_radio_smoke.py" in block
    ]
    assert len(blocks) == 1, (
        f"expected exactly one validate step invoking check_radio_smoke.py, "
        f"found {len(blocks)}"
    )
    return _CONTINUATION_RE.sub(" ", blocks[0])


def test_validate_job_checks_the_radio_smoke_receipts() -> None:
    step = _receipt_step()
    assert "gh release download \"$TAG\" -p 'radio-smoke-*.json'" in step, (
        "the receipts must come from the triggering release via gh release "
        f"download with the radio-smoke glob; step:\n{step}"
    )
    assert "scripts/release/check_radio_smoke.py check" in step
    assert '--tag "$TAG"' in step, "the checker must validate against the release tag"
    assert '--git-sha "$GITHUB_SHA"' in step, (
        "the checker must validate against the tagged commit ($GITHUB_SHA of "
        "the release event)"
    )
    assert "--rx" in step and "--tx" in step, "both fragments must be checked"


def test_receipt_step_uses_the_job_github_token_and_release_tag() -> None:
    step = _receipt_step()
    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in step, (
        "gh release download authenticates with the job's GITHUB_TOKEN — no "
        f"new secret; step:\n{step}"
    )
    assert "TAG: ${{ github.event.release.tag_name }}" in step, (
        "the tag must come from the triggering release event, not a ref guess"
    )


def test_receipt_step_runs_before_the_test_suite() -> None:
    body = _validate_job()
    assert "check_radio_smoke.py" in body and "name: Run tests" in body
    assert body.index("check_radio_smoke.py") < body.index("name: Run tests"), (
        "the receipt gate must fail fast, before the frontend/test matrix work"
    )


def test_needs_chain_unchanged() -> None:
    text = _workflow_text()
    assert re.search(r"^  build:\n    needs: validate\n", text, re.MULTILINE), (
        "build must still need validate"
    )
    assert re.search(r"^  publish:\n    needs: build\n", text, re.MULTILINE), (
        "publish must still need build"
    )


def test_validate_job_gains_no_environment_or_secret() -> None:
    body = _validate_job()
    assert not re.search(r"^    environment:", body, re.MULTILINE), (
        "the validate job must not gain an environment — only publish keeps "
        "the pypi environment"
    )
    publish = _job_body(_PUBLISH_JOB_RE, "publish")
    assert re.search(r"^    environment:\n      name: pypi", publish, re.MULTILINE), (
        "the publish job must keep its pypi environment"
    )


def test_permissions_unchanged() -> None:
    match = _PERMISSIONS_RE.search(_workflow_text())
    assert match is not None, "publish.yml has no top-level permissions block"
    granted = {line.strip() for line in match.group(1).splitlines()}
    assert granted == {"contents: read", "id-token: write"}, (
        f"release-asset download needs only contents: read; got {sorted(granted)}"
    )
