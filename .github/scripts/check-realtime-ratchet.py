#!/usr/bin/env python3
"""Check a candidate Git tree against its base's test timing budget (MOR-3920).

To initialize a repository, generate the baseline with --generate --base REF.
To refresh it after committing test edits, also pass --head HEAD.
"""

from __future__ import annotations

import argparse
from collections import Counter
from html import escape
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
from shlex import quote
import subprocess
import sys
from collections.abc import Callable

Finding = tuple[int, str]
Markers = dict[int, str]
Scanner = Callable[[str], tuple[list[Finding], Markers]]
Counts = dict[str, int]
_EMPTY_COUNTS = {"markers": 0, "offenders": 0}
_DEFAULT_BASELINE = ".github/scripts/realtime-ratchet-baseline.json"
_HUNK = re.compile(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
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


def scan_frontend(source: str) -> tuple[list[Finding], Markers]:
    """Recognize test timer calls while skipping comments and quoted source examples."""
    findings = []
    markers = {}
    line = 1
    offset = 0
    for match in _JS_CODE.finditer(source):
        line += source.count("\n", offset, match.start())
        raw = match.group()
        if match["timer"]:
            findings.append((line, match["timer"].removeprefix(".").strip()))
        elif raw.startswith("// real-time:"):
            if reason := raw.removeprefix("// real-time:").strip():
                markers[line] = reason
        line += raw.count("\n")
        offset = match.end()
    return [(line, label) for line, label in findings if line not in markers], markers


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
    def __init__(self, repo: Path, ref: str, analyze_python: Scanner):
        self.repo = repo
        self.sha = (
            git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
            .decode()
            .strip()
        )
        self.files = paths(git(repo, "ls-tree", "-r", "--name-only", "-z", self.sha))
        self.analyze_python = analyze_python

    def read(self, path: str) -> bytes:
        return git(self.repo, "cat-file", "blob", f"{self.sha}:{path}")

    def scan(self, path: str) -> tuple[list[Finding], Markers, list[str]]:
        if path not in self.files or (language := test_language(path)) is None:
            return [], {}, []
        try:
            source = self.read(path)
            if language == "python":
                # decode just as Python does, including a PEP 263 encoding declaration
                import io
                import tokenize

                encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
                decoded = source.decode(encoding)
                findings, markers = self.analyze_python(decoded)
            else:
                decoded = source.decode("utf-8-sig")
                findings, markers = scan_frontend(decoded)
        except (SyntaxError, UnicodeError) as exc:
            raise ValueError(
                f"{path}:{getattr(exc, 'lineno', None) or 1}: {exc}"
            ) from exc
        return findings, markers, decoded.splitlines()

    def inventory(self) -> dict[str, Counts]:
        counts = {}
        for path in sorted(self.files):
            findings, markers, _ = self.scan(path)
            if findings or markers:
                counts[path] = {"markers": len(markers), "offenders": len(findings)}
        return counts

    def baseline(self, path: str) -> dict[str, Counts]:
        counts = json.loads(self.read(path))
        if not isinstance(counts, dict) or any(
            test_language(name) is None
            or not isinstance(entry, dict)
            or entry.keys() != _EMPTY_COUNTS.keys()
            or any(type(count) is not int or count < 0 for count in entry.values())
            or not any(entry.values())
            for name, entry in counts.items()
        ):
            raise ValueError(f"{path} must map test paths to marker and offender counts")
        return counts


def new_findings(
    base: Tree, path: str, findings: list[Finding], source_lines: list[str]
) -> list[Finding]:
    """Prefer diagnostics on newly introduced statements even when inserted above old ones."""
    old, _, old_lines = base.scan(path)
    existing = Counter((label, old_lines[line - 1].strip()) for line, label in old)
    added = []
    for line, label in findings:
        key = label, source_lines[line - 1].strip()
        if not existing[key]:
            added.append((line, label))
        else:
            existing[key] -= 1
    return added


def added_markers(base: Tree, head: Tree, path: str, markers: Markers) -> list[str]:
    # With zero context, every line in a hunk's new range was added by the diff.
    patch = git(
        head.repo,
        "--literal-pathspecs",
        "diff",
        "--no-renames",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--text",
        "--unified=0",
        "--inter-hunk-context=0",
        base.sha,
        head.sha,
        "--",
        path,
    )
    added = []
    for header in patch.split(b"\n"):
        if match := _HUNK.match(header):
            start, count = match.groups()
            first = int(start)
            added.extend(
                f"{escape(path)}:{line}: {escape(markers[line])}\n"
                for line in range(first, first + int(count or b"1"))
                if line in markers
            )
    return added


def check(base: Tree, head: Tree, baseline_path: str) -> list[str]:
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
    scans = {
        path: head.scan(path) for path in sorted(changed) if test_language(path)
    }
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        added = [
            entry
            for path, (_, markers, _) in scans.items()
            if markers
            for entry in added_markers(base, head, path, markers)
        ]
        if added:
            with Path(summary).open("a", encoding="utf-8") as output:
                output.write("\n### Added real-time exemptions\n\n<pre>\n")
                output.writelines(added)
                output.write("</pre>\n")

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
    command = (
        "python3 .github/scripts/check-realtime-ratchet.py --generate --base origin/main"
        f" --head HEAD > {quote(baseline_path)}"
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
        prior_counts = previous.get(path, _EMPTY_COUNTS)
        head_counts = current.get(path, _EMPTY_COUNTS)
        ceiling = prior_counts["offenders"]
        recorded = head_counts["offenders"]
        findings, markers, source_lines = scans[path] if path in scans else head.scan(path)
        # A free removal may leave a stale ceiling, so compare with the base tree too.
        old_markers = len(base.scan(path)[1]) if markers else 0
        marker_count = len(markers)
        recorded_markers = head_counts["markers"]
        if marker_count > min(old_markers, prior_counts["markers"]) and (
            baseline_path not in changed or recorded_markers != marker_count
        ):
            errors.append(
                f"{path}: marker count raised to {marker_count}; edit the baseline with "
                f"`{command}` only if the exemption is justified (adviser reads it)."
            )
        if recorded_markers > max(prior_counts["markers"], marker_count):
            errors.append(
                f"{baseline_path}: {path} records {recorded_markers} markers "
                f"but has {marker_count}. Run `{command}`."
            )
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
                f"{baseline_path}: {update} ({actual} real-time offenders remain). "
                f"Run `{command}`."
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True, help="base Git revision")
    parser.add_argument(
        "--head", help="candidate Git revision (default: HEAD; base for generation)"
    )
    parser.add_argument("--baseline", default=_DEFAULT_BASELINE)
    parser.add_argument(
        "--generate", action="store_true",
        help="print the head inventory, or base if --head is omitted",
    )
    parser.add_argument(
        "--sdk-source", type=Path, help="load the SDK analyzer from a Core checkout"
    )
    args = parser.parse_args()
    try:
        local_sdk = Path(__file__).resolve().parents[2] / "src/rigplane/testing/realtime.py"
        if args.sdk_source or local_sdk.is_file():
            # The light runner can use the stdlib-only analyzer without installing Core.
            analyze_python = runpy.run_path(str(args.sdk_source or local_sdk))["analyze_python"]
        else:
            from rigplane.testing.realtime import analyze_python

        if args.generate:
            tree = Tree(args.repo, args.head or args.base, analyze_python)
            entries = [
                f"  {json.dumps(path)}: {json.dumps(counts, sort_keys=True)}"
                for path, counts in tree.inventory().items()
            ]
            print("{\n" + ",\n".join(entries) + "\n}" if entries else "{}")
            return 0
        base = Tree(args.repo, args.base, analyze_python)
        head = Tree(args.repo, args.head or "HEAD", analyze_python)
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
