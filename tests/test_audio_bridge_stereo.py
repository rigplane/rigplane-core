"""Regression tests for issue #1381 — stereo radio codec → bridge downmix.

Verifies that AudioBridge, when the radio negotiates a stereo codec
(PCM_2CH_16BIT / OPUS_2CH), correctly downmixes L+R → mono before
writing to the loopback OutputStream, instead of feeding interleaved
stereo bytes to a mono PortAudio stream (which halves the effective
sample-rate and compresses the spectrum 2x).
"""

from __future__ import annotations

import asyncio
import logging
import types
from unittest.mock import AsyncMock

import numpy as np
import pytest

from icom_lan.audio.backend import (
    AudioDeviceId,
    AudioDeviceInfo,
    FakeAudioBackend,
)
from icom_lan.audio.lan_stream import AudioPacket
from icom_lan.audio_bridge import (
    AudioBridge,
    SAMPLE_RATE,
    SAMPLES_PER_FRAME,
)
from icom_lan.types import AudioCodec


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


_BH_DEVICE = AudioDeviceInfo(
    id=AudioDeviceId(1),
    name="BlackHole 2ch",
    input_channels=2,
    output_channels=2,
)


def _bridge_backend() -> FakeAudioBackend:
    return FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(0),
                name="Built-in Output",
                output_channels=2,
            ),
            _BH_DEVICE,
        ]
    )


def _make_radio(codec: AudioCodec | None) -> types.SimpleNamespace:
    from icom_lan.audio_bus import AudioBus

    radio: types.SimpleNamespace = types.SimpleNamespace(
        audio_codec=codec,
        start_audio_rx_opus=AsyncMock(),
        stop_audio_rx_opus=AsyncMock(),
        start_audio_tx_pcm=AsyncMock(),
        stop_audio_tx_pcm=AsyncMock(),
        push_audio_tx_pcm=AsyncMock(),
        push_audio_tx_opus=AsyncMock(),
    )
    bus = AudioBus(radio)
    radio.audio_bus = bus
    return radio


def _sine_mono_int16(
    freq_hz: float,
    duration_s: float,
    sample_rate: int = SAMPLE_RATE,
    amplitude_dbfs: float = -6.0,
) -> np.ndarray:
    """Generate a mono int16 sine wave."""
    n = int(sample_rate * duration_s)
    t = np.arange(n) / sample_rate
    amp = (10.0 ** (amplitude_dbfs / 20.0)) * 32767.0
    return (amp * np.sin(2 * np.pi * freq_hz * t)).astype(np.int16)


def _interleave_stereo(left: np.ndarray, right: np.ndarray) -> bytes:
    """Build interleaved stereo s16le bytes from two mono int16 arrays."""
    return np.column_stack([left, right]).astype(np.int16).tobytes()


def _peak_freq_hz(pcm_mono_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> float:
    """Return the dominant frequency (Hz) of a mono s16le PCM stream via FFT."""
    samples = np.frombuffer(pcm_mono_bytes, dtype=np.int16).astype(np.float64)
    if samples.size < 2:
        return 0.0
    # Window to reduce leakage at packet boundaries.
    window = np.hanning(samples.size)
    spectrum = np.fft.rfft(samples * window)
    freqs = np.fft.rfftfreq(samples.size, d=1.0 / sample_rate)
    idx = int(np.argmax(np.abs(spectrum)))
    return float(freqs[idx])


def _rms_int16(pcm_bytes: bytes) -> float:
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples**2)))


async def _drain_subscription(bridge: AudioBridge) -> None:
    """Yield long enough for queued packets to flow through the rx_loop."""
    for _ in range(10):
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Test 1 — codec → input_channels resolution (parametrised)
# ---------------------------------------------------------------------------


def _opus_available() -> bool:
    try:
        import opuslib  # noqa: F401
    except Exception:
        return False
    return True


@pytest.mark.parametrize(
    "codec, expected_channels",
    [
        (AudioCodec.ULAW_1CH, 1),
        (AudioCodec.PCM_1CH_8BIT, 1),
        (AudioCodec.PCM_1CH_16BIT, 1),
        (AudioCodec.PCM_2CH_8BIT, 2),
        (AudioCodec.PCM_2CH_16BIT, 2),
        (AudioCodec.ULAW_2CH, 2),
        pytest.param(
            AudioCodec.OPUS_1CH,
            1,
            marks=pytest.mark.skipif(
                not _opus_available(), reason="libopus not installed"
            ),
        ),
        pytest.param(
            AudioCodec.OPUS_2CH,
            2,
            marks=pytest.mark.skipif(
                not _opus_available(), reason="libopus not installed"
            ),
        ),
        (None, 1),  # codec not yet negotiated → safe default
    ],
)
async def test_bridge_resolves_input_channels_from_radio_codec(
    codec: AudioCodec | None, expected_channels: int
) -> None:
    radio = _make_radio(codec)
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    # Default before start
    assert bridge._input_channels == 1

    await bridge.start()
    try:
        assert bridge._input_channels == expected_channels
    finally:
        await bridge.stop()


# ---------------------------------------------------------------------------
# Test 2 — stereo input downmixed preserves frequency (DSP golden)
# ---------------------------------------------------------------------------


async def test_bridge_stereo_input_downmixed_keeps_frequency() -> None:
    """1 kHz sine in stereo PCM_2CH_16BIT must remain 1 kHz after downmix.

    Without the fix, interleaved stereo bytes are written to a mono
    OutputStream — the radio's L/R samples are interpreted as consecutive
    mono samples, halving the effective sample rate and shifting the
    spectrum to ~500 Hz.
    """
    radio = _make_radio(AudioCodec.PCM_2CH_16BIT)
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    await bridge.start()
    try:
        assert bridge._input_channels == 2

        # 200 ms 1 kHz sine, L = R = same waveform.
        sine = _sine_mono_int16(freq_hz=1000.0, duration_s=0.2)
        stereo_bytes = _interleave_stereo(sine, sine)

        # Slice into 20 ms stereo frames: SAMPLES_PER_FRAME * 2ch * 2B = 3840 B
        stereo_frame_bytes = SAMPLES_PER_FRAME * 2 * 2
        for offset in range(0, len(stereo_bytes), stereo_frame_bytes):
            chunk = stereo_bytes[offset : offset + stereo_frame_bytes]
            if len(chunk) < stereo_frame_bytes:
                break
            radio.audio_bus._on_opus_packet(
                AudioPacket(ident=0x80, send_seq=offset, data=chunk)
            )

        await _drain_subscription(bridge)

        written = b"".join(backend.tx_streams[0].written_frames)
        # Each mono output frame = 1920 B; we sent 10 stereo frames → 10 mono frames.
        assert len(written) >= SAMPLES_PER_FRAME * 2 * 9, (
            f"too few output bytes: {len(written)}"
        )

        peak = _peak_freq_hz(written)
        # FFT bin width ~5 Hz at this length; allow ±50 Hz for hanning leakage.
        assert 950.0 <= peak <= 1050.0, (
            f"peak {peak:.1f} Hz — expected ~1000 Hz, "
            f"500 Hz indicates missing downmix (issue #1381)"
        )
    finally:
        await bridge.stop()


# ---------------------------------------------------------------------------
# Test 3 — mono passthrough is byte-for-byte unchanged (control)
# ---------------------------------------------------------------------------


async def test_bridge_mono_input_passthrough_unchanged() -> None:
    """Mono codec path must not touch payload bytes — no downmix applied."""
    radio = _make_radio(AudioCodec.PCM_1CH_16BIT)
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    await bridge.start()
    try:
        assert bridge._input_channels == 1

        sine = _sine_mono_int16(freq_hz=1000.0, duration_s=0.04)
        # Two 20 ms mono frames.
        mono_frame_bytes = SAMPLES_PER_FRAME * 2
        payload = sine.tobytes()
        for offset in range(0, len(payload), mono_frame_bytes):
            chunk = payload[offset : offset + mono_frame_bytes]
            if len(chunk) < mono_frame_bytes:
                break
            radio.audio_bus._on_opus_packet(
                AudioPacket(ident=0x80, send_seq=offset, data=chunk)
            )

        await _drain_subscription(bridge)

        written = b"".join(backend.tx_streams[0].written_frames)
        # Bytes must match the injected mono payload exactly.
        assert written == payload[: len(written)]
        assert len(written) == 2 * mono_frame_bytes
    finally:
        await bridge.stop()


# ---------------------------------------------------------------------------
# Test 4 — L=sine, R=silence preserves L (sanity check on averaging)
# ---------------------------------------------------------------------------


async def test_bridge_stereo_input_silent_R_preserves_L() -> None:
    """L=1 kHz sine, R=silence → mono = L/2. Frequency preserved, level −6 dB."""
    radio = _make_radio(AudioCodec.PCM_2CH_16BIT)
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    await bridge.start()
    try:
        assert bridge._input_channels == 2

        sine = _sine_mono_int16(freq_hz=1000.0, duration_s=0.2)
        silence = np.zeros_like(sine)
        stereo_bytes = _interleave_stereo(sine, silence)

        stereo_frame_bytes = SAMPLES_PER_FRAME * 2 * 2
        for offset in range(0, len(stereo_bytes), stereo_frame_bytes):
            chunk = stereo_bytes[offset : offset + stereo_frame_bytes]
            if len(chunk) < stereo_frame_bytes:
                break
            radio.audio_bus._on_opus_packet(
                AudioPacket(ident=0x80, send_seq=offset, data=chunk)
            )

        await _drain_subscription(bridge)

        written = b"".join(backend.tx_streams[0].written_frames)
        assert len(written) >= SAMPLES_PER_FRAME * 2 * 9

        # Frequency should still be ~1000 Hz.
        peak = _peak_freq_hz(written)
        assert 950.0 <= peak <= 1050.0, f"peak {peak:.1f} Hz — frequency lost"

        # Amplitude should be ~half of L-only RMS (−6 dB) since R=0.
        ref_rms = _rms_int16(sine.tobytes())
        actual_rms = _rms_int16(written)
        # Allow 10% tolerance — half-mix loses LSB precision via integer division.
        assert 0.40 * ref_rms <= actual_rms <= 0.60 * ref_rms, (
            f"RMS {actual_rms:.0f} not within 0.5×{ref_rms:.0f} ±10%"
        )
    finally:
        await bridge.stop()


# ---------------------------------------------------------------------------
# Test 5 — observability: codec resolution logged at start
# ---------------------------------------------------------------------------


async def test_bridge_logs_codec_resolution_at_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    radio = _make_radio(AudioCodec.PCM_2CH_16BIT)
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    with caplog.at_level(logging.INFO, logger="icom_lan.audio.bridge"):
        await bridge.start()
    try:
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "bridge codec=PCM_2CH_16BIT" in joined, joined
        assert "input_channels=2" in joined, joined
        assert "output_channels=1" in joined, joined
        assert f"sample_rate={SAMPLE_RATE}" in joined, joined
    finally:
        await bridge.stop()
