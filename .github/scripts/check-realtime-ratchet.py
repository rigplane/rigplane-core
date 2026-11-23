#!/usr/bin/env python3
"""Check a candidate Git tree against its base's test timing budget (MOR-3920).

To initialize a repository, run with --generate --base REF and save stdout as
the baseline. Generate from the base tree before changing any tests.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import subprocess
import sys
from collections.abc import Callable

Finding = tuple[int, str]
Scanner = Callable[[str], list[Finding]]
_TEST_DIRS = {"test", "tests", "__tests__", "e2e", "testkit"}
_JS_TEST = re.compile(r"(?:[._](?:test|spec))\.[cm]?[jt]sx?$")
_JS_CODE = re.compile(
    r"//[^\r\n]*|/\*[\s\S]*?\*/|"
    r"'(?:\\[\s\S]|[^'\\])*'|\"(?:\\[\s\S]|[^\"\\])*\"|`(?:\\[\s\S]|[^`\\])*`|"
    r"\bfunction\s+setTimeout\s*(?=\()|\bsetTimeout\s*\(\s*[\w$]+\??\s*:|"
    r"(?P<timer>(?<![\w$])setTimeout|\.\s*waitForTimeout)\s*(?=\()"
)
_PYTHON_HELP = (
    "use virtual_clock and clock.elapse, or wait_until / await_until for a real event"
)
_FRONTEND_HELP = "use a Playwright assertion (expect.poll) for an event or vi.useFakeTimers for a clock"


def scan_frontend(source: str) -> list[Finding]:
    """Recognize test timer calls while skipping comments and quoted source examples."""
    findings = []
    marked = set()
    line = 1
    offset = 0
    for match in _JS_CODE.finditer(source):
        line += source.count("\n", offset, match.start())
        raw = match.group()
        if match["timer"]:
            findings.append((line, match["timer"].removeprefix(".").strip()))
        elif (
            raw.startswith("// real-time:")
            and raw.removeprefix("// real-time:").strip()
        ):
            marked.add(line)
        line += raw.count("\n")
        offset = match.end()
    return [(line, label) for line, label in findings if line not in marked]


def test_language(path: str) -> str | None:
    item = PurePosixPath(path)
    in_tests = bool(_TEST_DIRS.intersection(item.parts[:-1]))
    if item.suffix == ".py" and (
        in_tests
        or item.name == "conftest.py"
        or item.stem.startswith("test_")
        or item.stem.endswith(("_test", ".test"))
    ):
        return "python"
    if _JS_TEST.search(item.name) or (
        in_tests
        and re.fullmatch(r"\.[cm]?[jt]sx?", item.suffix)
        and "frontend" in item.parts
    ):
        return "frontend"
    return None


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE
    ).stdout


def paths(data: bytes) -> set[str]:
    return {os.fsdecode(path) for path in data.split(b"\0") if path}


class Tree:
    def __init__(self, repo: Path, ref: str, scan_python: Scanner):
        self.repo = repo
        self.sha = (
            git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
            .decode()
            .strip()
        )
        self.files = paths(git(repo, "ls-tree", "-r", "--name-only", "-z", self.sha))
        self.scan_python = scan_python

    def read(self, path: str) -> bytes:
        return git(self.repo, "cat-file", "blob", f"{self.sha}:{path}")

    def findings(self, path: str) -> tuple[list[Finding], list[str]]:
        if path not in self.files or (language := test_language(path)) is None:
            return [], []
        try:
            source = self.read(path)
            if language == "python":
                # decode just as Python does, including a PEP 263 encoding declaration
                import io
                import tokenize

                encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
                decoded = source.decode(encoding)
                findings = self.scan_python(decoded)
            else:
                decoded = source.decode("utf-8-sig")
                findings = scan_frontend(decoded)
        except (SyntaxError, UnicodeError) as exc:
            raise ValueError(
                f"{path}:{getattr(exc, 'lineno', None) or 1}: {exc}"
            ) from exc
        return findings, decoded.splitlines()

    def inventory(self) -> dict[str, int]:
        counts = {}
        for path in sorted(self.files):
            findings, _ = self.findings(path)
            if findings:
                counts[path] = len(findings)
        return counts

    def baseline(self, path: str) -> dict[str, int]:
        counts = json.loads(self.read(path))
        if not isinstance(counts, dict) or any(
            test_language(name) is None or type(count) is not int or count <= 0
            for name, count in counts.items()
        ):
            raise ValueError(f"{path} must map test paths to positive offender counts")
        return counts


def new_findings(
    base: Tree, path: str, findings: list[Finding], source_lines: list[str]
) -> list[Finding]:
    """Prefer diagnostics on newly introduced statements even when inserted above old ones."""
    old, old_lines = base.findings(path)
    existing = Counter((label, old_lines[line - 1].strip()) for line, label in old)
    added = []
    for line, label in findings:
        key = label, source_lines[line - 1].strip()
        if not existing[key]:
            added.append((line, label))
        else:
            existing[key] -= 1
    return added


def check(base: Tree, head: Tree, baseline_path: str) -> list[str]:
    previous = (
        base.baseline(baseline_path)
        if baseline_path in base.files
        else base.inventory()
    )
    if baseline_path not in head.files:
        return [
            f"{baseline_path}: retain the timing baseline (use an empty object when clean)"
        ]
    current = head.baseline(baseline_path)
    changed = paths(
        git(
            head.repo,
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            base.sha,
            head.sha,
            "--",
        )
    )
    affected = {
        path
        for path in previous.keys() | current.keys()
        if previous.get(path) != current.get(path)
    }
    errors = []
    for path in sorted(changed | affected):
        if test_language(path) is None:
            continue
        ceiling = previous.get(path, 0)
        recorded = current.get(path, 0)
        findings, source_lines = head.findings(path)
        actual = len(findings)
        if recorded > ceiling:
            errors.append(
                f"{baseline_path}: baseline may only shrink ({path}: {ceiling} -> {recorded})"
            )
        if actual > min(ceiling, recorded):
            help_text = (
                _PYTHON_HELP if test_language(path) == "python" else _FRONTEND_HELP
            )
            added = (
                new_findings(base, path, findings, source_lines)
                if ceiling
                else findings
            )
            for line, kind in added or findings:
                errors.append(f"{path}:{line}: {kind}. {help_text}")
        elif actual < recorded:
            update = f"remove {path}" if actual == 0 else f"reduce {path} to {actual}"
            errors.append(
                f"{baseline_path}: {update} ({actual} real-time offenders remain)"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True, help="base Git revision")
    parser.add_argument("--head", default="HEAD", help="candidate Git revision")
    parser.add_argument(
        "--baseline", default=".github/scripts/realtime-ratchet-baseline.json"
    )
    parser.add_argument(
        "--generate", action="store_true", help="print the base tree inventory"
    )
    parser.add_argument(
        "--sdk-source", type=Path, help="load the SDK analyzer from a Core checkout"
    )
    args = parser.parse_args()
    try:
        if args.sdk_source:
            # The light runner can use the stdlib-only analyzer without installing Core.
            scan_python = runpy.run_path(str(args.sdk_source))["scan_python"]
        else:
            from rigplane.testing.realtime import scan_python

        base = Tree(args.repo, args.base, scan_python)
        if args.generate:
            print(json.dumps(base.inventory(), indent=2))
            return 0
        head = Tree(args.repo, args.head, scan_python)
        errors = check(base, head, args.baseline)
    except (OSError, ValueError, ImportError, subprocess.CalledProcessError) as exc:
        print(
            f"realtime ratchet could not verify the candidate: {exc}", file=sys.stderr
        )
        return 1
    for error in errors:
        print(error)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
