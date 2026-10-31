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

MOR-3998: a receipt pair produced against a release candidate
(``core_version`` ``X.Y.ZrcN``, ``git_sha`` the candidate commit) also
validates for the final tag ``vX.Y.Z`` when the final commit is proven to
differ from the candidate only in the version. The receipts keep naming
what was really tested — no rewriting. ``pyproject.toml`` and ``uv.lock``
below are parsed with ``tomllib``: the project requires Python >= 3.11
(``requires-python = ">=3.11"``), and CI already runs sibling scripts
that import ``tomllib`` under the same system ``python3``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SCHEMA = 1

_RC_VERSION_RE = re.compile(r"^(\d+\.\d+\.\d+)rc(\d+)$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_LINK_REF_RE = re.compile(r"^\[[^\]]+\]: https?://")

# The only paths a version-only rc-to-final commit may touch (MOR-3998).
_ALLOWED_RC_DIFF = frozenset({"pyproject.toml", "uv.lock", "docs/CHANGELOG.md"})

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


def _validate_head(payload: dict[str, Any], *, kind: str) -> list[str]:
    """Schema and kind lines, in the legacy order."""
    violations: list[str] = []
    schema = payload.get("schema")
    if isinstance(schema, bool) or schema != SCHEMA:
        violations.append(f"schema must be {SCHEMA}, got {schema!r}")
    actual_kind = payload.get("kind")
    if actual_kind != kind:
        violations.append(f"kind must be {kind!r}, got {actual_kind!r}")
    return violations


def _validate_tail(payload: dict[str, Any], *, kind: str, now: datetime) -> list[str]:
    """Verdict, timestamp and per-kind lines, in the legacy order."""
    violations: list[str] = []
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
    violations = _validate_head(payload, kind=kind)
    version = payload.get("core_version")
    if version != expected_version:
        violations.append(
            f"core_version must be {expected_version!r} (the tag version), got {version!r}"
        )
    sha = payload.get("git_sha")
    if sha != expected_sha:
        violations.append(f"git_sha must be {expected_sha!r}, got {sha!r}")
    violations.extend(_validate_tail(payload, kind=kind, now=now))
    return violations


def _read_fragment(path: Path) -> tuple[Any, str | None]:
    """Read and JSON-parse one receipt file.

    Returns ``(payload, None)`` on success, ``(None, error)`` otherwise —
    the caller adds the kind prefix.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"file not found: {path}"
    except UnicodeDecodeError as exc:
        # A ValueError, not an OSError: without this case an undecodable
        # receipt crashed the checker with an uncaught UnicodeDecodeError.
        return None, f"invalid UTF-8 in {path}: {exc}"
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"malformed JSON in {path}: {exc}"


def _check_file(
    path: Path,
    *,
    kind: str,
    expected_version: str,
    expected_sha: str,
    now: datetime,
) -> list[str]:
    """All violations of one fragment, each line prefixed with its kind."""
    payload, error = _read_fragment(path)
    if error is not None:
        return [f"{kind}: {error}"]
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


def _classify_identity(
    kind: str,
    version: Any,
    sha: Any,
    *,
    expected_version: str,
    expected_sha: str,
) -> tuple[str, str | None]:
    """Classify one fragment's ``(core_version, git_sha)``.

    Returns ``("exact", None)`` for the final tag version and commit,
    ``("candidate", None)`` for a well-formed same-series ``X.Y.ZrcN``
    claim, else ``(status, line)`` with one violation line that keeps the
    kind prefix and the offending value.
    """
    if version == expected_version and sha == expected_sha:
        return "exact", None
    if version == expected_version:
        return "none", (
            f"{kind}: git_sha {sha!r} does not match the final commit "
            f"{expected_sha!r} and names no release candidate"
        )
    match = _RC_VERSION_RE.match(version) if isinstance(version, str) else None
    if match is None or match.group(1) != expected_version:
        return "none", (
            f"{kind}: core_version {version!r} is neither the final version "
            f"{expected_version!r} nor a {expected_version!r}rcN candidate"
        )
    if int(match.group(2)) < 1:
        return "none", (
            f"{kind}: core_version {version!r} is not a release candidate "
            "(rcN needs N >= 1)"
        )
    if not isinstance(sha, str) or _SHA_RE.match(sha) is None:
        return "none", (f"{kind}: git_sha {sha!r} is not a 40-character hex commit")
    return "candidate", None


def _resolve_candidate(
    rx_id: tuple[Any, Any],
    tx_id: tuple[Any, Any],
    *,
    expected_version: str,
    expected_sha: str,
) -> tuple[tuple[str, str] | None, list[str]]:
    """Agree on one candidate from both fragments, or fail with one line."""
    rx_status, rx_line = _classify_identity(
        "rx",
        rx_id[0],
        rx_id[1],
        expected_version=expected_version,
        expected_sha=expected_sha,
    )
    tx_status, tx_line = _classify_identity(
        "tx",
        tx_id[0],
        tx_id[1],
        expected_version=expected_version,
        expected_sha=expected_sha,
    )
    lines = [line for line in (rx_line, tx_line) if line is not None]
    if lines:
        return None, lines
    if rx_status == "candidate" and tx_status == "candidate":
        if rx_id != tx_id:
            return None, [
                "rc: rx and tx name different candidates: "
                f"rx {rx_id[0]!r} @ {rx_id[1]!r}, "
                f"tx {tx_id[0]!r} @ {tx_id[1]!r} — "
                "both receipts must name the same commit"
            ]
        assert isinstance(rx_id[0], str) and isinstance(rx_id[1], str)
        return (rx_id[0], rx_id[1]), []
    return None, [
        "rc: rx and tx must name the same commit: "
        f"rx {rx_id[0]!r} @ {rx_id[1]!r}, tx {tx_id[0]!r} @ {tx_id[1]!r} — "
        "a mix of a final-matching and a candidate receipt fails"
    ]


def _git(repo: str, *args: str) -> tuple[int, str, str]:
    """Run git in ``repo``; never raise — failures are fail-closed lines."""
    try:
        completed = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return 127, "", f"cannot run git: {exc}"
    return (
        completed.returncode,
        completed.stdout.strip(),
        completed.stderr.strip(),
    )


def _git_show(repo: str, rev: str, path: str) -> tuple[str | None, str | None]:
    """Return ``(content, None)`` or ``(None, one fail-closed rc: line)``."""
    code, out, err = _git(repo, "show", f"{rev}:{path}")
    if code != 0:
        detail = err.splitlines()[0] if err else f"exit {code}"
        return None, f"rc: cannot read {path} at {rev} in {repo}: {detail}"
    return out, None


def _prove_version_only(
    repo: str,
    *,
    candidate_version: str,
    candidate_sha: str,
    final_sha: str,
    final_version: str,
) -> list[str]:
    """Prove FINAL differs from the candidate only in the version.

    Every failure is one ``rc:``-prefixed line; fail closed on any git
    error, missing repo or missing commit.
    """
    lines: list[str] = []
    if _SHA_RE.match(final_sha) is None:
        return [f"rc: --git-sha {final_sha!r} is not a 40-character hex commit"]
    code, _, err = _git(repo, "cat-file", "-e", f"{final_sha}^{{commit}}")
    if code != 0:
        detail = err.splitlines()[0] if err else f"exit {code}"
        return [f"rc: final commit {final_sha} not found in {repo}: {detail}"]
    tag = f"v{candidate_version}"
    code, resolved, err = _git(repo, "rev-parse", "--verify", f"{tag}^{{commit}}")
    if code != 0 or _SHA_RE.match(resolved) is None:
        detail = err.splitlines()[0] if err else f"exit {code}"
        return [f"rc: tag {tag!r} does not resolve to a commit in {repo}: {detail}"]
    if resolved != candidate_sha:
        return [
            f"rc: tag {tag!r} resolves to {resolved}, "
            f"not the receipt git_sha {candidate_sha}"
        ]
    code, _, _ = _git(repo, "merge-base", "--is-ancestor", candidate_sha, final_sha)
    if code != 0:
        return [
            f"rc: candidate {candidate_sha} ({tag}) is not an ancestor "
            f"of the final commit {final_sha}"
        ]
    code, out, err = _git(repo, "diff", "--name-only", candidate_sha, final_sha, "--")
    if code != 0:
        detail = err.splitlines()[0] if err else f"exit {code}"
        return [f"rc: cannot diff {candidate_sha}..{final_sha} in {repo}: {detail}"]
    for path in sorted(set(out.splitlines()) - _ALLOWED_RC_DIFF):
        lines.append(
            f"rc: {path} changed between {tag} and the final commit — "
            "only pyproject.toml, uv.lock and docs/CHANGELOG.md may change"
        )
    if lines:
        return lines
    lines.extend(
        _prove_pyproject_and_lock(
            repo,
            candidate_version=candidate_version,
            candidate_sha=candidate_sha,
            final_sha=final_sha,
            expected_version=final_version,
        )
    )
    lines.extend(_prove_changelog(repo, candidate_sha, final_sha))
    return lines


def _load_toml(
    repo: str, rev: str, path: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(parsed, None)`` or ``(None, one fail-closed rc: line)``."""
    content, error = _git_show(repo, rev, path)
    if error is not None:
        return None, error
    assert content is not None
    try:
        return tomllib.loads(content), None
    except tomllib.TOMLDecodeError as exc:
        return None, f"rc: cannot parse {path} at {rev}: {exc}"


def _packages_without_project_version(
    packages: Any, project: str
) -> tuple[list[dict[str, Any]] | None, Any]:
    """Drop the project entry's ``version`` from a ``[[package]]`` list.

    Returns ``(normalized, version)`` or ``(None, None)`` when the
    project entry is missing or malformed.
    """
    if not isinstance(packages, list):
        return None, None
    normalized: list[dict[str, Any]] = []
    version: Any = None
    found = False
    for entry in packages:
        if not isinstance(entry, dict):
            return None, None
        if not found and entry.get("name") == project:
            found = True
            version = entry.get("version")
            normalized.append(
                {key: value for key, value in entry.items() if key != "version"}
            )
        else:
            normalized.append(dict(entry))
    if not found:
        return None, None
    return normalized, version


def _prove_pyproject_and_lock(
    repo: str,
    *,
    candidate_version: str,
    candidate_sha: str,
    final_sha: str,
    expected_version: str,
) -> list[str]:
    """pyproject.toml and uv.lock must match except the project version."""
    lines: list[str] = []
    cand_py, error = _load_toml(repo, candidate_sha, "pyproject.toml")
    if error is not None:
        return [error]
    final_py, error = _load_toml(repo, final_sha, "pyproject.toml")
    if error is not None:
        return [error]
    assert cand_py is not None and final_py is not None
    cand_project = cand_py.get("project")
    final_project = final_py.get("project")
    if not isinstance(cand_project, dict):
        return [f"rc: pyproject.toml at {candidate_sha} has no [project] table"]
    if not isinstance(final_project, dict):
        return [f"rc: pyproject.toml at {final_sha} has no [project] table"]
    project = final_project.get("name") or cand_project.get("name")
    if not isinstance(project, str) or not project:
        return ["rc: pyproject.toml has no project name line"]
    cand_version = cand_project.get("version")
    final_version = final_project.get("version")
    if cand_version != candidate_version:
        lines.append(
            f"rc: pyproject.toml at {candidate_sha} says version "
            f"{cand_version!r}, not the receipt core_version "
            f"{candidate_version!r}"
        )
    if final_version != expected_version:
        lines.append(
            f"rc: pyproject.toml at the final commit says version "
            f"{final_version!r}, not the tag version {expected_version!r}"
        )
    if {key: value for key, value in cand_project.items() if key != "version"} != {
        key: value for key, value in final_project.items() if key != "version"
    }:
        lines.append(
            f"rc: pyproject.toml differs between {candidate_sha} and "
            f"{final_sha} beyond the project version"
        )
    cand_lock, error = _load_toml(repo, candidate_sha, "uv.lock")
    if error is not None:
        lines.append(error)
        return lines
    final_lock, error = _load_toml(repo, final_sha, "uv.lock")
    if error is not None:
        lines.append(error)
        return lines
    assert cand_lock is not None and final_lock is not None
    cand_packages, cand_lock_version = _packages_without_project_version(
        cand_lock.get("package"), project
    )
    if cand_packages is None:
        lines.append(f"rc: uv.lock at {candidate_sha} has no {project!r} package entry")
        return lines
    final_packages, final_lock_version = _packages_without_project_version(
        final_lock.get("package"), project
    )
    if final_packages is None:
        lines.append(f"rc: uv.lock at {final_sha} has no {project!r} package entry")
        return lines
    if cand_lock_version != cand_version:
        lines.append(
            f"rc: uv.lock at {candidate_sha} says {project} version "
            f"{cand_lock_version!r}, not the pyproject version {cand_version!r}"
        )
    if final_lock_version != final_version:
        lines.append(
            f"rc: uv.lock at the final commit says {project} version "
            f"{final_lock_version!r}, not the pyproject version "
            f"{final_version!r}"
        )
    if cand_packages != final_packages:
        lines.append(
            f"rc: uv.lock differs between {candidate_sha} and {final_sha} "
            f"beyond the {project} package version"
        )
    return lines


def _prove_changelog(repo: str, candidate_sha: str, final_sha: str) -> list[str]:
    """Only changelog headings and link refs may change rc-to-final."""
    code, out, err = _git(
        repo, "diff", "-U0", candidate_sha, final_sha, "--", "docs/CHANGELOG.md"
    )
    if code != 0:
        detail = err.splitlines()[0] if err else f"exit {code}"
        return [f"rc: cannot diff docs/CHANGELOG.md in {repo}: {detail}"]
    lines: list[str] = []
    for line in out.splitlines():
        if line.startswith(("+++", "---")) or not line.startswith(("+", "-")):
            continue
        body = line[1:]
        if body.startswith("## ["):
            continue
        if _LINK_REF_RE.match(body) is not None:
            continue
        lines.append(
            f"rc: docs/CHANGELOG.md changes more than headings and link refs: {body!r}"
        )
    return lines


def _check_pair(
    rx: dict[str, Any],
    tx: dict[str, Any],
    *,
    tag: str,
    expected_version: str,
    expected_sha: str,
    repo: str,
    rx_name: str,
    tx_name: str,
    now: datetime,
) -> tuple[list[str], str | None]:
    """Validate one RX/TX pair; return ``(violations, success_message)``.

    The success message is ``None`` when violations is non-empty. When
    both receipts name the final tag version and commit, the violations
    and the success text are byte-for-byte what the legacy check
    produced — the repo is never touched on that path.
    """
    violations: list[str] = []
    # One fragment at a time, head then tail: on the exact-match path the
    # version/sha checks below add no lines, so this order is the legacy
    # order line-for-line.
    for kind, payload in (("rx", rx), ("tx", tx)):
        violations.extend(
            f"{kind}: {line}" for line in _validate_head(payload, kind=kind)
        )
        violations.extend(
            f"{kind}: {line}" for line in _validate_tail(payload, kind=kind, now=now)
        )
    rx_id = (rx.get("core_version"), rx.get("git_sha"))
    tx_id = (tx.get("core_version"), tx.get("git_sha"))
    if rx_id == (expected_version, expected_sha) and tx_id == (
        expected_version,
        expected_sha,
    ):
        if violations:
            return violations, None
        return (
            [],
            f"radio smoke receipts OK: {rx_name} + {tx_name} "
            f"for {tag} @ {expected_sha}",
        )
    candidate, candidate_lines = _resolve_candidate(
        rx_id,
        tx_id,
        expected_version=expected_version,
        expected_sha=expected_sha,
    )
    violations.extend(candidate_lines)
    if candidate is None:
        return violations, None
    candidate_version, candidate_sha = candidate
    violations.extend(
        _prove_version_only(
            repo,
            candidate_version=candidate_version,
            candidate_sha=candidate_sha,
            final_sha=expected_sha,
            final_version=expected_version,
        )
    )
    if violations:
        return violations, None
    return (
        [],
        f"radio smoke receipts OK: {rx_name} + {tx_name} "
        f"for {tag} @ {expected_sha} "
        f"(tested candidate v{candidate_version} @ {candidate_sha}, "
        "version-only diff proven)",
    )


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
    check.add_argument(
        "--repo",
        default=".",
        help="repo checkout used to prove a version-only rc-to-final "
        "diff (MOR-3998); unneeded when both receipts name the final "
        "tag version and commit",
    )
    args = parser.parse_args(argv)

    expected_version = args.tag.removeprefix("v")
    now = datetime.now(timezone.utc)
    rx_payload, _ = _read_fragment(args.rx)
    tx_payload, _ = _read_fragment(args.tx)
    if not isinstance(rx_payload, dict) or not isinstance(tx_payload, dict):
        # Unreadable or non-object fragments: the legacy per-fragment
        # report, byte-for-byte.
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
        message: str | None = None
    else:
        violations, message = _check_pair(
            rx_payload,
            tx_payload,
            tag=args.tag,
            expected_version=expected_version,
            expected_sha=args.git_sha,
            repo=args.repo,
            rx_name=args.rx.name,
            tx_name=args.tx.name,
            now=now,
        )
    for line in violations:
        print(line, file=sys.stderr)
    if violations:
        print(
            f"radio smoke check FAILED: {len(violations)} violation(s)", file=sys.stderr
        )
        return 1
    assert message is not None
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
