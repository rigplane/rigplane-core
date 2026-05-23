"""Serial port enumeration, candidate filtering, and multi-protocol radio discovery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from rigplane.usb_audio_resolve import AudioDeviceMapping, resolve_audio_for_serial_port

__all__ = [
    "build_setup_discovery_payload",
    "CivProbeResult",
    "RadioDiscoveryResult",
    "SerialPortCandidate",
    "dedupe_radios",
    "discover_lan_radios",
    "discover_serial_radios",
    "enumerate_serial_ports",
    "probe_serial_civ",
    "probe_serial_kenwood_cat",
    "probe_serial_yaesu_cat",
]

_CIV_PROBE_CMD = bytes([0xFE, 0xFE, 0x00, 0xE0, 0x19, 0x00, 0xFD])
# Minimum valid response: FE FE E0 <addr> 19 00 <model_id_byte> FD = 8 bytes
_RESPONSE_MIN_LEN = 8

logger = logging.getLogger(__name__)

_VIRTUAL_KEYWORDS = ("debug", "wlan", "spi")
_BLUETOOTH_KEYWORDS = ("bluetooth",)


@dataclass
class SerialPortCandidate:
    """A USB serial port that may be connected to an Icom radio.

    Attributes:
        device: OS device path, e.g. ``/dev/ttyUSB0``.
        description: Human-readable description from the OS.
        hwid: Hardware ID string (USB VID:PID), or ``None`` if unavailable.
    """

    device: str
    description: str
    hwid: str | None
    vid: int | None = None
    pid: int | None = None
    manufacturer: str | None = None
    product: str | None = None
    serial_number: str | None = None


@dataclass
class RadioDiscoveryResult:
    """Result of a successful multi-protocol serial radio probe.

    Attributes:
        port: OS device path, e.g. ``/dev/ttyUSB0``.
        protocol: Protocol detected: ``"civ"``, ``"yaesu_cat"``, or ``"kenwood_cat"``.
        model: Human-readable model name, e.g. ``"IC-7610"`` or ``"FTX-1"``.
        profile_id: Rig profile identifier, e.g. ``"icom_ic7610"`` or ``"yaesu_ftx1"``.
        baudrate: Baud rate at which the radio was detected.
        address: CI-V address (``int``) for Icom radios; CAT model ID string for others.
        description: Human-readable OS serial port description, if available.
        hwid: OS hardware ID string, if available.
        vid: USB vendor ID from pySerial, if available.
        pid: USB product ID from pySerial, if available.
        manufacturer: USB manufacturer string from pySerial, if available.
        product: USB product string from pySerial, if available.
        serial_number: USB serial number from pySerial, if available.
        usb_audio: Optional USB audio resolution metadata. Keys match
            :class:`rigplane.usb_audio_resolve.AudioDeviceMapping` field names
            when topology resolution is available.
    """

    port: str
    protocol: str
    model: str
    profile_id: str
    baudrate: int
    address: int | str
    description: str | None = None
    hwid: str | None = None
    usb_audio: dict[str, Any] | None = None
    vid: int | None = None
    pid: int | None = None
    manufacturer: str | None = None
    product: str | None = None
    serial_number: str | None = None


@dataclass
class CivProbeResult:
    """Result of a successful CI-V probe on a serial port.

    Attributes:
        port: OS device path, e.g. ``/dev/ttyUSB0``.
        baud: Baud rate at which the radio responded.
        address: CI-V address reported by the radio (e.g. ``0x98`` for IC-7610).
        model_id: Model ID bytes from the transceiver ID response.
    """

    port: str
    baud: int
    address: int
    model_id: bytes


# Default open function; replaced in tests via _open_serial parameter.
_OpenSerial = Callable[..., Awaitable[tuple[Any, Any]]]


async def probe_serial_civ(
    port: str,
    baud_rates: list[int] | None = None,
    timeout: float = 1.0,
    *,
    _open_serial: _OpenSerial | None = None,
) -> CivProbeResult | None:
    """Probe a serial port for a CI-V radio, trying multiple baud rates.

    Sends a *Read Transceiver ID* broadcast (``FE FE 00 E0 19 00 FD``) at each
    baud rate and waits up to *timeout* seconds for a valid response.

    Args:
        port: Serial device path (e.g. ``/dev/ttyUSB0``).
        baud_rates: Baud rates to try, in order. Defaults to
            ``[19200, 9600, 115200, 4800]``.
        timeout: Per-baud timeout in seconds.
        _open_serial: Override for ``serial_asyncio.open_serial_connection``
            (used in tests).

    Returns:
        :class:`CivProbeResult` on success, or ``None`` if no radio responded.
    """
    if baud_rates is None:
        baud_rates = [19200, 9600, 115200, 4800]

    for baud in baud_rates:
        result = await _try_baud(port, baud, timeout, _open_serial=_open_serial)
        if result is not None:
            return result
    return None


async def _try_baud(
    port: str,
    baud: int,
    timeout: float,
    *,
    _open_serial: _OpenSerial | None = None,
) -> CivProbeResult | None:
    """Open *port* at *baud*, send CI-V probe, return result or None.

    Args:
        port: Serial device path.
        baud: Baud rate to attempt.
        timeout: Read timeout in seconds.
        _open_serial: Override for ``serial_asyncio.open_serial_connection``.

    Returns:
        :class:`CivProbeResult` on success, or ``None`` on timeout / bad data.
    """
    open_fn = _open_serial or _default_open_serial()
    try:
        reader, writer = await open_fn(url=port, baudrate=baud)
    except Exception:
        logger.debug("probe_serial_civ: cannot open %s @ %d", port, baud)
        return None

    try:
        writer.write(_CIV_PROBE_CMD)
        await writer.drain()
        logger.debug("probe_serial_civ: sent probe to %s @ %d baud", port, baud)

        # Read in a loop: first read often returns only the echo of our
        # command; the actual radio response arrives a few ms later.
        buf = bytearray()
        response_preamble = bytes([0xFE, 0xFE, 0xE0])
        try:
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    chunk = await asyncio.wait_for(reader.read(64), timeout=remaining)
                    buf.extend(chunk)
                except asyncio.TimeoutError:
                    break
                # Check if we have the response (not just echo)
                if buf.find(response_preamble) != -1:
                    break
        except asyncio.TimeoutError:
            pass

        if not buf:
            logger.debug("probe_serial_civ: timeout at %s @ %d", port, baud)
            return None

        return _parse_probe_response(port, baud, bytes(buf))
    finally:
        writer.close()
        await writer.wait_closed()


def _default_open_serial() -> _OpenSerial:
    """Return the real serial_asyncio opener, or raise ImportError with hint."""
    from rigplane._optional_deps import _require_pyserial_asyncio

    _require_pyserial_asyncio()
    import serial_asyncio  # type: ignore[import-untyped]

    return serial_asyncio.open_serial_connection  # type: ignore[no-any-return]


def _parse_probe_response(port: str, baud: int, data: bytes) -> CivProbeResult | None:
    """Parse raw bytes looking for a CI-V transceiver ID response.

    Expected frame: ``FE FE E0 <addr> 19 00 <model_id...> FD``

    Args:
        port: Serial device path (for building result).
        baud: Baud rate (for building result).
        data: Raw bytes received from the serial port.

    Returns:
        :class:`CivProbeResult` if a valid response is found, else ``None``.
    """
    # Scan for response preamble FE FE E0 (to=controller, ignoring echo)
    search = bytes([0xFE, 0xFE, 0xE0])
    idx = data.find(search)
    if idx == -1:
        logger.debug("probe_serial_civ: no valid preamble in response from %s", port)
        return None

    frame = data[idx:]
    if len(frame) < _RESPONSE_MIN_LEN:
        logger.debug("probe_serial_civ: response too short from %s", port)
        return None

    # Verify command echo: bytes 4-5 should be 0x19 0x00
    if frame[4] != 0x19 or frame[5] != 0x00:
        logger.debug(
            "probe_serial_civ: unexpected command bytes in response from %s", port
        )
        return None

    address = frame[3]

    # model_id is everything between byte 6 and the terminator FD
    end_idx = frame.find(0xFD, 6)
    if end_idx == -1:
        logger.debug("probe_serial_civ: no terminator in response from %s", port)
        return None

    model_id = bytes(frame[6:end_idx])
    logger.info(
        "probe_serial_civ: found radio at %s @ %d — addr=0x%02X model=%s",
        port,
        baud,
        address,
        model_id.hex(),
    )
    return CivProbeResult(port=port, baud=baud, address=address, model_id=model_id)


# ---------------------------------------------------------------------------
# Yaesu CAT probe
# ---------------------------------------------------------------------------

#: Mapping from 4-digit hex model ID string to (model_name, profile_id).
_YAESU_CAT_MODEL_MAP: dict[str, tuple[str, str]] = {
    "0840": ("FTX-1", "yaesu_ftx1"),
}

_YAESU_CAT_PROBE_BAUDS = [38400, 9600, 115200]
_KENWOOD_CAT_PROBE_BAUDS = [9600, 38400, 115200]

_YaesuTransportFactory = Callable[..., Any]


def _default_yaesu_transport_factory() -> _YaesuTransportFactory:
    """Return YaesuCatTransport class, or raise ImportError with hint."""
    from .yaesu_cat.transport import YaesuCatTransport

    return cast(_YaesuTransportFactory, YaesuCatTransport)


async def probe_serial_yaesu_cat(
    port: str,
    baud_rates: list[int] | None = None,
    timeout: float = 0.5,
    *,
    _transport_factory: _YaesuTransportFactory | None = None,
) -> RadioDiscoveryResult | None:
    """Probe a serial port for a Yaesu CAT radio, trying multiple baud rates.

    Sends ``ID;`` at each baud rate and parses the ``ID<model_id>;`` response.

    Args:
        port: Serial device path (e.g. ``/dev/ttyUSB0``).
        baud_rates: Baud rates to try, in order. Defaults to
            ``[38400, 9600, 115200]``.
        timeout: Per-baud timeout in seconds.
        _transport_factory: Override for ``YaesuCatTransport`` constructor
            (used in tests).

    Returns:
        :class:`RadioDiscoveryResult` on success, or ``None`` if no Yaesu radio responded.
    """
    if baud_rates is None:
        baud_rates = _YAESU_CAT_PROBE_BAUDS

    try:
        factory = _transport_factory or _default_yaesu_transport_factory()
    except ImportError:
        logger.debug("probe_serial_yaesu_cat: Yaesu CAT backend not available")
        return None

    for baud in baud_rates:
        result = await _try_yaesu_baud(port, baud, timeout, factory)
        if result is not None:
            return result
    return None


async def _try_yaesu_baud(
    port: str,
    baud: int,
    timeout: float,
    factory: _YaesuTransportFactory,
) -> RadioDiscoveryResult | None:
    """Open *port* at *baud* via Yaesu CAT, send ``ID;``, return result or None."""
    transport = factory(
        device=port, baudrate=baud, timeout=timeout, echo_suppression=True
    )
    try:
        await transport.connect()
    except Exception:
        logger.debug("probe_serial_yaesu_cat: cannot open %s @ %d", port, baud)
        return None

    try:
        response = await transport.query("ID;", timeout=timeout)
        return _parse_yaesu_id_response(port, baud, response)
    except Exception:
        logger.debug("probe_serial_yaesu_cat: timeout/error at %s @ %d", port, baud)
        return None
    finally:
        try:
            await transport.close()
        except Exception:
            pass


def _parse_yaesu_id_response(
    port: str, baud: int, response: str
) -> RadioDiscoveryResult | None:
    """Parse Yaesu CAT ``ID;`` response into a :class:`RadioDiscoveryResult`.

    Expected response (semicolon already stripped by transport): ``ID0840``

    Args:
        port: Serial device path (for building result).
        baud: Baud rate (for building result).
        response: Response string with trailing ``;`` stripped.

    Returns:
        :class:`RadioDiscoveryResult` if response is valid, else ``None``.
    """
    if not response.startswith("ID") or len(response) != 6:
        logger.debug(
            "probe_serial_yaesu_cat: unexpected ID response %r from %s", response, port
        )
        return None

    model_id_str = response[2:]  # "0840"
    entry = _YAESU_CAT_MODEL_MAP.get(model_id_str)
    if entry is None:
        model_name = f"Yaesu ({model_id_str})"
        profile_id = ""
    else:
        model_name, profile_id = entry

    logger.info(
        "probe_serial_yaesu_cat: found radio at %s @ %d — model=%s id=%s",
        port,
        baud,
        model_name,
        model_id_str,
    )
    return RadioDiscoveryResult(
        port=port,
        protocol="yaesu_cat",
        model=model_name,
        profile_id=profile_id,
        baudrate=baud,
        address=model_id_str,
    )


# ---------------------------------------------------------------------------
# Kenwood CAT probe (future use)
# ---------------------------------------------------------------------------


async def probe_serial_kenwood_cat(
    port: str,
    baud_rates: list[int] | None = None,
    timeout: float = 0.5,
    *,
    _transport_factory: Any | None = None,
) -> RadioDiscoveryResult | None:
    """Probe a serial port for a Kenwood CAT radio (reserved for future use).

    Args:
        port: Serial device path.
        baud_rates: Baud rates to try (currently unused).
        timeout: Per-baud timeout in seconds (currently unused).
        _transport_factory: Transport factory override (currently unused).

    Returns:
        Always ``None`` until Kenwood CAT transport is implemented.
    """
    return None


def enumerate_serial_ports() -> list[SerialPortCandidate]:
    """Enumerate candidate USB serial ports for Icom radios.

    Returns:
        List of :class:`SerialPortCandidate` objects for ports that pass the
        candidate filter (USB, non-Bluetooth, non-virtual).
    """
    from serial.tools import list_ports

    candidates = []
    for port in list_ports.comports():
        if _is_candidate(port):
            candidates.append(
                SerialPortCandidate(
                    device=port.device,
                    description=port.description,
                    hwid=port.hwid,
                    vid=getattr(port, "vid", None),
                    pid=getattr(port, "pid", None),
                    manufacturer=getattr(port, "manufacturer", None),
                    product=getattr(port, "product", None),
                    serial_number=getattr(port, "serial_number", None),
                )
            )
            logger.debug("Serial candidate: %s (%s)", port.device, port.description)
        else:
            logger.debug("Skipping port: %s (%s)", port.device, port.description)
    return candidates


async def discover_lan_radios(timeout: float = 3.0) -> list[dict[str, object]]:
    """Discover Icom radios on LAN via UDP broadcast ("Are You There").

    Args:
        timeout: How long to listen for responses, in seconds.

    Returns:
        List of dicts with keys ``host`` and ``remote_id``.
    """
    import socket
    import struct
    import time

    def _scan() -> list[dict[str, object]]:
        import random

        # Build "Are You There" packet with random sender_id
        # IC-7610 ignores packets with sender_id=0
        pkt = bytearray(0x10)
        struct.pack_into("<I", pkt, 0, 0x10)  # size
        struct.pack_into("<H", pkt, 4, 0x03)  # ARE_YOU_THERE
        struct.pack_into("<H", pkt, 6, 0)  # seq=0
        my_id = random.randint(1, 0xFFFFFFFF)
        struct.pack_into("<I", pkt, 8, my_id)  # sender_id (non-zero!)
        struct.pack_into("<I", pkt, 0x0C, 0)  # remote_id=0 (unknown)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.5)

        # Broadcast on common Icom ports
        for port in [50001]:
            try:
                sock.sendto(bytes(pkt), ("255.255.255.255", port))
            except OSError as exc:
                logger.warning("discover: broadcast failed on port %d: %s", port, exc)
                sock.close()
                return []

        found: dict[str, dict[str, object]] = {}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(256)
                if len(data) >= 0x10:
                    ptype = struct.unpack_from("<H", data, 4)[0]
                    if ptype == 0x04:  # I_AM_HERE
                        remote_id = struct.unpack_from("<I", data, 8)[0]
                        found[addr[0]] = {"host": addr[0], "remote_id": remote_id}
                        logger.info(
                            "discover_lan_radios: found %s id=0x%08X",
                            addr[0],
                            remote_id,
                        )
            except socket.timeout:
                continue

        sock.close()
        return list(found.values())

    return await asyncio.get_running_loop().run_in_executor(None, _scan)


async def discover_serial_radios(
    *,
    _open_serial: _OpenSerial | None = None,
    _yaesu_transport_factory: _YaesuTransportFactory | None = None,
) -> list[RadioDiscoveryResult]:
    """Discover radios connected via USB serial (CI-V, Yaesu CAT, Kenwood CAT).

    Probes each candidate port in sequence: CI-V → Yaesu CAT → Kenwood CAT.
    Stops after the first successful match for each port.

    Args:
        _open_serial: Override for ``serial_asyncio.open_serial_connection``
            passed through to :func:`probe_serial_civ` (used in tests).
        _yaesu_transport_factory: Override for ``YaesuCatTransport`` constructor
            passed through to :func:`probe_serial_yaesu_cat` (used in tests).

    Returns:
        List of :class:`RadioDiscoveryResult` for all detected radios.
    """
    from rigplane.radios import CIV_PROFILE_MAP, identify_radio

    candidates = enumerate_serial_ports()
    results: list[RadioDiscoveryResult] = []
    for port in candidates:
        # --- CI-V probe ---
        civ = await probe_serial_civ(port.device, _open_serial=_open_serial)
        if civ:
            model = identify_radio(civ.address, civ.model_id)
            profile_id = CIV_PROFILE_MAP.get(civ.address, "")
            results.append(
                RadioDiscoveryResult(
                    port=civ.port,
                    protocol="civ",
                    model=model,
                    profile_id=profile_id,
                    baudrate=civ.baud,
                    address=civ.address,
                    description=port.description,
                    hwid=port.hwid,
                    vid=port.vid,
                    pid=port.pid,
                    manufacturer=port.manufacturer,
                    product=port.product,
                    serial_number=port.serial_number,
                    usb_audio=_resolve_usb_audio_metadata(civ.port),
                )
            )
            continue

        # --- Yaesu CAT probe ---
        yaesu = await probe_serial_yaesu_cat(
            port.device,
            _transport_factory=_yaesu_transport_factory,
        )
        if yaesu:
            yaesu.description = port.description
            yaesu.hwid = port.hwid
            yaesu.vid = port.vid
            yaesu.pid = port.pid
            yaesu.manufacturer = port.manufacturer
            yaesu.product = port.product
            yaesu.serial_number = port.serial_number
            yaesu.usb_audio = _resolve_usb_audio_metadata(yaesu.port)
            results.append(yaesu)
            continue

        # --- Kenwood CAT probe ---
        kenwood = await probe_serial_kenwood_cat(port.device)
        if kenwood:
            kenwood.description = port.description
            kenwood.hwid = port.hwid
            kenwood.vid = port.vid
            kenwood.pid = port.pid
            kenwood.manufacturer = port.manufacturer
            kenwood.product = port.product
            kenwood.serial_number = port.serial_number
            kenwood.usb_audio = _resolve_usb_audio_metadata(kenwood.port)
            results.append(kenwood)

    return results


def _resolve_usb_audio_metadata(port: str) -> dict[str, object] | None:
    """Return optional USB audio mapping metadata without failing discovery."""
    mapping: AudioDeviceMapping | None
    try:
        mapping = resolve_audio_for_serial_port(port)
    except Exception as exc:
        logger.debug("discover_serial_radios: USB audio resolve failed: %s", exc)
        return None
    if mapping is None:
        return None
    return {
        "rx_device_index": mapping.rx_device_index,
        "tx_device_index": mapping.tx_device_index,
        "serial_port": mapping.serial_port,
        "location_prefix": mapping.location_prefix,
    }


def dedupe_radios(
    lan_radios: list[dict[str, Any]], serial_radios: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Group LAN and serial discovery results by radio identity.

    Two entries are considered the same radio when their ``model`` **and**
    ``address`` (CI-V address) fields match.  Entries that lack either field
    are treated as distinct radios and are never merged.

    Args:
        lan_radios: Dicts from :func:`discover_lan_radios`.
        serial_radios: Dicts from :func:`discover_serial_radios`.

    Returns:
        List of dicts, each with keys ``model``, ``lan`` (list), ``serial``
        (list).  ``model`` is the display name of the radio.
    """
    radios: dict[tuple[object, ...], dict[str, Any]] = {}
    _counter = 0

    for lan in lan_radios:
        model = lan.get("model")
        address = lan.get("address")
        if model is not None and address is not None:
            key: tuple[object, ...] = (model, address)
        else:
            _counter += 1
            key = (f"__unid_lan_{_counter}",)
        if key not in radios:
            radios[key] = {
                "model": model or lan.get("host", "Unknown"),
                "lan": [],
                "serial": [],
            }
        cast_list = radios[key]["lan"]
        assert isinstance(cast_list, list)
        cast_list.append(lan)

    for serial in serial_radios:
        model = serial.get("model")
        address = serial.get("address")
        if model is not None and address is not None:
            key = (model, address)
        else:
            _counter += 1
            key = (f"__unid_serial_{_counter}",)
        if key not in radios:
            radios[key] = {"model": model or "Unknown", "lan": [], "serial": []}
        cast_list = radios[key]["serial"]
        assert isinstance(cast_list, list)
        cast_list.append(serial)

    return list(radios.values())


def build_setup_discovery_payload(
    grouped_radios: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build stable discovery JSON for setup wizards and managed supervisors."""
    radios: list[dict[str, Any]] = []
    for index, radio in enumerate(grouped_radios, start=1):
        connections: list[dict[str, Any]] = []
        for lan in radio.get("lan", []):
            host = str(lan.get("host", ""))
            remote_id = lan.get("remote_id")
            connection: dict[str, Any] = {
                "type": "lan",
                "backend": "lan",
                "label": f"LAN {host}" if host else "LAN",
                "host": host,
                "remoteId": remote_id,
                "requiresCredentials": True,
            }
            connections.append(connection)

        for serial in radio.get("serial", []):
            port = str(serial.get("port", ""))
            baudrate = serial.get("baudrate", serial.get("baud"))
            protocol = serial.get("protocol", "civ")
            backend = "yaesu-cat" if protocol == "yaesu_cat" else "serial"
            connection = {
                "type": "serial",
                "backend": backend,
                "label": f"USB serial {port}" if port else "USB serial",
                "port": port,
                "protocol": protocol,
                "profileId": serial.get("profile_id"),
                "baudrate": baudrate,
                "address": serial.get("address"),
                "description": serial.get("description"),
                "hwid": serial.get("hwid"),
                "vid": serial.get("vid"),
                "pid": serial.get("pid"),
                "manufacturer": serial.get("manufacturer"),
                "product": serial.get("product"),
                "serialNumber": serial.get("serial_number"),
                "requiresCredentials": False,
            }
            usb_audio = _normalize_usb_audio_metadata(serial.get("usb_audio"))
            if usb_audio is not None:
                connection["usbAudio"] = usb_audio
            connections.append(connection)

        radios.append(
            {
                "id": f"radio-{index}",
                "model": radio.get("model", "Unknown"),
                "connections": connections,
            }
        )

    return {
        "schema": "rigplane.discovery.v1",
        "radios": radios,
        "limitations": {
            "macosUsbAudio": "coreaudio-device-selection",
            "windowsUsbAudio": "manual-device-selection",
            "linuxUsbAudio": "pipewire-or-pulseaudio-device-selection",
        },
    }


def _normalize_usb_audio_metadata(value: object) -> dict[str, object] | None:
    """Convert internal USB audio metadata into stable discovery JSON."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return None

    result: dict[str, object] = {}
    field_map = {
        "rx_device_index": "rxDeviceIndex",
        "tx_device_index": "txDeviceIndex",
        "serial_port": "serialPort",
        "location_prefix": "locationPrefix",
    }
    for source, target in field_map.items():
        if source in value:
            result[target] = value[source]
    return result or None


def _is_candidate(port: object) -> bool:
    """Return True if *port* is a plausible Icom serial connection.

    Inclusion criteria:
    - Device path contains ``"usb"`` (case-insensitive)

    Exclusion criteria:
    - Device path contains ``"bluetooth"`` (case-insensitive)
    - Device path contains ``"debug"``, ``"wlan"``, or ``"spi"`` (virtual ports)

    Args:
        port: A ``serial.tools.list_ports_common.ListPortInfo`` object.

    Returns:
        ``True`` if the port should be offered as a candidate.
    """
    device: str = getattr(port, "device", "")
    device_lower = device.lower()

    for kw in _BLUETOOTH_KEYWORDS:
        if kw in device_lower:
            return False

    for kw in _VIRTUAL_KEYWORDS:
        if kw in device_lower:
            return False

    return "usb" in device_lower
