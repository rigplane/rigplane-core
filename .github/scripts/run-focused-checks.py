#!/usr/bin/env python3
"""Validate and run narrow development checks without shell interpolation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from typing import Callable, Sequence


REVISION_RE = re.compile(r"[0-9a-f]{40}")
PATH_RE = re.compile(r"[A-Za-z0-9_./-]+")
NODEID_RE = re.compile(r"[A-Za-z0-9_./:\[\],=+@-]+")
MAX_TARGETS_PER_KIND = 64


class InputError(ValueError):
    """Raised when a workflow input is outside the focused-check contract."""


def _load_array(name: str, raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(f"{name} must be a JSON array: {exc.msg}") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InputError(f"{name} must be a JSON array of strings")
    if len(value) > MAX_TARGETS_PER_KIND:
        raise InputError(f"{name} has more than {MAX_TARGETS_PER_KIND} targets")
    return value


def _validate_path(
    value: str,
    *,
    roots: tuple[str, ...],
    repo: Path,
    must_be_file: bool = False,
) -> str:
    if not value or value.startswith("-") or not PATH_RE.fullmatch(value):
        raise InputError(f"unsafe target: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.parts[0] not in roots:
        raise InputError(f"target is outside allowed roots {roots}: {value!r}")

    candidate = repo.joinpath(*path.parts)
    try:
        candidate.resolve(strict=True).relative_to(repo.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise InputError(
            f"target does not resolve inside the checkout: {value!r}"
        ) from exc
    if must_be_file and not candidate.is_file():
        raise InputError(f"target must be a file: {value!r}")
    return value


def build_plan(
    *,
    revision: str,
    pytest_raw: str,
    ruff_raw: str,
    vitest_raw: str,
    repo: Path,
) -> dict[str, object]:
    if not REVISION_RE.fullmatch(revision):
        raise InputError("revision must be an exact 40-character lowercase commit SHA")

    pytest_targets: list[str] = []
    for nodeid in _load_array("pytest_targets", pytest_raw):
        if not nodeid or nodeid.startswith("-") or not NODEID_RE.fullmatch(nodeid):
            raise InputError(f"unsafe pytest nodeid: {nodeid!r}")
        path_part = nodeid.split("::", 1)[0]
        _validate_path(path_part, roots=("tests",), repo=repo, must_be_file=True)
        if not path_part.endswith(".py"):
            raise InputError(f"pytest target must name a Python test file: {nodeid!r}")
        pytest_targets.append(nodeid)

    ruff_targets = [
        _validate_path(value, roots=("src", "tests"), repo=repo)
        for value in _load_array("ruff_targets", ruff_raw)
    ]

    vitest_targets: list[str] = []
    for value in _load_array("vitest_targets", vitest_raw):
        _validate_path(value, roots=("frontend",), repo=repo, must_be_file=True)
        if not value.endswith((".js", ".jsx", ".ts", ".tsx")):
            raise InputError(f"Vitest target must name a JS/TS test file: {value!r}")
        vitest_targets.append(value)

    if not pytest_targets and not ruff_targets and not vitest_targets:
        raise InputError("at least one focused target is required")

    return {
        "revision": revision,
        "pytest_targets": pytest_targets,
        "ruff_targets": ruff_targets,
        "vitest_targets": vitest_targets,
    }


def _record_outputs(plan: dict[str, object]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    has_python = bool(plan["pytest_targets"] or plan["ruff_targets"])
    has_frontend = bool(plan["vitest_targets"])
    with Path(output_path).open("a", encoding="utf-8") as output:
        output.write(f"has_python={str(has_python).lower()}\n")
        output.write(f"has_frontend={str(has_frontend).lower()}\n")


Runner = Callable[..., subprocess.CompletedProcess[bytes]]


def execute_plan(
    plan: dict[str, object],
    *,
    repo: Path,
    runner: Runner = subprocess.run,
) -> list[dict[str, object]]:
    commands: list[tuple[str, list[str], Path]] = []
    pytest_targets = list(plan["pytest_targets"])
    ruff_targets = list(plan["ruff_targets"])
    vitest_targets = list(plan["vitest_targets"])

    if pytest_targets:
        commands.append(("pytest", ["uv", "run", "pytest", *pytest_targets], repo))
    if ruff_targets:
        commands.extend(
            (
                ("ruff-check", ["uv", "run", "ruff", "check", *ruff_targets], repo),
                (
                    "ruff-format",
                    ["uv", "run", "ruff", "format", "--check", *ruff_targets],
                    repo,
                ),
            )
        )
    if vitest_targets:
        relative_targets = [
            str(PurePosixPath(value).relative_to("frontend"))
            for value in vitest_targets
        ]
        commands.append(
            (
                "vitest",
                ["npm", "exec", "--", "vitest", "run", *relative_targets],
                repo / "frontend",
            )
        )

    results: list[dict[str, object]] = []
    for name, command, cwd in commands:
        started = time.monotonic()
        completed = runner(command, cwd=cwd, check=False)
        results.append(
            {
                "name": name,
                "argv": command,
                "returncode": completed.returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        )
    return results


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        plan = build_plan(
            revision=os.environ.get("FOCUSED_REVISION", ""),
            pytest_raw=os.environ.get("FOCUSED_PYTEST_TARGETS", "[]"),
            ruff_raw=os.environ.get("FOCUSED_RUFF_TARGETS", "[]"),
            vitest_raw=os.environ.get("FOCUSED_VITEST_TARGETS", "[]"),
            repo=args.repo,
        )
    except InputError as exc:
        print(f"focused-check input rejected: {exc}", file=sys.stderr)
        return 2

    args.plan.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    _record_outputs(plan)
    if not args.execute:
        return 0

    if args.results is None:
        print("--results is required with --execute", file=sys.stderr)
        return 2
    results = execute_plan(plan, repo=args.repo)
    args.results.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0 if all(result["returncode"] == 0 for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
