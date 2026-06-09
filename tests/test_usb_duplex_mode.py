"""Tests for the USB duplex-policy resolver (MOR-534, AudioTransport 1/12).

Pure read-only policy: ``resolve_usb_duplex_mode`` plus the lazy
``UsbAudioDriver.duplex_mode`` property. ``"exclusive"`` iff macOS AND
RX/TX resolve to the same device index AND the device is a real CODEC
(not a virtual loopback). Nothing consumes the policy yet.
"""

from __future__ import annotations

import sys

import pytest

from rigplane.audio import bridge
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.usb_driver import (
    UsbAudioDevice,
    UsbAudioDriver,
    resolve_usb_duplex_mode,
)


def _codec(index: int = 1, name: str = "USB Audio CODEC") -> UsbAudioDevice:
    return UsbAudioDevice(
        index=index,
        name=name,
        input_channels=2,
        output_channels=2,
    )


def _darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")


def test_same_codec_device_on_macos_is_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _darwin(monkeypatch)
    dev = _codec()
    assert resolve_usb_duplex_mode(dev, dev) == "exclusive"


def test_separate_devices_on_macos_are_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _darwin(monkeypatch)
    assert resolve_usb_duplex_mode(_codec(index=1), _codec(index=2)) == "full"


def test_virtual_loopback_on_macos_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _darwin(monkeypatch)
    dev = _codec(name="BlackHole 2ch")
    assert resolve_usb_duplex_mode(dev, dev) == "full"


def test_same_codec_device_on_non_darwin_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    dev = _codec()
    assert resolve_usb_duplex_mode(dev, dev) == "full"


def test_resolver_shares_bridge_loopback_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Monkeypatching the bridge predicate must steer the resolver too."""
    _darwin(monkeypatch)
    seen: list[str] = []

    def fake_predicate(dev: AudioDeviceInfo) -> bool:
        seen.append(dev.name)
        return True

    monkeypatch.setattr(bridge, "_is_virtual_loopback_device", fake_predicate)
    dev = _codec()
    assert resolve_usb_duplex_mode(dev, dev) == "full"
    assert seen == [dev.name]


def test_driver_duplex_mode_property_same_codec_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _darwin(monkeypatch)
    backend = FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="USB Audio CODEC",
                input_channels=2,
                output_channels=2,
            ),
        ]
    )
    driver = UsbAudioDriver(backend=backend)
    assert driver.duplex_mode == "exclusive"
    # The lazy property resolves devices via the normal selection path.
    assert driver.selected_rx_device is not None
    assert driver.selected_tx_device is not None


def test_driver_duplex_mode_property_loopback_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _darwin(monkeypatch)
    backend = FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="BlackHole 2ch",
                input_channels=2,
                output_channels=2,
            ),
        ]
    )
    driver = UsbAudioDriver(backend=backend)
    assert driver.duplex_mode == "full"
