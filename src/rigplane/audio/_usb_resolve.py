"""Resolve serial USB audio through shared pairing on macOS and Windows."""

from __future__ import annotations

import logging
import platform
import re
from dataclasses import dataclass
from typing import Any, Sequence

from rigplane.audio._usb_pairing import (
    WindowsAudioAmbiguityError,
    WindowsAudioEndpointNameError,
    WindowsAudioTopologyError,
)
from rigplane.audio.backend import _ALSA_HW_RE
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
    """Result of USB audio device resolution.

    Attributes:
        rx_device_index: ``sounddevice`` index for the RX (input/capture) device.
        tx_device_index: ``sounddevice`` index for the TX (output/playback) device.
        serial_port: The serial port path that was resolved.
        location_prefix: A platform-specific USB-topology diagnostic value,
            not used to select the match: a synthesized bus/port identity on
            Linux (see :func:`_usb_path_location_prefix`), or ``None`` otherwise.
    """

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
    """Resolve USB Audio input/output device indices for a serial CI-V port.

    Args:
        serial_port: Path to the serial port (e.g. ``/dev/cu.usbserial-201410``).
        sounddevice_module: Optional injected ``sounddevice`` module (for testing).

    Returns:
        An :class:`AudioDeviceMapping` with RX/TX device indices, or ``None``
        if resolution failed (no matching audio devices found).
    """
    if platform.system() == "Linux":
        return _resolve_linux(serial_port, sounddevice_module=sounddevice_module)
    if platform.system() in {"Darwin", "Windows"}:
        from ._portaudio_enum import read_portaudio_enumeration
        from ._usb_adapt_macos import adapt_macos
        from ._usb_adapt_windows import adapt_windows
        from ._usb_pairing import PairRefused, pair, pairing_refusal_error
        from ._usb_raw_macos import RawMacos, read_raw_macos
        from ._usb_raw_windows import read_raw_windows

        macos = platform.system() == "Darwin"
        try:
            raw = read_raw_macos(timeout_s=5.0) if macos else read_raw_windows(5.0)
        except Exception:
            logger.warning("usb-audio-resolve: USB topology unavailable", exc_info=True)
            if not macos:
                return None
            raw = RawMacos(unavailable=({"part": "macos", "reason": "reader failed"},))
        if not isinstance(raw, RawMacos) and not raw.devnodes:
            return None
        sd: Any = sounddevice_module
        if sd is None:
            try:
                import sounddevice as sd_mod
            except ImportError:
                return None
            sd = sd_mod
        enumeration = read_portaudio_enumeration(sd)
        snapshot = (
            adapt_macos(raw, enumeration)
            if isinstance(raw, RawMacos)
            else adapt_windows(
                {
                    "raw": {
                        "windows": raw.to_json_dict(),
                        "serial_ports": [],
                        "portaudio": enumeration.to_json_dict(),
                    }
                }
            )
        )
        result = pair(snapshot, serial_port, usb_identity)
        if isinstance(result, PairRefused):
            raise pairing_refusal_error(result)
        return AudioDeviceMapping(result.rx_index, result.tx_index, serial_port)
    logger.info(
        "usb-audio-resolve: platform %s — topology resolution not supported, "
        "falling back to name-based selection",
        platform.system(),
    )
    return None


# ---------------------------------------------------------------------------
# Linux — sysfs topology resolution (MOR-228)
# ---------------------------------------------------------------------------


def _resolve_linux(
    serial_port: str,
    *,
    sounddevice_module: object | None = None,
    sysfs_root: str = "/sys",
) -> AudioDeviceMapping | None:
    """Linux-specific resolution via the ``/sys`` (sysfs) USB topology.

    Args:
        serial_port: Serial port path (``/dev/ttyACM0``, ``/dev/ttyUSB0``).
        sounddevice_module: Injected ``sounddevice`` module (for testing).
        sysfs_root: Root of the sysfs tree. Injectable so tests can point at a
            fixture directory; defaults to the real ``/sys``.

    Algorithm: resolve the serial port and every ALSA card to their USB device
    node under sysfs, pick the audio card sharing the longest common
    USB-device path prefix with the serial port, then map that card's name to
    ``sounddevice`` indices by identity (name + same-name rank).
    """
    # 1. Serial port → USB device node.
    tty_name = _extract_linux_tty_name(serial_port)
    if tty_name is None:
        logger.warning(
            "usb-audio-resolve: cannot extract Linux tty name from %r", serial_port
        )
        return None

    serial_dev = _sysfs_usb_device_for_tty(sysfs_root, tty_name)
    if serial_dev is None:
        logger.warning(
            "usb-audio-resolve: no USB device node for tty %r under %s",
            tty_name,
            sysfs_root,
        )
        return None

    # 2. Each ALSA card → USB device node.
    cards = _sysfs_usb_audio_cards(sysfs_root)
    if not cards:
        logger.warning(
            "usb-audio-resolve: no USB-backed ALSA cards found under %s", sysfs_root
        )
        return None

    # 3. Link = audio card with the longest common USB-device path prefix.
    best: tuple[str, str] | None = None  # (card_dev, card_name)
    best_score = -1
    serial_ids = _read_usb_ids(serial_dev)
    for card_dev, card_name in cards:
        score = _common_usb_path_score(serial_dev, card_dev)
        # Robust-identity tie-break: a card on the SAME physical device as the
        # serial port (shared idVendor:idProduct) wins ties. This disambiguates
        # composite radios where audio + CAT live on one USB device.
        if serial_ids is not None and _read_usb_ids(card_dev) == serial_ids:
            score += 1
        if score > best_score:
            best_score = score
            best = (card_dev, card_name)

    if best is None or best_score <= 0:
        logger.warning(
            "usb-audio-resolve: no ALSA card shares a USB path with %s (serial %s)",
            serial_port,
            serial_dev,
        )
        return None

    matched_card_dev, matched_card_name = best

    # 4. Map the matched card to sounddevice indices by identity.
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

    # Rank the matched card among same-named cards in sysfs order.
    same_name_rank = sum(
        1
        for c_dev, c_name in cards
        if c_name == matched_card_name and c_dev < matched_card_dev
    )
    pair = _pair_audio_cluster_by_name_rank(devices, matched_card_name, same_name_rank)
    if pair is None:
        logger.warning(
            "usb-audio-resolve: could not pair sounddevice cluster for ALSA card "
            "%r rank %d (cards=%s)",
            matched_card_name,
            same_name_rank,
            [(n, d) for d, n in cards],
        )
        return None

    rx_idx, tx_idx = pair

    vid_pid = _read_usb_ids(matched_card_dev)
    location_prefix = _usb_path_location_prefix(matched_card_dev)
    logger.info(
        "usb-audio-resolve: %s → ALSA card %r (%s) → RX device [%d], TX device [%d]",
        serial_port,
        matched_card_name,
        f"{vid_pid[0]}:{vid_pid[1]}" if vid_pid else "vid:pid unknown",
        rx_idx,
        tx_idx,
    )

    return AudioDeviceMapping(
        rx_device_index=rx_idx,
        tx_device_index=tx_idx,
        serial_port=serial_port,
        location_prefix=location_prefix,
    )


def _extract_linux_tty_name(serial_port: str) -> str | None:
    """Extract the Linux tty kernel name from a serial port path.

    Recognises both CDC-ACM (``ttyACM*``) and USB-serial (``ttyUSB*``) device
    nodes. These are the two enumeration schemes Linux uses for USB CI-V
    bridges: ``ttyACM*`` for CDC-ACM composite bridges (the X6200's WCH CH342),
    ``ttyUSB*`` for FTDI/CP210x. The sysfs lookup keys on the full kernel name.

    >>> _extract_linux_tty_name("/dev/ttyACM0")
    'ttyACM0'
    >>> _extract_linux_tty_name("/dev/ttyUSB1")
    'ttyUSB1'
    >>> _extract_linux_tty_name("/dev/cu.usbserial-201410") is None
    True
    """
    m = re.search(r"(tty(?:ACM|USB)\d+)", serial_port)
    if m:
        return m.group(1)
    return None


def _usb_device_node_from_realpath(real: str) -> str | None:
    """Walk up a realpath'd sysfs path to the nearest USB *device* node.

    A USB device node basename looks like ``1-1`` or ``1-1.4`` (bus-port[.port]
    with no colon). A USB *interface* node basename looks like ``1-1:1.0``
    (device``:``config.interface). ``<...>/device`` symlinks for tty and sound
    classes point at an interface node, so we ascend until the basename has no
    ``:`` and matches the device-node shape, returning that absolute path.

    Returns ``None`` if no USB device node is found in the ancestry.
    """
    parts = real.split("/")
    # Walk from the deepest component upward.
    for i in range(len(parts), 0, -1):
        base = parts[i - 1]
        if ":" in base:
            continue
        if re.fullmatch(r"\d+-\d+(?:\.\d+)*", base):
            return "/".join(parts[:i])
    return None


def _sysfs_usb_device_for_tty(sysfs_root: str, tty_name: str) -> str | None:
    """Resolve ``<sysfs_root>/class/tty/<tty_name>/device`` to its USB node."""
    import os

    link = os.path.join(sysfs_root, "class", "tty", tty_name, "device")
    try:
        real = os.path.realpath(link)
    except OSError:
        return None
    if not os.path.exists(real):
        return None
    return _usb_device_node_from_realpath(real)


def _sysfs_usb_audio_cards(sysfs_root: str) -> list[tuple[str, str]]:
    """Enumerate USB-backed ALSA cards as ``(usb_device_path, card_name)``.

    For each ``<sysfs_root>/class/sound/card*`` whose ``device`` symlink lands
    in the USB tree, return the USB device node path and the card's product
    name (the USB ``product`` string, which is the PortAudio/ALSA device-name
    identity key). Sorted by USB device path for deterministic same-name rank.
    """
    import glob
    import os

    cards: list[tuple[str, str]] = []
    pattern = os.path.join(sysfs_root, "class", "sound", "card*")
    for card_dir in sorted(glob.glob(pattern)):
        if not re.fullmatch(r"card\d+", os.path.basename(card_dir)):
            continue
        link = os.path.join(card_dir, "device")
        try:
            real = os.path.realpath(link)
        except OSError:
            continue
        if not os.path.exists(real):
            continue
        usb_dev = _usb_device_node_from_realpath(real)
        if usb_dev is None:
            continue
        name = _read_sysfs_str(os.path.join(usb_dev, "product"))
        if name is None:
            name = os.path.basename(card_dir)
        cards.append((usb_dev, name))
    return sorted(cards, key=lambda c: c[0])


def _common_prefix_score(a: Sequence[Any], b: Sequence[Any]) -> int:
    """Count the shared leading elements of two sequences."""
    score = 0
    for x, y in zip(a, b):
        if x != y:
            break
        score += 1
    return score


def _common_usb_path_score(serial_dev: str, card_dev: str) -> int:
    """Score the shared USB-device path prefix length of two device nodes.

    Splits each device's bus-port path (e.g. ``1-1.4`` → ``["1", "1", "4"]``)
    and counts matching leading components. A higher score means the two
    devices sit deeper on the same physical hub branch; an equal full path
    means they are the *same* USB device (composite radio).
    """
    return _common_prefix_score(
        _usb_path_components(serial_dev), _usb_path_components(card_dev)
    )


def _usb_path_components(usb_dev: str) -> list[str]:
    """Split a USB device node basename into bus/port components.

    ``.../usb1/1-1.4`` → ``["1", "1", "4"]`` (bus, then each port hop).
    """
    import os

    base = os.path.basename(usb_dev)
    m = re.fullmatch(r"(\d+)-(\d+(?:\.\d+)*)", base)
    if not m:
        return []
    bus = m.group(1)
    ports = m.group(2).split(".")
    return [bus, *ports]


def _usb_path_location_prefix(usb_dev: str) -> int | None:
    """Derive an integer ``location_prefix`` from a USB device path.

    There is no macOS-style ``locationID`` on Linux; we synthesise a stable
    small integer from the bus and root-port hops (``bus<<8 | first_port``) so
    AudioDeviceMapping carries a sensible, comparable hub identity. Returns
    ``None`` if the path cannot be parsed.
    """
    comps = _usb_path_components(usb_dev)
    if len(comps) < 2:
        return None
    try:
        bus = int(comps[0])
        root_port = int(comps[1])
    except ValueError:
        return None
    return (bus << 8) | root_port


def _read_usb_ids(usb_dev: str) -> tuple[str, str] | None:
    """Read ``(idVendor, idProduct)`` from a USB device node, lowercased."""
    import os

    vid = _read_sysfs_str(os.path.join(usb_dev, "idVendor"))
    pid = _read_sysfs_str(os.path.join(usb_dev, "idProduct"))
    if vid is None or pid is None:
        return None
    return vid.lower(), pid.lower()


def _read_sysfs_str(path: str) -> str | None:
    """Read and strip a sysfs attribute file, or ``None`` on failure/empty."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            value = fh.read().strip()
    except OSError:
        return None
    return value or None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Trailing ALSA decoration PortAudio appends on Linux: " (hw:X,Y)" (or
# "(plughw:X,Y)"). Anchored variant of backend._ALSA_HW_RE for suffix-stripping.
_ALSA_NAME_SUFFIX_RE = re.compile(rf"\s*\({_ALSA_HW_RE.pattern}\)\s*$")


def _normalize_alsa_device_name(name: str) -> str:
    """Strip PortAudio's ALSA decoration down to the bare card name (MOR-549).

    On Linux, ``sounddevice.query_devices()`` reports ALSA devices as
    ``"<card>: <pcm> (hw:X,Y)"`` (e.g. ``"USB Audio CODEC: Audio (hw:2,0)"``),
    while the sysfs ``product`` attribute — the topology identity key used by
    :func:`_resolve_linux` — is just ``"<card>"`` (``"USB Audio CODEC"``).
    Normalising the sounddevice side makes the exact-identity match work.

    Conservative by design: a name without an ``(hw:X,Y)`` suffix is returned
    unchanged (macOS/Windows names, plain test names, names containing a colon
    for other reasons). The ``": <pcm>"`` tail is only stripped when the ALSA
    suffix was present, so legitimate names are never over-stripped.
    """
    m = _ALSA_NAME_SUFFIX_RE.search(name)
    if m is None:
        return name
    base = name[: m.start()]
    head, sep, _ = base.partition(":")
    return head.strip() if sep else base.strip()


def _pair_audio_cluster_by_name_rank(
    devices: list[Any],
    audio_name: str,
    same_name_rank: int,
) -> tuple[int, int] | None:
    """Select ``(rx_index, tx_index)`` for the rank-th same-named audio cluster.

    Returns ``None`` when no same-named cluster occupies ``same_name_rank`` or
    the matched cluster is missing an RX or TX index.
    """
    clusters = _cluster_usb_audio_devices(devices)
    # Normalise the sounddevice-side name: on Linux/ALSA it carries a
    # ": <pcm> (hw:X,Y)" decoration absent from the sysfs product string
    # (MOR-549). A no-op for macOS/Windows names.
    same_name_clusters = [
        c for c in clusters if _normalize_alsa_device_name(c[0]) == audio_name
    ]
    if same_name_rank >= len(same_name_clusters):
        logger.warning(
            "usb-audio-resolve: %r rank %d out of range (%d matching clusters)",
            audio_name,
            same_name_rank,
            len(same_name_clusters),
        )
        return None
    _name, rx, tx = same_name_clusters[same_name_rank]
    if rx is None or tx is None:
        logger.warning(
            "usb-audio-resolve: incomplete audio cluster for %r rank %d (rx=%r, tx=%r)",
            audio_name,
            same_name_rank,
            rx,
            tx,
        )
        return None
    return rx, tx


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
    key used by the Linux resolver.
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
