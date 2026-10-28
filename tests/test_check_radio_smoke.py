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


def _run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    rx: dict[str, Any] | str | None,
    tx: dict[str, Any] | str | None,
    tag: str = TAG,
    sha: str = SHA,
) -> tuple[int, str]:
    """Write the fragments, run ``check``, return (exit code, stderr)."""
    rx_path = tmp_path / "radio-smoke-rx.json"
    tx_path = tmp_path / "radio-smoke-tx.json"
    for path, payload in ((rx_path, rx), (tx_path, tx)):
        if payload is None:
            continue
        text = payload if isinstance(payload, str) else json.dumps(payload)
        path.write_text(text, encoding="utf-8")
    rc = checker.main(
        [
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
    )
    return rc, capsys.readouterr().err


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
