"""Runtime-owned radio state snapshots and freshness tracking."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from rigplane.core.state_pipeline_contracts import (
    ChangeSet,
    FieldChange,
    FieldPath,
    Observation,
    SourceMetadata,
)

__all__ = [
    "FieldSnapshot",
    "FreshnessClock",
    "FreshnessState",
    "FreshnessTransition",
    "ReconciliationRequest",
    "SnapshotDelta",
    "StateSnapshot",
    "StateStore",
]


class FreshnessState(StrEnum):
    """Freshness state for one observed field."""

    UNKNOWN = "unknown"
    FRESH = "fresh"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class FreshnessTransition:
    """Freshness-only transition for a field."""

    path: FieldPath
    previous: FreshnessState
    current: FreshnessState
    freshness_revision: int
    timestamp_monotonic: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "previous": self.previous.value,
            "current": self.current.value,
            "freshnessRevision": self.freshness_revision,
            "timestampMonotonic": self.timestamp_monotonic,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationRequest:
    """A future scheduler hint emitted when a field becomes stale."""

    path: FieldPath
    reason: str
    requested_at_monotonic: float
    state_revision: int
    freshness_revision: int
    max_age: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "reason": self.reason,
            "requestedAtMonotonic": self.requested_at_monotonic,
            "stateRevision": self.state_revision,
            "freshnessRevision": self.freshness_revision,
            "maxAge": self.max_age,
        }


@dataclass(frozen=True, slots=True)
class FieldSnapshot:
    """Consumer-facing snapshot of one field."""

    path: FieldPath
    value: Any
    freshness: FreshnessState
    last_observed_monotonic: float
    max_age: float | None
    source: SourceMetadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "value": _copy_value(self.value),
            "freshness": self.freshness.value,
            "lastObservedMonotonic": self.last_observed_monotonic,
            "maxAge": self.max_age,
            "source": self.source.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """Immutable projection of the store-owned state at one point in time."""

    state_revision: int
    freshness_revision: int
    observation_seq: int
    generated_at_monotonic: float
    fields: tuple[FieldSnapshot, ...]

    @classmethod
    def empty(cls) -> StateSnapshot:
        return cls(
            state_revision=0,
            freshness_revision=0,
            observation_seq=0,
            generated_at_monotonic=0.0,
            fields=(),
        )

    def field(self, path: FieldPath | str) -> FieldSnapshot:
        needle = FieldPath.parse(path) if isinstance(path, str) else path
        for field in self.fields:
            if field.path == needle:
                return field
        raise KeyError(str(needle))

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {str(field.path): field.to_dict() for field in self.fields}

    def to_dict(self) -> dict[str, Any]:
        return {
            "stateRevision": self.state_revision,
            "freshnessRevision": self.freshness_revision,
            "observationSeq": self.observation_seq,
            "generatedAtMonotonic": self.generated_at_monotonic,
            "fields": [field.to_dict() for field in self.fields],
        }


@dataclass(frozen=True, slots=True)
class SnapshotDelta:
    """Consumer-facing delta projection since a prior snapshot."""

    state_revision: int
    freshness_revision: int
    observation_seq: int
    changes: tuple[FieldChange, ...]
    freshness: tuple[FreshnessTransition, ...] = ()
    reconciliation_requests: tuple[ReconciliationRequest, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "stateRevision": self.state_revision,
            "freshnessRevision": self.freshness_revision,
            "observationSeq": self.observation_seq,
            "changes": [change.to_dict() for change in self.changes],
            "freshness": [transition.to_dict() for transition in self.freshness],
            "reconciliationRequests": [
                request.to_dict() for request in self.reconciliation_requests
            ],
        }


@dataclass(slots=True)
class _FieldEntry:
    value: Any
    freshness: FreshnessState
    last_observed_monotonic: float
    max_age: float | None
    source: SourceMetadata


class FreshnessClock:
    """Monotonic clock used by freshness expiration."""

    __slots__ = ("_manual_now",)

    def __init__(self, *, start: float | None = None) -> None:
        self._manual_now = None if start is None else float(start)

    def now(self) -> float:
        if self._manual_now is None:
            return time.monotonic()
        return self._manual_now

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("freshness clock cannot move backwards")
        if self._manual_now is None:
            self._manual_now = time.monotonic()
        self._manual_now += seconds
        return self._manual_now


class StateStore:
    """Single-writer store for confirmed radio state observations."""

    __slots__ = (
        "_entries",
        "_freshness_clock",
        "_freshness_revision",
        "_history",
        "_observation_seq",
        "_state_revision",
    )

    def __init__(self, *, freshness_clock: FreshnessClock | None = None) -> None:
        self._freshness_clock = freshness_clock or FreshnessClock()
        self._state_revision = 0
        self._freshness_revision = 0
        self._observation_seq = 0
        self._entries: dict[FieldPath, _FieldEntry] = {}
        self._history: list[SnapshotDelta] = []

    def apply(self, observation: Observation) -> ChangeSet:
        """Apply one confirmed observation and return its state ChangeSet."""

        self._observation_seq += 1
        previous_entry = self._entries.get(observation.path)
        previous_freshness = (
            FreshnessState.UNKNOWN
            if previous_entry is None
            else previous_entry.freshness
        )
        freshness_transition = self._mark_fresh(
            observation.path,
            previous_freshness=previous_freshness,
            timestamp_monotonic=observation.timestamp_monotonic,
        )

        previous_value = None if previous_entry is None else previous_entry.value
        semantic_changed = previous_entry is None or previous_value != observation.value
        changes: tuple[FieldChange, ...]
        if semantic_changed:
            self._state_revision += 1
            changes = (
                FieldChange(
                    path=observation.path,
                    previous=_copy_value(previous_value),
                    current=_copy_value(observation.value),
                ),
            )
        else:
            changes = ()

        self._entries[observation.path] = _FieldEntry(
            value=_copy_value(observation.value),
            freshness=FreshnessState.FRESH,
            last_observed_monotonic=observation.timestamp_monotonic,
            max_age=observation.max_age,
            source=observation.source,
        )
        changeset = ChangeSet(
            revision=self._state_revision,
            freshness_revision=self._freshness_revision,
            observation_seq=self._observation_seq,
            changes=changes,
            timestamp_monotonic=observation.timestamp_monotonic,
            sources=(observation.source,),
            coalesced=False,
        )
        self._append_history(
            changes=changes,
            freshness=() if freshness_transition is None else (freshness_transition,),
            reconciliation_requests=(),
        )
        return changeset

    def mark_stale_due(self, *, now: float | None = None) -> SnapshotDelta:
        """Mark overdue fresh fields stale and emit reconciliation hints."""

        timestamp = self._freshness_clock.now() if now is None else now
        transitions: list[FreshnessTransition] = []
        requests: list[ReconciliationRequest] = []
        for path, entry in sorted(self._entries.items(), key=lambda item: str(item[0])):
            if entry.freshness is not FreshnessState.FRESH or entry.max_age is None:
                continue
            if timestamp - entry.last_observed_monotonic <= entry.max_age:
                continue

            self._freshness_revision += 1
            entry.freshness = FreshnessState.STALE
            transition = FreshnessTransition(
                path=path,
                previous=FreshnessState.FRESH,
                current=FreshnessState.STALE,
                freshness_revision=self._freshness_revision,
                timestamp_monotonic=timestamp,
            )
            request = ReconciliationRequest(
                path=path,
                reason="stale",
                requested_at_monotonic=timestamp,
                state_revision=self._state_revision,
                freshness_revision=self._freshness_revision,
                max_age=entry.max_age,
            )
            transitions.append(transition)
            requests.append(request)

        delta = SnapshotDelta(
            state_revision=self._state_revision,
            freshness_revision=self._freshness_revision,
            observation_seq=self._observation_seq,
            changes=(),
            freshness=tuple(transitions),
            reconciliation_requests=tuple(requests),
        )
        if transitions or requests:
            self._history.append(delta)
        return delta

    def snapshot(self) -> StateSnapshot:
        """Return a full immutable projection of the current store state."""

        return StateSnapshot(
            state_revision=self._state_revision,
            freshness_revision=self._freshness_revision,
            observation_seq=self._observation_seq,
            generated_at_monotonic=self._freshness_clock.now(),
            fields=tuple(
                FieldSnapshot(
                    path=path,
                    value=_copy_value(entry.value),
                    freshness=entry.freshness,
                    last_observed_monotonic=entry.last_observed_monotonic,
                    max_age=entry.max_age,
                    source=entry.source,
                )
                for path, entry in sorted(
                    self._entries.items(),
                    key=lambda item: str(item[0]),
                )
            ),
        )

    def delta_since(self, snapshot: StateSnapshot) -> SnapshotDelta:
        """Return all semantic and freshness deltas after ``snapshot``."""

        changes: list[FieldChange] = []
        freshness: list[FreshnessTransition] = []
        requests: list[ReconciliationRequest] = []
        for delta in self._history:
            if delta.state_revision > snapshot.state_revision:
                changes.extend(delta.changes)
            freshness.extend(
                transition
                for transition in delta.freshness
                if transition.freshness_revision > snapshot.freshness_revision
            )
            requests.extend(
                request
                for request in delta.reconciliation_requests
                if request.freshness_revision > snapshot.freshness_revision
            )

        return SnapshotDelta(
            state_revision=self._state_revision,
            freshness_revision=self._freshness_revision,
            observation_seq=self._observation_seq,
            changes=_copy_changes(tuple(changes)),
            freshness=tuple(freshness),
            reconciliation_requests=tuple(requests),
        )

    def _mark_fresh(
        self,
        path: FieldPath,
        *,
        previous_freshness: FreshnessState,
        timestamp_monotonic: float,
    ) -> FreshnessTransition | None:
        if previous_freshness is FreshnessState.FRESH:
            return None
        self._freshness_revision += 1
        return FreshnessTransition(
            path=path,
            previous=previous_freshness,
            current=FreshnessState.FRESH,
            freshness_revision=self._freshness_revision,
            timestamp_monotonic=timestamp_monotonic,
        )

    def _append_history(
        self,
        *,
        changes: tuple[FieldChange, ...],
        freshness: tuple[FreshnessTransition, ...],
        reconciliation_requests: tuple[ReconciliationRequest, ...],
    ) -> None:
        if not changes and not freshness and not reconciliation_requests:
            return
        self._history.append(
            SnapshotDelta(
                state_revision=self._state_revision,
                freshness_revision=self._freshness_revision,
                observation_seq=self._observation_seq,
                changes=_copy_changes(changes),
                freshness=freshness,
                reconciliation_requests=reconciliation_requests,
            )
        )


def _copy_value(value: Any) -> Any:
    return copy.deepcopy(value)


def _copy_changes(changes: tuple[FieldChange, ...]) -> tuple[FieldChange, ...]:
    return tuple(
        FieldChange(
            path=change.path,
            previous=_copy_value(change.previous),
            current=_copy_value(change.current),
        )
        for change in changes
    )
