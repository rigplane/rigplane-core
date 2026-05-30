"""MOR-242: serial Icom backends must arm USB-audio PCM TX.

Reproduces the "PCM TX not started" spam where ``start_audio_tx_pcm`` opened
the USB CODEC stream but never set ``_pcm_tx_fmt``, so every
``push_audio_tx_pcm`` raised ``RuntimeError`` from the runtime mixin guard and
no frame reached the radio.

The fakes are reused from the IC-7610 serial test module so there are no
one-off mocks (CLAUDE.md: "MagicMock hides signature bugs").
"""

from __future__ import annotations

import asyncio

import pytest

from rigplane.audio.backend import (
    AudioDeviceId,
    AudioDeviceInfo,
    FakeAudioBackend,
)
from rigplane.audio_bridge import AudioBridge, SAMPLES_PER_FRAME
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.ic9700 import Ic9700SerialRadio

from test_icom7610_serial_radio import _FakeSerialCivLink, _FakeUsbAudioDriver


# A 20ms mono s16le frame at 48kHz = 960 samples * 2 bytes = 1920 bytes.
_FRAME = (1000).to_bytes(2, "little", signed=True) * SAMPLES_PER_FRAME


def _serial_radio_factories():
    """(id, factory) pairs for every non-7610 serial Icom backend.

    Includes the IC-705 backend twice: once with its default model and once as
    the X6200 (which the factory routes through the IC-705 backend).
    """
    return [
        pytest.param(
            lambda drv: Ic705SerialRadio(
                device="/dev/ttyUSB0",
                civ_link=_FakeSerialCivLink(),
                audio_driver=drv,
            ),
            id="ic705",
        ),
        pytest.param(
            lambda drv: Ic705SerialRadio(
                device="/dev/ttyUSB0",
                civ_link=_FakeSerialCivLink(),
                audio_driver=drv,
                model="X6200",
            ),
            id="x6200",
        ),
        pytest.param(
            lambda drv: Ic7300SerialRadio(
                device="/dev/ttyUSB0",
                civ_link=_FakeSerialCivLink(),
                audio_driver=drv,
            ),
            id="ic7300",
        ),
        pytest.param(
            lambda drv: Ic9700SerialRadio(
                device="/dev/ttyUSB0",
                civ_link=_FakeSerialCivLink(),
                audio_driver=drv,
            ),
            id="ic9700",
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("make_radio", _serial_radio_factories())
async def test_serial_backend_arms_pcm_tx_and_pushes_frame(make_radio) -> None:  # type: ignore[no-untyped-def]
    """start_audio_tx_pcm arms _pcm_tx_fmt and push_audio_tx_pcm reaches driver.

    On the unfixed base, start_audio_tx_pcm never set _pcm_tx_fmt and the base
    had no push_audio_tx_pcm override, so the mixin guard raised
    "PCM TX not started" on every frame.
    """
    usb_audio = _FakeUsbAudioDriver()
    radio = make_radio(usb_audio)

    await radio.connect()
    await radio.start_audio_tx_pcm()

    assert radio._pcm_tx_fmt is not None
    # USB CODEC mic input is mono-only; channels must be clamped to 1.
    assert usb_audio.tx_start_kwargs.get("channels") == 1

    await radio.push_audio_tx_pcm(_FRAME)
    assert usb_audio.tx_frames, "push_audio_tx_pcm must reach the USB audio driver"
    assert usb_audio.tx_frames[0] == _FRAME

    await radio.stop_audio_tx_pcm()
    assert radio._pcm_tx_fmt is None

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_backend_push_before_start_raises() -> None:
    """push_audio_tx_pcm before start raises the armed-format guard."""
    usb_audio = _FakeUsbAudioDriver()
    radio = Ic705SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
    )
    await radio.connect()
    with pytest.raises(RuntimeError, match="PCM TX not started"):
        await radio.push_audio_tx_pcm(_FRAME)
    await radio.disconnect()


# ---------------------------------------------------------------------------
# Bridge reproducer — a real AudioBridge + FakeAudioBackend against a
# fake-driver-backed serial radio: captured frame must reach the driver and
# raise no RuntimeError. AsyncMock-based bridge tests could not catch this.
# ---------------------------------------------------------------------------


def _bridge_backend() -> FakeAudioBackend:
    return FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(0),
                name="Built-in Output",
                input_channels=0,
                output_channels=2,
            ),
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="BlackHole 2ch",
                input_channels=2,
                output_channels=2,
            ),
        ]
    )


@pytest.mark.asyncio
async def test_bridge_tx_reaches_serial_driver_without_runtime_error() -> None:
    """MOR-242 end-to-end: bridge TX loopback frame reaches the serial driver."""
    usb_audio = _FakeUsbAudioDriver()
    radio = Ic705SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
    )
    await radio.connect()

    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=True, backend=backend
    )
    await bridge.start()

    loud = (2000).to_bytes(2, "little", signed=True) * SAMPLES_PER_FRAME
    backend.rx_streams[0].inject_frame(loud)
    await asyncio.sleep(0.1)

    assert bridge._tx_enabled is True
    assert bridge._tx_frames > 0
    assert usb_audio.tx_frames, "loopback frame must reach the serial USB driver"

    await bridge.stop()
    await radio.disconnect()


# ---------------------------------------------------------------------------
# Part B2 — bridge degrades to RX-only when the radio rejects TX, logging once.
# ---------------------------------------------------------------------------


class _TxRejectingRadio:
    """Serial-style radio whose start_audio_tx_pcm raises RuntimeError."""

    def __init__(self) -> None:
        from rigplane.audio_bus import AudioBus
        from rigplane.types import AudioCodec

        self.audio_codec = AudioCodec.PCM_1CH_16BIT
        self.audio_bus = AudioBus(self)
        self.start_tx_calls = 0
        self.push_calls = 0

    async def start_audio_rx_opus(self, callback, **kwargs) -> None:  # type: ignore[no-untyped-def]
        return None

    async def stop_audio_rx_opus(self) -> None:
        return None

    async def start_audio_tx_pcm(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.start_tx_calls += 1
        raise RuntimeError("PCM TX not started")

    async def stop_audio_tx_pcm(self) -> None:
        return None

    async def push_audio_tx_pcm(self, pcm_bytes) -> None:  # type: ignore[no-untyped-def]
        self.push_calls += 1


@pytest.mark.asyncio
async def test_bridge_downgrades_to_rx_only_when_tx_rejected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A radio that rejects TX start → bridge runs RX-only, logs once, no _tx_loop."""
    import logging

    radio = _TxRejectingRadio()
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=True, backend=backend
    )

    with caplog.at_level(logging.WARNING, logger="rigplane.audio.bridge"):
        await bridge.start()

    # Degraded to RX-only.
    assert bridge._tx_enabled is False
    assert bridge._tx_started is False
    assert bridge._tx_task is None

    # RX still works: a published packet drives the RX loop.
    assert radio.audio_bus.subscriber_count == 1

    # No per-frame spam: at most one WARNING about the TX rejection.
    tx_warnings = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and "RX-only" in r.getMessage()
    ]
    assert len(tx_warnings) == 1
    assert radio.push_calls == 0

    await bridge.stop()
