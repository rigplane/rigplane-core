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
DOC_SUFFIXES = {".md", ".rst"}
DOC_EXACT = {
    ".github/scripts/doc-citation-baseline.txt",
    ".github/scripts/doc-citation-dangling-baseline.txt",
    ".github/scripts/doc-link-baseline.txt",
    "AUTHORS",
    "COPYING",
    "LICENSE",
    "LICENSE.txt",
    "NOTICE",
}
CI_EXACT = {"tests/test_ci_path_filters.py"}
CORE_EXACT = {"pyproject.toml", "uv.lock", ".importlinter"}
# Paths under docs/ that a test under tests/ reads (Path(...)/open()/
# read_text()) at test time, not merely cites in a comment or docstring — a
# change to any of these can break the named test(s), so they must run
# pytest like any other test input rather than take the docs-only fast path.
# Each comment stops being true, and the entry should be removed, once the
# named test stops reading that file.
CORE_DOCS_TEST_INPUT_EXACT = {
    "docs/internals/ui-radio-control-contract.toml",  # tests/architecture/test_ui_radio_control_contract.py, tests/test_mor1408_registry_projection.py
    "docs/parity/ic7610_command_matrix.json",  # tests/test_ic7610_parity_matrix.py
    "docs/PROJECT.md",  # tests/test_ic7610_parity_matrix.py
    "docs/parity/README.md",  # tests/test_ic7610_parity_matrix.py
    "docs/operations/managed-runtime-packaging.md",  # tests/test_managed_runtime_packaging_doc.py
    "docs/api/web.md",  # tests/test_web_api_contract.py
    "docs/guide/web-ui.md",  # tests/test_web_api_contract.py, tests/test_docs_runtime_sync.py
    "docs/api/command-catalog.md",  # tests/test_handlers_coverage.py, tests/test_command_catalog_docs.py
    "docs/api/radio.md",  # tests/test_docs_runtime_sync.py
    "docs/guide/connection.md",  # tests/test_docs_runtime_sync.py
    "docs/api/audio.md",  # tests/test_docs_runtime_sync.py
    "docs/guide/audio-recipes.md",  # tests/test_docs_runtime_sync.py
    "docs/guide/diagnostic-reports.md",  # tests/test_docs_runtime_sync.py
    "docs/internals/audio-capture-health.md",  # tests/test_docs_runtime_sync.py
}


class ClassificationError(ValueError):
    """Raised when the requested diff cannot be classified safely."""


def _parts(path: str) -> tuple[str, ...]:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ClassificationError(f"invalid repository path: {path!r}")
    return pure.parts


def is_documentation(path: str) -> bool:
    parts = _parts(path)
    if path in CORE_DOCS_TEST_INPUT_EXACT:
        return False
    suffix = PurePosixPath(path).suffix.lower()
    return (
        parts[0] in {"docs", ".claude"} or suffix in DOC_SUFFIXES or path in DOC_EXACT
    )


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
    if path in CORE_DOCS_TEST_INPUT_EXACT:
        return True
    if is_documentation(path) or is_ci_control(path):
        return False
    return parts[0] in {"src", "tests", "rigs", "contracts"} or path in CORE_EXACT


def classify(paths: Sequence[str]) -> dict[str, bool]:
    normalized = list(dict.fromkeys(paths))
    if not normalized:
        raise ClassificationError("the exact diff contains no changed paths")
    return {
        "core": any(is_core(path) for path in normalized),
        "frontend": any(is_frontend(path) for path in normalized),
        "ci": any(is_ci_control(path) for path in normalized),
        "docs": all(is_documentation(path) for path in normalized),
    }


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
