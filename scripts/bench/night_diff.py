"""Compare two ``rigplane validate --json`` artifacts from consecutive bench nights.

Both inputs are projected through ``rigplane.validation.normalize_artifact``
first, so timestamps, evidence and the transport endpoint never show up as
changes, and a normalized file compares equal to the raw artifact it came from.

Usage: night_diff.py PREVIOUS CURRENT [--label NAME]

Prints a Markdown summary. Exit 0 when nothing changed or PREVIOUS does not
exist (first night), 1 when a check was added, removed or changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from rigplane.validation import normalize_artifact


def _rows(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    checks: list[dict[str, Any]] = normalize_artifact(artifact)["checks"]  # type: ignore[assignment]
    return {str(row["check_id"]): row for row in checks}


def diff_artifacts(
    previous: dict[str, Any], current: dict[str, Any]
) -> dict[str, list[Any]]:
    """Return the check ids added and removed, and per-field changes, by id."""
    before = _rows(previous)
    after = _rows(current)
    changed = []
    for check_id in sorted(before.keys() & after.keys()):
        fields = {
            key: [before[check_id].get(key), after[check_id].get(key)]
            for key in sorted(
                (before[check_id].keys() | after[check_id].keys()) - {"check_id"}
            )
            if before[check_id].get(key) != after[check_id].get(key)
        }
        if fields:
            changed.append({"check_id": check_id, "fields": fields})
    return {
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "changed": changed,
    }


def render(diff: dict[str, list[Any]], label: str) -> str:
    lines = [
        f"### {label}: {len(diff['added'])} added, {len(diff['removed'])} removed, "
        f"{len(diff['changed'])} changed"
    ]
    lines += [f"- added `{check_id}`" for check_id in diff["added"]]
    lines += [f"- removed `{check_id}`" for check_id in diff["removed"]]
    for entry in diff["changed"]:
        parts = ", ".join(
            f"{key} {old} -> {new}" for key, (old, new) in entry["fields"].items()
        )
        lines.append(f"- `{entry['check_id']}`: {parts}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("previous", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--label", default="validate")
    args = parser.parse_args(argv)
    if not args.previous.is_file():
        print(f"### {args.label}: no previous night to compare against")
        return 0
    previous = json.loads(args.previous.read_text(encoding="utf-8"))
    current = json.loads(args.current.read_text(encoding="utf-8"))
    diff = diff_artifacts(previous, current)
    print(render(diff, args.label))
    return 1 if any(diff.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
