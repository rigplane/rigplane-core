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

MOR-3998: receipts naming a candidate also validate for the final when checks 0-4
all hold — one ``rc:`` line each, any git/parse error fails closed: (0) same candidate;
(1) tag resolves via ``refs/tags/`` and is an ancestor; (2) only identical-mode edits
of the three version files; (3) TOML revisions parse and match past the claimed version;
(4) final changelog has the final heading, no rc heading. Body is not checked.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    # fromisoformat accepts a trailing "Z" only from Python 3.11 on.
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
        if missing := [key for key in TX_SEQUENCE_KEYS if key not in sequences]:
            violations.append(f"sequences missing required keys: {', '.join(missing)}")
        if extra := sorted(key for key in sequences if key not in TX_SEQUENCE_KEYS):
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
    payload: Any, kind: str, expected_version: str, expected_sha: str, now: datetime
) -> list[str]:
    if not isinstance(payload, dict):
        return ["top level is not a JSON object"]
    violations: list[str] = []
    schema = payload.get("schema")
    if isinstance(schema, bool) or schema != SCHEMA:
        violations.append(f"schema must be {SCHEMA}, got {schema!r}")
    if payload.get("kind") != kind:
        violations.append(f"kind must be {kind!r}, got {payload.get('kind')!r}")
    if payload.get("core_version") != expected_version:
        violations.append(
            f"core_version must be {expected_version!r} (the tag version), got {payload.get('core_version')!r}"
        )
    if payload.get("git_sha") != expected_sha:
        violations.append(
            f"git_sha must be {expected_sha!r}, got {payload.get('git_sha')!r}"
        )
    if payload.get("verdict") != "PASS":
        violations.append(f'verdict must be "PASS", got {payload.get("verdict")!r}')
    parsed, error = _parse_generated_at(payload.get("generated_at"))
    if error is not None:
        violations.append(error)
    elif parsed is not None and parsed > now:
        violations.append(
            f"generated_at {parsed.isoformat()} is in the future (now {now.isoformat()})"
        )
    violations.extend(_validate_tx(payload) if kind == "tx" else _validate_rx(payload))
    return violations


def _read_fragment(path: Path) -> tuple[Any, str | None]:
    """Read and JSON-parse one receipt file; the caller adds the kind prefix."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"file not found: {path}"
    except UnicodeDecodeError as exc:
        # A ValueError, not an OSError: crashing here hides the real violation.
        return None, f"invalid UTF-8 in {path}: {exc}"
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"malformed JSON in {path}: {exc}"


# The only paths a version-only rc-to-final commit may touch (MOR-3998).
_ALLOWED_RC_PATHS = frozenset((b"pyproject.toml", b"uv.lock", b"docs/CHANGELOG.md"))


def _is_hex_sha(value: Any) -> bool:
    """True for a 40-character hex commit sha."""
    try:
        return isinstance(value, str) and len(value) == 40 and int(value, 16) >= 0
    except ValueError:
        return False


def _is_rc_of(version: Any, final_version: str) -> bool:
    """True when ``version`` is ``X.Y.ZrcN`` with the tag's X.Y.Z, N >= 1."""
    if not isinstance(version, str):
        return False
    head = final_version + "rc"
    tail = version[len(head) :] if version.startswith(head) else ""
    # All-digit with a nonzero digit means N >= 1, with no int() overflow trap.
    return bool(tail.isascii() and tail.isdigit() and tail.strip("0"))


def _git(repo: str, *args: str) -> tuple[int, bytes, str]:
    """Run git in ``repo``; never raise — failures are fail-closed lines."""
    try:
        completed = subprocess.run(
            ["git", "-C", repo, *args], capture_output=True, check=False
        )
    except OSError as exc:
        return 127, b"", f"cannot run git: {exc}"
    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    return completed.returncode, completed.stdout, stderr


def _toml_at(
    repo: str, rev: str, path: str, side: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(parsed TOML document, None)`` or ``(None, one rc: line)``."""
    code, out, err = _git(repo, "show", f"{rev}:{path}")
    if code != 0:
        return None, f"rc: cannot read {path} ({side}) in {repo}"
    try:
        return tomllib.loads(out.decode("utf-8")), None
    except ValueError:
        return None, f"rc: cannot parse {path} ({side}) in {repo}"


def _lock_entry(document: dict[str, Any], project: str) -> dict[str, Any] | None:
    """The single ``[[package]]`` entry named ``project``, else None."""
    packages = document.get("package")
    if not isinstance(packages, list):
        return None
    hits = [e for e in packages if isinstance(e, dict) and e.get("name") == project]
    return hits[0] if len(hits) == 1 else None


def _resolve_candidate(
    rx_id: tuple[Any, Any], tx_id: tuple[Any, Any], want: tuple[str, str]
) -> tuple[tuple[str, str] | None, list[str]]:
    """Check 0: agree on one candidate from both fragments, or fail."""
    lines: list[str] = []
    for kind, (version, sha) in (("rx", rx_id), ("tx", tx_id)):
        if (version, sha) == want:
            continue
        elif version == want[0]:
            lines.append(f"{kind}: git_sha {sha!r} != final, nor a candidate")
        elif not _is_rc_of(version, want[0]):
            lines.append(f"{kind}: {version!r} is no {want[0]!r}rcN")
        elif not _is_hex_sha(sha):
            lines.append(f"{kind}: git_sha {sha!r} is not a 40-hex commit")
    if lines:
        return None, lines
    if rx_id == tx_id:
        return (rx_id[0], rx_id[1]), []
    if want in (rx_id, tx_id):
        return None, ["rc: rx and tx mix final and candidate receipts"]
    if rx_id[0] != tx_id[0]:
        return None, [f"rc: rx {rx_id[0]} vs tx {tx_id[0]}: different candidates"]
    return None, ["rc: rx and tx name different candidates"]


def _prove_version_only(
    repo: str, candidate: tuple[str, str], final_version: str, final_sha: str
) -> list[str]:
    """Checks 1-4: prove FINAL differs from the candidate only in the version."""
    candidate_version, candidate_sha = candidate
    final = (final_version, final_sha)
    tag = f"v{candidate_version}"
    code, resolved, err = _git(
        repo, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"
    )
    resolved_sha = resolved.decode("ascii", errors="replace").strip()
    if code != 0 or not _is_hex_sha(resolved_sha):
        return [f"rc: tag {tag!r} does not resolve to a commit in {repo}"]
    if resolved_sha != candidate_sha:
        return [f"rc: tag {tag!r} points elsewhere, not {candidate_sha}"]
    code, _, _ = _git(repo, "merge-base", "--is-ancestor", candidate_sha, final_sha)
    if code != 0:
        return [f"rc: {tag} is no ancestor of the final {final_sha}"]
    lines = _prove_tree(repo, candidate_sha, final_sha, tag)
    lines += _prove_toml_versions(repo, candidate, final)
    lines += _prove_changelog_headings(repo, candidate, final)
    return lines


def _prove_tree(repo: str, candidate_sha: str, final_sha: str, tag: str) -> list[str]:
    """Check 2: only identical-mode edits of the three version files."""
    code, out, err = _git(
        repo,
        "diff-tree",
        "-r",
        "--raw",
        "--no-renames",
        "--no-abbrev",
        "-z",
        candidate_sha,
        final_sha,
        "--",
    )
    if code != 0:
        return [f"rc: cannot diff candidate..final in {repo}"]
    entries = out.split(b"\x00")
    if entries[-1:] != [b""] or len(entries) % 2 != 1:
        return [f"rc: cannot parse diff-tree output in {repo}"]
    lines: list[str] = []
    for index in range(0, len(entries) - 1, 2):
        info, path = entries[index], entries[index + 1]
        fields = info.split(b" ")
        good = (
            len(fields) == 5
            and fields[4] == b"M"
            and fields[0:2] == [b":100644", b"100644"]
            and path in _ALLOWED_RC_PATHS
        )
        if not good:
            status = (
                fields[4].decode("ascii", errors="replace") if len(fields) == 5 else "?"
            )
            name = path.decode("utf-8", errors="replace")
            lines.append(f"rc: {name} ({status}) is not a version-only change")
    return lines


def _prove_toml_versions(
    repo: str, cand: tuple[str, str], final: tuple[str, str]
) -> list[str]:
    """Check 3: the TOML documents match past the one version each side claims."""
    candidate_version, candidate_sha = cand
    final_version, final_sha = final
    lines: list[str] = []
    cand_py, error = _toml_at(repo, candidate_sha, "pyproject.toml", "candidate")
    final_py, error2 = _toml_at(repo, final_sha, "pyproject.toml", "final")
    if error is not None or error2 is not None:
        return [error or error2]
    cand_p, final_p = cand_py.get("project"), final_py.get("project")
    if not isinstance(cand_p, dict) or not isinstance(final_p, dict):
        return ["rc: pyproject.toml has no [project] table at one of the commits"]
    project = final_p.get("name")
    if not isinstance(project, str) or cand_p.get("name") != project:
        return ["rc: pyproject.toml [project] name is missing or differs"]
    cand_v, final_v = cand_p.get("version"), final_p.get("version")
    if cand_v != candidate_version:
        lines.append(f"rc: pyproject.toml rc {cand_v!r} != {candidate_version!r}")
    if final_v != final_version:
        lines.append(f"rc: pyproject.toml final {final_v!r} != {final_version!r}")
    if (cand_v, final_v) == (candidate_version, final_version):
        del cand_p["version"], final_p["version"]
        if cand_py != final_py:
            lines.append("rc: pyproject.toml differs past the version")
    cand_lock, error = _toml_at(repo, candidate_sha, "uv.lock", "candidate")
    final_lock, error2 = _toml_at(repo, final_sha, "uv.lock", "final")
    if error is not None or error2 is not None:
        return [*lines, error or error2]
    cand_e, final_e = _lock_entry(cand_lock, project), _lock_entry(final_lock, project)
    if cand_e is None or final_e is None:
        return [*lines, f"rc: uv.lock has no single {project!r} package entry"]
    cand_lv, final_lv = cand_e.get("version"), final_e.get("version")
    if cand_lv != candidate_version:
        lines.append(f"rc: uv.lock rc {cand_lv!r} != {candidate_version!r}")
    if final_lv != final_version:
        lines.append(f"rc: uv.lock final {final_lv!r} != {final_version!r}")
    if (cand_lv, final_lv) == (candidate_version, final_version):
        del cand_e["version"], final_e["version"]
        if cand_lock != final_lock:
            lines.append(f"rc: uv.lock differs past the {project} version")
    return lines


def _prove_changelog_headings(
    repo: str, cand: tuple[str, str], final: tuple[str, str]
) -> list[str]:
    """Check 4: the final headings name the final, not the candidate."""
    candidate_version, candidate_sha = cand
    final_version, final_sha = final
    blobs = []
    for rev, side in ((candidate_sha, "candidate"), (final_sha, "final")):
        code, blob, err = _git(repo, "show", f"{rev}:docs/CHANGELOG.md")
        if code != 0:
            return [f"rc: cannot read CHANGELOG.md ({side}) in {repo}"]
        # errors="replace" never raises: undecodable bytes cannot fake the headings.
        blobs.append(blob.decode("utf-8", errors="replace"))
    cand_text, final_text = blobs
    cand_heading, final_heading = f"## [{candidate_version}]", f"## [{final_version}]"
    lines: list[str] = []
    if not any(line.startswith(cand_heading) for line in cand_text.splitlines()):
        lines.append(f"rc: CHANGELOG.md candidate lacks {cand_heading!r}")
    if not any(line.startswith(final_heading) for line in final_text.splitlines()):
        lines.append(f"rc: CHANGELOG.md final lacks {final_heading!r}")
    if any(line.startswith(cand_heading) for line in final_text.splitlines()):
        lines.append(f"rc: CHANGELOG.md final still has {cand_heading!r}")
    return lines


def _check_pair(
    rx_path: Path, tx_path: Path, tag: str, expected_sha: str, repo: str, now: datetime
) -> tuple[list[str], str | None]:
    """Validate one RX/TX pair; exact final receipts skip the repo entirely."""
    expected_version = tag.removeprefix("v")
    rx_payload, rx_error = _read_fragment(rx_path)
    tx_payload, tx_error = _read_fragment(tx_path)
    fragments = (("rx", rx_payload, rx_error), ("tx", tx_payload, tx_error))
    if not all(
        error is None and isinstance(payload, dict) for _, payload, error in fragments
    ):
        # Unreadable or non-object fragments: the legacy per-fragment report.
        violations = []
        context = (expected_version, expected_sha)
        for kind, payload, error in fragments:
            if error is not None:
                violations.append(f"{kind}: {error}")
            else:
                for line in _validate(payload, kind, context[0], context[1], now):
                    violations.append(f"{kind}: {line}")
        return violations, None
    rx_id = (rx_payload.get("core_version"), rx_payload.get("git_sha"))
    tx_id = (tx_payload.get("core_version"), tx_payload.get("git_sha"))
    want = (expected_version, expected_sha)
    exact = rx_id == want and tx_id == want
    candidate, extra = (None, []) if exact else _resolve_candidate(rx_id, tx_id, want)
    context = candidate if candidate is not None else want
    violations = [
        f"{kind}: {line}"
        for kind, payload in (("rx", rx_payload), ("tx", tx_payload))
        for line in _validate(payload, kind, context[0], context[1], now)
    ]
    violations.extend(extra)
    if candidate is not None:
        proof = _prove_version_only(repo, candidate, expected_version, expected_sha)
        violations.extend(proof)
    if violations:
        return violations, None
    ok = f"radio smoke receipts OK: {rx_path.name} + {tx_path.name} for {tag} @ {expected_sha}"
    if exact:
        return [], ok
    tested = (
        f"(tested candidate v{candidate[0]} @ {candidate[1]}, version-only diff proven)"
    )
    return [], f"{ok} {tested}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_radio_smoke.py",
        description="Validate the radio-smoke receipts of a GitHub Release.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="validate the receipts")
    check.add_argument("--tag", required=True, help="release tag, e.g. v3.1.0")
    check.add_argument("--git-sha", required=True, help="the tagged commit SHA")
    check.add_argument("--rx", required=True, type=Path, help="radio-smoke-rx.json")
    check.add_argument("--tx", required=True, type=Path, help="radio-smoke-tx.json")
    check.add_argument("--repo", default=".", help="repo for the rc-to-final proof")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    violations, message = _check_pair(
        args.rx, args.tx, args.tag, args.git_sha, args.repo, now
    )
    for line in violations:
        print(line, file=sys.stderr)
    if violations:
        print(
            f"radio smoke check FAILED: {len(violations)} violation(s)", file=sys.stderr
        )
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
