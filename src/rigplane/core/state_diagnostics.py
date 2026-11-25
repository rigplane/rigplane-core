"""Behavior-neutral diagnostics for legacy state pipeline migration."""

from __future__ import annotations

import logging
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "StateDiagnosticEvent",
    "StateDiagnosticKind",
    "StateDiagnosticsRecorder",
    "UNKNOWN_STATUS",
]

logger = logging.getLogger(__name__)
UNKNOWN_STATUS = "UNKNOWN"

StateDiagnosticKind = Literal[
    "backend_read",
    "direct_state_write",
    "revision_producing_event",
    "rigctld_delivery_trigger",
    "web_delivery_trigger",
    "unknown_status",
]


@dataclass(frozen=True, slots=True)
class StateDiagnosticEvent:
    """One recorded state-pipeline diagnostic event."""

    kind: str
    source: str
    monotonic_ts: float
    details: dict[str, Any]


class StateDiagnosticsRecorder:
    """Record operational warnings with optional state-pipeline tracing."""

    def __init__(self, *, enabled: bool = False, max_events: int = 512) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self.enabled = enabled
        self._events: deque[StateDiagnosticEvent] = deque(maxlen=max_events)
        self._counts: Counter[str] = Counter()
        self._unknown_status_values: set[tuple[str, str | int]] = set()

    def status_name(
        self, *, field: str, raw: str | int, name: str | None, source: str
    ) -> str:
        """Return the decoded status and report each unassigned value once."""
        if name is not None:
            return name
        key = (field, raw)
        if key not in self._unknown_status_values:
            self._unknown_status_values.add(key)
            message = f"Unrecognized radio status {field}={raw!r} ({source})"
            logger.warning("%s", message)
            self.record("unknown_status", source, field=field, raw=raw, message=message)
        return UNKNOWN_STATUS

    def record(
        self,
        kind: StateDiagnosticKind | str,
        source: str,
        **details: Any,
    ) -> StateDiagnosticEvent | None:
        """Record operational warnings and, when enabled, debug events."""
        if not self.enabled and kind != "unknown_status":
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
