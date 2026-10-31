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

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

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


def _receipt_step_block() -> str:
    blocks = [
        block
        for block in _step_blocks(_validate_job())
        if "check_radio_smoke.py" in block
    ]
    assert len(blocks) == 1, (
        f"expected exactly one validate step invoking check_radio_smoke.py, "
        f"found {len(blocks)}"
    )
    return blocks[0]


def _receipt_step() -> str:
    return _CONTINUATION_RE.sub(" ", _receipt_step_block())


_REPO_ROOT = WORKFLOW_PATH.parent.parent.parent
_RUN_BLOCK_START_RE = re.compile(r"^        run: \|\n", re.MULTILINE)
_RUN_BODY_INDENT = "          "


def _receipt_run_script() -> str:
    """The verbatim shell body of the receipt step's literal ``run: |`` block."""
    block = _receipt_step_block()
    match = _RUN_BLOCK_START_RE.search(block)
    assert match is not None, (
        f"the receipt step must keep a literal-block `run: |` script; step:\n{block}"
    )
    lines: list[str] = []
    for line in block[match.end() :].splitlines():
        if line.strip() and not line.startswith(_RUN_BODY_INDENT):
            break
        lines.append(line[len(_RUN_BODY_INDENT) :] if line.strip() else "")
    script = "\n".join(lines) + "\n"
    assert "check_radio_smoke.py check" in script, (
        f"the receipt step must invoke the checker; extracted script:\n{script}"
    )
    return script


def test_validate_job_checks_the_radio_smoke_receipts() -> None:
    step = _receipt_step()
    assert "gh release download \"$TAG\" -p 'radio-smoke-*.json'" in step, (
        "the receipts must come from the triggering release via gh release "
        f"download with the radio-smoke glob; step:\n{step}"
    )
    assert "scripts/release/check_radio_smoke.py check" in step
    assert (
        "uv run --no-project python scripts/release/check_radio_smoke.py check" in step
    ), (
        "the checker needs Python >= 3.11 (tomllib) but the runners' system "
        "python3 is 3.10, so the gate must run on the matrix interpreter "
        f"(job-level UV_PYTHON pins it); step:\n{step}"
    )
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


def test_receipt_step_passes_the_repo_to_the_checker() -> None:
    step = _receipt_step()
    assert '--repo "$GITHUB_WORKSPACE"' in step, (
        "the checker proves a version-only rc-to-final diff inside the "
        f"checked-out repo; step:\n{step}"
    )


def test_validate_checkout_has_full_history_with_tags() -> None:
    body = _validate_job()
    assert "fetch-depth: 0" in body, (
        "the receipt gate resolves the rc tag and walks the candidate's "
        "ancestry, so the validate checkout needs full history with tags, "
        "not the depth-2 checkout the test matrix needs"
    )


def test_receipt_step_runs_before_the_test_suite() -> None:
    body = _validate_job()
    assert "check_radio_smoke.py" in body and "name: Run tests" in body
    assert body.index("check_radio_smoke.py") < body.index("name: Run tests"), (
        "the receipt gate must fail fast, before the frontend/test matrix work"
    )


def test_receipt_step_runs_on_the_matrix_interpreter_after_setup_before_deps() -> None:
    """The gate needs the matrix interpreter, but must still precede the
    expensive steps: it runs after `Set up Python` (which installs that
    interpreter) and before `Install dependencies` (MOR-3998)."""
    body = _validate_job()
    markers = (
        "name: Set up Python",
        "check_radio_smoke.py",
        "name: Install dependencies",
        "name: Run tests",
    )
    for marker in markers:
        assert marker in body, f"the validate job lost its `{marker}` step"
    positions = [body.index(marker) for marker in markers]
    assert positions == sorted(positions), (
        "the receipt gate must run after `Set up Python` and before "
        "`Install dependencies` and the test suite"
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


# --- End-to-end pins: run the step's REAL shell body (MOR-3839 pre-review) ---
#
# Text pins alone cannot see a deleted `sys.exit(main())` in the checker or a
# dropped checker invocation: the workflow's checker command would exit 0
# without validating anything. These tests extract the literal run: script
# from publish.yml and execute it with bash, faking only the GitHub download
# (a stub `gh` on PATH) and the `uv run` launcher (the test interpreter
# plays the matrix interpreter's role), and invoking the real checker as a
# subprocess, so the process exit status — the behavior the gate exists
# for — is asserted.

TAG = "v3.1.0"
VERSION = "3.1.0"
SHA = "0123456789abcdef0123456789abcdef01234567"
GENERATED_AT = "2026-10-01T12:00:00+00:00"

_VALID_RX: dict[str, object] = {
    "schema": 1,
    "kind": "rx",
    "core_version": VERSION,
    "git_sha": SHA,
    "verdict": "PASS",
    "generated_at": GENERATED_AT,
    "radios": {"IC-7610": "PASS", "IC-705": "PASS"},
}
_VALID_TX: dict[str, object] = {
    "schema": 1,
    "kind": "tx",
    "core_version": VERSION,
    "git_sha": SHA,
    "verdict": "PASS",
    "generated_at": GENERATED_AT,
    "sequences": {
        "single": "PASS",
        "rekey_150ms": "PASS",
        "rekey_immediate": "PASS",
        "double_cycle": "PASS",
        "force_off_inflight": "PASS",
    },
    "stuck_tx": False,
    "release_debt": False,
    "ended_rx": True,
}

_STUB_GH = """\
#!/usr/bin/env bash
# Fake `gh release download`: copies the test's receipt files into --dir, or
# fails the download when STUB_GH_DOWNLOAD_FAILS=1.
set -u
if [ "${STUB_GH_DOWNLOAD_FAILS:-0}" = "1" ]; then
  echo "stub gh: release download failed" >&2
  exit 1
fi
dir=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dir) dir="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [ -z "$dir" ]; then
  echo "stub gh: --dir missing" >&2
  exit 2
fi
cp "$STUB_RECEIPTS_DIR"/* "$dir"/
"""


def _write_receipts(directory: Path, receipts: dict[str, bytes]) -> None:
    directory.mkdir()
    for name, content in receipts.items():
        (directory / name).write_bytes(content)


def _valid_receipts() -> dict[str, bytes]:
    return {
        "radio-smoke-rx.json": json.dumps(_VALID_RX).encode(),
        "radio-smoke-tx.json": json.dumps(_VALID_TX).encode(),
    }


def _run_receipt_step(
    tmp_path: Path,
    *,
    receipts: dict[str, bytes],
    download_fails: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the real publish.yml receipt-step shell body with a stub gh."""
    script = _receipt_run_script()
    # CI runs the checker through `uv run --no-project python` on the matrix
    # interpreter (the runners' system python3 is 3.10, no tomllib); locally
    # the test interpreter plays that role (MOR-3998).
    script = script.replace("uv run --no-project python", shlex.quote(sys.executable))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(_STUB_GH, encoding="utf-8")
    gh.chmod(0o755)
    receipts_dir = tmp_path / "receipts"
    _write_receipts(receipts_dir, receipts)
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "TAG": TAG,
            "GITHUB_SHA": SHA,
            # The runner always sets GITHUB_WORKSPACE; the receipt step
            # passes it as --repo, so the harness mirrors it (MOR-3998).
            "GITHUB_WORKSPACE": str(_REPO_ROOT),
            "GH_TOKEN": "stub-token",
            "STUB_RECEIPTS_DIR": str(receipts_dir),
            "STUB_GH_DOWNLOAD_FAILS": "1" if download_fails else "0",
        }
    )
    return subprocess.run(
        ["bash", "-c", script],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_receipt_step_shell_body_passes_for_valid_receipts(tmp_path: Path) -> None:
    completed = _run_receipt_step(tmp_path, receipts=_valid_receipts())
    assert completed.returncode == 0, (
        f"valid receipts must let the step pass; "
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    assert "radio smoke receipts OK" in completed.stdout, (
        f"the real checker subprocess must report success; stdout:\n{completed.stdout}"
    )


def _receipts_with(**patches: object) -> dict[str, bytes]:
    """Valid receipts with dotted mutations, e.g. tx__verdict="FAIL"."""
    rx = dict(_VALID_RX)
    tx = dict(_VALID_TX)
    for dotted, value in patches.items():
        fragment, _, key = dotted.partition("__")
        target = {"rx": rx, "tx": tx}[fragment]
        if value is ...:
            target.pop(key, None)
        else:
            target[key] = value
    return {
        "radio-smoke-rx.json": json.dumps(rx).encode(),
        "radio-smoke-tx.json": json.dumps(tx).encode(),
    }


_FAILURE_CASES: dict[str, dict[str, object]] = {
    "download-failure": {"download_fails": True},
    "missing-receipt": {
        "receipts": {"radio-smoke-rx.json": json.dumps(_VALID_RX).encode()}
    },
    "malformed-receipt": {
        "receipts": {
            "radio-smoke-rx.json": b"{not json",
            "radio-smoke-tx.json": json.dumps(_VALID_TX).encode(),
        }
    },
    "mismatched-version": {"receipts": _receipts_with(rx__core_version="3.2.0")},
    "mismatched-sha": {"receipts": _receipts_with(tx__git_sha="f" * 40)},
    "failed-smoke-result": {"receipts": _receipts_with(tx__verdict="FAIL")},
}


@pytest.mark.parametrize("case_id", sorted(_FAILURE_CASES))
def test_receipt_step_shell_body_fails_closed(tmp_path: Path, case_id: str) -> None:
    """Every red input must turn the step's process exit status non-zero.

    This is the pin that bites when the checker's ``sys.exit(main())`` entry
    point or the checker invocation in the step is removed: the workflow's
    checker command would then exit 0 without validating anything.
    """
    kwargs = dict(_FAILURE_CASES[case_id])
    receipts = kwargs.pop("receipts", None) or _valid_receipts()
    completed = _run_receipt_step(tmp_path, receipts=receipts, **kwargs)
    assert completed.returncode != 0, (
        f"{case_id}: the step must fail; "
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    assert "radio smoke receipts OK" not in completed.stdout
