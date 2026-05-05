"""NRScipyNode — spectral subtraction noise reduction.

Uses FFT-based spectral subtraction with a running minimum noise estimate.
Requires scipy; raises DSPBackendUnavailable if not importable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from icom_lan.dsp.exceptions import DSPBackendUnavailable

if TYPE_CHECKING:
    import numpy as np

__all__ = ["NRScipyNode"]

# Number of initial frames used to bootstrap the noise estimate.
_WARMUP_FRAMES = 4
# Smoothing factor for running minimum noise estimate update.
_NOISE_SMOOTH = 0.98
# Spectral floor to prevent musical noise artifacts.
_SPECTRAL_FLOOR = 0.02


def _import_scipy_fft() -> Any:
    """Lazy-import scipy.fft."""
    try:
        import scipy.fft  # noqa: TID251

        return scipy.fft
    except ImportError:
        raise DSPBackendUnavailable(
            "NRScipyNode requires scipy. Install with: pip install scipy"
        ) from None


def _import_numpy() -> Any:
    """Lazy-import numpy."""
    try:
        import numpy as _np  # noqa: TID251

        return _np
    except ImportError:
        raise DSPBackendUnavailable(
            "NRScipyNode requires numpy. Install with: pip install numpy"
        ) from None


class NRScipyNode:
    """Spectral subtraction noise reduction node.

    Implements the DSPNode protocol.

    Attributes:
        name: ``"nr_scipy"``
        enabled: Whether the node is active in the pipeline.
        required_sample_rate: ``48000``
    """

    name: str = "nr_scipy"
    enabled: bool = True
    required_sample_rate: int | None = 48000

    def __init__(self, strength: float = 0.6) -> None:
        # Eagerly verify that scipy and numpy are available.
        self._fft = _import_scipy_fft()
        self._np = _import_numpy()

        self._strength: float = strength
        self._noise_estimate: np.ndarray | None = None
        self._frame_count: int = 0

    # -- DSPNode interface -----------------------------------------------------

    def process(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Apply spectral subtraction noise reduction.

        Args:
            samples: Float32 numpy array, mono.
            sample_rate: Sample rate in Hz.

        Returns:
            Denoised float32 numpy array of the same length.
        """
        np = self._np
        fft = self._fft

        n = len(samples)
        if n == 0:
            return samples

        # Forward FFT
        spectrum = fft.rfft(samples)
        magnitude = np.abs(spectrum)
        phase = np.angle(spectrum)

        # --- Noise estimation ---
        if self._noise_estimate is None:
            # First frame: initialise estimate from current magnitude.
            self._noise_estimate = magnitude.copy()
            self._frame_count = 1
        elif self._frame_count < _WARMUP_FRAMES:
            # Warmup: accumulate a running average.
            self._noise_estimate = (
                self._noise_estimate * self._frame_count + magnitude
            ) / (self._frame_count + 1)
            self._frame_count += 1
        else:
            # Exponential smoothing — tracks slowly-changing noise floor.
            self._noise_estimate = self._noise_estimate * _NOISE_SMOOTH + magnitude * (
                1 - _NOISE_SMOOTH
            )
            self._frame_count += 1

        # --- Spectral subtraction ---
        subtracted = magnitude - self._strength * self._noise_estimate
        # Apply spectral floor to prevent musical noise.
        floor = _SPECTRAL_FLOOR * magnitude
        clean_mag = np.maximum(subtracted, floor)

        # Reconstruct
        clean_spectrum = clean_mag * np.exp(1j * phase)
        result = fft.irfft(clean_spectrum, n=n)

        return result.astype(np.float32)  # type: ignore[no-any-return]

    def get_params(self) -> dict[str, Any]:
        """Return current node parameters."""
        return {"strength": self._strength}

    def set_params(self, **kwargs: Any) -> None:
        """Update node parameters."""
        if "strength" in kwargs:
            self._strength = float(kwargs["strength"])

    def reset(self) -> None:
        """Reset internal state — clears the noise estimate."""
        self._noise_estimate = None
        self._frame_count = 0
