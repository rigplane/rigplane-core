#!/usr/bin/env python3
"""Compute the Core wheel version for one commit (MOR-4408).

Inputs: the ``[project]`` version of the checkout being built, the 40-hex
commit SHA, and the JSON array of tag names pointing at that commit (peeled;
the ``resolve`` job of ``core-wheel.yml`` produces it from a single
``git ls-remote``).

Rule:

- exactly one ``v<version>`` release tag at the SHA whose version equals the
  pyproject version -> the wheel version is the pyproject version unchanged
  (a release build);
- exactly one ``v<version>`` release tag whose version differs -> fail: a
  mismatched tag must never ship;
- anything else (no release tag, or more than one) -> ``<pyproject>+g<sha12>``,
  a PEP 440 local version: unique per commit, satisfies a
  ``==<pyproject version>`` pin, sorts newer than the same public version,
  and PyPI rejects local versions, so such a wheel can never be published
  by accident.

Validation uses the canonical PEP 440 regex, not ``packaging``: the workflow
runs this script under ``uv run --no-project``, where ``packaging`` is not
installed, and the tests must exercise exactly the code path the workflow
runs instead of an import-available fallback that could diverge.

``--write-pyproject`` rewrites the first ``version = "..."`` line of the
given pyproject (the throwaway build checkout's, never the repository's)
with the computed version, atomically.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Sequence

# Canonical PEP 440 version regex (PEP 440 appendix), tightened with
# fullmatch. A version is valid when the whole string matches and a release
# segment is present; everything else (including junk like "3.2.0.0.0x")
# fails.
_VERSION_RE = re.compile(
    r"""
    v?
    (?:(?P<epoch>[0-9]+)!)?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>[-_.]?(?P<pre_l>a|b|c|rc|alpha|beta|pre|preview)[-_.]?(?P<pre_n>[0-9]+)?)?
    (?P<post>(?:-(?P<post_n1>[0-9]+))|(?:[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n2>[0-9]+)?))?
    (?P<dev>[-_.]?dev[-_.]?(?P<dev_n>[0-9]+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
    """,
    re.VERBOSE | re.IGNORECASE,
)

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_VERSION_LINE_RE = re.compile(r'(?m)^version = "[^"\n]*"$')

_PRE_LETTER = {
    "alpha": "a",
    "beta": "b",
    "c": "rc",
    "pre": "rc",
    "preview": "rc",
}


class VersionRuleError(ValueError):
    """The inputs do not satisfy the wheel version rule."""


def normalize_public_version(text: str) -> str:
    """Return the canonical public form of ``text``; local parts are rejected.

    ``3.2.0-rc.1``, ``3.2.0rc1`` and ``v3.2.0RC1`` all normalize to
    ``3.2.0rc1``. Numeric pre/post/dev suffixes normalize as integers with
    the omitted number meaning zero, per PEP 440: ``rc01`` equals ``rc1``,
    and a bare ``rc`` equals ``rc0`` (same for ``.post``/``.dev``), so a
    tag ``v3.2.0rc01`` releases pyproject's ``3.2.0rc1``. Release segments
    are compared literally (``3.2`` is not ``3.2.0``): a release tag must
    mirror the pyproject version exactly, and anything looser would hide a
    release mistake.
    """
    match = _VERSION_RE.fullmatch(text.strip())
    if match is None or match.group("release") is None:
        raise VersionRuleError(f"not a valid PEP 440 version: {text!r}")
    if match.group("local"):
        raise VersionRuleError(
            f"version {text!r} already carries a local part; the version rule "
            "generates local parts itself and never accepts one as input"
        )
    epoch = f"{match.group('epoch')}!" if match.group("epoch") else ""
    canonical = epoch + match.group("release")
    if match.group("pre"):
        letter = match.group("pre_l").lower()
        letter = _PRE_LETTER.get(letter, letter)
        number = str(int(match.group("pre_n") or "0"))
        canonical += f"{letter}{number}"
    if match.group("post"):
        raw_number = match.group("post_n1") or match.group("post_n2") or "0"
        canonical += f".post{int(raw_number)}"
    if match.group("dev"):
        canonical += f".dev{int(match.group('dev_n') or '0')}"
    return canonical


def release_tag_versions(tag_names: Sequence[str]) -> dict[str, str]:
    """Map each ``v<version>`` release tag to its normalized version.

    Tags that are not ``v``-prefixed release tags (no ``v`` prefix, or a
    suffix that is not a clean public version) are not release tags and are
    left out.
    """
    versions: dict[str, str] = {}
    for name in tag_names:
        if not name.startswith("v"):
            continue
        try:
            versions[name] = normalize_public_version(name[1:])
        except VersionRuleError:
            continue
    return versions


def wheel_version(pyproject_version: str, sha: str, tag_names: Sequence[str]) -> str:
    """Apply the version rule; raises :class:`VersionRuleError` on mismatch."""
    if not _SHA_RE.fullmatch(sha):
        raise VersionRuleError(
            f"SHA must be exactly 40 lowercase hex characters: {sha!r}"
        )
    public = normalize_public_version(pyproject_version)
    release_tags = release_tag_versions(tag_names)
    if len(release_tags) == 1:
        [(tag_name, tag_version)] = release_tags.items()
        if tag_version != public:
            raise VersionRuleError(
                f"tag {tag_name} at SHA {sha} says version {tag_version}, "
                f"but pyproject.toml says {pyproject_version!r}; a "
                "mismatched tag must never ship"
            )
        return pyproject_version
    return f"{pyproject_version}+g{sha[:12]}"


def read_pyproject_version(path: Path) -> str:
    import tomllib

    with path.open("rb") as stream:
        data = tomllib.load(stream)
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise VersionRuleError(f"{path} has no [project] version string")
    return version


def write_pyproject_version(path: Path, version: str) -> None:
    """Atomically rewrite the first ``version = "..."`` line of ``path``."""
    text = path.read_text(encoding="utf-8")
    updated, count = _VERSION_LINE_RE.subn(f'version = "{version}"', text, count=1)
    if count != 1:
        raise VersionRuleError(
            f'{path} has no top-level `version = "..."` line to rewrite'
        )
    _atomic_write(path, updated)


def _atomic_write(path: Path, text: str) -> None:
    handle, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", type=Path, required=True)
    parser.add_argument(
        "--sha",
        required=True,
        help="40-hex commit SHA the wheel is built from",
    )
    parser.add_argument(
        "--tags-json",
        default="[]",
        help="JSON array of tag names pointing at the SHA (peeled)",
    )
    parser.add_argument(
        "--write-pyproject",
        type=Path,
        help="rewrite the version line of this pyproject with the result",
    )
    args = parser.parse_args(argv)
    try:
        tag_names = json.loads(args.tags_json)
        if not isinstance(tag_names, list) or not all(
            isinstance(name, str) for name in tag_names
        ):
            raise VersionRuleError("--tags-json must be a JSON array of strings")
        version = wheel_version(
            read_pyproject_version(args.pyproject), args.sha, tag_names
        )
        if args.write_pyproject is not None:
            write_pyproject_version(args.write_pyproject, version)
    except (VersionRuleError, ValueError, OSError) as exc:
        print(f"core wheel version FAILED: {exc}", file=sys.stderr)
        return 2
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
