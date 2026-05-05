"""BridgeMetrics — structured audio bridge telemetry."""

from __future__ import annotations

import dataclasses
from typing import Any

__all__ = ["BridgeMetrics"]


@dataclasses.dataclass(slots=True)
class BridgeMetrics:
    """Audio bridge telemetry snapshot.

    All timing values are in seconds (float) or milliseconds (float)
    as indicated by the field name suffix.
    """

    running: bool = False
    label: str = ""
    bridge_state: str = "idle"
    reconnect_attempt: int = 0

    # Frame counters
    rx_frames: int = 0
    tx_frames: int = 0
    rx_drops: int = 0

    # Underrun / overrun
    rx_underruns: int = 0
    tx_overruns: int = 0

    # Timing
    uptime_seconds: float = 0.0
    rx_interval_ms: float = 0.0
    tx_interval_ms: float = 0.0

    # Jitter (standard deviation of inter-frame interval)
    rx_jitter_ms: float = 0.0
    tx_jitter_ms: float = 0.0

    # Audio levels (RMS in dBFS, 0.0 = full scale)
    rx_level_dbfs: float = -96.0
    tx_level_dbfs: float = -96.0

    # Latency sample buffer size
    buffer_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (JSON-safe)."""
        return dataclasses.asdict(self)
