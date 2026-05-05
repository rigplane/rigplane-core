"""Lightweight audio analyzer — realtime SNR estimation from PCM stream.

Pure Python, no numpy required. Processes s16le mono PCM frames and
estimates RMS level, noise floor (sliding minimum), and SNR.
"""

from __future__ import annotations

import math
import struct
from collections import deque

__all__ = ["AudioAnalyzer"]

_MIN_DB: float = -96.0
_REF: float = 32768.0


class AudioAnalyzer:
    """Realtime audio SNR estimator from PCM stream.

    Processes s16le mono PCM frames and estimates:
    - RMS level (dB)
    - Noise floor (dB) -- sliding minimum of RMS over window
    - SNR (dB) -- rms_db - noise_floor_db
    """

    __slots__ = (
        "_smoothing",
        "_window_frames",
        "_rms_db",
        "_rms_history",
        "_frame_count",
    )

    def __init__(self, window_seconds: float = 3.0, smoothing: float = 0.3) -> None:
        self._smoothing = smoothing
        # Assume ~100 frames/sec (480 samples @ 48kHz = 10ms per frame)
        self._window_frames = max(1, int(window_seconds * 100))
        self._rms_db: float = _MIN_DB
        self._rms_history: deque[float] = deque(maxlen=self._window_frames)
        self._frame_count: int = 0

    def feed_audio(self, pcm: bytes) -> None:
        """Feed s16le mono PCM data. Updates internal estimates."""
        n_bytes = len(pcm)
        # Need at least one complete s16le sample (2 bytes)
        if n_bytes < 2:
            return

        n_samples = n_bytes // 2
        # Unpack s16le samples
        samples = struct.unpack(f"<{n_samples}h", pcm[: n_samples * 2])

        # Compute RMS
        sum_sq = 0.0
        for s in samples:
            sum_sq += s * s
        rms = math.sqrt(sum_sq / n_samples)

        # Convert to dB (relative to full-scale 32768)
        if rms > 0:
            rms_db = 20.0 * math.log10(rms / _REF)
        else:
            rms_db = _MIN_DB

        # Clamp
        rms_db = max(_MIN_DB, rms_db)

        # Exponential moving average
        if self._frame_count == 0:
            self._rms_db = rms_db
        else:
            alpha = self._smoothing
            self._rms_db = alpha * rms_db + (1.0 - alpha) * self._rms_db

        self._rms_history.append(self._rms_db)
        self._frame_count += 1

    @property
    def rms_db(self) -> float:
        """Current smoothed RMS level in dB."""
        return self._rms_db

    @property
    def noise_floor_db(self) -> float:
        """Estimated noise floor in dB (sliding minimum)."""
        if not self._rms_history:
            return _MIN_DB
        return min(self._rms_history)

    @property
    def snr_db(self) -> float:
        """Estimated SNR in dB (rms - noise floor)."""
        return max(0.0, self._rms_db - self.noise_floor_db)

    def to_dict(self) -> dict[str, float]:
        """Return current analysis as dict for API/WS."""
        return {
            "rms_db": round(self.rms_db, 1),
            "noise_floor_db": round(self.noise_floor_db, 1),
            "snr_db": round(self.snr_db, 1),
        }

    def reset(self) -> None:
        """Reset all state (on stream discontinuity)."""
        self._rms_db = _MIN_DB
        self._rms_history.clear()
        self._frame_count = 0
