"""Backend-neutral command execution service.

The service normalizes command ingress into :class:`CommandIntent`, delegates
actual radio work to an injected executor, and applies any resulting readbacks
as confirmed :class:`Observation` values through :class:`StateStore`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from rigplane.core.state_pipeline_contracts import (
    ChangeSet,
    CommandIntent,
    CommandLifecycleEvent,
    CommandLifecycleState,
    CommandSource,
    FieldPath,
    Observation,
)
from rigplane.core.state_store import StateStore

__all__ = [
    "CommandExecutionResult",
    "CommandExecutor",
    "CommandService",
    "CommandServiceResult",
    "PendingOverlay",
]


Clock = Callable[[], float]
LifecycleSubscriber = Callable[[CommandLifecycleEvent], None]


@dataclass(frozen=True, slots=True)
class CommandExecutionResult:
    """Backend executor result returned after command queue/backend execution."""

    observations: tuple[Observation, ...] = ()
    details: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(
            self,
            "details",
            {} if self.details is None else dict(self.details),
        )


class CommandExecutor(Protocol):
    """Backend-neutral command executor seam.

    Runtime, Web, rigctld, and public API adapters can implement this protocol
    by delegating to the existing command queue, IcomCommander, or backend API.
    """

    async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
        """Execute one command intent without directly mutating semantic state."""


@dataclass(frozen=True, slots=True)
class PendingOverlay:
    """Read-your-writes projection scoped to one command ingress context."""

    source: CommandSource
    session_id: str | None
    command_id: str
    path: FieldPath
    value: Any
    expires_at_monotonic: float

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at_monotonic


@dataclass(frozen=True, slots=True)
class CommandServiceResult:
    """Observable result of one command service execution."""

    lifecycle_events: tuple[CommandLifecycleEvent, ...]
    observation_changes: tuple[ChangeSet, ...]
    executor_result: CommandExecutionResult


class CommandService:
    """Coordinate backend-neutral command execution and pending overlays."""

    __slots__ = (
        "_clock",
        "_default_pending_ttl",
        "_events",
        "_executor",
        "_overlays",
        "_state_store",
        "_subscribers",
    )

    def __init__(
        self,
        *,
        executor: CommandExecutor,
        state_store: StateStore,
        clock: Clock | None = None,
        default_pending_ttl: float = 2.0,
    ) -> None:
        if default_pending_ttl < 0:
            raise ValueError("default_pending_ttl must be non-negative")
        self._executor = executor
        self._state_store = state_store
        self._clock = clock or time.monotonic
        self._default_pending_ttl = default_pending_ttl
        self._events: list[CommandLifecycleEvent] = []
        self._subscribers: list[LifecycleSubscriber] = []
        self._overlays: list[PendingOverlay] = []

    async def execute(self, intent: CommandIntent) -> CommandServiceResult:
        """Execute an intent through the injected backend executor."""

        start = len(self._events)
        self.emit_lifecycle(intent, "accepted")
        self._record_intent_overlay(intent)
        self.emit_lifecycle(intent, "queued")
        self.emit_lifecycle(intent, "sent")

        try:
            executor_result = await self._executor.execute(intent)
        except TimeoutError as exc:
            self.expire_command(intent.id)
            self.emit_lifecycle(intent, "timed_out", message=str(exc) or None)
            raise
        except Exception as exc:
            self.expire_command(intent.id)
            self.emit_lifecycle(intent, "failed", message=str(exc) or None)
            raise

        self.emit_lifecycle(intent, "acknowledged", details=executor_result.details)
        changes: list[ChangeSet] = []
        for observation in executor_result.observations:
            changes.append(self.apply_observation(observation))

        return CommandServiceResult(
            lifecycle_events=tuple(self._events[start:]),
            observation_changes=tuple(changes),
            executor_result=executor_result,
        )

    def apply_observation(self, observation: Observation) -> ChangeSet:
        """Apply a confirmed observation and reconcile matching overlays."""

        changeset = self._state_store.apply(observation)
        self._reconcile_observation(observation, changeset)
        return changeset

    def emit_lifecycle(
        self,
        intent: CommandIntent,
        state: CommandLifecycleState,
        *,
        message: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> CommandLifecycleEvent:
        """Record and publish a lifecycle event for an intent."""

        event = CommandLifecycleEvent(
            command_id=intent.id,
            state=state,
            timestamp_monotonic=self._clock(),
            source=intent.source,
            target=intent.target,
            message=message,
            details=details,
        )
        self._events.append(event)
        for subscriber in tuple(self._subscribers):
            subscriber(event)
        return event

    def lifecycle_events(self) -> tuple[CommandLifecycleEvent, ...]:
        """Return recorded lifecycle events in emission order."""

        return tuple(self._events)

    def subscribe_lifecycle(
        self,
        subscriber: LifecycleSubscriber,
    ) -> Callable[[], None]:
        """Subscribe to lifecycle events and return an unsubscribe callback."""

        self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            try:
                self._subscribers.remove(subscriber)
            except ValueError:
                pass

        return unsubscribe

    def record_pending_overlay(self, overlay: PendingOverlay) -> None:
        """Record or replace one scoped pending overlay."""

        self._purge_expired()
        self._overlays = [
            item
            for item in self._overlays
            if not (
                item.source == overlay.source
                and item.session_id == overlay.session_id
                and item.command_id == overlay.command_id
                and item.path == overlay.path
            )
        ]
        if not overlay.is_expired(self._clock()):
            self._overlays.append(overlay)

    def pending_overlays(
        self,
        *,
        source: CommandSource,
        session_id: str | None,
        command_id: str | None = None,
        path: FieldPath | None = None,
    ) -> tuple[PendingOverlay, ...]:
        """Return non-expired overlays matching an ingress scope."""

        self._purge_expired()
        return tuple(
            item
            for item in self._overlays
            if item.source == source
            and item.session_id == session_id
            and (command_id is None or item.command_id == command_id)
            and (path is None or item.path == path)
        )

    def project_pending_values(
        self,
        *,
        source: CommandSource,
        session_id: str | None,
        paths: Sequence[FieldPath],
    ) -> dict[FieldPath, Any]:
        """Project pending values for one source/session without leakage."""

        wanted = set(paths)
        projected: dict[FieldPath, Any] = {}
        for overlay in self.pending_overlays(source=source, session_id=session_id):
            if overlay.path in wanted:
                projected[overlay.path] = overlay.value
        return projected

    def expire_command(self, command_id: str) -> None:
        """Remove all pending overlays created by one command."""

        self._overlays = [
            item for item in self._overlays if item.command_id != command_id
        ]

    def _record_intent_overlay(self, intent: CommandIntent) -> None:
        if intent.pending_policy != "scoped" or intent.target is None:
            return
        try:
            value = _pending_value_for_intent(intent)
        except KeyError:
            return
        timeout = (
            self._default_pending_ttl if intent.timeout is None else intent.timeout
        )
        self.record_pending_overlay(
            PendingOverlay(
                source=intent.source,
                session_id=_session_id(intent),
                command_id=intent.id,
                path=intent.target,
                value=value,
                expires_at_monotonic=self._clock() + timeout,
            )
        )

    def _reconcile_observation(
        self,
        observation: Observation,
        changeset: ChangeSet,
    ) -> None:
        self._purge_expired()
        reconciled: list[PendingOverlay] = []
        remaining: list[PendingOverlay] = []
        for overlay in self._overlays:
            if overlay.path == observation.path and overlay.value == observation.value:
                reconciled.append(overlay)
            else:
                remaining.append(overlay)
        self._overlays = remaining

        for overlay in reconciled:
            self.emit_lifecycle(
                CommandIntent(
                    id=overlay.command_id,
                    name="reconcile",
                    params={},
                    source=overlay.source,
                    target=overlay.path,
                ),
                "reconciled",
                message="confirmed by matching observation",
                details={
                    "revision": changeset.revision,
                    "observationSeq": changeset.observation_seq,
                },
            )

    def _purge_expired(self) -> None:
        now = self._clock()
        self._overlays = [
            overlay for overlay in self._overlays if not overlay.is_expired(now)
        ]


def _pending_value_for_intent(intent: CommandIntent) -> Any:
    assert intent.target is not None
    params = intent.params
    if intent.target.name in params:
        return params[intent.target.name]
    if "value" in params:
        return params["value"]
    raise KeyError(intent.target.name)


def _session_id(intent: CommandIntent) -> str | None:
    value = intent.params.get("session_id")
    return None if value is None else str(value)
