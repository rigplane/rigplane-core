"""Optional DSP pipeline for audio bridge — noise gate, RMS normalization, limiter.

All stages operate on PCM s16le frames (bytes in, bytes out). Designed for
voice-grade ham radio audio at 48kHz mono. All stages are **off by default**
— instantiate only the stages you need and combine them in a :class:`DspPipeline`.

Usage::

    pipeline = DspPipeline([
        NoiseGate(threshold_db=-50),
        RmsNormalizer(target_db=-20),
        Limiter(ceiling_db=-1),
    ])
    out_pcm = pipeline.process(in_pcm)

No new dependencies — uses numpy (already required by ``[bridge]``).
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "DspPipeline",
    "DspStage",
    "Limiter",
    "NoiseGate",
    "RmsNormalizer",
]

_INT16_MAX = 32767
_INT16_MIN = -32768


def _db_to_linear(db: float) -> float:
    """Convert dB to linear amplitude (relative to int16 full scale)."""
    return 10 ** (db / 20.0) * _INT16_MAX


class DspStage(Protocol):
    """Protocol for a single DSP processing stage."""

    def process(self, pcm: bytes) -> bytes: ...


class NoiseGate:
    """Squelch frames below a peak amplitude threshold.

    Frames whose peak sample is below *threshold_db* are replaced with silence.
    This prevents transmitting noise during RX silence.

    Args:
        threshold_db: Gate threshold in dB relative to full scale (default -50dB).
    """

    def __init__(self, threshold_db: float = -50.0) -> None:
        self._threshold = _db_to_linear(threshold_db)

    def process(self, pcm: bytes) -> bytes:
        import numpy as np

        samples = np.frombuffer(pcm, dtype=np.int16)
        if np.max(np.abs(samples)) < self._threshold:
            return b"\x00" * len(pcm)
        return pcm


class RmsNormalizer:
    """Normalize audio RMS level to a target with attack/release smoothing.

    Uses a single-pole envelope follower for smooth gain changes, avoiding
    pumping artifacts common in ham radio audio.

    Args:
        target_db: Target RMS level in dB relative to full scale (default -20dB).
        attack_ms: Attack time in ms — how quickly the envelope reacts to louder
            audio (gain decreases). Short = fast compression (default 5ms).
        release_ms: Release time in ms — how quickly the envelope recovers after
            a loud transient (gain increases). Long = smooth recovery (default 50ms).
        max_gain_db: Maximum gain to prevent amplifying noise (default 30dB).
    """

    def __init__(
        self,
        target_db: float = -20.0,
        attack_ms: float = 5.0,
        release_ms: float = 50.0,
        max_gain_db: float = 30.0,
        sample_rate: int = 48_000,
    ) -> None:
        self._target_rms = _db_to_linear(target_db) / (2**0.5)  # peak→rms
        self._max_gain = 10 ** (max_gain_db / 20.0)
        # Single-pole coefficients (per-frame, not per-sample, since we
        # process whole 20ms frames at once)
        frame_s = 0.020  # 20ms frames assumed
        self._attack_coeff = 1.0 - _exp_decay(attack_ms / 1000.0, frame_s)
        self._release_coeff = 1.0 - _exp_decay(release_ms / 1000.0, frame_s)
        self._envelope: float = 0.0

    def process(self, pcm: bytes) -> bytes:
        import numpy as np

        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        rms = float(np.sqrt(np.mean(samples**2)))

        if rms < 1.0:
            # Near-silence — don't adjust gain
            return pcm

        # Envelope follower
        if rms > self._envelope:
            self._envelope += self._attack_coeff * (rms - self._envelope)
        else:
            self._envelope += self._release_coeff * (rms - self._envelope)

        if self._envelope < 1.0:
            return pcm

        gain = self._target_rms / self._envelope
        gain = min(gain, self._max_gain)

        result = samples * gain
        return np.clip(result, _INT16_MIN, _INT16_MAX).astype(np.int16).tobytes()  # type: ignore[no-any-return]


class Limiter:
    """Hard limiter — clamps peaks above ceiling to prevent clipping.

    Applied after normalization to catch any transient overshoot.

    Args:
        ceiling_db: Maximum output level in dB relative to full scale (default -1dB).
    """

    def __init__(self, ceiling_db: float = -1.0) -> None:
        self._ceiling = _db_to_linear(ceiling_db)

    def process(self, pcm: bytes) -> bytes:
        import numpy as np

        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        peak: float = np.max(np.abs(samples))

        if peak <= self._ceiling:
            return pcm

        gain = self._ceiling / peak
        result = samples * gain
        return result.astype(np.int16).tobytes()  # type: ignore[no-any-return]


class DspPipeline:
    """Chain of DSP stages applied in order to PCM frames.

    Args:
        stages: Ordered list of :class:`DspStage` instances.
    """

    def __init__(self, stages: list[DspStage] | None = None) -> None:
        self._stages: list[DspStage] = list(stages or [])

    @property
    def empty(self) -> bool:
        """True when no stages are configured."""
        return len(self._stages) == 0

    def process(self, pcm: bytes) -> bytes:
        """Process a PCM frame through all stages in order."""
        for stage in self._stages:
            pcm = stage.process(pcm)
        return pcm


def _exp_decay(time_constant: float, frame_duration: float) -> float:
    """Compute exponential decay coefficient."""
    import math

    if time_constant <= 0:
        return 0.0
    return math.exp(-frame_duration / time_constant)
