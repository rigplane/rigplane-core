"""Base DSP nodes: PassthroughNode and GainNode.

These are the fundamental building blocks for DSP pipelines.
Numpy is lazy-imported to avoid a hard dependency at module level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from icom_lan.dsp.exceptions import DSPBackendUnavailable

if TYPE_CHECKING:
    import numpy as np


def _import_numpy() -> Any:
    """Lazy-import numpy to avoid hard dependency at module level."""
    try:
        import numpy as np

        return np
    except ImportError:
        raise DSPBackendUnavailable(
            "DSP nodes require numpy. Install with: pip install numpy"
        ) from None


class PassthroughNode:
    """DSP node that passes samples through unchanged.

    Useful as a pipeline placeholder or for testing.
    """

    name: str = "passthrough"
    enabled: bool = True
    required_sample_rate: int | None = None

    def process(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Return samples unchanged."""
        return samples

    def get_params(self) -> dict[str, Any]:
        """Return empty params dict."""
        return {}

    def set_params(self, **kwargs: Any) -> None:
        """No-op — passthrough has no parameters."""

    def reset(self) -> None:
        """No-op — passthrough has no internal state."""


class GainNode:
    """DSP node that applies linear gain and clips to [-1.0, 1.0].

    Args:
        gain_db: Gain in decibels. 0.0 = unity (passthrough).
    """

    name: str = "gain"
    enabled: bool = True
    required_sample_rate: int | None = None

    def __init__(self, gain_db: float = 0.0) -> None:
        self._gain_db = gain_db
        self._linear_gain = 10.0 ** (gain_db / 20.0)

    def process(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Apply gain and clip to [-1.0, 1.0]."""
        _np = _import_numpy()
        result = samples * self._linear_gain
        clipped: np.ndarray = _np.clip(result, -1.0, 1.0)
        return clipped

    def get_params(self) -> dict[str, Any]:
        """Return current gain parameter."""
        return {"gain_db": self._gain_db}

    def set_params(self, **kwargs: Any) -> None:
        """Update gain_db if provided."""
        if "gain_db" in kwargs:
            self._gain_db = float(kwargs["gain_db"])
            self._linear_gain = 10.0 ** (self._gain_db / 20.0)

    def reset(self) -> None:
        """No-op — gain node has no internal state to reset."""
