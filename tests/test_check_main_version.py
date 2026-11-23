"""Tests for ``scripts/release/check_main_version.py`` (MOR-4714).

After a release the merge-back must move main's version to the next
development version (``X.Y.(Z+1).dev0`` after ``vX.Y.Z``): a Core server
built from main reports its version in ``/api/v1/info``, support reports and
Sentry release names, and main kept saying ``3.2.0rc11`` after ``v3.3.0``
shipped. The checker exits 0 only when the checked-out tree's
``pyproject.toml`` version is strictly newer than the newest ``v*`` tag in
version order (rc/b/dev suffixes included, numeric — not lexicographic), and
the project's own ``uv.lock`` entry agrees with ``pyproject.toml``. Equal or
older is exit 1; a check that cannot run (no tags, git error, missing or
unparseable version) is exit 2 — never a silent pass.

The script is loaded by path (same shape as ``tests/test_check_radio_smoke.py``)
because ``scripts/`` is not a package. End-to-end cases run on real temporary
git repos (``git init``, commits, annotated tags) — git is never mocked.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "release"
    / "check_main_version.py"
)
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_main_version", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load()


# --- version ordering (the repo's PEP 440 scheme) ---


@pytest.mark.parametrize(
    ("older", "newer"),
    [
        ("3.3.0.dev0", "3.3.0b1"),
        ("3.3.0b1", "3.3.0rc1"),
        ("3.3.0rc1", "3.3.0"),
        ("3.3.0rc2", "3.3.0rc10"),
        ("3.3.0rc6", "3.3.0"),
        ("3.3.0", "3.3.1.dev0"),
        ("3.3.0", "3.4.0.dev0"),
        ("3.9.0", "3.10.0"),
        ("2.9.0a0", "2.9.0"),
        ("3.3.1.dev0", "3.3.1"),
    ],
)
def test_version_order(older: str, newer: str) -> None:
    assert checker.version_key(older) < checker.version_key(newer)


def test_version_suffix_spellings_normalize() -> None:
    assert checker.version_key("3.3.0rc1") == checker.version_key("3.3.0.rc1")
    assert checker.version_key("2.9.0-alpha") == checker.version_key("2.9.0a0")


@pytest.mark.parametrize("bad", ["3.3", "3.3.0.1", "garbage", "", "3.3.0post1"])
def test_unparseable_version_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        checker.version_key(bad)


def test_latest_tag_is_numeric_not_lexicographic() -> None:
    tags = ["v3.9.0", "v3.10.0rc1", "v3.3.0"]
    assert checker.latest_release_tag(tags) == "v3.10.0rc1"


def test_latest_tag_prefers_final_over_its_rc() -> None:
    assert checker.latest_release_tag(["v3.3.0rc6", "v3.3.0"]) == "v3.3.0"


def test_no_tags_raises() -> None:
    with pytest.raises(ValueError):
        checker.latest_release_tag([])


def test_unparseable_tag_raises() -> None:
    with pytest.raises(ValueError):
        checker.latest_release_tag(["v3.3.0", "vbanana"])


# --- end-to-end on real temporary git repos ---


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, (
        f"git {' '.join(args)} failed:\n{completed.stderr}"
    )
    return completed.stdout.strip()


def _pyproject(version: str) -> str:
    return (
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
        "\n"
        "[project]\n"
        'name = "rigplane"\n'
        f'version = "{version}"\n'
        'requires-python = ">=3.11"\n'
    )


def _uv_lock(version: str) -> str:
    return (
        "version = 1\n"
        'requires-python = ">=3.11"\n'
        "\n"
        "[[package]]\n"
        'name = "rigplane"\n'
        f'version = "{version}"\n'
        'source = { editable = "." }\n'
    )


def _build_repo(
    tmp_path: Path,
    *,
    version: str,
    lock_version: str | None = None,
    tags: tuple[str, ...] = ("v3.3.0rc6", "v3.3.0"),
) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "version-test")
    _git(repo, "config", "user.email", "version-test@example.com")
    (repo / "pyproject.toml").write_text(_pyproject(version), encoding="utf-8")
    (repo / "uv.lock").write_text(
        _uv_lock(lock_version if lock_version is not None else version),
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fixture")
    for tag in tags:
        _git(repo, "tag", "-a", tag, "-m", tag)
    return repo


def _run(repo: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = checker.main(["--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_dev_version_after_release_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _build_repo(tmp_path, version="3.3.1.dev0")
    code, out, err = _run(repo, capsys)
    assert code == 0, err
    assert "3.3.1.dev0" in out and "v3.3.0" in out


def test_version_equal_to_latest_tag_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _build_repo(tmp_path, version="3.3.0")
    code, _, err = _run(repo, capsys)
    assert code == 1
    assert "3.3.0" in err and "v3.3.0" in err


def test_version_older_than_latest_tag_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The reported MOR-4714 defect: main says 3.2.0rc11 after v3.3.0 shipped.
    repo = _build_repo(tmp_path, version="3.2.0rc11")
    code, _, err = _run(repo, capsys)
    assert code == 1
    assert "3.2.0rc11" in err and "v3.3.0" in err


def test_newer_rc_tag_also_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _build_repo(tmp_path, version="3.3.1.dev0", tags=("v3.3.0", "v3.4.0rc1"))
    code, _, err = _run(repo, capsys)
    assert code == 1
    assert "v3.4.0rc1" in err


def test_uv_lock_disagreement_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _build_repo(tmp_path, version="3.3.1.dev0", lock_version="3.2.0rc11")
    code, _, err = _run(repo, capsys)
    assert code == 1
    assert "uv.lock" in err


def test_missing_uv_lock_cannot_decide(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _build_repo(tmp_path, version="3.3.1.dev0")
    (repo / "uv.lock").unlink()
    code, _, err = _run(repo, capsys)
    assert code == 2
    assert "uv.lock" in err


@pytest.mark.parametrize("version_line", ["", "version = 42\n"])
def test_lock_project_version_missing_or_not_string_cannot_decide(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], version_line: str
) -> None:
    repo = _build_repo(tmp_path, version="3.3.1.dev0")
    (repo / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "rigplane"\n' + version_line,
        encoding="utf-8",
    )
    code, _, err = _run(repo, capsys)
    assert code == 2
    assert "uv.lock" in err and "version" in err


def test_no_tags_cannot_decide(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _build_repo(tmp_path, version="3.3.1.dev0", tags=())
    code, _, err = _run(repo, capsys)
    assert code == 2
    assert err.strip()


def test_unparseable_tree_version_cannot_decide(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _build_repo(tmp_path, version="3.3")
    code, _, err = _run(repo, capsys)
    assert code == 2
    assert err.strip()


def test_missing_pyproject_cannot_decide(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _build_repo(tmp_path, version="3.3.1.dev0")
    (repo / "pyproject.toml").unlink()
    code, _, err = _run(repo, capsys)
    assert code == 2
    assert err.strip()


def test_lock_without_project_entry_cannot_decide(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _build_repo(tmp_path, version="3.3.1.dev0")
    (repo / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "other"\nversion = "1.0"\n',
        encoding="utf-8",
    )
    code, _, err = _run(repo, capsys)
    assert code == 2
    assert "uv.lock" in err


def test_cli_entry_point(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path, version="3.3.1.dev0")
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert "OK" in completed.stdout


# --- the checked-out repo itself ---


def test_repo_version_files_agree() -> None:
    pyproject = tomllib.loads(
        (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    lock = tomllib.loads((_REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    name = pyproject["project"]["name"]
    entry = next(p for p in lock["package"] if p.get("name") == name)
    assert entry["version"] == pyproject["project"]["version"]


def test_all_repo_tags_parse() -> None:
    tags = subprocess.run(
        ["git", "tag", "--list", "v*"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.split()
    if not tags:
        pytest.skip("checkout has no tags (shallow clone or file mirror)")
    for tag in tags:
        checker.version_key(tag.removeprefix("v"))
