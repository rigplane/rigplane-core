"""Opt-in hardware smoke test: X6200 serial USB-audio TX path arms (MOR-242).

SAFETY: this test NEVER engages PTT and NEVER transmits RF. It only verifies
that the serial backend's PCM TX path arms (``_pcm_tx_fmt`` set) and that a few
PCM frames are accepted by the USB-audio OUTPUT device at the driver level.
Pushing PCM to the USB audio out device without keying PTT does not transmit.

Skipped by default. Enable with ``RIGPLANE_HW_SMOKE=1`` on a machine with a
real X6200 attached over USB CI-V. Mirrors the env-gated convention used by
``tests/hardware/test_ic7610_audio_pipeline.py``.
"""

from __future__ import annotations

import os

import pytest

from rigplane.backends.ic705 import Ic705SerialRadio

pytestmark = pytest.mark.hardware

_ENABLE_ENV = "RIGPLANE_HW_SMOKE"
_DEVICE_ENV = "RIGPLANE_HW_X6200_SERIAL"
_BAUD_ENV = "RIGPLANE_HW_X6200_BAUD"

# 20ms mono s16le frame at 48kHz: 960 samples * 2 bytes.
_FRAME = (1500).to_bytes(2, "little", signed=True) * 960


def _flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@pytest.mark.asyncio
async def test_x6200_serial_tx_pcm_path_arms_without_transmitting() -> None:
    if not _flag_enabled(_ENABLE_ENV):
        pytest.skip(f"Set {_ENABLE_ENV}=1 to run the X6200 serial TX smoke test")

    device = os.environ.get(_DEVICE_ENV, "").strip()
    if not device:
        pytest.skip(f"Set {_DEVICE_ENV}=/dev/tty... to point at the X6200 serial port")

    baud = int(os.environ.get(_BAUD_ENV, "115200"))

    radio = Ic705SerialRadio(device=device, baudrate=baud, model="X6200")
    await radio.connect()
    try:
        # Arm the PCM TX path. This opens the USB CODEC OUTPUT stream only —
        # no PTT, no RF.
        await radio.start_audio_tx_pcm()
        assert radio._pcm_tx_fmt is not None, "TX PCM path must arm _pcm_tx_fmt"

        # Push a few frames to the USB audio OUTPUT device. Without PTT this
        # does not transmit; it only proves the driver accepts frames.
        for _ in range(5):
            await radio.push_audio_tx_pcm(_FRAME)
    finally:
        await radio.stop_audio_tx_pcm()
        await radio.disconnect()
