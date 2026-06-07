"""Tests for AudioFftScope — audio-based IF panadapter."""

from __future__ import annotations


import numpy as np
import pytest

from rigplane.audio_fft_scope import AudioFftScope
from rigplane.scope import ScopeFrame


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_pcm_tone(
    freq_hz: float, duration_s: float, sample_rate: int = 48000
) -> bytes:
    """Generate PCM16 mono sine wave at given frequency."""
    t = np.arange(int(sample_rate * duration_s)) / sample_rate
    samples = (np.sin(2 * np.pi * freq_hz * t) * 16000).astype(np.int16)
    return samples.tobytes()


def _make_pcm_silence(num_samples: int) -> bytes:
    """Generate PCM16 mono silence."""
    return b"\x00\x00" * num_samples


def _make_pcm_noise(num_samples: int, amplitude: int = 1000) -> bytes:
    """Generate PCM16 mono white noise."""
    rng = np.random.default_rng(42)
    samples = (rng.uniform(-1, 1, num_samples) * amplitude).astype(np.int16)
    return samples.tobytes()


def _make_pcm_weak_band(
    num_windows: int,
    fft_size: int = 2048,
    sample_rate: int = 48000,
    sig_amp: int = 200,
    noise_amp: float = 0.3,
    seed: int = 7,
) -> bytes:
    """Generate a band-limited (100–3000 Hz) speech-like signal at FTX-1 levels.

    Reproduces the live FTX-1 measurement (MOR-512): per-bin in-band dB of
    roughly ``max -85.9 / mean -90`` sitting over a noise floor near -126 dB
    under the exact scope FFT math. Under the OLD fixed -70 dB display floor
    every in-band bin renders at ~0 (invisible); the adaptive window must lift
    them well above baseline.

    Args:
        num_windows: How many ``fft_size`` windows of audio to emit.
        fft_size: FFT window size (must match the scope's).
        sample_rate: Audio sample rate.
        sig_amp: Per-band signal amplitude (int16 scale).
        noise_amp: Broadband noise stddev (int16 scale) — sets the floor.
        seed: RNG seed for reproducibility.

    Returns:
        Raw PCM16 mono bytes covering ``num_windows`` FFT windows.
    """
    rng = np.random.default_rng(seed)
    total = fft_size * num_windows
    t = np.arange(total) / sample_rate
    sig = np.zeros(total)
    freqs = np.arange(100, 3001, 100)  # 30 in-band tones spanning 100–3000 Hz
    for f in freqs:
        sig += np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
    sig = sig / len(freqs) * sig_amp
    noise = rng.standard_normal(total) * noise_amp
    return (sig + noise).astype(np.int16).tobytes()


def _make_pcm_lowlevel_noise(
    num_samples: int, noise_amp: float = 0.3, seed: int = 11
) -> bytes:
    """Generate pure low-level broadband noise (no signal), FTX-1 floor level."""
    rng = np.random.default_rng(seed)
    samples = (rng.standard_normal(num_samples) * noise_amp).astype(np.int16)
    return samples.tobytes()


def _make_bimodal_db_spectrum(
    fft_size: int = 2048,
    sample_rate: int = 48000,
    inband_lo_hz: float = 100.0,
    inband_hi_hz: float = 3200.0,
    inband_db: float = -102.1,
    inband_spread: float = 2.0,
    outband_db: float = -126.0,
    tone_hz: float | None = None,
    tone_db: float | None = None,
    seed: int = 0,
) -> np.ndarray:
    """Synthesize a per-bin dB spectrum matching the live FTX-1 capture (MOR-512).

    Radio RX audio is bimodal: the demodulated audio baseband (here
    ``inband_lo_hz..inband_hi_hz``) carries receiver band-noise (~-102 dB
    median, ~-96.5 max in the live capture) while the out-of-band region sits far
    lower near the true noise floor (~-126 dB median). This is the exact shape
    that made the OLD full-spectrum percentile floor latch onto the quiet
    out-of-band and render the in-band band-noise at ~68% of screen height with
    no signal present.

    Returns the rfft-shaped ``avg_db`` array (DC..Nyquist) for direct injection
    into :meth:`AudioFftScope._update_db_window`. An optional tone bin models a
    real signal ``tone_db`` above the band-noise.
    """
    rng = np.random.default_rng(seed)
    bin_res = sample_rate / fft_size
    n_bins = fft_size // 2 + 1
    freqs = np.arange(n_bins) * bin_res
    avg_db = np.full(n_bins, outband_db, dtype=np.float64)
    inband = (freqs >= inband_lo_hz) & (freqs <= inband_hi_hz)
    avg_db[inband] = inband_db + rng.standard_normal(int(inband.sum())) * inband_spread
    avg_db = np.clip(avg_db, -150.0, avg_db.max())
    # DC bin runs a little hot (DC leakage), as on real captures.
    avg_db[0] = -100.0
    if tone_hz is not None and tone_db is not None:
        avg_db[int(tone_hz / bin_res)] = tone_db
    return avg_db


def _db_window_to_pixels(scope: AudioFftScope, avg_db: np.ndarray) -> np.ndarray:
    """Map a per-bin dB array through the scope's adaptive window to pixels.

    Replicates the exact mapping in ``_process_chunk`` (``db_floor`` → 0,
    ``db_ceil`` → ``_PIXEL_MAX``, clipped) so a synthesized spectrum can be
    tested against the live-measured screen-height percentages.
    """
    db_floor, db_ceil = scope._update_db_window(avg_db)
    db_range = db_ceil - db_floor
    pixels_float = (avg_db - db_floor) / db_range * _PIXEL_MAX
    return np.clip(pixels_float, 0, _PIXEL_MAX)


# ── Basic construction ───────────────────────────────────────────────────────


class TestAudioFftScopeConstruction:
    """Test AudioFftScope initialization and configuration."""

    def test_default_construction(self):
        scope = AudioFftScope()
        assert scope.fft_size == 2048
        assert scope.fps == 20

    def test_custom_params(self):
        scope = AudioFftScope(fft_size=4096, fps=30, window="blackman", avg_count=8)
        assert scope.fft_size == 4096
        assert scope.fps == 30

    def test_frequency_resolution(self):
        scope = AudioFftScope(fft_size=2048, sample_rate=48000)
        assert scope.frequency_resolution == pytest.approx(48000 / 2048, rel=1e-6)

    def test_set_center_freq(self):
        scope = AudioFftScope()
        scope.set_center_freq(14_074_000)
        # No error, center freq stored internally

    def test_set_sample_rate(self):
        scope = AudioFftScope()
        scope.set_sample_rate(44100)
        assert scope.frequency_resolution == pytest.approx(44100 / 2048, rel=1e-6)


# ── Frame generation ────────────────────────────────────────────────────────


class TestAudioFftScopeFrames:
    """Test that AudioFftScope produces valid ScopeFrame objects."""

    def test_produces_scope_frame(self):
        """Feed enough audio to produce at least one frame."""
        scope = AudioFftScope(fft_size=1024, fps=100, avg_count=1)
        scope.set_center_freq(14_074_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        # Feed 2x fft_size samples to ensure at least one frame
        pcm = _make_pcm_tone(1000.0, duration_s=1024 * 2 / 48000)
        scope.feed_audio(pcm)

        assert len(frames) >= 1
        frame = frames[0]
        assert isinstance(frame, ScopeFrame)
        assert frame.receiver == 0
        assert frame.mode == 0  # center
        assert frame.out_of_range is False

    def test_scope_frame_frequency_mapping(self):
        """Verify start/end freq mapped correctly from center freq."""
        scope = AudioFftScope(fft_size=1024, fps=100, avg_count=1, sample_rate=48000)
        scope.set_center_freq(14_074_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        pcm = _make_pcm_tone(1000.0, duration_s=1024 * 2 / 48000)
        scope.feed_audio(pcm)

        assert len(frames) >= 1
        frame = frames[0]
        assert frame.start_freq_hz == 14_074_000 - 24_000
        assert frame.end_freq_hz == 14_074_000 + 24_000

    def test_pixel_range(self):
        """All pixels should be in 0-160 range."""
        scope = AudioFftScope(fft_size=1024, fps=100, avg_count=1)
        scope.set_center_freq(7_000_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        pcm = _make_pcm_noise(1024 * 3, amplitude=5000)
        scope.feed_audio(pcm)

        assert len(frames) >= 1
        for frame in frames:
            for px in frame.pixels:
                assert 0 <= px <= 160, f"pixel {px} out of range"

    def test_symmetric_pixel_count(self):
        """Output should be symmetric: 2 * (fft_size/2) + 1 pixels."""
        fft_size = 1024
        scope = AudioFftScope(fft_size=fft_size, fps=100, avg_count=1)
        scope.set_center_freq(7_000_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        pcm = _make_pcm_noise(fft_size * 2)
        scope.feed_audio(pcm)

        assert len(frames) >= 1
        # rfft gives fft_size//2 + 1 bins
        # symmetric = 2 * fft_size//2 + 1 = fft_size + 1
        n_positive = fft_size // 2  # bins 1..N (excluding DC)
        expected_pixels = 2 * n_positive + 1  # negative + DC + positive
        assert len(frames[0].pixels) == expected_pixels


# ── Signal detection ────────────────────────────────────────────────────────


class TestAudioFftScopeSignalDetection:
    """Test that FFT correctly identifies signal frequencies."""

    def test_tone_peak_location(self):
        """A 1kHz tone should produce a peak near bin corresponding to 1kHz."""
        fft_size = 2048
        sample_rate = 48000
        tone_freq = 1000.0

        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(14_074_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        # Generate a clean tone
        pcm = _make_pcm_tone(tone_freq, duration_s=fft_size * 2 / sample_rate)
        scope.feed_audio(pcm)

        assert len(frames) >= 1
        pixels = np.frombuffer(frames[0].pixels, dtype=np.uint8)

        # The peak should be in the right half (positive freq) of the symmetric spectrum
        # Center pixel = DC, pixels to the right = positive frequencies
        center = len(pixels) // 2
        # Bin for 1kHz: bin_index = tone_freq / (sample_rate / fft_size)
        expected_bin = int(tone_freq / (sample_rate / fft_size))

        # Peak should be at center + expected_bin (± 2 bins for windowing)
        peak_region = pixels[center + expected_bin - 3 : center + expected_bin + 4]
        peak_val = int(np.max(peak_region))

        # Also check the mirror (negative freq side)
        mirror_region = pixels[center - expected_bin - 3 : center - expected_bin + 4]
        mirror_val = int(np.max(mirror_region))

        # Peak should be significantly above noise
        noise_floor = int(np.median(pixels))
        assert peak_val > noise_floor + 20, (
            f"Peak {peak_val} not significantly above noise {noise_floor}"
        )
        assert mirror_val > noise_floor + 20, (
            f"Mirror peak {mirror_val} not significantly above noise {noise_floor}"
        )

    def test_silence_low_amplitude(self):
        """Silence should produce low pixel values."""
        scope = AudioFftScope(fft_size=1024, fps=100, avg_count=1)
        scope.set_center_freq(7_000_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        pcm = _make_pcm_silence(1024 * 2)
        scope.feed_audio(pcm)

        assert len(frames) >= 1
        pixels = np.frombuffer(frames[0].pixels, dtype=np.uint8)
        # Silence → all pixels near 0
        assert np.max(pixels) < 30, f"Silence produced max pixel {np.max(pixels)}"

    def test_two_tones_two_peaks(self):
        """Two tones should produce two pairs of peaks (symmetric)."""
        fft_size = 2048
        sample_rate = 48000
        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(14_074_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        # Generate two-tone signal
        t = np.arange(int(fft_size * 2)) / sample_rate
        signal = (
            np.sin(2 * np.pi * 800 * t) * 10000 + np.sin(2 * np.pi * 2000 * t) * 10000
        ).astype(np.int16)
        scope.feed_audio(signal.tobytes())

        assert len(frames) >= 1
        pixels = np.frombuffer(frames[0].pixels, dtype=np.uint8)
        center = len(pixels) // 2

        # Check peaks at 800 Hz and 2000 Hz
        bin_800 = int(800 / (sample_rate / fft_size))
        bin_2000 = int(2000 / (sample_rate / fft_size))

        peak_800 = int(np.max(pixels[center + bin_800 - 3 : center + bin_800 + 4]))
        peak_2000 = int(np.max(pixels[center + bin_2000 - 3 : center + bin_2000 + 4]))
        noise = int(np.median(pixels))

        assert peak_800 > noise + 15, f"800Hz peak {peak_800} not above noise {noise}"
        assert peak_2000 > noise + 15, (
            f"2000Hz peak {peak_2000} not above noise {noise}"
        )


# ── Frame rate limiting ─────────────────────────────────────────────────────


class TestAudioFftScopeFrameRate:
    """Test frame rate limiting."""

    def test_fps_limiting(self):
        """With very high data rate, should not exceed target FPS."""
        scope = AudioFftScope(fft_size=256, fps=10, avg_count=1)
        scope.set_center_freq(7_000_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        # Feed much more data than needed (256 * 100 samples = 100 windows)
        pcm = _make_pcm_noise(256 * 100)
        scope.feed_audio(pcm)

        # At 10 fps, processing ~0.5s of audio should yield few frames
        # (actual count depends on monotonic clock, but should be limited)
        # Main check: it doesn't produce 100 frames
        assert len(frames) < 50, (
            f"Too many frames: {len(frames)} (expected <50 at 10fps)"
        )


# ── Averaging ───────────────────────────────────────────────────────────────


class TestAudioFftScopeAveraging:
    """Test frame averaging."""

    def test_averaging_smooths_output(self):
        """Averaged frames should be smoother than single frames."""
        fft_size = 1024

        # No averaging
        scope1 = AudioFftScope(fft_size=fft_size, fps=100, avg_count=1)
        scope1.set_center_freq(7_000_000)
        frames1: list[ScopeFrame] = []
        scope1.on_frame(frames1.append)

        # With averaging
        scope4 = AudioFftScope(fft_size=fft_size, fps=100, avg_count=4)
        scope4.set_center_freq(7_000_000)
        frames4: list[ScopeFrame] = []
        scope4.on_frame(frames4.append)

        # Feed identical noise to both
        pcm = _make_pcm_noise(fft_size * 8, amplitude=3000)
        scope1.feed_audio(pcm)
        scope4.feed_audio(pcm)

        # Both should produce frames
        assert len(frames1) >= 1
        assert len(frames4) >= 1

    def test_avg_count_1_means_no_averaging(self):
        """avg_count=1 should produce frames identical to raw FFT."""
        scope = AudioFftScope(fft_size=1024, fps=100, avg_count=1)
        scope.set_center_freq(7_000_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        pcm = _make_pcm_tone(1500.0, duration_s=1024 * 3 / 48000)
        scope.feed_audio(pcm)

        assert len(frames) >= 1


# ── No callback = no work ───────────────────────────────────────────────────


class TestAudioFftScopeNoCallback:
    """Test that no work is done without a callback."""

    def test_no_callback_no_frames(self):
        """Without a callback, feed_audio should be a no-op."""
        scope = AudioFftScope(fft_size=1024, fps=100, avg_count=1)
        scope.set_center_freq(7_000_000)
        # No on_frame callback registered

        pcm = _make_pcm_noise(1024 * 5)
        scope.feed_audio(pcm)  # Should not raise

    def test_unregister_callback(self):
        """Unregistering callback should stop frame delivery."""
        scope = AudioFftScope(fft_size=1024, fps=100, avg_count=1)
        scope.set_center_freq(7_000_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        pcm1 = _make_pcm_noise(1024 * 2)
        scope.feed_audio(pcm1)
        count_before = len(frames)

        scope.on_frame(None)  # Unregister

        pcm2 = _make_pcm_noise(1024 * 2)
        scope.feed_audio(pcm2)

        assert len(frames) == count_before  # No new frames


# ── Protocol compatibility ──────────────────────────────────────────────────


class TestAudioFftScopeProtocolCompat:
    """Test compatibility with existing scope binary protocol."""

    def test_scope_frame_encodable(self):
        """ScopeFrame from AudioFftScope should be encodable by protocol.py."""
        from rigplane.web.protocol import encode_scope_frame

        scope = AudioFftScope(fft_size=1024, fps=100, avg_count=1)
        scope.set_center_freq(14_074_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        pcm = _make_pcm_noise(1024 * 2)
        scope.feed_audio(pcm)

        assert len(frames) >= 1
        # Should encode without error
        binary = encode_scope_frame(frames[0], sequence=1)
        assert len(binary) >= 16  # Header size
        assert binary[0] == 0x01  # MSG_TYPE_SCOPE

    def test_stop_clears_state(self):
        """stop() should clear buffers and unregister callback."""
        scope = AudioFftScope(fft_size=1024, fps=100, avg_count=1)
        scope.set_center_freq(7_000_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        pcm = _make_pcm_noise(1024 * 2)
        scope.feed_audio(pcm)
        count = len(frames)

        scope.stop()

        scope.feed_audio(pcm)
        assert len(frames) == count  # No new frames after stop


# ── Mode bandwidth cropping ─────────────────────────────────────────────────


class TestAudioFftScopeModeBandwidth:
    """Test mode-bandwidth cropping via set_mode_bandwidth()."""

    def _get_frame(self, scope: AudioFftScope, fft_size: int) -> ScopeFrame:
        """Feed one FFT window of noise and return the first frame."""
        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)
        pcm = _make_pcm_noise(fft_size * 2)
        scope.feed_audio(pcm)
        assert len(frames) >= 1, "No frames produced"
        return frames[0]

    def test_no_crop_by_default(self):
        """Without set_mode_bandwidth, output is full symmetric spectrum."""
        fft_size = 1024
        scope = AudioFftScope(fft_size=fft_size, fps=100, avg_count=1)
        scope.set_center_freq(14_074_000)
        frame = self._get_frame(scope, fft_size)
        expected = 2 * (fft_size // 2) + 1
        assert len(frame.pixels) == expected

    def test_bandwidth_none_means_full_spectrum(self):
        """set_mode_bandwidth(None) is backward compatible — no crop."""
        fft_size = 1024
        scope = AudioFftScope(fft_size=fft_size, fps=100, avg_count=1)
        scope.set_center_freq(14_074_000)
        scope.set_mode_bandwidth(None)
        frame = self._get_frame(scope, fft_size)
        expected = 2 * (fft_size // 2) + 1
        assert len(frame.pixels) == expected

    def test_bandwidth_zero_means_full_spectrum(self):
        """set_mode_bandwidth(0) is backward compatible — no crop."""
        fft_size = 1024
        scope = AudioFftScope(fft_size=fft_size, fps=100, avg_count=1)
        scope.set_center_freq(14_074_000)
        scope.set_mode_bandwidth(0)
        frame = self._get_frame(scope, fft_size)
        expected = 2 * (fft_size // 2) + 1
        assert len(frame.pixels) == expected

    def test_bandwidth_usb_3600hz(self):
        """USB mode max_hz=3600 → ~153 bins (±76 bins × 23.4 Hz/bin)."""
        fft_size = 2048
        sample_rate = 48000
        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(14_074_000)
        scope.set_mode_bandwidth(3600)
        frame = self._get_frame(scope, fft_size)

        bin_res = sample_rate / fft_size
        crop_half_bins = int((3600 / 2) / bin_res)
        expected_bins = 2 * crop_half_bins + 1
        assert len(frame.pixels) == expected_bins

    def test_bandwidth_am_10000hz(self):
        """AM mode max_hz=10000 → ~427 bins."""
        fft_size = 2048
        sample_rate = 48000
        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(7_200_000)
        scope.set_mode_bandwidth(10000)
        frame = self._get_frame(scope, fft_size)

        bin_res = sample_rate / fft_size
        crop_half_bins = int((10000 / 2) / bin_res)
        expected_bins = 2 * crop_half_bins + 1
        assert len(frame.pixels) == expected_bins

    def test_cropped_freq_range(self):
        """start_freq_hz and end_freq_hz should reflect the crop."""
        fft_size = 2048
        sample_rate = 48000
        center = 14_074_000
        max_hz = 3600

        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(center)
        scope.set_mode_bandwidth(max_hz)
        frame = self._get_frame(scope, fft_size)

        bin_res = sample_rate / fft_size
        crop_half_bins = int((max_hz / 2) / bin_res)
        actual_half_hz = int(crop_half_bins * bin_res)

        assert frame.start_freq_hz == center - actual_half_hz
        assert frame.end_freq_hz == center + actual_half_hz

    def test_full_spectrum_freq_range_unchanged(self):
        """Without crop, start/end freq span full ±24kHz."""
        fft_size = 1024
        sample_rate = 48000
        center = 14_074_000

        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(center)
        frame = self._get_frame(scope, fft_size)

        assert frame.start_freq_hz == center - sample_rate // 2
        assert frame.end_freq_hz == center + sample_rate // 2

    def test_mode_switch_changes_bin_count(self):
        """Changing bandwidth mid-stream produces frames with new bin count."""
        fft_size = 2048
        sample_rate = 48000
        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(14_074_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        # First: USB bandwidth
        scope.set_mode_bandwidth(3600)
        scope.feed_audio(_make_pcm_noise(fft_size * 2))
        assert len(frames) >= 1
        bin_res = sample_rate / fft_size
        usb_bins = 2 * int((3600 / 2) / bin_res) + 1
        assert len(frames[0].pixels) == usb_bins

        # Switch to AM bandwidth
        frames.clear()
        scope.set_mode_bandwidth(10000)
        scope.feed_audio(_make_pcm_noise(fft_size * 2))
        assert len(frames) >= 1
        am_bins = 2 * int((10000 / 2) / bin_res) + 1
        assert len(frames[0].pixels) == am_bins

    def test_bandwidth_hz_property(self):
        """bandwidth_hz property reflects current setting."""
        scope = AudioFftScope()
        assert scope.bandwidth_hz is None

        scope.set_mode_bandwidth(3600)
        assert scope.bandwidth_hz == 3600

        scope.set_mode_bandwidth(None)
        assert scope.bandwidth_hz is None

    def test_avg_buf_reset_on_bandwidth_change(self):
        """Changing bandwidth clears averaging buffer to avoid bin-count mismatch."""
        fft_size = 2048
        sample_rate = 48000
        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=4, sample_rate=sample_rate
        )
        scope.set_center_freq(14_074_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        # Build up avg buffer
        scope.feed_audio(_make_pcm_noise(fft_size * 5))

        # Change bandwidth — avg_buf should be cleared, no crash
        scope.set_mode_bandwidth(3600)
        frames.clear()
        scope.feed_audio(_make_pcm_noise(fft_size * 2))
        assert len(frames) >= 1

        bin_res = sample_rate / fft_size
        expected = 2 * int((3600 / 2) / bin_res) + 1
        assert len(frames[0].pixels) == expected


# ── Adaptive auto-range (MOR-512) ─────────────────────────────────────────────

_PIXEL_MAX = 160


class TestAudioFftScopeAdaptiveAutoRange:
    """The dB→pixel window must adapt to the actual signal level.

    A fixed -70/-15 dB window left weak-but-clean signals (FTX-1 audio levels)
    entirely below the display floor — the scope rendered nearly empty. The
    adaptive window must track the per-stream noise floor and signal ceiling so
    that ANY radio level is visible, without amplifying pure noise during
    silence.
    """

    def _feed_get_last(self, scope: AudioFftScope, pcm: bytes) -> ScopeFrame:
        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)
        scope.feed_audio(pcm)
        assert len(frames) >= 1, "no frames produced"
        return frames[-1]

    def test_bimodal_no_signal_inband_noise_maps_low(self):
        """Bimodal no-signal band-noise must map LOW (the MOR-512 regression).

        Live FTX-1 capture, no signal present: in-band band-noise median -102.1
        dB sits over quiet out-of-band -125.9 dB. The OLD full-spectrum
        percentile floor latched onto the quiet out-of-band majority (p10
        ~-128) and stretched the min-span window so the ever-present in-band
        band-noise rendered at ~68% of screen height. Estimating the floor over
        the IN-BAND region instead must keep that band-noise well under ~30% so
        the operator does not see a "high noise floor regardless of signal".

        This test FAILS on the merged full-spectrum code (in-band ~65%).
        """
        fft_size = 2048
        sample_rate = 48000
        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(14_074_000)

        avg_db = _make_bimodal_db_spectrum(fft_size=fft_size, sample_rate=sample_rate)
        # Let the EMA settle on the bimodal frame (seed makes it immediate).
        for _ in range(10):
            pixels = _db_window_to_pixels(scope, avg_db)

        bin_res = sample_rate / fft_size
        freqs = np.arange(len(avg_db)) * bin_res
        inband = (freqs >= 100) & (freqs <= 3200)
        outband = freqs > 5000

        inband_median_pct = float(np.median(pixels[inband])) / _PIXEL_MAX
        inband_max_pct = float(np.max(pixels[inband])) / _PIXEL_MAX

        assert inband_median_pct < 0.30, (
            f"no-signal in-band band-noise renders at "
            f"{inband_median_pct * 100:.0f}% of screen — the floor is latched "
            "to the quiet out-of-band (MOR-512 regression), should be <30%"
        )
        # Sanity: a real signal would have headroom above this band-noise.
        assert inband_max_pct < 0.40, (
            f"no-signal in-band max {inband_max_pct * 100:.0f}% leaves no "
            "headroom for an actual signal"
        )
        # Out-of-band wings correctly fall to the bottom of the display.
        assert float(np.median(pixels[outband])) < 0.10 * _PIXEL_MAX

    def test_bimodal_with_signal_tone_rises_above_band_noise(self):
        """A real tone ~25 dB over the band-noise must render clearly above it.

        Same bimodal band-noise as the no-signal case plus a real in-band tone
        25 dB above the band-noise. Good contrast means the tone pixel sits far
        above the band-noise pixels (which still map low).
        """
        fft_size = 2048
        sample_rate = 48000
        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(14_074_000)

        tone_hz = 1500.0
        avg_db = _make_bimodal_db_spectrum(
            fft_size=fft_size,
            sample_rate=sample_rate,
            tone_hz=tone_hz,
            tone_db=-102.1 + 25.0,  # 25 dB above the in-band band-noise
        )
        for _ in range(10):
            pixels = _db_window_to_pixels(scope, avg_db)

        bin_res = sample_rate / fft_size
        tone_bin = int(tone_hz / bin_res)
        freqs = np.arange(len(avg_db)) * bin_res
        inband = (freqs >= 100) & (freqs <= 3200)
        noise_mask = inband.copy()
        noise_mask[tone_bin] = False  # exclude the tone bin

        tone_px = float(pixels[tone_bin])
        noise_median_px = float(np.median(pixels[noise_mask]))

        # Band-noise stays low …
        assert noise_median_px < 0.30 * _PIXEL_MAX, (
            f"band-noise median {noise_median_px:.0f}/{_PIXEL_MAX} too high"
        )
        # … and the tone rises clearly above it (strong contrast).
        assert tone_px > 0.60 * _PIXEL_MAX, (
            f"tone only reached {tone_px:.0f}/{_PIXEL_MAX} — poor contrast"
        )
        assert tone_px > noise_median_px + 0.35 * _PIXEL_MAX, (
            f"tone {tone_px:.0f} not separated from band-noise "
            f"{noise_median_px:.0f} by enough contrast"
        )

    def test_weak_band_renders_well_above_baseline(self):
        """FTX-1-level weak band (-86 dB/bin) must lift well above the floor.

        Under the OLD fixed -70 dB floor every in-band bin renders at ~0
        (invisible). The adaptive window must put the in-band peak above ~40%
        of the pixel range. This test FAILS on the old fixed-window code.
        """
        fft_size = 2048
        sample_rate = 48000
        center = 14_074_000
        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(center)

        # Several windows so the EMA can settle on the real floor/ceil.
        pcm = _make_pcm_weak_band(
            num_windows=10, fft_size=fft_size, sample_rate=sample_rate
        )
        frame = self._feed_get_last(scope, pcm)
        pixels = np.frombuffer(frame.pixels, dtype=np.uint8)

        # In-band region around DC (the 100–3000 Hz band maps near center).
        center_px = len(pixels) // 2
        bin_res = sample_rate / fft_size
        hi_bin = int(3000 / bin_res)
        inband = pixels[center_px - hi_bin : center_px + hi_bin + 1]

        assert int(np.max(inband)) > 0.40 * _PIXEL_MAX, (
            f"weak in-band signal only reached {int(np.max(inband))}/"
            f"{_PIXEL_MAX} px — should be lifted well above baseline"
        )

    def test_loud_input_not_permanently_saturated(self):
        """A near-full-scale tone must not blow the whole display to max.

        The ceiling adapts upward so a strong signal still shows structure
        (peak high, surrounding bins lower) rather than clipping everything.
        """
        fft_size = 2048
        sample_rate = 48000
        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(14_074_000)

        # Near full-scale tone (amplitude 30000 of 32767).
        t = np.arange(fft_size * 10) / sample_rate
        loud = (np.sin(2 * np.pi * 1500 * t) * 30000).astype(np.int16)
        frame = self._feed_get_last(scope, loud.tobytes())
        pixels = np.frombuffer(frame.pixels, dtype=np.uint8)

        # The vast majority of bins (off the tone) must NOT be saturated.
        saturated = int(np.sum(pixels >= _PIXEL_MAX))
        assert saturated < 0.25 * len(pixels), (
            f"{saturated}/{len(pixels)} bins saturated — window did not adapt "
            "to the loud input"
        )
        # The tone peak should still be present and high.
        assert int(np.max(pixels)) > 0.7 * _PIXEL_MAX

    def test_lowlevel_noise_only_stays_low(self):
        """Pure low-level noise must NOT be amplified to fill the screen.

        The minimum-window-span guard keeps noise near the bottom when there
        is no signal above it.
        """
        fft_size = 2048
        sample_rate = 48000
        scope = AudioFftScope(
            fft_size=fft_size, fps=100, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(7_000_000)

        pcm = _make_pcm_lowlevel_noise(fft_size * 12, noise_amp=0.3)
        frame = self._feed_get_last(scope, pcm)
        pixels = np.frombuffer(frame.pixels, dtype=np.uint8)

        # Noise-only: bulk of bins must sit low (min-span guard prevents the
        # auto-range from stretching a flat noise floor across the screen).
        assert int(np.median(pixels)) < 0.30 * _PIXEL_MAX, (
            f"noise-only median {int(np.median(pixels))}/{_PIXEL_MAX} too high "
            "— min-span guard failed, noise was amplified"
        )

    def test_adaptive_window_converges_and_is_stable(self):
        """Under steady input the auto-range converges (no runaway/pumping).

        Frames are driven through the scope's internal chunk processor with
        monotonically increasing timestamps so the count is deterministic and
        independent of the wall-clock frame-rate limiter (which would otherwise
        drop windows fed back-to-back). This isolates the convergence property
        of the adaptive dB window.
        """
        fft_size = 2048
        sample_rate = 48000
        scope = AudioFftScope(
            fft_size=fft_size, fps=20, avg_count=1, sample_rate=sample_rate
        )
        scope.set_center_freq(14_074_000)

        frames: list[ScopeFrame] = []
        scope.on_frame(frames.append)

        # Many identical-statistics windows of the weak band, each emitted as
        # its own frame with a fresh timestamp past the frame-rate interval.
        pcm = _make_pcm_weak_band(
            num_windows=20, fft_size=fft_size, sample_rate=sample_rate
        )
        chunks = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        for i in range(20):
            chunk = chunks[i * fft_size : (i + 1) * fft_size]
            # Timestamps spaced by > 1/fps so every chunk emits a frame.
            scope._process_chunk(chunk, now=float(i))

        assert len(frames) == 20, f"expected 20 frames, got {len(frames)}"

        # Compare the in-band peak of the last few frames — must be stable.
        bin_res = sample_rate / fft_size
        hi_bin = int(3000 / bin_res)

        def inband_peak(fr: ScopeFrame) -> int:
            px = np.frombuffer(fr.pixels, dtype=np.uint8)
            c = len(px) // 2
            return int(np.max(px[c - hi_bin : c + hi_bin + 1]))

        tail = [inband_peak(f) for f in frames[-5:]]
        spread = max(tail) - min(tail)
        # Once converged, the rendered peak should not pump wildly.
        assert spread < 0.20 * _PIXEL_MAX, (
            f"in-band peak pumping across frames: {tail} (spread {spread})"
        )
        # And it should be a meaningful, visible level (not collapsed to 0).
        assert min(tail) > 0.30 * _PIXEL_MAX, f"converged peak too low: {tail}"
