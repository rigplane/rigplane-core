"""Serial port enumeration, candidate filtering, and multi-protocol radio discovery."""

from __future__ import annotations

import asyncio
import errno
import logging
import struct
import threading
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, NotRequired, TypedDict, cast

from rigplane.core.serial_open import open_serial_port, serial_open_diagnostic
from rigplane.core.usb_identity import UsbAudioIdentity
from rigplane.profiles import get_radio_profile, preferred_serial_bauds
from rigplane.usb_audio_resolve import AudioDeviceMapping, resolve_audio_for_serial_port

from .hamlib_probe import (
    DiscoveryCandidate,
    DiscoveryEvidence,
    HamlibProbeTarget,
    ProbeAuditRecord,
    ProbeOptions,
    ProbeResult,
    probe_hamlib_rigctld_targets,
    rank_hamlib_probe,
)

__all__ = [
    "LAN_DISCOVERY_PORT",
    "build_hamlib_discovery_payload",
    "build_setup_discovery_payload",
    "CivProbeResult",
    "CivProbeDiagnostic",
    "DiscoveryCandidate",
    "DiscoveryEvidence",
    "HamlibProbeTarget",
    "ProbeAuditRecord",
    "ProbeOptions",
    "ProbeResult",
    "RadioDiscoveryResult",
    "RadioDiscoveryResultDict",
    "GroupedRadio",
    "SerialPortCandidate",
    "SetupLanConnection",
    "SetupSerialConnection",
    "dedupe_radios",
    "discover_lan_radios",
    "discover_serial_radios",
    "enumerate_serial_ports",
    "probe_hamlib_rigctld_targets",
    "probe_lan_radio",
    "probe_serial_civ",
    "probe_serial_kenwood_cat",
    "probe_serial_yaesu_cat",
    "probe_xiegu_model_id",
    "rank_hamlib_probe",
    "resolve_civ_radio_identity",
]

#: UDP port the LAN "Are You There" probe is broadcast to: the Icom control
#: port. A radio found by :func:`discover_lan_radios` answered on it, so
#: ``discover`` pins it in the commands it prints (MOR-3859).
LAN_DISCOVERY_PORT = 50001

#: Re-send the LAN "Are You There" probe periodically across the listen
#: window so a single dropped UDP packet (or brief control-port contention)
#: does not hide a radio for the entire window; shared by the broadcast scan
#: (:func:`discover_lan_radios`) and the unicast probe
#: (:func:`probe_lan_radio`).
_LAN_RESEND_INTERVAL = 0.6
#: Per-socket receive poll. Polling N sockets costs up to N × 50 ms per
#: loop iteration, so a scan may overshoot its deadline by that much.
_LAN_RECV_POLL_INTERVAL = 0.05

#: OS errno values that mean "the probed host or network cannot be reached
#: at all": no route, host/link down, or the ICMP port unreachable. Windows
#: reports the same situations as WSA* errnos and as
#: ``ConnectionRefusedError`` / ``ConnectionResetError``, which
#: ``_is_unreachable_error`` matches by type.
_PROBE_UNREACHABLE_ERRNOS = frozenset(
    getattr(errno, name)
    for name in (
        "EHOSTDOWN",
        "EHOSTUNREACH",
        "ENETUNREACH",
        "ECONNREFUSED",
        "WSAEHOSTDOWN",
        "WSAEHOSTUNREACH",
        "WSAENETUNREACH",
        "WSAECONNREFUSED",
        "WSAECONNRESET",
    )
    if hasattr(errno, name)
)

_CIV_PROBE_CMD = bytes([0xFE, 0xFE, 0x00, 0xE0, 0x19, 0x00, 0xFD])
# Minimum valid response: FE FE E0 <addr> 19 00 <model_id_byte> FD = 8 bytes
_RESPONSE_MIN_LEN = 8

logger = logging.getLogger(__name__)

_VIRTUAL_KEYWORDS = ("debug", "wlan", "spi")
_BLUETOOTH_KEYWORDS = ("bluetooth",)


async def _finish_close(close: Awaitable[Any]) -> None:
    """Await a port-close step to completion even when this task is cancelled meanwhile.

    A cancelled scan (or a sibling probe failing) must not abandon a half-closed serial
    port: the close runs to its end, then the cancellation is re-raised.
    """
    closing = asyncio.ensure_future(close)
    cancelled = False
    while True:
        try:
            await asyncio.shield(closing)
            break
        except asyncio.CancelledError:
            if closing.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError


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
        preferred_baudrate: The identified profile's ``default_baud``
            (``None`` without a known profile), MOR-4207.
        low_baud_hint: For a CI-V radio below its profile baud, what to set
            on the radio — the serial backend's own scope guidance.
        low_baud: Derived: the radio answered only below
            ``preferred_baudrate``. For an Icom CI-V radio the scope is then
            off; setup can show ``low_baud_hint``.
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
    preferred_baudrate: int | None = None
    low_baud_hint: str | None = None
    low_baud: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.low_baud = (
            self.preferred_baudrate is not None
            and self.baudrate < self.preferred_baudrate
        )

    def to_dict(self) -> RadioDiscoveryResultDict:
        """The result's fields by name: the form discovery output is grouped in."""
        return cast(RadioDiscoveryResultDict, asdict(self))


class RadioDiscoveryResultDict(TypedDict, total=False):
    """:meth:`RadioDiscoveryResult.to_dict` — one key per result field (MOR-4207).

    The serialized serial result :func:`dedupe_radios` groups and
    :func:`build_setup_discovery_payload` reads. Keys never drift from the
    dataclass (``tests/test_discovery_probe_baud_order.py`` pins them).
    """

    port: str
    protocol: str
    model: str
    profile_id: str
    baudrate: int
    address: int | str
    description: str | None
    hwid: str | None
    usb_audio: dict[str, Any] | None
    vid: int | None
    pid: int | None
    manufacturer: str | None
    product: str | None
    serial_number: str | None
    preferred_baudrate: int | None
    low_baud_hint: str | None
    low_baud: bool


class GroupedRadio(TypedDict):
    """One radio of :func:`dedupe_radios`: its LAN and serial results."""

    model: object
    lan: list[dict[str, Any]]
    serial: list[RadioDiscoveryResultDict]


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


_OpenSerial = Callable[..., Awaitable[tuple[Any, Any]]]


_ProbeCode = Literal[
    "serial_busy",
    "serial_permission_denied",
    "serial_not_found",
    "serial_timeout",
    "serial_unknown",
]


@dataclass
class CivProbeDiagnostic:
    """Caller-local CI-V probe status without device or exception contents."""

    status: Literal[
        "not_attempted",
        "response",
        "open_failed",
        "opened_no_response",
        "incomplete_reply",
        "malformed_reply",
        "probe_error",
    ] = "not_attempted"
    stage: Literal["open", "write", "read", "parse", "close"] | None = None
    code: _ProbeCode | None = None
    open_failure_codes: tuple[_ProbeCode, ...] = ()


# Documented serial baud rates per protocol: the fallbacks a port is swept at
# after the profile bauds, in this order (MOR-4207, see serial_probe_baud_rates).
_DOCUMENTED_PROBE_BAUDS: dict[str, tuple[int, ...]] = {
    "civ": (19200, 9600, 115200, 4800),
    "yaesu_cat": (38400, 9600, 115200),
}


def serial_probe_baud_rates(
    protocol_type: Literal["civ", "yaesu_cat"],
) -> tuple[int, ...]:
    """The order a serial port is probed at for *protocol_type* (MOR-4207).

    An Icom radio on ``CI-V USB Baud Rate = Auto`` keeps the baud of the first
    access to its port until it is restarted, so the first probe decides the
    speed the radio then runs at. The loaded profiles' ``default_baud`` values
    come first, highest first (115200 for the Icom CI-V profiles: the serial
    scope minimum), then the protocol's other documented rates in their
    documented order. A serial connect tries its own profile baud and the
    configured baud ahead of it (:func:`serial_connect_baud_rates`).
    """
    preferred = preferred_serial_bauds(protocol_type)
    return tuple(dict.fromkeys((*preferred, *_DOCUMENTED_PROBE_BAUDS[protocol_type])))


def serial_connect_baud_rates(
    protocol_type: Literal["civ", "yaesu_cat"], profile_baud: int, hint: int
) -> tuple[int, ...]:
    """The order a serial connect contacts its radio at (MOR-4207).

    The discovery rule for one known radio: its profile ``default_baud``
    first, so an Icom on ``CI-V USB Baud Rate = Auto`` locks there; then
    *hint*, the baud the caller passed (``--serial-baud``, a saved route),
    so a radio fixed at it answers after one miss; then the rest of
    :func:`serial_probe_baud_rates`.
    """
    return tuple(
        dict.fromkeys((profile_baud, hint, *serial_probe_baud_rates(protocol_type)))
    )


async def sweep_serial_bauds(
    bauds: tuple[int, ...],
    *,
    start: int,
    retune: Callable[[int], bool],
    heard_at: Callable[[int], Awaitable[bool]],
    usable: Callable[[], bool],
) -> int | None:
    """Ask the radio on an open link at each of *bauds* but *start* (MOR-3620).

    The one connect-time baud search of the serial backends (CI-V and Yaesu
    CAT). The link is retuned in place, never reopened. Stops as soon as
    *usable* is false (the attempt was retired or the link died); skips a
    baud the port refuses (*retune* false); returns the first baud
    *heard_at* reports heard, or ``None``. Putting *start* back after a miss
    is the caller's: only it knows whether the attempt is still current.
    """
    for baud in bauds:
        if baud == start:
            continue
        if not usable():
            return None
        if not retune(baud):
            continue
        if await heard_at(baud):
            return baud
    return None


def _profile_baud(profile_id: str) -> int | None:
    """The ``default_baud`` of *profile_id*, or ``None`` without a usable profile.

    Result metadata, like the USB audio lookup: a profile lookup failure
    never fails discovery.
    """
    if not profile_id:
        return None
    try:
        return get_radio_profile(profile_id).default_baud
    except Exception as exc:
        logger.debug("discover_serial_radios: profile baud lookup failed: %s", exc)
        return None


async def probe_serial_civ(
    port: str,
    baud_rates: list[int] | None = None,
    timeout: float = 1.0,
    *,
    _open_serial: _OpenSerial | None = None,
    diagnostics: CivProbeDiagnostic | None = None,
) -> CivProbeResult | None:
    """Probe a serial port for a CI-V radio, trying multiple baud rates.

    Sends a *Read Transceiver ID* broadcast (``FE FE 00 E0 19 00 FD``) at each
    baud rate and waits up to *timeout* seconds for a valid response.

    Args:
        port: Serial device path (e.g. ``/dev/ttyUSB0``).
        baud_rates: Baud rates to try, in order. Defaults to
            :func:`serial_probe_baud_rates` for ``"civ"``.
        timeout: Per-baud timeout in seconds.
        _open_serial: Overrides the open step of :func:`rigplane.core.serial_open.open_serial_port`;
            post-open control-line deassert still applies (used in tests).
        diagnostics: Optional caller-local status, reset before the baud sweep.

    Returns:
        :class:`CivProbeResult` on success, or ``None`` if no radio responded.
    """
    if baud_rates is None:
        baud_rates = list(serial_probe_baud_rates("civ"))

    if diagnostics is not None:
        diagnostics.status = "not_attempted"
        diagnostics.stage = None
        diagnostics.code = None
        diagnostics.open_failure_codes = ()

    for baud in baud_rates:
        if diagnostics is None:
            result = await _try_baud(port, baud, timeout, _open_serial=_open_serial)
        else:
            attempt = CivProbeDiagnostic()
            try:
                result = await _try_baud(
                    port, baud, timeout, _open_serial=_open_serial, diagnostics=attempt
                )
            finally:
                for code in attempt.open_failure_codes:
                    if code not in diagnostics.open_failure_codes:
                        diagnostics.open_failure_codes += (code,)
                priority = {
                    "not_attempted": 0,
                    "open_failed": 1,
                    "opened_no_response": 2,
                    "incomplete_reply": 3,
                    "malformed_reply": 3,
                    "response": 4,
                    "probe_error": 5,
                }
                if priority[attempt.status] >= priority[diagnostics.status]:
                    diagnostics.status = attempt.status
                    diagnostics.stage = attempt.stage
                    diagnostics.code = attempt.code
                if diagnostics.status == "open_failed":
                    diagnostics.code = (
                        diagnostics.open_failure_codes[0]
                        if len(diagnostics.open_failure_codes) == 1
                        else "serial_unknown"
                    )
        if result is not None:
            return result
    return None


async def _try_baud(
    port: str,
    baud: int,
    timeout: float,
    *,
    _open_serial: _OpenSerial | None = None,
    diagnostics: CivProbeDiagnostic | None = None,
) -> CivProbeResult | None:
    """Open *port* at *baud*, send CI-V probe, return result or None.

    Args:
        port: Serial device path.
        baud: Baud rate to attempt.
        timeout: Read timeout in seconds.
        _open_serial: Overrides the open step of :func:`rigplane.core.serial_open.open_serial_port`;
            post-open control-line deassert still applies.

    Returns:
        :class:`CivProbeResult` on success, or ``None`` on timeout / bad data.
    """
    logger.debug("probe_serial_civ: opening %s @ %d", port, baud)
    try:
        reader, writer = await open_serial_port(
            url=port, baudrate=baud, opener=_open_serial
        )
    except OSError as exc:
        # Expected during a scan: port busy, absent, or refusing the baud
        # rate. pyserial's SerialException subclasses OSError.
        logger.debug("probe_serial_civ: cannot open %s @ %d", port, baud)
        if diagnostics is not None:
            diagnostics.status = "open_failed"
            diagnostics.stage = "open"
            diagnostics.code = cast(
                _ProbeCode, serial_open_diagnostic(exc).split(":")[0]
            )
            diagnostics.open_failure_codes = (diagnostics.code,)
        return None
    except Exception as exc:
        # Unexpected: programming error or broken environment. Not a
        # "cannot open" — make it visible at default logging.
        logger.warning(
            "probe_serial_civ: unexpected %s opening %s @ %d: %s",
            type(exc).__name__,
            port,
            baud,
            exc,
            exc_info=True,
        )
        if diagnostics is not None:
            diagnostics.status = "open_failed"
            diagnostics.stage = "open"
            diagnostics.code = cast(
                _ProbeCode, serial_open_diagnostic(exc).split(":")[0]
            )
            diagnostics.open_failure_codes = (diagnostics.code,)
        return None

    operation: Literal["write", "read", "parse"] = "write"
    try:
        writer.write(_CIV_PROBE_CMD)
        await writer.drain()
        logger.debug("probe_serial_civ: sent probe to %s @ %d baud", port, baud)

        # Read in a loop: first read often returns only the echo of our
        # command; the actual radio response arrives a few ms later.
        buf = bytearray()
        response_preamble = bytes([0xFE, 0xFE, 0xE0])
        operation = "read"
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
            operation = "parse"
            result = _parse_probe_response(port, baud, bytes(buf))
            if result is not None:
                if diagnostics is not None:
                    diagnostics.status = "response"
                    diagnostics.stage = "parse"
                return result
            operation = "read"

        if not buf:
            logger.debug("probe_serial_civ: timeout at %s @ %d", port, baud)
            if diagnostics is not None:
                diagnostics.status = "opened_no_response"
                diagnostics.stage = "read"
            return None

        operation = "parse"
        result = _parse_probe_response(port, baud, bytes(buf))
        if diagnostics is not None:
            diagnostics.stage = "parse"
            if result is not None:
                diagnostics.status = "response"
            elif not bytes(buf).replace(_CIV_PROBE_CMD, b""):
                diagnostics.status = "opened_no_response"
                diagnostics.stage = "read"
            else:
                start = buf.rfind(response_preamble)
                frame = buf[start:] if start >= 0 else bytearray()
                diagnostics.status = (
                    "incomplete_reply"
                    if frame
                    and 0xFD not in frame[3:]
                    and (len(frame) < 6 or frame[4:6] == b"\x19\x00")
                    else "malformed_reply"
                )
        return result
    except BaseException:
        if diagnostics is not None:
            diagnostics.status = "probe_error"
            diagnostics.stage = operation
            diagnostics.code = None
        raise
    finally:
        logger.debug("probe_serial_civ: closing %s", port)
        try:
            writer.close()
            await _finish_close(writer.wait_closed())
        except BaseException:
            if diagnostics is not None:
                diagnostics.status = "probe_error"
                diagnostics.stage = "close"
                diagnostics.code = None
            raise
        logger.debug("probe_serial_civ: closed %s", port)


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
    search = bytes([0xFE, 0xFE, 0xE0])
    cursor = 0
    while True:
        idx = data.find(search, cursor)
        if idx == -1:
            return None
        cursor = idx + len(search)
        end_idx = data.find(0xFD, idx + 3)
        if end_idx == -1:
            continue
        next_preamble = data.find(b"\xfe\xfe", idx + 2)
        if next_preamble != -1 and next_preamble < end_idx:
            continue
        frame = data[idx : end_idx + 1]
        if len(frame) < _RESPONSE_MIN_LEN or frame[4:6] != b"\x19\x00":
            continue
        address = frame[3]
        model_id = bytes(frame[6:-1])
        logger.info(
            "probe_serial_civ: found radio at %s @ %d — addr=0x%02X model=%s",
            port,
            baud,
            address,
            model_id.hex(),
        )
        return CivProbeResult(port=port, baud=baud, address=address, model_id=model_id)


# ---------------------------------------------------------------------------
# Xiegu model-ID probe (Xiegu-vs-Icom disambiguation; see resolve_civ_radio_identity)
# ---------------------------------------------------------------------------


def _xiegu_model_id_cmd(address: int) -> bytes:
    """CI-V *get Xiegu model ID* frame (``0x1D 0x19``) for a radio at
    *address*.

    Documented for the X6200 at 0xA4 (Radioddity *X6200 CI-V
    implementation V1.0.6*, PDF page 9); the G7250 answers the opcode at
    any address and echoes the asked address as its own (LIVE bench audit
    2026-11-09, MOR-4352), so the echo path asks at 0x00. Icom radios at
    the same address (IC-705) have no such opcode and answer with a NAK.
    """
    return bytes([0xFE, 0xFE, address, 0xE0, 0x1D, 0x19, 0xFD])


async def probe_xiegu_model_id(
    port: str,
    baud: int,
    address: int,
    timeout: float = 0.5,
    *,
    _open_serial: _OpenSerial | None = None,
) -> bytes | None:
    """Ask the Xiegu model-ID query (``0x1D 0x19``) at *address*; return the
    reply's model-id bytes.

    Disambiguates Xiegu radios from Icom at shared CI-V addresses: only the
    Xiegu firmware implements ``0x1D 0x19`` (Radioddity X6200 CI-V V1.0.6,
    PDF page 9; the G7250 answers it too, LIVE 2026-11-09). A valid
    (non-NAK) reply positively identifies a Xiegu regardless of the USB
    bridge it enumerates behind — so it survives USB-chip changes the hwid
    fingerprint misses (MOR-226). Safe to send to an IC-705: standard Icom
    CI-V NAKs unknown commands rather than wedging.

    Args:
        port: Serial device path.
        baud: Baud rate to use (the rate the CI-V probe already succeeded at).
        address: CI-V address to ask (0xA4 for the family default; 0x00 for
            a radio that echoed the broadcast address — the G7250 answers
            any address, LIVE G7250 2026-11-09, MOR-4352).
        timeout: Read timeout in seconds.
        _open_serial: Overrides the open step of :func:`rigplane.core.serial_open.open_serial_port`;
            post-open control-line deassert still applies (used in tests).

    Returns:
        The model-id payload bytes (e.g. ``b"\x72\x50"``) on a valid
        ``0x1D 0x19`` reply, ``None`` on NAK, timeout, unexpected data, or
        open failure.
    """
    logger.debug("probe_xiegu_model_id: opening %s @ %d", port, baud)
    try:
        reader, writer = await open_serial_port(
            url=port, baudrate=baud, opener=_open_serial
        )
    except OSError:
        # Expected during a scan: port busy, absent, or refusing the baud
        # rate. pyserial's SerialException subclasses OSError.
        logger.debug("probe_xiegu_model_id: cannot open %s @ %d", port, baud)
        return None
    except Exception as exc:
        # Unexpected: programming error or broken environment. Not a
        # "cannot open" — make it visible at default logging.
        logger.warning(
            "probe_xiegu_model_id: unexpected %s opening %s @ %d: %s",
            type(exc).__name__,
            port,
            baud,
            exc,
            exc_info=True,
        )
        return None

    try:
        writer.write(_xiegu_model_id_cmd(address))
        await writer.drain()

        buf = bytearray()
        response_preamble = bytes([0xFE, 0xFE, 0xE0])
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
            if buf.find(response_preamble) != -1:
                break

        return _parse_xiegu_model_id_reply(port, bytes(buf))
    finally:
        logger.debug("probe_xiegu_model_id: closing %s", port)
        writer.close()
        await _finish_close(writer.wait_closed())
        logger.debug("probe_xiegu_model_id: closed %s", port)


def _parse_xiegu_model_id_reply(port: str, data: bytes) -> bytes | None:
    """Return the model-id payload of a valid CI-V ``0x1D 0x19`` reply in
    *data*, or ``None``.

    Expected frame: ``FE FE E0 <addr> 1D 19 <model...> FD``. A NAK collision
    (``FE FE E0 <addr> FA FD``) or any other command echo means the radio
    does not implement the Xiegu opcode (i.e. it is an IC-705, not a Xiegu).
    """
    search = bytes([0xFE, 0xFE, 0xE0])
    idx = data.find(search)
    if idx == -1:
        logger.debug("probe_xiegu_model_id: no reply from %s (not a Xiegu)", port)
        return None

    frame = data[idx:]
    if len(frame) < 6:
        logger.debug("probe_xiegu_model_id: reply too short from %s", port)
        return None

    if frame[4] == 0xFA:
        logger.debug("probe_xiegu_model_id: %s NAKed 0x1D 0x19 (not a Xiegu)", port)
        return None
    if frame[4] != 0x1D or frame[5] != 0x19:
        logger.debug(
            "probe_xiegu_model_id: unexpected reply from %s: %s",
            port,
            frame[:8].hex(),
        )
        return None

    end_idx = frame.find(0xFD, 6)
    model = bytes(frame[6:end_idx]) if end_idx != -1 else b""
    logger.info(
        "probe_xiegu_model_id: %s is a Xiegu (model id %s)", port, model.hex() or "?"
    )
    return model


# ---------------------------------------------------------------------------
# Yaesu CAT probe
# ---------------------------------------------------------------------------

#: Mapping from 4-digit hex model ID string to (model_name, profile_id).
_YAESU_CAT_MODEL_MAP: dict[str, tuple[str, str]] = {
    "0840": ("FTX-1", "yaesu_ftx1"),
}

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
            :func:`serial_probe_baud_rates` for ``"yaesu_cat"``.
        timeout: Per-baud timeout in seconds.
        _transport_factory: Override for ``YaesuCatTransport`` constructor
            (used in tests).

    Returns:
        :class:`RadioDiscoveryResult` on success, or ``None`` if no Yaesu radio responded.
    """
    if baud_rates is None:
        baud_rates = list(serial_probe_baud_rates("yaesu_cat"))

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
    logger.debug("probe_serial_yaesu_cat: opening %s @ %d", port, baud)
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
        logger.debug("probe_serial_yaesu_cat: closing %s", port)
        try:
            await _finish_close(transport.close())
        except Exception:
            pass
        logger.debug("probe_serial_yaesu_cat: closed %s", port)


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
        preferred_baudrate=_profile_baud(profile_id),
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


def _local_ipv4_addresses() -> list[str]:
    """Return a best-effort subset of the host's local IPv4 addresses.

    Combines the default-route address — a UDP ``connect`` sends no packet,
    it only makes the OS pick the outbound interface — with, on Windows,
    the IPv4 addresses the hostname resolves to. Loopback and unspecified
    addresses are dropped; duplicates are removed with a stable order.
    """
    import socket
    import sys

    addresses: list[str] = []

    # UDP connect() transmits nothing; it just records the routing decision,
    # so getsockname() reports the default-route interface's address. The
    # target is TEST-NET-1 (RFC 5737, documentation only), so no real
    # traffic can leave the host.
    try:
        probe_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe_sock.connect(("192.0.2.1", 9))
            addresses.append(probe_sock.getsockname()[0])
        finally:
            probe_sock.close()
    except OSError:
        pass

    # Windows-only: there the hostname resolves locally to every adapter's
    # address, and Windows is where the single-adapter broadcast problem is;
    # elsewhere getaddrinfo(gethostname()) can stall for seconds on a name
    # that does not resolve locally (e.g. .local) before the scan's clock
    # even starts. The UDP-connect probe above runs on every platform.
    if sys.platform == "win32":
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                addresses.append(info[4][0])
        except OSError:
            pass

    unique: list[str] = []
    for address in addresses:
        if address.startswith("127.") or address == "0.0.0.0" or address in unique:
            continue
        unique.append(address)
    return unique


def _are_you_there_packet(sender_id: int) -> bytes:
    """Build the 16-byte Icom LAN "Are You There" (ptype 0x03) packet.

    Shared by the :func:`discover_lan_radios` broadcast scan and the
    :func:`probe_lan_radio` unicast probe. *sender_id* must be non-zero:
    the IC-7610 ignores packets with sender_id=0.
    """
    pkt = bytearray(0x10)
    struct.pack_into("<I", pkt, 0, 0x10)  # size
    struct.pack_into("<H", pkt, 4, 0x03)  # ARE_YOU_THERE
    struct.pack_into("<H", pkt, 6, 0)  # seq=0
    struct.pack_into("<I", pkt, 8, sender_id)  # sender_id (non-zero!)
    struct.pack_into("<I", pkt, 0x0C, 0)  # remote_id=0 (unknown)
    return bytes(pkt)


def _is_unreachable_error(exc: BaseException) -> bool:
    """True when *exc* reports the probed host or network unreachable.

    Covers the no-route / host-down errnos, the ICMP port unreachable
    (as ``ConnectionRefusedError`` / ``ConnectionResetError``) and the
    unresolvable-name ``socket.gaierror``.
    """
    import socket

    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, socket.gaierror)):
        return True
    return isinstance(exc, OSError) and exc.errno in _PROBE_UNREACHABLE_ERRNOS


async def discover_lan_radios(timeout: float = 3.0) -> list[dict[str, object]]:
    """Discover Icom radios on LAN via UDP broadcast ("Are You There").

    The probe is sent from one unbound socket and from one socket bound to
    each enumerated local IPv4 address (best-effort). On Windows an unbound
    socket sends the limited broadcast (255.255.255.255) out of a single
    interface only, while a socket bound to an adapter's address sends it
    through that adapter; on other platforms the extra bound sockets are
    harmless duplicates. Per-socket receive errors (including the
    WSAECONNRESET Windows raises after an ICMP port-unreachable) are
    tolerated.

    Args:
        timeout: How long to listen for responses, in seconds.

    Returns:
        List of dicts with keys ``host`` and ``remote_id``.
    """
    import socket
    import time

    def _scan() -> list[dict[str, object]]:
        import random

        # Build "Are You There" packet with random sender_id
        # IC-7610 ignores packets with sender_id=0
        my_id = random.randint(1, 0xFFFFFFFF)
        pkt = _are_you_there_packet(my_id)

        # Enumeration is best-effort: any exception (a chroot without routing,
        # exotic resolver errors) falls back to the unbound socket alone.
        try:
            local_addresses = _local_ipv4_addresses()
        except Exception:
            local_addresses = []

        def _open_probe_socket(address: str | None) -> socket.socket:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                if address is not None:
                    sock.bind((address, 0))
                sock.settimeout(_LAN_RECV_POLL_INTERVAL)
            except BaseException:
                sock.close()
                raise
            return sock

        found: dict[str, dict[str, object]] = {}
        sockets: list[socket.socket] = []
        # Bound address per socket ("unbound" for the unbound socket), for
        # send-failure log lines.
        labels: dict[socket.socket, str] = {}

        try:
            # One unbound socket as before, plus one per local address so
            # that, on Windows, the limited broadcast also leaves through
            # each bound adapter; on other platforms the bound sockets are
            # harmless duplicates (coverage is best-effort). A socket that
            # fails to create/bind (address vanished, adapter removed) is
            # logged and skipped; the remaining adapters still get probed.
            try:
                sock = _open_probe_socket(None)
            except OSError as exc:
                logger.debug("discover: could not open unbound socket: %s", exc)
            else:
                sockets.append(sock)
                labels[sock] = "unbound"
            for address in local_addresses:
                try:
                    sock = _open_probe_socket(address)
                except OSError as exc:
                    logger.debug("discover: could not bind %s: %s", address, exc)
                else:
                    sockets.append(sock)
                    labels[sock] = address

            # Broadcast on the Icom discovery port. Returns False only if
            # the very first send fails on every socket (no usable socket);
            # a send failure on one socket is logged — the first failure at
            # WARNING with the bound address, repeats at DEBUG — and
            # tolerated so listening continues.
            send_failed: set[socket.socket] = set()

            def _broadcast(*, first: bool) -> bool:
                sent_any = False
                for sock in sockets:
                    try:
                        sock.sendto(bytes(pkt), ("255.255.255.255", LAN_DISCOVERY_PORT))
                        sent_any = True
                    except OSError as exc:
                        if sock in send_failed:
                            logger.debug(
                                "discover: broadcast failed again on %s: %s",
                                labels[sock],
                                exc,
                            )
                        else:
                            send_failed.add(sock)
                            logger.warning(
                                "discover: broadcast failed on %s: %s",
                                labels[sock],
                                exc,
                            )
                return sent_any or not first

            if not _broadcast(first=True):
                return []

            start = time.monotonic()
            deadline = start + timeout
            next_resend = start + _LAN_RESEND_INTERVAL
            while time.monotonic() < deadline:
                # Spread retransmits across the window; keep within `timeout`.
                now = time.monotonic()
                if now >= next_resend:
                    _broadcast(first=False)
                    next_resend = now + _LAN_RESEND_INTERVAL
                for sock in sockets:
                    try:
                        data, addr = sock.recvfrom(256)
                    except socket.timeout:
                        continue
                    except OSError as exc:
                        # Windows surfaces the ICMP port-unreachable for a
                        # previous broadcast as ConnectionResetError from
                        # recvfrom; any other per-socket error also just
                        # skips this poll. A socket in a permanent error
                        # state returns immediately, so pace the loop.
                        logger.debug("discover: recvfrom failed: %s", exc)
                        time.sleep(0.01)
                        continue
                    if len(data) >= 0x10:
                        ptype = struct.unpack_from("<H", data, 4)[0]
                        if ptype == 0x04:  # I_AM_HERE
                            remote_id = struct.unpack_from("<I", data, 8)[0]
                            if addr[0] not in found:
                                logger.info(
                                    "discover_lan_radios: found %s id=0x%08X",
                                    addr[0],
                                    remote_id,
                                )
                            # Dedup by host: a radio answering multiple probes
                            # is recorded once, whichever socket heard it.
                            found[addr[0]] = {"host": addr[0], "remote_id": remote_id}
        finally:
            for sock in sockets:
                sock.close()

        return list(found.values())

    return await asyncio.get_running_loop().run_in_executor(None, _scan)


async def probe_lan_radio(
    host: str,
    port: int = LAN_DISCOVERY_PORT,
    *,
    timeout: float = 3.0,
) -> bool:
    """Unicast "Are You There" liveness probe for one Icom LAN radio.

    Asks a known (remembered) radio's control port whether it is
    answering, with the radio's own discovery handshake — the same
    packet :func:`discover_lan_radios` broadcasts — retransmitted every
    0.6 s inside the window. Prefer this over connecting to the control
    port with TCP: the port is UDP, and a real radio may silently drop a
    TCP SYN, making a reachable radio look unreachable.

    Args:
        host: Hostname or IPv4 address of the radio.
        port: UDP control port of the radio.
        timeout: How long to keep probing, in seconds.

    Returns:
        ``True`` on the first "I Am Here" from *host*; ``False`` when the
        window ends with no answer or the OS reports the host unreachable
        (no route, host down, ICMP port unreachable, unresolvable name).
        Never raises for an unreachable host; programming errors raise.
    """
    import random
    import socket
    import time

    # Set from the cancelled/finished awaiting coroutine so the worker
    # thread closes its socket promptly instead of holding it until the
    # window ends.
    stop = threading.Event()

    def _probe() -> bool:
        # Resolve in the worker thread: a slow resolver must not block the
        # event loop, and an unresolvable name is a plain False.
        try:
            infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
        except OSError:  # includes socket.gaierror (unresolvable name)
            return False
        target = infos[0][4]

        pkt = _are_you_there_packet(random.randint(1, 0xFFFFFFFF))
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(_LAN_RECV_POLL_INTERVAL)

            def _send() -> bool:
                """Send one probe; False when the host is unreachable."""
                try:
                    sock.sendto(pkt, target)
                except OSError as exc:
                    if _is_unreachable_error(exc):
                        logger.info(
                            "probe_lan_radio: %s reported unreachable: %s",
                            host,
                            exc,
                        )
                        return False
                    logger.debug("probe_lan_radio: send failed: %s", exc)
                return True

            if not _send():
                return False

            start = time.monotonic()
            deadline = start + timeout
            next_resend = start + _LAN_RESEND_INTERVAL
            # The `now < deadline` loop form (like the scan) also ends a
            # non-positive or NaN window immediately instead of spinning.
            while time.monotonic() < deadline:
                if stop.is_set():
                    return False
                # Spread retransmits across the window; keep within
                # `timeout`.
                now = time.monotonic()
                if now >= next_resend:
                    if not _send():
                        return False
                    next_resend = now + _LAN_RESEND_INTERVAL
                try:
                    data, addr = sock.recvfrom(256)
                except socket.timeout:
                    continue
                except OSError as exc:
                    if _is_unreachable_error(exc):
                        logger.info(
                            "probe_lan_radio: %s reported unreachable: %s",
                            host,
                            exc,
                        )
                        return False
                    # Any other per-socket error just skips this poll; the
                    # socket stays usable, so pace the loop (same tolerance
                    # as the broadcast scan).
                    logger.debug("probe_lan_radio: recvfrom failed: %s", exc)
                    time.sleep(0.01)
                    continue
                if (
                    addr[0] == target[0]
                    and len(data) >= 0x10
                    and struct.unpack_from("<H", data, 4)[0] == 0x04  # I_AM_HERE
                ):
                    logger.info(
                        "probe_lan_radio: %s answered (remote_id=0x%08X)",
                        host,
                        struct.unpack_from("<I", data, 8)[0],
                    )
                    return True
            return False
        finally:
            sock.close()

    # Same executor approach as discover_lan_radios: this module's LAN
    # probing already runs its blocking-socket loop in the default
    # executor, so the event loop stays free; the stop event above adds
    # the prompt socket close on cancellation the one-shot probe needs.
    future = asyncio.get_running_loop().run_in_executor(None, _probe)
    try:
        return await future
    finally:
        stop.set()


def _resolve_xiegu_x6200_override(
    address: int, port: SerialPortCandidate
) -> tuple[str, str] | None:
    """Disambiguate Xiegu X6200 from Icom IC-705 at the shared CI-V address 0xA4.

    Both rigs default to CI-V address 0xA4 (Hamlib ``rigs/icom/xiegu.c``
    ``x6100_priv_caps``; Radioddity *X6200 CI-V implementation V1.0.6*
    PDF page 4). The address probe alone cannot distinguish them, so this
    helper looks at the USB hardware fingerprint instead:

    - X6200 enumerates over a Nanjing QinHeng / WCH CH342 chip
      (``VID:PID = 1A86:55D2``) as a composite dual-ACM device with
      product name ``"USB Dual_Serial"``. IC-705 uses an Icom-made USB
      controller and never matches that fingerprint.
    - Either signal alone is sufficient. VID/PID is the strong match;
      the product-name fallback covers OS-level enumerations where
      ``vid``/``pid`` were not surfaced by pyserial.

    Returns ``(model_name, profile_id)`` when the fingerprint matches;
    otherwise ``None`` (the caller falls back to the address-based
    mapping, which keeps the existing IC-705 behaviour intact). MOR-170.
    """
    if address != 0xA4:
        return None
    if port.vid == 0x1A86 and port.pid == 0x55D2:
        return ("X6200", "xiegu_x6200")
    if port.product and "USB Dual_Serial" in port.product:
        return ("X6200", "xiegu_x6200")
    return None


async def resolve_civ_radio_identity(
    civ: CivProbeResult,
    port: SerialPortCandidate | None = None,
    *,
    _open_serial: _OpenSerial | None = None,
) -> tuple[str, str]:
    """Resolve a CI-V probe hit to ``(model, profile_id)``.

    Single owner of the serial identity rules (MOR-3777), shared by
    :func:`discover_serial_radios` and by setup consumers that probe one
    operator-chosen port:

    1. the CI-V address/model-ID table
       (:func:`rigplane.radios.identify_radio`, ``CIV_PROFILE_MAP``);
    2. the X6200 USB-hardware fingerprint override at the shared 0xA4
       address (MOR-170), when *port* metadata is supplied;
    3. the Xiegu-only ``0x1D 0x19`` model-ID opcode when the fingerprint
       is absent or inconclusive at 0xA4 (MOR-226), and at 0x00 for a
       radio that answered the broadcast probe by echoing the broadcast
       address (MOR-4352) — the one extra probe, sent to no other address,
       with *port*/*baud* taken from *civ* and the same ``_open_serial``
       injection as the CI-V probe. The reply is mapped to a profile
       through the profiles' ``[radio].xiegu_model_id`` data.

    Callers own the address/model *match* decision; this helper only
    names what answered.
    """
    from rigplane.profiles import profile_by_xiegu_model_id
    from rigplane.radios import CIV_PROFILE_MAP, identify_radio

    model = identify_radio(civ.address, civ.model_id)
    profile_id = CIV_PROFILE_MAP.get(civ.address, "")
    # Hardware-fingerprint override for the IC-705 / X6200 CI-V
    # address collision (both default to 0xA4). MOR-170.
    xiegu_override = (
        _resolve_xiegu_x6200_override(civ.address, port) if port is not None else None
    )
    if xiegu_override is None and civ.address in (0xA4, 0x00):
        # When the USB fingerprint is missing or inconclusive at the shared
        # 0xA4 address, confirm via the Xiegu-only CI-V model-ID opcode
        # (0x1D 0x19) — survives future USB-chip changes the hwid
        # fingerprint misses and never mislabels an IC-705 (which NAKs the
        # opcode). MOR-226.
        # MOR-4352 also asks at 0x00: a Xiegu that answered the broadcast
        # probe by echoing the broadcast address (address 0x00 in *civ*,
        # LIVE G7250 2026-11-09) is otherwise "Unknown (0x00)" — the address
        # table cannot name it, but its 0x1D 0x19 reply can.
        model_id = await probe_xiegu_model_id(
            civ.port, civ.baud, civ.address, _open_serial=_open_serial
        )
        if model_id is not None:
            # Data-driven mapping through the profiles' [radio]
            # .xiegu_model_id (0x6200 -> X6200, 0x7250 -> G7250): no
            # per-model branch here, and an unlisted Xiegu is named as
            # what it is instead of being mislabeled X6200 (MOR-4352).
            profile = profile_by_xiegu_model_id(model_id)
            if profile is not None:
                xiegu_override = (profile.model, profile.id)
            else:
                xiegu_override = (
                    f"Unknown Xiegu (model id {model_id.hex()})",
                    "",
                )
    if xiegu_override is not None:
        model, profile_id = xiegu_override
    return model, profile_id


async def _discover_one_port(
    port: SerialPortCandidate,
    *,
    _open_serial: _OpenSerial | None = None,
    _yaesu_transport_factory: _YaesuTransportFactory | None = None,
) -> RadioDiscoveryResult | None:
    """Probe a single candidate port: CI-V → Yaesu CAT → Kenwood CAT.

    Stops after the first successful match for the port.

    Args:
        port: The candidate port to probe.
        _open_serial: Overrides the open step of :func:`rigplane.core.serial_open.open_serial_port`,
            passed through to :func:`probe_serial_civ` (used in tests).
        _yaesu_transport_factory: Override for ``YaesuCatTransport`` constructor
            passed through to :func:`probe_serial_yaesu_cat` (used in tests).

    Returns:
        :class:`RadioDiscoveryResult` on success, or ``None`` if no radio answered.
    """
    # --- CI-V probe ---
    civ = await probe_serial_civ(port.device, _open_serial=_open_serial)
    if civ:
        model, profile_id = await resolve_civ_radio_identity(
            civ, port, _open_serial=_open_serial
        )
        result = RadioDiscoveryResult(
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
            usb_audio=await _resolve_profile_usb_audio_metadata(civ.port, profile_id),
            preferred_baudrate=_profile_baud(profile_id),
        )
        if result.low_baud:
            # Below the profile baud a CI-V radio has no scope: hand setup the
            # serial backend's own advice (MOR-4207).
            from ._icom_serial_base import low_baud_scope_guidance

            result.low_baud_hint = low_baud_scope_guidance()
        return result

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
        yaesu.usb_audio = await _resolve_profile_usb_audio_metadata(
            yaesu.port, yaesu.profile_id
        )
        return yaesu

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
        kenwood.usb_audio = await _resolve_profile_usb_audio_metadata(
            kenwood.port, kenwood.profile_id
        )
        return kenwood

    return None


async def discover_serial_radios(
    *,
    _open_serial: _OpenSerial | None = None,
    _yaesu_transport_factory: _YaesuTransportFactory | None = None,
) -> list[RadioDiscoveryResult]:
    """Discover radios connected via USB serial (CI-V, Yaesu CAT, Kenwood CAT).

    Probes all candidate ports concurrently, so the wall time is that of the
    slowest single port rather than the sum. Each port is probed in order:
    CI-V → Yaesu CAT → Kenwood CAT, stopping after the first match.

    Args:
        _open_serial: Overrides the open step of :func:`rigplane.core.serial_open.open_serial_port`,
            passed through to :func:`probe_serial_civ` (used in tests).
        _yaesu_transport_factory: Override for ``YaesuCatTransport`` constructor
            passed through to :func:`probe_serial_yaesu_cat` (used in tests).

    Returns:
        List of :class:`RadioDiscoveryResult` for all detected radios, in
        candidate enumeration order.
    """
    candidates = enumerate_serial_ports()
    tasks = [
        asyncio.ensure_future(
            _discover_one_port(
                port,
                _open_serial=_open_serial,
                _yaesu_transport_factory=_yaesu_transport_factory,
            )
        )
        for port in candidates
    ]
    try:
        probed = await asyncio.gather(*tasks)
    except BaseException:
        # A failed or cancelled scan must not leave sibling probes holding serial ports.
        for task in tasks:
            # A task already cancelling (scan cancellation reached it through gather) is
            # finishing its port close; a second cancel would interrupt that.
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return [result for result in probed if result is not None]


async def _resolve_profile_usb_audio_metadata(
    port: str, profile_id: str
) -> dict[str, object] | None:
    try:
        identity = get_radio_profile(profile_id).usb_identity if profile_id else None
    except Exception as exc:
        logger.debug("discover_serial_radios: USB audio resolve failed: %s", exc)
        return None
    return await asyncio.to_thread(_resolve_usb_audio_metadata, port, identity)


def _resolve_usb_audio_metadata(
    port: str, usb_identity: UsbAudioIdentity | None
) -> dict[str, object] | None:
    """Return optional USB audio mapping metadata without failing discovery."""
    mapping: AudioDeviceMapping | None
    try:
        mapping = resolve_audio_for_serial_port(port, usb_identity=usb_identity)
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
    lan_radios: list[dict[str, Any]], serial_radios: list[RadioDiscoveryResultDict]
) -> list[GroupedRadio]:
    """Group LAN and serial discovery results by radio identity.

    Two entries are considered the same radio when their ``model`` **and**
    ``address`` (CI-V address) fields match.  Entries that lack either field
    are treated as distinct radios and are never merged.

    Args:
        lan_radios: Dicts from :func:`discover_lan_radios`.
        serial_radios: :meth:`RadioDiscoveryResult.to_dict` of the
            :func:`discover_serial_radios` results.

    Returns:
        List of dicts, each with keys ``model``, ``lan`` (list), ``serial``
        (list).  ``model`` is the display name of the radio.
    """
    radios: dict[tuple[object, ...], GroupedRadio] = {}
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
        radios[key]["lan"].append(lan)

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
        radios[key]["serial"].append(serial)

    return list(radios.values())


class SetupLanConnection(TypedDict):
    """A LAN connection in the ``rigplane.discovery.v1`` setup JSON."""

    type: Literal["lan"]
    backend: Literal["lan"]
    label: str
    host: str
    remoteId: object
    requiresCredentials: bool


class SetupSerialConnection(TypedDict):
    """A USB serial connection in the ``rigplane.discovery.v1`` setup JSON.

    ``baudrate`` is the rate the radio answered at. ``preferredBaudrate``,
    ``lowBaud`` and ``lowBaudHint`` carry :class:`RadioDiscoveryResult`'s
    ``preferred_baudrate`` / ``low_baud`` / ``low_baud_hint`` (MOR-4207), so
    setup can show the scope-baud guidance.
    """

    type: Literal["serial"]
    backend: Literal["serial", "yaesu-cat"]
    label: str
    port: str
    protocol: object
    profileId: object
    baudrate: object
    preferredBaudrate: int | None
    lowBaud: bool
    lowBaudHint: str | None
    address: object
    description: object
    hwid: object
    vid: object
    pid: object
    manufacturer: object
    product: object
    serialNumber: object
    requiresCredentials: bool
    usbAudio: NotRequired[dict[str, object]]


def build_setup_discovery_payload(
    grouped_radios: list[GroupedRadio],
) -> dict[str, Any]:
    """Build stable discovery JSON for setup wizards and managed supervisors.

    Serial entries are :class:`RadioDiscoveryResultDict`; each connection is a
    :class:`SetupLanConnection` or :class:`SetupSerialConnection`.
    """
    radios: list[dict[str, Any]] = []
    for index, radio in enumerate(grouped_radios, start=1):
        connections: list[SetupLanConnection | SetupSerialConnection] = []
        for lan in radio.get("lan", []):
            host = str(lan.get("host", ""))
            remote_id = lan.get("remote_id")
            lan_connection: SetupLanConnection = {
                "type": "lan",
                "backend": "lan",
                "label": f"LAN {host}" if host else "LAN",
                "host": host,
                "remoteId": remote_id,
                "requiresCredentials": True,
            }
            connections.append(lan_connection)

        for serial in radio.get("serial", []):
            port = str(serial.get("port", ""))
            baudrate = serial.get("baudrate")
            protocol = serial.get("protocol", "civ")
            backend: Literal["serial", "yaesu-cat"] = (
                "yaesu-cat" if protocol == "yaesu_cat" else "serial"
            )
            connection: SetupSerialConnection = {
                "type": "serial",
                "backend": backend,
                "label": f"USB serial {port}" if port else "USB serial",
                "port": port,
                "protocol": protocol,
                "profileId": serial.get("profile_id"),
                "baudrate": baudrate,
                "preferredBaudrate": serial.get("preferred_baudrate"),
                "lowBaud": serial.get("low_baud") is True,
                "lowBaudHint": serial.get("low_baud_hint"),
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


# ---------------------------------------------------------------------------
# Hamlib-assisted discovery payload builders (MOR-911 — promoted to public API)
# ---------------------------------------------------------------------------


def _camelize_discovery_key(key: str) -> str:
    parts = key.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _camelize_discovery_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            _camelize_discovery_key(str(key)): _camelize_discovery_value(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_camelize_discovery_value(item) for item in value]
    return value


def _hamlib_catalog_payload(catalog: Any) -> dict[str, object]:
    return {
        "available": catalog.degraded_reason is None and bool(catalog.models),
        "sourceTool": catalog.source_tool,
        "modelCount": len(catalog.models),
        "degradedReason": catalog.degraded_reason,
    }


def _hamlib_evidence_payload(evidence: object) -> dict[str, object]:
    payload = {
        "source": getattr(evidence, "source"),
        "kind": getattr(evidence, "kind"),
        "status": getattr(evidence, "status"),
        "detail": getattr(evidence, "detail"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _hamlib_candidate_payload(
    candidate: object,
    *,
    candidate_id: str,
    auto_selectable: bool,
) -> dict[str, object]:
    return {
        "id": candidate_id,
        "transport": getattr(candidate, "transport"),
        "address": getattr(candidate, "address"),
        "observedIdentity": _camelize_discovery_value(
            getattr(candidate, "observed_identity")
        ),
        "suggestedBackend": getattr(candidate, "suggested_backend"),
        "suggestedModel": getattr(candidate, "suggested_model"),
        "confidence": getattr(candidate, "confidence"),
        "evidence": [
            _hamlib_evidence_payload(item) for item in getattr(candidate, "evidence")
        ],
        "safeNextAction": getattr(candidate, "safe_next_action"),
        "autoSelectable": auto_selectable,
    }


def _match_hamlib_catalog_model(serial: dict[str, object], catalog: Any) -> Any | None:
    if catalog.degraded_reason is not None:
        return None
    serial_model = str(serial.get("model") or "").lower()
    if not serial_model:
        return None
    serial_tokens = {
        token
        for token in serial_model.replace("-", " ").replace("_", " ").split()
        if token
    }
    for model in sorted(catalog.models.values(), key=lambda item: item.model_id):
        catalog_name = model.name.lower()
        catalog_tokens = {
            token
            for token in catalog_name.replace("-", " ").replace("_", " ").split()
            if token
        }
        if serial_model in catalog_name or serial_tokens <= catalog_tokens:
            return model
    return None


def _build_hamlib_serial_candidate(
    serial: dict[str, object],
    *,
    catalog: Any,
    candidate_id: str,
) -> dict[str, object]:
    catalog_match = _match_hamlib_catalog_model(serial, catalog)
    evidence = [
        DiscoveryEvidence(
            source="serial_discovery",
            kind="identity",
            status="detected",
            detail=str(serial.get("model") or serial.get("protocol") or "serial"),
        )
    ]
    if catalog.degraded_reason is not None:
        evidence.append(
            DiscoveryEvidence(
                source="hamlib_catalog",
                kind="catalog",
                status="degraded",
                detail=catalog.degraded_reason,
            )
        )
    elif catalog_match is not None:
        evidence.append(
            DiscoveryEvidence(
                source="hamlib_catalog",
                kind="model",
                status="catalog_match",
                detail=f"{catalog_match.model_id} {catalog_match.name}",
            )
        )
    else:
        evidence.append(
            DiscoveryEvidence(
                source="hamlib_catalog",
                kind="model",
                status="no_match",
            )
        )

    confidence: Literal["high", "medium", "low"] = (
        "medium" if catalog_match is not None else "low"
    )
    if catalog_match is not None:
        safe_next_action = "run_read_only_validation"
        suggested_model = f"{catalog_match.model_id} {catalog_match.name}"
    elif catalog.degraded_reason is not None:
        safe_next_action = "install_hamlib_then_validate"
        suggested_model = None
    else:
        safe_next_action = "manual_configuration_required"
        suggested_model = None

    candidate = DiscoveryCandidate(
        transport="serial",
        address=str(serial.get("port") or ""),
        observed_identity={
            "model": serial.get("model"),
            "protocol": serial.get("protocol"),
            "profile_id": serial.get("profile_id"),
            "baudrate": serial.get("baudrate", serial.get("baud")),
            "radio_address": serial.get("address"),
            "description": serial.get("description"),
            "hwid": serial.get("hwid"),
            "vid": serial.get("vid"),
            "pid": serial.get("pid"),
            "manufacturer": serial.get("manufacturer"),
            "product": serial.get("product"),
        },
        suggested_backend="hamlib",
        suggested_model=suggested_model,
        confidence=confidence,
        evidence=evidence,
        safe_next_action=safe_next_action,
    )
    return _hamlib_candidate_payload(
        candidate,
        candidate_id=candidate_id,
        auto_selectable=False,
    )


def _hamlib_audit_payload(audit: object) -> dict[str, object]:
    payload = {
        "targetRef": getattr(audit, "target_ref"),
        "operation": getattr(audit, "operation"),
        "status": getattr(audit, "status"),
        "durationMs": getattr(audit, "duration_ms"),
        "detail": getattr(audit, "detail"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _hamlib_validation_payload(results: list[object]) -> dict[str, object]:
    audit_records = [
        record for result in results for record in getattr(result, "audit", [])
    ]
    frequency_readable = any(
        getattr(record, "operation") == "read_frequency"
        and getattr(record, "status") == "ok"
        for record in audit_records
    )
    mode_readable = any(
        getattr(record, "operation") == "read_mode"
        and getattr(record, "status") == "ok"
        for record in audit_records
    )
    identity_readable = any(
        getattr(record, "operation") == "read_info"
        and getattr(record, "status") == "ok"
        for record in audit_records
    )
    high_confidence = any(
        getattr(candidate, "confidence") == "high"
        for result in results
        for candidate in getattr(result, "candidates", [])
    )
    if high_confidence and frequency_readable and mode_readable:
        status = "confirmed"
    elif frequency_readable or mode_readable or identity_readable:
        status = "partial"
    else:
        status = "unconfirmed"

    return {
        "status": status,
        "readOnly": True,
        "safeOperations": ["read_info", "read_frequency", "read_mode"],
        "targetRefs": [getattr(result, "target_ref") for result in results],
        "frequencyReadable": frequency_readable,
        "modeReadable": mode_readable,
        "identityEvidence": "available" if identity_readable else "unconfirmed",
        "audit": [_hamlib_audit_payload(record) for record in audit_records],
    }


def build_hamlib_discovery_payload(
    *,
    catalog: Any,
    serial_radios: list[dict[str, object]],
    serial_scan_enabled: bool,
    candidates_requested: bool,
    validation_results: list[object],
) -> dict[str, object]:
    """Build the stable Hamlib-assisted discovery JSON payload.

    Promoted to the public ``rigplane.backends.discovery`` surface (MOR-911)
    so that rigplane-pro and other consumers can import from a stable path
    instead of reaching into ``rigplane.cli._discover_hamlib``.

    Args:
        catalog: A Hamlib model catalog object (with ``degraded_reason`` and
            ``models`` attributes).
        serial_radios: List of serial radio dicts from discovery.
        serial_scan_enabled: Whether serial scanning was active.
        candidates_requested: Whether to include candidate entries in the payload.
        validation_results: Probe/validation result objects.

    Returns:
        A dict conforming to the ``rigplane.discovery.hamlib.v1`` schema.
    """
    messages: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []

    if catalog.degraded_reason is not None:
        messages.append(
            {
                "severity": "warning",
                "code": "hamlibCatalogUnavailable",
                "message": catalog.degraded_reason,
            }
        )

    if candidates_requested:
        if serial_scan_enabled and not serial_radios:
            messages.append(
                {
                    "severity": "info",
                    "code": "noSerialCandidates",
                    "message": "No serial radio candidates were found.",
                }
            )
        elif not serial_scan_enabled:
            messages.append(
                {
                    "severity": "info",
                    "code": "serialScanSkipped",
                    "message": "Serial scanning was skipped by --lan-only.",
                }
            )
        for index, serial in enumerate(serial_radios, start=1):
            candidates.append(
                _build_hamlib_serial_candidate(
                    serial,
                    catalog=catalog,
                    candidate_id=f"hamlib-serial-{index}",
                )
            )

    for result_index, result in enumerate(validation_results, start=1):
        for candidate_index, candidate in enumerate(
            getattr(result, "candidates", []),
            start=1,
        ):
            candidates.append(
                _hamlib_candidate_payload(
                    candidate,
                    candidate_id=f"hamlib-validation-{result_index}-{candidate_index}",
                    auto_selectable=False,
                )
            )

    high_candidate_ids = [
        str(candidate["id"])
        for candidate in candidates
        if candidate.get("confidence") == "high"
    ]
    if len(high_candidate_ids) == 1:
        for candidate in candidates:
            candidate["autoSelectable"] = candidate["id"] == high_candidate_ids[0]

    payload: dict[str, object] = {
        "schema": "rigplane.discovery.hamlib.v1",
        "catalog": _hamlib_catalog_payload(catalog),
        "candidates": candidates,
        "messages": messages,
        "summary": {
            "candidateCount": len(candidates),
            "highConfidenceCount": len(high_candidate_ids),
            "autoSelectableCount": 1 if len(high_candidate_ids) == 1 else 0,
        },
    }
    if validation_results:
        payload["validation"] = _hamlib_validation_payload(validation_results)
    return payload


def _is_candidate(port: object) -> bool:
    """Return True if *port* is a plausible USB-connected radio serial port.

    Inclusion criteria (any one is sufficient):
    - The OS reports a USB vendor id for the port (``port.vid`` is set), or
    - The hardware id string contains ``"usb"`` (e.g. ``USB VID:PID=...``), or
    - The device path contains ``"usb"`` (case-insensitive).

    Exclusion criteria (checked first, take precedence):
    - Device path contains ``"bluetooth"`` (case-insensitive)
    - Device path contains ``"debug"``, ``"wlan"``, or ``"spi"`` (virtual ports)

    Anchoring on the USB *identity* rather than only the device-path substring
    matters cross-platform: USB serial radios do not all carry ``"usb"`` in
    their OS device name. macOS uses ``/dev/cu.usbserial-*`` / ``usbmodem*``
    (has ``"usb"``) and Linux ``/dev/ttyUSB*`` for FTDI/CP210x bridges, but
    CDC-ACM composite bridges enumerate as ``/dev/ttyACM*`` on Linux and
    ``COMx`` on Windows — neither contains ``"usb"``. The Xiegu X6200's WCH
    CH342 is exactly such a CDC-ACM device, so the old device-name-only filter
    hard-skipped it on Linux/Windows before any CI-V probe could run (MOR-224).

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

    if getattr(port, "vid", None) is not None:
        return True
    hwid_lower = str(getattr(port, "hwid", "") or "").lower()
    if "usb" in hwid_lower:
        return True
    return "usb" in device_lower
