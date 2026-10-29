#!/usr/bin/env python3
"""Validate the RX/TX radio-smoke receipts attached to a GitHub Release.

MOR-3839: live radio smoke is a mechanical condition of a PyPI publish.
``publish.yml``'s ``validate`` job downloads ``radio-smoke-rx.json`` and
``radio-smoke-tx.json`` from the release being published and runs::

    check_radio_smoke.py check --tag vX --git-sha SHA --rx rx.json --tx tx.json

before any build or PyPI upload starts. Exit 0 only when both fragments
validate: schema 1, matching ``kind``, ``core_version`` equal to the tag
version, ``git_sha`` equal to the tagged commit, ``verdict == "PASS"``, a
parseable non-future ``generated_at`` — plus, for the TX fragment, all five
sequences PASS with no stuck TX, no release debt and the radio back in RX,
and for the RX fragment a non-empty all-PASS ``radios`` map. Every violation
prints one line to stderr; the exit status is non-zero if any exist.

Stdlib only. Fragment format contract: ``docs/development/release-gates.md``,
section "Radio-smoke receipts".
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SCHEMA = 1

TX_SEQUENCE_KEYS = (
    "single",
    "rekey_150ms",
    "rekey_immediate",
    "double_cycle",
    "force_off_inflight",
)


def _parse_generated_at(value: Any) -> tuple[datetime | None, str | None]:
    if not isinstance(value, str):
        return None, f"generated_at must be an ISO 8601 string, got {value!r}"
    # datetime.fromisoformat accepts the trailing "Z" only from Python 3.11;
    # the workflow runs whatever python3 ubuntu-latest ships.
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, f"generated_at is not a parseable ISO 8601 timestamp: {value!r}"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed, None


def _validate_tx(payload: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    sequences = payload.get("sequences")
    if not isinstance(sequences, dict):
        violations.append(f"sequences must be an object, got {sequences!r}")
    else:
        missing = [key for key in TX_SEQUENCE_KEYS if key not in sequences]
        if missing:
            violations.append(f"sequences missing required keys: {', '.join(missing)}")
        extra = sorted(key for key in sequences if key not in TX_SEQUENCE_KEYS)
        if extra:
            violations.append(f"sequences has unexpected keys: {', '.join(extra)}")
        for key in TX_SEQUENCE_KEYS:
            if key in sequences and sequences[key] != "PASS":
                violations.append(
                    f'sequences["{key}"] must be "PASS", got {sequences[key]!r}'
                )
    for key, expected in (
        ("stuck_tx", False),
        ("release_debt", False),
        ("ended_rx", True),
    ):
        value = payload.get(key)
        if not isinstance(value, bool) or value is not expected:
            violations.append(f"{key} must be {json.dumps(expected)}, got {value!r}")
    return violations


def _validate_rx(payload: dict[str, Any]) -> list[str]:
    radios = payload.get("radios")
    if not isinstance(radios, dict):
        return [f"radios must be an object, got {radios!r}"]
    if not radios:
        return ["radios must list at least one radio, got an empty map"]
    return [
        f'radios["{name}"] must be "PASS", got {verdict!r}'
        for name, verdict in sorted(radios.items())
        if verdict != "PASS"
    ]


def _validate(
    payload: Any,
    *,
    kind: str,
    expected_version: str,
    expected_sha: str,
    now: datetime,
) -> list[str]:
    if not isinstance(payload, dict):
        return ["top level is not a JSON object"]
    violations: list[str] = []
    schema = payload.get("schema")
    if isinstance(schema, bool) or schema != SCHEMA:
        violations.append(f"schema must be {SCHEMA}, got {schema!r}")
    actual_kind = payload.get("kind")
    if actual_kind != kind:
        violations.append(f"kind must be {kind!r}, got {actual_kind!r}")
    version = payload.get("core_version")
    if version != expected_version:
        violations.append(
            f"core_version must be {expected_version!r} (the tag version), got {version!r}"
        )
    sha = payload.get("git_sha")
    if sha != expected_sha:
        violations.append(f"git_sha must be {expected_sha!r}, got {sha!r}")
    verdict = payload.get("verdict")
    if verdict != "PASS":
        violations.append(f'verdict must be "PASS", got {verdict!r}')
    parsed, error = _parse_generated_at(payload.get("generated_at"))
    if error is not None:
        violations.append(error)
    elif parsed is not None and parsed > now:
        violations.append(
            f"generated_at {parsed.isoformat()} is in the future (now {now.isoformat()})"
        )
    violations.extend(_validate_tx(payload) if kind == "tx" else _validate_rx(payload))
    return violations


def _check_file(
    path: Path,
    *,
    kind: str,
    expected_version: str,
    expected_sha: str,
    now: datetime,
) -> list[str]:
    """All violations of one fragment, each line prefixed with its kind."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"{kind}: file not found: {path}"]
    except UnicodeDecodeError as exc:
        # A ValueError, not an OSError: without this case an undecodable
        # receipt crashed the checker with an uncaught UnicodeDecodeError.
        return [f"{kind}: invalid UTF-8 in {path}: {exc}"]
    except OSError as exc:
        return [f"{kind}: cannot read {path}: {exc}"]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f"{kind}: malformed JSON in {path}: {exc}"]
    return [
        f"{kind}: {violation}"
        for violation in _validate(
            payload,
            kind=kind,
            expected_version=expected_version,
            expected_sha=expected_sha,
            now=now,
        )
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_radio_smoke.py",
        description="Validate the radio-smoke receipts of a GitHub Release.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser(
        "check", help="validate the RX and TX receipt files of a release"
    )
    check.add_argument("--tag", required=True, help="release tag, e.g. v3.1.0")
    check.add_argument("--git-sha", required=True, help="the tagged commit SHA")
    check.add_argument("--rx", required=True, type=Path, help="radio-smoke-rx.json")
    check.add_argument("--tx", required=True, type=Path, help="radio-smoke-tx.json")
    args = parser.parse_args(argv)

    expected_version = args.tag.removeprefix("v")
    now = datetime.now(timezone.utc)
    violations: list[str] = []
    for kind, path in (("rx", args.rx), ("tx", args.tx)):
        violations.extend(
            _check_file(
                path,
                kind=kind,
                expected_version=expected_version,
                expected_sha=args.git_sha,
                now=now,
            )
        )
    for line in violations:
        print(line, file=sys.stderr)
    if violations:
        print(
            f"radio smoke check FAILED: {len(violations)} violation(s)", file=sys.stderr
        )
        return 1
    print(
        f"radio smoke receipts OK: {args.rx.name} + {args.tx.name} "
        f"for {args.tag} @ {args.git_sha}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
