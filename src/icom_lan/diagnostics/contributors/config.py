"""Config contributor — read ``ctx.config_dir/*.toml``, redact, drop secret keys."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from icom_lan.diagnostics.redaction import redact_credentials

if TYPE_CHECKING:
    from icom_lan.diagnostics.contributor import BundleContext


# Keys whose values are NEVER recorded — entire key is dropped (not masked).
# Match is case-insensitive, exact-key (not substring), at any nesting depth.
_SECRET_KEYS: frozenset[str] = frozenset({"password", "pwd", "secret", "token"})


def _sanitise(value: Any) -> Any:
    """Recursively drop secret-named keys; redact string values.

    String values are redacted in-place (per-value) rather than after
    ``json.dumps`` so the credential-pattern regex (``\\S+``) cannot
    over-greedy-match across JSON structural characters.
    """
    if isinstance(value, dict):
        return {
            k: _sanitise(v) for k, v in value.items() if k.lower() not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [_sanitise(item) for item in value]
    if isinstance(value, str):
        return redact_credentials(value)
    return value


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
                        "content": _sanitise(parsed),
                    }
                )

        payload = {"files": files}
        text = json.dumps(payload, indent=2, sort_keys=True)
        # No post-dump redaction: values redacted in-place during _sanitise.
        (output_dir / "config-summary.json").write_text(text + "\n", encoding="utf-8")
