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
from rigplane.scope.levels import AdaptiveLevelMapper

__all__ = ["AudioFftScope"]

_log = logging.getLogger(__name__)

# Scope mode: center (matches SpectrumPanel expected mode)
_SCOPE_MODE_CENTER = 0

# Amplitude mapping: FFT dB → 0-160 pixel range (ScopeFrame convention) via
# the shared adaptive level mapper (MOR-512 tuning; extracted to
# ``rigplane.scope.levels`` in MOR-3152). The dB→pixel window is ADAPTIVE: a
# fixed window was tuned for one radio's RX audio level and rendered nearly
# empty for radios at a different level (e.g. FTX-1). The mapper instead
# tracks the per-stream noise floor and signal ceiling and slides the window
# to follow them, so any radio/level stays visible.

# In-band (audio baseband) region used for the mapper's floor/ceil estimate.
#
# Lower edge: skip DC / sub-sonic bins (hum, DC leakage) that do not represent
# band-noise. Upper edge: when the mode bandwidth is known the in-band region is
# the displayed passband (``_crop_max_hz / 2`` of audio, since the crop keeps
# ±half on each side of center); when it is unknown (e.g. FTX-1, which reports no
# filter ``max_hz`` and shows the full spectrum) fall back to a fixed audio
# baseband limit that covers a typical voice passband without reaching into the
# quiet out-of-band tail that caused the regression.
_INBAND_LO_HZ = 50.0
_INBAND_FALLBACK_HI_HZ = 3500.0


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

        # Adaptive dB→pixel level mapper (MOR-512 tuning, shared via
        # rigplane.scope.levels since MOR-3152). The audio-specific in-band
        # estimation region is applied via ``_refresh_inband_bins``.
        self._mapper = AdaptiveLevelMapper()
        self._refresh_inband_bins()

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
            self._refresh_inband_bins()
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
            self._refresh_inband_bins()
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

        # Map dB to pixel values (0-160) through the ADAPTIVE level mapper
        # (linear mapping: db_floor → 0, db_ceil → pixel max, clipped).
        pixels_uint8 = np.frombuffer(self._mapper.map(avg_db), dtype=np.uint8)

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

    def _inband_bins(self) -> tuple[int, int]:
        """Return the in-band (audio baseband) bin range ``(lo, hi)``.

        ``avg_db`` is the rfft per-bin dB array, indexed DC..Nyquist; bin ``i``
        sits at ``i * sample_rate / fft_size`` Hz of audio. The in-band region
        is ``_INBAND_LO_HZ <= freq <= upper``. When the mode bandwidth is known
        ``upper`` is the displayed passband (``_crop_max_hz / 2``) capped at
        ``_INBAND_FALLBACK_HI_HZ`` (MOR-528): a wide-crop mode (AM) would
        otherwise re-include the quiet out-of-band tail that MOR-512 excluded.
        When it is unknown the fixed ``_INBAND_FALLBACK_HI_HZ`` is used. The
        mapper falls back to the full array if the range would be degenerate
        (degenerate fft_size / sample_rate or short input).

        Radio RX audio is BIMODAL (MOR-512): the demodulated audio baseband
        (~0-3.2 kHz) always carries receiver band-noise, while the out-of-band
        region (>~4-5 kHz) sits far lower, near the true noise floor.
        Estimating the floor over the FULL spectrum latches it onto the quiet
        out-of-band majority, which drags the floor way down and pushes the
        ever-present in-band band-noise up to ~68% of the screen with NO signal
        present ("high noise floor regardless of signal"). Restricting the
        estimate to this region keeps the band-noise mapped LOW so a real
        signal stands out above it.
        """
        bin_res = self._sample_rate / self._fft_size
        if self._crop_max_hz:
            # Cap the upper edge even when a wider crop is known (MOR-528): a
            # wide-crop mode (IC-7610 AM, max_hz=10000 → 5000 Hz edge) would
            # otherwise re-include the quiet out-of-band tail above ~3.2 kHz that
            # MOR-512 excluded, latching the floor back onto it for NARROW audio.
            upper_hz = min(self._crop_max_hz / 2.0, _INBAND_FALLBACK_HI_HZ)
        else:
            upper_hz = _INBAND_FALLBACK_HI_HZ
        lo_bin = int(_INBAND_LO_HZ / bin_res)
        hi_bin = int(upper_hz / bin_res)
        return lo_bin, hi_bin

    def _refresh_inband_bins(self) -> None:
        """Apply the current in-band bin range to the level mapper."""
        self._mapper.set_inband_bins(*self._inband_bins())

    def _update_db_window(self, avg_db: Any) -> tuple[float, float]:
        """Adapt the dB→pixel window to the current frame and return it.

        Delegates to the shared :class:`~rigplane.scope.levels.AdaptiveLevelMapper`
        (MOR-3152); the tracked floor/ceil state and smoothing now live there.
        Kept as the audio-side seam so existing scope tests can drive the
        window directly with synthesized dB frames.

        Args:
            avg_db: The (averaged) per-bin dB array for this frame.

        Returns:
            ``(db_floor, db_ceil)`` — the window endpoints to map to pixels.
        """
        return self._mapper.window(avg_db)

    def stop(self) -> None:
        """Stop the scope and clear buffers."""
        self._callback = None
        self._buf = self._np.zeros(0, dtype=self._np.float32)
        self._avg_buf.clear()
        self._mapper.reset()
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
