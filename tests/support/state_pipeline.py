"""Deterministic fakes for radio state pipeline regression tests.

These helpers model the state-pipeline contracts from the design spec without
depending on a production StateStore implementation. They are intentionally
small so Web, rigctld, backend adapter, scheduler, and command-service tests can
share the same revision and freshness assertions while production code evolves.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

ObservationSource = Literal[
    "civ_unsolicited",
    "command_response",
    "poll_response",
    "state_poller",
    "hamlib_response",
    "yaesu_poll_response",
    "local_reconcile",
]

FreshnessState = Literal["unknown", "fresh", "stale"]


@dataclass(frozen=True, order=True, slots=True)
class FieldPath:
    """Backend-neutral state path for tests."""

    scope: str
    receiver_id: str | None
    slot: str | None
    family: str
    name: str

    @classmethod
    def receiver(
        cls,
        receiver: str,
        slot: str | None,
        family: str,
        name: str,
    ) -> FieldPath:
        return cls(
            scope="receiver",
            receiver_id=receiver,
            slot=slot,
            family=family,
            name=name,
        )

    @classmethod
    def global_(cls, family: str, name: str) -> FieldPath:
        return cls(
            scope="global",
            receiver_id=None,
            slot=None,
            family=family,
            name=name,
        )


@dataclass(frozen=True, slots=True)
class Observation:
    """A decoded state-bearing sample from an acquisition source."""

    path: FieldPath
    value: Any
    source: ObservationSource
    timestamp_monotonic: float
    quality: tuple[str, ...] = ("confirmed",)
    correlation_id: str | None = None
    max_age: float | None = None


@dataclass(frozen=True, slots=True)
class FieldChange:
    """One consumer-visible state value change."""

    path: FieldPath
    previous: Any
    current: Any


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """Result of applying one or more observations to the fake state model."""

    state_revision: int
    freshness_revision: int
    observation_seq: int
    changes: tuple[FieldChange, ...]
    timestamp_monotonic: float
    sources: tuple[ObservationSource, ...]
    coalesced: bool = False


@dataclass(frozen=True, slots=True)
class FreshnessTransition:
    """A freshness-only transition visible to state consumers."""

    path: FieldPath
    previous: FreshnessState
    current: FreshnessState
    freshness_revision: int
    timestamp_monotonic: float


@dataclass(frozen=True, slots=True)
class AcquisitionRequest:
    """Fake acquisition request scheduled by an ensure-fresh call."""

    paths: tuple[FieldPath, ...]
    max_age: float
    timeout: float
    reason: str
    requested_at: float
    due_at: float


@dataclass(frozen=True, slots=True)
class PendingOverlay:
    """Scoped pending value created by a command intent."""

    source: str
    session_id: str | None
    command_id: str
    path: FieldPath
    value: Any
    expires_at: float


@dataclass(slots=True)
class _FreshnessEntry:
    state: FreshnessState
    last_observed: float
    source: ObservationSource
    max_age: float | None


class FakeClock:
    """Manual monotonic clock for freshness and scheduler tests."""

    def __init__(self, *, start: float = 0.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("fake time cannot move backwards")
        self._now += seconds
        return self._now


class FakeStatePipeline:
    """Small state model with spec-compatible revision counters."""

    def __init__(self, *, clock: FakeClock | None = None) -> None:
        self.clock = clock or FakeClock()
        self.state_revision = 0
        self.freshness_revision = 0
        self.observation_seq = 0
        self.observations: list[Observation] = []
        self.consumer_deltas: list[ChangeSet] = []
        self.freshness_events: list[FreshnessTransition] = []
        self._state: dict[FieldPath, Any] = {}
        self._freshness: dict[FieldPath, _FreshnessEntry] = {}

    def apply(self, observation: Observation | None) -> ChangeSet | None:
        """Apply an observation and return a delta only for real value changes."""

        if observation is None:
            return None

        self.observation_seq += 1
        self.observations.append(observation)

        freshness_entry = self._freshness.get(observation.path)
        previous_freshness = freshness_entry.state if freshness_entry else "unknown"
        self._freshness[observation.path] = _FreshnessEntry(
            state="fresh",
            last_observed=observation.timestamp_monotonic,
            source=observation.source,
            max_age=observation.max_age,
        )
        if previous_freshness != "fresh":
            self.freshness_revision += 1

        previous = self._state.get(observation.path)
        if observation.path in self._state and previous == observation.value:
            return None

        self._state[observation.path] = observation.value
        self.state_revision += 1
        changeset = ChangeSet(
            state_revision=self.state_revision,
            freshness_revision=self.freshness_revision,
            observation_seq=self.observation_seq,
            changes=(
                FieldChange(
                    path=observation.path,
                    previous=previous,
                    current=observation.value,
                ),
            ),
            timestamp_monotonic=observation.timestamp_monotonic,
            sources=(observation.source,),
        )
        self.consumer_deltas.append(changeset)
        return changeset

    def mark_stale_due(self) -> list[FreshnessTransition]:
        """Advance freshness state for fields whose max-age deadline passed."""

        now = self.clock.now()
        transitions: list[FreshnessTransition] = []
        for path, entry in sorted(self._freshness.items()):
            if entry.state != "fresh" or entry.max_age is None:
                continue
            if now - entry.last_observed <= entry.max_age:
                continue
            self.freshness_revision += 1
            self._freshness[path] = _FreshnessEntry(
                state="stale",
                last_observed=entry.last_observed,
                source=entry.source,
                max_age=entry.max_age,
            )
            transition = FreshnessTransition(
                path=path,
                previous="fresh",
                current="stale",
                freshness_revision=self.freshness_revision,
                timestamp_monotonic=now,
            )
            transitions.append(transition)
            self.freshness_events.append(transition)
        return transitions

    def snapshot(self) -> dict[FieldPath, Any]:
        return dict(self._state)


class FakeAcquisitionScheduler:
    """Fake ensure-fresh scheduler with deterministic due times."""

    def __init__(self, *, clock: FakeClock | None = None) -> None:
        self.clock = clock or FakeClock()
        self.requests: list[AcquisitionRequest] = []
        self._requests_by_key: dict[tuple[FieldPath, ...], AcquisitionRequest] = {}

    def ensure_fresh(
        self,
        paths: Iterable[FieldPath],
        *,
        max_age: float,
        timeout: float,
        reason: str,
    ) -> AcquisitionRequest:
        key = tuple(sorted(paths))
        existing = self._requests_by_key.get(key)
        if existing is not None:
            return existing

        requested_at = self.clock.now()
        request = AcquisitionRequest(
            paths=key,
            max_age=max_age,
            timeout=timeout,
            reason=reason,
            requested_at=requested_at,
            due_at=requested_at + max_age,
        )
        self.requests.append(request)
        self._requests_by_key[key] = request
        return request

    def due_requests(self) -> list[AcquisitionRequest]:
        now = self.clock.now()
        return [request for request in self.requests if request.due_at <= now]


class FakePendingOverlayStore:
    """Scoped pending overlay fake for command-service and rigctld tests."""

    def __init__(self, *, clock: FakeClock | None = None) -> None:
        self.clock = clock or FakeClock()
        self.overlays: list[PendingOverlay] = []

    def put(
        self,
        *,
        source: str,
        session_id: str | None,
        command_id: str,
        path: FieldPath,
        value: Any,
        ttl: float,
    ) -> PendingOverlay:
        overlay = PendingOverlay(
            source=source,
            session_id=session_id,
            command_id=command_id,
            path=path,
            value=value,
            expires_at=self.clock.now() + ttl,
        )
        self.overlays.append(overlay)
        return overlay

    def visible_value(
        self,
        *,
        source: str,
        session_id: str | None,
        command_id: str,
        path: FieldPath,
    ) -> Any | None:
        now = self.clock.now()
        for overlay in reversed(self.overlays):
            if overlay.expires_at <= now:
                continue
            if (
                overlay.source == source
                and overlay.session_id == session_id
                and overlay.command_id == command_id
                and overlay.path == path
            ):
                return overlay.value
        return None

    def confirm(
        self,
        *,
        source: str,
        session_id: str | None,
        command_id: str,
        path: FieldPath,
        value: Any,
    ) -> list[PendingOverlay]:
        matched = [
            overlay
            for overlay in self.overlays
            if (
                overlay.source == source
                and overlay.session_id == session_id
                and overlay.command_id == command_id
                and overlay.path == path
                and overlay.value == value
            )
        ]
        if matched:
            self.overlays = [
                overlay
                for overlay in self.overlays
                if overlay not in matched
            ]
        return matched

    def expire_due(self) -> list[PendingOverlay]:
        now = self.clock.now()
        expired = [overlay for overlay in self.overlays if overlay.expires_at <= now]
        if expired:
            self.overlays = [
                overlay for overlay in self.overlays if overlay.expires_at > now
            ]
        return expired


class FakeStateBackend:
    """Base fake backend that emits observations for state pipeline tests."""

    default_source: ObservationSource = "poll_response"
    capabilities: frozenset[str] = frozenset()

    def __init__(self, *, clock: FakeClock | None = None) -> None:
        self.clock = clock or FakeClock()
        self.emitted: list[Observation] = []
        self.dropped: list[Observation] = []
        self.command_log: list[str] = []
        self._drop_next: set[FieldPath] = set()

    def observation(
        self,
        path: FieldPath,
        value: Any,
        *,
        source: ObservationSource | None = None,
        correlation_id: str | None = None,
        max_age: float | None = None,
        quality: tuple[str, ...] = ("confirmed",),
    ) -> Observation:
        observation = Observation(
            path=path,
            value=value,
            source=source or self.default_source,
            timestamp_monotonic=self.clock.now(),
            quality=quality,
            correlation_id=correlation_id,
            max_age=max_age,
        )
        self.emitted.append(observation)
        return observation

    def unsolicited(
        self,
        path: FieldPath,
        value: Any,
        *,
        max_age: float | None = None,
    ) -> Observation | None:
        observation = self.observation(
            path,
            value,
            source="civ_unsolicited",
            max_age=max_age,
        )
        if path in self._drop_next:
            self._drop_next.remove(path)
            self.dropped.append(observation)
            return None
        return observation

    def command_response(
        self,
        path: FieldPath,
        value: Any,
        *,
        correlation_id: str | None = None,
        max_age: float | None = None,
    ) -> Observation:
        if correlation_id is not None:
            self.command_log.append(correlation_id)
        return self.observation(
            path,
            value,
            source="command_response",
            correlation_id=correlation_id,
            max_age=max_age,
        )

    def poll_response(self, path: FieldPath, value: Any) -> Observation:
        return self.observation(path, value, source="poll_response")

    def meter_sample(self, path: FieldPath, value: Any) -> Observation:
        return self.observation(path, value, source=self.default_source)

    def drop_next_unsolicited(self, path: FieldPath) -> None:
        self._drop_next.add(path)


class FakeCivPushBackend(FakeStateBackend):
    """Icom-like fake backend with CI-V push support."""

    default_source = "civ_unsolicited"
    capabilities = frozenset({"civ_push", "command_response", "meters"})


class FakeCommandResponseBackend(FakeStateBackend):
    """Fake backend that reports confirmed state through command responses."""

    default_source = "command_response"
    capabilities = frozenset({"command_response"})


class FakeDroppedUnsolicitedBackend(FakeCivPushBackend):
    """CI-V push fake that can deterministically drop selected push samples."""

    capabilities = frozenset({"civ_push", "drops_unsolicited", "poll_response"})


class FakePollingOnlyBackend(FakeStateBackend):
    """Backend fake for radios that never emit unsolicited state."""

    default_source = "state_poller"
    capabilities = frozenset({"polling_only"})

    def poll_response(self, path: FieldPath, value: Any) -> Observation:
        return self.observation(path, value, source="state_poller")


class FakeYaesuLikeBackend(FakePollingOnlyBackend):
    """Yaesu-like request/response fake for CAT poller tests."""

    capabilities = frozenset({"yaesu_cat", "polling_only", "command_response"})

    def poll_response(self, path: FieldPath, value: Any) -> Observation:
        return self.observation(path, value, source="yaesu_poll_response")


class FakeExternalRigctldClientBackend(FakeStateBackend):
    """Fake external Hamlib rigctld-client backend response source."""

    default_source = "hamlib_response"
    capabilities = frozenset({"external_rigctld_client", "poll_response"})

    def poll_response(self, path: FieldPath, value: Any) -> Observation:
        return self.observation(path, value, source="hamlib_response")


def assert_revision_counters(
    pipeline: FakeStatePipeline,
    *,
    state_revision: int,
    freshness_revision: int,
    observation_seq: int,
) -> None:
    assert pipeline.state_revision == state_revision
    assert pipeline.freshness_revision == freshness_revision
    assert pipeline.observation_seq == observation_seq


def assert_consumer_delta(
    changeset: ChangeSet,
    *,
    path: FieldPath,
    previous: Any,
    current: Any,
) -> None:
    assert changeset.changes == (
        FieldChange(path=path, previous=previous, current=current),
    )


def assert_no_consumer_delta(changeset: ChangeSet | None) -> None:
    assert changeset is None
