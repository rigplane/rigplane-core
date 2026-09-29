"""Tests for the USB duplex-policy resolver (MOR-534, AudioTransport 1/12).

Pure read-only policy: ``resolve_usb_duplex_mode`` plus the
``UsbAudioDriver.duplex_mode`` property (MOR-2892: a pure cache read —
resolution happens only on the bounded start paths). ``"exclusive"``
iff macOS AND RX/TX resolve to the same device index AND the device is
a real CODEC (not a virtual loopback).
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
    dev = _codec(name="RigPlane Virtual Cable")
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


@pytest.mark.asyncio
async def test_driver_duplex_mode_cold_cache_returns_safe_default() -> None:
    """MOR-2892: a cold cache reads ``"full"`` without resolving.

    ``duplex_mode`` is a pure cache read — resolution happens only on
    the bounded start paths, so a fresh driver (and a cache cleared by
    ``set_serial_port``) answers the ``"full"`` safe default. The
    no-PortAudio-touch regression lives in
    ``test_usb_audio_portaudio_off_loop_mor2892.py``.
    """
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

    assert driver.duplex_mode == "full"
    driver.set_serial_port("/dev/cu.usbserial-9931")
    assert driver.duplex_mode == "full"


@pytest.mark.asyncio
async def test_driver_duplex_mode_warm_cache_reflects_resolved_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a bounded start warmed the cache, the pure read answers.

    ``start_rx`` resolves the pair through the bounded off-loop path
    (MOR-2892); the subsequent property read reflects the resolved
    same-CODEC policy without any further device work.
    """
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

    await driver.start_rx(lambda _frame: None)
    try:
        assert driver.duplex_mode == "exclusive"
        assert driver.selected_rx_device is not None
        assert driver.selected_tx_device is not None
    finally:
        await driver.stop_rx()
