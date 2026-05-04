"""Dependencies contributor — ``pip-freeze.txt`` via ``importlib.metadata``."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from icom_lan.diagnostics.contributor import BundleContext


class DependenciesContributor:
    """Emits ``dependencies/pip-freeze.txt`` listing installed packages."""

    name = "dependencies"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        lines = sorted(
            (
                f"{dist.name}=={dist.version}"
                for dist in importlib.metadata.distributions()
                if dist.name
            ),
            key=str.lower,
        )
        (output_dir / "pip-freeze.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
