"""Tests for USB audio device resolution from serial port topology."""

from __future__ import annotations

import textwrap
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from rigplane.usb_audio_resolve import (
    AudioDeviceMapping,
    _extract_tty_suffix,
    _find_audio_codec_locations,
    _find_serial_location,
    _is_usb_audio_codec,
    _resolve_macos,
    resolve_audio_for_serial_port,
)

# ---------------------------------------------------------------------------
# Fixtures — minimal IORegistry snippets for testing
# ---------------------------------------------------------------------------

# Simulates two Icom radios: one at hub prefix 0x2014, one at 0x0111
IOREG_TWO_RADIOS = textwrap.dedent("""\
    | +-o USB Audio CODEC@20144000  <class IOUSBHostDevice, id 0x1000045f6>
    | |   "locationID" = 538198016
    | |   "USB Product Name" = "USB Audio CODEC"
    | |   "iSerialNumber" = 0
    | +-o CP2102 USB to UART Bridge Controller@20141000
    | |   "locationID" = 538185728
    | |   "USB Serial Number" = "IC-7300 02040999"
    | | +-o AppleUSBSLCOM
    | |   | "IOTTYSuffix" = "201410"
    | |   | "IOCalloutDevice" = "/dev/cu.usbserial-201410"
    | +-o USB Audio CODEC@01111400  <class IOUSBHostDevice, id 0x1000045b4>
    | |   "locationID" = 17895424
    | |   "USB Product Name" = "USB Audio CODEC"
    | |   "iSerialNumber" = 0
    | +-o CP2102 USB to UART Bridge Controller@01112000
    | |   "locationID" = 17895936
    | |   "USB Serial Number" = "IC-7610 21001793 A"
    | | +-o AppleUSBSLCOM
    | |   | "IOTTYSuffix" = "111120"
    | |   | "IOCalloutDevice" = "/dev/cu.usbserial-111120"
""")

# Single radio only
IOREG_SINGLE_RADIO = textwrap.dedent("""\
    | +-o USB Audio CODEC@20144000  <class IOUSBHostDevice, id 0x100004500>
    | |   "locationID" = 538198016
    | +-o CP2102 USB to UART Bridge Controller@20141000
    | |   "locationID" = 538185728
    | | +-o AppleUSBSLCOM
    | |   | "IOTTYSuffix" = "201410"
""")

# No audio devices
IOREG_NO_AUDIO = textwrap.dedent("""\
    | +-o CP2102 USB to UART Bridge Controller@20141000
    | |   "locationID" = 538185728
    | | +-o AppleUSBSLCOM
    | |   | "IOTTYSuffix" = "201410"
""")

# Three radios to test scalability
IOREG_THREE_RADIOS = textwrap.dedent("""\
    | +-o USB Audio CODEC@01111400  <class IOUSBHostDevice>
    | +-o CP2102@01112000
    | |   "locationID" = 17895936
    | | +-o AppleUSBSLCOM
    | |   | "IOTTYSuffix" = "111120"
    | +-o USB Audio CODEC@20144000  <class IOUSBHostDevice>
    | +-o CP2102@20141000
    | |   "locationID" = 538185728
    | | +-o AppleUSBSLCOM
    | |   | "IOTTYSuffix" = "201410"
    | +-o USB Audio CODEC@30144000  <class IOUSBHostDevice>
    | +-o CP2102@30141000
    | |   "locationID" = 806621184
    | | +-o AppleUSBSLCOM
    | |   | "IOTTYSuffix" = "301410"
""")


# Yaesu FTX-1 ("USB Audio Device") alongside Icom IC-7610 ("USB Audio CODEC")
# FTX-1: audio @ 0x20132200, serial HRI @ 0x20131000 (same hub prefix 0x2013)
IOREG_YAESU_AND_ICOM = textwrap.dedent("""\
    | +-o USB Audio Device@20132200  <class IOUSBHostDevice, id 0x1000d4711>
    | |   "locationID" = 538124800
    | |   "USB Product Name" = "USB Audio Device"
    | +-o YAESU HRI USB I/F@20131000  <class IOUSBHostDevice, id 0x1000d470e>
    | |   "locationID" = 538120192
    | |   "USB Vendor Name" = "YAESUMUSEN"
    | | +-o AppleUSBSLCOM
    | |   | "IOTTYSuffix" = "01AE340D0"
    | |   | "IOCalloutDevice" = "/dev/cu.usbserial-01AE340D0"
    | +-o USB Audio CODEC@01111400  <class IOUSBHostDevice, id 0x1000a9ab5>
    | |   "locationID" = 17895424
    | |   "USB Product Name" = "USB Audio CODEC"
    | +-o CP2102 USB to UART Bridge Controller@01112000
    | |   "locationID" = 17895936
    | | +-o AppleUSBSLCOM
    | |   | "IOTTYSuffix" = "111120"
    | |   | "IOCalloutDevice" = "/dev/cu.usbserial-111120"
""")

# Yaesu FTX-1 only (no Icom)
# audio @ 0x20132200, serial HRI @ 0x20131000 (same hub prefix 0x2013)
IOREG_YAESU_ONLY = textwrap.dedent("""\
    | +-o USB Audio Device@20132200  <class IOUSBHostDevice, id 0x1000d4711>
    | |   "locationID" = 538124800
    | |   "USB Product Name" = "USB Audio Device"
    | +-o YAESU HRI USB I/F@20131000  <class IOUSBHostDevice, id 0x1000d470e>
    | |   "locationID" = 538120192
    | | +-o AppleUSBSLCOM
    | |   | "IOTTYSuffix" = "01AE340D0"
    | |   | "IOCalloutDevice" = "/dev/cu.usbserial-01AE340D0"
""")


def _make_mock_sd_mixed(
    usb_codec_count: int = 1,
    usb_device_count: int = 1,
) -> MagicMock:
    """Create a mock sounddevice with both "USB Audio CODEC" and "USB Audio Device" pairs."""
    devices: list[dict[str, Any]] = [
        {
            "name": "Built-in Speaker",
            "index": 0,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
    ]
    for i in range(usb_device_count):
        base_idx = len(devices)
        devices.append(
            {
                "name": "USB Audio Device",
                "index": base_idx,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            }
        )
        devices.append(
            {
                "name": "USB Audio Device",
                "index": base_idx + 1,
                "max_input_channels": 2,
                "max_output_channels": 0,
                "default_samplerate": 48000.0,
            }
        )
    for i in range(usb_codec_count):
        base_idx = len(devices)
        devices.append(
            {
                "name": "USB Audio CODEC",
                "index": base_idx,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            }
        )
        devices.append(
            {
                "name": "USB Audio CODEC",
                "index": base_idx + 1,
                "max_input_channels": 2,
                "max_output_channels": 0,
                "default_samplerate": 48000.0,
            }
        )
    sd = MagicMock()
    sd.query_devices.return_value = devices
    sd.default.device = [-1, -1]
    return sd


def _make_mock_sd(
    usb_codec_count: int = 2,
) -> MagicMock:
    """Create a mock sounddevice module with N USB Audio CODEC pairs.

    Each pair consists of one output-only and one input-only device.
    Device indices start at 1 (0 is built-in speaker).
    """
    devices: list[dict[str, Any]] = [
        {
            "name": "Built-in Speaker",
            "index": 0,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
    ]
    for i in range(usb_codec_count):
        base_idx = 1 + i * 2
        devices.append(
            {
                "name": "USB Audio CODEC",
                "index": base_idx,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            }
        )
        devices.append(
            {
                "name": "USB Audio CODEC",
                "index": base_idx + 1,
                "max_input_channels": 2,
                "max_output_channels": 0,
                "default_samplerate": 48000.0,
            }
        )

    sd = MagicMock()
    sd.query_devices.return_value = devices
    sd.default.device = [-1, -1]
    return sd


# ---------------------------------------------------------------------------
# Unit tests — helper functions
# ---------------------------------------------------------------------------


class TestExtractTtySuffix:
    def test_macos_cu(self) -> None:
        assert _extract_tty_suffix("/dev/cu.usbserial-201410") == "201410"

    def test_macos_tty(self) -> None:
        assert _extract_tty_suffix("/dev/tty.usbserial-201410") == "201410"

    def test_long_suffix(self) -> None:
        assert _extract_tty_suffix("/dev/cu.usbserial-ABCDEF01") == "ABCDEF01"

    def test_no_match(self) -> None:
        assert _extract_tty_suffix("/dev/ttyUSB0") is None

    def test_empty(self) -> None:
        assert _extract_tty_suffix("") is None


class TestFindAudioCodecLocations:
    def test_two_radios(self) -> None:
        locs = _find_audio_codec_locations(IOREG_TWO_RADIOS)
        assert locs == [0x01111400, 0x20144000]

    def test_single_radio(self) -> None:
        locs = _find_audio_codec_locations(IOREG_SINGLE_RADIO)
        assert locs == [0x20144000]

    def test_no_audio(self) -> None:
        assert _find_audio_codec_locations(IOREG_NO_AUDIO) == []

    def test_three_radios(self) -> None:
        locs = _find_audio_codec_locations(IOREG_THREE_RADIOS)
        assert locs == [0x01111400, 0x20144000, 0x30144000]

    def test_yaesu_usb_audio_device(self) -> None:
        """Yaesu FTX-1 uses 'USB Audio Device' instead of 'USB Audio CODEC'."""
        locs = _find_audio_codec_locations(IOREG_YAESU_ONLY)
        assert locs == [0x20132200]

    def test_mixed_yaesu_and_icom(self) -> None:
        """Both Yaesu 'USB Audio Device' and Icom 'USB Audio CODEC' are found."""
        locs = _find_audio_codec_locations(IOREG_YAESU_AND_ICOM)
        assert locs == [0x01111400, 0x20132200]


class TestFindSerialLocation:
    def test_ic7300(self) -> None:
        loc = _find_serial_location(IOREG_TWO_RADIOS, "201410")
        assert loc == 538185728  # 0x20141000

    def test_ic7610(self) -> None:
        loc = _find_serial_location(IOREG_TWO_RADIOS, "111120")
        assert loc == 17895936  # 0x01112000

    def test_not_found(self) -> None:
        assert _find_serial_location(IOREG_TWO_RADIOS, "999999") is None

    def test_empty_ioreg(self) -> None:
        assert _find_serial_location("", "201410") is None


class TestIsUsbAudioCodec:
    def test_exact(self) -> None:
        assert _is_usb_audio_codec("USB Audio CODEC") is True

    def test_lowercase(self) -> None:
        assert _is_usb_audio_codec("usb audio codec") is True

    def test_mixed_case(self) -> None:
        assert _is_usb_audio_codec("USB audio CODEC") is True

    def test_no_match(self) -> None:
        assert _is_usb_audio_codec("Built-in Speaker") is False


# ---------------------------------------------------------------------------
# Integration tests — full resolution flow
# ---------------------------------------------------------------------------


class TestResolveMacos:
    """Test the full macOS resolution pipeline with mocked IORegistry."""

    def test_two_radios_ic7300(self) -> None:
        sd = _make_mock_sd(usb_codec_count=2)
        result = _resolve_macos(
            "/dev/cu.usbserial-201410",
            sounddevice_module=sd,
            ioreg_output=IOREG_TWO_RADIOS,
        )
        assert result is not None
        assert result.serial_port == "/dev/cu.usbserial-201410"
        assert result.location_prefix == 0x2014
        # Second pair (sorted by locationID: 0x0111 < 0x2014)
        assert result.rx_device_index == 4  # input[1]
        assert result.tx_device_index == 3  # output[1]

    def test_two_radios_ic7610(self) -> None:
        sd = _make_mock_sd(usb_codec_count=2)
        result = _resolve_macos(
            "/dev/cu.usbserial-111120",
            sounddevice_module=sd,
            ioreg_output=IOREG_TWO_RADIOS,
        )
        assert result is not None
        assert result.location_prefix == 0x0111
        # First pair
        assert result.rx_device_index == 2  # input[0]
        assert result.tx_device_index == 1  # output[0]

    def test_single_radio(self) -> None:
        sd = _make_mock_sd(usb_codec_count=1)
        result = _resolve_macos(
            "/dev/cu.usbserial-201410",
            sounddevice_module=sd,
            ioreg_output=IOREG_SINGLE_RADIO,
        )
        assert result is not None
        assert result.rx_device_index == 2
        assert result.tx_device_index == 1

    def test_no_audio_devices(self) -> None:
        sd = _make_mock_sd(usb_codec_count=0)
        result = _resolve_macos(
            "/dev/cu.usbserial-201410",
            sounddevice_module=sd,
            ioreg_output=IOREG_NO_AUDIO,
        )
        assert result is None

    def test_serial_port_not_in_ioreg(self) -> None:
        sd = _make_mock_sd(usb_codec_count=2)
        result = _resolve_macos(
            "/dev/cu.usbserial-999999",
            sounddevice_module=sd,
            ioreg_output=IOREG_TWO_RADIOS,
        )
        assert result is None

    def test_non_usb_serial_port(self) -> None:
        sd = _make_mock_sd(usb_codec_count=2)
        result = _resolve_macos(
            "/dev/ttyUSB0",
            sounddevice_module=sd,
            ioreg_output=IOREG_TWO_RADIOS,
        )
        assert result is None

    def test_three_radios_middle(self) -> None:
        """Resolve the middle radio in a 3-radio setup."""
        sd = _make_mock_sd(usb_codec_count=3)
        result = _resolve_macos(
            "/dev/cu.usbserial-201410",
            sounddevice_module=sd,
            ioreg_output=IOREG_THREE_RADIOS,
        )
        assert result is not None
        assert result.location_prefix == 0x2014
        # Middle pair (sorted: 0x0111, 0x2014, 0x3014)
        assert result.rx_device_index == 4  # input[1]
        assert result.tx_device_index == 3  # output[1]

    def test_three_radios_last(self) -> None:
        """Resolve the third radio in a 3-radio setup."""
        sd = _make_mock_sd(usb_codec_count=3)
        result = _resolve_macos(
            "/dev/cu.usbserial-301410",
            sounddevice_module=sd,
            ioreg_output=IOREG_THREE_RADIOS,
        )
        assert result is not None
        assert result.location_prefix == 0x3014
        assert result.rx_device_index == 6  # input[2]
        assert result.tx_device_index == 5  # output[2]


class TestResolveYaesu:
    """Test resolution for Yaesu radios using 'USB Audio Device' naming."""

    def test_yaesu_ftx1_alone(self) -> None:
        sd = _make_mock_sd_mixed(usb_codec_count=0, usb_device_count=1)
        result = _resolve_macos(
            "/dev/cu.usbserial-01AE340D0",
            sounddevice_module=sd,
            ioreg_output=IOREG_YAESU_ONLY,
        )
        assert result is not None
        assert result.location_prefix == 0x2013
        assert result.serial_port == "/dev/cu.usbserial-01AE340D0"

    def test_yaesu_alongside_icom(self) -> None:
        """Yaesu FTX-1 + Icom IC-7610 both resolved correctly."""
        sd = _make_mock_sd_mixed(usb_codec_count=1, usb_device_count=1)
        # Resolve Yaesu
        result_yaesu = _resolve_macos(
            "/dev/cu.usbserial-01AE340D0",
            sounddevice_module=sd,
            ioreg_output=IOREG_YAESU_AND_ICOM,
        )
        assert result_yaesu is not None
        assert result_yaesu.location_prefix == 0x2013

        # Resolve Icom
        result_icom = _resolve_macos(
            "/dev/cu.usbserial-111120",
            sounddevice_module=sd,
            ioreg_output=IOREG_YAESU_AND_ICOM,
        )
        assert result_icom is not None
        assert result_icom.location_prefix == 0x0111

        # Different devices
        assert result_yaesu.rx_device_index != result_icom.rx_device_index


class TestResolvePlatformDispatch:
    """Test that resolve_audio_for_serial_port dispatches correctly."""

    @patch("rigplane.usb_audio_resolve.platform")
    def test_non_darwin_returns_none(self, mock_platform: MagicMock) -> None:
        mock_platform.system.return_value = "Linux"
        result = resolve_audio_for_serial_port("/dev/ttyUSB0")
        assert result is None

    @patch("rigplane.usb_audio_resolve.platform")
    @patch("rigplane.usb_audio_resolve._resolve_macos")
    def test_darwin_delegates(
        self, mock_resolve: MagicMock, mock_platform: MagicMock
    ) -> None:
        mock_platform.system.return_value = "Darwin"
        mock_resolve.return_value = AudioDeviceMapping(
            rx_device_index=4,
            tx_device_index=3,
            serial_port="/dev/cu.usbserial-201410",
            location_prefix=0x2014,
        )
        result = resolve_audio_for_serial_port("/dev/cu.usbserial-201410")
        assert result is not None
        assert result.rx_device_index == 4
        mock_resolve.assert_called_once()


class TestAudioDeviceMapping:
    """Test the dataclass itself."""

    def test_creation(self) -> None:
        m = AudioDeviceMapping(
            rx_device_index=2,
            tx_device_index=1,
            serial_port="/dev/cu.usbserial-201410",
            location_prefix=0x2014,
        )
        assert m.rx_device_index == 2
        assert m.tx_device_index == 1
        assert m.serial_port == "/dev/cu.usbserial-201410"
        assert m.location_prefix == 0x2014

    def test_default_location_prefix(self) -> None:
        m = AudioDeviceMapping(
            rx_device_index=2,
            tx_device_index=1,
            serial_port="/dev/cu.usbserial-201410",
        )
        assert m.location_prefix is None

    def test_frozen(self) -> None:
        m = AudioDeviceMapping(
            rx_device_index=2,
            tx_device_index=1,
            serial_port="/dev/cu.usbserial-201410",
        )
        with pytest.raises(AttributeError):
            m.rx_device_index = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MOR-219 — Xiegu X6200 (CDC-ACM usbmodem + C-Media USB Audio)
# ---------------------------------------------------------------------------

# Xiegu X6200: WCH CH342 dual-serial CDC-ACM bridge (/dev/cu.usbmodem…) plus a
# C-Media "USB Audio Device", both on hub prefix 0x1423. The CAT port is the
# second ACM interface (SERIAL-B → /dev/cu.usbmodem14203). Before MOR-219,
# _extract_tty_suffix matched only "usbserial-…", so the X6200's usbmodem port
# never reached topology resolution and the browser RX stream stayed silent.
IOREG_XIEGU_X6200 = textwrap.dedent("""\
    | +-o USB Audio Device@14232200  <class IOUSBHostDevice, id 0x1000c0de1>
    | |   "locationID" = 337846784
    | |   "USB Product Name" = "USB Audio Device"
    | |   "USB Vendor Name" = "C-Media Electronics Inc.      "
    | +-o USB Dual_Serial@14231000  <class IOUSBHostDevice, id 0x1000c0de0>
    | |   "locationID" = 337842176
    | |   "USB Vendor Name" = "Nanjing QinHeng Electronics Co."
    | | +-o AppleUSBACMData
    | |   | "IOTTYSuffix" = "14203"
    | |   | "IOCalloutDevice" = "/dev/cu.usbmodem14203"
""")

# X6200 (usbmodem) alongside an Icom IC-7610 (usbserial) — proves the usbmodem
# port still resolves its own hub prefix when a CP2102-based radio is present.
IOREG_XIEGU_AND_ICOM = textwrap.dedent("""\
    | +-o USB Audio Device@14232200  <class IOUSBHostDevice, id 0x1000c0de1>
    | |   "locationID" = 337846784
    | |   "USB Product Name" = "USB Audio Device"
    | +-o USB Dual_Serial@14231000  <class IOUSBHostDevice, id 0x1000c0de0>
    | |   "locationID" = 337842176
    | | +-o AppleUSBACMData
    | |   | "IOTTYSuffix" = "14203"
    | |   | "IOCalloutDevice" = "/dev/cu.usbmodem14203"
    | +-o USB Audio CODEC@01111400  <class IOUSBHostDevice, id 0x1000a9ab5>
    | |   "locationID" = 17895424
    | |   "USB Product Name" = "USB Audio CODEC"
    | +-o CP2102 USB to UART Bridge Controller@01112000
    | |   "locationID" = 17895936
    | | +-o AppleUSBSLCOM
    | |   | "IOTTYSuffix" = "111120"
    | |   | "IOCalloutDevice" = "/dev/cu.usbserial-111120"
""")


def _make_mock_sd_xiegu() -> MagicMock:
    """Mock sounddevice for an X6200: one built-in + one C-Media duplex device.

    The C-Media codec enumerates on macOS CoreAudio as a single "USB Audio
    Device" with both capture (1ch) and playback (2ch) channels.
    """
    devices: list[dict[str, Any]] = [
        {
            "name": "Built-in Speaker",
            "index": 0,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
        {
            "name": "USB Audio Device",
            "index": 1,
            "max_input_channels": 1,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
    ]
    sd = MagicMock()
    sd.query_devices.return_value = devices
    sd.default.device = [-1, -1]
    return sd


class TestExtractTtySuffixCdcAcm:
    """MOR-219: usbmodem (CDC-ACM) ports must yield a TTY suffix."""

    def test_usbmodem_cu(self) -> None:
        assert _extract_tty_suffix("/dev/cu.usbmodem14201") == "14201"

    def test_usbmodem_tty(self) -> None:
        assert _extract_tty_suffix("/dev/tty.usbmodem1434203") == "1434203"

    def test_usbmodem_composite_acm_index(self) -> None:
        # CH342 dual-serial exposes …1 and …3; CAT is on SERIAL-B (…3).
        assert _extract_tty_suffix("/dev/cu.usbmodem14203") == "14203"

    def test_usbserial_unchanged(self) -> None:
        # Existing FTDI/CP210x extraction must not regress.
        assert _extract_tty_suffix("/dev/cu.usbserial-201410") == "201410"


class TestIsUsbAudioCodecXiegu:
    """MOR-219: recognise the C-Media / generic 'USB Audio Device' identity."""

    def test_usb_audio_device(self) -> None:
        assert _is_usb_audio_codec("USB Audio Device") is True

    def test_cmedia_vendor_dashed(self) -> None:
        assert _is_usb_audio_codec("C-Media USB Headphone Set") is True

    def test_cmedia_vendor_plain(self) -> None:
        assert _is_usb_audio_codec("CMedia Audio") is True

    def test_unrelated_still_false(self) -> None:
        assert _is_usb_audio_codec("Built-in Microphone") is False


class TestResolveXieguX6200:
    """Full macOS topology resolution for the X6200."""

    def test_audio_codec_locations_include_cmedia(self) -> None:
        assert _find_audio_codec_locations(IOREG_XIEGU_X6200) == [0x14232200]

    def test_single_x6200_usbmodem_resolves_to_cmedia(self) -> None:
        sd = _make_mock_sd_xiegu()
        result = _resolve_macos(
            "/dev/cu.usbmodem14203",
            sounddevice_module=sd,
            ioreg_output=IOREG_XIEGU_X6200,
        )
        assert result is not None
        assert result.serial_port == "/dev/cu.usbmodem14203"
        assert result.location_prefix == 0x1423
        # The duplex C-Media device serves both RX and TX.
        assert result.rx_device_index == 1
        assert result.tx_device_index == 1

    def test_x6200_serial_location_found_alongside_icom(self) -> None:
        # Root-cause regression: the usbmodem suffix must extract AND its
        # locationID must be found even when a usbserial Icom is also present.
        loc = _find_serial_location(IOREG_XIEGU_AND_ICOM, "14203")
        assert loc == 0x14231000
        assert (loc >> 16) == 0x1423

    def test_icom_unaffected_by_xiegu_presence(self) -> None:
        loc = _find_serial_location(IOREG_XIEGU_AND_ICOM, "111120")
        assert loc == 17895936  # matches the IC-7610 fixture literal
        assert (loc >> 16) == 0x0111

    def test_audio_locations_mixed(self) -> None:
        assert _find_audio_codec_locations(IOREG_XIEGU_AND_ICOM) == [
            0x01111400,
            0x14232200,
        ]


# ---------------------------------------------------------------------------
# MOR-230 — identity-based pairing for mixed-vendor / mixed-shape sets
# ---------------------------------------------------------------------------


def _make_mock_sd_cmedia_duplex_plus_icom_split() -> MagicMock:
    """Mock the CoreAudio enumeration for an X6200 + Icom IC-7610 set.

    The C-Media codec (X6200) enumerates as ONE duplex device (in=1/out=2),
    while the Icom "USB Audio CODEC" enumerates as a split pair: an
    output-only device followed by an input-only device.

    Enumeration order here deliberately interleaves shapes so a flat
    ``usb_inputs[i]`` / ``usb_outputs[i]`` positional index desyncs:

        idx 0 -> Built-in Speaker      (skipped, not USB audio)
        idx 1 -> C-Media duplex        in=1 out=2   (X6200, prefix 0x1423)
        idx 2 -> Icom CODEC playback   in=0 out=2   (IC-7610, prefix 0x0111)
        idx 3 -> Icom CODEC capture     in=2 out=0   (IC-7610, prefix 0x0111)
    """
    devices: list[dict[str, Any]] = [
        {
            "name": "Built-in Speaker",
            "index": 0,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
        {
            "name": "USB Audio Device",
            "index": 1,
            "max_input_channels": 1,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
        {
            "name": "USB Audio CODEC",
            "index": 2,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
        {
            "name": "USB Audio CODEC",
            "index": 3,
            "max_input_channels": 2,
            "max_output_channels": 0,
            "default_samplerate": 48000.0,
        },
    ]
    sd = MagicMock()
    sd.query_devices.return_value = devices
    sd.default.device = [-1, -1]
    return sd


class TestResolveMixedVendorShapes:
    """MOR-230: a duplex C-Media device next to a split-pair Icom CODEC.

    The pre-MOR-230 positional logic flattens every USB-audio entry into a
    single ``usb_inputs`` / ``usb_outputs`` list and indexes both by the
    sorted-prefix position. With mixed device shapes those flat lists no
    longer line up with the sorted prefixes, so each port resolves to the
    *other* radio's device. Identity-based pairing must select each port's
    OWN audio device.

    Sorted audio locations: [0x01111400 (Icom), 0x14232200 (X6200)].
    Correct outcome:
        - X6200 usbmodem (prefix 0x1423) -> C-Media duplex (idx 1/1)
        - Icom usbserial (prefix 0x0111) -> CODEC split   (rx 3 / tx 2)
    """

    def test_x6200_resolves_to_cmedia_duplex(self) -> None:
        sd = _make_mock_sd_cmedia_duplex_plus_icom_split()
        result = _resolve_macos(
            "/dev/cu.usbmodem14203",
            sounddevice_module=sd,
            ioreg_output=IOREG_XIEGU_AND_ICOM,
        )
        assert result is not None
        assert result.location_prefix == 0x1423
        # The C-Media duplex device serves both RX and TX.
        assert result.rx_device_index == 1
        assert result.tx_device_index == 1

    def test_icom_resolves_to_codec_split_pair(self) -> None:
        sd = _make_mock_sd_cmedia_duplex_plus_icom_split()
        result = _resolve_macos(
            "/dev/cu.usbserial-111120",
            sounddevice_module=sd,
            ioreg_output=IOREG_XIEGU_AND_ICOM,
        )
        assert result is not None
        assert result.location_prefix == 0x0111
        # Icom CODEC: input-only capture (idx 3) + output-only playback (idx 2).
        assert result.rx_device_index == 3
        assert result.tx_device_index == 2

    def test_each_port_resolves_to_its_own_device(self) -> None:
        sd = _make_mock_sd_cmedia_duplex_plus_icom_split()
        x6200 = _resolve_macos(
            "/dev/cu.usbmodem14203",
            sounddevice_module=sd,
            ioreg_output=IOREG_XIEGU_AND_ICOM,
        )
        icom = _resolve_macos(
            "/dev/cu.usbserial-111120",
            sounddevice_module=sd,
            ioreg_output=IOREG_XIEGU_AND_ICOM,
        )
        assert x6200 is not None and icom is not None
        # The two radios must never share an audio device.
        assert x6200.rx_device_index != icom.rx_device_index
        assert x6200.tx_device_index != icom.tx_device_index


class TestResolveSplitPairAtIndexZero:
    """MOR-230 regression: a split-pair USB codec whose playback device
    enumerates at sounddevice index 0 (e.g. a headless host with no built-in
    audio ahead of the USB codec). Index 0 is a valid but falsy index; the
    split-cluster merge must not drop it (the `pend_tx or tx` bug)."""

    def _sd_codec_split_at_zero(self) -> MagicMock:
        # No built-in device: the USB Audio CODEC pair occupies indices 0/1,
        # output-only at 0 (the falsy index), input-only at 1.
        devices: list[dict[str, Any]] = [
            {
                "name": "USB Audio CODEC",
                "index": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            },
            {
                "name": "USB Audio CODEC",
                "index": 1,
                "max_input_channels": 2,
                "max_output_channels": 0,
                "default_samplerate": 48000.0,
            },
        ]
        sd = MagicMock()
        sd.query_devices.return_value = devices
        sd.default.device = [-1, -1]
        return sd

    def test_index_zero_playback_not_dropped(self) -> None:
        result = _resolve_macos(
            "/dev/cu.usbserial-201410",
            sounddevice_module=self._sd_codec_split_at_zero(),
            ioreg_output=IOREG_SINGLE_RADIO,
        )
        assert result is not None
        assert result.rx_device_index == 1
        # TX is the output-only device at index 0 — must survive the merge.
        assert result.tx_device_index == 0


class TestNameFallbackXiegu:
    """MOR-219: name-based fallback (Linux/Windows) prefers the C-Media codec
    over an unknown commodity device when topology is unavailable."""

    def test_cmedia_preferred_over_unknown_duplex(self) -> None:
        from rigplane.audio.usb_driver import (
            UsbAudioDevice,
            select_usb_audio_devices,
        )

        devices = [
            UsbAudioDevice(
                index=0,
                name="Some Cheap Headset",
                input_channels=1,
                output_channels=2,
            ),
            UsbAudioDevice(
                index=1,
                name="C-Media Electronics",
                input_channels=1,
                output_channels=2,
            ),
        ]
        rx, tx = select_usb_audio_devices(devices)
        assert rx.index == 1
        assert tx.index == 1
