"""Build provenance contract: ``core/_build_info.json``, reader, stamp script (MOR-4408).

The Core wheel workflow and Pro's source-checkout freeze both stamp
``src/rigplane/core/_build_info.json`` through
``scripts/release/stamp_build_info.py`` (one producer); the typed contract
for that file lives in ``rigplane.core.build_info`` (``BuildInfo``), and
``load_build_info`` is the single reader. The reader must never raise into
the server: a missing or malformed file reports unknown, and there is
deliberately no runtime ``git`` fallback — an installed package inside an
unrelated git repository would report the wrong repository.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from rigplane.core.build_info import (
    UNKNOWN_BUILD_INFO,
    BuildInfo,
    load_build_info,
    write_build_info,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STAMP_SCRIPT = _REPO_ROOT / "scripts" / "release" / "stamp_build_info.py"
_BUILD_INFO_PATH = _REPO_ROOT / "src" / "rigplane" / "core" / "_build_info.json"

SHA = "0123456789abcdef0123456789abcdef01234567"


def _load_stamp_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("stamp_build_info", _STAMP_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Reader: absent / present / malformed ───────────────────────────


def test_load_without_file_reports_unknown(tmp_path: Path) -> None:
    assert load_build_info(tmp_path / "_build_info.json") == UNKNOWN_BUILD_INFO
    assert load_build_info(tmp_path / "_build_info.json") == BuildInfo(
        git_sha=None, requested_ref=None
    )


def test_load_valid_file(tmp_path: Path) -> None:
    target = tmp_path / "_build_info.json"
    target.write_text(
        json.dumps({"git_sha": SHA, "requested_ref": "main"}),
        encoding="utf-8",
    )
    assert load_build_info(target) == BuildInfo(git_sha=SHA, requested_ref="main")


@pytest.mark.parametrize(
    "payload",
    [
        "{ not json",
        "[]",
        '"a string"',
        '{"git_sha": "0123"}',  # not 40 hex
        '{"git_sha": "0123456789ABCDEF0123456789ABCDEF01234567"}',  # uppercase
        '{"git_sha": 123}',
        '{"git_sha": null, "requested_ref": "main"}',  # a sha is required to report
        '{"git_sha": "%s", "requested_ref": ""}' % SHA,
        '{"git_sha": "%s", "requested_ref": "main\\nother"}' % SHA,
        '{"git_sha": "%s", "requested_ref": 7}' % SHA,
    ],
)
def test_load_malformed_reports_unknown(tmp_path: Path, payload: str) -> None:
    target = tmp_path / "_build_info.json"
    target.write_text(payload, encoding="utf-8")
    assert load_build_info(target) == UNKNOWN_BUILD_INFO, payload


def test_load_ref_is_optional_but_sha_must_be_real(tmp_path: Path) -> None:
    target = tmp_path / "_build_info.json"
    target.write_text(json.dumps({"git_sha": SHA}), encoding="utf-8")
    assert load_build_info(target) == BuildInfo(git_sha=SHA, requested_ref=None)


def test_load_unreadable_file_reports_unknown(tmp_path: Path) -> None:
    assert load_build_info(tmp_path) == UNKNOWN_BUILD_INFO  # a directory


# ── Writer: strict, atomic ─────────────────────────────────────────


def test_write_then_load_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "_build_info.json"
    target.parent.mkdir()
    write_build_info(BuildInfo(git_sha=SHA, requested_ref="v3.2.0"), target)
    assert not target.with_name("_build_info.json.tmp").exists()
    assert load_build_info(target) == BuildInfo(git_sha=SHA, requested_ref="v3.2.0")


@pytest.mark.parametrize(
    "info",
    [
        BuildInfo(git_sha=None, requested_ref="main"),
        BuildInfo(git_sha="not-a-sha", requested_ref="main"),
        BuildInfo(git_sha=SHA, requested_ref=None),
        BuildInfo(git_sha=SHA, requested_ref=""),
        BuildInfo(git_sha=SHA, requested_ref="main\nother"),
    ],
)
def test_write_refuses_invalid_build_info(tmp_path: Path, info: BuildInfo) -> None:
    target = tmp_path / "_build_info.json"
    with pytest.raises(ValueError):
        write_build_info(info, target)
    assert not target.exists()


# ── Stamp script (the one producer) ────────────────────────────────


def test_stamp_script_writes_the_contract_file(tmp_path: Path) -> None:
    module = _load_stamp_script()
    package_dir = tmp_path / "src" / "rigplane" / "core"
    package_dir.mkdir(parents=True)
    code: Any = module.main(
        [
            "--sha",
            SHA,
            "--requested-ref",
            "main",
            "--root",
            str(tmp_path),
        ]
    )
    assert code == 0
    stamped = package_dir / "_build_info.json"
    assert load_build_info(stamped) == BuildInfo(git_sha=SHA, requested_ref="main")


def test_stamp_script_rejects_invalid_inputs(tmp_path: Path) -> None:
    module = _load_stamp_script()
    package_dir = tmp_path / "src" / "rigplane" / "core"
    package_dir.mkdir(parents=True)
    for argv in (
        ["--sha", "short", "--requested-ref", "main", "--root", str(tmp_path)],
        ["--sha", SHA, "--requested-ref", "", "--root", str(tmp_path)],
        ["--sha", SHA, "--requested-ref", "main\nother", "--root", str(tmp_path)],
    ):
        assert module.main(argv) == 2
    assert not (package_dir / "_build_info.json").exists()


# ── Repository hygiene ─────────────────────────────────────────────


def test_repo_checkout_is_not_stamped() -> None:
    """The repo never carries a build-info file.

    The file is generated only inside throwaway CI checkouts (and Pro's
    frozen source checkout); a stale committed copy would misreport the
    running build. Its absence also keeps the file out of .gitignore, which
    matters because hatchling excludes ignored files from the wheel.
    """
    assert not _BUILD_INFO_PATH.exists()
