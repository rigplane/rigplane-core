"""Tests for ``scripts/release/check_radio_smoke.py`` (MOR-3839).

``publish.yml``'s ``validate`` job downloads ``radio-smoke-rx.json`` and
``radio-smoke-tx.json`` from the release being published and runs this
checker before any build or PyPI upload starts. Exit 0 is allowed only when
both fragments validate: schema 1, matching ``kind``, ``core_version`` equal
to the tag version, ``git_sha`` equal to the tagged commit,
``verdict == "PASS"``, and a parseable non-future ``generated_at`` — plus the
per-kind requirements (all five TX sequences PASS with no stuck TX, no
release debt and a radio back in RX; a non-empty all-PASS ``radios`` map for
RX). Every violation class below must produce a non-zero exit with one line
per violation.

The script is loaded by path (same shape as
``scripts/bench/test_night_diff.py``) because ``scripts/`` is not a package.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "release"
    / "check_radio_smoke.py"
)

TAG = "v3.1.0"
VERSION = "3.1.0"
SHA = "0123456789abcdef0123456789abcdef01234567"
GENERATED_AT = "2026-10-01T12:00:00+00:00"
FUTURE_GENERATED_AT = "2999-01-01T00:00:00+00:00"

TX_SEQUENCES = {
    "single": "PASS",
    "rekey_150ms": "PASS",
    "rekey_immediate": "PASS",
    "double_cycle": "PASS",
    "force_off_inflight": "PASS",
}


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_radio_smoke", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load()


def _rx_payload() -> dict[str, Any]:
    return {
        "schema": 1,
        "kind": "rx",
        "core_version": VERSION,
        "git_sha": SHA,
        "verdict": "PASS",
        "generated_at": GENERATED_AT,
        "radios": {"IC-7610": "PASS", "IC-705": "PASS"},
    }


def _tx_payload() -> dict[str, Any]:
    return {
        "schema": 1,
        "kind": "tx",
        "core_version": VERSION,
        "git_sha": SHA,
        "verdict": "PASS",
        "generated_at": GENERATED_AT,
        "sequences": dict(TX_SEQUENCES),
        "stuck_tx": False,
        "release_debt": False,
        "ended_rx": True,
    }


def _write_and_check(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    rx: dict[str, Any] | str | bytes | None,
    tx: dict[str, Any] | str | bytes | None,
    tag: str = TAG,
    sha: str = SHA,
    repo: Path | None = None,
) -> tuple[int, str, str]:
    """Write the fragments, run ``check``, return (exit, stdout, stderr)."""
    rx_path = tmp_path / "radio-smoke-rx.json"
    tx_path = tmp_path / "radio-smoke-tx.json"
    for path, payload in ((rx_path, rx), (tx_path, tx)):
        if payload is None:
            continue
        if isinstance(payload, bytes):
            path.write_bytes(payload)
        else:
            text = payload if isinstance(payload, str) else json.dumps(payload)
            path.write_text(text, encoding="utf-8")
    argv = [
        "check",
        "--tag",
        tag,
        "--git-sha",
        sha,
        "--rx",
        str(rx_path),
        "--tx",
        str(tx_path),
    ]
    if repo is not None:
        argv += ["--repo", str(repo)]
    rc = checker.main(argv)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    rx: dict[str, Any] | str | bytes | None,
    tx: dict[str, Any] | str | bytes | None,
    tag: str = TAG,
    sha: str = SHA,
) -> tuple[int, str]:
    """Write the fragments, run ``check``, return (exit code, stderr)."""
    rc, _, stderr = _write_and_check(tmp_path, capsys, rx=rx, tx=tx, tag=tag, sha=sha)
    return rc, stderr


def _stderr_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.strip()]


def test_valid_pair_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, stderr = _run(tmp_path, capsys, rx=_rx_payload(), tx=_tx_payload())
    assert rc == 0, f"a valid receipt pair must pass; stderr: {stderr}"
    assert stderr == "", f"a passing check stays quiet on stderr: {stderr!r}"


def test_tag_without_leading_v_matches_the_same_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, _ = _run(tmp_path, capsys, rx=_rx_payload(), tx=_tx_payload(), tag=VERSION)
    assert rc == 0


def test_stdlib_only() -> None:
    tree = checker.__dict__
    assert tree, "module must load"
    import ast

    imported: set[str] = set()
    for node in ast.walk(ast.parse(_SCRIPT.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names), (
        f"non-stdlib imports in {_SCRIPT.name}: {sorted(imported - set(sys.stdlib_module_names))}"
    )


Mutator = Callable[[dict[str, Any], dict[str, Any]], None]


def _mutate(**patches: Any) -> Mutator:
    """Build a mutator from dotted paths: rx__sequences__drop="double_cycle"."""

    def apply(rx: dict[str, Any], tx: dict[str, Any]) -> None:
        for dotted, value in patches.items():
            parts = dotted.split("__")
            target = {"rx": rx, "tx": tx}[parts[0]]
            for key in parts[1:-1]:
                target = target[key]
            if isinstance(value, _Drop):
                target.pop(parts[-1], None)
            else:
                target[parts[-1]] = value

    return apply


class _Drop:
    pass


DROP = _Drop()


def _swap_generated_at(fragment: str, value: str) -> Mutator:
    return _mutate(**{f"{fragment}__generated_at": value})


VIOLATION_CASES: list[tuple[str, Mutator, list[str]]] = [
    (
        "wrong-version-rx",
        _mutate(rx__core_version="3.2.0"),
        ["rx:", "core_version", "3.2.0"],
    ),
    (
        "wrong-version-tx",
        _mutate(tx__core_version="3.0.9"),
        ["tx:", "core_version"],
    ),
    (
        "wrong-sha",
        _mutate(tx__git_sha="f" * 40),
        ["tx:", "git_sha"],
    ),
    (
        "wrong-schema",
        _mutate(rx__schema=2),
        ["rx:", "schema"],
    ),
    (
        "wrong-kind",
        _mutate(rx__kind="tx"),
        ["rx:", "kind"],
    ),
    (
        "verdict-fail",
        _mutate(tx__verdict="FAIL"),
        ["tx:", "verdict", "FAIL"],
    ),
    (
        "future-timestamp",
        _swap_generated_at("tx", FUTURE_GENERATED_AT),
        ["tx:", "generated_at", "future"],
    ),
    (
        "unparseable-timestamp",
        _swap_generated_at("rx", "not a timestamp"),
        ["rx:", "generated_at"],
    ),
    (
        "missing-sequence",
        _mutate(tx__sequences__double_cycle=DROP),
        ["tx:", "double_cycle", "missing"],
    ),
    (
        "extra-sequence",
        _mutate(tx__sequences__bonus="PASS"),
        ["tx:", "bonus", "unexpected"],
    ),
    (
        "fail-sequence",
        _mutate(tx__sequences__rekey_150ms="FAIL"),
        ["tx:", "rekey_150ms", "FAIL"],
    ),
    (
        "stuck-tx",
        _mutate(tx__stuck_tx=True),
        ["tx:", "stuck_tx"],
    ),
    (
        "release-debt",
        _mutate(tx__release_debt=True),
        ["tx:", "release_debt"],
    ),
    (
        "ended-rx-false",
        _mutate(tx__ended_rx=False),
        ["tx:", "ended_rx"],
    ),
    (
        "empty-radios",
        _mutate(rx__radios={}),
        ["rx:", "radios"],
    ),
    (
        "non-pass-radio",
        _mutate(rx__radios={"IC-7610": "PASS", "IC-705": "FAIL"}),
        ["rx:", "radios", "IC-705", "FAIL"],
    ),
]


@pytest.mark.parametrize(
    ("case_id", "mutate", "expect"),
    VIOLATION_CASES,
    ids=[case[0] for case in VIOLATION_CASES],
)
def test_violation_fails_with_one_line(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case_id: str,
    mutate: Mutator,
    expect: list[str],
) -> None:
    rx, tx = _rx_payload(), _tx_payload()
    mutate(rx, tx)
    rc, stderr = _run(tmp_path, capsys, rx=rx, tx=tx)
    assert rc != 0, f"{case_id}: the check must fail"
    lines = _stderr_lines(stderr)
    assert any(all(part in line for part in expect) for line in lines), (
        f"{case_id}: no violation line mentions all of {expect}; stderr:\n{stderr}"
    )


def test_malformed_json_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, stderr = _run(tmp_path, capsys, rx="{not json", tx=_tx_payload())
    assert rc != 0
    assert any(
        "rx:" in line and "malformed JSON" in line for line in _stderr_lines(stderr)
    ), stderr


def test_invalid_utf8_is_a_reported_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invalid UTF-8 must be one violation line, not an uncaught UnicodeDecodeError."""
    rc, stderr = _run(
        tmp_path, capsys, rx=b'{"schema": 1, "\xff\xfe": 1}', tx=_tx_payload()
    )
    assert rc != 0, "an undecodable receipt must fail the check"
    lines = _stderr_lines(stderr)
    utf8_lines = [line for line in lines if "UTF-8" in line]
    assert len(utf8_lines) == 1 and utf8_lines[0].startswith("rx:"), (
        f"expected exactly one rx violation line naming UTF-8; stderr:\n{stderr}"
    )


def test_missing_file_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, stderr = _run(tmp_path, capsys, rx=_rx_payload(), tx=None)
    assert rc != 0
    assert any(
        "tx:" in line and "not found" in line for line in _stderr_lines(stderr)
    ), stderr


def test_every_violation_gets_its_own_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No fail-fast: one broken field in each fragment yields two lines."""
    rx, tx = _rx_payload(), _tx_payload()
    rx["core_version"] = "0.0.0"
    tx["core_version"] = "0.0.0"
    rc, stderr = _run(tmp_path, capsys, rx=rx, tx=tx)
    assert rc != 0
    lines = _stderr_lines(stderr)
    assert sum("core_version" in line for line in lines) == 2, (
        f"expected one core_version line per fragment; stderr:\n{stderr}"
    )
    assert any(line.startswith("rx:") for line in lines)
    assert any(line.startswith("tx:") for line in lines)


def test_top_level_must_be_an_object(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, stderr = _run(
        tmp_path, capsys, rx=json.dumps(["not", "a", "dict"]), tx=_tx_payload()
    )
    assert rc != 0
    assert any("rx:" in line and "object" in line for line in _stderr_lines(stderr)), (
        stderr
    )


# --- MOR-3998: rc receipts for a version-only final ---
#
# A receipt pair produced against a release candidate (``core_version``
# ``X.Y.ZrcN``, ``git_sha`` the candidate commit) validates for the final
# tag ``vX.Y.Z`` when the final commit differs from the candidate only in
# the version: the ``version`` line in ``pyproject.toml``, the project's
# own ``version`` line in ``uv.lock``, and the changelog
# heading/link lines. Everything is built on real temporary git repos
# (``git init``, commits, annotated tags) — git is never mocked.


RC_VERSION = "3.1.0rc1"
RC_TAG = "v3.1.0rc1"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, (
        f"git {' '.join(args)} failed:\n{completed.stderr}"
    )
    return completed.stdout.strip()


def _rc_pyproject(version: str) -> str:
    return (
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
        "\n"
        "[project]\n"
        'name = "rigplane"\n'
        f'version = "{version}"\n'
        'description = "test fixture"\n'
        'requires-python = ">=3.11"\n'
    )


def _rc_uv_lock(version: str) -> str:
    return (
        "version = 1\n"
        'requires-python = ">=3.11"\n'
        "\n"
        "[[package]]\n"
        'name = "rigplane"\n'
        f'version = "{version}"\n'
        'source = { editable = "." }\n'
        "\n"
        "[[package]]\n"
        'name = "dep-one"\n'
        'version = "1.2.3"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
    )


def _rc_changelog(version: str) -> str:
    return (
        "# Changelog\n"
        "\n"
        "## [Unreleased]\n"
        "\n"
        f"## [{version}] — 2026-10-31\n"
        "\n"
        "- Test entry one.\n"
        "\n"
        f"[Unreleased]: https://github.com/rigplane/rigplane-core"
        f"/compare/v{version}...HEAD\n"
        f"[{version}]: https://github.com/rigplane/rigplane-core"
        f"/compare/v0.0.0...v{version}\n"
    )


def _build_rc_repo(
    tmp_path: Path,
    *,
    final_extra: Callable[[Path], None] | None = None,
    pyproject_fn: Callable[[str], str] | None = None,
) -> tuple[Path, str, str]:
    """Real git repo: rc commit tagged ``v3.1.0rc1``, then the final commit.

    The final commit rewrites only the version (the pyproject version,
    the rigplane uv.lock version, the changelog headings) unless
    ``final_extra`` mutates the tree further before the final commit.
    Returns ``(repo, candidate_sha, final_sha)``.
    """
    render_pyproject = pyproject_fn or _rc_pyproject
    repo = tmp_path / "rc-repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "rc-test")
    _git(repo, "config", "user.email", "rc-test@example.com")
    (repo / "pyproject.toml").write_text(render_pyproject(RC_VERSION), encoding="utf-8")
    (repo / "uv.lock").write_text(_rc_uv_lock(RC_VERSION), encoding="utf-8")
    (repo / "docs" / "CHANGELOG.md").write_text(
        _rc_changelog(RC_VERSION), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chore(release): bump to 3.1.0rc1")
    _git(repo, "tag", "-a", RC_TAG, "-m", "Release 3.1.0rc1")
    candidate = _git(repo, "rev-parse", "HEAD")
    (repo / "pyproject.toml").write_text(render_pyproject(VERSION), encoding="utf-8")
    (repo / "uv.lock").write_text(_rc_uv_lock(VERSION), encoding="utf-8")
    (repo / "docs" / "CHANGELOG.md").write_text(
        _rc_changelog(VERSION), encoding="utf-8"
    )
    if final_extra is not None:
        final_extra(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chore(release): bump to 3.1.0")
    return repo, candidate, _git(repo, "rev-parse", "HEAD")


def _rc_pair(candidate: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """A valid RX/TX pair naming the candidate commit, as really tested."""
    rx, tx = _rx_payload(), _tx_payload()
    rx["core_version"] = RC_VERSION
    rx["git_sha"] = candidate
    tx["core_version"] = RC_VERSION
    tx["git_sha"] = candidate
    return rx, tx


def test_rc_receipts_pass_for_a_version_only_final(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, candidate, final = _build_rc_repo(tmp_path)
    rx, tx = _rc_pair(candidate)
    rc, stdout, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc == 0, f"a version-only final must pass; stderr:\n{stderr}"
    assert stderr == "", f"a passing check stays quiet on stderr: {stderr!r}"
    assert f"for {TAG} @ {final}" in stdout, stdout
    assert (
        f"(tested candidate v{RC_VERSION} @ {candidate}, "
        "version-only diff proven)" in stdout
    ), stdout


def test_rc_receipts_fail_when_code_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def extra(repo: Path) -> None:
        src = repo / "src"
        src.mkdir()
        (src / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")

    repo, candidate, final = _build_rc_repo(tmp_path, final_extra=extra)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a code change beyond the version must fail"
    assert any("src/extra.py" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_a_dependency_version_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def extra(repo: Path) -> None:
        text = (repo / "uv.lock").read_text(encoding="utf-8")
        (repo / "uv.lock").write_text(
            text.replace('version = "1.2.3"', 'version = "1.2.4"'),
            encoding="utf-8",
        )

    repo, candidate, final = _build_rc_repo(tmp_path, final_extra=extra)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a dependency change must fail"
    assert any("uv.lock" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_a_package_is_added(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def extra(repo: Path) -> None:
        with (repo / "uv.lock").open("a", encoding="utf-8") as handle:
            handle.write('\n[[package]]\nname = "dep-two"\nversion = "0.1.0"\n')

    repo, candidate, final = _build_rc_repo(tmp_path, final_extra=extra)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "an added package must fail"
    assert any("uv.lock" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_pyproject_changes_beyond_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def extra(repo: Path) -> None:
        text = (repo / "pyproject.toml").read_text(encoding="utf-8")
        (repo / "pyproject.toml").write_text(
            text.replace('description = "test fixture"', 'description = "changed"'),
            encoding="utf-8",
        )

    repo, candidate, final = _build_rc_repo(tmp_path, final_extra=extra)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a non-version pyproject change must fail"
    assert any("pyproject.toml" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_for_a_different_series(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An rc of another X.Y.Z (3.2.0rc1 for tag v3.1.0) never validates."""
    repo, candidate, final = _build_rc_repo(tmp_path)
    rx, tx = _rc_pair(candidate)
    rx["core_version"] = "3.2.0rc1"
    tx["core_version"] = "3.2.0rc1"
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a candidate of a different series must fail"
    assert any("3.2.0rc1" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_a_final_and_an_rc_receipt_mix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One receipt naming the final commit, one naming the rc: a mix fails."""
    repo, candidate, final = _build_rc_repo(tmp_path)
    rx, _ = _rc_pair(candidate)
    tx = _tx_payload()
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a final/rc mix must fail"
    assert _stderr_lines(stderr), "a mix must print at least one line"


def test_rc_receipts_fail_when_rx_and_tx_name_different_candidates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, candidate, final = _build_rc_repo(tmp_path)
    rx, tx = _rc_pair(candidate)
    tx["git_sha"] = "f" * 40
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "two different candidates must fail"
    assert any("different candidates" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_the_tag_points_elsewhere(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ``vX.Y.ZrcN`` tag must resolve to the receipt's ``git_sha``."""
    repo, candidate, final = _build_rc_repo(tmp_path)
    _git(repo, "tag", "-f", "-a", RC_TAG, "-m", "moved", final)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a tag that does not resolve to the receipt sha must fail"
    assert any(RC_TAG in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_the_candidate_is_not_an_ancestor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The candidate must be an ancestor of the final commit."""
    repo, candidate, _ = _build_rc_repo(tmp_path)
    _git(repo, "checkout", "-q", "--orphan", "detached")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "detached final")
    detached = _git(repo, "rev-parse", "HEAD")
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=detached, repo=repo
    )
    assert rc != 0, "a candidate that is no ancestor of FINAL must fail"
    assert any("ancestor" in line for line in _stderr_lines(stderr)), stderr


# --- MOR-3998 rc receipts for a version-only final ---
#
# Each test below pins one of the four checks the gate performs for
# receipts naming a candidate (``X.Y.ZrcN``): rx and tx name the same
# candidate; the tag resolves through ``refs/tags/`` (a same-named
# branch is not a tag) and the candidate is an ancestor of the final;
# the candidate-to-final tree holds only identical-mode modifications
# of exactly ``pyproject.toml``, ``uv.lock`` and ``docs/CHANGELOG.md``;
# the TOML documents match past the version and the final changelog
# carries the final heading and no rc heading. CHANGELOG body
# integrity is not checked.


def test_rc_receipts_fail_when_only_a_branch_names_the_candidate_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A branch named like the tag is not the tag: no tag must fail."""
    repo, candidate, final = _build_rc_repo(tmp_path)
    _git(repo, "tag", "-d", RC_TAG)
    _git(repo, "branch", RC_TAG, candidate)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a branch shadowing the candidate tag must fail"
    assert any(RC_TAG in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_pyproject_gains_the_executable_bit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 100644 -> 100755 mode change is not a version-only change."""

    def extra(repo: Path) -> None:
        (repo / "pyproject.toml").chmod(0o755)

    repo, candidate, final = _build_rc_repo(tmp_path, final_extra=extra)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "an executable-bit change must fail"
    assert any("pyproject.toml" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_a_versioned_file_becomes_a_symlink(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Replacing a version file with a symlink is not version-only."""

    def extra(repo: Path) -> None:
        lock = repo / "uv.lock"
        target = repo / "uv.lock.real"
        target.write_bytes(lock.read_bytes())
        lock.unlink()
        lock.symlink_to("uv.lock.real")

    repo, candidate, final = _build_rc_repo(tmp_path, final_extra=extra)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a symlink replacing a version file must fail"
    assert any("uv.lock" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_an_allowed_path_is_renamed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Renaming a version file is not a version-only change."""

    def extra(repo: Path) -> None:
        (repo / "docs" / "CHANGELOG.md").rename(repo / "docs" / "CHANGELOG2.md")

    repo, candidate, final = _build_rc_repo(tmp_path, final_extra=extra)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a rename of an allowed path must fail"
    assert any("CHANGELOG" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_hatch_wheel_packages_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ``[tool.hatch]`` build-config change is not version-only."""

    def extra(repo: Path) -> None:
        path = repo / "pyproject.toml"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text + '[tool.hatch.build.targets.wheel]\npackages = ["src/rigplane"]\n',
            encoding="utf-8",
        )

    repo, candidate, final = _build_rc_repo(tmp_path, final_extra=extra)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a hatch wheel packages change must fail"
    assert any("pyproject.toml" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_uv_lock_top_level_metadata_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A top-level ``uv.lock`` field change is not version-only."""

    def extra(repo: Path) -> None:
        path = repo / "uv.lock"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'requires-python = ">=3.11"', 'requires-python = ">=3.12"'
            ),
            encoding="utf-8",
        )

    repo, candidate, final = _build_rc_repo(tmp_path, final_extra=extra)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a top-level uv.lock metadata change must fail"
    assert any("uv.lock" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_the_lock_is_unchanged_but_still_names_the_rc(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file the tree diff skips must still carry the final version."""

    def extra(repo: Path) -> None:
        (repo / "uv.lock").write_text(_rc_uv_lock(RC_VERSION), encoding="utf-8")

    repo, candidate, final = _build_rc_repo(tmp_path, final_extra=extra)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "an unchanged lock still naming the rc must fail"
    assert any("uv.lock" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_the_final_changelog_has_no_final_heading(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The final changelog must carry a ``## [X.Y.Z]`` heading line."""

    def extra(repo: Path) -> None:
        path = repo / "docs" / "CHANGELOG.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(f"## [{VERSION}]", "## [Next]", 1),
            encoding="utf-8",
        )

    repo, candidate, final = _build_rc_repo(tmp_path, final_extra=extra)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a final changelog without the final heading must fail"
    assert any("CHANGELOG" in line for line in _stderr_lines(stderr)), stderr


def test_rc_receipts_fail_when_the_final_changelog_still_names_the_rc(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The final changelog must not still carry the ``## [X.Y.ZrcN]`` line."""

    def extra(repo: Path) -> None:
        (repo / "docs" / "CHANGELOG.md").write_text(
            _rc_changelog(RC_VERSION), encoding="utf-8"
        )

    repo, candidate, final = _build_rc_repo(tmp_path, final_extra=extra)
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "a final changelog still naming the rc must fail"
    assert any("CHANGELOG" in line for line in _stderr_lines(stderr)), stderr


def _multiline_string_pyproject(version: str) -> str:
    return (
        _rc_pyproject(version)
        + '\n[tool.example]\nnote = """\nline one\nversion = "1.0.0"\nline three\n"""\n'
    )


def test_rc_receipts_fail_when_a_toml_multiline_string_hides_a_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An edit inside a multiline string changes the parsed document."""

    def extra(repo: Path) -> None:
        path = repo / "pyproject.toml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'version = "1.0.0"', 'version = "2.0.0"', 1
            ),
            encoding="utf-8",
        )

    repo, candidate, final = _build_rc_repo(
        tmp_path, final_extra=extra, pyproject_fn=_multiline_string_pyproject
    )
    rx, tx = _rc_pair(candidate)
    rc, _, stderr = _write_and_check(
        tmp_path, capsys, rx=rx, tx=tx, tag=TAG, sha=final, repo=repo
    )
    assert rc != 0, "an edit inside a multiline string must fail"
    assert any("pyproject.toml" in line for line in _stderr_lines(stderr)), stderr
