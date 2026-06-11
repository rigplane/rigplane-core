"""Tests for USB audio device resolution from serial port topology."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from rigplane.usb_audio_resolve import (
    AudioDeviceMapping,
    _extract_linux_tty_name,
    _extract_tty_suffix,
    _find_audio_codec_locations,
    _find_serial_location,
    _is_usb_audio_codec,
    _normalize_alsa_device_name,
    _resolve_linux,
    _resolve_macos,
    _usb_device_node_from_realpath,
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
    def test_unsupported_platform_returns_none(self, mock_platform: MagicMock) -> None:
        # macOS/Linux/Windows all have resolvers now; an unsupported platform
        # falls through to the name-based selection path.
        mock_platform.system.return_value = "FreeBSD"
        result = resolve_audio_for_serial_port("/dev/cuaU0")
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


# ---------------------------------------------------------------------------
# MOR-229 — Windows USB topology / robust-identity resolution
# ---------------------------------------------------------------------------

# Windows enumerates USB devices through PnP. Each USB radio is a composite
# parent device (instance path like USB\VID_xxxx&PID_yyyy\serial) whose
# children are the CDC/serial function (exposing a COMx name) and the USB
# Audio Class function (exposing an audio endpoint name). The shared *parent*
# instance path is the topology anchor, mirroring the macOS hub-prefix anchor.
#
# Modelled set:
#   - Xiegu X6200: C-Media composite parent, COM3 serial + "USB Audio Device"
#     audio endpoint, VID:PID 0D8C:0012.
#   - A second USB radio (Icom-like) on a DIFFERENT parent: COM7 serial +
#     "USB Audio CODEC" audio endpoint, VID:PID 10C4:EA60 (CP210x bridge).


def _x6200_pnp_records() -> list[Any]:
    """X6200 alone: C-Media composite parent with serial (COM3) + audio."""
    from rigplane.usb_audio_resolve import WindowsPnpDevice

    parent = r"USB\VID_0D8C&PID_0012\6&1A2B3C4D&0&1"
    return [
        WindowsPnpDevice(
            pnp_device_id=parent + r"&0000",
            parent_pnp_id=parent,
            vid="0D8C",
            pid="0012",
            com_port="COM3",
            audio_endpoint_name=None,
        ),
        WindowsPnpDevice(
            pnp_device_id=parent + r"&0001",
            parent_pnp_id=parent,
            vid="0D8C",
            pid="0012",
            com_port=None,
            audio_endpoint_name="USB Audio Device",
        ),
    ]


def _x6200_and_icom_pnp_records() -> list[Any]:
    """X6200 (COM3, C-Media) + a second radio (COM7, Icom CODEC) on a
    different USB parent. Each radio's serial and audio share one parent."""
    from rigplane.usb_audio_resolve import WindowsPnpDevice

    x6200_parent = r"USB\VID_0D8C&PID_0012\6&1A2B3C4D&0&1"
    icom_parent = r"USB\VID_10C4&PID_EA60\IC7610_0001"
    return [
        WindowsPnpDevice(
            pnp_device_id=x6200_parent + r"&0000",
            parent_pnp_id=x6200_parent,
            vid="0D8C",
            pid="0012",
            com_port="COM3",
            audio_endpoint_name=None,
        ),
        WindowsPnpDevice(
            pnp_device_id=x6200_parent + r"&0001",
            parent_pnp_id=x6200_parent,
            vid="0D8C",
            pid="0012",
            com_port=None,
            audio_endpoint_name="USB Audio Device",
        ),
        WindowsPnpDevice(
            pnp_device_id=icom_parent + r"&0000",
            parent_pnp_id=icom_parent,
            vid="10C4",
            pid="EA60",
            com_port="COM7",
            audio_endpoint_name=None,
        ),
        WindowsPnpDevice(
            pnp_device_id=icom_parent + r"&0001",
            parent_pnp_id=icom_parent,
            vid="10C4",
            pid="EA60",
            com_port=None,
            audio_endpoint_name="USB Audio CODEC",
        ),
    ]


class TestResolveWindows:
    """MOR-229: Windows topology resolution via an injected pnp_query."""

    def test_single_x6200_resolves_to_cmedia(self) -> None:
        from rigplane.usb_audio_resolve import _resolve_windows

        sd = _make_mock_sd_xiegu()
        result = _resolve_windows(
            "COM3",
            sounddevice_module=sd,
            pnp_query=lambda: _x6200_pnp_records(),
        )
        assert result is not None
        assert result.serial_port == "COM3"
        # The C-Media duplex device serves both RX and TX.
        assert result.rx_device_index == 1
        assert result.tx_device_index == 1

    def test_two_radios_each_resolves_to_own_audio(self) -> None:
        from rigplane.usb_audio_resolve import _resolve_windows

        sd = _make_mock_sd_cmedia_duplex_plus_icom_split()
        records = _x6200_and_icom_pnp_records()
        x6200 = _resolve_windows(
            "COM3",
            sounddevice_module=sd,
            pnp_query=lambda: records,
        )
        icom = _resolve_windows(
            "COM7",
            sounddevice_module=sd,
            pnp_query=lambda: records,
        )
        assert x6200 is not None and icom is not None
        # X6200 → C-Media duplex (idx 1/1).
        assert x6200.rx_device_index == 1
        assert x6200.tx_device_index == 1
        # Icom → CODEC split pair (rx capture idx 3 / tx playback idx 2).
        assert icom.rx_device_index == 3
        assert icom.tx_device_index == 2
        # The two radios must never share an audio device.
        assert x6200.rx_device_index != icom.rx_device_index
        assert x6200.tx_device_index != icom.tx_device_index

    def test_unknown_com_port_returns_none(self) -> None:
        from rigplane.usb_audio_resolve import _resolve_windows

        sd = _make_mock_sd_xiegu()
        result = _resolve_windows(
            "COM99",
            sounddevice_module=sd,
            pnp_query=lambda: _x6200_pnp_records(),
        )
        assert result is None

    def test_no_sibling_audio_endpoint_returns_none(self) -> None:
        from rigplane.usb_audio_resolve import WindowsPnpDevice, _resolve_windows

        # A serial-only device with no audio function on its parent.
        parent = r"USB\VID_10C4&PID_EA60\NOAUDIO"
        records = [
            WindowsPnpDevice(
                pnp_device_id=parent + r"&0000",
                parent_pnp_id=parent,
                vid="10C4",
                pid="EA60",
                com_port="COM5",
                audio_endpoint_name=None,
            ),
        ]
        sd = _make_mock_sd_xiegu()
        result = _resolve_windows(
            "COM5",
            sounddevice_module=sd,
            pnp_query=lambda: records,
        )
        assert result is None

    def test_pnp_query_unavailable_returns_none(self) -> None:
        from rigplane.usb_audio_resolve import _resolve_windows

        def boom() -> list[Any]:
            raise OSError("WMI not available (headless)")

        sd = _make_mock_sd_xiegu()
        result = _resolve_windows(
            "COM3",
            sounddevice_module=sd,
            pnp_query=boom,
        )
        assert result is None

    def test_pnp_query_returns_empty_returns_none(self) -> None:
        from rigplane.usb_audio_resolve import _resolve_windows

        sd = _make_mock_sd_xiegu()
        result = _resolve_windows(
            "COM3",
            sounddevice_module=sd,
            pnp_query=lambda: [],
        )
        assert result is None

    def test_vidpid_fallback_when_parent_ambiguous(self) -> None:
        """Robust-identity fallback: when the serial device exposes no usable
        parent link, fall back to matching the audio endpoint by VID:PID."""
        from rigplane.usb_audio_resolve import WindowsPnpDevice, _resolve_windows

        # Serial and audio carry the same VID:PID but report no parent
        # (parent_pnp_id empty) — topology is ambiguous, VID:PID must link.
        records = [
            WindowsPnpDevice(
                pnp_device_id=r"USB\VID_0D8C&PID_0012\SER\&0000",
                parent_pnp_id="",
                vid="0D8C",
                pid="0012",
                com_port="COM3",
                audio_endpoint_name=None,
            ),
            WindowsPnpDevice(
                pnp_device_id=r"USB\VID_0D8C&PID_0012\SER\&0001",
                parent_pnp_id="",
                vid="0D8C",
                pid="0012",
                com_port=None,
                audio_endpoint_name="USB Audio Device",
            ),
        ]
        sd = _make_mock_sd_xiegu()
        result = _resolve_windows(
            "COM3",
            sounddevice_module=sd,
            pnp_query=lambda: records,
        )
        assert result is not None
        assert result.rx_device_index == 1
        assert result.tx_device_index == 1


class TestResolvePlatformDispatchWindows:
    """MOR-229: resolve_audio_for_serial_port dispatches to _resolve_windows."""

    @patch("rigplane.usb_audio_resolve.platform")
    @patch("rigplane.usb_audio_resolve._resolve_windows")
    def test_windows_delegates(
        self, mock_resolve: MagicMock, mock_platform: MagicMock
    ) -> None:
        mock_platform.system.return_value = "Windows"
        mock_resolve.return_value = AudioDeviceMapping(
            rx_device_index=1,
            tx_device_index=1,
            serial_port="COM3",
            location_prefix=None,
        )
        result = resolve_audio_for_serial_port("COM3")
        assert result is not None
        assert result.rx_device_index == 1
        mock_resolve.assert_called_once()


# ---------------------------------------------------------------------------
# MOR-228 — Linux sysfs topology resolution
# ---------------------------------------------------------------------------


def _mk_usb_device(
    root: Path,
    usb_dev_path: str,
    *,
    id_vendor: str | None = None,
    id_product: str | None = None,
    product: str | None = None,
) -> Path:
    """Create a fake USB *device* node directory under a fixture sysfs tree.

    ``usb_dev_path`` is relative to ``<root>/devices`` and ends in a USB
    device node name (e.g. ``usb1/1-1/1-1.1``). Writes the optional
    ``idVendor``/``idProduct``/``product`` attribute files. Returns the
    absolute path to the device node directory.
    """
    dev_dir = root / "devices" / usb_dev_path
    dev_dir.mkdir(parents=True, exist_ok=True)
    if id_vendor is not None:
        (dev_dir / "idVendor").write_text(id_vendor + "\n")
    if id_product is not None:
        (dev_dir / "idProduct").write_text(id_product + "\n")
    if product is not None:
        (dev_dir / "product").write_text(product + "\n")
    return dev_dir


def _link_tty(root: Path, tty_name: str, usb_dev_dir: Path) -> None:
    """Wire <root>/class/tty/<tty_name>/device → an interface under usb_dev_dir."""
    # The kernel's `device` symlink points at the *interface* node; create one.
    iface = usb_dev_dir / (os.path.basename(str(usb_dev_dir)) + ":1.0")
    iface.mkdir(parents=True, exist_ok=True)
    tty_class_dir = root / "class" / "tty" / tty_name
    tty_class_dir.mkdir(parents=True, exist_ok=True)
    (tty_class_dir / "device").symlink_to(iface, target_is_directory=True)


def _link_card(root: Path, card_n: int, usb_dev_dir: Path) -> None:
    """Wire <root>/class/sound/cardN/device → an interface under usb_dev_dir."""
    iface = usb_dev_dir / (os.path.basename(str(usb_dev_dir)) + ":1.0")
    iface.mkdir(parents=True, exist_ok=True)
    card_dir = root / "class" / "sound" / f"card{card_n}"
    card_dir.mkdir(parents=True, exist_ok=True)
    (card_dir / "device").symlink_to(iface, target_is_directory=True)


def _make_mock_sd_two_usb_named(name_a: str, name_b: str) -> MagicMock:
    """Two duplex USB-audio devices with distinct names, after a built-in.

    idx 0 -> Built-in Speaker (skipped)
    idx 1 -> name_a duplex (in/out)
    idx 2 -> name_b duplex (in/out)
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
            "name": name_a,
            "index": 1,
            "max_input_channels": 1,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
        {
            "name": name_b,
            "index": 2,
            "max_input_channels": 1,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
    ]
    sd = MagicMock()
    sd.query_devices.return_value = devices
    sd.default.device = [-1, -1]
    return sd


class TestExtractLinuxTtyName:
    """MOR-228: Linux tty names for both CDC-ACM and USB-serial nodes."""

    def test_ttyacm(self) -> None:
        assert _extract_linux_tty_name("/dev/ttyACM0") == "ttyACM0"

    def test_ttyusb(self) -> None:
        assert _extract_linux_tty_name("/dev/ttyUSB1") == "ttyUSB1"

    def test_ttyacm_high_index(self) -> None:
        assert _extract_linux_tty_name("/dev/ttyACM12") == "ttyACM12"

    def test_macos_path_is_none(self) -> None:
        assert _extract_linux_tty_name("/dev/cu.usbserial-201410") is None

    def test_unrelated_is_none(self) -> None:
        assert _extract_linux_tty_name("/dev/ttyS0") is None


class TestUsbDeviceNodeFromRealpath:
    """MOR-228: ascend an interface realpath to the USB device node."""

    def test_interface_to_device(self) -> None:
        real = "/sys/devices/pci0000:00/usb1/1-1/1-1.4/1-1.4:1.0"
        assert (
            _usb_device_node_from_realpath(real)
            == "/sys/devices/pci0000:00/usb1/1-1/1-1.4"
        )

    def test_root_port_device(self) -> None:
        real = "/sys/devices/pci0000:00/usb2/2-1/2-1:1.0"
        assert (
            _usb_device_node_from_realpath(real) == "/sys/devices/pci0000:00/usb2/2-1"
        )

    def test_no_usb_node(self) -> None:
        assert _usb_device_node_from_realpath("/sys/devices/platform/foo") is None


class TestResolveLinuxSysfs:
    """MOR-228: full Linux resolution against a fixture sysfs tree.

    Topology modelled:
      bus 1, hub 1-1: X6200 — CAT (ttyACM0) at 1-1.1, C-Media audio at 1-1.2
      bus 1, hub 1-2: FTDI radio — serial (ttyUSB0) at 1-2.1, audio at 1-2.2

    Each radio's CAT/serial port shares a deeper USB-path prefix with its OWN
    audio device (score 2: bus + hub) than with the other radio's audio
    (score 1: bus only), so each must resolve to its own card.
    """

    @staticmethod
    def _build_tree(root: Path) -> None:
        # X6200 CAT (CDC-ACM) and its C-Media audio share hub 1-1.
        x6200_cat = _mk_usb_device(
            root,
            "pci0000:00/usb1/1-1/1-1.1",
            id_vendor="1a86",
            id_product="55d4",
            product="USB Dual_Serial",
        )
        x6200_audio = _mk_usb_device(
            root,
            "pci0000:00/usb1/1-1/1-1.2",
            id_vendor="0d8c",
            id_product="0012",
            product="USB Audio Device",
        )
        # FTDI radio serial and its audio share a *different* hub 1-2.
        ftdi_serial = _mk_usb_device(
            root,
            "pci0000:00/usb1/1-2/1-2.1",
            id_vendor="0403",
            id_product="6001",
            product="USB Serial",
        )
        ftdi_audio = _mk_usb_device(
            root,
            "pci0000:00/usb1/1-2/1-2.2",
            id_vendor="08bb",
            id_product="2901",
            product="USB Audio CODEC",
        )
        _link_tty(root, "ttyACM0", x6200_cat)
        _link_tty(root, "ttyUSB0", ftdi_serial)
        _link_card(root, 0, x6200_audio)
        _link_card(root, 1, ftdi_audio)

    def test_x6200_resolves_to_cmedia(self, tmp_path: Path) -> None:
        self._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "USB Audio CODEC")
        result = _resolve_linux(
            "/dev/ttyACM0",
            sounddevice_module=sd,
            sysfs_root=str(tmp_path),
        )
        assert result is not None
        assert result.serial_port == "/dev/ttyACM0"
        # C-Media "USB Audio Device" duplex enumerates at sounddevice index 1.
        assert result.rx_device_index == 1
        assert result.tx_device_index == 1

    def test_ftdi_resolves_to_its_own_codec(self, tmp_path: Path) -> None:
        self._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "USB Audio CODEC")
        result = _resolve_linux(
            "/dev/ttyUSB0",
            sounddevice_module=sd,
            sysfs_root=str(tmp_path),
        )
        assert result is not None
        assert result.serial_port == "/dev/ttyUSB0"
        # "USB Audio CODEC" enumerates at sounddevice index 2.
        assert result.rx_device_index == 2
        assert result.tx_device_index == 2

    def test_each_port_resolves_to_its_own_device(self, tmp_path: Path) -> None:
        self._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "USB Audio CODEC")
        x6200 = _resolve_linux(
            "/dev/ttyACM0", sounddevice_module=sd, sysfs_root=str(tmp_path)
        )
        ftdi = _resolve_linux(
            "/dev/ttyUSB0", sounddevice_module=sd, sysfs_root=str(tmp_path)
        )
        assert x6200 is not None and ftdi is not None
        assert x6200.rx_device_index != ftdi.rx_device_index
        assert x6200.tx_device_index != ftdi.tx_device_index

    def test_unknown_tty_returns_none(self, tmp_path: Path) -> None:
        self._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "USB Audio CODEC")
        result = _resolve_linux(
            "/dev/ttyACM9",  # not wired into the fixture tree
            sounddevice_module=sd,
            sysfs_root=str(tmp_path),
        )
        assert result is None

    def test_non_linux_serial_path_returns_none(self, tmp_path: Path) -> None:
        self._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "USB Audio CODEC")
        result = _resolve_linux(
            "/dev/cu.usbserial-201410",  # macOS shape, not a Linux tty
            sounddevice_module=sd,
            sysfs_root=str(tmp_path),
        )
        assert result is None

    def test_no_usb_audio_cards_returns_none(self, tmp_path: Path) -> None:
        # Wire only the serial port; no ALSA cards under /class/sound.
        x6200_cat = _mk_usb_device(
            tmp_path,
            "pci0000:00/usb1/1-1/1-1.1",
            id_vendor="1a86",
            id_product="55d4",
            product="USB Dual_Serial",
        )
        _link_tty(tmp_path, "ttyACM0", x6200_cat)
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "USB Audio CODEC")
        result = _resolve_linux(
            "/dev/ttyACM0", sounddevice_module=sd, sysfs_root=str(tmp_path)
        )
        assert result is None


class TestNormalizeAlsaDeviceName:
    """MOR-549: strip PortAudio's ALSA decoration down to the card name."""

    def test_full_alsa_form(self) -> None:
        assert (
            _normalize_alsa_device_name("USB Audio CODEC: Audio (hw:2,0)")
            == "USB Audio CODEC"
        )

    def test_alsa_form_with_pcm_descriptor(self) -> None:
        assert (
            _normalize_alsa_device_name("USB Audio Device: USB Audio (hw:1,0)")
            == "USB Audio Device"
        )

    def test_plughw_suffix(self) -> None:
        assert (
            _normalize_alsa_device_name("USB Audio CODEC: Audio (plughw:2,0)")
            == "USB Audio CODEC"
        )

    def test_suffix_without_descriptor(self) -> None:
        assert _normalize_alsa_device_name("USB Audio CODEC (hw:2,0)") == (
            "USB Audio CODEC"
        )

    def test_plain_name_unchanged(self) -> None:
        assert _normalize_alsa_device_name("USB Audio CODEC") == "USB Audio CODEC"

    def test_macos_name_unchanged(self) -> None:
        assert _normalize_alsa_device_name("USB Audio Device") == "USB Audio Device"

    def test_colon_without_hw_suffix_unchanged(self) -> None:
        # Conservative: only strip the ": <descriptor>" tail when the name
        # carries an ALSA (hw:X,Y) suffix — never over-strip legitimate names.
        assert (
            _normalize_alsa_device_name("Radio: Special Edition")
            == "Radio: Special Edition"
        )


class TestResolveLinuxAlsaDeviceNames:
    """MOR-549: real ALSA enumerations decorate names as
    ``"<card>: <pcm> (hw:X,Y)"`` while sysfs ``product`` is the bare card name
    (``"USB Audio CODEC"``). Topology pairing must match despite the suffix.
    """

    def test_ftdi_resolves_with_realistic_alsa_names(self, tmp_path: Path) -> None:
        TestResolveLinuxSysfs._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named(
            "USB Audio Device: USB Audio (hw:1,0)",
            "USB Audio CODEC: Audio (hw:2,0)",
        )
        result = _resolve_linux(
            "/dev/ttyUSB0", sounddevice_module=sd, sysfs_root=str(tmp_path)
        )
        assert result is not None
        # sysfs product "USB Audio CODEC" must match the decorated ALSA name
        # at sounddevice index 2.
        assert result.rx_device_index == 2
        assert result.tx_device_index == 2

    def test_x6200_resolves_with_realistic_alsa_names(self, tmp_path: Path) -> None:
        TestResolveLinuxSysfs._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named(
            "USB Audio Device: USB Audio (hw:1,0)",
            "USB Audio CODEC: Audio (hw:2,0)",
        )
        result = _resolve_linux(
            "/dev/ttyACM0", sounddevice_module=sd, sysfs_root=str(tmp_path)
        )
        assert result is not None
        assert result.rx_device_index == 1
        assert result.tx_device_index == 1


class TestResolveLinuxCompositeIdentity:
    """MOR-228: when two cards tie on USB-path prefix, the idVendor/idProduct
    matching the radio's OWN USB device breaks the tie (composite radio whose
    audio + CAT live on one physical device behind a shared hub)."""

    def test_vid_pid_tiebreak(self, tmp_path: Path) -> None:
        # All three sit on sibling ports of the SAME hub (1-1.x), so both audio
        # cards tie on USB-path prefix score against the serial port (common
        # ["1","1"] = 2). Only the idVendor:idProduct tie-break (+1) then
        # decides — the audio card sharing the serial port's VID:PID wins.
        # Distinct product names map each card unambiguously to its sounddevice
        # entry, so the asserted index proves *which* card was selected.
        cat = _mk_usb_device(
            tmp_path,
            "pci0000:00/usb1/1-1.1",
            id_vendor="0d8c",
            id_product="0012",
            product="USB Dual_Serial",
        )
        # NON-matching audio on the LOWER sibling port (1-1.2). Cards are
        # enumerated in ascending sysfs-path order, so this one is seen first
        # and would win the bare prefix tie by first-wins. Different VID:PID.
        # Its name must still be a recognised USB-audio token to be clustered.
        other_audio = _mk_usb_device(
            tmp_path,
            "pci0000:00/usb1/1-1.2",
            id_vendor="08bb",
            id_product="2901",
            product="C-Media USB Audio",
        )
        # OWN audio (the X6200 codec) on the higher port (1-1.3), sharing the
        # serial port's VID:PID. Ties on prefix; only the VID:PID +1 wins it.
        own_audio = _mk_usb_device(
            tmp_path,
            "pci0000:00/usb1/1-1.3",
            id_vendor="0d8c",
            id_product="0012",
            product="USB Audio Device",
        )
        _link_tty(tmp_path, "ttyACM0", cat)
        _link_card(tmp_path, 0, other_audio)
        _link_card(tmp_path, 1, own_audio)
        # idx 1 -> "USB Audio Device" (own); idx 2 -> "C-Media USB Audio" (other).
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "C-Media USB Audio")
        result = _resolve_linux(
            "/dev/ttyACM0", sounddevice_module=sd, sysfs_root=str(tmp_path)
        )
        assert result is not None
        # The VID:PID tie-break selects own_audio → idx 1. Without it, the
        # first-enumerated "C-Media USB Audio" (idx 2) would win the prefix tie.
        assert result.rx_device_index == 1
        assert result.tx_device_index == 1


class TestResolveLinuxDispatch:
    """MOR-228: resolve_audio_for_serial_port dispatches to _resolve_linux."""

    @patch("rigplane.usb_audio_resolve.platform")
    @patch("rigplane.usb_audio_resolve._resolve_linux")
    def test_linux_delegates(
        self, mock_resolve: MagicMock, mock_platform: MagicMock
    ) -> None:
        mock_platform.system.return_value = "Linux"
        mock_resolve.return_value = AudioDeviceMapping(
            rx_device_index=1,
            tx_device_index=1,
            serial_port="/dev/ttyACM0",
            location_prefix=0x0101,
        )
        result = resolve_audio_for_serial_port("/dev/ttyACM0")
        assert result is not None
        assert result.rx_device_index == 1
        mock_resolve.assert_called_once()
