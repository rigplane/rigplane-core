"""
bump_downstream_pin.py — edit downstream pin files for a new core version.

Usage (from the root of the target downstream repo):
    python bump_downstream_pin.py --repo pro   --version 2.11.0
    python bump_downstream_pin.py --repo station --version 2.11.0

For "pro":
  - Writes `v<version>` to CORE_VERSION
  - Replaces `rigplane[bridge]==<old>` with `rigplane[bridge]==<version>` in pyproject.toml

For "station":
  - Replaces `rigplane==<old>` with `rigplane==<version>` in pyproject.toml

The script exits with code 0 and a summary on success, non-zero on error.
It is intentionally free of third-party deps (stdlib only) so it can run
in a bare `actions/checkout` step with no dependency install.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _replace_once(text: str, pattern: str, replacement: str, label: str) -> str:
    """Replace exactly one occurrence; raise if zero or >1 matches."""
    matches = list(re.finditer(pattern, text))
    if len(matches) == 0:
        raise ValueError(f"Pattern not found in {label}: {pattern!r}")
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous: {len(matches)} matches for {pattern!r} in {label}; "
            "expected exactly one."
        )
    return text[: matches[0].start()] + replacement + text[matches[0].end() :]


def bump_pro(repo_root: Path, version: str) -> None:
    """Bump CORE_VERSION and rigplane[bridge]== in pyproject.toml for rigplane-pro."""
    core_version_file = repo_root / "CORE_VERSION"
    pyproject_file = repo_root / "pyproject.toml"

    if not core_version_file.exists():
        raise FileNotFoundError(f"CORE_VERSION not found at {core_version_file}")
    if not pyproject_file.exists():
        raise FileNotFoundError(f"pyproject.toml not found at {pyproject_file}")

    old_tag = core_version_file.read_text(encoding="utf-8").strip()
    old_version = old_tag.removeprefix("v")

    new_tag = f"v{version}"
    core_version_file.write_text(new_tag + "\n", encoding="utf-8")
    print(f"  CORE_VERSION: {old_tag!r} -> {new_tag!r}")

    pyproject_text = pyproject_file.read_text(encoding="utf-8")
    old_pin = f"rigplane[bridge]=={old_version}"
    new_pin = f"rigplane[bridge]=={version}"
    # Match the pin as it appears inside the TOML deps array (possibly quoted)
    pattern = re.escape(old_pin)
    updated = _replace_once(pyproject_text, pattern, new_pin, "pyproject.toml")
    pyproject_file.write_text(updated, encoding="utf-8")
    print(f"  pyproject.toml: {old_pin!r} -> {new_pin!r}")


def bump_station(repo_root: Path, version: str) -> None:
    """Bump rigplane== in pyproject.toml for rigplane-station."""
    pyproject_file = repo_root / "pyproject.toml"

    if not pyproject_file.exists():
        raise FileNotFoundError(f"pyproject.toml not found at {pyproject_file}")

    pyproject_text = pyproject_file.read_text(encoding="utf-8")

    # Find current pinned version to report it
    m = re.search(r"rigplane==(\d+\.\d+\.\d+)", pyproject_text)
    old_pin = m.group(0) if m else "rigplane==<unknown>"
    new_pin = f"rigplane=={version}"

    if old_pin == new_pin:
        print(f"  pyproject.toml already at {new_pin!r} — no-op")
        return

    updated = _replace_once(
        pyproject_text, re.escape(old_pin), new_pin, "pyproject.toml"
    )
    pyproject_file.write_text(updated, encoding="utf-8")
    print(f"  pyproject.toml: {old_pin!r} -> {new_pin!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bump downstream core pin files.")
    parser.add_argument(
        "--repo",
        required=True,
        choices=["pro", "station"],
        help="Which downstream repo layout to bump.",
    )
    parser.add_argument(
        "--version",
        required=True,
        help="New core version WITHOUT leading 'v' (e.g. 2.11.0).",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Root directory of the downstream repo (default: cwd).",
    )
    args = parser.parse_args(argv)

    version = args.version.lstrip("v")  # tolerate accidental 'v' prefix
    repo_root = Path(args.root).resolve()

    print(f"Bumping {args.repo} pin to {version} in {repo_root}")
    if args.repo == "pro":
        bump_pro(repo_root, version)
    else:
        bump_station(repo_root, version)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
