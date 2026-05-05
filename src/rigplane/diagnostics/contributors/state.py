"""State contributor — current radio state snapshot (freq/mode/meters)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from icom_lan.diagnostics.redaction import redact_paths

if TYPE_CHECKING:
    from icom_lan.diagnostics.contributor import BundleContext


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_paths(value)
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


class StateContributor:
    """Emits ``state/state.json`` with current radio state."""

    name = "state"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        radio = ctx.radio
        if radio is None:
            payload: dict[str, Any] = {
                "available": False,
                "note": "ctx.radio is None — invocation context has no live session",
            }
        else:
            # Prefer a state-snapshot method if present; else read individual fields.
            snapshot: dict[str, Any] | None = None
            snapshot_method = _safe_attr(radio, "state_snapshot")
            if callable(snapshot_method):
                try:
                    candidate = snapshot_method()
                    if isinstance(candidate, dict):
                        snapshot = candidate
                except Exception:
                    snapshot = None
            if snapshot is None:
                snapshot = {
                    "freq_hz": _safe_attr(radio, "freq_hz")
                    or _safe_attr(radio, "frequency"),
                    "mode": _redact(_safe_attr(radio, "mode")),
                    "vfo": _redact(
                        _safe_attr(radio, "active_vfo") or _safe_attr(radio, "vfo")
                    ),
                    "meters": _safe_attr(radio, "meters"),
                }
            else:
                # Walk dict/list returned by state_snapshot() and redact strings.
                snapshot = _redact(snapshot)
            payload = {
                "available": True,
                "state": snapshot,
            }
        text = json.dumps(payload, indent=2, sort_keys=True, default=str)
        (output_dir / "state.json").write_text(text + "\n", encoding="utf-8")
