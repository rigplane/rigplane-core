"""Inter-node resample utility for the DSP pipeline.

Resamples float32 audio arrays between nodes that require different sample
rates.  Uses ``scipy.signal.resample_poly`` when available, falling back to
linear interpolation (numpy only).

This module is distinct from ``icom_lan.audio.resample`` which operates on
s16le bytes for the audio bridge.
"""

from __future__ import annotations

from math import gcd
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

__all__ = ["resample_if_needed"]


def _import_numpy() -> Any:
    from .._optional_deps import _require_numpy

    _require_numpy()
    import numpy as _np  # noqa: TID251

    return _np


def resample_if_needed(
    samples: np.ndarray,
    from_rate: int,
    to_rate: int,
) -> tuple[np.ndarray, int]:
    """Resample float32 samples if rates differ.

    Args:
        samples: Float32 numpy array (mono).
        from_rate: Source sample rate in Hz.
        to_rate: Target sample rate in Hz.

    Returns:
        ``(samples, actual_rate)`` — resampled array and the actual rate.
        When *from_rate* equals *to_rate*, the input array is returned as-is
        (identity, zero copy).
    """
    if from_rate == to_rate:
        return samples, from_rate

    np = _import_numpy()

    # Try scipy.signal.resample_poly for high-quality polyphase resampling.
    try:
        from scipy.signal import resample_poly  # noqa: TID251

        g = gcd(from_rate, to_rate)
        up = to_rate // g
        down = from_rate // g
        resampled = resample_poly(samples.astype(np.float64), up, down).astype(
            np.float32
        )
        return resampled, to_rate
    except ImportError:
        pass

    # Fallback: linear interpolation with numpy.
    out_len = int(len(samples) * to_rate / from_rate)
    indices = np.linspace(0, len(samples) - 1, out_len, dtype=np.float64)
    resampled = np.interp(indices, np.arange(len(samples)), samples).astype(np.float32)
    return resampled, to_rate
