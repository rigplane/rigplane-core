"""System diagnostic contributor — OS, arch, Python, rigplane version, install method."""

from __future__ import annotations

import importlib.metadata
import json
import platform
from pathlib import Path
from typing import TYPE_CHECKING

from rigplane.diagnostics.redaction import redact_paths

if TYPE_CHECKING:
    from rigplane.diagnostics.contributor import BundleContext


def _get_version() -> str:
    try:
        return importlib.metadata.version("rigplane")
    except Exception:
        return "unknown"


def _detect_install_method() -> str:
    """Return ``"editable"`` if installed via ``pip install -e``, else ``"wheel"``.

    Falls back to ``"unknown"`` on any metadata access failure.
    """
    try:
        dist = importlib.metadata.distribution("rigplane")
        direct_url = dist.read_text("direct_url.json")
        if direct_url is None:
            return "wheel"
        data = json.loads(direct_url)
        dir_info = data.get("dir_info") or {}
        if dir_info.get("editable") is True:
            return "editable"
        return "wheel"
    except Exception:
        return "unknown"


def _normalise_os() -> str:
    name = platform.system().lower()
    if name == "darwin":
        return "darwin"
    if name == "linux":
        return "linux"
    if name == "windows":
        return "windows"
    return name or "unknown"


class SystemContributor:
    """Emits ``system/system.json`` with OS, arch, Python, rigplane version."""

    name = "system"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        payload = {
            "os": _normalise_os(),
            "arch": platform.machine() or "unknown",
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "rigplane_version": redact_paths(_get_version()),
            "install_method": redact_paths(_detect_install_method()),
        }
        text = json.dumps(payload, indent=2, sort_keys=True)
        (output_dir / "system.json").write_text(text + "\n", encoding="utf-8")
