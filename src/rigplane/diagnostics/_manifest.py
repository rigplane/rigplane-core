"""manifest.json builder per ``rigplane-bundle-v2`` schema (spec §5.2).

The bundle producer supports two on-the-wire schema strings:

- ``rigplane-bundle-v2`` — canonical for rigplane v2.0.0+ (default).
- ``icom-lan-bundle-v1`` — legacy format from the icom-lan brand. Tower
  accepts it for the 12-month deprecation window documented in
  ``docs/contracts/diagnostic-bundle-v2.md``. Kept available for
  backwards-compatibility tests and unit fixtures.

Pick the schema by passing ``schema_version=SCHEMA_VERSION_V1`` (or v2) to
:class:`_Manifest` / :func:`rigplane.diagnostics.build_bundle`. ``app.name``
is derived from the schema choice and need not be set independently.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from rigplane.diagnostics.contributor import BundleContext, DiagnosticContributor

# Wire-contract schema strings. v1 is the historical icom-lan format kept
# for testing + backwards-compat verification; v2 is the canonical format
# for rigplane v2.0.0+.
SCHEMA_VERSION_V1: Final = "icom-lan-bundle-v1"
SCHEMA_VERSION_V2: Final = "rigplane-bundle-v2"
SCHEMA_VERSION: Final = SCHEMA_VERSION_V2  # current default

# Application name embedded in bundle metadata. Derived from the schema
# choice; v1 emits "icom-lan", v2 emits "rigplane".
APP_NAME_V1: Final = "icom-lan"
APP_NAME_V2: Final = "rigplane"
APP_NAME: Final = APP_NAME_V2  # current default

# All accepted schema_version strings (used to validate caller input).
_SUPPORTED_SCHEMAS: Final = frozenset({SCHEMA_VERSION_V1, SCHEMA_VERSION_V2})

# Map schema_version → app.name. Keeps the two fields locked together so a
# caller can never produce a v2 manifest with app.name == "icom-lan".
_APP_NAME_FOR_SCHEMA: Final = {
    SCHEMA_VERSION_V1: APP_NAME_V1,
    SCHEMA_VERSION_V2: APP_NAME_V2,
}

_PYPI_NAME = "rigplane"
_VERSION_FALLBACK = "0.0.0+unknown"
_GIT_TIMEOUT_S = 2.0

_OS_MAP = {"darwin": "darwin", "linux": "linux", "win32": "windows"}


def _read_version() -> str:
    try:
        return importlib.metadata.metadata(_PYPI_NAME)["Version"] or _VERSION_FALLBACK
    except Exception:
        return _VERSION_FALLBACK


def _read_build_id() -> str | None:
    """Best-effort `git describe --always`; absent for non-git installs."""
    try:
        result = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def _platform_dict() -> dict[str, str]:
    os_name = _OS_MAP.get(sys.platform, sys.platform)
    arch = platform.machine() or "unknown"
    if arch == "AMD64":
        arch = "x86_64"
    return {
        "os": os_name,
        "arch": arch,
        "python_version": sys.version.split()[0],
    }


@dataclass
class _ContributorRecord:
    name: str
    files: list[str]
    size_bytes: int


@dataclass
class _Warning:
    contributor: str
    message: str


class _Manifest:
    def __init__(
        self, ctx: BundleContext, *, schema_version: str = SCHEMA_VERSION
    ) -> None:
        if schema_version not in _SUPPORTED_SCHEMAS:
            raise ValueError(
                f"unsupported schema_version: {schema_version!r}; "
                f"expected one of {sorted(_SUPPORTED_SCHEMAS)}"
            )
        self._ctx = ctx
        self._schema_version = schema_version
        self._app_name = _APP_NAME_FOR_SCHEMA[schema_version]
        self._contributors: list[_ContributorRecord] = []
        self._warnings: list[_Warning] = []

    def record_success(
        self, contributor: DiagnosticContributor, contributor_dir: Path
    ) -> None:
        files: list[str] = []
        size = 0
        for f in sorted(contributor_dir.rglob("*")):
            if f.is_file():
                files.append(str(f.relative_to(contributor_dir)))
                size += f.stat().st_size
        self._contributors.append(
            _ContributorRecord(name=contributor.name, files=files, size_bytes=size)
        )

    def record_warning(self, contributor: DiagnosticContributor, message: str) -> None:
        self._warnings.append(_Warning(contributor=contributor.name, message=message))

    def to_dict(self) -> dict[str, Any]:
        ctx = self._ctx
        app: dict[str, Any] = {"name": self._app_name, "version": _read_version()}
        build_id = _read_build_id()
        if build_id:
            app["build_id"] = build_id

        manifest: dict[str, Any] = {
            "schema_version": self._schema_version,
            "submission_id": ctx.submission_id,
            "generated_at_unix": ctx.generated_at_unix,
            "app": app,
            "platform": _platform_dict(),
        }

        if ctx.user_description:
            manifest["user_description"] = ctx.user_description
        if ctx.issue_ref:
            manifest["issue_ref"] = ctx.issue_ref

        contact: dict[str, str] = {}
        if ctx.contact_email:
            contact["email"] = ctx.contact_email
        if ctx.contact_callsign:
            contact["callsign"] = ctx.contact_callsign
        if contact:
            manifest["contact"] = contact

        if self._contributors:
            manifest["contributors"] = [
                {"name": c.name, "files": c.files, "size_bytes": c.size_bytes}
                for c in self._contributors
            ]
        if self._warnings:
            manifest["warnings"] = [
                {"contributor": w.contributor, "message": w.message}
                for w in self._warnings
            ]

        return manifest

    def write(self, path: Path) -> None:
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )
