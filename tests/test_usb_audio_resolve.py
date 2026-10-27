"""Public USB audio mapping compatibility and standalone selection."""

from unittest.mock import MagicMock, patch

import pytest

from rigplane.usb_audio_resolve import AudioDeviceMapping, resolve_audio_for_serial_port


class TestResolvePlatformDispatch:
    @patch("rigplane.usb_audio_resolve.platform")
    def test_unsupported_platform_returns_none(self, mock_platform: MagicMock) -> None:
        mock_platform.system.return_value = "FreeBSD"
        assert resolve_audio_for_serial_port("/dev/cuaU0") is None


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


class TestStandaloneNameSelection:
    """Standalone selection without a serial radio retains name preferences."""

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
