"""Invocation diagnostic contributor — ``sys.argv`` (filtered) and env (allowlist)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from icom_lan.diagnostics.redaction import redact_credentials, redact_paths

if TYPE_CHECKING:
    from icom_lan.diagnostics.contributor import BundleContext


# Allowlisted environment variables — only these are recorded in the bundle.
# Anything not on this list is silently dropped (avoid leaking arbitrary
# user environment, secrets, ssh agent paths, etc.).
_ENV_ALLOWLIST: tuple[str, ...] = (
    "ICOM_LAN_DISABLE_DIAGNOSTIC_LOGGING",
    "ICOM_LAN_REPORT_ENDPOINT",
    "ICOM_LAN_LOG_DIR",
    "PATH",
    "PYTHONPATH",
    "LANG",
    "LC_ALL",
    "TZ",
)

# Cap the number of PATH entries we serialise — full PATH on a developer
# machine is noisy and may leak install layout.
_PATH_ENTRY_LIMIT = 5


def _redact_string(s: str) -> str:
    """Apply redactors to a single plain-text value before JSON serialisation.

    Redacting per-value avoids the ``\\S+`` greediness pitfall that would
    otherwise consume JSON structural characters (closing quote, comma,
    newline) when applied to an already-serialised JSON document.
    """
    return redact_credentials(redact_paths(s))


def _filtered_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for key in _ENV_ALLOWLIST:
        value = os.environ.get(key)
        if value is None:
            continue
        if key == "PATH":
            # Redact each segment individually before re-joining: if we joined
            # first, ``redact_paths``'s lookbehind ``(?<![/:\w])`` would skip
            # every segment after the first ``:`` separator and leak
            # ``/Users/<name>`` paths past the first entry.
            entries = value.split(os.pathsep)[:_PATH_ENTRY_LIMIT]
            out[key] = os.pathsep.join(_redact_string(entry) for entry in entries)
        else:
            out[key] = _redact_string(value)
    return out


def _filtered_argv() -> list[str]:
    if not sys.argv:
        return []
    argv0 = Path(sys.argv[0]).name if sys.argv[0] else ""
    return [_redact_string(s) for s in [argv0, *sys.argv[1:]]]


class InvocationContributor:
    """Emits ``invocation/invocation.json`` with argv (filtered) and env (allowlisted)."""

    name = "invocation"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        payload = {
            "argv": _filtered_argv(),
            "env": _filtered_env(),
        }
        text = json.dumps(payload, indent=2, sort_keys=True)
        (output_dir / "invocation.json").write_text(text + "\n", encoding="utf-8")
