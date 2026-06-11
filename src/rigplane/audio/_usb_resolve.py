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
- **Linux**: Full topology-based resolution via ``/sys`` sysfs traversal for
  both CDC-ACM (``ttyACM*``, e.g. the X6200's WCH CH342) and FTDI/CP210x
  (``ttyUSB*``) serial ports. The serial port and each ALSA card are resolved
  to their USB device node; the audio card sharing the **longest common
  USB-device path prefix** (same physical device or hub) with the serial port
  is the link, with ``idVendor``/``idProduct`` as a robust-identity tie-break
  (MOR-228).
- **Windows**: Topology resolution via USB PnP (MOR-229). The serial ``COMx``
  function and the USB Audio Class function of one physical radio share a
  parent USB composite-device instance path, which is the topology anchor
  (the analogue of the macOS hub prefix). When the parent link is unavailable,
  a VID:PID robust-identity fallback links serial↔audio. The OS enumeration is
  isolated behind an injectable ``pnp_query`` callable so the resolver is
  testable off-Windows. Audio→``sounddevice`` mapping reuses the MOR-230
  identity primitive (product name + same-name rank).

For hosts with multiple identical radios (same VID:PID, ambiguous topology),
capture the exact audio device names on hardware and set the ``[usb]`` override
in ``audio.toml`` (MOR-219).

**Algorithm (Linux — sysfs)**:

1. Serial port → USB device path: read ``<sysfs_root>/class/tty/<ttyXXX>/device``
   and ``realpath`` it into the USB tree (``.../usbN/N-x[.y]/...``). The USB
   *device* node is the deepest ancestor whose basename has no ``:`` (an
   interface node is ``N-x:c.i``; its parent device node is ``N-x``).
2. Each ALSA card → USB device path: enumerate
   ``<sysfs_root>/class/sound/card*/device`` and ``realpath`` likewise.
3. Link = the audio card whose USB device path shares the **longest common
   USB-device path prefix** with the serial port (same physical device/hub).
4. Map to ``sounddevice`` indices by **identity**: the ALSA card name (the USB
   ``product`` string) is the CoreAudio/PortAudio device-name key, matched
   against the :func:`_cluster_usb_audio_devices` clusters with same-name rank
   (shared with the macOS path, MOR-230).
5. Robust-identity fallback: ``idVendor``/``idProduct`` read from the USB node
   disambiguate when the topology prefix match is ambiguous.
"""

from __future__ import annotations

import logging
import platform
import re
import subprocess
from dataclasses import dataclass
from typing import Any

from rigplane.audio.backend import _ALSA_HW_RE

logger = logging.getLogger(__name__)

__all__ = [
    "AudioDeviceMapping",
    "WindowsPnpDevice",
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


@dataclass(frozen=True, slots=True)
class WindowsPnpDevice:
    """One USB PnP function enumerated by the Windows PnP/WMI subsystem.

    Each entry models a single child function of a USB composite device — the
    serial (CDC/COM) function or the USB Audio Class function. The
    ``parent_pnp_id`` is the shared composite-device instance path that links
    the two functions of one physical radio (the Windows analogue of the macOS
    USB hub prefix).

    Attributes:
        pnp_device_id: The function's own PnP instance path
            (e.g. ``USB\\VID_0D8C&PID_0012\\6&1a2b&0&1&0000``).
        parent_pnp_id: The parent composite-device instance path that the
            serial and audio functions share. May be empty if the PnP query
            could not resolve the parent (then VID:PID is the only link).
        vid: USB vendor id, 4-hex-digit string (e.g. ``"0D8C"``), or ``None``.
        pid: USB product id, 4-hex-digit string (e.g. ``"0012"``), or ``None``.
        com_port: The ``COMx`` name if this function is the serial port, else
            ``None``.
        audio_endpoint_name: The audio device/endpoint product name if this
            function is the USB Audio Class device, else ``None``. This is the
            string CoreAudio/WASAPI surfaces as the ``sounddevice`` device
            name, so it is the identity key for pairing.
    """

    pnp_device_id: str
    parent_pnp_id: str
    vid: str | None
    pid: str | None
    com_port: str | None
    audio_endpoint_name: str | None


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
    if platform.system() == "Linux":
        return _resolve_linux(serial_port, sounddevice_module=sounddevice_module)
    if platform.system() == "Windows":
        return _resolve_windows(serial_port, sounddevice_module=sounddevice_module)
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


def _resolve_windows(
    serial_port: str,
    *,
    sounddevice_module: object | None = None,
    pnp_query: object | None = None,
) -> AudioDeviceMapping | None:
    """Windows-specific resolution via USB PnP topology.

    Args:
        serial_port: The ``COMx`` name of the radio's CI-V serial port.
        sounddevice_module: Injected ``sounddevice`` (for testing).
        pnp_query: Injected zero-arg callable returning a list of
            :class:`WindowsPnpDevice` (for testing without a Windows host).
            Defaults to :func:`_query_windows_pnp_devices`, the real
            PowerShell/WMI enumeration. Isolating the OS call here keeps the
            resolver body testable on non-Windows hosts.

    Algorithm:
        1. Enumerate USB PnP functions via ``pnp_query`` (behind ``try/except``;
           headless-safe — any failure → ``None``, name-based fallback).
        2. Locate the serial function whose ``com_port`` matches ``serial_port``.
        3. Find the audio endpoint(s) **sharing the serial function's parent**
           USB composite device. If the parent link is unavailable (empty
           ``parent_pnp_id``), fall back to matching by shared **VID:PID**
           (robust-identity).
        4. Map the matched audio endpoint name to ``sounddevice`` indices by
           identity, reusing the MOR-230 cluster + same-name-rank primitive.

    Multi-radio caveat: when several identical radios share one host (same
    VID:PID, ambiguous parent), the VID:PID fallback cannot disambiguate them;
    set the ``[usb] rx_device``/``tx_device`` override in ``audio.toml``.
    """
    query: Any = pnp_query if pnp_query is not None else _query_windows_pnp_devices
    try:
        records = list(query())
    except Exception as exc:  # noqa: BLE001 — headless-safe: any failure → fallback
        logger.warning("usb-audio-resolve: Windows PnP query failed: %s", exc)
        return None

    if not records:
        logger.warning("usb-audio-resolve: Windows PnP query returned no devices")
        return None

    target = serial_port.strip().upper()
    serial_dev = next(
        (
            r
            for r in records
            if r.com_port is not None and r.com_port.strip().upper() == target
        ),
        None,
    )
    if serial_dev is None:
        logger.warning("usb-audio-resolve: no PnP serial function for %r", serial_port)
        return None

    # 1. Topology link: audio endpoints sharing the serial function's parent.
    audio_devs: list[WindowsPnpDevice] = []
    if serial_dev.parent_pnp_id:
        audio_devs = [
            r
            for r in records
            if r.audio_endpoint_name is not None
            and r.parent_pnp_id == serial_dev.parent_pnp_id
        ]
    # 2. Robust-identity fallback: link by shared VID:PID when topology is
    #    ambiguous (no parent or no co-parented audio function).
    if not audio_devs and serial_dev.vid is not None and serial_dev.pid is not None:
        audio_devs = [
            r
            for r in records
            if r.audio_endpoint_name is not None
            and r.vid == serial_dev.vid
            and r.pid == serial_dev.pid
        ]
        if audio_devs:
            logger.info(
                "usb-audio-resolve: %s linked to audio by VID:PID %s:%s "
                "(topology ambiguous — multi-radio hosts may need [usb] override)",
                serial_port,
                serial_dev.vid,
                serial_dev.pid,
            )

    if not audio_devs:
        logger.warning(
            "usb-audio-resolve: no audio endpoint shares parent/identity with %s",
            serial_port,
        )
        return None

    audio_dev = audio_devs[0]
    audio_name = audio_dev.audio_endpoint_name
    assert audio_name is not None  # narrowed by the filters above

    # Same-name rank: position of the matched endpoint among all same-named
    # audio endpoints (ordered by PnP instance path), mirroring the macOS
    # name + same-name-rank identity used by _pair_audio_device_for_location.
    same_name = sorted(
        (r for r in records if r.audio_endpoint_name == audio_name),
        key=lambda r: r.pnp_device_id,
    )
    same_name_rank = next(
        (
            i
            for i, r in enumerate(same_name)
            if r.pnp_device_id == audio_dev.pnp_device_id
        ),
        0,
    )

    # 3. Map to sounddevice indices by identity (reuses the MOR-230 cluster).
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
    pair = _pair_audio_cluster_by_name_rank(devices, audio_name, same_name_rank)
    if pair is None:
        logger.warning(
            "usb-audio-resolve: could not map audio endpoint %r (rank %d) to "
            "sounddevice indices for %s",
            audio_name,
            same_name_rank,
            serial_port,
        )
        return None

    rx_idx, tx_idx = pair
    logger.info(
        "usb-audio-resolve: %s → audio %r → RX device [%d], TX device [%d]",
        serial_port,
        audio_name,
        rx_idx,
        tx_idx,
    )
    return AudioDeviceMapping(
        rx_device_index=rx_idx,
        tx_device_index=tx_idx,
        serial_port=serial_port,
        location_prefix=None,
    )


def _query_windows_pnp_devices() -> list[WindowsPnpDevice]:
    """Enumerate USB PnP functions on a real Windows host (best-effort).

    Runs PowerShell ``Get-PnpDevice`` and joins each device with its parent
    instance path and VID:PID parsed from the PnP instance id, plus the COM
    port name (for serial functions) or the friendly audio endpoint name (for
    USB Audio Class functions). Returns ``[]`` on any failure — the caller
    treats an empty/raised result as "topology unavailable" and falls back to
    name-based selection. This keeps the base install free of a hard PnP/WMI
    dependency; the heavy lifting is shelled out to PowerShell, which ships
    with Windows.

    NOTE: This function is only ever executed on Windows. It is intentionally
    NOT exercised by the unit tests (which inject a fake ``pnp_query``); its
    exact field shape MUST be validated against a real Windows host + X6200
    before relying on the topology path in production (see PR checklist).
    """
    if platform.system() != "Windows":
        return []
    # PowerShell one-liner: emit one CSV-ish line per USB PnP device with the
    # fields we need. We parse the parent and VID:PID from the InstanceId.
    script = (
        "Get-PnpDevice -PresentOnly | "
        "ForEach-Object { "
        "$id = $_.InstanceId; "
        "$parent = (Get-PnpDeviceProperty -InstanceId $id "
        "-KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data; "
        "$friendly = $_.FriendlyName; "
        "'{0}`t{1}`t{2}`t{3}' -f $id, $parent, $_.Class, $friendly }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=20,
        )
        if result.returncode != 0:
            logger.debug(
                "usb-audio-resolve: Get-PnpDevice exited %d", result.returncode
            )
            return []
        text = result.stdout.decode("utf-8", errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("usb-audio-resolve: PowerShell PnP query failed: %s", exc)
        return []

    devices: list[WindowsPnpDevice] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        instance_id, parent, dev_class, friendly = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
            parts[3].strip(),
        )
        vid, pid = _parse_vid_pid(instance_id)
        com_port = _parse_com_port(friendly)
        # Audio Class functions report Class in {AudioEndpoint, MEDIA}; the
        # friendly name is the endpoint product name.
        is_audio = dev_class.upper() in {
            "AUDIOENDPOINT",
            "MEDIA",
        } or _is_usb_audio_codec(friendly)
        audio_name = friendly if (is_audio and com_port is None) else None
        if com_port is None and audio_name is None:
            continue
        devices.append(
            WindowsPnpDevice(
                pnp_device_id=instance_id,
                parent_pnp_id=parent,
                vid=vid,
                pid=pid,
                com_port=com_port,
                audio_endpoint_name=audio_name,
            )
        )
    return devices


def _parse_vid_pid(instance_id: str) -> tuple[str | None, str | None]:
    """Extract ``(VID, PID)`` 4-hex-digit strings from a PnP instance id."""
    vid_m = re.search(r"VID_([0-9A-Fa-f]{4})", instance_id)
    pid_m = re.search(r"PID_([0-9A-Fa-f]{4})", instance_id)
    vid = vid_m.group(1).upper() if vid_m else None
    pid = pid_m.group(1).upper() if pid_m else None
    return vid, pid


def _parse_com_port(friendly_name: str) -> str | None:
    """Extract a ``COMx`` port name from a PnP friendly name (e.g. ``... (COM3)``)."""
    m = re.search(r"\b(COM\d+)\b", friendly_name)
    return m.group(1) if m else None


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

    # Rank the matched card among USB-backed cards carrying the same name, in
    # sysfs card order — mirrors the macOS same-name rank semantics so
    # _cluster_usb_audio_devices can be shared unchanged (MOR-230).
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
    ``ttyUSB*`` for FTDI/CP210x. This is the Linux counterpart of the macOS
    :func:`_extract_tty_suffix`; it is deliberately separate because the sysfs
    lookup keys on the full kernel name (``ttyACM0``), not a chip-serial suffix.

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


def _common_usb_path_score(serial_dev: str, card_dev: str) -> int:
    """Score the shared USB-device path prefix length of two device nodes.

    Splits each device's bus-port path (e.g. ``1-1.4`` → ``["1", "1", "4"]``)
    and counts matching leading components. A higher score means the two
    devices sit deeper on the same physical hub branch; an equal full path
    means they are the *same* USB device (composite radio).
    """
    a = _usb_path_components(serial_dev)
    b = _usb_path_components(card_dev)
    score = 0
    for x, y in zip(a, b):
        if x != y:
            break
        score += 1
    return score


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

    This is the platform-neutral tail of :func:`_pair_audio_device_for_location`,
    factored out so the Windows and Linux resolvers can reuse the exact
    cluster-and-rank selection. They derive the identity key (product / ALSA
    card name + same-name rank) from PnP records and sysfs respectively, rather
    than from IORegistry ``locationID``s; the macOS path continues to derive the
    name+rank from ``locationID``-sorted entries.

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
    return _pair_audio_cluster_by_name_rank(devices, matched_name, same_name_rank)


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
