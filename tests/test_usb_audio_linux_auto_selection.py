"""Linux serial auto selection with fake audio and USB topology."""

from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from rigplane.audio import _usb_resolve, usb_driver
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.usb_driver import AudioDeviceSelectionError, UsbAudioDriver


def _driver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    usb_present: bool = True,
    usb_channels: tuple[int, int] = (2, 2),
    serial_port: str | None = "/dev/ttyUSB0",
    rx_device: str | None = None,
    tx_device: str | None = None,
) -> tuple[UsbAudioDriver, FakeAudioBackend]:
    monkeypatch.setattr(usb_driver.platform, "system", lambda: "Linux")
    serial = tmp_path / "devices/pci0000:00/usb1/1-1/1-1.1"
    serial.mkdir(parents=True)
    tty = tmp_path / "class/tty/ttyUSB0"
    tty.mkdir(parents=True)
    (tty / "device").symlink_to(serial, target_is_directory=True)

    devices = [
        AudioDeviceInfo(
            id=AudioDeviceId(0),
            name="HDA Intel: Generic Analog (hw:0,0)",
            input_channels=2,
            output_channels=2,
            is_default_input=True,
            is_default_output=True,
        )
    ]
    if usb_present:
        audio = tmp_path / "devices/pci0000:00/usb1/1-1/1-1.2"
        audio.mkdir(parents=True)
        (audio / "product").write_text("USB Audio CODEC\n")
        card = tmp_path / "class/sound/card1"
        card.mkdir(parents=True)
        (card / "device").symlink_to(audio, target_is_directory=True)
        devices.append(
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="USB Audio CODEC: USB Audio (hw:1,0)",
                input_channels=usb_channels[0],
                output_channels=usb_channels[1],
                platform_uid="hw:1,0",
            )
        )

    backend = FakeAudioBackend(devices)
    metadata = SimpleNamespace(
        query_devices=lambda: [
            {
                "index": int(device.id),
                "name": device.name,
                "max_input_channels": device.input_channels,
                "max_output_channels": device.output_channels,
                "default_samplerate": device.default_samplerate,
            }
            for device in backend.list_devices()
        ]
    )
    monkeypatch.setattr(usb_driver, "_extract_sounddevice_module", lambda _: metadata)
    monkeypatch.setattr(
        _usb_resolve,
        "resolve_audio_for_serial_port",
        partial(_usb_resolve._resolve_linux, sysfs_root=str(tmp_path)),
    )
    return (
        UsbAudioDriver(
            backend=backend,
            serial_port=serial_port,
            rx_device=rx_device,
            tx_device=tx_device,
        ),
        backend,
    )


async def _close(driver: UsbAudioDriver) -> None:
    if driver.tx_running:
        await driver.stop_tx()
    if driver.rx_running:
        await driver.stop_rx()


@pytest.mark.parametrize("direction", ["rx", "tx"])
@pytest.mark.parametrize(
    "usb_present,usb_channels",
    [(False, (2, 2)), (True, (0, 2)), (True, (2, 0))],
    ids=["no-usb", "missing-rx", "missing-tx"],
)
async def test_linux_serial_auto_requires_complete_usb_pair_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    direction: str,
    usb_present: bool,
    usb_channels: tuple[int, int],
) -> None:
    driver, backend = _driver(
        tmp_path,
        monkeypatch,
        usb_present=usb_present,
        usb_channels=usb_channels,
    )
    try:
        with pytest.raises(AudioDeviceSelectionError, match="Linux.*USB"):
            if direction == "rx":
                await driver.start_rx(lambda _: None)
            else:
                await driver.start_tx()
        assert not backend.rx_streams
        assert not backend.tx_streams
        assert not backend.duplex_streams
        assert driver.selected_rx_device is None
        assert driver.selected_tx_device is None
    finally:
        await _close(driver)


async def test_linux_serial_auto_opens_topology_matched_usb_instead_of_hda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, backend = _driver(tmp_path, monkeypatch)
    try:
        await driver.start_rx(lambda _: None)
        await driver.start_tx()
        assert driver.selected_rx_device is not None
        assert driver.selected_tx_device is not None
        assert driver.selected_rx_device.index == 1
        assert driver.selected_tx_device.index == 1
        assert len(backend.rx_streams) == len(backend.tx_streams) == 1
    finally:
        await _close(driver)


@pytest.mark.parametrize(
    "rx_device,tx_device",
    [("hw:1,0", None), (None, "hw:1,0"), ("hw:1,0", "hw:1,0")],
    ids=["explicit-rx", "explicit-tx", "explicit-pair"],
)
async def test_linux_serial_explicit_usb_selection_keeps_existing_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rx_device: str | None,
    tx_device: str | None,
) -> None:
    driver, _ = _driver(tmp_path, monkeypatch, rx_device=rx_device, tx_device=tx_device)
    try:
        await driver.start_rx(lambda _: None)
        await driver.start_tx()
        assert driver.selected_rx_device is not None
        assert driver.selected_tx_device is not None
        assert driver.selected_rx_device.index == driver.selected_tx_device.index == 1
    finally:
        await _close(driver)


async def test_linux_without_serial_keeps_standalone_device_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, _ = _driver(tmp_path, monkeypatch, usb_present=False, serial_port=None)
    try:
        await driver.start_rx(lambda _: None)
        assert driver.selected_rx_device is not None
        assert driver.selected_rx_device.index == 0
    finally:
        await _close(driver)


async def test_linux_auto_guard_does_not_change_darwin_selection_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, _ = _driver(tmp_path, monkeypatch, usb_present=False)
    monkeypatch.setattr(usb_driver.platform, "system", lambda: "Darwin")
    try:
        await driver.start_rx(lambda _: None)
        assert driver.selected_rx_device is not None
        assert driver.selected_rx_device.index == 0
    finally:
        await _close(driver)
