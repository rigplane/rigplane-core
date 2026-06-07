"""Regression tests for MOR-236 — Xiegu X6200 RX USB audio delivers 0 frames.

Root cause: the ``rigs/x6200.toml`` profile had no ``[audio]`` section, so the
radio's RX codec fell back to the global default ``PCM_2CH_16BIT`` (stereo).
``UsbAudioDriver.start_rx`` then asked PortAudio for a 2-channel ``InputStream``
on the mono Xiegu USB CODEC, which PortAudio rejects with "Invalid number of
channels" (PaErrorCode -9998). The ``AudioBus`` caught it as "failed to start
RX" and the browser received zero RX frames.

These tests pin the mono codec resolution and exercise the
capture -> AudioBus -> AudioBroadcaster -> client wiring with a backend that
reproduces PortAudio's channel-count rejection.
"""

from __future__ import annotations

import asyncio

import pytest

from rigplane.audio.backend import (
    AudioDeviceId,
    AudioDeviceInfo,
    FakeRxStream,
    FakeTxStream,
)
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.backends.config import SerialBackendConfig
from rigplane.backends.factory import create_radio
from rigplane.backends.ic705.serial import Ic705SerialRadio
from rigplane.types import AudioCodec
from rigplane.web.handlers import AudioBroadcaster
from rigplane.web.protocol import AUDIO_CODEC_PCM16, AUDIO_HEADER_SIZE


class _MonoUsbAudioBackend:
    """Fake backend that mimics a mono USB CODEC like the Xiegu X6200.

    ``open_rx`` rejects any channel count other than the single input
    channel the device exposes, reproducing PortAudio's PaErrorCode -9998
    ("Invalid number of channels") that is the MOR-236 failure mode.
    """

    def __init__(self, *, input_channels: int = 1) -> None:
        self._device = AudioDeviceInfo(
            id=AudioDeviceId(0),
            name="USB Audio Device",
            input_channels=input_channels,
            output_channels=input_channels,
            default_samplerate=48_000,
            is_default_input=True,
            is_default_output=True,
        )
        self.rx_streams: list[FakeRxStream] = []
        self.tx_streams: list[FakeTxStream] = []

    def list_devices(self) -> list[AudioDeviceInfo]:
        return [self._device]

    def check_sample_rate(
        self, device: AudioDeviceId, sample_rate: int, *, direction: str = "rx"
    ) -> bool:
        return sample_rate in (48_000, 24_000, 16_000, 8_000)

    def open_rx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        deliver_channels: int | None = None,
        rx_audio_channel: str = "mix",
    ) -> FakeRxStream:
        # ``deliver_channels`` is the post-downmix consumer count (MOR-504); the
        # mono Xiegu device opens at its single native channel, so it is unused
        # here. ``channels`` is the OS open count the device validates.
        if channels != self._device.input_channels:
            raise RuntimeError(
                "Error opening InputStream: Invalid number of channels "
                f"[requested {channels}, device exposes "
                f"{self._device.input_channels}]"
            )
        stream = FakeRxStream()
        self.rx_streams.append(stream)
        return stream

    def open_tx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> FakeTxStream:
        stream = FakeTxStream()
        self.tx_streams.append(stream)
        return stream


class _FakeSerialCivLink:
    """Minimal serial CI-V link double so the radio reports ``connected``."""

    def __init__(self) -> None:
        self.connected = False
        self.ready = False
        self.healthy = False

    async def connect(self) -> None:
        self.connected = True
        self.ready = True
        self.healthy = True

    async def disconnect(self) -> None:
        self.connected = False
        self.ready = False
        self.healthy = False

    async def send(self, frame: bytes) -> None:
        _ = frame

    async def receive(self, timeout: float | None = None) -> bytes | None:
        await asyncio.sleep(0.02 if timeout is None else min(timeout, 0.02))
        return None


def test_x6200_profile_resolves_mono_rx_codec() -> None:
    """X6200 must resolve to a 1-channel RX codec (root-cause guard)."""
    radio = create_radio(SerialBackendConfig(device="/dev/null", model="X6200"))
    assert AudioCodec(radio.audio_codec) == AudioCodec.PCM_1CH_16BIT
    assert radio.profile.codec_preference is not None
    assert radio.profile.codec_preference[0] == "PCM_1CH_16BIT"
    # The serial audio path derives RX/TX channel count from the codec; a
    # stereo codec is what made PortAudio reject the mono Xiegu InputStream.
    assert radio._serial_audio_channels_for_codec() == 1


@pytest.mark.asyncio
async def test_usb_driver_rx_clamps_stereo_on_mono_device() -> None:
    """A stereo request on a mono device clamps to 1 ch (MOR-238 self-heal).

    Before MOR-238 this raised PortAudio's -9998 "Invalid number of channels"
    (the MOR-236 failure mode). The driver now clamps the open to the device's
    real ``input_channels`` so the capture starts regardless of the requested
    codec's channel count.
    """
    backend = _MonoUsbAudioBackend()
    driver = UsbAudioDriver(serial_port=None, backend=backend)
    await driver.start_rx(lambda _frame: None, channels=2)
    assert driver.rx_running
    contract = driver.usb_audio_contract
    assert contract is not None and contract.rx is not None
    assert contract.rx.channels == 1
    assert contract.rx.channel_source == "device-clamp"
    await driver.stop_rx()


@pytest.mark.asyncio
async def test_usb_driver_rx_mono_capture_feeds_callback() -> None:
    """A 1-channel capture opens and pushes PCM frames to the callback."""
    backend = _MonoUsbAudioBackend()
    driver = UsbAudioDriver(serial_port=None, backend=backend)
    received: list[bytes] = []

    await driver.start_rx(received.append, channels=1)
    assert driver.rx_running
    assert len(backend.rx_streams) == 1

    pcm = b"\x10\x20" * 480
    backend.rx_streams[0].inject_frame(pcm)
    assert received == [pcm]

    await driver.stop_rx()
    assert not driver.rx_running


@pytest.mark.asyncio
async def test_x6200_serial_capture_feeds_broadcaster() -> None:
    """End-to-end: X6200 mono capture reaches a broadcaster client queue.

    Uses the real ``Ic705SerialRadio`` resolved against the X6200 profile so
    the RX channel count is whatever the profile produces. With the mono
    backend, a regression back to a stereo codec would raise inside
    ``start_audio_rx_opus`` and the client would receive no frames.
    """
    backend = _MonoUsbAudioBackend()
    audio_driver = UsbAudioDriver(serial_port=None, backend=backend)
    radio = Ic705SerialRadio(
        device="/dev/tty.usbmodem-X6200",
        model="X6200",
        civ_link=_FakeSerialCivLink(),
        audio_driver=audio_driver,
    )
    await radio.connect()
    broadcaster = AudioBroadcaster(radio)
    queue = await broadcaster.subscribe()
    try:
        # The relay/bus subscription has opened the RX stream by now.
        assert len(backend.rx_streams) == 1, "RX capture did not open"
        pcm_frame = b"\xab\xcd" * 480
        backend.rx_streams[0].inject_frame(pcm_frame)
        web_frame = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert web_frame[1] == AUDIO_CODEC_PCM16
        assert web_frame[AUDIO_HEADER_SIZE:] == pcm_frame
    finally:
        await broadcaster.unsubscribe(queue)
        await radio.disconnect()
