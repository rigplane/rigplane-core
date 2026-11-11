#!/usr/bin/env python3
"""Typed contract for ``core-wheel.json`` (MOR-4408).

``core-wheel.json`` is the manifest inside the uploaded wheel artifact: it
crosses a product boundary (Core's CI writes it; Pro, Station and RigDeck
read it), so it goes through this typed contract instead of shell
interpolation of ref text into a JSON heredoc — a legal ref containing a
quote (``feature/"probe"``) would produce invalid JSON that way.

- :class:`CoreWheelManifest` is the schema (exactly six string fields);
- :func:`build_manifest` validates and :func:`render_manifest` serializes
  (a real JSON encoder, ``json.dumps``);
- :func:`parse_manifest` is the reader consumers (and the tests) use: the
  exact field set, the declared types, and the field rules below.

Field rules: ``sha`` is 40 lowercase hex; ``version``, ``requested_ref`` and
``wheel`` are non-empty single-line strings; ``wheel`` is a file name (no
path separators); ``wheel_sha256`` is 64 lowercase hex; ``profile_smoke`` is
one of :data:`PROFILE_SMOKE_VALUES` — ``target`` (the target revision's own
smoke script verified the installed wheel) or ``not_available_for_target``
(the target predates the script; the skip is explicit, never a silent pass
or a silent failure). Unknown keys are rejected — consumers read the
contract's fields, and silent drift must fail loudly. The version's own
semantics belong to ``core_wheel_version.py``.

Standard library only: the workflow runs this under ``uv run --no-project``
and the tests under a bare ``python3``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence, TypedDict

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

# The installed-profile smoke outcomes a manifest may carry.
PROFILE_SMOKE_VALUES = ("target", "not_available_for_target")


class CoreWheelManifest(TypedDict):
    """The six-field manifest inside the wheel artifact."""

    sha: str
    version: str
    requested_ref: str
    wheel: str
    wheel_sha256: str
    profile_smoke: str


class ManifestError(ValueError):
    """The manifest text does not satisfy the contract."""


def _validate_field(name: str, value: object) -> str:
    if name in ("sha", "wheel_sha256"):
        pattern = _SHA_RE if name == "sha" else _SHA256_RE
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ManifestError(
                f"{name} must be {'40' if name == 'sha' else '64'} lowercase hex characters"
            )
        return value
    if not isinstance(value, str):
        raise ManifestError(f"{name} must be a string")
    if not value or "\n" in value or "\r" in value:
        raise ManifestError(f"{name} must be a non-empty single-line string")
    if name == "wheel" and ("/" in value or "\\" in value):
        raise ManifestError("wheel must be a file name, not a path")
    if name == "profile_smoke" and value not in PROFILE_SMOKE_VALUES:
        raise ManifestError(
            f"profile_smoke must be one of {list(PROFILE_SMOKE_VALUES)}, not {value!r}"
        )
    return value


def build_manifest(
    *,
    sha: str,
    version: str,
    requested_ref: str,
    wheel: str,
    wheel_sha256: str,
    profile_smoke: str,
) -> CoreWheelManifest:
    """Validate the fields and return the manifest."""
    manifest = CoreWheelManifest(
        sha=sha,
        version=version,
        requested_ref=requested_ref,
        wheel=wheel,
        wheel_sha256=wheel_sha256,
        profile_smoke=profile_smoke,
    )
    for name in manifest:
        _validate_field(name, manifest[name])
    return manifest


def render_manifest(manifest: CoreWheelManifest) -> str:
    """Serialize with a real JSON encoder — never string interpolation."""
    return json.dumps(manifest, indent=2, sort_keys=True)


def parse_manifest(text: str) -> CoreWheelManifest:
    """Parse and validate ``text``; :class:`ManifestError` on any drift."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ManifestError(f"not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ManifestError("the manifest must be a JSON object")
    expected = set(CoreWheelManifest.__annotations__)
    unknown = set(data) - expected
    if unknown:
        raise ManifestError(f"unknown manifest keys: {sorted(unknown)}")
    missing = expected - set(data)
    if missing:
        raise ManifestError(f"missing manifest keys: {sorted(missing)}")
    return build_manifest(**data)  # type: ignore[arg-type]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True, help="40-hex commit built")
    parser.add_argument("--version", required=True, help="wheel version")
    parser.add_argument(
        "--requested-ref", required=True, help="the core_ref input, verbatim"
    )
    parser.add_argument("--wheel", required=True, help="wheel file name")
    parser.add_argument("--wheel-sha256", required=True, help="sha256 of the wheel")
    parser.add_argument(
        "--profile-smoke",
        required=True,
        help="installed-profile smoke outcome: 'target' or 'not_available_for_target'",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="manifest path to write"
    )
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(
            sha=args.sha,
            version=args.version,
            requested_ref=args.requested_ref,
            wheel=args.wheel,
            wheel_sha256=args.wheel_sha256,
            profile_smoke=args.profile_smoke,
        )
    except ManifestError as exc:
        print(f"core-wheel manifest FAILED: {exc}", file=sys.stderr)
        return 2
    args.output.write_text(render_manifest(manifest) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
