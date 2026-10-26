"""Resolve serial USB audio through the shared USB pairing rule."""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass
from typing import Any

from rigplane.audio._usb_pairing import (
    WindowsAudioAmbiguityError,
    WindowsAudioEndpointNameError,
    WindowsAudioTopologyError,
)
from rigplane.core.usb_identity import UsbAudioIdentity

logger = logging.getLogger(__name__)

__all__ = [
    "AudioDeviceMapping",
    "WindowsAudioAmbiguityError",
    "WindowsAudioEndpointNameError",
    "WindowsAudioTopologyError",
    "resolve_audio_for_serial_port",
]


@dataclass(frozen=True, slots=True)
class AudioDeviceMapping:
    """PortAudio indices; location_prefix is retained for caller compatibility."""

    rx_device_index: int
    tx_device_index: int
    serial_port: str
    location_prefix: int | None = None


def resolve_audio_for_serial_port(
    serial_port: str,
    *,
    sounddevice_module: object | None = None,
    usb_identity: UsbAudioIdentity | None = None,
) -> AudioDeviceMapping | None:
    """Resolve a serial USB pair, raising a typed refusal when pairing fails."""
    system = platform.system()
    if system in {"Darwin", "Linux", "Windows"}:
        from ._portaudio_enum import read_portaudio_enumeration
        from ._usb_adapt_linux import adapt_linux
        from ._usb_adapt_macos import adapt_macos
        from ._usb_adapt_windows import adapt_windows
        from ._usb_pairing import PairRefused, pair, pairing_refusal_error
        from ._usb_raw_linux import RawLinux, read_raw_linux
        from ._usb_raw_macos import RawMacos, read_raw_macos
        from ._usb_raw_windows import RawWindows, read_raw_windows

        raw: RawLinux | RawMacos | RawWindows
        try:
            if system == "Linux":
                raw = read_raw_linux(timeout_s=5.0)
            elif system == "Darwin":
                raw = read_raw_macos(timeout_s=5.0)
            else:
                raw = read_raw_windows(5.0)
        except Exception:
            logger.warning("usb-audio-resolve: USB topology unavailable", exc_info=True)
            if system == "Windows":
                return None
            failure = ({"part": system.lower(), "reason": "reader failed"},)
            raw = (
                RawLinux(unavailable=failure)
                if system == "Linux"
                else RawMacos(unavailable=failure)
            )
        if isinstance(raw, RawWindows) and not raw.devnodes:
            return None
        sd: Any = sounddevice_module
        if sd is None:
            try:
                import sounddevice as sd_mod
            except ImportError:
                return None
            sd = sd_mod
        enumeration = read_portaudio_enumeration(sd)
        if isinstance(raw, RawLinux):
            snapshot = adapt_linux(raw, enumeration)
        elif isinstance(raw, RawMacos):
            snapshot = adapt_macos(raw, enumeration)
        else:
            snapshot = adapt_windows(
                {
                    "raw": {
                        "windows": raw.to_json_dict(),
                        "serial_ports": [],
                        "portaudio": enumeration.to_json_dict(),
                    }
                }
            )
        result = pair(snapshot, serial_port, usb_identity)
        if isinstance(result, PairRefused):
            raise pairing_refusal_error(result)
        return AudioDeviceMapping(result.rx_index, result.tx_index, serial_port)
    logger.info("usb-audio-resolve: platform %s has no USB topology adapter", system)
    return None
