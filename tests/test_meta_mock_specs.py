"""Meta-test: mocks standing in for project objects must carry a spec.

MOR-3238. ``CLAUDE.md`` states the project rule this gate enforces:
"MagicMock hides signature bugs -- verify against real dataclasses". A
``MagicMock()``/``AsyncMock()``/``Mock()`` built without ``spec=`` or
``spec_set=`` accepts every attribute name and every call signature, so
once it stands in for a radio, backend or transport it silently accepts
methods and parameters the real class does not have: a rename or a
signature change then passes the suite.

Counting rule (what this module's ledger lists): a test file offends
when it constructs ``MagicMock``/``AsyncMock``/``Mock`` -- by the called
name, however imported, and with no ``spec``/``spec_set`` keyword -- in
one of four uses, each of which names the mock as a project object:

1. assigned to a variable whose name contains ``radio``, ``backend`` or
   ``transport`` (case-insensitive), via ``=`` or an annotated ``=``;
2. assigned to an attribute whose own name contains one of those tokens
   (``radio._civ_transport = MagicMock()``);
3. passed as a keyword argument whose parameter name contains one of
   those tokens (``make_handler(radio=AsyncMock())``);
4. returned or yielded by a function whose own name contains one of
   those tokens (a ``radio``/``backend`` fixture factory).

Deliberately out of scope: bare mocks under any other name, mocks passed
positionally, ``patch()`` results, and ``create_autospec()`` (spec'd by
construction). The rule trades coverage for near-zero false positives so
the ledger only ever ratchets down.

``tests/mock_spec_uncovered.txt`` lists today's offenders, one path per
line (relative to ``tests/``), sorted. ``test_no_unlisted_specless_project_mock`` fails on an
offender missing from it; ``test_ledger_entries_still_offend`` fails on
a line that no longer offends. Regenerate, do not edit:

    uv run python tests/test_meta_mock_specs.py --regenerate
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
LEDGER_FILE = pathlib.Path(__file__).with_name("mock_spec_uncovered.txt")
REGEN_ENV = "RIGPLANE_REGEN_MOCK_SPEC_UNCOVERED"
MOCK_CONSTRUCTORS = frozenset({"MagicMock", "AsyncMock", "Mock"})
PROJECT_NAME_TOKENS = ("radio", "backend", "transport")

Offence = tuple[int, str]  # (source line, why this is an offence)


# ── detection ───────────────────────────────────────────────────────


def _called_name(func: ast.expr) -> str | None:
    """The simple name a call target is known by, however imported."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_bare_mock_constructor(node: ast.AST) -> bool:
    """True for a ``MagicMock()``/``AsyncMock()``/``Mock()`` without spec."""
    if not isinstance(node, ast.Call):
        return False
    if _called_name(node.func) not in MOCK_CONSTRUCTORS:
        return False
    return not any(kw.arg in ("spec", "spec_set") for kw in node.keywords)


def _mentions_project_type(name: str) -> bool:
    """True when *name* contains radio/backend/transport, case-insensitively."""
    low = name.lower()
    return any(token in low for token in PROJECT_NAME_TOKENS)


def _mock_spec_offences(tree: ast.Module) -> list[Offence]:
    """Every bare mock construction the counting rule flags, with reasons."""
    found: list[Offence] = []

    def _targets(node: ast.Assign | ast.AnnAssign) -> list[ast.expr]:
        return list(node.targets) if isinstance(node, ast.Assign) else [node.target]

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and _is_bare_mock_constructor(
            node.value
        ):
            for target in _targets(node):
                if isinstance(target, ast.Name) and _mentions_project_type(target.id):
                    found.append((node.lineno, f"assigned to '{target.id}'"))
                elif isinstance(target, ast.Attribute) and _mentions_project_type(
                    target.attr
                ):
                    found.append(
                        (node.lineno, f"assigned to attribute '{target.attr}'")
                    )
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg
                    and _mentions_project_type(kw.arg)
                    and _is_bare_mock_constructor(kw.value)
                ):
                    found.append((node.lineno, f"keyword argument '{kw.arg}'"))
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and _mentions_project_type(node.name):
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and _is_bare_mock_constructor(
                    child.value
                ):
                    found.append((child.lineno, f"returned from '{node.name}'"))
                elif isinstance(
                    child, (ast.Yield, ast.YieldFrom)
                ) and _is_bare_mock_constructor(child.value):
                    found.append((child.lineno, f"yielded from '{node.name}'"))
    return found


def _scan_files(root: pathlib.Path) -> dict[str, list[Offence]]:
    """Scan ``root/**/*.py``; map each relative path to its offences."""
    offending: dict[str, list[Offence]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offences = _mock_spec_offences(tree)
        if offences:
            offending[path.relative_to(root).as_posix()] = offences
    return offending


@functools.lru_cache(maxsize=4)
def _cached_scan(root: pathlib.Path) -> dict[str, list[Offence]]:
    return _scan_files(root)


# ── ledger ──────────────────────────────────────────────────────────


def _render_ledger(offending: Mapping[str, Sequence[Offence]]) -> str:
    header = [
        "# tests/test_meta_mock_specs.py: test files that construct",
        "# MagicMock()/AsyncMock()/Mock() without spec=/spec_set= and use",
        "# it where a project object (radio, backend, transport) is",
        "# expected. One path per line, relative to tests/, sorted. The",
        "# test fails on an offender missing here, and on a line here that",
        "# offends -- delete that line, never keep it. Regenerate, do",
        "# not edit: uv run python tests/test_meta_mock_specs.py",
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
        f"{path}: first offence at line {offences[0][0]} ({offences[0][1]})"
        for path, offences in sorted(offending.items())
    )


# ── inline fixtures for the scanner's own unit tests ────────────────

_OFFENDING_SOURCE = textwrap.dedent(
    """
    from unittest.mock import AsyncMock, MagicMock, Mock


    def test_bare_mock_radio():
        radio = MagicMock()
        radio.set_frequency(14074000)


    async def test_bare_mock_async_backend():
        backend = AsyncMock()


    def test_attribute_transport(radio):
        radio._civ_transport = MagicMock()


    def test_keyword_radio(make_handler):
        handler = make_handler(radio=AsyncMock())
        assert handler is not None


    def radio():
        return Mock()
    """
)

_CLEAN_SOURCE = textwrap.dedent(
    """
    from unittest.mock import AsyncMock, MagicMock, Mock, create_autospec, patch

    from rigplane.core.radio_protocol import Radio


    def test_specs_and_unrelated_mocks_are_fine():
        radio = MagicMock(spec=Radio)
        backend = AsyncMock(spec_set=Radio)
        unrelated = MagicMock()
        logger = Mock()
        made = create_autospec(Radio)
        with patch("somemod.transport") as patched:
            patched.stuff()
        other.attr = MagicMock()
        takes(value=MagicMock())
        assert radio and backend and unrelated and logger and made
    """
)


# ── the scanner's own unit tests ────────────────────────────────────


def test_offending_fixture_is_flagged(tmp_path: pathlib.Path) -> None:
    """Each of the four counting-rule uses is reported with a reason."""
    (tmp_path / "test_offender.py").write_text(_OFFENDING_SOURCE, encoding="utf-8")
    offending = _scan_files(tmp_path)
    assert list(offending) == ["test_offender.py"]
    reasons = {why for _, why in offending["test_offender.py"]}
    assert reasons == {
        "assigned to 'radio'",
        "assigned to 'backend'",
        "assigned to attribute '_civ_transport'",
        "keyword argument 'radio'",
        "returned from 'radio'",
    }
    assert all(line > 0 for line, _ in offending["test_offender.py"])


def test_clean_fixture_is_not_flagged(tmp_path: pathlib.Path) -> None:
    """Spec'd mocks, unrelated bare mocks, patch() and autospec pass."""
    (tmp_path / "test_clean.py").write_text(_CLEAN_SOURCE, encoding="utf-8")
    assert _scan_files(tmp_path) == {}


def test_ledger_render_parse_round_trip() -> None:
    """Rendered ledger text parses back to exactly its sorted paths."""
    rendered = _render_ledger(
        {"tests/test_b.py": [(3, "assigned to 'radio'")], "tests/test_a.py": [(9, "")]}
    )
    assert _parse_ledger_text(rendered) == ["tests/test_a.py", "tests/test_b.py"]


def test_ratchet_failure_directions() -> None:
    """Unlisted offenders and stale ledger lines are reported separately."""
    scan = {"tests/test_kept.py": [(1, "assigned to 'radio'")]}
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


def test_no_unlisted_specless_project_mock(
    tree_offences: Mapping[str, Sequence[Offence]],
) -> None:
    """A spec-less mock used as a project object needs a ledger line."""
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
        sys.exit("usage: uv run python tests/test_meta_mock_specs.py --regenerate")
    offences = _cached_scan(TESTS_DIR)
    LEDGER_FILE.write_text(_render_ledger(offences), encoding="utf-8")
    print(f"{LEDGER_FILE.name}: {len(offences)} offending files listed")
