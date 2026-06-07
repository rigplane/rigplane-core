"""Regression tests for MOR-238 — clamp USB audio channels to device capability.

USB RX/TX channel count is derived from the codec (``_CHANNELS_BY_CODEC`` /
``_serial_audio_channels_for_codec``), not from the device. A mono USB codec
(1 input channel) whose profile lacks a mono ``codec_preference`` entry made the
driver request a 2-channel ``InputStream`` → PortAudio PaErrorCode -9998
("Invalid number of channels") → 0 RX frames / silent LIVE audio. This is the
class of bug behind MOR-236, which was worked around per-profile.

Sample rate already auto-negotiates against the device. These tests pin that the
channel count is now device-capability-aware too: ``UsbAudioDriver`` clamps the
open to ``device.input_channels`` (RX) / ``device.output_channels`` (TX), so any
mono/stereo USB codec self-heals with no per-profile entry — including the X6200
path with its ``codec_preference`` removed.
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
from rigplane.backends.ic705.serial import Ic705SerialRadio
from rigplane.types import AudioCodec


class _StrictChannelBackend:
    """Fake backend that rejects any open whose channel count ≠ device capability.

    Reproduces PortAudio's -9998 ("Invalid number of channels") so a test only
    passes if the driver clamps the requested channels to what the device
    actually exposes before opening the stream.
    """

    def __init__(self, *, input_channels: int = 1, output_channels: int = 1) -> None:
        self._device = AudioDeviceInfo(
            id=AudioDeviceId(0),
            name="USB Audio Device",
            input_channels=input_channels,
            output_channels=output_channels,
            default_samplerate=48_000,
            is_default_input=True,
            is_default_output=True,
        )
        self.rx_streams: list[FakeRxStream] = []
        self.tx_streams: list[FakeTxStream] = []
        self.rx_open_channels: list[int] = []
        self.tx_open_channels: list[int] = []
        self.rx_deliver_channels: list[int | None] = []

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
        self.rx_open_channels.append(channels)
        self.rx_deliver_channels.append(deliver_channels)
        # ``channels`` is the OS open count: the strict backend models a device
        # that rejects any open whose channel count ≠ its capability (PortAudio
        # -9998 / AUHAL -10863). The driver must open at the device-native count
        # — clamping DOWN for an over-request, opening NATIVE for a mono request
        # on a stereo device (then downmixing in software).
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
        self.tx_open_channels.append(channels)
        if channels != self._device.output_channels:
            raise RuntimeError(
                "Error opening OutputStream: Invalid number of channels "
                f"[requested {channels}, device exposes "
                f"{self._device.output_channels}]"
            )
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


@pytest.mark.asyncio
async def test_rx_stereo_request_clamps_to_mono_device() -> None:
    """A 2-channel RX request opens at 1 ch on a mono-input device."""
    backend = _StrictChannelBackend(input_channels=1)
    driver = UsbAudioDriver(serial_port=None, backend=backend)
    received: list[bytes] = []

    await driver.start_rx(received.append, channels=2)

    assert driver.rx_running
    assert backend.rx_open_channels == [1]
    contract = driver.usb_audio_contract
    assert contract is not None and contract.rx is not None
    assert contract.rx.channels == 1
    assert contract.rx.channel_source == "device-clamp"
    assert contract.rx.fallback_reason == "channels-2-clamped-to-device-1"

    pcm = b"\x10\x20" * 480
    backend.rx_streams[0].inject_frame(pcm)
    assert received == [pcm]
    await driver.stop_rx()


@pytest.mark.asyncio
async def test_tx_stereo_request_clamps_to_mono_device() -> None:
    """A 2-channel TX request opens at 1 ch on a mono-output device."""
    backend = _StrictChannelBackend(output_channels=1)
    driver = UsbAudioDriver(serial_port=None, backend=backend)

    await driver.start_tx(channels=2)

    assert driver.tx_running
    assert backend.tx_open_channels == [1]
    contract = driver.usb_audio_contract
    assert contract is not None and contract.tx is not None
    assert contract.tx.channels == 1
    assert contract.tx.channel_source == "device-clamp"
    await driver.stop_tx()


@pytest.mark.asyncio
async def test_stereo_device_request_is_unchanged() -> None:
    """A stereo request on a stereo device is passed through verbatim."""
    backend = _StrictChannelBackend(input_channels=2, output_channels=2)
    driver = UsbAudioDriver(serial_port=None, backend=backend)

    await driver.start_rx(lambda _frame: None, channels=2)

    assert backend.rx_open_channels == [2]
    contract = driver.usb_audio_contract
    assert contract is not None and contract.rx is not None
    assert contract.rx.channels == 2
    assert contract.rx.channel_source == "requested"
    assert contract.rx.fallback_reason is None
    await driver.stop_rx()


@pytest.mark.asyncio
async def test_mono_request_on_stereo_device_opens_device_native() -> None:
    """A mono RX request on a stereo-native device opens at the device count.

    macOS CoreAudio/AUHAL refuses to open a 2-channel device at 1 channel
    (err -10863) → no frames → blank audio scope (MOR-504). The driver must
    reconcile UP to the device-native open count (with ``channel_source ==
    "device-native"``) and carry the mono request as the deliver target so the
    RX stream downmixes back to mono. This is the macOS analogue of the MOR-238
    over-request narrowing — previously the reconcile only narrowed, so a mono
    request on a stereo device stayed 1 ch and the open was rejected.
    """
    backend = _StrictChannelBackend(input_channels=2, output_channels=2)
    driver = UsbAudioDriver(serial_port=None, backend=backend)

    await driver.start_rx(lambda _frame: None, channels=1)

    assert driver.rx_running
    # Opened at the device-native 2 ch (no rejection), delivering mono.
    assert backend.rx_open_channels == [2]
    assert backend.rx_deliver_channels == [1]
    contract = driver.usb_audio_contract
    assert contract is not None and contract.rx is not None
    # ``channels`` stays the DELIVERED (mono) count; ``open_channels`` carries
    # the device-native open count the OS stream is actually opened at.
    assert contract.rx.channels == 1
    assert contract.rx.open_channels == 2
    assert contract.rx.effective_open_channels == 2
    assert contract.rx.channel_source == "device-native"
    assert contract.rx.fallback_reason == "channels-1-opened-as-device-2-downmix"
    await driver.stop_rx()


@pytest.mark.asyncio
async def test_mono_request_on_stereo_device_opens_native_and_downmixes() -> None:
    """End-to-end: stereo-native open + software downmix delivers mono average.

    Combines the driver-level reconciliation (open at device-native 2 ch, no
    rejection from the strict backend) with the PortAudio-level downmix (a
    2-ch interleaved s16le frame collapses to the per-pair mono average at the
    correct fixed mono byte length). FakeRxStream is a verbatim pass-through, so
    the downmix itself is pinned by ``test_portaudio_rx_stereo_native_downmix``
    below; here we assert the driver opens native + delivers mono and that the
    contract advertises the downmix.
    """
    backend = _StrictChannelBackend(input_channels=2, output_channels=2)
    driver = UsbAudioDriver(serial_port=None, backend=backend)
    received: list[bytes] = []

    # (a) open is not rejected → reconciled to device-native 2 ch.
    await driver.start_rx(received.append, channels=1)
    assert backend.rx_open_channels == [2]
    assert backend.rx_deliver_channels == [1]

    # (b) FakeRxStream is a pass-through; the real downmix lives in
    # _PortAudioRxStream (pinned separately). A frame injected here is delivered
    # verbatim, proving the consumer wiring survives the reconcile.
    pcm = b"\x10\x20\x30\x40" * 240  # 240 stereo s16le L/R pairs
    backend.rx_streams[0].inject_frame(pcm)
    assert received == [pcm]
    await driver.stop_rx()


@pytest.mark.asyncio
async def test_portaudio_rx_stereo_native_downmix_delivers_mono_average() -> None:
    """_PortAudioRxStream opened at 2 ch / deliver 1 ch downmixes to the average.

    Focused unit test for the MOR-504 downmix: a 2-ch interleaved s16le block
    is collapsed to the per-pair mono average and re-chunked to the DELIVER
    (mono) fixed-frame byte length (1920 bytes = 960 mono samples / 20 ms /
    48 kHz), NOT the native 2-ch length. Interleaved stereo must never reach
    the consumer (that halves the spectrum — bridge guard, issue #1381).
    """
    import struct

    from rigplane.audio.backend import PortAudioBackend

    class FakeIndata:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def tobytes(self) -> bytes:
            return self._payload

    captured_cb: dict[str, object] = {}

    class FakeSd:
        class InputStream:
            def __init__(self, **kw: object) -> None:
                captured_cb["cb"] = kw["callback"]

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def close(self) -> None:
                pass

    backend = PortAudioBackend(dependency_loader=lambda: (FakeSd(), object()))
    # Device-native open=2, deliver=1 → downmix path engaged.
    stream = backend.open_rx(
        AudioDeviceId(0),
        sample_rate=48_000,
        channels=2,
        frame_ms=20,
        deliver_channels=1,
    )

    received: list[bytes] = []
    await stream.start(received.append)
    cb = captured_cb["cb"]
    assert callable(cb)

    # 960 stereo L/R pairs (one whole 20 ms mono frame after downmix). L and R
    # differ so a wrong (non-averaging or pass-through) downmix is caught:
    # L = 1000, R = 2000 → mono average 1500.
    pairs = 960
    block = b"".join(struct.pack("<hh", 1000, 2000) for _ in range(pairs))
    cb(FakeIndata(block), pairs, None, None)  # type: ignore[operator]

    expected_frame = b"".join(struct.pack("<h", 1500) for _ in range(pairs))
    assert len(expected_frame) == 1920  # mono fixed-frame, not 3840 (stereo)
    assert received == [expected_frame]

    await stream.stop()


def test_portaudio_rx_stereo_native_downmix_truncates_odd_pair() -> None:
    """Downmix drops a trailing byte run that is not a whole L/R sample pair."""
    import struct

    from rigplane.audio.backend import _downmix_stereo_to_mono_s16le

    # Two whole L/R pairs + 3 dangling bytes (not a 4-byte stereo sample).
    pcm = struct.pack("<hh", 10, 30) + struct.pack("<hh", -20, -40) + b"\x01\x02\x03"
    mono = _downmix_stereo_to_mono_s16le(pcm)
    # (10+30)//2 = 20 ; (-20+-40)//2 = -30 ; dangling bytes dropped.
    assert mono == struct.pack("<h", 20) + struct.pack("<h", -30)


@pytest.mark.asyncio
async def test_zero_channel_device_is_not_clamped_to_zero() -> None:
    """A device advertising 0 channels for the direction is left untouched.

    Clamping to 0 would silently open an unusable stream; instead the request
    passes through so the open surfaces the real selection error.
    """
    backend = _StrictChannelBackend(input_channels=0, output_channels=2)
    driver = UsbAudioDriver(serial_port=None, backend=backend)

    # Device selection picks the device; the open then requests the real
    # requested channel count (2), which the 0-input device rejects.
    with pytest.raises(Exception):
        await driver.start_rx(lambda _frame: None, channels=2)
    # The clamp must not have rewritten the request down to 0 channels.
    assert 0 not in backend.rx_open_channels


@pytest.mark.asyncio
async def test_x6200_path_self_heals_without_codec_preference() -> None:
    """The X6200 mono capture flows even when the codec asks for stereo.

    Proves the clamp alone suffices: the radio is built with the *default*
    stereo ``PCM_2CH_16BIT`` codec (i.e. as if the X6200 profile's
    ``codec_preference`` were removed). The serial path derives 2 channels from
    that codec, but the driver clamps the open to the mono device and RX PCM
    still reaches the radio callback. The profile entry stays in place as
    belt-and-suspenders; this test guards that the clamp is the load-bearing
    fix.
    """
    backend = _StrictChannelBackend(input_channels=1, output_channels=1)
    audio_driver = UsbAudioDriver(serial_port=None, backend=backend)
    radio = Ic705SerialRadio(
        device="/dev/tty.usbmodem-X6200",
        model="X6200",
        civ_link=_FakeSerialCivLink(),
        audio_driver=audio_driver,
    )
    # Emulate the X6200 profile with its mono ``codec_preference`` REMOVED: the
    # resolved codec falls back to the global stereo default. (The profile entry
    # stays in the tree as belt-and-suspenders; we override only the resolved
    # codec here to prove the clamp — not the profile entry — is load-bearing.)
    radio._audio_codec = AudioCodec.PCM_2CH_16BIT
    assert radio._serial_audio_channels_for_codec() == 2

    await radio.connect()
    packets: list[bytes] = []
    try:
        await radio.start_audio_rx_opus(lambda pkt: packets.append(pkt.data))
        assert len(backend.rx_streams) == 1, "RX capture did not open"
        assert backend.rx_open_channels == [1], "channels were not clamped to mono"
        pcm_frame = b"\xab\xcd" * 480
        backend.rx_streams[0].inject_frame(pcm_frame)
        await asyncio.sleep(0.01)
        # PCM codec → no Opus transcode → raw mono PCM forwarded verbatim.
        assert packets == [pcm_frame]
    finally:
        await radio.stop_audio_rx_opus()
        await radio.disconnect()
