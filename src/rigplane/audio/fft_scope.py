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

from rigplane.scope import ScopeFrame

__all__ = ["AudioFftScope"]

_log = logging.getLogger(__name__)

# Scope mode: center (matches SpectrumPanel expected mode)
_SCOPE_MODE_CENTER = 0

# Amplitude mapping: FFT dB range → 0-160 pixel range (ScopeFrame convention).
#
# The dB→pixel window is ADAPTIVE (MOR-512): a fixed window was tuned for one
# radio's RX audio level and rendered nearly empty for radios at a different
# level (e.g. FTX-1, whose clean in-band signal sat ~16-20 dB below a fixed
# -70 dB floor). Instead we track the per-stream noise floor and signal ceiling
# and slide the window to follow them, so any radio/level stays visible.
_PIXEL_MAX = 160

# Robust per-frame floor/ceil estimators (percentiles of the dB array).
# A low percentile rejects the few near-silent bins; a high percentile rejects
# single-bin spikes (DC leakage etc.) that would otherwise inflate the ceiling.
_FLOOR_PCT = 10.0
_CEIL_PCT = 99.0

# EMA / attack-decay smoothing of the tracked floor & ceil. The floor adapts
# SLOWLY for a stable baseline; the ceil rises fast (attack) but falls slowly
# (decay) so a vanishing signal does not snap the window down and re-amplify
# noise. Values are per-frame blend factors in (0, 1].
_FLOOR_ALPHA = 0.05
_CEIL_ATTACK = 0.30
_CEIL_DECAY = 0.05

# Margins placed below the tracked floor / above the tracked ceil (dB).
_FLOOR_MARGIN = 5.0
_CEIL_MARGIN = 3.0

# Minimum display-window span (dB). During silence the observed floor and ceil
# collapse together; without a floor on the span the auto-range would stretch a
# flat noise field across the whole screen. Enforcing a wide minimum span keeps
# noise pinned near the bottom until a real signal rises above it.
_MIN_SPAN_DB = 45.0

# Absolute sanity clamps on the window endpoints (dB, on the /fft_size scale).
_ABS_FLOOR_MIN = -160.0
_ABS_FLOOR_MAX = -20.0
_ABS_CEIL_MAX = 0.0


def _import_numpy() -> Any:
    """Lazy-import numpy to avoid hard dependency at module level."""
    from rigplane._optional_deps import _require_numpy

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

        # Adaptive dB→pixel window state (MOR-512). None until seeded from the
        # first processed frame, then EMA-tracked toward the live floor/ceil.
        self._db_floor_ema: float | None = None
        self._db_ceil_ema: float | None = None

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

        # Map dB to pixel values (0-160) through the ADAPTIVE window.
        # Linear mapping: db_floor → 0, db_ceil → _PIXEL_MAX.
        db_floor, db_ceil = self._update_db_window(avg_db)
        db_range = db_ceil - db_floor
        pixels_float = (avg_db - db_floor) / db_range * _PIXEL_MAX
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

    def _update_db_window(self, avg_db: Any) -> tuple[float, float]:
        """Adapt the dB→pixel window to the current frame and return it.

        Tracks a robust noise floor (low percentile) and signal ceiling (high
        percentile) of ``avg_db``, smooths each with an EMA / attack-decay so
        the window neither jitters nor pumps, then applies margins, a minimum
        span (so silence does not amplify noise) and absolute clamps.

        Args:
            avg_db: The (averaged) per-bin dB array for this frame.

        Returns:
            ``(db_floor, db_ceil)`` — the window endpoints to map to pixels.
        """
        np = self._np

        obs_floor = float(np.percentile(avg_db, _FLOOR_PCT))
        obs_ceil = float(np.percentile(avg_db, _CEIL_PCT))

        if self._db_floor_ema is None or self._db_ceil_ema is None:
            # Seed from the first frame so startup is immediately sane.
            self._db_floor_ema = obs_floor
            self._db_ceil_ema = obs_ceil
        else:
            # Floor: slow EMA → stable baseline.
            self._db_floor_ema += (obs_floor - self._db_floor_ema) * _FLOOR_ALPHA
            # Ceil: fast attack up, slow decay down → no pump when signal drops.
            ceil_alpha = _CEIL_ATTACK if obs_ceil > self._db_ceil_ema else _CEIL_DECAY
            self._db_ceil_ema += (obs_ceil - self._db_ceil_ema) * ceil_alpha

        # Apply display margins.
        db_floor = self._db_floor_ema - _FLOOR_MARGIN
        db_ceil = self._db_ceil_ema + _CEIL_MARGIN

        # Absolute sanity clamps.
        db_floor = min(max(db_floor, _ABS_FLOOR_MIN), _ABS_FLOOR_MAX)
        db_ceil = min(db_ceil, _ABS_CEIL_MAX)

        # Minimum span: never let the window collapse onto a flat noise field
        # (silence) — keep noise pinned near the bottom.
        if db_ceil - db_floor < _MIN_SPAN_DB:
            db_ceil = db_floor + _MIN_SPAN_DB

        return db_floor, db_ceil

    def stop(self) -> None:
        """Stop the scope and clear buffers."""
        self._callback = None
        self._buf = self._np.zeros(0, dtype=self._np.float32)
        self._avg_buf.clear()
        self._db_floor_ema = None
        self._db_ceil_ema = None
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
