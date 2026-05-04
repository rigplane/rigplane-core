"""Radio contributor — model, FW, backend, capabilities, audio codec; IPs masked."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from icom_lan.core.radio_protocol import (
    AudioCapable,
    RigctldRoutable,
    StatePollable,
    UsbAudioCapable,
)
from icom_lan.diagnostics.redaction import (
    redact_credentials,
    redact_hostnames,
    redact_ips,
    redact_paths,
)

if TYPE_CHECKING:
    from icom_lan.diagnostics.contributor import BundleContext


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _redact(s: str | None) -> str | None:
    if s is None:
        return None
    return redact_credentials(redact_ips(redact_paths(s)))


def _redact_host(s: str | None) -> str | None:
    """Host-field redactor: full chain plus hostname scrubbing.

    ``redact_hostnames`` is intentionally NOT in the generic ``_redact`` chain
    because it would over-redact common identifiers (``radio.json``,
    ``IC-7610.fw``, etc.) on other fields. Apply only where DNS-shape values
    are expected — the radio's ``host``/``_host`` connection field.
    """
    if s is None:
        return None
    return redact_credentials(redact_hostnames(redact_ips(redact_paths(s))))


def _capabilities(radio: Any) -> list[str]:
    caps: list[str] = []
    if isinstance(radio, AudioCapable):
        caps.append("AudioCapable")
    if isinstance(radio, StatePollable):
        caps.append("StatePollable")
    if isinstance(radio, RigctldRoutable):
        caps.append("RigctldRoutable")
    if isinstance(radio, UsbAudioCapable):
        caps.append("UsbAudioCapable")
    return caps


class RadioContributor:
    """Emits ``radio/radio.json`` with radio model, FW, backend, capabilities."""

    name = "radio"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        radio = ctx.radio
        if radio is None:
            payload: dict[str, Any] = {
                "available": False,
                "note": "ctx.radio is None — invocation context has no live session",
            }
        else:
            payload = {
                "available": True,
                "model": _redact(
                    _safe_attr(radio, "model") or _safe_attr(radio, "radio_model")
                ),
                "firmware_version": _redact(
                    _safe_attr(radio, "firmware_version")
                    or _safe_attr(radio, "fw_version")
                ),
                "backend": _redact(
                    _safe_attr(radio, "backend_id")
                    or _safe_attr(radio, "backend_name")
                    or radio.__class__.__name__
                ),
                "capabilities": _capabilities(radio),
                "audio_codec": str(_safe_attr(radio, "audio_codec") or "unknown"),
                # Live runtimes store connection info as ``_host``/``_port``
                # (private attrs). Try public first for stub-friendliness,
                # then fall back so an active LAN session reports its real
                # endpoint.
                "host": _redact_host(
                    _safe_attr(radio, "host") or _safe_attr(radio, "_host")
                ),
                "port": _safe_attr(radio, "port") or _safe_attr(radio, "_port"),
            }
        text = json.dumps(payload, indent=2, sort_keys=True)
        (output_dir / "radio.json").write_text(text + "\n", encoding="utf-8")
