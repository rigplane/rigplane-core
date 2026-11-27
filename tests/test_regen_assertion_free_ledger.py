"""The one-command assertion-free ledger regen (MOR-4890).

`scripts/regen_assertion_free_ledger.py` is the fix the stale-ledger
failure message names: `--check` regenerates in memory and fails WITH
THE DIFF, a bare run rewrites the ledger. These tests pin that contract
on a small fabricated tree, never on the real `tests/` directory.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "regen_assertion_free_ledger",
    ROOT / "scripts/regen_assertion_free_ledger.py",
)
assert _spec is not None and _spec.loader is not None
regen = importlib.util.module_from_spec(_spec)
sys.modules["regen_assertion_free_ledger"] = regen
_spec.loader.exec_module(regen)

ASSERTION_FREE = "def test_nothing_checked(record):\n    record.play()\n"
WITH_ASSERT = "def test_checked(radio):\n    assert radio.connected\n"


def _tree(tmp_path: Path, ledger_text: str) -> Path:
    root = tmp_path / "repo"
    tests = root / "tests"
    tests.mkdir(parents=True)
    (tests / "test_a.py").write_text(ASSERTION_FREE, encoding="utf-8")
    (tests / "test_b.py").write_text(WITH_ASSERT, encoding="utf-8")
    (tests / "assertion_free_uncovered.txt").write_text(ledger_text, encoding="utf-8")
    return root


def _fresh_ledger(root: Path) -> str:
    tests = root / "tests"
    return regen.render_ledger(regen.scan_files(tests))


def test_check_passes_on_a_fresh_ledger(tmp_path: Path, capsys) -> None:
    root = _tree(tmp_path, "")
    (root / "tests" / "assertion_free_uncovered.txt").write_text(
        _fresh_ledger(root), encoding="utf-8"
    )
    assert regen.main(["--check"], root=root) == 0
    assert capsys.readouterr().out == ""


def test_check_fails_with_the_diff_on_a_stale_ledger(tmp_path: Path, capsys) -> None:
    root = _tree(tmp_path, regen.render_ledger({}))
    assert regen.main(["--check"], root=root) == 1
    out = capsys.readouterr().out
    assert "+test_a.py" in out.replace(" ", "")
    assert "regen_assertion_free_ledger" in out


def test_bare_run_rewrites_a_stale_ledger(tmp_path: Path) -> None:
    root = _tree(tmp_path, regen.render_ledger({}))
    assert regen.main([], root=root) == 0
    ledger = (root / "tests" / "assertion_free_uncovered.txt").read_text(
        encoding="utf-8"
    )
    assert "test_a.py" in ledger
    assert "test_b.py" not in ledger
