"""Tests for `.github/scripts/check-workflow-runs-on.py`."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".github/scripts/check-workflow-runs-on.py"


def run_checker(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo)],
        check=False,
        capture_output=True,
        text=True,
    )


def _indent(runs_on: str) -> str:
    """Continuation lines of a multi-line ``runs-on`` sit under the job's keys."""
    return runs_on.replace("\n", "\n            ")


def write_workflow(repo: Path, name: str, body: str) -> None:
    path = repo / ".github/workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def test_repo_workflows_pass() -> None:
    result = run_checker(REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_rejects_ubuntu_latest(tmp_path: Path) -> None:
    write_workflow(
        tmp_path,
        "bad.yml",
        """
        name: bad
        on: push
        jobs:
          oops:
            runs-on: ubuntu-latest
            steps:
              - run: echo no
        """,
    )
    result = run_checker(tmp_path)
    assert result.returncode == 1
    assert "ubuntu-latest" in result.stdout or "GitHub-hosted" in result.stdout


def test_rejects_legacy_lowercase_linux_pool(tmp_path: Path) -> None:
    write_workflow(
        tmp_path,
        "legacy.yml",
        """
        name: legacy
        on: push
        jobs:
          gate:
            runs-on: [self-hosted, linux, light]
            steps:
              - run: echo no
        """,
    )
    result = run_checker(tmp_path)
    assert result.returncode == 1
    assert "not an approved pool" in result.stdout


def test_accepts_approved_light_pool(tmp_path: Path) -> None:
    write_workflow(
        tmp_path,
        "ok.yml",
        """
        name: ok
        on: push
        jobs:
          gate:
            runs-on: [self-hosted, Linux, ARM64, light]
            steps:
              - run: echo yes
        """,
    )
    result = run_checker(tmp_path)
    assert result.returncode == 0, result.stdout


def test_dynamic_runs_on_requires_allowlist_comment(tmp_path: Path) -> None:
    write_workflow(
        tmp_path,
        "dyn.yml",
        """
        name: dyn
        on: workflow_call
        jobs:
          native:
            runs-on: ['${{ matrix.selector }}']
            steps:
              - run: echo no
        """,
    )
    result = run_checker(tmp_path)
    assert result.returncode == 1
    assert "runs-on-allowlist" in result.stdout


def test_allowlisted_dynamic_runs_on_passes(tmp_path: Path) -> None:
    write_workflow(
        tmp_path,
        "fleet.yml",
        """
        name: fleet
        on: workflow_call
        jobs:
          native:
            # runs-on-allowlist: matrix.selector resolves to registered fleet self-hosted runners (fleet-*), not GitHub-hosted.
            runs-on: ['${{ matrix.selector }}']
            steps:
              - run: echo yes
        """,
    )
    result = run_checker(tmp_path)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    "labels",
    [
        "[self-hosted, Linux, ARM64, gate]",
        "[self-hosted, Linux, ARM64, build]",
        "[self-hosted, Linux, X64, build]",
        "[self-hosted, macOS, ARM64, bench]",
    ],
)
def test_accepts_each_verified_pool(tmp_path: Path, labels: str) -> None:
    write_workflow(
        tmp_path,
        "pool.yml",
        f"""
        name: pool
        on: push
        jobs:
          job:
            runs-on: {labels}
            steps:
              - run: echo ok
        """,
    )
    assert run_checker(tmp_path).returncode == 0


HOSTED_BLOCK_LIST = """
    name: bad
    on: push
    jobs:
      oops:
        runs-on:
          - ubuntu-latest
        steps:
          - run: echo no
    """


@pytest.mark.parametrize("suffix", [".yml", ".yaml"])
def test_scans_both_workflow_extensions(tmp_path: Path, suffix: str) -> None:
    write_workflow(tmp_path, f"bad{suffix}", HOSTED_BLOCK_LIST)
    result = run_checker(tmp_path)
    assert result.returncode == 1
    assert "bad" + suffix in result.stdout


@pytest.mark.parametrize(
    "runs_on",
    [
        "runs-on:\n  - ubuntu-latest",
        "runs-on: # header comment\n  - self-hosted\n  - linux",
        "runs-on: ubuntu-latest # trailing",
        "runs-on:\n  labels: [ubuntu-latest]",
        "runs-on:\n  group: hosted-pool",
        "runs-on: self-hosted",
        "runs-on: [self-hosted]",
    ],
)
def test_rejects_every_unapproved_runs_on_shape(tmp_path: Path, runs_on: str) -> None:
    write_workflow(
        tmp_path,
        "shape.yml",
        f"""
        name: shape
        on: push
        jobs:
          job:
            {_indent(runs_on)}
            steps:
              - run: echo no
        """,
    )
    assert run_checker(tmp_path).returncode == 1


@pytest.mark.parametrize(
    "runs_on",
    [
        "runs-on: # header comment\n  - self-hosted\n  - Linux\n  - ARM64\n  - light",
        "runs-on: [self-hosted, Linux, ARM64, light] # trailing comment",
        "runs-on:\n  labels: [self-hosted, Linux, X64, build]",
    ],
)
def test_accepts_approved_pool_in_any_yaml_shape(tmp_path: Path, runs_on: str) -> None:
    write_workflow(
        tmp_path,
        "shape.yml",
        f"""
        name: shape
        on: push
        jobs:
          job:
            {_indent(runs_on)}
            steps:
              - run: echo ok
        """,
    )
    result = run_checker(tmp_path)
    assert result.returncode == 0, result.stdout


def test_job_without_runs_on_fails_but_a_reusable_call_passes(tmp_path: Path) -> None:
    write_workflow(
        tmp_path,
        "reuse.yml",
        """
        name: reuse
        on: push
        jobs:
          call:
            uses: ./.github/workflows/other.yml
        """,
    )
    assert run_checker(tmp_path).returncode == 0
    write_workflow(
        tmp_path,
        "none.yml",
        """
        name: none
        on: push
        jobs:
          job:
            steps:
              - run: echo no
        """,
    )
    result = run_checker(tmp_path)
    assert result.returncode == 1
    assert "no runs-on" in result.stdout


def test_invalid_yaml_fails(tmp_path: Path) -> None:
    write_workflow(tmp_path, "broken.yml", "jobs: [unclosed")
    result = run_checker(tmp_path)
    assert result.returncode == 1
    assert "not valid YAML" in result.stdout
