"""Bridge connection state machine.

State diagram::

                     start()
    IDLE ─────────────────────────► CONNECTING
                                        │
                      streams opened    │    error
                            ┌───────────┘       │
                            ▼                   ▼
                        RUNNING ──────► RECONNECTING
                            ▲    stream      │
                            │     error      │  max_retries exhausted
                            │                ▼
                            │              FAILED
                            │
                  reconnect success ◄── backoff sleep
"""

from __future__ import annotations

import dataclasses
import enum

__all__ = ["BridgeState", "BridgeStateChange"]


class BridgeState(enum.Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclasses.dataclass(frozen=True, slots=True)
class BridgeStateChange:
    previous: BridgeState
    current: BridgeState
    reason: str
    attempt: int = 0
