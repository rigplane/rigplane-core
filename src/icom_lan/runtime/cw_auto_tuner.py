"""CW Auto Tuner — FFT-based CW tone frequency detection.

Collects PCM audio samples, runs an FFT, and detects the dominant
CW tone frequency within the 200–1500 Hz band.

Usage::

    tuner = CwAutoTuner()
    tuner.start_collection(lambda hz: print(f"Detected: {hz} Hz"))

    # In audio callback:
    tuner.feed_audio(pcm_bytes)
"""

from __future__ import annotations

import logging
from typing import Any, Callable

__all__ = ["CwAutoTuner"]

_log = logging.getLogger(__name__)

_SAMPLE_RATE = 48000
_COLLECT_SAMPLES = 24000  # 500 ms at 48 kHz
_FFT_SIZE = 8192
_MIN_HZ = 200
_MAX_HZ = 1500
_NOISE_FLOOR_DB = -50.0


def _import_numpy() -> Any:
    """Lazy-import numpy to avoid hard dependency at module level."""
    from icom_lan._optional_deps import _require_numpy

    _require_numpy()
    import numpy as np

    return np


class CwAutoTuner:
    """Detects CW tone frequency from PCM audio via FFT peak detection.

    Call :meth:`start_collection` with a callback, then feed 16-bit signed
    mono PCM audio via :meth:`feed_audio`.  After 500 ms of audio the
    callback fires with the detected frequency in Hz, or ``None`` if no
    tone was found above the noise floor.
    """

    def __init__(self) -> None:
        self._callback: Callable[[int | None], None] | None = None
        self._buffer: bytes = b""
        self._active: bool = False

    def start_collection(self, callback: Callable[[int | None], None]) -> None:
        """Arm the tuner and begin collecting audio samples."""
        self._callback = callback
        self._buffer = b""
        self._active = True

    def feed_audio(self, pcm: bytes) -> None:
        """Feed s16le mono PCM audio.  Triggers detection when enough data."""
        if not self._active:
            return
        self._buffer += pcm
        # 2 bytes per sample (int16)
        if len(self._buffer) >= _COLLECT_SAMPLES * 2:
            self._detect()

    def cancel(self) -> None:
        """Abort collection without firing the callback."""
        self._active = False
        self._callback = None
        self._buffer = b""

    @property
    def active(self) -> bool:
        """Whether the tuner is currently collecting audio."""
        return self._active

    def _detect(self) -> None:
        """Run FFT peak detection and fire the callback."""
        np = _import_numpy()
        callback = self._callback

        # Disarm before callback (one-shot)
        self._active = False
        self._callback = None

        # Decode s16le → float64
        samples = np.frombuffer(self._buffer, dtype=np.int16).astype(np.float64)
        self._buffer = b""

        # Take exactly _COLLECT_SAMPLES, then window with _FFT_SIZE
        samples = samples[:_COLLECT_SAMPLES]

        # Use the last _FFT_SIZE samples for FFT (zero-pad if short)
        if len(samples) < _FFT_SIZE:
            chunk = np.zeros(_FFT_SIZE, dtype=np.float64)
            chunk[: len(samples)] = samples
        else:
            chunk = samples[-_FFT_SIZE:]

        window = np.hanning(_FFT_SIZE)
        windowed = chunk * window

        # Real FFT (explicit n= guarantees bin-to-Hz mapping)
        spectrum = np.fft.rfft(windowed, n=_FFT_SIZE)
        magnitudes = np.abs(spectrum)

        # Avoid log(0)
        magnitudes = np.maximum(magnitudes, 1e-10)
        db = 20.0 * np.log10(magnitudes / _FFT_SIZE)

        # Bin range for 200–1500 Hz
        bin_min = int(_MIN_HZ * _FFT_SIZE / _SAMPLE_RATE)
        bin_max = int(_MAX_HZ * _FFT_SIZE / _SAMPLE_RATE) + 1

        band_db = db[bin_min:bin_max]
        peak_idx = int(np.argmax(band_db))
        peak_db = band_db[peak_idx]

        if peak_db < _NOISE_FLOOR_DB:
            _log.debug("No CW tone above noise floor (peak %.1f dB)", peak_db)
            if callback:
                callback(None)
            return

        # Absolute bin index
        k = bin_min + peak_idx

        # Parabolic interpolation for sub-bin accuracy
        if 0 < peak_idx < len(band_db) - 1:
            y1 = db[k - 1]
            y2 = db[k]
            y3 = db[k + 1]
            denom = 2.0 * (2.0 * y2 - y1 - y3)
            if abs(denom) > 1e-10:
                offset = (y3 - y1) / denom
                k_refined = k + offset
            else:
                k_refined = float(k)
        else:
            k_refined = float(k)

        freq_hz = k_refined * _SAMPLE_RATE / _FFT_SIZE
        detected = int(round(freq_hz))
        _log.debug("CW tone detected: %d Hz (%.1f dB)", detected, peak_db)

        if callback:
            callback(detected)
