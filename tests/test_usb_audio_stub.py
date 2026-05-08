"""Deterministic unit tests for production USB audio driver contracts."""

from __future__ import annotations


import pytest

from rigplane.audio.backend import (
    AudioDeviceId,
    AudioDeviceInfo,
    FakeAudioBackend,
)
from rigplane.backends.icom7610.drivers.usb_audio import (
    AudioDeviceSelectionError,
    AudioDriverLifecycleError,
    UsbAudioDevice,
    UsbAudioDriver,
    select_usb_audio_devices,
)


def _fake_devices() -> list[AudioDeviceInfo]:
    return [
        AudioDeviceInfo(
            id=AudioDeviceId(0),
            name="MacBook Pro Speakers",
            input_channels=0,
            output_channels=2,
            default_samplerate=48_000,
        ),
        AudioDeviceInfo(
            id=AudioDeviceId(1),
            name="USB Audio CODEC",
            input_channels=2,
            output_channels=2,
            default_samplerate=48_000,
            is_default_input=True,
            is_default_output=True,
        ),
        AudioDeviceInfo(
            id=AudioDeviceId(2),
            name="IC-7610 USB Audio",
            input_channels=2,
            output_channels=2,
            default_samplerate=48_000,
        ),
    ]


def _make_driver(
    devices: list[AudioDeviceInfo] | None = None,
) -> tuple[UsbAudioDriver, FakeAudioBackend]:
    backend = FakeAudioBackend(devices or _fake_devices())
    driver = UsbAudioDriver(backend=backend)
    return driver, backend


def test_select_usb_audio_devices_explicit_overrides_take_precedence() -> None:
    devices = [
        UsbAudioDevice(index=1, name="A", input_channels=2, output_channels=2),
        UsbAudioDevice(index=2, name="B", input_channels=2, output_channels=2),
    ]
    selected_rx, selected_tx = select_usb_audio_devices(
        devices,
        rx_device="B",
        tx_device="A",
    )
    assert selected_rx.name == "B"
    assert selected_tx.name == "A"


def test_select_usb_audio_devices_auto_detect_prefers_usb_audio_codec() -> None:
    # Generic "USB Audio CODEC" now ranks above vendor-specific names so the
    # driver works equally well for Icom, Yaesu and Kenwood radios (all use
    # the same Burr-Brown/TI USB Audio Class chip with this name).
    devices = [
        UsbAudioDevice(
            index=0,
            name="Default System Input",
            input_channels=2,
            output_channels=0,
            is_default_input=True,
        ),
        UsbAudioDevice(
            index=1,
            name="USB Audio CODEC",
            input_channels=2,
            output_channels=2,
        ),
        UsbAudioDevice(
            index=2,
            name="IC-7610 USB Audio",
            input_channels=2,
            output_channels=2,
        ),
    ]
    selected_rx, selected_tx = select_usb_audio_devices(devices)
    assert selected_rx.name == "USB Audio CODEC"
    assert selected_tx.name == "USB Audio CODEC"


def test_select_usb_audio_devices_invalid_override_raises_clear_error() -> None:
    devices = [
        UsbAudioDevice(
            index=1, name="USB Audio CODEC", input_channels=2, output_channels=2
        ),
    ]
    with pytest.raises(AudioDeviceSelectionError, match="Unknown RX device"):
        select_usb_audio_devices(devices, rx_device="Not Existing")


def test_select_usb_audio_devices_missing_directional_capability_raises() -> None:
    devices = [
        UsbAudioDevice(index=1, name="InputOnly", input_channels=2, output_channels=0),
    ]
    with pytest.raises(
        AudioDeviceSelectionError, match="No suitable TX USB audio device"
    ):
        select_usb_audio_devices(devices)


@pytest.mark.asyncio
async def test_usb_audio_driver_lifecycle_start_stop_and_io() -> None:
    driver, backend = _make_driver()

    received_frames: list[bytes] = []
    pcm_frame = b"\x11\x22" * 960

    await driver.start_rx(received_frames.append)
    assert driver.rx_running is True
    assert len(backend.rx_streams) == 1
    rx_stream = backend.rx_streams[0]
    assert rx_stream.running is True

    # Inject a frame via the fake stream
    rx_stream.inject_frame(pcm_frame)
    assert received_frames == [pcm_frame]

    await driver.start_tx()
    assert driver.tx_running is True
    assert len(backend.tx_streams) == 1
    tx_stream = backend.tx_streams[0]
    assert tx_stream.running is True

    await driver._push_tx_pcm(pcm_frame)
    assert tx_stream.written_frames == [pcm_frame]

    await driver.stop_tx()
    await driver.stop_rx()
    assert driver.rx_running is False
    assert driver.tx_running is False
    assert rx_stream.stopped_count == 1
    assert tx_stream.stopped_count == 1


@pytest.mark.asyncio
async def test_usb_audio_driver_double_start_and_missing_tx_guardrails() -> None:
    driver, _ = _make_driver()
    await driver.start_rx(lambda _frame: None)
    with pytest.raises(AudioDriverLifecycleError, match="already started"):
        await driver.start_rx(lambda _frame: None)
    with pytest.raises(
        AudioDriverLifecycleError, match="Audio TX stream is not started"
    ):
        await driver._push_tx_pcm(b"\x00\x01" * 960)
    await driver.stop_rx()
    await driver.stop_rx()  # idempotent


@pytest.mark.asyncio
async def test_usb_audio_driver_missing_backend_dependencies_is_actionable() -> None:
    from rigplane.audio.backend import PortAudioBackend

    def _missing_sounddevice() -> tuple[object, object]:
        raise ImportError("No module named 'sounddevice'")

    backend = PortAudioBackend(dependency_loader=_missing_sounddevice)
    driver = UsbAudioDriver(backend=backend)
    with pytest.raises(ImportError, match="pip install rigplane\\[bridge\\]"):
        await driver.start_rx(lambda _frame: None)


@pytest.mark.asyncio
async def test_usb_audio_driver_list_devices_returns_usb_audio_devices() -> None:
    driver, _ = _make_driver()
    devices = driver.list_devices()
    assert len(devices) == 3
    assert all(isinstance(d, UsbAudioDevice) for d in devices)
    assert devices[1].name == "USB Audio CODEC"
    assert devices[1].index == 1


@pytest.mark.asyncio
async def test_usb_audio_driver_selected_devices_populated_after_start() -> None:
    driver, _ = _make_driver()
    assert driver.selected_rx_device is None
    assert driver.selected_tx_device is None
    await driver.start_rx(lambda _: None)
    assert driver.selected_rx_device is not None
    assert driver.selected_rx_device.name == "USB Audio CODEC"
    await driver.stop_rx()


@pytest.mark.asyncio
async def test_usb_audio_driver_auto_sample_rate_falls_back_and_reports_contract() -> (
    None
):
    backend = FakeAudioBackend(_fake_devices(), supported_sample_rates={16_000})
    driver = UsbAudioDriver(backend=backend, sample_rate=48_000)

    await driver.start_rx(lambda _: None)

    contract = driver.usb_audio_contract
    assert contract is not None
    assert contract.rx.sample_rate_hz == 16_000
    assert contract.rx.sample_rate_source == "fallback"
    assert contract.rx.fallback_reason == "sample-rate-48000-unsupported"
    assert contract.rx.device.name == "USB Audio CODEC"
    assert contract.to_dict()["rx"]["sample_rate_hz"] == 16_000
    assert contract.to_dict()["rx"]["sample_rate_source"] == "fallback"
    await driver.stop_rx()


@pytest.mark.asyncio
async def test_usb_audio_driver_explicit_sample_rate_failure_is_clear() -> None:
    backend = FakeAudioBackend(_fake_devices(), supported_sample_rates={16_000})
    driver = UsbAudioDriver(backend=backend, sample_rate=48_000)

    with pytest.raises(
        AudioDriverLifecycleError,
        match="Explicit RX sample rate 48000 Hz is not supported",
    ):
        await driver.start_rx(
            lambda _: None,
            sample_rate=48_000,
            allow_sample_rate_fallback=False,
        )


@pytest.mark.asyncio
async def test_usb_audio_driver_explicit_sample_rate_reports_explicit_source() -> None:
    backend = FakeAudioBackend(_fake_devices(), supported_sample_rates={48_000})
    driver = UsbAudioDriver(backend=backend)

    await driver.start_rx(
        lambda _: None,
        sample_rate=48_000,
        allow_sample_rate_fallback=False,
    )

    assert driver.usb_audio_contract is not None
    assert driver.usb_audio_contract.rx is not None
    assert driver.usb_audio_contract.rx.sample_rate_source == "explicit"
    await driver.stop_rx()


@pytest.mark.asyncio
async def test_usb_audio_driver_reports_rx_and_tx_effective_contract() -> None:
    backend = FakeAudioBackend(_fake_devices(), supported_sample_rates={48_000})
    driver = UsbAudioDriver(backend=backend)

    await driver.start_rx(lambda _: None)
    await driver.start_tx()

    contract = driver.usb_audio_contract
    assert contract is not None
    assert contract.rx.sample_rate_source == "default"
    assert contract.tx is not None
    assert contract.tx.sample_rate_hz == 48_000
    assert contract.tx.direction == "tx"
    assert contract.tx.device.name == "USB Audio CODEC"
    await driver.stop_tx()
    await driver.stop_rx()
