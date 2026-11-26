"""Config contributor — read ``ctx.config_dir/*.toml``, redact, drop secret keys."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rigplane.diagnostics.redaction import redact_config

if TYPE_CHECKING:
    from rigplane.diagnostics.contributor import BundleContext


class ConfigContributor:
    """Emits ``config/config-summary.json`` with sanitised TOML configs."""

    name = "config"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        files: list[dict[str, Any]] = []

        config_dir = ctx.config_dir
        if config_dir.exists() and config_dir.is_dir():
            for toml_path in sorted(config_dir.glob("*.toml")):
                try:
                    with toml_path.open("rb") as fh:
                        parsed = tomllib.load(fh)
                except Exception as exc:
                    files.append(
                        {
                            "name": toml_path.name,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    continue
                files.append(
                    {
                        "name": toml_path.name,
                        "content": redact_config(parsed),
                    }
                )

        payload = {"files": files}
        text = json.dumps(payload, indent=2, sort_keys=True)
        # Redact per value before dumping so regexes cannot consume JSON structure.
        (output_dir / "config-summary.json").write_text(text + "\n", encoding="utf-8")
