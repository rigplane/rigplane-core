#!/usr/bin/env python3
"""check_main_version.py — the tree's version must be newer than every release tag (MOR-4714).

After a release the merge-back sets main's version to the next development
version (``X.Y.(Z+1).dev0`` after ``vX.Y.Z``), so a build from main never
reports a version older than the last release in ``/api/v1/info``, support
reports and Sentry release names. This checker is the merge-back gate:

    python scripts/release/check_main_version.py [--repo PATH]

Exit codes: 0 — the tree's ``pyproject.toml`` version is strictly newer than
the newest ``v*`` tag (version order, rc/b/dev suffixes included) and the
project's own ``uv.lock`` entry agrees; 1 — equal or older, or the version
files disagree (one line per finding); 2 — the check cannot run (no tags, git
error, missing or unparseable version). Needs a checkout with tags
(``fetch-depth: 0`` in CI); stdlib only, no hardware, no network.

Version scheme: the repo's PEP 440 forms ``X.Y.Z``, ``X.Y.ZrcN``, ``X.Y.ZbN``,
``X.Y.Z.devN`` (plus the legacy ``alpha`` spelling and an optional ``-_.``
separator before the suffix). Anything else fails closed rather than guessing
an order.
"""

from __future__ import annotations

import sys


def _check_python_version(version: tuple[int, ...] = sys.version_info) -> int | None:
    """Return exit code 2 when ``version`` is too old, else None.

    ``tomllib`` is stdlib only from 3.11; the runner's system python may be
    older, so fail clearly instead of an ImportError traceback.
    """
    if version < (3, 11):
        print(
            f"check_main_version.py needs Python 3.11+ (tomllib), got "
            f"{'.'.join(map(str, version[:3]))}",
            file=sys.stderr,
        )
        return 2
    return None


if _check_python_version() is not None:
    sys.exit(2)

import argparse  # noqa: E402 — after the version guard so a wrong interpreter fails clearly
import re  # noqa: E402
import subprocess  # noqa: E402
import tomllib  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Iterable, NamedTuple  # noqa: E402

_VERSION_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)(?:[-_.]?(a|alpha|b|beta|rc|dev)(\d+)?)?$"
)
# PEP 440 phase order within one X.Y.Z: dev < a < b < rc < final.
_PHASES = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "rc": 3}


def version_key(version: str) -> tuple[int, int, int, int, int]:
    """Ordering key for the repo's version scheme; ValueError on anything else."""
    match = _VERSION_RE.match(version)
    if match is None:
        raise ValueError(f"unparseable version: {version!r}")
    major, minor, patch, phase, num = match.groups()
    return (
        int(major),
        int(minor),
        int(patch),
        _PHASES.get(phase, 4),
        int(num) if num else 0,
    )


def latest_release_tag(tags: Iterable[str]) -> str:
    """Newest ``v*`` tag in version order; ValueError when none or one is bad."""
    newest: str | None = None
    newest_key: tuple[int, int, int, int, int] | None = None
    for tag in tags:
        key = version_key(tag.removeprefix("v"))
        if newest_key is None or key > newest_key:
            newest, newest_key = tag, key
    if newest is None:
        raise ValueError("no v* tags found (fetch tags before running this check)")
    return newest


def _git_tags(repo: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo), "tag", "--list", "v*"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return completed.stdout.split()


class ProjectMetadata(NamedTuple):
    name: str
    version: str


class LockMetadata(NamedTuple):
    version: str


def _read_versions(repo: Path) -> tuple[ProjectMetadata, LockMetadata]:
    """Read the required project and lock metadata."""
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    raw_project = pyproject.get("project")
    if not isinstance(raw_project, dict):
        raise ValueError("pyproject.toml has no project table")
    name, version = raw_project.get("name"), raw_project.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise ValueError("pyproject.toml project name and version must be strings")
    project = ProjectMetadata(name, version)
    lock = tomllib.loads((repo / "uv.lock").read_text(encoding="utf-8"))
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise ValueError("uv.lock has no package array")
    for package in packages:
        if isinstance(package, dict) and package.get("name") == project.name:
            lock_version = package.get("version")
            if not isinstance(lock_version, str):
                raise ValueError("uv.lock project version must be a string")
            return project, LockMetadata(lock_version)
    raise ValueError(f"uv.lock has no {project.name!r} entry")


def check(repo: Path) -> tuple[int, list[str]]:
    """Return (exit code, lines): 0 pass, 1 finding(s), 2 cannot decide."""
    try:
        newest = latest_release_tag(_git_tags(repo))
        project, lock = _read_versions(repo)
        tree_key = version_key(project.version)
        tag_key = version_key(newest.removeprefix("v"))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        return 2, [f"cannot decide: {exc}"]
    lines = []
    version = project.version
    if lock.version != version:
        lines.append(
            f"uv.lock version {lock.version!r} != pyproject.toml version {version!r}"
        )
    if tree_key <= tag_key:
        lines.append(f"version {version} is not newer than latest release tag {newest}")
    if lines:
        return 1, lines
    return 0, [f"version {version} > latest release tag {newest}: OK"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_main_version.py",
        description=(
            "Fail when the tree's version is not newer than the newest release tag."
        ),
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="checkout to check (default: this repository)",
    )
    args = parser.parse_args(argv)
    code, lines = check(args.repo)
    stream = sys.stdout if code == 0 else sys.stderr
    for line in lines:
        print(line, file=stream)
    return code


if __name__ == "__main__":
    sys.exit(main())
