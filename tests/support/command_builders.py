"""Shared command-source inventory for tests."""

from __future__ import annotations

import ast
import functools
import importlib
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CommandBuilderKey = tuple[str, str]

_IGNORED_TREE_PARTS = frozenset({".git", ".venv", "node_modules"})


@dataclass(frozen=True, slots=True)
class ParsedPython:
    """One successfully parsed Python source."""

    path: Path
    tree: ast.Module


@dataclass(frozen=True, slots=True)
class PythonSyntaxFailure:
    """A syntax error retained with source provenance."""

    path: Path
    line: int | None
    message: str


@dataclass(frozen=True, slots=True)
class PythonParseHealth:
    """Aggregate parse results exposed to completeness diagnostics."""

    parsed: tuple[ParsedPython, ...]
    syntax_errors: tuple[PythonSyntaxFailure, ...]

    @property
    def parsed_count(self) -> int:
        return len(self.parsed)

    @property
    def syntax_error_count(self) -> int:
        return len(self.syntax_errors)

    def require_clean(self) -> None:
        """Raise with measured counts and every syntax failure."""
        if not self.syntax_errors:
            return
        details = "; ".join(
            f"{failure.path}:{failure.line or '?'}: {failure.message}"
            for failure in self.syntax_errors
        )
        raise AssertionError(
            f"parse health: parsed={self.parsed_count} "
            f"syntax_errors={self.syntax_error_count}: {details}"
        )


def repository_python_paths(repo_root: Path) -> tuple[Path, ...]:
    """Return repository Python sources outside vendored and build trees."""
    return tuple(
        sorted(
            path
            for path in repo_root.rglob("*.py")
            if not _IGNORED_TREE_PARTS.intersection(path.relative_to(repo_root).parts)
        )
    )


def parse_python_paths(paths: tuple[Path, ...]) -> PythonParseHealth:
    """Parse every path without silently dropping syntax errors."""
    parsed: list[ParsedPython] = []
    failures: list[PythonSyntaxFailure] = []
    for path in sorted(paths):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            failures.append(
                PythonSyntaxFailure(
                    path=path,
                    line=exc.lineno,
                    message=exc.msg,
                )
            )
        else:
            parsed.append(ParsedPython(path=path, tree=tree))
    return PythonParseHealth(tuple(parsed), tuple(failures))


@functools.lru_cache(maxsize=4)
def public_command_builders(command_dir: Path) -> dict[CommandBuilderKey, Any]:
    """Return public command-package functions whose signature accepts ``cmd_map``."""
    found: dict[CommandBuilderKey, Any] = {}
    for path in sorted(command_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        module = importlib.import_module(f"rigplane.commands.{path.stem}")
        for value in vars(module).values():
            if not inspect.isfunction(value) or value.__name__.startswith("_"):
                continue
            if value.__module__ != module.__name__:
                continue
            if "cmd_map" in inspect.signature(value).parameters:
                found[(path.name, value.__name__)] = value
    return found
