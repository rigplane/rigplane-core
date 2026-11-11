#!/usr/bin/env python3
"""Resolve a Core ref to exactly one commit (MOR-4408).

``core_ref`` may be a branch name, a tag name (annotated tags resolve to
their peeled commit), or a 40-hex SHA that is reachable in the repository —
either advertised by some ref in ``git ls-remote`` or fetchable by exact SHA.
Anything else — globs, short SHAs, commit-ish expressions such as ``HEAD~2``,
or names that match both a branch and a tag pointing at different commits —
is refused with exit code 2.

The branch is resolved here, once: the caller (``core-wheel.yml``'s build
job) checks out the resolved SHA, never the moving ref.

Outputs (``GITHUB_OUTPUT`` when set, else one JSON object on stdout):
``sha`` (40-hex), ``sha12``, ``requested_ref`` (the input verbatim) and
``tags_json`` (JSON array of every tag name, without ``refs/tags/``, whose
peeled commit is the resolved SHA).

Only the Python standard library is used: the light runner pool's system
``python3`` (3.10) executes this script without a project environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

_SHA_RE = re.compile(r"[0-9a-f]{40}")

# Names git refuses as ref names in practice. Exact matching against the
# full ls-remote listing makes glob metacharacters moot; they are refused
# anyway so the error names the real problem.
_BAD_REF_RE = re.compile(r"(?:^[-/])|\.\.|//|[\s~^:?*\[\\]|\.(?:lock)?$|@\{")


class RefResolutionError(ValueError):
    """The requested ref does not resolve to exactly one commit."""


def validate_ref_name(core_ref: str) -> None:
    if not core_ref or _BAD_REF_RE.search(core_ref):
        raise RefResolutionError(
            f"core_ref {core_ref!r} is not a valid branch or tag name"
        )


def parse_ls_remote(text: str) -> dict[str, str]:
    """Map every advertised ref name to its SHA (peeled entries included).

    Pseudo-refs such as ``HEAD`` (a symbolic ref, advertised by both local
    and HTTP remotes) are dropped: only ``refs/...`` name a branch or tag.
    """
    refs: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        sha, _, ref = line.partition("\t")
        if not _SHA_RE.fullmatch(sha) or not ref.startswith("refs/"):
            if ref == "HEAD":
                continue
            raise RefResolutionError(f"unexpected ls-remote line: {line!r}")
        refs[ref] = sha
    return refs


def _peeled_tag_sha(refs: dict[str, str], tag_ref: str) -> str:
    return refs.get(f"{tag_ref}^{{}}", refs[tag_ref])


def resolve_ref(refs: dict[str, str], core_ref: str) -> tuple[str, list[str]]:
    """Resolve to (sha, tag names at that sha) from one ls-remote listing."""
    validate_ref_name(core_ref)
    candidates = [f"refs/heads/{core_ref}", f"refs/tags/{core_ref}"]
    if core_ref.startswith(("refs/heads/", "refs/tags/")):
        candidates.insert(0, core_ref)
    commits: dict[str, str] = {}  # sha -> matched ref
    for ref in candidates:
        if ref in refs:
            sha = _peeled_tag_sha(refs, ref)
            commits.setdefault(sha, ref)
    if not commits:
        raise RefResolutionError(
            f"core_ref {core_ref!r} is neither a branch nor a tag of the repository"
        )
    if len(commits) > 1:
        pairs = ", ".join(f"{ref} -> {sha}" for sha, ref in commits.items())
        raise RefResolutionError(f"core_ref {core_ref!r} is ambiguous: {pairs}")
    [(sha, _)] = commits.items()
    return sha, tags_at_sha(refs, sha)


def tags_at_sha(refs: dict[str, str], sha: str) -> list[str]:
    """Every tag name whose peeled commit is ``sha``, sorted."""
    names = [
        ref[len("refs/tags/") :]
        for ref, ref_sha in refs.items()
        if ref.startswith("refs/tags/")
        and not ref.endswith("^{}")
        and _peeled_tag_sha(refs, ref) == sha
    ]
    return sorted(names)


def _sanitize(text: str, secret: str | None) -> str:
    if secret:
        return text.replace(secret, "***")
    return text


def _run_git(
    args: Sequence[str], *, cwd: Path | None = None, secret: str | None = None
) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = _sanitize(completed.stderr.strip(), secret)
        detail = detail.splitlines()[-1] if detail else "unknown error"
        raise RefResolutionError(f"git {' '.join(args[:2])} failed: {detail}")
    return completed.stdout


def _remote_url(remote: str, token: str | None) -> str:
    if not token or not remote.startswith("http"):
        return remote
    scheme, _, rest = remote.partition("://")
    return f"{scheme}://x-access-token:{token}@{rest}"


def _verify_sha_fetchable(remote: str, sha: str, token: str | None) -> None:
    """Reachability proof for a SHA advertised by no ref.

    GitHub's ``uploadpack.allowAnySHA1InWant`` serves exact-SHA fetches; a
    SHA no ref points at and that cannot be fetched is not in the repo.
    """
    with tempfile.TemporaryDirectory(prefix="core-ref-") as raw:
        scratch = Path(raw)
        _run_git(["init", "--quiet"], cwd=scratch)
        _run_git(
            ["fetch", "--quiet", "--no-tags", remote, sha],
            cwd=scratch,
            secret=token,
        )


def resolve(core_ref: str, *, remote: str, token: str | None) -> tuple[str, list[str]]:
    url = _remote_url(remote, token)
    listing = _run_git(["ls-remote", url], secret=token)
    refs = parse_ls_remote(listing)
    if _SHA_RE.fullmatch(core_ref):
        sha = core_ref
        if sha not in refs.values():
            _verify_sha_fetchable(url, sha, token)
            return sha, []
        return sha, tags_at_sha(refs, sha)
    return resolve_ref(refs, core_ref)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True, help="branch, tag or 40-hex SHA")
    parser.add_argument("--remote", required=True, help="remote URL or path")
    args = parser.parse_args(argv)
    try:
        sha, tags = resolve(
            args.ref,
            remote=args.remote,
            token=os.environ.get("GH_TOKEN"),
        )
    except (RefResolutionError, OSError) as exc:
        print(f"core_ref resolution FAILED: {exc}", file=sys.stderr)
        return 2
    outputs = {
        "sha": sha,
        "sha12": sha[:12],
        "requested_ref": args.ref,
        "tags_json": json.dumps(tags),
    }
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as stream:
            for name, value in outputs.items():
                stream.write(f"{name}={value}\n")
    else:
        print(json.dumps(outputs, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
