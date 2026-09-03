#!/usr/bin/env python3
"""Classify an exact diff for quick CI without shell-expanded file names."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Sequence


SHA_RE = re.compile(r"[0-9a-f]{40}")
CI_EXACT = {"tests/test_ci_path_filters.py"}
CORE_EXACT = {"pyproject.toml", "uv.lock", ".importlinter"}


class ClassificationError(ValueError):
    """Raised when the requested diff cannot be classified safely."""


def _parts(path: str) -> tuple[str, ...]:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ClassificationError(f"invalid repository path: {path!r}")
    return pure.parts


def is_documentation(path: str) -> bool:
    _parts(path)
    module = Path(__file__).with_name("docs-only-paths.js")
    completed = subprocess.run(
        ["node", "-e", "const p=require(process.argv[1]); process.stdout.write(JSON.stringify(p.isDocumentation(process.argv[2])));", str(module), path],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ClassificationError("trusted documentation predicate failed")
    try:
        return bool(json.loads(completed.stdout))
    except json.JSONDecodeError as exc:
        raise ClassificationError("trusted documentation predicate returned invalid JSON") from exc


def is_ci_control(path: str) -> bool:
    parts = _parts(path)
    return path in CI_EXACT or (
        len(parts) >= 2
        and parts[0] == ".github"
        and parts[1] in {"scripts", "workflows"}
        and not is_documentation(path)
    )


def is_frontend(path: str) -> bool:
    parts = _parts(path)
    if is_documentation(path):
        return False
    return parts[0] == "frontend" or parts[:3] == ("src", "rigplane", "web")


def is_core(path: str) -> bool:
    parts = _parts(path)
    if is_documentation(path) or is_ci_control(path):
        return False
    return parts[0] in {"src", "tests", "rigs", "contracts"} or path in CORE_EXACT


def classify(paths: Sequence[str]) -> dict[str, bool]:
    normalized = list(dict.fromkeys(paths))
    if not normalized:
        raise ClassificationError("the exact diff contains no changed paths")
    result = {
        "core": any(is_core(path) for path in normalized),
        "frontend": any(is_frontend(path) for path in normalized),
        "ci": any(is_ci_control(path) for path in normalized),
        "docs": all(is_documentation(path) for path in normalized),
    }
    if not result["docs"] and not any(result[name] for name in ("core", "frontend", "ci")):
        raise ClassificationError("non-documentation diff selected no substantive quick class")
    return result


def _ensure_commit(repo: Path, sha: str) -> None:
    present = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=repo,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if present.returncode == 0:
        return
    fetched = subprocess.run(
        ["git", "fetch", "--no-tags", "--depth=1", "origin", sha],
        cwd=repo,
        check=False,
    )
    if fetched.returncode != 0:
        raise ClassificationError(f"cannot fetch exact commit {sha}")


def changed_paths(repo: Path, *, base: str, head: str) -> list[str]:
    for name, sha in (("base", base), ("head", head)):
        if not SHA_RE.fullmatch(sha):
            raise ClassificationError(f"{name} must be an exact lowercase commit SHA")
        _ensure_commit(repo, sha)

    completed = subprocess.run(
        ["git", "diff", "--name-only", "-z", base, head, "--"],
        cwd=repo,
        check=False,
        stdout=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise ClassificationError("git diff failed")
    return [os.fsdecode(item) for item in completed.stdout.split(b"\0") if item]


def _write_outputs(result: dict[str, bool]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as output:
        for name, selected in result.items():
            output.write(f"{name}={str(selected).lower()}\n")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        paths = changed_paths(args.repo, base=args.base, head=args.head)
        result = classify(paths)
    except ClassificationError as exc:
        print(f"quick path classification failed: {exc}", file=sys.stderr)
        return 2
    _write_outputs(result)
    print(json.dumps({"paths": paths, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
