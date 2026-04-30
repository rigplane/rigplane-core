"""Classify import edges in static-cycle SCCs by execution context.

For each edge ``A -> B`` where both endpoints are inside a reported SCC,
walk ``A``'s AST and find every ``Import`` / ``ImportFrom`` whose target
resolves to ``B``. Classify each occurrence as one of:

  module          — top-level import (executes at import time; runtime cycle)
  type_checking   — under ``if TYPE_CHECKING:`` (no runtime effect)
  function_local  — inside a function/method body (deferred until called)
  conditional     — under any other ``If`` / ``Try`` (runtime-conditional)

A cycle is a *runtime cycle* if every edge has at least one ``module`` site.
A cycle is *deferred-only* if every edge can be classified entirely as
``type_checking`` or ``function_local`` for at least one occurrence (and
no ``module`` site exists). A cycle is *mixed* otherwise.

Known limitation
----------------
Imports inside ``try: ... except ImportError: ...`` shims are tagged
``conditional`` and excluded from the runtime-cycle subgraph. That is
correct for shims that swallow ``ImportError`` — the import does not
contribute to a cycle that breaks startup. It is **wrong** for cycles
broken by a ``try:`` shim that re-raises on a non-``ImportError``
condition. The current codebase has no such case (the only ``try:``
import is ``rigctld/__init__.py``, a swallow shim), but a future
codebase with ``try:``-import workarounds for cycle-breaking would
need this classifier extended.

Usage::

    uv run python docs/plans/discovery-artifacts/classify_cycle_edges.py \\
        docs/plans/discovery-artifacts/cycles.txt \\
        docs/plans/discovery-artifacts/import-graph.dot
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC_ROOT = Path("src/icom_lan")
INTERNAL_ROOT = "icom_lan"


def _module_to_path(dotted: str) -> Path:
    rel = dotted[len(INTERNAL_ROOT) + 1 :].replace(".", "/") if "." in dotted else ""
    pkg_init = SRC_ROOT / rel / "__init__.py"
    mod_py = SRC_ROOT / f"{rel}.py" if rel else SRC_ROOT / "__init__.py"
    if pkg_init.exists():
        return pkg_init
    if mod_py.exists():
        return mod_py
    raise FileNotFoundError(dotted)


def _enclosing_package(path: Path) -> str:
    if path.name == "__init__.py":
        rel = path.parent.relative_to(SRC_ROOT.parent)
    else:
        rel = path.relative_to(SRC_ROOT.parent).with_suffix("")
    parts = list(rel.parts)
    if path.name == "__init__.py":
        return ".".join(parts)
    return ".".join(parts[:-1])


def _resolve_relative(enclosing: str, level: int, module: str | None) -> str | None:
    if level == 0:
        return module
    base_parts = enclosing.split(".")
    drop = level - 1
    if drop > len(base_parts):
        return None
    pkg = ".".join(base_parts[: len(base_parts) - drop]) if drop else enclosing
    if module:
        return f"{pkg}.{module}" if pkg else module
    return pkg


def _resolves_to(target: str, candidate: str) -> bool:
    """True if `candidate` (resolved import target) is the module `target` or a parent of it."""
    if target == candidate:
        return True
    return target.startswith(candidate + ".") or candidate.startswith(target + ".")


def _classify_node_context(stack: list[ast.AST]) -> str:
    for node in reversed(stack):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return "function_local"
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                return "type_checking"
            if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
                return "type_checking"
            return "conditional"
        if isinstance(node, ast.Try):
            return "conditional"
    return "module"


def _find_edges(src_module: str, target_module: str) -> list[tuple[int, str]]:
    """Return [(lineno, kind)] for every import in `src_module` resolving to `target_module`."""
    src_path = _module_to_path(src_module)
    enclosing = _enclosing_package(src_path)
    tree = ast.parse(src_path.read_text())
    hits: list[tuple[int, str]] = []
    stack: list[ast.AST] = []

    class V(ast.NodeVisitor):
        def generic_visit(self, node: ast.AST) -> None:  # type: ignore[override]
            stack.append(node)
            super().generic_visit(node)
            stack.pop()

        def visit_Import(self, node: ast.Import) -> None:  # type: ignore[override]
            for alias in node.names:
                if _resolves_to(target_module, alias.name):
                    kind = _classify_node_context(stack)
                    hits.append((node.lineno, kind))
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # type: ignore[override]
            resolved = _resolve_relative(enclosing, node.level, node.module)
            if resolved is None:
                self.generic_visit(node)
                return
            if not node.module:
                # `from . import x` — names are submodules of resolved
                for alias in node.names:
                    candidate = f"{resolved}.{alias.name}" if resolved else alias.name
                    if _resolves_to(target_module, candidate):
                        kind = _classify_node_context(stack)
                        hits.append((node.lineno, kind))
            else:
                if _resolves_to(target_module, resolved):
                    kind = _classify_node_context(stack)
                    hits.append((node.lineno, kind))
            self.generic_visit(node)

    V().visit(tree)
    return hits


def _load_edges_from_dot(dot_path: Path) -> dict[str, set[str]]:
    """Parse a DOT file produced by discovery_graph.py into adjacency map."""
    graph: dict[str, set[str]] = {}
    for line in dot_path.read_text().splitlines():
        line = line.strip().rstrip(";")
        if "->" not in line:
            continue
        # Format: "src" -> "tgt"
        src_part, _, tgt_part = line.partition("->")
        src = src_part.strip().strip('"')
        tgt = tgt_part.strip().strip('"')
        graph.setdefault(src, set()).add(tgt)
    return graph


def _intra_scc_edges(scc: set[str], graph: dict[str, set[str]]) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for src in scc:
        for tgt in graph.get(src, ()):
            if tgt in scc:
                edges.append((src, tgt))
    return sorted(edges)


def main(argv: list[str]) -> int:
    cycles_path = Path(argv[1])
    dot_path = Path(argv[2])
    graph = _load_edges_from_dot(dot_path)

    text = cycles_path.read_text()
    sccs: list[set[str]] = []
    for line in text.splitlines():
        if line.startswith("CYCLE:"):
            chain = line[len("CYCLE:") :].strip()
            nodes = {n.strip() for n in chain.split("->") if n.strip()}
            if nodes:
                sccs.append(nodes)

    print(f"# Cycle classification ({len(sccs)} SCCs)")
    print()
    for scc in sccs:
        edges = _intra_scc_edges(scc, graph)
        scc_label = " ↔ ".join(sorted(scc))
        print(f"## SCC: {scc_label} ({len(scc)} nodes, {len(edges)} intra-SCC edges)")
        print()
        edge_kinds: list[set[str]] = []
        for src, tgt in edges:
            try:
                hits = _find_edges(src, tgt)
            except FileNotFoundError:
                hits = []
            kinds = {k for _, k in hits}
            edge_kinds.append(kinds)
            line_summary = ", ".join(f"L{ln}:{k}" for ln, k in hits) or "(none)"
            print(f"- {src} → {tgt}: {line_summary}")
        # Verdict — does there exist a runtime cycle (subset of edges, all
        # with at least one 'module' site, that still forms a cycle)?
        runtime_only_graph: dict[str, set[str]] = {}
        for (src, tgt), kinds in zip(edges, edge_kinds, strict=True):
            if "module" in kinds:
                runtime_only_graph.setdefault(src, set()).add(tgt)
        # quick reachability check: does any node in scc reach itself in runtime_only_graph?
        def reaches_self(start: str) -> bool:
            seen = {start}
            stack = [start]
            while stack:
                cur = stack.pop()
                for nxt in runtime_only_graph.get(cur, ()):
                    if nxt == start:
                        return True
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            return False

        runtime_cycle_present = any(reaches_self(n) for n in scc)
        verdict = (
            "RUNTIME CYCLE (at least one edge subset forms a cycle of top-level imports)"
            if runtime_cycle_present
            else "DEFERRED-ONLY (no runtime cycle survives when only top-level imports are kept)"
        )
        print()
        print(f"**Verdict:** {verdict}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
