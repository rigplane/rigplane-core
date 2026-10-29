"""Leaf boundary for ``rigplane.tci`` (MOR-3821, spec 4.1 item 2).

Every module under ``src/rigplane/tci/`` may import only the standard
library, ``__future__``, and ``rigplane.tci`` itself. This test is the
complete rule; the import-linter ``tci-is-a-leaf`` contract is the
visible early signal.

The scan parses real ``ast`` import statements (relative imports
included) instead of grepping text.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "src" / "rigplane" / "tci"

# Modules P0a delivers. L1/L2 extend this set with their own modules.
EXPECTED_MODULES = frozenset({"__init__.py", "ws.py"})


def _forbidden_imports(source: str) -> list[str]:
    """Return the disallowed top-level imports found in *source*."""
    tree = ast.parse(source)
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in sys.stdlib_module_names and top != "rigplane":
                    bad.append(alias.name)
                elif top == "rigplane":
                    rest = alias.name.split(".")
                    if len(rest) < 2 or rest[1] != "tci":
                        bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import inside the package
            if node.module is None:
                continue
            top = node.module.split(".")[0]
            if top == "__future__":
                continue
            if top in sys.stdlib_module_names:
                continue
            if top == "rigplane":
                rest = node.module.split(".")
                if len(rest) >= 2 and rest[1] == "tci":
                    continue
            bad.append(node.module)
    return bad


def _scan_package(directory: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(directory.glob("*.py")):
        bad = _forbidden_imports(path.read_text(encoding="utf-8"))
        if bad:
            found[path.name] = bad
    return found


def test_tci_package_has_expected_modules() -> None:
    assert PACKAGE_DIR.is_dir(), "src/rigplane/tci/ does not exist"
    actual = frozenset(p.name for p in PACKAGE_DIR.glob("*.py"))
    assert actual == EXPECTED_MODULES


def test_tci_imports_only_stdlib_and_itself() -> None:
    assert PACKAGE_DIR.is_dir(), "src/rigplane/tci/ does not exist"
    assert _scan_package(PACKAGE_DIR) == {}


@pytest.mark.parametrize(
    "source",
    ["import numpy\n", "from rigplane.core import x\n"],
)
def test_scanner_reports_forbidden_imports(source: str) -> None:
    """The check itself must be able to fail (spec 4.1: shown able to fail)."""
    assert _forbidden_imports(source) != []
