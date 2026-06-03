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

from rigplane.core.exceptions import TimeoutError as RigplaneTimeoutError
from rigplane.core.state_pipeline_contracts import (
    ChangeSet,
    CommandIntent,
    CommandLifecycleEvent,
    CommandLifecycleState,
    CommandSource,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore

__all__ = [
    "CommandExecutionResult",
    "CommandExecutor",
    "CommandService",
    "CommandServiceResult",
    "PendingOverlay",
    "command_intent_from_request",
    "command_response_observation",
]

_UNSET = object()


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
        except (TimeoutError, RigplaneTimeoutError) as exc:
            self.expire_command(
                intent.id,
                source=intent.source,
                session_id=_session_id(intent),
            )
            self.emit_lifecycle(intent, "timed_out", message=str(exc) or None)
            raise
        except Exception as exc:
            self.expire_command(
                intent.id,
                source=intent.source,
                session_id=_session_id(intent),
            )
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

    def fail_command(
        self,
        command_id: str,
        *,
        message: str | None = None,
        timed_out: bool = False,
        source: CommandSource | None = None,
        session_id: str | None | object = _UNSET,
    ) -> bool:
        """Mark a previously acknowledged command as failed and expire overlays."""

        template = self._last_event(
            command_id,
            source=source,
            session_id=session_id,
        )
        if template is None or template.state in {
            "failed",
            "timed_out",
            "reconciled",
            "confirmed",
            "superseded",
        }:
            return False
        scoped_source = template.source if source is None else source
        scoped_session = (
            _event_session_id(template) if session_id is _UNSET else session_id
        )
        self.expire_command(
            command_id,
            source=scoped_source,
            session_id=scoped_session,
        )
        params = {}
        if scoped_session is not _UNSET:
            params["session_id"] = scoped_session
        self.emit_lifecycle(
            CommandIntent(
                id=command_id,
                name="queued_completion",
                params=params,
                source=scoped_source,
                target=template.target,
                priority="user",
                timeout=None,
                pending_policy="none",
                expected_observations=(),
            ),
            "timed_out" if timed_out else "failed",
            message=message,
        )
        return True

    def emit_lifecycle(
        self,
        intent: CommandIntent,
        state: CommandLifecycleState,
        *,
        message: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> CommandLifecycleEvent:
        """Record and publish a lifecycle event for an intent."""

        payload_details = dict(details or {})
        if "session_id" in intent.params:
            payload_details.setdefault("session_id", intent.params["session_id"])
        event = CommandLifecycleEvent(
            command_id=intent.id,
            state=state,
            timestamp_monotonic=self._clock(),
            source=intent.source,
            target=intent.target,
            message=message,
            details=payload_details,
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

    def expire_command(
        self,
        command_id: str,
        *,
        source: CommandSource | None = None,
        session_id: str | None | object = _UNSET,
    ) -> None:
        """Remove all pending overlays created by one command."""

        self._overlays = [
            item
            for item in self._overlays
            if not _overlay_matches(
                item,
                command_id=command_id,
                source=source,
                session_id=session_id,
            )
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
            if _observation_reconciles_overlay(observation, overlay):
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

    def _last_event(
        self,
        command_id: str,
        *,
        source: CommandSource | None = None,
        session_id: str | None | object = _UNSET,
    ) -> CommandLifecycleEvent | None:
        for event in reversed(self._events):
            if (
                event.command_id == command_id
                and (source is None or event.source == source)
                and (session_id is _UNSET or _event_session_id(event) == session_id)
            ):
                return event
        return None


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


def _event_session_id(event: CommandLifecycleEvent) -> str | None:
    value = (event.details or {}).get("session_id")
    return None if value is None else str(value)


def _overlay_matches(
    overlay: PendingOverlay,
    *,
    command_id: str,
    source: CommandSource | None,
    session_id: str | None | object,
) -> bool:
    return (
        overlay.command_id == command_id
        and (source is None or overlay.source == source)
        and (session_id is _UNSET or overlay.session_id == session_id)
    )


def _observation_reconciles_overlay(
    observation: Observation,
    overlay: PendingOverlay,
) -> bool:
    observed_source = observation.source.command_source
    if observed_source is not None and observed_source != overlay.source:
        return False
    observed_session = observation.source.session_id
    if observed_session is not None and observed_session != overlay.session_id:
        return False
    return (
        observation.correlation_id is not None
        and observation.correlation_id == overlay.command_id
        and overlay.path == observation.path
        and overlay.value == observation.value
    )


def command_intent_from_request(
    name: str,
    params: Mapping[str, Any],
    *,
    source: CommandSource,
    command_id: str | None = None,
    session_id: str | None = None,
    timeout: float | None = 2.0,
) -> CommandIntent:
    """Normalize a production command request into a backend-neutral intent."""

    normalized = dict(params)
    if session_id is not None:
        normalized["session_id"] = session_id
    command_name = str(name)
    if command_name == "set_freq":
        raw_freq = (
            normalized["freq_hz"] if "freq_hz" in normalized else normalized["freq"]
        )
        freq = int(raw_freq)
        normalized["freq_hz"] = freq
        normalized.setdefault("freq", freq)
    elif command_name == "set_mode":
        normalized["mode"] = str(normalized["mode"])
    elif command_name == "set_filter":
        if "filter_num" not in normalized:
            raw_filter = normalized.get("filter", normalized.get("value", 1))
            if isinstance(raw_filter, str):
                normalized["filter_num"] = (
                    int(raw_filter[-1]) if raw_filter[-1:].isdigit() else 1
                )
            else:
                normalized["filter_num"] = int(raw_filter)
        normalized["filter_width"] = int(normalized["filter_num"])
    elif command_name == "set_filter_width":
        normalized["filter_width"] = int(normalized["width"])
    elif command_name in ("set_ptt", "ptt"):
        normalized["ptt"] = _ptt_value(command_name, normalized)
    elif command_name == "ptt_on":
        normalized["ptt"] = True
    elif command_name == "ptt_off":
        normalized["ptt"] = False
    elif command_name == "set_rf_gain":
        normalized["rf_gain"] = int(normalized["level"])
    elif command_name == "set_af_level":
        normalized["af_level"] = int(normalized["level"])
    elif command_name in ("set_sql", "set_squelch"):
        normalized["squelch"] = int(normalized["level"])
    elif command_name in ("set_att", "set_attenuator", "set_attenuator_level"):
        raw_value = (
            normalized["db"]
            if "db" in normalized
            else normalized["level"]
            if "level" in normalized
            else normalized["value"]
        )
        normalized["att"] = int(raw_value)
    elif command_name == "set_preamp":
        raw_value = (
            normalized["level"] if "level" in normalized else normalized["value"]
        )
        normalized["preamp"] = int(raw_value)
    elif command_name == "set_nb":
        normalized["nb"] = bool(normalized["on"])
    elif command_name == "set_nr":
        normalized["nr"] = bool(normalized["on"])
    elif command_name == "set_pbt_inner":
        raw_level = (
            normalized["value"] if "value" in normalized else normalized["level"]
        )
        normalized["pbt_inner"] = int(raw_level)
    elif command_name == "set_pbt_outer":
        raw_level = (
            normalized["value"] if "value" in normalized else normalized["level"]
        )
        normalized["pbt_outer"] = int(raw_level)
    elif command_name == "set_powerstat":
        normalized["power_on"] = bool(normalized.get("on", True))
    elif command_name in ("set_rf_power", "set_power"):
        raw_level = (
            normalized["level"] if "level" in normalized else normalized["value"]
        )
        normalized["power_level"] = int(raw_level)
    elif command_name == "set_split":
        normalized["split"] = bool(normalized.get("on", False))
    elif command_name in ("set_vfo", "select_vfo"):
        active_slot = _active_slot_value(normalized.get("vfo", "A"))
        if active_slot is not None:
            normalized["active_slot"] = active_slot
    elif command_name == "set_level":
        normalized["level"] = str(normalized["level"]).upper()
        normalized["value"] = float(normalized["value"])
        _normalize_level_value(normalized)
    elif command_name == "set_func":
        func = str(normalized["func"]).lower()
        normalized["func"] = func.upper()
        normalized[func] = bool(normalized["on"])
    elif command_name == "set_split_vfo":
        normalized["split"] = bool(normalized["on"])

    target = _command_target(command_name, normalized)
    expected = () if target is None else (target,)
    return CommandIntent(
        id=command_id or f"{source}-{time.monotonic_ns()}",
        name=command_name,
        params=normalized,
        source=source,
        target=target,
        priority="user",
        timeout=timeout,
        pending_policy="scoped" if target is not None else "none",
        expected_observations=expected,
    )


def command_response_observation(
    intent: CommandIntent,
    *,
    timestamp_monotonic: float,
    provider: str,
    transport: str | None = None,
    value: Any = None,
) -> Observation:
    """Create a confirmed command-response observation for an intent target."""

    if intent.target is None:
        raise ValueError(f"command {intent.name!r} has no observable target")
    observed_value = _value_for_observable_intent(intent) if value is None else value
    return Observation(
        path=intent.target,
        value=observed_value,
        source=SourceMetadata(
            source="command_response",
            provider=provider,
            transport=transport,
            command_source=intent.source,
            session_id=_session_id(intent),
        ),
        timestamp_monotonic=timestamp_monotonic,
        correlation_id=intent.id,
    )


def _command_target(name: str, params: Mapping[str, Any]) -> FieldPath | None:
    receiver = str(int(params.get("receiver", 0)))
    if name == "set_freq":
        return FieldPath.receiver(receiver, "freq_mode", "freq_hz")
    if name == "set_mode":
        return FieldPath.receiver(receiver, "freq_mode", "mode")
    if name == "set_filter":
        # Filter SELECTION (FIL1/2/3) updates ``filter_num`` — NOT ``filter_width``
        # (the DSP IF-width control). Targeting filter_width wrote the preset
        # number into the wrong field, so the optimistic update never moved the
        # filterNum a client reads back (MOR-419).
        return FieldPath.receiver(receiver, "freq_mode", "filter_num")
    if name == "set_filter_width":
        return FieldPath.receiver(receiver, "freq_mode", "filter_width")
    if name in ("set_ptt", "ptt", "ptt_on", "ptt_off"):
        return FieldPath.global_("tx_state", "ptt")
    if name == "set_rf_gain":
        return FieldPath.receiver(receiver, "operator_controls", "rf_gain")
    if name == "set_af_level":
        return FieldPath.receiver(receiver, "operator_controls", "af_level")
    if name in ("set_sql", "set_squelch"):
        return FieldPath.receiver(receiver, "operator_controls", "squelch")
    if name in ("set_att", "set_attenuator", "set_attenuator_level"):
        return FieldPath.receiver(receiver, "operator_controls", "att")
    if name == "set_preamp":
        return FieldPath.receiver(receiver, "operator_controls", "preamp")
    if name == "set_nb":
        return FieldPath.receiver(receiver, "operator_toggles", "nb")
    if name == "set_nr":
        return FieldPath.receiver(receiver, "operator_toggles", "nr")
    if name == "set_pbt_inner":
        return FieldPath.receiver(receiver, "operator_controls", "pbt_inner")
    if name == "set_pbt_outer":
        return FieldPath.receiver(receiver, "operator_controls", "pbt_outer")
    if name == "set_powerstat":
        return FieldPath.global_("tx_state", "power_on")
    if name in ("set_rf_power", "set_power"):
        return FieldPath.global_("operator_controls", "power_level")
    if name == "set_split":
        return FieldPath.global_("tx_state", "split")
    if name in ("set_vfo", "select_vfo") and "active_slot" in params:
        return FieldPath.active_slot(receiver)
    if name == "set_level":
        return _level_target(params, receiver)
    if name == "set_func":
        return FieldPath.receiver(
            receiver,
            "operator_toggles",
            str(params["func"]).lower(),
        )
    if name == "set_split_vfo":
        return FieldPath.global_("tx_state", "split")
    return None


def _value_for_observable_intent(intent: CommandIntent) -> Any:
    if intent.target is None:
        raise ValueError(f"command {intent.name!r} has no observable target")
    params = intent.params
    if intent.target.name in params:
        return params[intent.target.name]
    if intent.target.name == "freq_hz" and "freq" in params:
        return params["freq"]
    if "value" in params:
        return params["value"]
    raise KeyError(intent.target.name)


def _ptt_value(name: str, params: Mapping[str, Any]) -> bool:
    if name == "ptt" and "state" in params:
        return bool(params["state"])
    if "on" in params:
        return bool(params["on"])
    if "value" in params:
        return bool(params["value"])
    return False


def _active_slot_value(value: Any) -> str | None:
    text = str(value).strip().upper()
    if text in ("B", "VFOB", "SUB", "1"):
        return "B"
    if text in ("A", "VFOA", "MAIN", "0"):
        return "A"
    return None


def _normalize_level_value(params: dict[str, Any]) -> None:
    level = str(params["level"]).upper()
    value = float(params["value"])
    receiver_control_names = {
        "AF": "af_level",
        "RF": "rf_gain",
        "SQL": "squelch",
        "NR": "nr_level",
        "NB": "nb_level",
        "COMP": "compressor_level",
        "MICGAIN": "mic_gain",
        "MONITOR_GAIN": "monitor_gain",
        "KEYSPD": "key_speed",
        "CWPITCH": "cw_pitch",
        "PREAMP": "preamp",
        "ATT": "att",
    }
    if level == "RFPOWER":
        params["power_level"] = round(value * 255)
    elif level in {"AF", "RF", "SQL", "NR", "NB", "COMP", "MICGAIN", "MONITOR_GAIN"}:
        params[receiver_control_names[level]] = max(0, min(255, round(value * 255)))
    elif level in receiver_control_names:
        params[receiver_control_names[level]] = round(value)


def _level_target(params: Mapping[str, Any], receiver: str) -> FieldPath | None:
    level = str(params["level"]).upper()
    if level == "RFPOWER":
        return FieldPath.global_("operator_controls", "power_level")
    names = {
        "AF": "af_level",
        "RF": "rf_gain",
        "SQL": "squelch",
        "NR": "nr_level",
        "NB": "nb_level",
        "COMP": "compressor_level",
        "MICGAIN": "mic_gain",
        "MONITOR_GAIN": "monitor_gain",
        "KEYSPD": "key_speed",
        "CWPITCH": "cw_pitch",
        "PREAMP": "preamp",
        "ATT": "att",
    }
    name = names.get(level)
    if name is None:
        return None
    return FieldPath.receiver(receiver, "operator_controls", name)
