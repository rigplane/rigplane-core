"""Resolve USB Audio devices associated with a serial CI-V port.

When multiple USB radios are connected, each exposes a USB Audio Class
device (Icom: "USB Audio CODEC"; Yaesu: "USB Audio Device"; Xiegu X6200:
a C-Media "USB Audio Device"). This module maps a serial CI-V port
(e.g. ``/dev/cu.usbserial-201410`` or the X6200's ``/dev/cu.usbmodemXXXXX``)
to the correct audio input/output device indices by correlating USB
topology information — anchoring on the physical hub the port sits on
rather than fragile, collision-prone device-name strings.

**Algorithm (macOS — IORegistry)**:

1. Parse the serial port's TTY suffix → find its ``locationID`` in IORegistry.
   Both ``usbserial-XXXX`` (FTDI/CP210x) and ``usbmodemXXXX`` (CDC-ACM, e.g.
   the X6200's WCH CH342 bridge) suffixes are recognised.
2. Extract the upper 16 bits of ``locationID`` as the USB hub prefix.
3. Find all USB audio devices (``USB Audio CODEC``, ``USB Audio Device``) in
   IORegistry with their ``locationID``.
4. Match audio devices sharing the same hub prefix as the serial port.
5. Map the matched audio device to ``sounddevice`` indices by **identity**:
   group the enumerated USB-audio entries into per-device clusters (a duplex
   entry stands alone; an output-only entry adjacent to a same-named
   input-only entry forms one split-pair cluster), then select the cluster
   whose product name and same-name rank match the IORegistry device. This
   survives mixed-vendor / mixed-shape sets (a single duplex C-Media device
   next to a split-pair Icom CODEC), where a flat positional index over
   inputs/outputs would desync (MOR-230).

**Fallback**: When IORegistry is unavailable, ``ioreg`` is missing, or the
platform is not macOS, falls back to name-based matching (see
:func:`_is_usb_audio_codec` and ``usb_driver._USB_NAME_PATTERNS``). Name
matching recognises the C-Media identity used by the X6200 so single-device
setups resolve even without topology.

Platform support:
- **macOS**: Full topology-based resolution via ``/usr/sbin/ioreg`` for both
  FTDI/CP210x and CDC-ACM (``usbmodem``) serial ports.
- **Linux**: Topology resolution not yet implemented (``/sys`` sysfs traversal
  is future work); relies on name-based / robust-identity fallback and the
  optional ``[usb] rx_device``/``tx_device`` override escape hatch.
- **Windows**: Topology resolution not implemented; same fallback path.

For Linux/Windows multi-radio disambiguation, capture the exact audio device
names on hardware and set the ``[usb]`` override in ``audio.toml`` (MOR-219).
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

    # 4. Find all USB Audio CODEC (name, locationID) pairs
    audio_entries = _find_audio_codec_entries(ioreg_text)
    if not audio_entries:
        logger.warning("usb-audio-resolve: no USB Audio CODEC devices found in ioreg")
        return None

    # 5. Check if any audio device shares our hub prefix
    matching = [loc for _name, loc in audio_entries if (loc >> 16) == serial_prefix]
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

    # 7. Pair by identity, not positional order across flat input/output lists.
    pair = _pair_audio_device_for_location(devices, audio_entries, serial_location)
    if pair is None:
        logger.warning(
            "usb-audio-resolve: could not pair an audio device for prefix %#06x "
            "(serial loc %#010x, audio entries %s)",
            serial_prefix,
            serial_location,
            [(n, f"{loc:#010x}") for n, loc in audio_entries],
        )
        return None

    rx_idx, tx_idx = pair

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


def _cluster_usb_audio_devices(
    devices: list[Any],
) -> list[tuple[str, int | None, int | None]]:
    """Group enumerated USB-audio entries into per-physical-device clusters.

    Each cluster is a ``(name, rx_index, tx_index)`` tuple describing one
    physical USB audio device's CoreAudio product name and its
    capture/playback ``sounddevice`` indices:

    - A **duplex** entry (both input and output channels) is a self-contained
      cluster — the X6200's C-Media codec, which CoreAudio surfaces as one
      bidirectional device.
    - A **split** device (Icom "USB Audio CODEC") surfaces as two adjacent
      same-named entries — one output-only and one input-only — which are
      merged into a single cluster. Adjacency in the ``sounddevice``
      enumeration mirrors USB topology order, so the two halves of one
      physical device sit next to each other.

    Clusters are returned in enumeration order. The ``name`` is the identity
    key that :func:`_pair_audio_device_for_location` matches against the
    IORegistry product name. Identity-based, not positional across flattened
    input/output lists.
    """
    clusters: list[tuple[str, int | None, int | None]] = []
    # pending split cluster: (name, rx_index|None, tx_index|None)
    pending: tuple[str, int | None, int | None] | None = None

    for idx, dev in enumerate(devices):
        name = dev.get("name", "")
        if not _is_usb_audio_codec(name):
            continue
        has_in = _safe_int(dev.get("max_input_channels")) > 0
        has_out = _safe_int(dev.get("max_output_channels")) > 0

        if has_in and has_out:
            # Flush any half-open split cluster before the duplex device.
            if pending is not None:
                clusters.append(pending)
                pending = None
            clusters.append((name, idx, idx))
            continue

        rx = idx if has_in else None
        tx = idx if has_out else None
        if pending is None:
            pending = (name, rx, tx)
            continue
        # Merge with the open split cluster when it is the same device name
        # and fills the missing half.
        pend_name, pend_rx, pend_tx = pending
        if pend_name == name and (
            (rx is not None and pend_rx is None) or (tx is not None and pend_tx is None)
        ):
            # Coalesce the two halves with explicit None checks: a device
            # index of 0 is valid and falsy, so `pend_rx or rx` would wrongly
            # drop it (e.g. a USB codec enumerating at index 0 on a headless
            # host with no built-in audio). MOR-230.
            merged_rx = pend_rx if pend_rx is not None else rx
            merged_tx = pend_tx if pend_tx is not None else tx
            clusters.append((pend_name, merged_rx, merged_tx))
            pending = None
        else:
            clusters.append(pending)
            pending = (name, rx, tx)

    if pending is not None:
        clusters.append(pending)

    return clusters


def _pair_audio_device_for_location(
    devices: list[Any],
    audio_entries: list[tuple[str, int]],
    serial_location: int,
) -> tuple[int, int] | None:
    """Select the ``(rx_index, tx_index)`` for the matched audio device.

    Pairing is by **identity**, not positional order across flattened
    input/output lists. The serial port's hub prefix selects the matched
    audio device (an IORegistry ``(product_name, locationID)`` entry); that
    device's identity is its product name plus its **rank among same-named
    audio devices** (sorted by ``locationID``). The corresponding
    ``sounddevice`` cluster (see :func:`_cluster_usb_audio_devices`) is the
    rank-th cluster carrying the same product name, and it yields the device
    indices.

    This is the reusable, platform-neutral pairing primitive the future
    Linux/Windows resolvers (MOR-228/229) can call once they enumerate audio
    ``(name, locationID)`` entries and ``sounddevice`` clusters.

    Why name + same-name rank, not a global location rank: ``sounddevice``
    exposes no ``locationID`` field, and CoreAudio does **not** enumerate USB
    audio devices in ``locationID`` order across vendors (a C-Media device
    may enumerate before an Icom CODEC with a lower ``locationID``). The
    CoreAudio product name *is* the IORegistry node name, so it is the
    strongest available identity link. Within one vendor/name the remaining
    ambiguity (e.g. two identical Icom CODECs) is resolved by enumeration
    order, which for same-model devices tracks ``locationID`` order — this
    preserves the validated homogeneous-multi-radio behaviour.

    Returns ``None`` when the serial port shares no audio hub prefix or when
    no same-named cluster occupies the matched device's rank.
    """
    serial_prefix = serial_location >> 16
    sorted_entries = sorted(audio_entries, key=lambda e: e[1])

    # The matched audio device shares the serial port's hub prefix.
    matched_idx = next(
        (
            i
            for i, (_n, loc) in enumerate(sorted_entries)
            if (loc >> 16) == serial_prefix
        ),
        None,
    )
    if matched_idx is None:
        return None
    matched_name, _matched_loc = sorted_entries[matched_idx]
    # Rank of the matched device among same-named audio devices.
    same_name_rank = sum(
        1 for _n, _loc in sorted_entries[:matched_idx] if _n == matched_name
    )

    clusters = _cluster_usb_audio_devices(devices)
    same_name_clusters = [c for c in clusters if c[0] == matched_name]
    if same_name_rank >= len(same_name_clusters):
        logger.warning(
            "usb-audio-resolve: %r rank %d out of range (%d matching clusters)",
            matched_name,
            same_name_rank,
            len(same_name_clusters),
        )
        return None

    _name, rx, tx = same_name_clusters[same_name_rank]
    if rx is None or tx is None:
        logger.warning(
            "usb-audio-resolve: incomplete audio cluster for %r rank %d (rx=%r, tx=%r)",
            matched_name,
            same_name_rank,
            rx,
            tx,
        )
        return None
    return rx, tx


def _extract_tty_suffix(serial_port: str) -> str | None:
    """Extract TTY suffix from a macOS serial port path.

    Handles both macOS USB serial enumeration schemes:

    - **FTDI / CP210x bridges** (``/dev/cu.usbserial-XXXXXX``) — the suffix
      follows a dash and is the chip serial number. Used by Icom CI-V
      cables (CP2102), Yaesu HRI, etc.
    - **CDC-ACM composite bridges** (``/dev/cu.usbmodemXXXXX``) — the suffix
      follows ``usbmodem`` directly (no dash) and is a location-derived id.
      Used by the WCH CH342 dual-serial bridge in the Xiegu X6200 (MOR-219).

    The matched suffix corresponds to the IORegistry ``IOTTYSuffix`` value,
    which is what :func:`_find_serial_location` correlates against.

    >>> _extract_tty_suffix("/dev/cu.usbserial-201410")
    '201410'
    >>> _extract_tty_suffix("/dev/tty.usbserial-201410")
    '201410'
    >>> _extract_tty_suffix("/dev/cu.usbmodem14201")
    '14201'
    >>> _extract_tty_suffix("/dev/tty.usbmodem1434203")
    '1434203'
    """
    m = re.search(r"usb(?:serial-|modem)(\w+)", serial_port)
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
    return sorted(loc for _name, loc in _find_audio_codec_entries(ioreg_text))


def _find_audio_codec_entries(ioreg_text: str) -> list[tuple[str, int]]:
    """Find ``(product_name, locationID)`` pairs for USB audio devices.

    The product name (``USB Audio CODEC`` / ``USB Audio Device``) is the same
    string CoreAudio reports as the ``sounddevice`` device name, so it is the
    identity key used to disambiguate mixed-vendor sets in
    :func:`_pair_audio_device_for_location` (MOR-230). Returned in ascending
    ``locationID`` order.
    """
    entries: list[tuple[str, int]] = []
    for m in re.finditer(r"(USB Audio (?:CODEC|Device))@([0-9a-fA-F]+)", ioreg_text):
        entries.append((m.group(1), int(m.group(2), 16)))
    return sorted(entries, key=lambda e: e[1])


def _is_usb_audio_codec(name: str) -> bool:
    """Check if a device name matches a USB audio device pattern.

    Uses a broad match to support radios that expose a USB Audio Class
    device under a vendor-specific or commodity name:

    - Icom (``USB Audio CODEC`` — Burr-Brown/TI),
    - Yaesu / Kenwood (vendor name in the device string),
    - Xiegu X6200 (``USB Audio Device`` — C-Media Electronics codec; the
      generic CMedia name is also matched as a robust-identity fallback on
      platforms without topology resolution, MOR-219).
    """
    lowered = name.lower()
    return any(
        p in lowered
        for p in (
            "usb audio codec",
            "usb audio device",
            "yaesu",
            "kenwood",
            "c-media",
            "cmedia",
        )
    )


def _safe_int(value: object, default: int = 0) -> int:
    """Safely convert a value to int."""
    try:
        if isinstance(value, (int, float, str, bytes, bytearray)):
            return int(value)
        return default
    except (TypeError, ValueError):
        return default
