"""Synthetic Linux USB driver selection; not hardware-verified."""

import pytest
from test_usb_pairing_linux import LINUX_PORT, _inject_linux

from rigplane.audio.usb_driver import AudioDeviceSelectionError, UsbAudioDriver


@pytest.mark.parametrize("direction", ["rx", "tx"])
@pytest.mark.parametrize("channels", [(0, 2), (2, 0), (0, 0)])
async def test_linux_serial_auto_requires_complete_usb_pair_before_open(
    tmp_path, monkeypatch, direction, channels
):
    """Synthetic missing channel directions; not hardware-verified."""
    backend, sd, identity, _ = _inject_linux(tmp_path, monkeypatch)
    row = sd.query_devices()[0]
    row["max_input_channels"], row["max_output_channels"] = channels
    driver = UsbAudioDriver(
        backend=backend, serial_port=LINUX_PORT, usb_identity=identity
    )
    with pytest.raises(RuntimeError):
        if direction == "rx":
            await driver.start_rx(lambda _: None)
        else:
            await driver.start_tx()
    assert not (backend.rx_streams or backend.tx_streams or backend.duplex_streams)
    assert driver.selected_rx_device is None
    assert driver.selected_tx_device is None


@pytest.mark.parametrize(
    "rx_device,tx_device", [("hw:2,0", None), (None, "hw:2,0"), ("hw:2,0", "hw:2,0")]
)
async def test_linux_serial_explicit_usb_selection_keeps_existing_contract(
    tmp_path, monkeypatch, rx_device, tx_device
):
    """Synthetic ALSA overrides; not hardware-verified."""
    backend, _, _, _ = _inject_linux(tmp_path, monkeypatch)
    driver = UsbAudioDriver(
        backend=backend,
        serial_port=LINUX_PORT,
        rx_device=rx_device,
        tx_device=tx_device,
    )
    try:
        await driver.start_rx(lambda _: None)
        await driver.start_tx()
        assert driver.selected_rx_device.index == driver.selected_tx_device.index == 0
        assert len(backend.rx_streams) == len(backend.tx_streams) == 1
    finally:
        await driver.stop_tx()
        await driver.stop_rx()


async def test_linux_without_serial_keeps_standalone_device_selection(
    tmp_path, monkeypatch
):
    """Synthetic standalone backend; not hardware-verified."""
    backend, _, _, _ = _inject_linux(tmp_path, monkeypatch)
    driver = UsbAudioDriver(backend=backend)
    try:
        await driver.start_rx(lambda _: None)
        assert driver.selected_rx_device.index == 0
    finally:
        await driver.stop_rx()


async def test_linux_missing_stable_id_refuses_without_falling_back(
    tmp_path, monkeypatch
):
    """Synthetic stale picker identity; not hardware-verified."""
    backend, _, _, _ = _inject_linux(tmp_path, monkeypatch)
    driver = UsbAudioDriver(
        backend=backend,
        serial_port=LINUX_PORT,
        rx_device="ALSA|missing",
        tx_device="ALSA|missing",
    )
    with pytest.raises(AudioDeviceSelectionError):
        await driver.start_rx(lambda _: None)
    assert not (backend.rx_streams or backend.tx_streams or backend.duplex_streams)
