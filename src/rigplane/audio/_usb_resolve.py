"""Resolve USB Audio devices associated with a serial CI-V port.

When multiple Icom radios are connected via USB, each exposes identical
"USB Audio CODEC" devices (Burr-Brown/TI USB Audio Class 1.0).  This module
maps a serial port (e.g. ``/dev/cu.usbserial-201410``) to the correct
audio input/output device indices by correlating USB topology information.

**Algorithm (macOS — IORegistry)**:

1. Parse the serial port's TTY suffix → find its ``locationID`` in IORegistry.
2. Extract the upper 16 bits of ``locationID`` as the USB hub prefix.
3. Find all USB audio devices (``USB Audio CODEC``, ``USB Audio Device``) in
   IORegistry with their ``locationID``.
4. Match audio devices sharing the same hub prefix as the serial port.
5. Map matched locations to ``sounddevice`` device indices by positional order.

**Fallback**: When IORegistry is unavailable (Linux, or ``ioreg`` missing),
falls back to name-based matching (existing ``UsbAudioDriver`` behavior).

Platform support:
- **macOS**: Full topology-based resolution via ``/usr/sbin/ioreg``.
- **Linux**: Future — ``/sys/bus/usb/devices/`` sysfs traversal (not yet implemented).
- **Windows**: Not supported (no USB topology introspection planned).
"""

from __future__ import annotations

import logging
import platform
import re
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "AudioDeviceMapping",
    "resolve_audio_for_serial_port",
]


@dataclass(frozen=True, slots=True)
class AudioDeviceMapping:
    """Result of USB audio device resolution.

    Attributes:
        rx_device_index: ``sounddevice`` index for the RX (input/capture) device.
        tx_device_index: ``sounddevice`` index for the TX (output/playback) device.
        serial_port: The serial port path that was resolved.
        location_prefix: The USB hub prefix (upper 16 bits of locationID) used
            for matching, or ``None`` if resolution used a fallback method.
    """

    rx_device_index: int
    tx_device_index: int
    serial_port: str
    location_prefix: int | None = None


def resolve_audio_for_serial_port(
    serial_port: str,
    *,
    sounddevice_module: object | None = None,
) -> AudioDeviceMapping | None:
    """Resolve USB Audio input/output device indices for a serial CI-V port.

    Args:
        serial_port: Path to the serial port (e.g. ``/dev/cu.usbserial-201410``).
        sounddevice_module: Optional injected ``sounddevice`` module (for testing).

    Returns:
        An :class:`AudioDeviceMapping` with RX/TX device indices, or ``None``
        if resolution failed (no matching audio devices found).
    """
    if platform.system() == "Darwin":
        return _resolve_macos(serial_port, sounddevice_module=sounddevice_module)
    # Future: Linux sysfs resolution
    logger.info(
        "usb-audio-resolve: platform %s — topology resolution not supported, "
        "falling back to name-based selection",
        platform.system(),
    )
    return None


def _resolve_macos(
    serial_port: str,
    *,
    sounddevice_module: object | None = None,
    ioreg_output: str | None = None,
) -> AudioDeviceMapping | None:
    """macOS-specific resolution via IORegistry.

    Args:
        serial_port: Serial port path.
        sounddevice_module: Injected sounddevice (for testing).
        ioreg_output: Pre-captured ioreg output (for testing without hardware).
    """
    # 1. Extract TTY suffix from port path
    tty_suffix = _extract_tty_suffix(serial_port)
    if tty_suffix is None:
        logger.warning(
            "usb-audio-resolve: cannot extract TTY suffix from %r", serial_port
        )
        return None

    # 2. Get IORegistry data (cached single call)
    ioreg_text = ioreg_output or _run_ioreg()
    if not ioreg_text:
        logger.warning("usb-audio-resolve: ioreg returned no output")
        return None

    # 3. Find serial port's locationID
    serial_location = _find_serial_location(ioreg_text, tty_suffix)
    if serial_location is None:
        logger.warning(
            "usb-audio-resolve: no locationID found for TTY suffix %r", tty_suffix
        )
        return None

    serial_prefix = serial_location >> 16

    # 4. Find all USB Audio CODEC locationIDs
    audio_locations = _find_audio_codec_locations(ioreg_text)
    if not audio_locations:
        logger.warning("usb-audio-resolve: no USB Audio CODEC devices found in ioreg")
        return None

    # 5. Check if any audio device shares our hub prefix
    matching = [loc for loc in audio_locations if (loc >> 16) == serial_prefix]
    if not matching:
        logger.warning(
            "usb-audio-resolve: no audio devices match prefix %#06x for %s",
            serial_prefix,
            serial_port,
        )
        return None

    # 6. Get sounddevice indices
    sd: Any = sounddevice_module
    if sd is None:
        try:
            import sounddevice as sd_mod

            sd = sd_mod
        except ImportError:
            logger.warning(
                "usb-audio-resolve: sounddevice not available, cannot map indices"
            )
            return None

    devices = list(sd.query_devices())

    # Collect all USB Audio CODEC input/output device indices (ordered)
    usb_inputs: list[int] = []
    usb_outputs: list[int] = []
    for idx, dev in enumerate(devices):
        if _is_usb_audio_codec(dev.get("name", "")):
            if _safe_int(dev.get("max_input_channels")) > 0:
                usb_inputs.append(idx)
            if _safe_int(dev.get("max_output_channels")) > 0:
                usb_outputs.append(idx)

    # 7. Determine positional index: sorted unique prefixes → pair index
    unique_prefixes = sorted(set(loc >> 16 for loc in audio_locations))
    try:
        pair_idx = unique_prefixes.index(serial_prefix)
    except ValueError:
        logger.warning(
            "usb-audio-resolve: prefix %#06x not in audio prefixes %s",
            serial_prefix,
            [f"{p:#06x}" for p in unique_prefixes],
        )
        return None

    if pair_idx >= len(usb_inputs) or pair_idx >= len(usb_outputs):
        logger.warning(
            "usb-audio-resolve: pair index %d out of range (inputs=%d, outputs=%d)",
            pair_idx,
            len(usb_inputs),
            len(usb_outputs),
        )
        return None

    rx_idx = usb_inputs[pair_idx]
    tx_idx = usb_outputs[pair_idx]

    logger.info(
        "usb-audio-resolve: %s → prefix %#06x → RX device [%d], TX device [%d]",
        serial_port,
        serial_prefix,
        rx_idx,
        tx_idx,
    )

    return AudioDeviceMapping(
        rx_device_index=rx_idx,
        tx_device_index=tx_idx,
        serial_port=serial_port,
        location_prefix=serial_prefix,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_tty_suffix(serial_port: str) -> str | None:
    """Extract TTY suffix from a serial port path.

    Handles macOS (``/dev/cu.usbserial-XXXXXX``) and Linux
    (``/dev/ttyUSBX``) formats.

    >>> _extract_tty_suffix("/dev/cu.usbserial-201410")
    '201410'
    >>> _extract_tty_suffix("/dev/tty.usbserial-201410")
    '201410'
    """
    m = re.search(r"usbserial-(\w+)", serial_port)
    if m:
        return m.group(1)
    return None


def _run_ioreg() -> str | None:
    """Run ``/usr/sbin/ioreg -l`` and return stdout, or ``None`` on failure."""
    try:
        result = subprocess.run(
            ["/usr/sbin/ioreg", "-l"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            # ioreg may contain non-UTF-8 bytes (e.g. firmware blobs),
            # decode leniently to avoid UnicodeDecodeError.
            return result.stdout.decode("utf-8", errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("usb-audio-resolve: ioreg failed: %s", exc)
    return None


def _find_serial_location(ioreg_text: str, tty_suffix: str) -> int | None:
    """Find the ``locationID`` for a serial port identified by its TTY suffix.

    Searches backward from the ``IOTTYSuffix`` line to find the nearest
    ``locationID`` in the IORegistry tree.
    """
    lines = ioreg_text.split("\n")
    for i, line in enumerate(lines):
        if f'"IOTTYSuffix" = "{tty_suffix}"' in line:
            # Search backward for the nearest locationID
            for j in range(i, max(i - 80, 0), -1):
                m = re.search(r'"locationID"\s*=\s*(\d+)', lines[j])
                if m:
                    return int(m.group(1))
    return None


def _find_audio_codec_locations(ioreg_text: str) -> list[int]:
    """Find all ``locationID`` values for USB audio devices.

    Parses IORegistry tree node addresses to extract the location encoded
    in the node path.  Supports multiple naming conventions:

    - ``USB Audio CODEC@...`` — Icom (Burr-Brown/TI chip)
    - ``USB Audio Device@...`` — Yaesu FTX-1 and similar
    """
    locations: list[int] = []
    for m in re.finditer(r"USB Audio (?:CODEC|Device)@([0-9a-fA-F]+)", ioreg_text):
        locations.append(int(m.group(1), 16))
    return sorted(locations)


def _is_usb_audio_codec(name: str) -> bool:
    """Check if a device name matches a USB audio device pattern.

    Uses a broad match to support Icom, Yaesu, and Kenwood radios that
    expose USB Audio Class devices.
    """
    lowered = name.lower()
    return any(
        p in lowered
        for p in ("usb audio codec", "usb audio device", "yaesu", "kenwood")
    )


def _safe_int(value: object, default: int = 0) -> int:
    """Safely convert a value to int."""
    try:
        if isinstance(value, (int, float, str, bytes, bytearray)):
            return int(value)
        return default
    except (TypeError, ValueError):
        return default
