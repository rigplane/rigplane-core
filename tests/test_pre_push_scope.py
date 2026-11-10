"""The pre-push quick gate's decisions (MOR-4372).

`scripts/local-ci.sh --pre-push` must run what CI's `quick` run would reject, before the push:
which tests to run, which legs (Python / frontend / strict web type check) a diff selects, and
whether the ruff on the test host is the one uv.lock pins. These tests pin the selection on a
small git repository built in `tmp_path`; the leg choice goes through CI's own classifier
(`.github/scripts/classify-quick-paths.py`), never a second copy of its rules.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("pre_push_scope", ROOT / "scripts/pre_push_scope.py")
assert _spec is not None and _spec.loader is not None
scope = importlib.util.module_from_spec(_spec)
sys.modules["pre_push_scope"] = scope  # @dataclass looks the module up by name
_spec.loader.exec_module(scope)

LOCK = '[[package]]\nname = "ruff"\nversion = "0.15.2"\nsource = { registry = "x" }\n'


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    _write(r, ".github/workflows/quick.yml", "name: Tests (quick)\n")
    _write(r, "src/rigplane/audio/bridge.py", "X = 1\n")
    _write(r, "src/rigplane/web/server.py", "Y = 1\n")
    _write(r, "frontend/src/lib/meter.ts", "export const m = 1;\n")
    _write(r, "tests/test_quick_workflow.py", 'PIN = ".github/workflows/quick.yml"\n')
    _write(r, "tests/test_bridge.py", "from rigplane.audio import bridge\n")
    _write(r, "tests/test_meter_asset.py", 'SRC = "frontend/src/lib/meter.ts"\n')
    _write(r, "tests/test_unrelated.py", "def test_x():\n    pass\n")
    _write(r, "tests/conftest.py", "# mentions __init__.py and conftest.py on purpose\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")
    return r


def _changes(repo: Path) -> list:
    return scope.changed_files(repo, "origin/main")


def _related(repo: Path) -> list[str]:
    return scope.related_tests(repo, _changes(repo))


def test_changed_files_sees_committed_unstaged_deleted_and_untracked(repo: Path) -> None:
    _write(repo, "src/rigplane/audio/bridge.py", "X = 2\n")
    _git(repo, "commit", "-qam", "edit")
    _git(repo, "rm", "-q", "tests/test_unrelated.py")
    _write(repo, "scripts/new.sh", "echo\n")  # untracked
    got = {(c.status, c.path) for c in _changes(repo)}
    assert got == {
        ("M", "src/rigplane/audio/bridge.py"),
        ("D", "tests/test_unrelated.py"),
        ("A", "scripts/new.sh"),
    }


def test_a_test_that_names_the_changed_source_module_runs(repo: Path) -> None:
    _write(repo, "src/rigplane/audio/bridge.py", "X = 2\n")
    assert _related(repo) == ["tests/test_bridge.py"]


def test_a_test_that_pins_a_workflow_file_runs_when_that_file_changes(repo: Path) -> None:
    # the dependency pytest cannot see: the test reads the workflow as data
    _write(repo, ".github/workflows/quick.yml", "name: changed\n")
    assert _related(repo) == ["tests/test_quick_workflow.py"]


def test_a_test_that_reads_a_frontend_file_runs_when_it_changes(repo: Path) -> None:
    _write(repo, "frontend/src/lib/meter.ts", "export const m = 2;\n")
    assert _related(repo) == ["tests/test_meter_asset.py"]


def test_a_changed_test_file_runs_and_a_deleted_one_does_not(repo: Path) -> None:
    _write(repo, "tests/test_unrelated.py", "def test_x():\n    assert True\n")
    _git(repo, "rm", "-q", "tests/test_bridge.py")
    assert _related(repo) == ["tests/test_unrelated.py"]


def test_generic_file_names_do_not_select_the_whole_suite(repo: Path) -> None:
    _write(repo, "src/rigplane/audio/__init__.py", "")
    _write(repo, "tests/conftest.py", "# changed\n")
    # conftest.py and __init__.py are named in tests/conftest.py's own text, not in a test
    assert _related(repo) == []


def test_no_change_selects_nothing(repo: Path) -> None:
    assert _changes(repo) == []
    assert _related(repo) == []
    assert scope.legs([]) == []


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["frontend/src/lib/meter.ts"], ["frontend"]),
        (["src/rigplane/audio/bridge.py"], ["python"]),
        (["tests/test_bridge.py"], ["python"]),
        (["pyproject.toml"], ["python"]),
        # src/rigplane/web crosses the boundary: CI runs core AND frontend and the strict check
        (["src/rigplane/web/server.py"], ["python", "frontend", "mypy-web"]),
        (["src/rigplane/audio/bridge.py", "frontend/src/lib/meter.ts"], ["python", "frontend", "mypy-web"]),
        (["docs/guide/web-ui.md"], ["python"]),  # prose that a test reads is a core input
        (["docs/guide/other.md"], []),
        (["README.md"], []),
        (["scripts/pre_push_scope.py"], []),
        (["tests/test_bridge.py", "frontend/package-lock.json"], ["python", "frontend", "mypy-web"]),
    ],
)
def test_the_legs_follow_ci_classification(paths: list[str], expected: list[str]) -> None:
    changes = [scope.Change("M", p) for p in paths]
    assert scope.legs(changes) == expected


def test_a_deleted_path_still_selects_its_leg() -> None:
    # --no-renames: the deleted side of a rename must classify (MOR-2589 in the classifier)
    assert scope.legs([scope.Change("D", "frontend/src/lib/meter.ts")]) == ["frontend"]


def test_the_ruff_pin_is_the_version_in_uv_lock() -> None:
    assert scope.ruff_lock_version(LOCK) == "0.15.2"
    other = '[[package]]\nname = "ruffian"\nversion = "9.9"\n\n' + LOCK
    assert scope.ruff_lock_version(other) == "0.15.2"
    with pytest.raises(ValueError, match="no ruff"):
        scope.ruff_lock_version('[[package]]\nname = "pytest"\nversion = "8"\n')


def test_a_stale_host_ruff_is_reported_with_the_fix() -> None:
    assert scope.ruff_version_problem("0.15.2", "ruff 0.15.2") is None
    problem = scope.ruff_version_problem("0.15.2", "ruff 0.14.0 (abc 2025-01-01)")
    assert problem is not None
    assert "0.14.0" in problem and "0.15.2" in problem and "uv sync" in problem
    assert scope.ruff_version_problem("0.15.2", "") is not None


def test_the_command_line_prints_what_the_script_runs(repo: Path, tmp_path: Path) -> None:
    _write(repo, "src/rigplane/audio/bridge.py", "X = 2\n")
    _write(repo, "uv.lock", LOCK)

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/pre_push_scope.py"), *args],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )

    assert run("related", "--base", "origin/main").stdout.split() == ["tests/test_bridge.py"]
    assert run("legs", "--base", "origin/main").stdout.split() == ["python"]
    assert run("ruff-pin", "ruff 0.15.2").returncode == 0
    stale = run("ruff-pin", "ruff 0.14.0")
    assert stale.returncode == 1 and "0.15.2" in stale.stdout
