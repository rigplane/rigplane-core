#!/usr/bin/env python3
"""Resolve a Core ref to exactly one commit (MOR-4408).

``core_ref`` may be a branch name, a tag name (annotated tags resolve to
their peeled commit), or a 40-hex object ID that is reachable in the
repository — either advertised by some ref in ``git ls-remote`` or fetchable
by exact SHA — and names a commit: a raw annotated-tag OBJECT ID is peeled
to its commit, and a fetchable non-commit object (a blob, a tree) is
refused. Anything else — globs, short SHAs, commit-ish expressions such as
``HEAD~2``, or names that match both a branch and a tag pointing at
different commits — is refused with exit code 2.

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


_USERINFO_RE = re.compile(r"://[^/@\s'\"]+@")


def _sanitize(text: str, secret: str | None) -> str:
    """Strip every credential form out of ``text``.

    The token itself is replaced, and any URL userinfo git may have echoed
    (``https://x-access-token:***@...`` after the first pass) collapses to
    ``***@``: diagnostics must not even hint at the credential slot.
    """
    if secret:
        text = text.replace(secret, "***")
    return _USERINFO_RE.sub("://***@", text)


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
        # Never the argv: for ls-remote/fetch it carries the
        # credential-bearing remote URL, sanitized or not.
        raise RefResolutionError(f"git {args[0]} failed: {detail}")
    return completed.stdout


def _remote_url(remote: str, token: str | None) -> str:
    if not token or not remote.startswith("http"):
        return remote
    scheme, _, rest = remote.partition("://")
    return f"{scheme}://x-access-token:{token}@{rest}"


def _peel_to_commit(url: str, sha: str, token: str | None) -> str:
    """Fetch ``sha`` into a scratch repo and peel it to its commit SHA.

    A SHA advertised by no ref must be proven fetchable anyway (GitHub's
    ``uploadpack.allowAnySHA1InWant`` serves exact-SHA fetches), and a raw
    object ID that is an annotated tag OBJECT — not its commit — must peel
    so the commit SHA propagates everywhere (stamp, manifest, artifact
    name). Anything fetchable that is not a commit (a blob, a tree) is
    refused: a wheel must be built from a commit.
    """
    with tempfile.TemporaryDirectory(prefix="core-ref-") as raw:
        scratch = Path(raw)
        _run_git(["init", "--quiet"], cwd=scratch)
        _run_git(["fetch", "--quiet", "--no-tags", url, sha], cwd=scratch, secret=token)
        try:
            peeled = _run_git(["rev-parse", f"{sha}^{{commit}}"], cwd=scratch).strip()
        except RefResolutionError:
            object_type = _object_type(scratch, sha)
            raise RefResolutionError(
                f"core_ref {sha} is a {object_type} object, not a commit"
            ) from None
    if not _SHA_RE.fullmatch(peeled):
        raise RefResolutionError(f"core_ref {sha} did not peel to a commit: {peeled!r}")
    return peeled


def _object_type(scratch: Path, sha: str) -> str:
    try:
        return _run_git(["cat-file", "-t", sha], cwd=scratch).strip()
    except RefResolutionError:
        return "non-commit"


def resolve(core_ref: str, *, remote: str, token: str | None) -> tuple[str, list[str]]:
    url = _remote_url(remote, token)
    listing = _run_git(["ls-remote", url], secret=token)
    refs = parse_ls_remote(listing)
    if _SHA_RE.fullmatch(core_ref):
        commit = _peel_to_commit(url, core_ref, token)
        return commit, tags_at_sha(refs, commit)
    return resolve_ref(refs, core_ref)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ref", required=True, help="branch, tag or 40-hex object ID naming a commit"
    )
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
