"""Meta-test: a test must assert something real (MOR-3239).

A test that only calls production code proves nothing but "it did not
raise"; a test whose only checks are ``assert_called*``/
``assert_awaited*`` proves only that a mock was poked, not that the
right value travelled through it. Both shapes pass silently when the
behaviour they meant to pin changes.

Counting rule (what this module's ledger lists): a test function is
assertion-free when its body -- including anything defined inside it --
contains none of:

1. an ``assert`` statement;
2. a call to ``raises`` or ``approx``, however imported
   (``pytest.raises``/``pytest.approx`` or the bare names);
3. a call whose target name starts with ``assert`` other than
   ``assert_called*``/``assert_awaited*``: unittest-style
   ``self.assertEqual``/``self.assertRaises`` and shared assertion
   helpers such as ``assert_frame`` count, mock call-verification
   alone never does.

Every ``test_*`` function or method, sync or async, anywhere in the
file counts; helper functions with other names do not. A file offends
when it contains at least one assertion-free test function. Tests that
deliberately pin only "does not raise" land here too: they stay in the
ledger until they gain a real assertion.

``tests/assertion_free_uncovered.txt`` lists today's offenders, one path
per line (relative to ``tests/``), sorted.
``test_no_unlisted_assertion_free_test`` fails on an offender missing
from it; ``test_ledger_entries_still_offend`` fails on a line that no
longer offends. Regenerate, do not edit:

    uv run python tests/test_meta_assertions.py --regenerate
"""

from __future__ import annotations

import ast
import functools
import os
import pathlib
import sys
import textwrap
from typing import Mapping, Sequence

import pytest

TESTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "tests"
LEDGER_FILE = pathlib.Path(__file__).with_name("assertion_free_uncovered.txt")
REGEN_ENV = "RIGPLANE_REGEN_ASSERTION_FREE_UNCOVERED"
TEST_FUNCTION_PREFIX = "test_"
REAL_CALL_NAMES = frozenset({"raises", "approx"})
MOCK_VERIFICATION_PREFIXES = ("assert_called", "assert_awaited")

Offence = tuple[int, str]  # (source line, assertion-free test name)


# ── detection ───────────────────────────────────────────────────────


def _called_name(func: ast.expr) -> str | None:
    """The simple name a call target is known by, however imported."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_real_assertion(node: ast.AST) -> bool:
    """True for an ``assert`` stmt, ``raises``/``approx`` or ``assert_*`` call."""
    if isinstance(node, ast.Assert):
        return True
    if not isinstance(node, ast.Call):
        return False
    name = _called_name(node.func)
    if name is None:
        return False
    if name in REAL_CALL_NAMES:
        return True
    if not name.startswith("assert"):
        return False
    return not name.startswith(MOCK_VERIFICATION_PREFIXES)


def _assertion_free_tests(tree: ast.Module) -> list[Offence]:
    """Every ``test_*`` function whose body asserts nothing real."""
    flagged: list[Offence] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith(TEST_FUNCTION_PREFIX)
        ):
            continue
        if not any(_is_real_assertion(child) for child in ast.walk(node)):
            flagged.append((node.lineno, node.name))
    return flagged


def _scan_files(root: pathlib.Path) -> dict[str, list[Offence]]:
    """Scan ``root/**/*.py``; map each relative path to its offences."""
    offending: dict[str, list[Offence]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offences = _assertion_free_tests(tree)
        if offences:
            offending[path.relative_to(root).as_posix()] = offences
    return offending


@functools.lru_cache(maxsize=4)
def _cached_scan(root: pathlib.Path) -> dict[str, list[Offence]]:
    return _scan_files(root)


# ── ledger ──────────────────────────────────────────────────────────


def _render_ledger(offending: Mapping[str, Sequence[Offence]]) -> str:
    header = [
        "# tests/test_meta_assertions.py: test files containing at least",
        "# one test function that asserts nothing real -- no assert",
        "# statement, no raises/approx call, no assert-prefixed call",
        "# other than mock-verification (assert_called*/assert_awaited*).",
        "# One path per line, relative to tests/, sorted. The test fails",
        "# offender missing here, and on a line here that no longer",
        "# offends -- delete that line, never keep it. Regenerate, do",
        "# not edit: uv run python tests/test_meta_assertions.py",
        "# --regenerate",
    ]
    rows = sorted(offending)
    return "\n".join(header + rows) + "\n"


def _parse_ledger_text(text: str) -> list[str]:
    """Ledger paths: non-blank, non-comment lines, order as written."""
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _ratchet_failures(
    offending: Mapping[str, Sequence[Offence]], listed: Sequence[str]
) -> tuple[list[str], list[str]]:
    """(unlisted offenders, stale ledger lines) for a scan and a ledger."""
    unlisted = sorted(set(offending) - set(listed))
    stale = sorted(set(listed) - set(offending))
    return unlisted, stale


def _describe(offending: Mapping[str, Sequence[Offence]]) -> str:
    return "\n".join(
        f"{path}: first assertion-free test at line {offences[0][0]}"
        f" ({offences[0][1]}, {len(offences)} total)"
        for path, offences in sorted(offending.items())
    )


# ── inline fixtures for the scanner's own unit tests ────────────────

_OFFENDING_SOURCE = textwrap.dedent(
    """
    from unittest.mock import AsyncMock


    def test_no_assertion_at_all(record):
        record.play()


    def test_only_mock_call_verification(radio):
        radio.set_frequency.assert_called_once_with(14074000)


    async def test_only_awaited_mock_verification(radio):
        radio.get_frequency.assert_awaited_once()
    """
)

_CLEAN_SOURCE = textwrap.dedent(
    """
    import pytest


    def arrange(radio):
        radio.warm_up()
        return radio


    def test_plain_assert(radio):
        assert radio.connected


    def test_raises(radio):
        with pytest.raises(ValueError):
            radio.connect()


    def test_approx(radio):
        assert radio.frequency == pytest.approx(14_074_000)


    def test_unittest_style_assert(self_like):
        self_like.assertEqual(radio.model, "IC-7300")


    def test_assertion_helper(frames):
        assert_frame(frames[0], b"frame-payload")


    def test_mock_check_alongside_real_assertion(radio):
        radio.set_frequency(14074000)
        radio.set_frequency.assert_called_once()
        assert radio.connected
    """
)


# ── the scanner's own unit tests ────────────────────────────────────


def test_offending_fixture_is_flagged(tmp_path: pathlib.Path) -> None:
    """No-assert, mock-only-verify and await-only-verify tests are flagged."""
    (tmp_path / "test_offender.py").write_text(_OFFENDING_SOURCE, encoding="utf-8")
    offending = _scan_files(tmp_path)
    assert list(offending) == ["test_offender.py"]
    assert [name for _, name in offending["test_offender.py"]] == [
        "test_no_assertion_at_all",
        "test_only_mock_call_verification",
        "test_only_awaited_mock_verification",
    ]
    assert all(line > 0 for line, _ in offending["test_offender.py"])


def test_clean_fixture_is_not_flagged(tmp_path: pathlib.Path) -> None:
    """Assert statements, raises/approx and assert_* helpers all count."""
    (tmp_path / "test_clean.py").write_text(_CLEAN_SOURCE, encoding="utf-8")
    assert _scan_files(tmp_path) == {}


def test_ledger_render_parse_round_trip() -> None:
    """Rendered ledger text parses back to exactly its sorted paths."""
    rendered = _render_ledger(
        {"tests/test_b.py": [(3, "test_b")], "tests/test_a.py": [(9, "test_a")]}
    )
    assert _parse_ledger_text(rendered) == ["tests/test_a.py", "tests/test_b.py"]


def test_ratchet_failure_directions() -> None:
    """Unlisted offenders and stale ledger lines are reported separately."""
    scan = {"tests/test_kept.py": [(1, "test_kept")]}
    listed = ["tests/test_kept.py", "tests/test_fixed.py"]
    unlisted, stale = _ratchet_failures(scan, listed)
    assert unlisted == []
    assert stale == ["tests/test_fixed.py"]
    unlisted, stale = _ratchet_failures(scan, [])
    assert unlisted == ["tests/test_kept.py"]
    assert stale == []


# ── the ratchet over the real tree ──────────────────────────────────


@pytest.fixture(scope="module")
def tree_offences() -> Mapping[str, Sequence[Offence]]:
    result = _cached_scan(TESTS_DIR)
    # Not a truthiness test: ``REGEN_ENV=0`` must mean off, not on.
    if os.environ.get(REGEN_ENV, "") not in {"", "0"}:
        LEDGER_FILE.write_text(_render_ledger(result), encoding="utf-8")
        pytest.skip(f"{REGEN_ENV} set: ledger rewritten, not checked")
    return result


def test_no_unlisted_assertion_free_test(
    tree_offences: Mapping[str, Sequence[Offence]],
) -> None:
    """An assertion-free test function needs a ledger line."""
    listed = _parse_ledger_text(LEDGER_FILE.read_text(encoding="utf-8"))
    unlisted, _ = _ratchet_failures(tree_offences, listed)
    assert not unlisted, _describe({p: tree_offences[p] for p in unlisted})


def test_ledger_entries_still_offend(
    tree_offences: Mapping[str, Sequence[Offence]],
) -> None:
    """A ledger line whose file no longer offends must be deleted."""
    listed = _parse_ledger_text(LEDGER_FILE.read_text(encoding="utf-8"))
    _, stale = _ratchet_failures(tree_offences, listed)
    assert not stale, "\n".join(stale)


if __name__ == "__main__":
    if "--regenerate" not in sys.argv[1:]:
        sys.exit("usage: uv run python tests/test_meta_assertions.py --regenerate")
    offences = _cached_scan(TESTS_DIR)
    LEDGER_FILE.write_text(_render_ledger(offences), encoding="utf-8")
    print(f"{LEDGER_FILE.name}: {len(offences)} offending files listed")
