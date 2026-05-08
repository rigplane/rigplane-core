"""Audio contributor — codec, channels, sample rate, devices, bridge state."""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rigplane.core.radio_protocol import AudioCapable
from rigplane.types import AudioCodec
from rigplane.diagnostics.redaction import redact_paths

if TYPE_CHECKING:
    from rigplane.diagnostics.contributor import BundleContext


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _diag_value(value: Any) -> Any:
    if isinstance(value, AudioCodec):
        return value.name
    if isinstance(value, Enum):
        return value.value
    return value


def _audio_leg(contract: Any, direction: str) -> dict[str, Any]:
    return {
        "codec": _diag_value(_safe_attr(contract, f"{direction}_codec")),
        "sample_rate_hz": _safe_attr(contract, f"{direction}_sample_rate_hz"),
        "channels": _safe_attr(contract, f"{direction}_channels"),
        "codec_source": _diag_value(_safe_attr(contract, f"{direction}_codec_source")),
        "sample_rate_source": _diag_value(
            _safe_attr(contract, f"{direction}_sample_rate_source")
        ),
    }


def _radio_native_contracts(radio: Any) -> dict[str, Any]:
    contracts: dict[str, Any] = {}
    request = _safe_attr(radio, "audio_stream_request")
    if request is not None:
        contracts["requested"] = {
            "rx": _audio_leg(request, "rx"),
            "tx": _audio_leg(request, "tx"),
        }
    effective = _safe_attr(radio, "audio_stream_contract")
    if effective is not None:
        contracts["effective"] = {
            "rx": _audio_leg(effective, "rx"),
            "tx": _audio_leg(effective, "tx"),
        }
        fallback_reason = _safe_attr(effective, "fallback_reason")
        if fallback_reason:
            contracts["effective"]["fallback_reason"] = fallback_reason
    return contracts


def _codec_name(value: Any) -> str:
    if isinstance(value, AudioCodec):
        return value.name
    if isinstance(value, str):
        return value
    return str(value or "unknown")


def _resolve_web_rx_codec(
    *,
    radio_codec: Any,
    transport: str | None,
    transcode_to_opus: bool | None,
) -> str:
    radio_codec_name = _codec_name(radio_codec)
    if radio_codec_name.startswith("OPUS_"):
        return "OPUS"
    if transport == "pcm":
        return "PCM16"
    if transport == "opus" and transcode_to_opus is not False:
        return "OPUS"
    if transport == "auto" and transcode_to_opus is True:
        return "OPUS"
    return "PCM16"


def _web_rx_policy(radio: Any) -> dict[str, Any] | None:
    contract = _safe_attr(radio, "audio_stream_contract")
    profile = _safe_attr(radio, "profile")
    transport = _safe_attr(profile, "browser_rx_transport") if profile else None
    transcode_to_opus = (
        _safe_attr(profile, "browser_rx_transcode_to_opus") if profile else None
    )
    if contract is None and transport is None and transcode_to_opus is None:
        return None

    codec = _resolve_web_rx_codec(
        radio_codec=_safe_attr(contract, "rx_codec", _safe_attr(radio, "audio_codec")),
        transport=transport,
        transcode_to_opus=transcode_to_opus,
    )
    sample_rate_hz = _safe_attr(
        contract, "rx_sample_rate_hz", _safe_attr(radio, "audio_sample_rate")
    )
    channels = _safe_attr(contract, "rx_channels", _safe_attr(radio, "audio_channels"))
    policy_source = (
        "profile-default"
        if transport is not None or transcode_to_opus is not None
        else "global-default"
    )
    return {
        "state": "configured-policy",
        "transport": transport or "auto",
        "transcode_to_opus": transcode_to_opus,
        "codec": codec,
        "sample_rate_hz": sample_rate_hz,
        "channels": channels,
        "codec_source": policy_source,
        "sample_rate_source": "radio-native-effective",
        "channels_source": "radio-native-effective",
    }


def _usb_audio_contract(radio: Any) -> dict[str, Any] | None:
    contract = _safe_attr(radio, "usb_audio_contract")
    to_dict = _safe_attr(contract, "to_dict")
    if callable(to_dict):
        data = to_dict()
        if isinstance(data, dict):
            for leg in ("rx", "tx"):
                stream = data.get(leg)
                if not isinstance(stream, dict):
                    continue
                device = stream.get("device")
                if not isinstance(device, dict):
                    continue
                name = device.get("name")
                if isinstance(name, str):
                    device["name"] = _redact_device_name(name)
                platform_uid = device.get("platform_uid")
                if isinstance(platform_uid, str):
                    device["platform_uid"] = redact_paths(platform_uid)
            return data
    return None


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
                "codec": _codec_name(_safe_attr(radio, "audio_codec")),
                "sample_rate_hz": _safe_attr(radio, "audio_sample_rate"),
                "channels": _safe_attr(radio, "audio_channels"),
                "rx_device": _redact_device_name(_safe_attr(radio, "audio_rx_device")),
                "tx_device": _redact_device_name(_safe_attr(radio, "audio_tx_device")),
                "bridge_active": bool(_safe_attr(radio, "audio_bridge_active", False)),
            }
            radio_native = _radio_native_contracts(radio)
            if radio_native:
                payload["radio_native"] = radio_native
            web_rx = _web_rx_policy(radio)
            if web_rx is not None:
                payload["web_rx"] = web_rx
            usb_audio = _usb_audio_contract(radio)
            if usb_audio is not None:
                payload["usb_audio"] = usb_audio
        text = json.dumps(payload, indent=2, sort_keys=True)
        (output_dir / "audio.json").write_text(text + "\n", encoding="utf-8")
