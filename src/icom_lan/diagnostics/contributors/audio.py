"""Audio contributor — codec, channels, sample rate, devices, bridge state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from icom_lan.core.radio_protocol import AudioCapable
from icom_lan.diagnostics.redaction import redact_paths

if TYPE_CHECKING:
    from icom_lan.diagnostics.contributor import BundleContext


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _redact_device_name(name: str | None) -> str | None:
    """Redact OS-level device names that may embed usernames.

    macOS device names often embed the OS username (e.g.,
    ``"BlackHole 2ch (moroz's Mac)"``); apply :func:`redact_paths`
    to also catch ``/Users/<name>`` if present.
    """
    if name is None:
        return None
    return redact_paths(name)


class AudioContributor:
    """Emits ``audio/audio.json`` with codec, channels, sample rate, devices."""

    name = "audio"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        radio = ctx.radio
        payload: dict[str, Any]
        if radio is None or not isinstance(radio, AudioCapable):
            payload = {
                "available": False,
                "note": "no radio or radio is not AudioCapable",
            }
        else:
            payload = {
                "available": True,
                "codec": str(_safe_attr(radio, "audio_codec") or "unknown"),
                "sample_rate_hz": _safe_attr(radio, "audio_sample_rate"),
                "channels": _safe_attr(radio, "audio_channels"),
                "rx_device": _redact_device_name(_safe_attr(radio, "audio_rx_device")),
                "tx_device": _redact_device_name(_safe_attr(radio, "audio_tx_device")),
                "bridge_active": bool(_safe_attr(radio, "audio_bridge_active", False)),
            }
        text = json.dumps(payload, indent=2, sort_keys=True)
        (output_dir / "audio.json").write_text(text + "\n", encoding="utf-8")
