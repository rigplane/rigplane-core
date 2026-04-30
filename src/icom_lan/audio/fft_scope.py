"""Audio FFT Scope — derive IF panadapter from RX audio PCM stream.

Performs real-time FFT on audio PCM data to generate :class:`ScopeFrame`
objects compatible with the existing spectrum/waterfall display pipeline.

Typical bandwidth is ±24 kHz (48 kHz sample rate) centered on the current
VFO frequency, showing signals within the receiver passband.

Usage::

    scope = AudioFftScope(fft_size=2048, fps=20)
    scope.set_center_freq(14_074_000)
    scope.on_frame(my_callback)

    # In audio RX callback:
    scope.feed_audio(pcm_bytes)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from icom_lan.scope import ScopeFrame

__all__ = ["AudioFftScope"]

_log = logging.getLogger(__name__)

# Scope mode: center (matches SpectrumPanel expected mode)
_SCOPE_MODE_CENTER = 0

# Amplitude mapping: FFT dB range → 0-160 pixel range (ScopeFrame convention)
# Range tuned for typical radio RX audio levels.
# 55 dB span ≈ 2.9 px/dB — good contrast between noise floor and signals.
_PIXEL_MAX = 160
_DB_FLOOR = -70.0  # noise floor dB
_DB_CEIL = -15.0  # clipping level dB


def _import_numpy() -> Any:
    """Lazy-import numpy to avoid hard dependency at module level."""
    from icom_lan._optional_deps import _require_numpy

    _require_numpy()
    import numpy as np

    return np


class AudioFftScope:
    """Real-time FFT scope derived from audio PCM stream.

    Consumes 16-bit signed mono PCM audio frames and produces
    :class:`ScopeFrame` objects at a configurable frame rate.

    Args:
        fft_size: FFT window size in samples. Power of 2 recommended.
            Larger = better frequency resolution, more latency.
            1024 → ~47 Hz/bin, 2048 → ~23 Hz/bin, 4096 → ~12 Hz/bin.
        fps: Target scope frames per second (default 20).
        window: Window function name ('hann', 'blackman', 'hamming').
        avg_count: Number of FFT frames to average for smoothing (1 = no averaging).
        sample_rate: Audio sample rate in Hz (default 48000).
    """

    def __init__(
        self,
        fft_size: int = 2048,
        fps: int = 20,
        window: str = "hann",
        avg_count: int = 4,
        sample_rate: int = 48000,
    ) -> None:
        np = _import_numpy()
        self._np = np

        self._fft_size = fft_size
        self._fps = max(1, fps)
        self._avg_count = max(1, avg_count)
        self._sample_rate = sample_rate
        self._center_freq: int = 0
        self._crop_max_hz: int | None = None
        self._callback: Callable[[ScopeFrame], None] | None = None

        # Pre-compute window function
        self._window = self._make_window(window, fft_size)

        # Audio sample accumulation buffer (float32)
        self._buf = np.zeros(0, dtype=np.float32)

        # Rolling average buffer
        self._avg_buf: list[object] = []  # list of numpy arrays

        # Frame rate limiting
        self._min_interval = 1.0 / self._fps
        self._last_frame_time: float = 0.0

        # Sequence counter
        self._seq: int = 0

        _log.info(
            "AudioFftScope: fft_size=%d fps=%d window=%s avg=%d sr=%d",
            fft_size,
            fps,
            window,
            avg_count,
            sample_rate,
        )

    def _make_window(self, name: str, size: int) -> Any:
        """Create a numpy window function array."""
        np = self._np
        windows = {
            "hann": np.hanning,
            "blackman": np.blackman,
            "hamming": np.hamming,
        }
        fn = windows.get(name)
        if fn is None:
            _log.warning("Unknown window '%s', using hann", name)
            fn = np.hanning
        return fn(size).astype(np.float32)

    def set_center_freq(self, freq_hz: int) -> None:
        """Update the VFO center frequency for RF mapping.

        Args:
            freq_hz: Center frequency in Hz.
        """
        self._center_freq = freq_hz

    def set_mode_bandwidth(self, max_hz: int | None) -> None:
        """Set the maximum bandwidth for cropping the FFT output.

        When set, the scope will only emit bins within ±max_hz/2 of the
        center frequency. Pass None or 0 for full 48 kHz spectrum.

        Args:
            max_hz: Maximum bandwidth in Hz, or None/0 for no crop.
        """
        new_val = max_hz if max_hz else None
        if new_val != self._crop_max_hz:
            self._crop_max_hz = new_val
            self._avg_buf.clear()
            self._last_frame_time = 0.0  # emit next frame immediately
            _log.info("AudioFftScope: mode bandwidth set to %s Hz", new_val)

    def set_sample_rate(self, rate: int) -> None:
        """Update the audio sample rate.

        Args:
            rate: Sample rate in Hz (e.g. 48000).
        """
        if rate != self._sample_rate:
            self._sample_rate = rate
            self._window = self._make_window("hann", self._fft_size)
            self._buf = self._np.zeros(0, dtype=self._np.float32)
            self._avg_buf.clear()
            _log.info("AudioFftScope: sample rate changed to %d", rate)

    def on_frame(self, callback: Callable[[ScopeFrame], None] | None) -> None:
        """Register or unregister scope frame callback.

        Args:
            callback: Function receiving :class:`ScopeFrame`, or None to unregister.
        """
        self._callback = callback

    def feed_audio(self, pcm_data: bytes) -> None:
        """Feed raw PCM16 mono audio data.

        Called from audio RX callback. Non-blocking — FFT is computed
        inline since numpy FFT on 2048 samples takes <0.1ms.

        Args:
            pcm_data: Raw 16-bit signed little-endian mono PCM bytes.
        """
        if self._callback is None or self._center_freq <= 0:
            return

        np = self._np

        # Convert PCM16 bytes to float32 [-1.0, 1.0]
        samples = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0

        # Append to accumulation buffer
        self._buf = np.concatenate([self._buf, samples])

        # Process as many complete FFT windows as available
        while len(self._buf) >= self._fft_size:
            now = time.monotonic()
            if now - self._last_frame_time < self._min_interval:
                # Frame rate limit: skip this window
                self._buf = self._buf[self._fft_size :]
                continue

            chunk = self._buf[: self._fft_size]
            self._buf = self._buf[self._fft_size :]
            self._process_chunk(chunk, now)

    def _process_chunk(self, chunk: Any, now: float) -> None:
        """Perform FFT on one window and emit a ScopeFrame."""
        np = self._np
        callback = self._callback
        if callback is None:
            return

        # Apply window function
        windowed = chunk * self._window

        # Real FFT → positive frequencies only
        spectrum = np.fft.rfft(windowed)
        magnitudes = np.abs(spectrum)

        # Avoid log(0)
        magnitudes = np.maximum(magnitudes, 1e-10)

        # Convert to dB (normalized to FFT size)
        db = 20.0 * np.log10(magnitudes / self._fft_size)

        # Rolling average
        self._avg_buf.append(db)
        if len(self._avg_buf) > self._avg_count:
            self._avg_buf = self._avg_buf[-self._avg_count :]

        if len(self._avg_buf) > 1:
            avg_db = np.mean(np.stack(self._avg_buf), axis=0)
        else:
            avg_db = db

        # Map dB to pixel values (0-160)
        # Linear mapping: _DB_FLOOR → 0, _DB_CEIL → _PIXEL_MAX
        db_range = _DB_CEIL - _DB_FLOOR
        pixels_float = (avg_db - _DB_FLOOR) / db_range * _PIXEL_MAX
        pixels_uint8 = np.clip(pixels_float, 0, _PIXEL_MAX).astype(np.uint8)

        # rfft produces fft_size//2 + 1 bins from DC to Nyquist.
        # Bin 0 = DC (center freq), bin N = Nyquist (+sample_rate/2).
        # We want a symmetric display: -Nyquist ... DC ... +Nyquist
        # Mirror: [N..1] + [0..N] = full symmetric spectrum
        positive = pixels_uint8[1:]  # skip DC
        negative = positive[::-1]  # mirror
        dc = pixels_uint8[0:1]
        symmetric = np.concatenate([negative, dc, positive])

        # RF frequency mapping — optionally cropped to mode bandwidth
        if self._crop_max_hz:
            bin_res = self._sample_rate / self._fft_size
            crop_half_bins = int((self._crop_max_hz / 2) / bin_res)
            center = len(symmetric) // 2
            lo = max(0, center - crop_half_bins)
            hi = min(len(symmetric), center + crop_half_bins + 1)
            symmetric = symmetric[lo:hi]
            actual_half_hz = int(crop_half_bins * bin_res)
            start_freq = self._center_freq - actual_half_hz
            end_freq = self._center_freq + actual_half_hz
        else:
            half_bw = self._sample_rate // 2
            start_freq = self._center_freq - half_bw
            end_freq = self._center_freq + half_bw

        frame = ScopeFrame(
            receiver=0,
            mode=_SCOPE_MODE_CENTER,
            start_freq_hz=start_freq,
            end_freq_hz=end_freq,
            pixels=bytes(symmetric),
            out_of_range=False,
        )

        self._last_frame_time = now
        self._seq += 1

        try:
            callback(frame)
        except Exception:
            _log.exception("AudioFftScope: frame callback error")

    def stop(self) -> None:
        """Stop the scope and clear buffers."""
        self._callback = None
        self._buf = self._np.zeros(0, dtype=self._np.float32)
        self._avg_buf.clear()
        _log.info("AudioFftScope stopped")

    @property
    def fft_size(self) -> int:
        """Current FFT window size."""
        return self._fft_size

    @property
    def fps(self) -> int:
        """Target frames per second."""
        return self._fps

    @property
    def bin_count(self) -> int:
        """Number of pixels in output (symmetric spectrum)."""
        return self._fft_size  # rfft gives fft_size//2+1, symmetric = 2*(N/2) + 1 ≈ N

    @property
    def bandwidth_hz(self) -> int | None:
        """Current mode bandwidth crop in Hz, or None for full spectrum."""
        return self._crop_max_hz

    @property
    def frequency_resolution(self) -> float:
        """Frequency resolution per bin in Hz."""
        return self._sample_rate / self._fft_size
