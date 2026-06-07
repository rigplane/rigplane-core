"""Regression tests for MOR-241 — X6200 AUDIO SCOPE panel is blank.

Root cause: ``AudioFftScope.on_frame()`` is a single-slot callback setter.
``WebServer.__init__`` registered ``_broadcast_audio_scope`` (the
``/api/v1/audio-scope`` relay) and then, for non-hardware-scope radios,
*overwrote* it with ``_broadcast_scope`` (the ``/api/v1/scope`` relay). The
second registration silently clobbered the first, so the audio-scope path
received zero FFT frames and the AUDIO SCOPE panel rendered blank. IC-7610
(``CAP_SCOPE`` present) skipped the second registration and was unaffected.

The fix registers a single dispatch method that fans out to BOTH broadcasters
for non-hardware-scope radios, while a hardware-scope radio drives ONLY the
audio-scope path (so the IC-7610 main spectrum keeps coming from the real
hardware scope, byte-identical).

These tests are hardware-free: they reuse the MOR-236 mono-USB X6200 pattern
(a real ``Ic705SerialRadio`` resolved against the X6200 profile) and a
hardware-scope fake radio, feeding synthetic PCM16 through the broadcaster's
PCM tap exactly as the live audio pipeline does.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from rigplane.audio.backend import (
    AudioDeviceId,
    AudioDeviceInfo,
    FakeRxStream,
    FakeTxStream,
)
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.backends.ic705.serial import Ic705SerialRadio
from rigplane.capabilities import CAP_AUDIO, CAP_SCOPE
from rigplane.scope import ScopeFrame
from rigplane.web.server import WebConfig, WebServer


# ── Fakes ────────────────────────────────────────────────────────────────────


class _MonoUsbAudioBackend:
    """Fake mono USB CODEC backend (Xiegu X6200 shape), as used by MOR-236."""

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
        await asyncio.sleep(0.0 if timeout is None else min(timeout, 0.0))
        return None


class _HardwareScopeRadio:
    """Minimal fake radio that advertises BOTH audio and hardware scope.

    Used to prove the IC-7610-class non-regression: a hardware-scope radio
    must still receive audio-scope frames but must NOT have the audio FFT
    drive ``/api/v1/scope`` (its main spectrum comes from the real scope).
    """

    def __init__(self) -> None:
        from rigplane.radio_state import RadioState

        self.capabilities = frozenset({CAP_AUDIO, CAP_SCOPE})
        self.radio_state = RadioState()
        self.audio_codec = None
        self.audio_sample_rate = 48_000


class _FakeScopeHandler:
    """Records frames enqueued by the server's broadcast methods."""

    def __init__(self) -> None:
        self.frames: list[ScopeFrame] = []

    def enqueue_frame(self, frame: ScopeFrame) -> None:
        self.frames.append(frame)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_pcm_noise(num_samples: int, amplitude: int = 5000) -> bytes:
    """Generate PCM16 mono white noise frames (radio RX shape)."""
    rng = np.random.default_rng(241)
    samples = (rng.uniform(-1, 1, num_samples) * amplitude).astype(np.int16)
    return samples.tobytes()


def _feed_pcm_through_tap(server: WebServer, total_samples: int) -> None:
    """Push synthetic PCM16 through the broadcaster's PCM tap.

    The FFT scope is rate-limited to ``fps``; reset its last-frame clock so
    the first window emits immediately, then drive enough samples that at
    least one FFT window is processed.
    """
    scope = server._audio_fft_scope
    assert scope is not None
    scope._last_frame_time = 0.0  # bypass the fps rate-limit for the first frame
    # The broadcaster fans PCM out to all registered taps; the FFT scope tap
    # is the one under test. Feed in chunks to mimic 20 ms RX frames.
    chunk = 960  # 20 ms @ 48 kHz mono
    fed = 0
    while fed < total_samples:
        pcm = _make_pcm_noise(min(chunk, total_samples - fed))
        server._audio_broadcaster._tap_registry.feed(pcm)
        fed += chunk


# ── X6200 (no hardware scope) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_x6200_audio_scope_receives_fft_frames() -> None:
    """Non-hardware-scope X6200 must relay FFT frames to /api/v1/audio-scope.

    THIS IS THE MOR-241 REGRESSION GUARD. On the unfixed code the second
    ``on_frame`` registration clobbers ``_broadcast_audio_scope`` and this
    assertion fails (zero audio-scope frames). After the dispatch fix it
    passes.
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
    try:
        # Sanity: X6200 has audio FFT but no hardware scope.
        assert CAP_AUDIO in radio.capabilities
        assert CAP_SCOPE not in radio.capabilities

        server = WebServer(radio=radio, config=WebConfig())
        assert server._audio_fft_scope is not None, "audio FFT scope not wired"

        # center_freq > 0 gate inside feed_audio must pass.
        radio.radio_state.main.freq = 14_074_000
        server._audio_fft_scope.set_center_freq(14_074_000)

        audio_handler = _FakeScopeHandler()
        main_handler = _FakeScopeHandler()
        server._audio_scope_handlers.add(audio_handler)
        server._scope_handlers.add(main_handler)

        # Feed several FFT windows worth of PCM through the broadcaster tap.
        _feed_pcm_through_tap(server, total_samples=2048 * 4)

        assert len(audio_handler.frames) >= 1, (
            "audio-scope handler received zero FFT frames "
            "(MOR-241: dispatch clobbered the audio-scope callback)"
        )
        assert isinstance(audio_handler.frames[0], ScopeFrame)
        # Non-hardware-scope radios also feed the MAIN spectrum (X6200 relies
        # on /api/v1/scope for its panadapter) — must NOT be dropped.
        assert len(main_handler.frames) >= 1, (
            "non-hw-scope radio stopped feeding /api/v1/scope (regression)"
        )
    finally:
        await radio.disconnect()


# ── Hardware-scope radio (IC-7610 class) non-regression ──────────────────────


def test_hardware_scope_radio_audio_fft_only_on_audio_scope() -> None:
    """Hardware-scope radio: audio FFT drives ONLY /api/v1/audio-scope.

    IC-7610 must stay byte-identical: the audio FFT must reach the
    audio-scope path but must NEVER be pushed to /api/v1/scope (whose
    frames come from the real hardware scope).
    """
    radio = _HardwareScopeRadio()
    server = WebServer(radio=radio, config=WebConfig())
    assert server._audio_fft_scope is not None

    audio_handler = _FakeScopeHandler()
    main_handler = _FakeScopeHandler()
    server._audio_scope_handlers.add(audio_handler)
    server._scope_handlers.add(main_handler)

    # Drive the dispatch entry point directly with a synthetic frame.
    frame = ScopeFrame(
        receiver=0,
        mode=0,
        start_freq_hz=14_000_000,
        end_freq_hz=14_100_000,
        pixels=bytes(160),
        out_of_range=False,
    )
    server._dispatch_audio_fft_frame(frame)

    assert audio_handler.frames == [frame], "audio-scope must receive the FFT frame"
    assert main_handler.frames == [], (
        "hardware-scope radio must NOT route audio FFT to /api/v1/scope"
    )
