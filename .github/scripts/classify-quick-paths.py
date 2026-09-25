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
# Rule (not a hand list): machine-readable data files under docs/ are never
# documentation, because tests parse them — e.g.
# tests/architecture/test_ui_radio_control_contract.py and
# tests/test_mor1408_registry_projection.py read
# docs/internals/ui-radio-control-contract.toml,
# tests/test_ic7610_parity_matrix.py reads docs/parity/ic7610_command_matrix.json,
# and tests/test_validation_templates.py globs docs/validation/templates/*.json.
# A diff touching one must select the core checks. quick.yml's paths-ignore
# re-includes the same suffixes ("!docs/**/*.toml" etc.), so such a diff now
# reaches this classifier instead of the workflow being skipped wholesale, and
# the docs-only publisher predicate (.github/scripts/docs-only-paths.js) and
# the base route policy (.github/scripts/base-gate-policy-v1.js) apply the same
# suffix rule before treating a docs/** path as documentation. Suffix matching
# is case-insensitive; quick.yml's negations are lowercase-only, which covers
# every test-referenced data file (their paths are exact lowercase literals).
DOCS_DATA_SUFFIXES = {".json", ".toml", ".yaml", ".yml"}
# Hand-maintained list of docs/ PROSE files that a test under tests/ actually
# reads (Path(...)/open()/read_text()) at test time, found by grepping
# tests/ for docs/ path literals and, separately, an open-audit of test
# runs for files opened under docs/ and .claude/. A diff touching one of
# them must select the core checks: quick.yml's paths-ignore re-includes
# each exact path after "docs/**" and "**/*.md", and the docs-only publisher
# predicate (.github/scripts/docs-only-paths.js) and the base route policy
# (.github/scripts/base-gate-policy-v1.js) mirror this list, so neither
# treats such a diff as documentation. Prose under docs/ that tests do not
# read stays documentation-only. This list is NOT complete: files read by
# tests/test_claude_instruction_paths.py under .claude/, and any *.py under
# docs/ or .claude/ walked by tests/support/command_builders.py's
# repository_python_paths, are not listed — grepping for literal path
# strings cannot see either.
CORE_DOCS_TEST_INPUT_EXACT = {
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


def _is_docs_data_file(parts: tuple[str, ...], path: str) -> bool:
    return (
        len(parts) > 1
        and parts[0] == "docs"
        and PurePosixPath(path).suffix.lower() in DOCS_DATA_SUFFIXES
    )


def is_documentation(path: str) -> bool:
    parts = _parts(path)
    if path in CORE_DOCS_TEST_INPUT_EXACT or _is_docs_data_file(parts, path):
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
    if path in CORE_DOCS_TEST_INPUT_EXACT or _is_docs_data_file(parts, path):
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
        # --no-renames: with rename detection (diff.renames defaults on for
        # git diff), --name-only lists only the NEW name of a rename, so the
        # deleted side escaped classification (MOR-2589).
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
