"""DSP pipeline core: DSPNode protocol and DSPPipeline orchestrator.

All DSP nodes operate on float32 numpy arrays (mono, range [-1.0, 1.0]).
The pipeline chains nodes in order, skipping disabled ones.

Numpy is lazy-imported to avoid a hard dependency at module level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from icom_lan.dsp.exceptions import DSPBackendUnavailable, DSPConfigError
from icom_lan.dsp.resample import resample_if_needed

if TYPE_CHECKING:
    import numpy as np

__all__ = ["DSPNode", "DSPPipeline"]

_INT16_MAX = 32767
_INT16_MIN = -32768


def _import_numpy() -> Any:
    """Lazy-import numpy to avoid hard dependency at module level."""
    try:
        import numpy as np

        return np
    except ImportError:
        raise DSPBackendUnavailable(
            "DSP pipeline requires numpy. Install with: pip install numpy"
        ) from None


@runtime_checkable
class DSPNode(Protocol):
    """Protocol for a single DSP processing node.

    Attributes:
        name: Unique identifier for this node.
        enabled: Whether the node is active in the pipeline.
        required_sample_rate: Expected sample rate, or None to accept any.
    """

    name: str
    enabled: bool
    required_sample_rate: int | None

    def process(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Process audio samples.

        Args:
            samples: Float32 numpy array, mono, range [-1.0, 1.0].
            sample_rate: Sample rate in Hz.

        Returns:
            Processed float32 numpy array.
        """
        ...

    def get_params(self) -> dict[str, Any]:
        """Return current node parameters."""
        ...

    def set_params(self, **kwargs: Any) -> None:
        """Update node parameters."""
        ...

    def reset(self) -> None:
        """Reset internal state."""
        ...


class DSPPipeline:
    """Ordered chain of DSP nodes.

    Processes audio samples through nodes sequentially, skipping disabled ones.
    Provides convenience methods for s16le byte conversion and serialization.
    """

    def __init__(self, nodes: list[DSPNode] | None = None) -> None:
        self._nodes: list[DSPNode] = list(nodes) if nodes else []

    # -- Node management -----------------------------------------------------

    def add_node(self, node: DSPNode) -> None:
        """Append a node to the pipeline."""
        self._nodes.append(node)

    def remove_node(self, name: str) -> None:
        """Remove a node by name.

        Raises:
            KeyError: If no node with the given name exists.
        """
        for i, n in enumerate(self._nodes):
            if n.name == name:
                self._nodes.pop(i)
                return
        raise KeyError(f"No DSP node named {name!r}")

    def get_node(self, name: str) -> DSPNode | None:
        """Return a node by name, or None if not found."""
        for n in self._nodes:
            if n.name == name:
                return n
        return None

    @property
    def empty(self) -> bool:
        """True if the pipeline has no nodes."""
        return len(self._nodes) == 0

    # -- Processing ----------------------------------------------------------

    def process(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Run samples through all enabled nodes in order.

        Args:
            samples: Float32 numpy array.
            sample_rate: Sample rate in Hz.

        Returns:
            Processed float32 numpy array.
        """
        result = samples
        for node in self._nodes:
            if not node.enabled:
                continue
            node_rate = node.required_sample_rate
            if node_rate is not None and node_rate != sample_rate:
                result, _ = resample_if_needed(result, sample_rate, node_rate)
                result = node.process(result, node_rate)
                result, _ = resample_if_needed(result, node_rate, sample_rate)
            else:
                result = node.process(result, sample_rate)
        return result

    def process_bytes(self, pcm: bytes, sample_rate: int = 48000) -> bytes:
        """Convenience: s16le bytes -> float32 -> process -> s16le bytes.

        Zero overhead when no nodes are present.

        Args:
            pcm: Raw PCM s16le bytes.
            sample_rate: Sample rate in Hz.

        Returns:
            Processed PCM s16le bytes.
        """
        if not self._nodes:
            return pcm

        np = _import_numpy()

        # s16le -> float32 [-1.0, 1.0]
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / _INT16_MAX

        # Process
        samples = self.process(samples, sample_rate)

        # float32 -> s16le (clip to valid range)
        int_samples = np.clip(
            np.round(samples * _INT16_MAX), _INT16_MIN, _INT16_MAX
        ).astype(np.int16)
        return bytes(int_samples.tobytes())

    # -- State ---------------------------------------------------------------

    def reset(self) -> None:
        """Call reset on all nodes."""
        for node in self._nodes:
            node.reset()

    # -- Serialization -------------------------------------------------------

    def to_config(self) -> list[dict[str, Any]]:
        """Serialize pipeline to a list of node configs.

        Returns:
            List of dicts with keys: name, enabled, params.
        """
        return [
            {
                "name": node.name,
                "enabled": node.enabled,
                "params": node.get_params(),
            }
            for node in self._nodes
        ]

    @classmethod
    def from_config(
        cls,
        config: list[dict[str, Any]],
        registry: dict[str, Any],
    ) -> DSPPipeline:
        """Rebuild a pipeline from serialized config.

        Args:
            config: List of node configs (from to_config).
            registry: Mapping of node name -> factory callable(name, params).

        Returns:
            New DSPPipeline instance.

        Raises:
            DSPConfigError: If a node name is not in the registry.
        """
        nodes: list[DSPNode] = []
        for entry in config:
            name = entry["name"]
            if name not in registry:
                raise DSPConfigError(
                    f"Unknown DSP node {name!r} — not found in registry"
                )
            factory = registry[name]
            node = factory(name, entry.get("params", {}))
            node.enabled = entry.get("enabled", True)
            nodes.append(node)
        return cls(nodes)
