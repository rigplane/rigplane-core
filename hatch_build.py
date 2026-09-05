"""Hatchling build hook that bundles the Svelte web UI into the package.

The frontend lives in ``frontend/`` and its build output (``frontend/dist``)
is gitignored. At runtime the SPA is served from ``src/rigplane/web/static``,
so the package must ship the built assets at that location.

This hook gives each build target the frontend payload needed for its role:

* If ``frontend/dist/index.html`` is missing and a ``frontend/`` source tree is
  present, it builds the SPA with ``npm`` (``npm ci``/``npm install`` then
  ``npm run build``). If ``npm`` is unavailable and no prebuilt dist exists, it
  raises a clear ``RuntimeError`` so the failure is actionable.
* If ``frontend/dist/index.html`` already exists, the npm build is skipped — the
  hook is idempotent, so the PyPI publish flow (which prebuilds the frontend)
  does not rebuild it.
* The built ``frontend/dist`` is force-included at ``src/rigplane/web/static``
  for whichever target is building, centralizing the mapping in one place.

A built wheel contains the Web UI and needs no Node.js to install. A source
distribution contains the frontend source but not ``frontend/dist``; building
a wheel from that sdist invokes this hook and requires Node.js/npm to compile
the Web UI.

Without this, ``pip install git+https://...`` (a direct wheel build) would not
bundle the SPA and the desktop UI would 404.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Runtime location the installed package serves the SPA from. Must not change.
_RUNTIME_STATIC = "src/rigplane/web/static"


class FrontendBuildHook(BuildHookInterface):  # type: ignore[type-arg]
    """Build the web UI (if needed) and bundle it into the package."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root)
        frontend_dir = root / "frontend"
        dist_index = frontend_dir / "dist" / "index.html"

        if not dist_index.exists() and frontend_dir.is_dir():
            self._build_frontend(frontend_dir)

        # Inject the force-include so the SPA lands at the runtime location for
        # whichever target (wheel or sdist) is building.
        force_include = build_data.setdefault("force_include", {})
        force_include["frontend/dist"] = _RUNTIME_STATIC

    def _build_frontend(self, frontend_dir: Path) -> None:
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError(
                "Building rigplane from source requires Node.js/npm to build "
                "the web UI (frontend/dist is not present and could not be "
                "built). Install Node.js/npm, or install a prebuilt wheel "
                "(the wheel includes the web UI and needs no Node.js to "
                "install)."
            )

        lockfile = frontend_dir / "package-lock.json"
        install_cmd = [npm, "ci"] if lockfile.exists() else [npm, "install"]

        self.app.display_info("Building rigplane web UI (npm)...")
        subprocess.run(install_cmd, cwd=frontend_dir, check=True)
        subprocess.run([npm, "run", "build"], cwd=frontend_dir, check=True)
