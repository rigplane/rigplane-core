#!/usr/bin/env python3
"""What the pre-push quick gate (`scripts/local-ci.sh --pre-push`, MOR-4372) must run.

Core's `quick` CI run rejected 30% of pull requests on errors a builder could have found before
the push (frontend install/check/tests/build, Python tests, ruff). This module holds the
decisions; `local-ci.sh` runs them on a test host. It is the Core half of the mechanism
rigplane-pro introduced in MOR-4101; the leg choice reuses CI's own classifier
(`.github/scripts/classify-quick-paths.py`), not a copy of its rules.

Subcommands (all take `--base REF`, default origin/main, and work from the repo root):
  related    print the tests to run: changed test files plus tests that mention a changed file
  legs       print which checks the diff selects, one per line: python | frontend | mypy-web
  ruff-pin   compare `ruff --version` (argument) with the ruff version locked in uv.lock
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
# Names that appear in nearly every test; matching on them would select the whole suite.
GENERIC_NAMES = frozenset({"__init__.py", "conftest.py", "README.md", "main.py"})


@dataclass(frozen=True)
class Change:
    status: str  # A added, M modified, D deleted (renames are reported as D + A)
    path: str


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def merge_base(root: Path, base: str) -> str:
    return _git(root, "merge-base", base, "HEAD").strip()


def changed_files(root: Path, base: str) -> list[Change]:
    """Committed, staged, unstaged and untracked changes against the merge base."""
    out = _git(
        root, "diff", "--name-status", "--no-renames", "-z", merge_base(root, base)
    )
    parts = out.split("\0")
    changes = [Change(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]
    seen = {c.path for c in changes}
    for path in _git(root, "ls-files", "-o", "--exclude-standard", "-z").split("\0"):
        if path and path not in seen:
            changes.append(Change("A", path))
    return changes


def _needles(path: str) -> list[str]:
    needles = [path]
    name = Path(path).name
    if name not in GENERIC_NAMES:
        needles.append(name)
    # the dotted module name stays selective even for a generic basename (`pkg.main`);
    # only a package's own __init__ would reduce to the bare package name
    if path.startswith("src/") and path.endswith(".py") and name != "__init__.py":
        needles.append(path[len("src/") : -len(".py")].replace("/", "."))
    return needles


def related_tests(root: Path, changes: Sequence[Change]) -> list[str]:
    """Changed test files, plus every test whose source mentions a changed file.

    "Mentions" = the repo path, the basename, or (for src/ modules) the dotted module path.
    A test that pins `.github/workflows/quick.yml` therefore runs when that file changes, which
    is exactly the dependency pytest cannot see.
    """
    needles = {n for c in changes for n in _needles(c.path)}
    selected: set[str] = set()
    for test in sorted((root / "tests").rglob("test_*.py")):
        rel = test.relative_to(root).as_posix()
        if any(c.status != "D" and c.path == rel for c in changes):
            selected.add(rel)
            continue
        text = test.read_text(encoding="utf-8", errors="replace")
        if any(n in text for n in needles):
            selected.add(rel)
    return sorted(selected)


def _classifier() -> ModuleType:
    """CI's own path classifier (hyphenated file name, so it is loaded by path)."""
    path = ROOT / ".github/scripts/classify-quick-paths.py"
    spec = importlib.util.spec_from_file_location("_pre_push_classify", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def legs(changes: Sequence[Change]) -> list[str]:
    """The checks of `quick.yml` a diff selects, in the order the gate runs them.

    python: ruff, import-linter, validation goldens (quick-python, `core`).
    frontend: install, lint, i18n, svelte-check, unit tests, build (quick-frontend).
    mypy-web: the strict web-boundary type check, which CI runs only when core AND frontend
    are both selected.
    """
    paths = [c.path for c in changes]
    if not paths:
        return []
    result = _classifier().classify(paths)
    selected = []
    if result["core"]:
        selected.append("python")
    if result["frontend"]:
        selected.append("frontend")
    if result["core"] and result["frontend"]:
        selected.append("mypy-web")
    return selected


def ruff_lock_version(lock_text: str) -> str:
    match = re.search(
        r'^name = "ruff"\nversion = "([0-9][0-9.]*)"', lock_text, re.MULTILINE
    )
    if match is None:
        raise ValueError("uv.lock has no ruff package entry")
    return match.group(1)


def ruff_version_problem(locked: str, version_output: str) -> str | None:
    match = re.search(r"ruff\s+([0-9][0-9.]*)", version_output)
    found = match.group(1) if match else version_output.strip()
    if found == locked:
        return None
    return (
        f"ruff {found or 'of unknown version'} would run, uv.lock pins {locked}: formatting "
        "can differ. Run `uv sync --all-extras` so the locked ruff is the one used."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("command", choices=["related", "legs", "ruff-pin"])
    parser.add_argument(
        "version_output", nargs="?", help="ruff-pin: output of `ruff --version`"
    )
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(argv)
    root = Path.cwd()
    if args.command == "ruff-pin":
        locked = ruff_lock_version((root / "uv.lock").read_text(encoding="utf-8"))
        problem = ruff_version_problem(locked, args.version_output or "")
        if problem:
            print(problem)
        return 1 if problem else 0
    changes = changed_files(root, args.base)
    if args.command == "legs":
        print("\n".join(legs(changes)))
    else:
        print("\n".join(related_tests(root, changes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
