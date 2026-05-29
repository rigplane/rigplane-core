"""Tests for serial port enumeration, candidate filtering, and CI-V probing."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from functools import partial
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from rigplane.discovery import (
    CivProbeResult,
    RadioDiscoveryResult,
    SerialPortCandidate,
    _is_candidate,
    _parse_probe_response,
    build_setup_discovery_payload,
    _parse_yaesu_id_response,
    dedupe_radios,
    discover_serial_radios,
    enumerate_serial_ports,
    probe_serial_civ,
    probe_serial_yaesu_cat,
)
from rigplane.usb_audio_resolve import AudioDeviceMapping


@contextmanager
def _fast_probes():
    """Patch probe_serial_civ / probe_serial_yaesu_cat to use a single baud and
    tiny timeout. Cuts ~4 s (4 bauds × 1.0 s) per miss path to milliseconds.

    Used by ``TestDiscoverSerialRadios`` — those tests only care about the
    probe outcome, not about baud sweeping or real timeout values.
    """
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "rigplane.discovery.probe_serial_civ",
                partial(probe_serial_civ, baud_rates=[19200], timeout=0.01),
            )
        )
        stack.enter_context(
            patch(
                "rigplane.discovery.probe_serial_yaesu_cat",
                partial(probe_serial_yaesu_cat, baud_rates=[38400], timeout=0.01),
            )
        )
        yield


# ---------------------------------------------------------------------------
# Fake serial transport helpers for CI-V probe tests
# ---------------------------------------------------------------------------


class _FakeReader:
    def __init__(self, chunks: list[bytes]) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        for chunk in chunks:
            self._queue.put_nowait(chunk)

    async def read(self, n: int) -> bytes:
        return await self._queue.get()


class _FakeWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(bytes(data))

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


def _make_open(reader: _FakeReader, writer: _FakeWriter):
    async def _open(*, url: str, baudrate: int, **_kw: object):
        return reader, writer

    return _open


_IC7610_RESPONSE = bytes([0xFE, 0xFE, 0xE0, 0x98, 0x19, 0x00, 0x01, 0x06, 0xFD])
# IC-705 / X6200 share CI-V address 0xA4; the wire response is identical
# at the address level — disambiguation is by USB hwid, not CI-V payload.
_IC705_RESPONSE = bytes([0xFE, 0xFE, 0xE0, 0xA4, 0x19, 0x00, 0x01, 0x05, 0xFD])
_PROBE_CMD = bytes([0xFE, 0xFE, 0x00, 0xE0, 0x19, 0x00, 0xFD])


# ---------------------------------------------------------------------------
# CI-V probe tests
# ---------------------------------------------------------------------------


class TestProbeSerialCiv:
    @pytest.mark.asyncio
    async def test_success_at_first_baud(self) -> None:
        reader = _FakeReader([_IC7610_RESPONSE])
        writer = _FakeWriter()
        result = await probe_serial_civ(
            "/dev/ttyUSB0",
            baud_rates=[19200, 9600],
            timeout=0.1,
            _open_serial=_make_open(reader, writer),
        )
        assert isinstance(result, CivProbeResult)
        assert result.port == "/dev/ttyUSB0"
        assert result.baud == 19200
        assert result.address == 0x98
        assert result.model_id == bytes([0x01, 0x06])

    @pytest.mark.asyncio
    async def test_timeout_tries_next_baud(self) -> None:
        call_count = 0

        async def _open(*, url: str, baudrate: int, **_kw: object):
            nonlocal call_count
            call_count += 1
            if baudrate == 19200:
                return _FakeReader([]), _FakeWriter()
            return _FakeReader([_IC7610_RESPONSE]), _FakeWriter()

        result = await probe_serial_civ(
            "/dev/ttyUSB0",
            baud_rates=[19200, 9600],
            timeout=0.05,
            _open_serial=_open,
        )
        assert result is not None
        assert result.baud == 9600
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_invalid_response_returns_none(self) -> None:
        garbage = bytes([0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08])
        reader = _FakeReader([garbage])
        writer = _FakeWriter()
        result = await probe_serial_civ(
            "/dev/ttyUSB0",
            baud_rates=[19200],
            timeout=0.1,
            _open_serial=_make_open(reader, writer),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_all_bauds_fail_returns_none(self) -> None:
        async def _open(*, url: str, baudrate: int, **_kw: object):
            return _FakeReader([]), _FakeWriter()

        result = await probe_serial_civ(
            "/dev/ttyUSB0",
            baud_rates=[19200, 9600],
            timeout=0.05,
            _open_serial=_open,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_port_busy_returns_none(self) -> None:
        async def _open(*, url: str, baudrate: int, **_kw: object):
            raise OSError("Resource busy")

        result = await probe_serial_civ(
            "/dev/ttyUSB0",
            baud_rates=[19200, 9600],
            timeout=0.1,
            _open_serial=_open,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_echoed_command_before_response(self) -> None:
        data = _PROBE_CMD + _IC7610_RESPONSE
        reader = _FakeReader([data])
        writer = _FakeWriter()
        result = await probe_serial_civ(
            "/dev/ttyUSB0",
            baud_rates=[19200],
            timeout=0.1,
            _open_serial=_make_open(reader, writer),
        )
        assert result is not None
        assert result.address == 0x98
        assert result.model_id == bytes([0x01, 0x06])

    @pytest.mark.asyncio
    async def test_sends_correct_civ_command(self) -> None:
        reader = _FakeReader([_IC7610_RESPONSE])
        writer = _FakeWriter()
        await probe_serial_civ(
            "/dev/ttyUSB0",
            baud_rates=[19200],
            timeout=0.1,
            _open_serial=_make_open(reader, writer),
        )
        assert writer.written[0] == _PROBE_CMD

    @pytest.mark.asyncio
    async def test_closes_writer_after_success(self) -> None:
        reader = _FakeReader([_IC7610_RESPONSE])
        writer = _FakeWriter()
        await probe_serial_civ(
            "/dev/ttyUSB0",
            baud_rates=[19200],
            timeout=0.1,
            _open_serial=_make_open(reader, writer),
        )
        assert writer.closed is True

    @pytest.mark.asyncio
    async def test_default_baud_rates_used_when_none_specified(self) -> None:
        seen_bauds: list[int] = []

        async def _open(*, url: str, baudrate: int, **_kw: object):
            seen_bauds.append(baudrate)
            return _FakeReader([]), _FakeWriter()

        await probe_serial_civ("/dev/ttyUSB0", timeout=0.01, _open_serial=_open)
        assert seen_bauds == [19200, 9600, 115200, 4800]


class TestParseProbeResponse:
    def test_valid_ic7610_response(self) -> None:
        result = _parse_probe_response("/dev/x", 19200, _IC7610_RESPONSE)
        assert result is not None
        assert result.address == 0x98
        assert result.model_id == bytes([0x01, 0x06])

    def test_no_preamble_returns_none(self) -> None:
        assert _parse_probe_response("/dev/x", 19200, bytes(9)) is None

    def test_too_short_after_preamble_returns_none(self) -> None:
        data = bytes([0xFE, 0xFE, 0xE0, 0x98])
        assert _parse_probe_response("/dev/x", 19200, data) is None

    def test_wrong_command_byte_returns_none(self) -> None:
        # byte[4] should be 0x19; use 0x00 here
        data = bytes([0xFE, 0xFE, 0xE0, 0x98, 0x00, 0x00, 0x01, 0x06, 0xFD])
        assert _parse_probe_response("/dev/x", 19200, data) is None

    def test_no_terminator_returns_none(self) -> None:
        data = bytes([0xFE, 0xFE, 0xE0, 0x98, 0x19, 0x00, 0x01, 0x06])
        assert _parse_probe_response("/dev/x", 19200, data) is None


def _make_port(
    device: str,
    description: str = "",
    hwid: str | None = None,
    **metadata: object,
) -> SimpleNamespace:
    return SimpleNamespace(
        device=device,
        description=description,
        hwid=hwid,
        **metadata,
    )


class TestIsCandidate:
    def test_usb_serial_included(self) -> None:
        port = _make_port("/dev/ttyUSB0", "USB Serial", "USB VID:PID=10C4:EA60")
        assert _is_candidate(port) is True

    def test_usb_modem_included(self) -> None:
        # macOS USB CDC devices appear as /dev/tty.usbmodem*
        port = _make_port(
            "/dev/tty.usbmodem14101", "USB Modem", "USB VID:PID=0403:6001"
        )
        assert _is_candidate(port) is True

    def test_usb_upper_case_included(self) -> None:
        port = _make_port("/dev/ttyUSB1", "USB-Serial CH340", "USB VID:PID=1A86:7523")
        assert _is_candidate(port) is True

    def test_bluetooth_excluded(self) -> None:
        port = _make_port("/dev/tty.Bluetooth-Incoming-Port", "Bluetooth")
        assert _is_candidate(port) is False

    def test_bluetooth_lowercase_excluded(self) -> None:
        port = _make_port("/dev/rfcomm0", "Bluetooth Serial")
        assert _is_candidate(port) is False

    def test_debug_virtual_excluded(self) -> None:
        port = _make_port("/dev/ttydebug0", "Debug UART")
        assert _is_candidate(port) is False

    def test_wlan_virtual_excluded(self) -> None:
        port = _make_port("/dev/ttywlan0", "WLAN UART")
        assert _is_candidate(port) is False

    def test_spi_virtual_excluded(self) -> None:
        port = _make_port("/dev/ttyspi0", "SPI bridge")
        assert _is_candidate(port) is False

    def test_plain_serial_without_usb_excluded(self) -> None:
        port = _make_port("/dev/ttyS0", "Standard Serial")
        assert _is_candidate(port) is False

    # MOR-224: CDC-ACM bridges (e.g. the X6200's WCH CH342) enumerate under
    # device names that do NOT contain "usb" on Linux (/dev/ttyACM*) and
    # Windows (COMx). They must still be accepted via their USB identity.
    def test_x6200_linux_ttyacm_included_by_vid(self) -> None:
        port = _make_port(
            "/dev/ttyACM0",
            "USB Dual_Serial",
            "USB VID:PID=1A86:55D2 LOCATION=1-1.2.4",
            vid=0x1A86,
            pid=0x55D2,
            product="USB Dual_Serial",
        )
        assert _is_candidate(port) is True

    def test_x6200_windows_com_included_by_vid(self) -> None:
        port = _make_port(
            "COM3",
            "USB Dual_Serial",
            "USB VID:PID=1A86:55D2",
            vid=0x1A86,
            pid=0x55D2,
            product="USB Dual_Serial",
        )
        assert _is_candidate(port) is True

    def test_generic_usb_radio_windows_com_included_by_vid(self) -> None:
        # Any USB serial radio on Windows surfaces as COMx (no "usb" in name).
        port = _make_port(
            "COM4", "Silicon Labs CP210x", "USB VID:PID=10C4:EA60", vid=0x10C4
        )
        assert _is_candidate(port) is True

    def test_cdc_acm_included_by_hwid_when_vid_absent(self) -> None:
        # Some OS/permission setups don't surface vid via pyserial; the
        # "USB VID:PID=" hwid string is the robust-identity fallback.
        port = _make_port("/dev/ttyACM0", "", "USB VID:PID=1A86:55D2")
        assert _is_candidate(port) is True

    def test_bluetooth_with_vid_still_excluded(self) -> None:
        # Name-based exclusions take precedence over the USB-identity accept.
        port = _make_port("/dev/tty.Bluetooth-Incoming-Port", "Bluetooth", vid=0x1234)
        assert _is_candidate(port) is False


class TestEnumerateSerialPorts:
    def test_usb_port_returned(self) -> None:
        port = _make_port(
            "/dev/ttyUSB0",
            "USB Serial",
            "USB VID:PID=10C4:EA60",
            vid=0x10C4,
            pid=0xEA60,
            manufacturer="Silicon Labs",
            product="CP210x USB to UART Bridge",
            serial_number="0001",
        )
        with patch("serial.tools.list_ports.comports", return_value=[port]):
            result = enumerate_serial_ports()
        assert len(result) == 1
        assert result[0] == SerialPortCandidate(
            device="/dev/ttyUSB0",
            description="USB Serial",
            hwid="USB VID:PID=10C4:EA60",
            vid=0x10C4,
            pid=0xEA60,
            manufacturer="Silicon Labs",
            product="CP210x USB to UART Bridge",
            serial_number="0001",
        )

    def test_missing_usb_metadata_attrs_are_optional(self) -> None:
        port = _make_port("/dev/ttyUSB0", "USB Serial", "USB VID:PID=10C4:EA60")
        with patch("serial.tools.list_ports.comports", return_value=[port]):
            result = enumerate_serial_ports()
        assert result[0].vid is None
        assert result[0].pid is None
        assert result[0].manufacturer is None
        assert result[0].product is None
        assert result[0].serial_number is None

    def test_bluetooth_excluded(self) -> None:
        port = _make_port("/dev/tty.Bluetooth-Incoming-Port", "Bluetooth")
        with patch("serial.tools.list_ports.comports", return_value=[port]):
            result = enumerate_serial_ports()
        assert result == []

    def test_virtual_ports_excluded(self) -> None:
        ports = [
            _make_port("/dev/ttydebug0", "Debug"),
            _make_port("/dev/ttywlan0", "WLAN"),
            _make_port("/dev/ttyspi0", "SPI"),
        ]
        with patch("serial.tools.list_ports.comports", return_value=ports):
            result = enumerate_serial_ports()
        assert result == []

    def test_empty_list(self) -> None:
        with patch("serial.tools.list_ports.comports", return_value=[]):
            result = enumerate_serial_ports()
        assert result == []

    def test_mixed_ports_only_usb_returned(self) -> None:
        ports = [
            _make_port("/dev/ttyUSB0", "USB Serial", "USB VID:PID=10C4:EA60"),
            _make_port("/dev/tty.Bluetooth-Incoming-Port", "Bluetooth"),
            _make_port("/dev/ttyS0", "Standard"),
        ]
        with patch("serial.tools.list_ports.comports", return_value=ports):
            result = enumerate_serial_ports()
        assert len(result) == 1
        assert result[0].device == "/dev/ttyUSB0"

    def test_returns_list_of_candidates(self) -> None:
        with patch("serial.tools.list_ports.comports", return_value=[]):
            result = enumerate_serial_ports()
        assert isinstance(result, list)

    def test_x6200_cdc_acm_returned_as_candidate(self) -> None:
        # MOR-224: the X6200's CH342 on Linux (/dev/ttyACM*) must enumerate
        # as a candidate even though its device name has no "usb".
        port = _make_port(
            "/dev/ttyACM0",
            "USB Dual_Serial",
            "USB VID:PID=1A86:55D2 LOCATION=1-1.2.4",
            vid=0x1A86,
            pid=0x55D2,
            product="USB Dual_Serial",
            manufacturer=None,
            serial_number="5891018109",
        )
        with patch("serial.tools.list_ports.comports", return_value=[port]):
            result = enumerate_serial_ports()
        assert len(result) == 1
        assert result[0].device == "/dev/ttyACM0"
        assert result[0].vid == 0x1A86
        assert result[0].pid == 0x55D2


# ---------------------------------------------------------------------------
# dedupe_radios tests
# ---------------------------------------------------------------------------


class TestDedupeRadios:
    def test_same_radio_lan_and_serial_grouped(self) -> None:
        lan = [{"model": "IC-7610", "address": 0x98, "host": "192.168.1.100"}]
        serial = [
            {"model": "IC-7610", "address": 0x98, "port": "/dev/ttyUSB0", "baud": 19200}
        ]

        result = dedupe_radios(lan, serial)

        assert len(result) == 1
        assert result[0]["model"] == "IC-7610"
        assert len(result[0]["lan"]) == 1
        assert len(result[0]["serial"]) == 1

    def test_different_radios_stay_separate(self) -> None:
        lan = [{"model": "IC-7610", "address": 0x98, "host": "192.168.1.100"}]
        serial = [
            {"model": "IC-705", "address": 0xA4, "port": "/dev/ttyUSB0", "baud": 19200}
        ]

        result = dedupe_radios(lan, serial)

        assert len(result) == 2

    def test_lan_only_no_serial_section(self) -> None:
        lan = [{"model": "IC-7610", "address": 0x98, "host": "192.168.1.100"}]
        serial: list[dict] = []

        result = dedupe_radios(lan, serial)

        assert len(result) == 1
        assert len(result[0]["serial"]) == 0
        assert len(result[0]["lan"]) == 1

    def test_serial_only_no_lan_section(self) -> None:
        lan: list[dict] = []
        serial = [
            {"model": "IC-705", "address": 0xA4, "port": "/dev/ttyUSB0", "baud": 19200}
        ]

        result = dedupe_radios(lan, serial)

        assert len(result) == 1
        assert len(result[0]["lan"]) == 0
        assert len(result[0]["serial"]) == 1

    def test_empty_both(self) -> None:
        assert dedupe_radios([], []) == []

    def test_two_lan_same_radio_merged(self) -> None:
        # Same model+address from two LAN entries — merged (unusual but defensive)
        lan = [
            {"model": "IC-7610", "address": 0x98, "host": "192.168.1.100"},
            {"model": "IC-7610", "address": 0x98, "host": "192.168.1.101"},
        ]
        result = dedupe_radios(lan, [])
        assert len(result) == 1
        assert len(result[0]["lan"]) == 2

    def test_lan_without_model_not_merged_with_serial(self) -> None:
        # LAN entry has no model/address → cannot be deduped → stays separate
        lan = [{"host": "192.168.1.100"}]
        serial = [
            {"model": "IC-7610", "address": 0x98, "port": "/dev/ttyUSB0", "baud": 19200}
        ]

        result = dedupe_radios(lan, serial)

        assert len(result) == 2

    def test_multiple_lan_without_model_each_separate(self) -> None:
        # Two unidentified LAN radios → each is its own entry
        lan = [{"host": "192.168.1.100"}, {"host": "192.168.1.101"}]
        result = dedupe_radios(lan, [])
        assert len(result) == 2

    def test_return_type_is_list(self) -> None:
        result = dedupe_radios([], [])
        assert isinstance(result, list)

    def test_result_entry_has_required_keys(self) -> None:
        lan = [{"model": "IC-7610", "address": 0x98, "host": "192.168.1.100"}]
        result = dedupe_radios(lan, [])
        assert "model" in result[0]
        assert "lan" in result[0]
        assert "serial" in result[0]


def test_build_setup_discovery_payload_is_stable_and_credential_free() -> None:
    grouped = [
        {
            "model": "IC-7610",
            "lan": [
                {
                    "host": "192.168.55.40",
                    "remote_id": 0x12345678,
                    "user": "must-not-leak",
                    "password": "must-not-leak",
                }
            ],
            "serial": [
                {
                    "port": "/dev/cu.usbmodem7610",
                    "protocol": "civ",
                    "model": "IC-7610",
                    "profile_id": "icom_ic7610",
                    "baudrate": 115200,
                    "address": 0x98,
                    "description": "IC-7610 USB",
                    "hwid": "USB VID:PID=10C4:EA60",
                    "vid": 0x10C4,
                    "pid": 0xEA60,
                    "manufacturer": "Silicon Labs",
                    "product": "CP210x USB to UART Bridge",
                    "serial_number": "0001",
                    "token": "must-not-leak",
                    "hostname": "private-host.local",
                }
            ],
        }
    ]

    payload = build_setup_discovery_payload(grouped)

    assert payload["schema"] == "rigplane.discovery.v1"
    radio = payload["radios"][0]
    assert radio["model"] == "IC-7610"
    assert radio["connections"][0] == {
        "type": "lan",
        "backend": "lan",
        "label": "LAN 192.168.55.40",
        "host": "192.168.55.40",
        "remoteId": 0x12345678,
        "requiresCredentials": True,
    }
    assert radio["connections"][1]["type"] == "serial"
    assert radio["connections"][1]["description"] == "IC-7610 USB"
    assert radio["connections"][1]["hwid"] == "USB VID:PID=10C4:EA60"
    assert radio["connections"][1]["vid"] == 0x10C4
    assert radio["connections"][1]["pid"] == 0xEA60
    assert radio["connections"][1]["manufacturer"] == "Silicon Labs"
    assert radio["connections"][1]["product"] == "CP210x USB to UART Bridge"
    assert radio["connections"][1]["serialNumber"] == "0001"
    assert "password" not in str(payload).lower()
    assert "token" not in str(payload).lower()
    assert "private-host" not in str(payload).lower()
    assert payload["limitations"]["windowsUsbAudio"] == "manual-device-selection"


def test_setup_payload_preserves_usb_audio_metadata_without_credentials() -> None:
    grouped = [
        {
            "model": "IC-7610",
            "lan": [],
            "serial": [
                {
                    "port": "/dev/cu.usbserial-111120",
                    "protocol": "civ",
                    "model": "IC-7610",
                    "profile_id": "icom_ic7610",
                    "baudrate": 115200,
                    "address": 0x98,
                    "description": "Silicon Labs CP210x USB to UART Bridge",
                    "hwid": "USB VID:PID=10C4:EA60 LOCATION=1-1.2",
                    "usb_audio": {
                        "rx_device_index": 6,
                        "tx_device_index": 5,
                        "serial_port": "/dev/cu.usbserial-111120",
                        "location_prefix": 0x0111,
                    },
                    "user": "must-not-leak",
                    "password": "must-not-leak",
                    "private_key": "must-not-leak",
                }
            ],
        }
    ]

    payload = build_setup_discovery_payload(grouped)

    connection = payload["radios"][0]["connections"][0]
    assert connection["requiresCredentials"] is False
    assert connection["usbAudio"] == {
        "rxDeviceIndex": 6,
        "txDeviceIndex": 5,
        "serialPort": "/dev/cu.usbserial-111120",
        "locationPrefix": 0x0111,
    }
    assert "password" not in str(payload).lower()
    assert "private_key" not in str(payload).lower()


# ---------------------------------------------------------------------------
# Fake Yaesu CAT transport helpers
# ---------------------------------------------------------------------------


class _FakeCatTransport:
    """Minimal fake of YaesuCatTransport for probe tests."""

    def __init__(self, response: str | None, *, fail_connect: bool = False) -> None:
        self._response = response
        self._fail_connect = fail_connect
        self.connected = False
        self.closed = False
        self.queries: list[str] = []

    async def connect(self) -> None:
        if self._fail_connect:
            raise OSError("Resource busy")
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def query(self, command: str, *, timeout: float | None = None) -> str:
        self.queries.append(command)
        if self._response is None:
            from rigplane.backends.yaesu_cat.transport import CatTimeoutError

            raise CatTimeoutError("timeout")
        return self._response


def _make_yaesu_factory(response: str | None, *, fail_connect: bool = False):
    """Return a transport factory producing a _FakeCatTransport."""
    instances: list[_FakeCatTransport] = []

    def factory(**kwargs: object) -> _FakeCatTransport:
        t = _FakeCatTransport(response, fail_connect=fail_connect)
        instances.append(t)
        return t

    factory.instances = instances  # type: ignore[attr-defined]
    return factory


# ---------------------------------------------------------------------------
# _parse_yaesu_id_response tests
# ---------------------------------------------------------------------------


class TestParseYaesuIdResponse:
    def test_known_ftx1(self) -> None:
        result = _parse_yaesu_id_response("/dev/ttyUSB0", 38400, "ID0840")
        assert result is not None
        assert result.model == "FTX-1"
        assert result.profile_id == "yaesu_ftx1"
        assert result.protocol == "yaesu_cat"
        assert result.address == "0840"
        assert result.baudrate == 38400
        assert result.port == "/dev/ttyUSB0"

    def test_unknown_model_id(self) -> None:
        result = _parse_yaesu_id_response("/dev/ttyUSB0", 38400, "ID9999")
        assert result is not None
        assert result.model == "Yaesu (9999)"
        assert result.profile_id == ""
        assert result.address == "9999"

    def test_too_short(self) -> None:
        assert _parse_yaesu_id_response("/dev/x", 38400, "ID084") is None

    def test_too_long(self) -> None:
        assert _parse_yaesu_id_response("/dev/x", 38400, "ID08401") is None

    def test_wrong_prefix(self) -> None:
        assert _parse_yaesu_id_response("/dev/x", 38400, "FA0840") is None

    def test_empty_response(self) -> None:
        assert _parse_yaesu_id_response("/dev/x", 38400, "") is None


# ---------------------------------------------------------------------------
# probe_serial_yaesu_cat tests
# ---------------------------------------------------------------------------


class TestProbeSerialYaesuCat:
    @pytest.mark.asyncio
    async def test_success_ftx1(self) -> None:
        factory = _make_yaesu_factory("ID0840")
        result = await probe_serial_yaesu_cat(
            "/dev/ttyUSB0",
            baud_rates=[38400],
            timeout=0.1,
            _transport_factory=factory,
        )
        assert isinstance(result, RadioDiscoveryResult)
        assert result.model == "FTX-1"
        assert result.protocol == "yaesu_cat"
        assert result.baudrate == 38400
        assert result.address == "0840"

    @pytest.mark.asyncio
    async def test_success_at_second_baud(self) -> None:
        call_count = 0

        def factory(**kwargs: object) -> _FakeCatTransport:
            nonlocal call_count
            call_count += 1
            baud = kwargs.get("baudrate")
            if baud == 38400:
                return _FakeCatTransport(None)  # timeout
            return _FakeCatTransport("ID0840")

        result = await probe_serial_yaesu_cat(
            "/dev/ttyUSB0",
            baud_rates=[38400, 9600],
            timeout=0.1,
            _transport_factory=factory,
        )
        assert result is not None
        assert result.baudrate == 9600
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self) -> None:
        factory = _make_yaesu_factory(None)  # all timeouts
        result = await probe_serial_yaesu_cat(
            "/dev/ttyUSB0",
            baud_rates=[38400, 9600],
            timeout=0.1,
            _transport_factory=factory,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_port_busy_returns_none(self) -> None:
        factory = _make_yaesu_factory("ID0840", fail_connect=True)
        result = await probe_serial_yaesu_cat(
            "/dev/ttyUSB0",
            baud_rates=[38400],
            timeout=0.1,
            _transport_factory=factory,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_response_returns_none(self) -> None:
        factory = _make_yaesu_factory("GARBAGE")
        result = await probe_serial_yaesu_cat(
            "/dev/ttyUSB0",
            baud_rates=[38400],
            timeout=0.1,
            _transport_factory=factory,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_sends_id_command(self) -> None:
        factory = _make_yaesu_factory("ID0840")
        await probe_serial_yaesu_cat(
            "/dev/ttyUSB0",
            baud_rates=[38400],
            timeout=0.1,
            _transport_factory=factory,
        )
        assert factory.instances[0].queries == ["ID;"]

    @pytest.mark.asyncio
    async def test_closes_transport_after_success(self) -> None:
        factory = _make_yaesu_factory("ID0840")
        await probe_serial_yaesu_cat(
            "/dev/ttyUSB0",
            baud_rates=[38400],
            timeout=0.1,
            _transport_factory=factory,
        )
        assert factory.instances[0].closed is True

    @pytest.mark.asyncio
    async def test_closes_transport_on_timeout(self) -> None:
        factory = _make_yaesu_factory(None)
        await probe_serial_yaesu_cat(
            "/dev/ttyUSB0",
            baud_rates=[38400],
            timeout=0.1,
            _transport_factory=factory,
        )
        assert factory.instances[0].closed is True

    @pytest.mark.asyncio
    async def test_default_baud_rates(self) -> None:
        seen_bauds: list[int] = []

        def factory(**kwargs: object) -> _FakeCatTransport:
            seen_bauds.append(int(str(kwargs["baudrate"])))
            return _FakeCatTransport(None)

        await probe_serial_yaesu_cat(
            "/dev/ttyUSB0", timeout=0.01, _transport_factory=factory
        )
        assert seen_bauds == [38400, 9600, 115200]


# ---------------------------------------------------------------------------
# discover_serial_radios multi-protocol tests
# ---------------------------------------------------------------------------


def test_radio_discovery_result_legacy_positional_usb_audio_binding() -> None:
    usb_audio = {"rx_device_index": 6}

    result = RadioDiscoveryResult(
        "/dev/ttyUSB0",
        "civ",
        "IC-7610",
        "icom_ic7610",
        115200,
        0x98,
        "USB Serial",
        "USB VID:PID=10C4:EA60",
        usb_audio,
    )

    assert result.usb_audio is usb_audio
    assert result.vid is None
    assert result.pid is None


class TestDiscoverSerialRadios:
    @pytest.mark.asyncio
    async def test_civ_radio_detected(self) -> None:
        reader = _FakeReader([_IC7610_RESPONSE])
        writer = _FakeWriter()

        port = _make_port("/dev/ttyUSB0", "USB Serial", "USB VID:PID=10C4:EA60")
        with (
            _fast_probes(),
            patch("serial.tools.list_ports.comports", return_value=[port]),
        ):
            results = await discover_serial_radios(
                _open_serial=_make_open(reader, writer),
            )

        assert len(results) == 1
        r = results[0]
        assert isinstance(r, RadioDiscoveryResult)
        assert r.protocol == "civ"
        assert r.model == "IC-7610"
        assert r.profile_id == "icom_ic7610"
        assert r.address == 0x98
        assert r.description == "USB Serial"
        assert r.hwid == "USB VID:PID=10C4:EA60"

    @pytest.mark.asyncio
    async def test_civ_radio_preserves_serial_usb_metadata(self) -> None:
        reader = _FakeReader([_IC7610_RESPONSE])
        writer = _FakeWriter()

        port = _make_port(
            "/dev/ttyUSB0",
            "USB Serial",
            "USB VID:PID=10C4:EA60",
            vid=0x10C4,
            pid=0xEA60,
            manufacturer="Silicon Labs",
            product="CP210x USB to UART Bridge",
            serial_number="0001",
        )
        with (
            _fast_probes(),
            patch("serial.tools.list_ports.comports", return_value=[port]),
        ):
            results = await discover_serial_radios(
                _open_serial=_make_open(reader, writer),
            )

        r = results[0]
        assert r.vid == 0x10C4
        assert r.pid == 0xEA60
        assert r.manufacturer == "Silicon Labs"
        assert r.product == "CP210x USB to UART Bridge"
        assert r.serial_number == "0001"

    @pytest.mark.asyncio
    async def test_civ_addr_0xA4_with_xiegu_hwid_resolves_to_x6200(self) -> None:
        """MOR-170: at the shared 0xA4 address, the WCH CH342 VID:PID
        + ``USB Dual_Serial`` product name must classify as Xiegu X6200,
        not the address-default IC-705.
        """
        reader = _FakeReader([_IC705_RESPONSE])
        writer = _FakeWriter()

        port = _make_port(
            "/dev/cu.usbmodem58910181093",
            "USB Dual_Serial",
            "USB VID:PID=1A86:55D2 SER=5891018109 LOCATION=0-1.2.4",
            vid=0x1A86,
            pid=0x55D2,
            manufacturer=None,
            product="USB Dual_Serial",
            serial_number="5891018109",
        )
        with (
            _fast_probes(),
            patch("serial.tools.list_ports.comports", return_value=[port]),
        ):
            results = await discover_serial_radios(
                _open_serial=_make_open(reader, writer),
            )

        assert len(results) == 1
        r = results[0]
        assert r.protocol == "civ"
        assert r.address == 0xA4
        assert r.model == "X6200"
        assert r.profile_id == "xiegu_x6200"
        # USB metadata still surfaced unmodified
        assert r.vid == 0x1A86
        assert r.pid == 0x55D2
        assert r.product == "USB Dual_Serial"

    @pytest.mark.asyncio
    async def test_civ_addr_0xA4_without_xiegu_hwid_stays_ic705(self) -> None:
        """MOR-170: at 0xA4, ports that do NOT match the X6200 fingerprint
        must keep classifying as IC-705 — the existing behaviour for
        actual IC-705 owners is preserved.
        """
        reader = _FakeReader([_IC705_RESPONSE])
        writer = _FakeWriter()

        port = _make_port(
            "/dev/ttyUSB0",
            "USB Serial",
            "USB VID:PID=0C26:0036",
            vid=0x0C26,
            pid=0x0036,
            manufacturer="Icom Inc.",
            product="IC-705",
            serial_number="0001",
        )
        with (
            _fast_probes(),
            patch("serial.tools.list_ports.comports", return_value=[port]),
        ):
            results = await discover_serial_radios(
                _open_serial=_make_open(reader, writer),
            )

        assert len(results) == 1
        r = results[0]
        assert r.address == 0xA4
        assert r.model == "IC-705"
        assert r.profile_id == "icom_ic705"

    @pytest.mark.asyncio
    async def test_civ_addr_0xA4_xiegu_product_name_fallback(self) -> None:
        """MOR-170: even when pyserial does not surface VID/PID (some OSes /
        permission setups), a ``product`` field containing ``"USB Dual_Serial"``
        is enough to classify as X6200.
        """
        reader = _FakeReader([_IC705_RESPONSE])
        writer = _FakeWriter()

        port = _make_port(
            "/dev/cu.usbmodem58910181093",
            "USB Dual_Serial",
            "USB Dual_Serial",
            vid=None,
            pid=None,
            product="USB Dual_Serial",
        )
        with (
            _fast_probes(),
            patch("serial.tools.list_ports.comports", return_value=[port]),
        ):
            results = await discover_serial_radios(
                _open_serial=_make_open(reader, writer),
            )

        assert results[0].model == "X6200"
        assert results[0].profile_id == "xiegu_x6200"

    @pytest.mark.asyncio
    async def test_civ_radio_preserves_usb_audio_resolution_metadata(self) -> None:
        reader = _FakeReader([_IC7610_RESPONSE])
        writer = _FakeWriter()

        port = _make_port("/dev/ttyUSB0", "USB Serial", "USB VID:PID=10C4:EA60")
        mapping = AudioDeviceMapping(
            rx_device_index=6,
            tx_device_index=5,
            serial_port="/dev/ttyUSB0",
            location_prefix=None,
        )
        with (
            _fast_probes(),
            patch("serial.tools.list_ports.comports", return_value=[port]),
            patch(
                "rigplane.discovery.resolve_audio_for_serial_port",
                return_value=mapping,
            ),
        ):
            results = await discover_serial_radios(
                _open_serial=_make_open(reader, writer),
            )

        assert results[0].usb_audio == {
            "rx_device_index": 6,
            "tx_device_index": 5,
            "serial_port": "/dev/ttyUSB0",
            "location_prefix": None,
        }

    @pytest.mark.asyncio
    async def test_yaesu_radio_detected(self) -> None:
        # CI-V probe times out, Yaesu CAT succeeds
        async def _civ_open(*, url: str, baudrate: int, **_kw: object):
            return _FakeReader([]), _FakeWriter()

        yaesu_factory = _make_yaesu_factory("ID0840")

        port = _make_port(
            "/dev/ttyUSB0",
            "USB Serial",
            "USB VID:PID=0403:6001",
            vid=0x0403,
            pid=0x6001,
            manufacturer="FTDI",
            product="USB Serial Converter",
            serial_number="YAESU1",
        )
        with (
            _fast_probes(),
            patch("serial.tools.list_ports.comports", return_value=[port]),
        ):
            results = await discover_serial_radios(
                _open_serial=_civ_open,
                _yaesu_transport_factory=yaesu_factory,
            )

        assert len(results) == 1
        r = results[0]
        assert r.protocol == "yaesu_cat"
        assert r.model == "FTX-1"
        assert r.profile_id == "yaesu_ftx1"
        assert r.vid == 0x0403
        assert r.pid == 0x6001
        assert r.manufacturer == "FTDI"
        assert r.product == "USB Serial Converter"
        assert r.serial_number == "YAESU1"

    @pytest.mark.asyncio
    async def test_no_radio_on_port(self) -> None:
        async def _civ_open(*, url: str, baudrate: int, **_kw: object):
            return _FakeReader([]), _FakeWriter()

        yaesu_factory = _make_yaesu_factory(None)

        port = _make_port("/dev/ttyUSB0", "USB Serial", "USB VID:PID=0403:6001")
        with (
            _fast_probes(),
            patch("serial.tools.list_ports.comports", return_value=[port]),
        ):
            results = await discover_serial_radios(
                _open_serial=_civ_open,
                _yaesu_transport_factory=yaesu_factory,
            )

        assert results == []

    @pytest.mark.asyncio
    async def test_mixed_civ_and_yaesu(self) -> None:
        # Two ports: one CI-V, one Yaesu
        civ_reader = _FakeReader([_IC7610_RESPONSE])
        civ_writer = _FakeWriter()

        call_n = 0

        async def _civ_open(*, url: str, baudrate: int, **_kw: object):
            nonlocal call_n
            call_n += 1
            # Second port group: all CI-V bauds timeout
            if url == "/dev/ttyUSB1":
                return _FakeReader([]), _FakeWriter()
            return civ_reader, civ_writer

        yaesu_factory = _make_yaesu_factory("ID0840")

        ports = [
            _make_port("/dev/ttyUSB0", "USB Serial"),
            _make_port("/dev/ttyUSB1", "USB Serial"),
        ]
        with (
            _fast_probes(),
            patch("serial.tools.list_ports.comports", return_value=ports),
        ):
            results = await discover_serial_radios(
                _open_serial=_civ_open,
                _yaesu_transport_factory=yaesu_factory,
            )

        assert len(results) == 2
        protocols = {r.protocol for r in results}
        assert protocols == {"civ", "yaesu_cat"}
