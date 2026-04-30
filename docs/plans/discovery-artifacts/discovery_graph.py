"""AST-based discovery walker for icom-lan internal modularization (Phase 1).

This script walks every ``.py`` file under ``src/icom_lan/`` and emits three
deterministic artifacts:

  * ``import-graph.dot``   — DOT graph of intra-package import edges.
  * ``cycles.txt``         — Tarjan SCCs of size > 1 (cycle report).
  * ``file-inventory.json``— per-module metadata (summary, ``__all__``, side
                             effects, dynamic imports, PEP 562 ``__getattr__``).

Design choices (locked-in for Phase 1, see orchestrator brief):

  Import edge resolution
      ``from X import Y`` (where X is a non-empty module path) produces a
      single edge to ``X`` if ``X`` resolves to an internal package or
      module. The name ``Y`` is NOT a separate edge target, even when ``Y``
      happens to be a submodule of ``X`` (this undercount is consistent with
      the spec's "edge to X" wording; ``__init__.py`` re-export style
      imports are the typical victims).
      ``from . import x, y`` (and any ``from <relative-package> import x``
      where ``module`` is empty after resolution) is the special case
      ``names`` are submodule names of the resolved package, so we emit an
      edge to ``<resolved>.<x>`` for each ``x`` that resolves to an
      internal module. Without this carve-out every sibling re-export
      manufactures a spurious parent-package cycle.
      ``import a.b.c`` produces an edge to ``a.b.c`` only when ``a.b.c``
      resolves to an internal file/package.
      Relative imports (``from . import x`` / ``from ..foo import y``) are
      resolved against the source file's enclosing package. For
      ``__init__.py`` the enclosing package IS the directory.

  Module dotted paths
      ``src/icom_lan/__init__.py`` → ``icom_lan`` (never ``icom_lan.__init__``)
      ``src/icom_lan/audio/__init__.py`` → ``icom_lan.audio``
      ``src/icom_lan/foo.py`` → ``icom_lan.foo``

  ``__all__`` extraction
      Handles both ``Assign`` and ``AnnAssign``. Value is parsed via
      ``ast.literal_eval``; on failure we record ``null`` (no guessing).

  ``__init__.py`` side-effect allowlist (everything else is reported)
      * ``Import`` / ``ImportFrom``
      * ``Assign`` / ``AnnAssign`` whose every target is a ``Name`` that
        is a dunder (``__name__``, starts and ends with ``__``)
      * ``FunctionDef`` named ``__getattr__`` (PEP 562; also sets
        ``has_pep562_getattr=True``)
      * ``Try`` whose body contains only imports and whose handlers all
        match ``ImportError`` (or a tuple containing it) — typical optional
        import shim
      * Module docstring (first ``Expr(Constant(str))``)
      * ``If`` whose test is ``TYPE_CHECKING`` and whose body is import-only
        (documented exemption)

  Dynamic imports
      All ``Call`` nodes are inspected (not just top-level). Matched names:
      ``importlib.import_module``, ``__import__``, ``importlib.util.find_spec``.
      Recorded as ``"line:col"`` strings.

  Determinism
      All collections are sorted before serialisation. Re-running the script
      against the same source tree must produce byte-identical outputs.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

INTERNAL_ROOT_PACKAGE = "icom_lan"

DYNAMIC_IMPORT_NAMES = (
    "importlib.import_module",
    "importlib.util.find_spec",
    "__import__",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _module_dotted_path(file_path: Path, src_root: Path) -> str:
    """Convert a ``.py`` file path under ``src_root`` to its dotted module name.

    ``src/icom_lan/__init__.py`` → ``icom_lan``
    ``src/icom_lan/audio/__init__.py`` → ``icom_lan.audio``
    ``src/icom_lan/foo.py`` → ``icom_lan.foo``
    """
    rel = file_path.relative_to(src_root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]  # strip ".py"
    return ".".join(parts)


def _enclosing_package(module_dotted: str, is_init: bool) -> str:
    """Return the package name used to resolve relative imports.

    For ``__init__.py`` the enclosing package is the module itself.
    For a regular module ``a.b.c`` the enclosing package is ``a.b``.
    """
    if is_init:
        return module_dotted
    if "." not in module_dotted:
        return ""
    return module_dotted.rsplit(".", 1)[0]


def _resolve_relative(
    enclosing_package: str, level: int, module: str | None
) -> str | None:
    """Resolve a relative import to an absolute dotted path.

    Returns ``None`` if the relative reference walks past the root package.
    """
    if level == 0:
        return module
    parts = enclosing_package.split(".") if enclosing_package else []
    # Walk up `level - 1` steps (level=1 means current package).
    if level - 1 > len(parts):
        return None
    base_parts = parts[: len(parts) - (level - 1)] if level > 1 else parts
    if module:
        base_parts = base_parts + module.split(".")
    if not base_parts:
        return None
    return ".".join(base_parts)


def _docstring_summary(tree: ast.Module) -> str | None:
    docstring = ast.get_docstring(tree)
    if not docstring:
        return None
    for line in docstring.splitlines():
        line = line.strip()
        if line:
            return line
    return None


def _structural_summary(tree: ast.Module) -> str:
    """Fallback summary derived from top-level class/function names."""
    classes: list[str] = []
    functions: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                functions.append(node.name)
    parts: list[str] = []
    if classes:
        parts.append("classes: " + ", ".join(classes))
    if functions:
        parts.append("functions: " + ", ".join(functions))
    return "; ".join(parts) if parts else "(no docstring; no public top-level defs)"


def _extract_all(tree: ast.Module) -> Any | None:
    """Return the ``__all__`` literal value if extractable, else ``None``."""
    for node in tree.body:
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target] if node.target is not None else []
            value = node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                if value is None:
                    return None
                try:
                    return ast.literal_eval(value)
                except (ValueError, SyntaxError):
                    return None
    return None


def _is_dunder_name(name: str) -> bool:
    return len(name) >= 4 and name.startswith("__") and name.endswith("__")


def _is_dunder_assignment(node: ast.AST) -> bool:
    """True if every assignment target is a ``Name`` that is a dunder."""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target] if node.target is not None else []
    else:
        return False
    if not targets:
        return False
    for target in targets:
        if not isinstance(target, ast.Name):
            return False
        if not _is_dunder_name(target.id):
            return False
    return True


def _try_block_is_import_shim(node: ast.Try) -> bool:
    """A try/except is an ImportError shim if body is only imports and every
    handler catches ImportError (or a tuple containing it).
    """
    if not all(isinstance(stmt, (ast.Import, ast.ImportFrom)) for stmt in node.body):
        return False
    if not node.handlers:
        return False
    for handler in node.handlers:
        if not _handler_catches_import_error(handler):
            return False
    return True


def _handler_catches_import_error(handler: ast.ExceptHandler) -> bool:
    exc_type = handler.type
    if exc_type is None:
        return False
    if isinstance(exc_type, ast.Name) and exc_type.id == "ImportError":
        return True
    if isinstance(exc_type, ast.Tuple):
        return any(
            isinstance(elt, ast.Name) and elt.id == "ImportError"
            for elt in exc_type.elts
        )
    return False


def _is_type_checking_import_block(node: ast.If) -> bool:
    """``if TYPE_CHECKING: <imports only>`` — documented exemption."""
    test = node.test
    is_type_checking = False
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        is_type_checking = True
    elif isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        is_type_checking = True
    if not is_type_checking:
        return False
    return all(isinstance(stmt, (ast.Import, ast.ImportFrom)) for stmt in node.body)


def _has_pep562_getattr(tree: ast.Module) -> bool:
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "__getattr__"
        ):
            return True
    return False


def _init_side_effects(tree: ast.Module) -> list[str]:
    """Walk top-level statements and report anything outside the allowlist."""
    side_effects: list[str] = []
    for index, node in enumerate(tree.body):
        # Allowlisted: import statements
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        # Allowlisted: dunder assignment
        if _is_dunder_assignment(node):
            continue
        # Allowlisted: PEP 562 __getattr__ definition
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "__getattr__"
        ):
            continue
        # Allowlisted: module docstring (first stmt only)
        if (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        # Allowlisted: try/except ImportError import shim
        if isinstance(node, ast.Try) and _try_block_is_import_shim(node):
            continue
        # Allowlisted: if TYPE_CHECKING: <imports only>
        if isinstance(node, ast.If) and _is_type_checking_import_block(node):
            continue
        side_effects.append(f"{node.lineno}:{type(node).__name__}")
    return side_effects


def _dynamic_imports(tree: ast.Module) -> list[str]:
    """Find calls to importlib.import_module, importlib.util.find_spec, __import__."""
    locations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _resolve_call_name(node.func)
        if name in DYNAMIC_IMPORT_NAMES:
            locations.append(f"{node.lineno}:{node.col_offset}")
    return sorted(locations, key=_lc_key)


def _resolve_call_name(node: ast.AST) -> str:
    """Return a dotted-name string for a call target, or '' if not a Name/Attribute."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _resolve_call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _lc_key(loc: str) -> tuple[int, int]:
    line_str, col_str = loc.split(":", 1)
    return (int(line_str), int(col_str))


# ---------------------------------------------------------------------------
# Import edge extraction
# ---------------------------------------------------------------------------


def _extract_import_edges(
    tree: ast.Module,
    module_dotted: str,
    is_init: bool,
    internal_modules: set[str],
) -> set[str]:
    """Return the set of internal modules imported by ``tree``.

    Resolution rules (see module docstring):
      * ``from X import Y`` → edge to ``X`` (only X), if ``X`` is internal.
      * ``import a.b.c`` → edge to ``a.b.c``, if internal.
      * Relative imports resolved against the file's enclosing package.
    """
    enclosing = _enclosing_package(module_dotted, is_init)
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                if _is_internal(target, internal_modules):
                    edges.add(_canonicalise(target, internal_modules))
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_relative(enclosing, node.level, node.module)
            if resolved is None:
                continue
            if not node.module:
                # ``from . import x, y`` (or ``from .. import x``): the
                # ``names`` are submodule names of the resolved package, not
                # attributes of it. Emit an edge per submodule, not to the
                # parent package itself — otherwise every sibling re-export
                # fabricates a parent-package cycle.
                for alias in node.names:
                    candidate = (
                        f"{resolved}.{alias.name}" if resolved else alias.name
                    )
                    if _is_internal(candidate, internal_modules):
                        edges.add(_canonicalise(candidate, internal_modules))
            else:
                if _is_internal(resolved, internal_modules):
                    edges.add(_canonicalise(resolved, internal_modules))
    edges.discard(module_dotted)  # don't emit self-edges
    return edges


def _is_internal(target: str, internal_modules: set[str]) -> bool:
    if target in internal_modules:
        return True
    # Could be a package path; treat any prefix that matches an internal module.
    parts = target.split(".")
    while parts:
        if ".".join(parts) in internal_modules:
            return True
        parts.pop()
    # Fall back to the root-package check for safety.
    return target == INTERNAL_ROOT_PACKAGE or target.startswith(
        INTERNAL_ROOT_PACKAGE + "."
    )


def _canonicalise(target: str, internal_modules: set[str]) -> str:
    """Map a dotted import target to its closest internal module/package node.

    If the exact path exists in the internal map, use it. Otherwise walk up
    the parents and pick the closest one that does.
    """
    if target in internal_modules:
        return target
    parts = target.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in internal_modules:
            return candidate
        parts.pop()
    return target  # should not happen if _is_internal() returned True


# ---------------------------------------------------------------------------
# Tarjan SCC
# ---------------------------------------------------------------------------


def _tarjan_sccs(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return strongly-connected components as lists of nodes (size > 0)."""
    index_counter = [0]
    stack: list[str] = []
    lowlinks: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    sccs: list[list[str]] = []

    def strongconnect(node: str) -> None:
        index[node] = index_counter[0]
        lowlinks[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack[node] = True

        for successor in sorted(graph.get(node, ())):
            if successor not in index:
                strongconnect(successor)
                lowlinks[node] = min(lowlinks[node], lowlinks[successor])
            elif on_stack.get(successor):
                lowlinks[node] = min(lowlinks[node], index[successor])

        if lowlinks[node] == index[node]:
            component: list[str] = []
            while True:
                successor = stack.pop()
                on_stack[successor] = False
                component.append(successor)
                if successor == node:
                    break
            sccs.append(sorted(component))

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    for node in sorted(graph):
        if node not in index:
            strongconnect(node)
    return sccs


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def _iter_python_files(root: Path) -> Iterable[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _build_inventory(
    src_root: Path, package_root: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    """Walk all .py files; return (inventory, edges)."""
    files = list(_iter_python_files(package_root))
    # Build the set of internal module dotted paths first, so import resolution
    # can decide what is internal.
    internal_modules: set[str] = set()
    for path in files:
        internal_modules.add(_module_dotted_path(path, src_root))
    # Also add intermediate package names so an edge to a parent package
    # that has __init__.py resolves cleanly.
    for module in list(internal_modules):
        parts = module.split(".")
        while len(parts) > 1:
            parts.pop()
            internal_modules.add(".".join(parts))

    inventory: dict[str, dict[str, Any]] = {}
    edges: dict[str, set[str]] = {}

    for path in files:
        module_dotted = _module_dotted_path(path, src_root)
        is_init = path.name == "__init__.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        rel_path = str(path.relative_to(src_root.parent))
        summary = _docstring_summary(tree) or _structural_summary(tree)
        all_value = _extract_all(tree)
        side_effects = _init_side_effects(tree) if is_init else []
        dyn_imports = _dynamic_imports(tree)
        has_getattr = _has_pep562_getattr(tree)

        inventory[module_dotted] = {
            "path": rel_path,
            "summary": summary,
            "all": all_value,
            "has_pep562_getattr": has_getattr,
            "dynamic_imports": dyn_imports,
            "init_side_effects": side_effects,
        }
        edges[module_dotted] = _extract_import_edges(
            tree, module_dotted, is_init, internal_modules
        )

    return inventory, edges


def _write_dot(edges: dict[str, set[str]], output: Path) -> int:
    """Write DOT graph; return total edge count."""
    nodes = sorted(edges)
    edge_count = 0
    lines: list[str] = []
    lines.append("digraph icom_lan_imports {")
    lines.append("  rankdir=LR;")
    lines.append('  node [shape=box, fontname="Helvetica", fontsize=10];')
    for node in nodes:
        lines.append(f'  "{node}";')
    for src in nodes:
        for dst in sorted(edges[src]):
            lines.append(f'  "{src}" -> "{dst}";')
            edge_count += 1
    lines.append("}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return edge_count


def _write_cycles(sccs: list[list[str]], output: Path) -> int:
    """Write cycles file; return number of cycles found."""
    cycles = [scc for scc in sccs if len(scc) > 1]
    cycles.sort(key=lambda c: c[0])
    lines = ["# Import cycles (Tarjan SCCs of size > 1)"]
    if not cycles:
        lines.append("# (no cycles found)")
    else:
        for scc in cycles:
            lines.append("CYCLE: " + " -> ".join(scc))
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(cycles)


def _write_inventory(inventory: dict[str, dict[str, Any]], output: Path) -> None:
    sorted_inventory = {key: inventory[key] for key in sorted(inventory)}
    output.write_text(
        json.dumps(sorted_inventory, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover the icom-lan internal import graph (Phase 1)."
    )
    parser.add_argument(
        "--src-root",
        type=Path,
        default=Path("src"),
        help="Path to the src/ directory (default: src)",
    )
    parser.add_argument(
        "--package",
        default=INTERNAL_ROOT_PACKAGE,
        help=f"Internal package name (default: {INTERNAL_ROOT_PACKAGE})",
    )
    parser.add_argument(
        "--graph-output",
        type=Path,
        default=Path("docs/plans/discovery-artifacts/import-graph.dot"),
    )
    parser.add_argument(
        "--cycles-output",
        type=Path,
        default=Path("docs/plans/discovery-artifacts/cycles.txt"),
    )
    parser.add_argument(
        "--inventory-output",
        type=Path,
        default=Path("docs/plans/discovery-artifacts/file-inventory.json"),
    )
    args = parser.parse_args(argv)

    src_root = args.src_root.resolve()
    package_root = src_root / args.package
    if not package_root.is_dir():
        parser.error(f"Package root not found: {package_root}")

    inventory, edges = _build_inventory(src_root, package_root)

    args.graph_output.parent.mkdir(parents=True, exist_ok=True)
    args.cycles_output.parent.mkdir(parents=True, exist_ok=True)
    args.inventory_output.parent.mkdir(parents=True, exist_ok=True)

    edge_count = _write_dot(edges, args.graph_output)
    sccs = _tarjan_sccs({k: set(v) for k, v in edges.items()})
    cycle_count = _write_cycles(sccs, args.cycles_output)
    _write_inventory(inventory, args.inventory_output)

    print(f"FILES: {len(inventory)}")
    print(f"EDGES: {edge_count}")
    print(f"CYCLES: {cycle_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
