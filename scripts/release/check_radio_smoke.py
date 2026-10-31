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
what was really tested — no rewriting. The proof is byte-exact and uses
neither TOML parsing nor ``git diff`` text: a ``git diff-tree --raw``
plumbing check allows only identical-mode modifications of exactly
``pyproject.toml``, ``uv.lock`` and ``docs/CHANGELOG.md``, then both
revisions of each file are read as raw bytes and must be identical once
the single permitted version assignment is normalized away (the
``[project]`` version line, the project's own ``uv.lock`` version line,
the changelog headings and link reference definitions).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SCHEMA = 1

_RC_VERSION_RE = re.compile(r"^(\d+\.\d+\.\d+)rc(\d+)$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")

# The only paths a version-only rc-to-final commit may touch (MOR-3998),
# as raw diff-tree path bytes.
_ALLOWED_RC_PATHS = frozenset({b"pyproject.toml", b"uv.lock", b"docs/CHANGELOG.md"})

# One strict reader for version assignments: a bare ``version = "..."``
# or ``name = "..."`` line — no leading whitespace, no trailing comment.
# Both the normalizers and the version-match checks below use these two
# patterns through ``_scan_project_table``/``_scan_lock_package``; there
# is no second reader.
_VERSION_LINE_RE = re.compile(rb'^version = "([^"\n]*)"$')
_NAME_LINE_RE = re.compile(rb'^name = "([^"\n]*)"$')
_LINK_REF_LINE_RE = re.compile(rb"^\[[^\]]+\]: https?://\S+$")

# The fixed placeholder a permitted version line is replaced with before
# the byte comparison.
_NORMALIZED_VERSION_LINE = b'version = "0.0.0-mor3998-normalized"'

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


def _git_bytes(repo: str, *args: str) -> tuple[int, bytes, str]:
    """Run git in ``repo`` capturing stdout as bytes; never raise."""
    try:
        completed = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return 127, b"", f"cannot run git: {exc}"
    return (
        completed.returncode,
        completed.stdout,
        completed.stderr.decode("utf-8", errors="replace").strip(),
    )


def _git_blob(repo: str, rev: str, path: str) -> tuple[bytes | None, str | None]:
    """Return ``(raw blob bytes, None)`` or ``(None, one fail-closed rc: line)``.

    No decoding, no stripping, no textconv: the caller compares bytes.
    """
    code, out, err = _git_bytes(
        repo, "-c", "core.autocrlf=false", "show", f"{rev}:{path}"
    )
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
    code, resolved, err = _git(
        repo, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"
    )
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
    listed, tree_errors = _prove_tree(
        repo, candidate_sha=candidate_sha, final_sha=final_sha, tag=tag
    )
    lines.extend(tree_errors)
    if lines:
        return lines
    assert listed is not None
    lines.extend(
        _prove_blobs(
            repo,
            candidate_version=candidate_version,
            candidate_sha=candidate_sha,
            final_sha=final_sha,
            final_version=final_version,
            listed=listed,
        )
    )
    return lines


def _prove_tree(
    repo: str, *, candidate_sha: str, final_sha: str, tag: str
) -> tuple[set[bytes] | None, list[str]]:
    """Allow only identical-mode modifications of the version files.

    Runs the ``git diff-tree`` plumbing (no colour or config dependence)
    and returns the listed paths, or ``(None, lines)`` on any failure. An
    unchanged file is simply not listed.
    """
    code, out, err = _git_bytes(
        repo,
        "-c",
        "core.quotepath=off",
        "diff-tree",
        "-r",
        "--raw",
        "--no-renames",
        "--no-abbrev",
        "--no-commit-id",
        "-z",
        candidate_sha,
        final_sha,
        "--",
    )
    if code != 0:
        detail = err.splitlines()[0] if err else f"exit {code}"
        return None, [
            f"rc: cannot diff {candidate_sha}..{final_sha} in {repo}: {detail}"
        ]
    if out == b"":
        return set(), []
    entries = out.split(b"\x00")
    if entries[-1:] != [b""] or len(entries) % 2 != 1:
        return None, [
            f"rc: cannot parse diff-tree output between {candidate_sha} and {final_sha}"
        ]
    listed: set[bytes] = set()
    lines: list[str] = []
    for index in range(0, len(entries) - 1, 2):
        info, path = entries[index], entries[index + 1]
        fields = info.split(b" ")
        if len(fields) != 5 or not fields[0].startswith(b":") or path == b"":
            return None, [
                f"rc: cannot parse a diff-tree entry between {candidate_sha} "
                f"and {final_sha}"
            ]
        old_mode, new_mode, status = fields[0][1:], fields[1], fields[4]
        listed.add(path)
        if (
            status != b"M"
            or old_mode != b"100644"
            or new_mode != b"100644"
            or path not in _ALLOWED_RC_PATHS
        ):
            display = path.decode("utf-8", errors="replace")
            change = (
                f"{status.decode('ascii', errors='replace')} "
                f"{old_mode.decode('ascii', errors='replace')}"
                f"->{new_mode.decode('ascii', errors='replace')}"
            )
            lines.append(
                f"rc: {display} ({change}) changed between {tag} and the "
                "final commit — only pyproject.toml, uv.lock and "
                "docs/CHANGELOG.md may change as identical-mode modifications"
            )
    if lines:
        return None, lines
    return listed, []


def _scan_project_table(
    blob: bytes,
) -> tuple[list[tuple[int, bytes]], list[tuple[int, bytes]]]:
    """Find the bare ``name``/``version`` lines inside ``[project]``.

    Returns ``(names, versions)`` as ``(line_index, value)`` pairs. The
    scan itself never fails; callers require exactly one of each. Any line
    starting with ``[`` ends the table.
    """
    names: list[tuple[int, bytes]] = []
    versions: list[tuple[int, bytes]] = []
    in_project = False
    for index, line in enumerate(blob.split(b"\n")):
        if line.strip().startswith(b"["):
            in_project = line.strip() == b"[project]"
            continue
        if not in_project:
            continue
        match = _NAME_LINE_RE.match(line)
        if match is not None:
            names.append((index, match.group(1)))
            continue
        match = _VERSION_LINE_RE.match(line)
        if match is not None:
            versions.append((index, match.group(1)))
    return names, versions


def _scan_lock_package(
    blob: bytes, project: bytes
) -> tuple[int | None, bytes | None, str | None]:
    """Locate the ``[[package]]`` block named ``project`` in uv.lock bytes.

    Returns ``(version_line_index, version_value, error)`` — the single
    reader used by both the normalizer and the version-match check.
    """
    block_names: list[bytes | None] = []
    block_versions: list[list[tuple[int, bytes]]] = []
    for index, line in enumerate(blob.split(b"\n")):
        if line.strip() == b"[[package]]":
            block_names.append(None)
            block_versions.append([])
            continue
        if not block_names:
            continue
        match = _NAME_LINE_RE.match(line)
        if match is not None:
            if block_names[-1] is None:
                block_names[-1] = match.group(1)
            continue
        match = _VERSION_LINE_RE.match(line)
        if match is not None:
            block_versions[-1].append((index, match.group(1)))
    hits = [block for block, name in enumerate(block_names) if name == project]
    if len(hits) != 1:
        return (
            None,
            None,
            (
                f"expected exactly one [[package]] block named {project!r}, "
                f"found {len(hits)}"
            ),
        )
    found = block_versions[hits[0]]
    if len(found) != 1:
        return (
            None,
            None,
            (
                f"expected exactly one version line in the {project!r} "
                f"package block, found {len(found)}"
            ),
        )
    return found[0][0], found[0][1], None


# A normalized file side: ``(normalized_bytes, version_value, error)``.
# Exactly one of the first and the last is set; ``version_value`` is the
# single permitted version assignment the scan found.
_Normalized = tuple[bytes | None, bytes | None, str | None]


def _normalize_pyproject(blob: bytes) -> _Normalized:
    """Replace the single ``[project]`` version line with a placeholder."""
    _, versions = _scan_project_table(blob)
    if len(versions) != 1:
        return (
            None,
            None,
            f"expected exactly one version line in [project], found {len(versions)}",
        )
    lines = blob.split(b"\n")
    lines[versions[0][0]] = _NORMALIZED_VERSION_LINE
    return b"\n".join(lines), versions[0][1], None


def _normalize_lock(blob: bytes, project: bytes) -> _Normalized:
    """Replace the project package version line with a placeholder."""
    index, value, error = _scan_lock_package(blob, project)
    if error is not None:
        return None, None, error
    assert index is not None and value is not None
    lines = blob.split(b"\n")
    lines[index] = _NORMALIZED_VERSION_LINE
    return b"\n".join(lines), value, None


def _normalize_changelog(blob: bytes) -> bytes:
    """Drop changelog headings and link reference definitions."""
    return b"\n".join(
        line
        for line in blob.split(b"\n")
        if not line.startswith(b"## [") and _LINK_REF_LINE_RE.match(line) is None
    )


def _prove_blobs(
    repo: str,
    *,
    candidate_version: str,
    candidate_sha: str,
    final_sha: str,
    final_version: str,
    listed: set[bytes],
) -> list[str]:
    """Byte-compare each version file with only its version line normalized.

    ``listed`` holds the diff-tree paths; the changelog is only compared
    when listed (an unlisted file is identical by construction), while
    ``pyproject.toml`` and ``uv.lock`` are always read: their claimed
    versions must match the receipt and the tag even when the tree lists
    nothing.
    """
    cand_py, error = _git_blob(repo, candidate_sha, "pyproject.toml")
    if error is not None:
        return [error]
    final_py, error = _git_blob(repo, final_sha, "pyproject.toml")
    if error is not None:
        return [error]
    assert cand_py is not None and final_py is not None
    cand_names, _ = _scan_project_table(cand_py)
    final_names, _ = _scan_project_table(final_py)
    if len(final_names) != 1:
        return ["rc: pyproject.toml at the final commit has no [project] name line"]
    project = final_names[0][1]
    if len(cand_names) != 1 or cand_names[0][1] != project:
        return [
            f"rc: pyproject.toml at {candidate_sha} has no matching [project] name line"
        ]
    cand_claimed = candidate_version.encode("ascii")
    final_claimed = final_version.encode("ascii")
    project_text = project.decode("ascii", errors="replace")
    lines: list[str] = []
    cand_norm, cand_value, cand_error = _normalize_pyproject(cand_py)
    final_norm, final_value, final_error = _normalize_pyproject(final_py)
    if cand_error is not None:
        lines.append(f"rc: pyproject.toml at {candidate_sha}: {cand_error}")
    elif cand_value != cand_claimed:
        lines.append(
            f"rc: pyproject.toml at {candidate_sha} says version "
            f"{cand_value!r}, not the receipt core_version "
            f"{candidate_version!r}"
        )
    if final_error is not None:
        lines.append(f"rc: pyproject.toml at the final commit: {final_error}")
    elif final_value != final_claimed:
        lines.append(
            f"rc: pyproject.toml at the final commit says version "
            f"{final_value!r}, not the tag version {final_version!r}"
        )
    if cand_norm is not None and final_norm is not None and cand_norm != final_norm:
        lines.append(
            f"rc: pyproject.toml differs between {candidate_sha} and "
            f"{final_sha} beyond the project version"
        )
    cand_lock, error = _git_blob(repo, candidate_sha, "uv.lock")
    if error is not None:
        lines.append(error)
        return lines
    final_lock, error = _git_blob(repo, final_sha, "uv.lock")
    if error is not None:
        lines.append(error)
        return lines
    assert cand_lock is not None and final_lock is not None
    cand_lock_norm, cand_lock_value, cand_lock_error = _normalize_lock(
        cand_lock, project
    )
    final_lock_norm, final_lock_value, final_lock_error = _normalize_lock(
        final_lock, project
    )
    if cand_lock_error is not None:
        lines.append(f"rc: uv.lock at {candidate_sha}: {cand_lock_error}")
    elif cand_lock_value != cand_claimed:
        lines.append(
            f"rc: uv.lock at {candidate_sha} says {project_text} version "
            f"{cand_lock_value!r}, not the receipt core_version "
            f"{candidate_version!r}"
        )
    if final_lock_error is not None:
        lines.append(f"rc: uv.lock at the final commit: {final_lock_error}")
    elif final_lock_value != final_claimed:
        lines.append(
            f"rc: uv.lock at the final commit says {project_text} version "
            f"{final_lock_value!r}, not the tag version {final_version!r}"
        )
    if (
        cand_lock_norm is not None
        and final_lock_norm is not None
        and cand_lock_norm != final_lock_norm
    ):
        lines.append(
            f"rc: uv.lock differs between {candidate_sha} and {final_sha} "
            f"beyond the {project_text} package version"
        )
    if b"docs/CHANGELOG.md" in listed:
        cand_cl, error = _git_blob(repo, candidate_sha, "docs/CHANGELOG.md")
        if error is not None:
            lines.append(error)
            return lines
        final_cl, error = _git_blob(repo, final_sha, "docs/CHANGELOG.md")
        if error is not None:
            lines.append(error)
            return lines
        assert cand_cl is not None and final_cl is not None
        if _normalize_changelog(cand_cl) != _normalize_changelog(final_cl):
            lines.append(
                "rc: docs/CHANGELOG.md changes more than headings and "
                f"link refs between {candidate_sha} and {final_sha}"
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
