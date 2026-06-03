"""Behavior-neutral diagnostics for legacy state pipeline migration."""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "StateDiagnosticEvent",
    "StateDiagnosticKind",
    "StateDiagnosticsRecorder",
]

StateDiagnosticKind = Literal[
    "backend_read",
    "direct_state_write",
    "meter_cadence",
    "revision_producing_event",
    "rigctld_delivery_trigger",
    "web_delivery_trigger",
]


@dataclass(frozen=True, slots=True)
class StateDiagnosticEvent:
    """One recorded state-pipeline diagnostic event."""

    kind: str
    source: str
    monotonic_ts: float
    details: dict[str, Any]


class StateDiagnosticsRecorder:
    """Small in-memory event recorder, disabled by default.

    The recorder intentionally has no side effects when disabled. Runtime
    instrumentation may call :meth:`record` from hot paths; normal behavior is
    unchanged unless tests or debug startup explicitly enable this object.
    """

    def __init__(self, *, enabled: bool = False, max_events: int = 512) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self.enabled = enabled
        self._events: deque[StateDiagnosticEvent] = deque(maxlen=max_events)
        self._counts: Counter[str] = Counter()

    def record(
        self,
        kind: StateDiagnosticKind | str,
        source: str,
        **details: Any,
    ) -> StateDiagnosticEvent | None:
        """Record an event when enabled; otherwise return ``None``."""
        if not self.enabled:
            return None
        event = StateDiagnosticEvent(
            kind=str(kind),
            source=source,
            monotonic_ts=time.monotonic(),
            details=dict(details),
        )
        self._events.append(event)
        self._counts[event.kind] += 1
        return event

    def events(self) -> tuple[StateDiagnosticEvent, ...]:
        """Return recorded events in insertion order."""
        return tuple(self._events)

    def clear(self) -> None:
        """Clear recorded events and counters."""
        self._events.clear()
        self._counts.clear()

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of recorded diagnostics."""
        return {
            "enabled": self.enabled,
            "counts": dict(self._counts),
            "events": [
                {
                    "kind": event.kind,
                    "source": event.source,
                    "monotonicTs": event.monotonic_ts,
                    "details": dict(event.details),
                }
                for event in self._events
            ],
        }
