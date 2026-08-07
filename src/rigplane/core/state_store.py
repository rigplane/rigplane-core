"""Runtime-owned radio state snapshots and freshness tracking."""

from __future__ import annotations

import copy
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from rigplane.core.state_pipeline_contracts import (
    ChangeSet,
    FieldChange,
    FieldPath,
    Observation,
    SourceMetadata,
)

_DEFAULT_MAX_HISTORY_COUNT = 4096

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
    quality: tuple[str, ...] = ("confirmed",)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "value": _copy_value(self.value),
            "freshness": self.freshness.value,
            "lastObservedMonotonic": self.last_observed_monotonic,
            "maxAge": self.max_age,
            "source": self.source.to_dict(),
            "quality": list(self.quality),
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
    """Consumer-facing delta projection since a prior snapshot.

    ``requires_full_snapshot`` is true when the requested baseline is older than
    retained history; partial deltas are intentionally omitted in that case.
    """

    state_revision: int
    freshness_revision: int
    observation_seq: int
    changes: tuple[FieldChange, ...]
    freshness: tuple[FreshnessTransition, ...] = ()
    reconciliation_requests: tuple[ReconciliationRequest, ...] = ()
    requires_full_snapshot: bool = False

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
            "requiresFullSnapshot": self.requires_full_snapshot,
        }


@dataclass(slots=True)
class _FieldEntry:
    value: Any
    freshness: FreshnessState
    last_observed_monotonic: float
    max_age: float | None
    source: SourceMetadata
    quality: tuple[str, ...]


@dataclass(slots=True)
class _RelativeVfoRetention:
    generation: int
    required_paths: frozenset[FieldPath]
    paths: frozenset[FieldPath]
    max_age: float
    coherence_window: float
    pending: dict[FieldPath, Observation]
    expires_at: float | None = None


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
    """Single-writer store for confirmed radio state observations.

    Delta replay keeps at most ``max_history_count`` history entries. Requests
    older than the retained semantic or freshness floor return a replay miss
    marker so consumers can recover by requesting a full snapshot.

    Freshness decay is **not intrinsic** (MOR-432). The store never ages fields
    on its own: a field only transitions ``FRESH -> STALE`` when an external
    driver calls :meth:`mark_stale_due` (typically the periodic
    :class:`~rigplane.core.acquisition_scheduler.StateFreshnessService.run`
    loop wired over this store). A bare ``StateStore()`` with no such running
    service therefore reports last-observed values as ``FRESH`` indefinitely.
    This is acceptable only for non-canonical fallback stores; the production
    delivery stores (web server, rigctld server) always wire and drive a
    ``StateFreshnessService`` over the canonical store. See that service and
    :meth:`mark_stale_due`.
    """

    __slots__ = (
        "_entries",
        "_freshness_clock",
        "_freshness_revision",
        "_history",
        "_history_floor_freshness_revision",
        "_history_floor_state_revision",
        "_max_history_count",
        "_observation_seq",
        "_relative_vfo_retention",
        "_state_revision",
    )

    def __init__(
        self,
        *,
        freshness_clock: FreshnessClock | None = None,
        max_history_count: int = _DEFAULT_MAX_HISTORY_COUNT,
    ) -> None:
        if max_history_count < 0:
            raise ValueError("max_history_count must be non-negative")
        self._freshness_clock = freshness_clock or FreshnessClock()
        self._max_history_count = max_history_count
        self._state_revision = 0
        self._freshness_revision = 0
        self._observation_seq = 0
        self._relative_vfo_retention: _RelativeVfoRetention | None = None
        self._entries: dict[FieldPath, _FieldEntry] = {}
        self._history: list[SnapshotDelta] = []
        self._history_floor_state_revision = 0
        self._history_floor_freshness_revision = 0

    def apply(self, observation: Observation) -> ChangeSet:
        """Apply one confirmed observation and return its state ChangeSet."""

        retention = self._relative_vfo_retention
        if (
            retention is not None
            and observation.path in retention.paths
            and observation.source.provider == "vfo_binding"
        ):
            retention.pending.clear()
        return self._apply_one(observation)

    def _apply_one(self, observation: Observation) -> ChangeSet:
        """Apply one observation without relative-VFO staging."""

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
            quality=observation.quality,
        )
        changeset = ChangeSet(
            revision=self._state_revision,
            freshness_revision=self._freshness_revision,
            observation_seq=self._observation_seq,
            changes=changes,
            timestamp_monotonic=observation.timestamp_monotonic,
            sources=(observation.source,),
            coalesced=False,
            observed_paths=(observation.path,),
            freshness_paths=()
            if freshness_transition is None
            else (freshness_transition.path,),
        )
        self._append_history(
            changes=changes,
            freshness=() if freshness_transition is None else (freshness_transition,),
            reconciliation_requests=(),
        )
        return changeset

    def configure_relative_vfo_retention(
        self,
        *,
        generation: int,
        max_age: float,
        coherence_window: float,
    ) -> None:
        """Enable generation-scoped retention for Selected/Unselected tuples.

        A complete frequency+mode base is required before relative fields enter
        the canonical store. After that bootstrap, each same-generation leaf is
        authoritative immediately; a complete coherent mandatory refresh is
        required only to advance the common finite expiry deadline.
        """

        if generation < 0 or max_age <= 0 or coherence_window <= 0:
            raise ValueError("relative VFO retention values must be positive")
        paths = frozenset(
            builder("0", "freq_mode", name)
            for builder in (FieldPath.active, FieldPath.unselected)
            for name in ("freq_hz", "mode", "filter_num", "data_mode")
        )
        now = self._freshness_clock.now()
        existing = tuple(
            Observation(
                path=path,
                value=entry.value,
                source=entry.source,
                timestamp_monotonic=entry.last_observed_monotonic,
                quality=entry.quality,
                max_age=entry.max_age,
            )
            for path, entry in self._entries.items()
            if path in paths
            and entry.freshness is FreshnessState.FRESH
            and (
                entry.max_age is None
                or now - entry.last_observed_monotonic <= entry.max_age
            )
        )
        self.discard(paths)
        self._relative_vfo_retention = _RelativeVfoRetention(
            generation=generation,
            required_paths=frozenset(
                path for path in paths if path.name in {"freq_hz", "mode"}
            ),
            paths=paths,
            max_age=float(max_age),
            coherence_window=float(coherence_window),
            pending={},
        )
        self.apply_relative_vfo_observations(existing, generation=generation)

    def reset_relative_vfo_retention(self, *, generation: int) -> ChangeSet:
        """Clear all relative VFO proof and start one empty provider generation."""

        retention = self._relative_vfo_retention
        if retention is None:
            return self._empty_changeset()
        retention.generation = generation
        retention.pending.clear()
        retention.expires_at = None
        return self.discard(retention.paths)

    def apply_relative_vfo_observations(
        self,
        observations: Iterable[Observation],
        *,
        generation: int,
    ) -> ChangeSet:
        """Stage/bootstrap or immediately patch one relative VFO provider batch."""

        batch = tuple(observations)
        retention = self._relative_vfo_retention
        if retention is None:
            return self._apply_observation_batch(batch)
        if not batch or generation != retention.generation:
            return self._empty_changeset(
                timestamp=max(
                    (item.timestamp_monotonic for item in batch), default=None
                ),
            )

        relative = tuple(
            item
            for item in batch
            if item.path in retention.paths
            and (
                item.path not in self._entries
                or item.timestamp_monotonic
                >= self._entries[item.path].last_observed_monotonic
            )
        )
        changesets: list[ChangeSet] = []
        if not relative:
            return self._empty_changeset()

        newest_at = max(item.timestamp_monotonic for item in relative)
        if retention.expires_at is not None and newest_at >= retention.expires_at:
            expired = self.mark_stale_due(now=newest_at)
            if expired.freshness:
                changesets.append(
                    replace(
                        self._empty_changeset(timestamp=newest_at),
                        freshness_paths=tuple(item.path for item in expired.freshness),
                    )
                )

        if any(
            item.path in retention.required_paths and item.value is None
            for item in relative
        ):
            changesets.append(self.reset_relative_vfo_retention(generation=generation))
            return self._merge_changesets(changesets)

        for observation in relative:
            previous = retention.pending.get(observation.path)
            if (
                previous is None
                or observation.timestamp_monotonic >= previous.timestamp_monotonic
            ):
                retention.pending[observation.path] = observation

        timestamps = (
            retention.pending[path].timestamp_monotonic
            for path in retention.required_paths
            if path in retention.pending
        )
        required_times = tuple(timestamps)
        complete = (
            len(required_times) == len(retention.required_paths)
            and max(required_times) - min(required_times) <= retention.coherence_window
        )
        if retention.expires_at is None:
            if not complete:
                return self._merge_changesets(changesets)
            complete_at = max(required_times)
            retention.expires_at = complete_at + retention.max_age
            committed = tuple(
                replace(
                    item,
                    max_age=retention.expires_at - item.timestamp_monotonic,
                )
                for item in retention.pending.values()
                if abs(complete_at - item.timestamp_monotonic)
                <= retention.coherence_window
            )
            changesets.append(self.discard(retention.paths))
            changesets.append(self._apply_observation_batch(committed))
        else:
            immediate = tuple(
                replace(
                    item,
                    max_age=retention.expires_at - item.timestamp_monotonic,
                )
                for item in relative
            )
            changesets.append(self._apply_observation_batch(immediate))
            if not complete:
                return self._merge_changesets(changesets)
            retention.expires_at = max(required_times) + retention.max_age
            for path in retention.paths:
                entry = self._entries.get(path)
                if entry is not None:
                    entry.max_age = retention.expires_at - entry.last_observed_monotonic

        retention.pending.clear()
        return self._merge_changesets(changesets)

    def _expire_relative_vfo(self, *, now: float) -> tuple[FieldPath, ...]:
        retention = self._relative_vfo_retention
        if retention is None or retention.expires_at is None:
            return ()
        if now < retention.expires_at:
            return ()
        retention.expires_at = None
        retention.pending.clear()
        return tuple(retention.paths)

    def _apply_observation_batch(
        self,
        observations: tuple[Observation, ...],
    ) -> ChangeSet:
        return self._merge_changesets(
            tuple(self._apply_one(item) for item in observations)
        )

    def _empty_changeset(self, *, timestamp: float | None = None) -> ChangeSet:
        return ChangeSet(
            revision=self._state_revision,
            freshness_revision=self._freshness_revision,
            observation_seq=self._observation_seq,
            changes=(),
            timestamp_monotonic=(
                self._freshness_clock.now() if timestamp is None else timestamp
            ),
            sources=(),
            coalesced=False,
        )

    def _merge_changesets(self, changesets: Iterable[ChangeSet]) -> ChangeSet:
        parts = tuple(changesets)
        if not parts:
            return self._empty_changeset()
        return ChangeSet(
            revision=self._state_revision,
            freshness_revision=self._freshness_revision,
            observation_seq=self._observation_seq,
            changes=tuple(change for part in parts for change in part.changes),
            timestamp_monotonic=max(part.timestamp_monotonic for part in parts),
            sources=tuple(source for part in parts for source in part.sources),
            coalesced=any(part.coalesced for part in parts),
            observed_paths=tuple(
                path for part in parts for path in part.observed_paths
            ),
            freshness_paths=tuple(
                path for part in parts for path in part.freshness_paths
            ),
        )

    def discard(self, paths: Iterable[FieldPath | str]) -> ChangeSet:
        """Remove session-scoped facts so a new provider epoch starts unknown.

        This is deliberately not an observation: removing prior-session proof
        must not manufacture a fresh value or source. A semantic revision is
        emitted when at least one field existed so snapshot consumers publish
        the corresponding ``missing`` field status.
        """

        removed: list[FieldChange] = []
        for item in paths:
            path = FieldPath.parse(item) if isinstance(item, str) else item
            entry = self._entries.pop(path, None)
            if entry is None:
                continue
            removed.append(
                FieldChange(
                    path=path,
                    previous=_copy_value(entry.value),
                    current=None,
                )
            )
        changes = tuple(removed)
        if changes:
            self._state_revision += 1
            self._append_history(
                changes=changes,
                freshness=(),
                reconciliation_requests=(),
            )
        return ChangeSet(
            revision=self._state_revision,
            freshness_revision=self._freshness_revision,
            observation_seq=self._observation_seq,
            changes=changes,
            timestamp_monotonic=self._freshness_clock.now(),
            sources=(),
            coalesced=False,
        )

    def mark_stale_due(self, *, now: float | None = None) -> SnapshotDelta:
        """Mark overdue fresh fields stale and emit reconciliation hints.

        This is the **sole** freshness-decay entry point: the store does not
        age fields automatically (MOR-432). It must be driven externally — in
        production by the periodic
        :class:`~rigplane.core.acquisition_scheduler.StateFreshnessService`
        loop. If nothing calls this, fields never go ``STALE``.
        """

        timestamp = self._freshness_clock.now() if now is None else now
        transitions: list[FreshnessTransition] = []
        requests: list[ReconciliationRequest] = []
        relative_due = set(self._expire_relative_vfo(now=timestamp))
        for path, entry in sorted(self._entries.items(), key=lambda item: str(item[0])):
            if entry.freshness is not FreshnessState.FRESH or entry.max_age is None:
                continue
            if (
                path not in relative_due
                and timestamp - entry.last_observed_monotonic <= entry.max_age
            ):
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

        freshness = tuple(transitions)
        reconciliation_requests = tuple(requests)
        delta = SnapshotDelta(
            state_revision=self._state_revision,
            freshness_revision=self._freshness_revision,
            observation_seq=self._observation_seq,
            changes=(),
            freshness=freshness,
            reconciliation_requests=reconciliation_requests,
        )
        self._append_history(
            changes=(),
            freshness=freshness,
            reconciliation_requests=reconciliation_requests,
        )
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
                    quality=entry.quality,
                )
                for path, entry in sorted(
                    self._entries.items(),
                    key=lambda item: str(item[0]),
                )
            ),
        )

    def delta_since(self, snapshot: StateSnapshot) -> SnapshotDelta:
        """Return all semantic and freshness deltas after ``snapshot``."""

        if self._requires_full_snapshot(snapshot):
            return SnapshotDelta(
                state_revision=self._state_revision,
                freshness_revision=self._freshness_revision,
                observation_seq=self._observation_seq,
                changes=(),
                freshness=(),
                reconciliation_requests=(),
                requires_full_snapshot=True,
            )

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

    def _requires_full_snapshot(self, snapshot: StateSnapshot) -> bool:
        return (
            snapshot.state_revision < self._history_floor_state_revision
            or snapshot.freshness_revision < self._history_floor_freshness_revision
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
        self._prune_history()

    def _prune_history(self) -> None:
        while len(self._history) > self._max_history_count:
            pruned = self._history.pop(0)
            if pruned.changes:
                self._history_floor_state_revision = max(
                    self._history_floor_state_revision,
                    pruned.state_revision,
                )
            if pruned.freshness or pruned.reconciliation_requests:
                self._history_floor_freshness_revision = max(
                    self._history_floor_freshness_revision,
                    pruned.freshness_revision,
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
