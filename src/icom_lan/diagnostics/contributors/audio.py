"""Audio contributor — codec, channels, sample rate, devices, bridge state."""

from __future__ import annotations

import json
import re
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


# macOS device labels embed the local account name in parens, e.g.
# ``"BlackHole 2ch (moroz's Mac)"`` or ``"... (Alice's MacBook Pro)"``.
# Match the wrapping ``(... 's <Mac variant> ...)`` and replace with a
# stable redacted token. The non-greedy class avoids spanning closing parens.
_MACOS_USER_LABEL = re.compile(
    r"\([^()]*?'s\s+(?:Mac|MacBook|iMac|Mini|Pro)\b[^()]*?\)",
    re.IGNORECASE,
)


def _redact_device_name(name: str | None) -> str | None:
    """Redact OS-level device names that may embed usernames.

    macOS device names often embed the OS username — both as path-shaped
    strings (``/Users/<name>``) and as freeform parenthetical labels
    (``"(<name>'s Mac)"``). Apply both scrubbers so neither form leaks.
    """
    if name is None:
        return None
    name = redact_paths(name)
    name = _MACOS_USER_LABEL.sub("(<USER>'s Mac)", name)
    return name


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
