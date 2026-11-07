#!/usr/bin/env python3
"""Fail when a workflow job can run on a GitHub-hosted runner label.

Static scan of ``.github/workflows/*.yml`` only (reusable workflow bodies
included). Every ``runs-on`` must either:
  - resolve to an approved self-hosted label set, or
  - carry a ``runs-on-allowlist:`` justification comment on a nearby line.

Established by reading every workflow under ``.github/workflows/`` in this repo.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

WORKFLOW_DIR = Path(".github/workflows")

# Label sets verified on org runners 2026-10-31 (infra-control MOR-3875).
APPROVED_LABEL_SETS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"self-hosted", "Linux", "ARM64", "light"}),
        frozenset({"self-hosted", "Linux", "ARM64", "gate"}),
        frozenset({"self-hosted", "Linux", "ARM64", "build"}),
        frozenset({"self-hosted", "Linux", "X64", "build"}),
        frozenset({"self-hosted", "macOS", "ARM64", "bench"}),
        frozenset({"self-hosted", "macOS", "ARM64", "platform"}),
    }
)

HOSTED_TOKEN = re.compile(
    r"(^ubuntu|^macos|^windows|-latest$|"
    r"^windows-11-arm|^ubuntu-slim|^ubuntu-large)",
    re.IGNORECASE,
)

ALLOWLIST_MARKER = re.compile(r"runs-on-allowlist:\s*(.+)", re.IGNORECASE)

RUNS_ON = re.compile(r"^(\s+)runs-on:\s*(.+)\s*$")
JOB_START = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    job: str
    message: str


def _parse_label_list(raw: str) -> list[str] | None:
    text = raw.strip()
    if not text.startswith("[") or not text.endswith("]"):
        return None
    inner = text[1:-1].strip()
    if not inner:
        return []
    labels: list[str] = []
    for part in inner.split(","):
        token = part.strip().strip("'\"")
        if token:
            labels.append(token)
    return labels


def _is_allowlisted(job_lines: list[str]) -> str | None:
    for line in job_lines:
        match = ALLOWLIST_MARKER.search(line)
        if match:
            return match.group(1).strip()
    return None


def _hosted_tokens(labels: list[str]) -> list[str]:
    hits: list[str] = []
    for label in labels:
        if label == "self-hosted":
            continue
        if HOSTED_TOKEN.search(label):
            hits.append(label)
        elif "self-hosted" not in labels and not label.startswith("fleet-"):
            # Bare unknown labels without self-hosted are treated as hosted-sized risk.
            hits.append(label)
    return hits


def _check_static_labels(
    labels: list[str], *, path: str, line: int, job: str
) -> list[Finding]:
    findings: list[Finding] = []
    if any("${{" in label or "$(" in label for label in labels):
        return findings
    label_set = frozenset(labels)
    if "self-hosted" not in label_set:
        findings.append(
            Finding(
                path,
                line,
                job,
                f"runs-on {labels!r} omits the self-hosted label",
            )
        )
        return findings
    if label_set in APPROVED_LABEL_SETS:
        return findings
    hosted = _hosted_tokens(labels)
    if hosted:
        findings.append(
            Finding(
                path,
                line,
                job,
                f"runs-on includes GitHub-hosted-style label(s): {hosted!r}",
            )
        )
    findings.append(
        Finding(
            path,
            line,
            job,
            f"runs-on label set {sorted(label_set)!r} is not an approved pool",
        )
    )
    return findings


def _scan_file(path: Path) -> list[Finding]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    findings: list[Finding] = []
    in_jobs = False
    current_job: str | None = None
    job_lines: list[str] = []

    def flush_job() -> None:
        nonlocal current_job, job_lines
        current_job = None
        job_lines = []

    for index, line in enumerate(lines, start=1):
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        job_match = JOB_START.match(line)
        if job_match:
            flush_job()
            current_job = job_match.group(1)
            job_lines = [line]
            continue
        if current_job is None:
            continue
        job_lines.append(line)
        runs_match = RUNS_ON.match(line)
        if not runs_match:
            continue
        raw = runs_match.group(2)
        job = current_job
        allow_reason = _is_allowlisted(job_lines)
        labels = _parse_label_list(raw)
        if labels is not None:
            if any("${{" in label or "$(" in label for label in labels):
                if not allow_reason:
                    findings.append(
                        Finding(
                            str(path),
                            index,
                            job,
                            "dynamic runs-on cannot be proven self-hosted; add a "
                            "runs-on-allowlist: comment with justification",
                        )
                    )
                continue
            findings.extend(_check_static_labels(labels, path=str(path), line=index, job=job))
            continue
        if "${{" in raw or "$(" in raw:
            if allow_reason:
                continue
            findings.append(
                Finding(
                    str(path),
                    index,
                    job,
                    "dynamic runs-on cannot be proven self-hosted; add a "
                    "runs-on-allowlist: comment with justification",
                )
            )
            continue
        # Quoted single label, e.g. ubuntu-latest
        token = raw.strip().strip("'\"")
        if token == "self-hosted":
            continue
        if HOSTED_TOKEN.search(token) or token:
            findings.append(
                Finding(
                    str(path),
                    index,
                    job,
                    f"runs-on {raw!r} is not an approved self-hosted label set",
                )
            )
    return findings


def scan_workflows(root: Path) -> list[Finding]:
    workflow_dir = root / WORKFLOW_DIR
    findings: list[Finding] = []
    for path in sorted(workflow_dir.glob("*.yml")):
        findings.extend(_scan_file(path))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("."),
        help="Repository root (default: current directory)",
    )
    args = parser.parse_args(argv)
    root = args.repo.resolve()
    findings = scan_workflows(root)
    if not findings:
        print("OK: every workflow job runs on an approved self-hosted label set")
        return 0
    for item in findings:
        print(f"{item.path}:{item.line}: job {item.job}: {item.message}")
    print(f"FAILED: {len(findings)} workflow runs-on violation(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
