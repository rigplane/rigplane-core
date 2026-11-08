#!/usr/bin/env python3
"""Fail when a workflow job can run on a GitHub-hosted runner label.

Static scan of ``.github/workflows/*.yml`` and ``*.yaml`` (reusable workflow
bodies included), parsed as YAML. Every job's ``runs-on`` (scalar, list or
``labels:`` mapping) must either:
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

import yaml

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


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    job: str
    message: str


DYNAMIC_MESSAGE = (
    "dynamic runs-on cannot be proven self-hosted; add a "
    "runs-on-allowlist: comment with justification"
)


def _mapping_get(
    node: yaml.MappingNode, key: str
) -> tuple[yaml.Node, yaml.Node] | None:
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return key_node, value_node
    return None


def _has_allowlist(lines: list[str], first: int, last: int) -> bool:
    return any(ALLOWLIST_MARKER.search(line) for line in lines[first : last + 1])


def _hosted_tokens(labels: list[str]) -> list[str]:
    return [
        label
        for label in labels
        if label != "self-hosted" and HOSTED_TOKEN.search(label)
    ]


def _check_static_labels(
    labels: list[str], *, path: str, line: int, job: str
) -> list[Finding]:
    label_set = frozenset(labels)
    if label_set in APPROVED_LABEL_SETS:
        return []
    findings: list[Finding] = []
    if "self-hosted" not in label_set:
        findings.append(
            Finding(path, line, job, f"runs-on {labels!r} omits the self-hosted label")
        )
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


def _runs_on_labels(node: yaml.Node) -> list[str] | None:
    """Labels of a ``runs-on`` node, or ``None`` when they are not plain strings."""
    if isinstance(node, yaml.ScalarNode):
        return [node.value]
    if isinstance(node, yaml.SequenceNode):
        if all(isinstance(item, yaml.ScalarNode) for item in node.value):
            return [item.value for item in node.value]
        return None
    if isinstance(node, yaml.MappingNode):
        found = _mapping_get(node, "labels")
        if found is not None and not _mapping_get(node, "group"):
            return _runs_on_labels(found[1])
    return None


def _scan_job(
    path: str, lines: list[str], job: str, key_node: yaml.Node, job_node: yaml.Node
) -> list[Finding]:
    if not isinstance(job_node, yaml.MappingNode):
        return []
    runs_on = _mapping_get(job_node, "runs-on")
    if runs_on is None:
        if _mapping_get(job_node, "uses") is not None:
            return []  # reusable-workflow call: the callee's jobs are scanned there
        return [Finding(path, key_node.start_mark.line + 1, job, "job has no runs-on")]
    runs_key, runs_value = runs_on
    line = runs_key.start_mark.line + 1
    labels = _runs_on_labels(runs_value)
    if labels is None or any("${{" in label or "$(" in label for label in labels):
        if _has_allowlist(lines, key_node.start_mark.line, job_node.end_mark.line):
            return []
        return [Finding(path, line, job, DYNAMIC_MESSAGE)]
    return _check_static_labels(labels, path=path, line=line, job=job)


def _scan_file(path: Path) -> list[Finding]:
    text = path.read_text(encoding="utf-8")
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        return [Finding(str(path), 1, "-", f"workflow is not valid YAML: {exc}")]
    if not isinstance(root, yaml.MappingNode):
        return []
    jobs = _mapping_get(root, "jobs")
    if jobs is None or not isinstance(jobs[1], yaml.MappingNode):
        return []
    lines = text.splitlines()
    findings: list[Finding] = []
    for key_node, job_node in jobs[1].value:
        findings.extend(_scan_job(str(path), lines, key_node.value, key_node, job_node))
    return findings


def scan_workflows(root: Path) -> list[Finding]:
    workflow_dir = root / WORKFLOW_DIR
    paths = [*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")]
    findings: list[Finding] = []
    for path in sorted(paths):
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
