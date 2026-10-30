"""Leaf boundary for ``rigplane.tci`` plus the shared ``rigplane.ws`` (MOR-3821).

Every module under ``src/rigplane/tci/`` may import only the standard
library, ``__future__``, ``rigplane.tci`` itself, and the shared
lowest-layer module ``rigplane.ws``. ``src/rigplane/ws.py`` itself may
import only the standard library. This test is the complete rule; the
import-linter ``tci-is-a-leaf`` contract is the visible early signal.

The scan parses real ``ast`` import statements (relative imports
included) instead of grepping text.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "src" / "rigplane" / "tci"
WS_PATH = Path(__file__).resolve().parent.parent / "src" / "rigplane" / "ws.py"

# Modules the leaf package holds (round 7: framing lives in the shared
# lowest-layer module ``rigplane.ws``, not in the leaf).
EXPECTED_MODULES = frozenset(
    {"__init__.py", "protocol.py", "frames.py", "handler.py", "server.py"}
)


def _resolve_relative(level: int, module: str | None, package: str) -> str | None:
    """Resolve a relative import to an absolute dotted name.

    *package* is the containing package (``rigplane.tci`` for every file
    scanned here). Returns ``None`` when the level escapes the top.
    """
    parts = package.split(".")
    if level > len(parts):
        return None
    base = parts[: len(parts) - level + 1]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


#: Other ``rigplane`` modules the leaf (or the shared module) may import.
#: ``rigplane.ws`` is the stdlib-only lowest layer both ``rigplane.tci``
#: and ``rigplane.web`` build on; it is not in the forbidden list.
_ALLOWED_RIGPLANE_SECOND = frozenset({"tci", "ws"})


def _bad_absolute(dotted: str) -> str | None:
    """Return *dotted* when it escapes stdlib, ``rigplane.tci`` and ``rigplane.ws``."""
    top = dotted.split(".")[0]
    if top == "__future__":
        return None
    if top in sys.stdlib_module_names:
        return None
    if top == "rigplane":
        rest = dotted.split(".")
        if len(rest) >= 2 and rest[1] in _ALLOWED_RIGPLANE_SECOND:
            return None
    return dotted


def _forbidden_imports(source: str, package: str = "rigplane.tci") -> list[str]:
    """Return the disallowed top-level imports found in *source*.

    Relative imports resolve against *package* (the containing package,
    ``rigplane.tci`` for every scanned file) and face the same check:
    ``from .protocol import ...`` stays inside, ``from .. import ...``
    escapes.
    """
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
                    if len(rest) < 2 or rest[1] not in _ALLOWED_RIGPLANE_SECOND:
                        bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                absolute = _resolve_relative(node.level, node.module, package)
                if absolute is None:
                    bad.append("." * node.level + (node.module or ""))
                else:
                    hit = _bad_absolute(absolute)
                    if hit is not None:
                        bad.append(hit)
                continue
            if node.module is None:
                continue
            hit = _bad_absolute(node.module)
            if hit is not None:
                bad.append(hit)
    return bad


def _package_for_path(path: Path, package_dir: Path) -> str:
    """Containing package for *path* under the scanned leaf root.

    The scanned root maps to ``rigplane.tci``: ``ws.py`` lives in
    ``rigplane.tci``, ``extra/__init__.py`` and ``extra/foo.py`` both
    live in ``rigplane.tci.extra``. Relative imports resolve against
    this package, not the top one.
    """
    rel = path.relative_to(package_dir)
    parts = [part for part in rel.parent.parts if part not in ("", ".")]
    return ".".join(["rigplane", "tci", *parts])


def _scan_package(directory: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(directory.rglob("*.py")):
        package = _package_for_path(path, directory)
        bad = _forbidden_imports(path.read_text(encoding="utf-8"), package=package)
        if bad:
            found[path.relative_to(directory).as_posix()] = bad
    return found


def test_tci_package_has_expected_modules() -> None:
    assert PACKAGE_DIR.is_dir(), "src/rigplane/tci/ does not exist"
    actual = frozenset(
        p.relative_to(PACKAGE_DIR).as_posix() for p in PACKAGE_DIR.rglob("*.py")
    )
    assert actual == EXPECTED_MODULES


def test_tci_imports_only_stdlib_and_itself() -> None:
    assert PACKAGE_DIR.is_dir(), "src/rigplane/tci/ does not exist"
    assert _scan_package(PACKAGE_DIR) == {}


@pytest.mark.parametrize(
    "source",
    [
        "import numpy\n",
        "from rigplane.core import x\n",
        "from rigplane.core.types import x\n",
        "from ..diagnostics._logging import x\n",
        "from .. import x\n",
    ],
)
def test_scanner_reports_forbidden_imports(source: str) -> None:
    """The check itself must be able to fail (spec 4.1: shown able to fail)."""
    assert _forbidden_imports(source) != []


def test_scanner_allows_shared_ws_import() -> None:
    """The leaf may import the shared lowest layer ``rigplane.ws``."""
    assert _forbidden_imports("from rigplane.ws import WebSocketConnection\n") == []


def test_shared_ws_module_is_stdlib_only() -> None:
    """``src/rigplane/ws.py`` exists and imports only the standard library."""
    assert WS_PATH.is_file(), "src/rigplane/ws.py does not exist"
    assert (
        _forbidden_imports(WS_PATH.read_text(encoding="utf-8"), package="rigplane")
        == []
    )


def test_scanner_nested_extra_forbidden_import_fails() -> None:
    """A forbidden import inside ``tci/extra/foo.py`` must fail."""
    assert _forbidden_imports("import numpy\n", package="rigplane.tci.extra") != []


def test_scanner_allows_same_package_relative_import() -> None:
    """A relative import inside ``rigplane.tci`` stays inside the leaf."""
    assert _forbidden_imports("from .protocol import TciCommand\n") == []


def test_scanner_nested_package_forbidden_import_fails() -> None:
    """A forbidden import inside ``tci/extra/__init__.py`` must fail."""
    assert _forbidden_imports("import numpy\n", package="rigplane.tci.extra") != []


def test_scanner_nested_package_valid_relative_passes() -> None:
    """A valid relative import inside ``tci/extra`` stays inside the leaf."""
    assert (
        _forbidden_imports(
            "from ..protocol import TciCommand\n",
            package="rigplane.tci.extra",
        )
        == []
    )


def test_scan_package_recurses_into_nested_packages(tmp_path: Path) -> None:
    """The scan sees every ``*.py`` under the package, with real packages."""
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "__init__.py").write_text("import numpy\n", encoding="utf-8")
    (tmp_path / "ws.py").write_text(
        "from .protocol import TciCommand\n", encoding="utf-8"
    )
    found = _scan_package(tmp_path)
    assert "extra/__init__.py" in found
    assert "ws.py" not in found


def test_scan_package_nested_valid_relative_passes(tmp_path: Path) -> None:
    """A nested valid relative import resolves against its own package."""
    extra = tmp_path / "extra"
    extra.mkdir()
    # From ``rigplane.tci.extra``, ``from .. import x`` stays inside the
    # leaf (``rigplane.tci``); against the top package it would escape.
    # The sentinel proves the scan actually recursed (pre-fix it misses
    # both files, so both assertions fail there).
    (extra / "__init__.py").write_text("from .. import x\n", encoding="utf-8")
    (extra / "bad.py").write_text("import numpy\n", encoding="utf-8")
    found = _scan_package(tmp_path)
    assert "extra/bad.py" in found
    assert "extra/__init__.py" not in found
