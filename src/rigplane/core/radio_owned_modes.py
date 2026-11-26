"""Shared, profile-declared operating contexts and conditional refusal hints."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
import time
from typing import Any, Literal, cast
from uuid import uuid4

from .state_pipeline_contracts import (
    CommandSource,
    FieldPath,
    Observation,
    SourceMetadata,
)
from .state_store import FreshnessState, StateSnapshot, StateStore

MODE_REFUSAL_MESSAGE = (
    "The radio refused the mode change. Check the operating mode on the radio."
)
REFUSAL_MAX_AGE = 30.0


@dataclass(frozen=True, slots=True)
class ModeWriteCorrelation:
    command_id: str
    source: CommandSource | None = None
    session_id: str | None = None


mode_write_correlation: ContextVar[ModeWriteCorrelation | None] = ContextVar(
    "mode_write_correlation", default=None
)
ModeActivity = Literal["active", "inactive", "unknown"]
ModeCertainty = Literal["certain", "probable"]


@dataclass(frozen=True, slots=True)
class OperatingRestriction:
    operation: str
    receiver: str
    certainty: str = "suspected"


@dataclass(frozen=True, slots=True)
class RadioOwnedModeDefinition:
    id: str
    label: str
    scope: str
    restrictions: tuple[OperatingRestriction, ...] = ()
    enter_capability: str = "unavailable"
    leave_capability: str = "unavailable"
    enter_instruction: str = ""
    enter_provenance: str = ""
    leave_instruction: str = ""
    leave_provenance: str = ""
    refusal_hint: str = ""
    translation_key: str = ""
    evidence_refs: tuple[str, ...] = ()

    @property
    def path(self) -> FieldPath:
        name = f"radio_owned_mode_{self.id}"
        if self.scope == "global":
            return FieldPath.global_("operator_controls", name)
        return FieldPath.receiver(self.scope, "operator_controls", name)

    def applies_to(self, receiver: str) -> bool:
        return self.scope in ("global", receiver) and any(
            item.operation == "mode_write" and item.receiver in ("any", receiver)
            for item in self.restrictions
        )


@dataclass(frozen=True, slots=True)
class OperatingModeEvidence:
    kind: str
    command_id: str
    target: str
    command_source: CommandSource | None = None
    session_id: str | None = None
    observed_at_monotonic: float | None = None
    provider_generation: int | None = None


@dataclass(frozen=True, slots=True)
class RadioOwnedModeState:
    id: str
    label: str
    scope: str
    activity: ModeActivity = "unknown"
    certainty: ModeCertainty | None = None
    restrictions: tuple[OperatingRestriction, ...] = ()
    enter_capability: str = "unavailable"
    leave_capability: str = "unavailable"
    enter_instruction: str = ""
    enter_provenance: str = ""
    leave_instruction: str = ""
    leave_provenance: str = ""
    evidence_refs: tuple[str, ...] = ()
    evidence: tuple[OperatingModeEvidence, ...] = ()
    message: str | None = None
    translation_key: str = ""
    observation: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RadioOwnedModeState:
        """Ignore future members; unknown activity/certainty loses attribution."""
        activity = value.get("activity", "unknown")
        certainty = value.get("certainty")
        if activity not in ("active", "inactive", "unknown"):
            activity = "unknown"
        if certainty not in ("certain", "probable") or activity != "active":
            certainty = None
        if activity == "active" and certainty is None:
            activity = "unknown"
        refs = value.get("evidence_refs")
        if not isinstance(refs, (tuple, list)):
            refs = ()
        return cls(
            id=str(value.get("id", "unknown")),
            label=str(value.get("label", value.get("id", "unknown"))),
            scope=str(value.get("scope", "global")),
            activity=cast(ModeActivity, activity),
            certainty=cast(ModeCertainty | None, certainty),
            restrictions=tuple(
                OperatingRestriction(
                    operation=str(item.get("operation", "unknown")),
                    receiver=str(item.get("receiver", "unknown")),
                    certainty=str(item.get("certainty", "unknown")),
                )
                for item in _records(value.get("restrictions"))
            ),
            enter_capability=str(value.get("enter_capability", "unavailable")),
            leave_capability=str(value.get("leave_capability", "unavailable")),
            enter_instruction=str(value.get("enter_instruction", "")),
            enter_provenance=str(value.get("enter_provenance", "")),
            leave_instruction=str(value.get("leave_instruction", "")),
            leave_provenance=str(value.get("leave_provenance", "")),
            evidence_refs=tuple(item for item in refs if isinstance(item, str)),
            evidence=tuple(
                OperatingModeEvidence(
                    kind=str(item.get("kind", "unknown")),
                    command_id=str(item.get("command_id", "")),
                    target=str(item.get("target", "")),
                    command_source=item.get("command_source"),
                    session_id=item.get("session_id"),
                    observed_at_monotonic=item.get("observed_at_monotonic"),
                    provider_generation=item.get("provider_generation"),
                )
                for item in _records(value.get("evidence"))
            ),
            message=value.get("message")
            if isinstance(value.get("message"), str)
            else None,
            translation_key=str(value.get("translation_key", "")),
            observation=value.get("observation")
            if isinstance(value.get("observation"), Mapping)
            else None,
        )


def _records(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def mode_refusal_message(
    definitions: Sequence[RadioOwnedModeDefinition], receiver: str
) -> str:
    parts = [MODE_REFUSAL_MESSAGE]
    for definition in definitions:
        if definition.applies_to(receiver) and definition.refusal_hint:
            parts.extend((definition.refusal_hint, definition.leave_instruction))
    return " ".join(part for part in parts if part)


def parse_radio_owned_modes(raw: Any) -> tuple[RadioOwnedModeDefinition, ...]:
    """Load panel-only declarations; no detector or actuator is enabled here."""
    if not isinstance(raw, list):
        raise ValueError("radio_owned_modes must be an array of tables")
    result: list[RadioOwnedModeDefinition] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("radio_owned_modes entries must be tables")
        required = ("id", "label", "scope")
        text_fields = (
            *required,
            "enter_instruction",
            "enter_provenance",
            "leave_instruction",
            "leave_provenance",
            "refusal_hint",
            "translation_key",
        )
        for key in text_fields:
            if key in required and key not in item:
                raise ValueError(f"radio_owned_modes requires {key}")
            if key in item and not isinstance(item[key], str):
                raise ValueError(f"radio_owned_modes.{key} must be text")
        restrictions = item.get("restrictions", [])
        if not isinstance(restrictions, list) or any(
            not isinstance(r, dict)
            or set(r) != {"operation", "receiver", "certainty"}
            or any(not isinstance(v, str) for v in r.values())
            for r in restrictions
        ):
            raise ValueError(
                "radio_owned_modes.restrictions must declare operation, receiver, certainty"
            )
        refs = item.get("evidence_refs", [])
        if not isinstance(refs, list) or any(not isinstance(v, str) for v in refs):
            raise ValueError("radio_owned_modes.evidence_refs must be text array")
        definition = RadioOwnedModeDefinition(
            **{k: item[k] for k in text_fields if k in item},
            restrictions=tuple(OperatingRestriction(**r) for r in restrictions),
            evidence_refs=tuple(refs),
        )
        definition.path  # Validate canonical scope/id tokens.
        FieldPath.global_("operator_controls", definition.id)
        if any(d.id == definition.id and d.scope == definition.scope for d in result):
            raise ValueError("duplicate radio_owned_modes id and scope")
        result.append(definition)
    return tuple(result)


def _unknown(definition: RadioOwnedModeDefinition) -> RadioOwnedModeState:
    values = asdict(definition)
    values.pop("refusal_hint")
    values["restrictions"] = definition.restrictions
    return RadioOwnedModeState(**values)


def project_radio_owned_modes(
    definitions: Sequence[RadioOwnedModeDefinition],
    snapshot: StateSnapshot,
) -> tuple[RadioOwnedModeState, ...]:
    result: list[RadioOwnedModeState] = []
    for definition in definitions:
        state = _unknown(definition)
        try:
            field = snapshot.field(definition.path)
        except KeyError:
            result.append(state)
            continue
        if (
            field.provider_generation == snapshot.provider_generation
            and field.freshness is FreshnessState.FRESH
            and (
                field.max_age is None
                or snapshot.generated_at_monotonic - field.last_observed_monotonic
                <= field.max_age
            )
            and isinstance(field.value, Mapping)
        ):
            observed = RadioOwnedModeState.from_dict(field.value)
            if observed.id == definition.id and observed.scope == definition.scope:
                evidence = tuple(
                    item
                    for item in observed.evidence
                    if item.kind != "write_refusal"
                    or (
                        isinstance(item.observed_at_monotonic, (float, int))
                        and 0
                        <= snapshot.generated_at_monotonic - item.observed_at_monotonic
                        <= REFUSAL_MAX_AGE
                        and item.provider_generation == snapshot.provider_generation
                    )
                )
                state = replace(
                    state,
                    activity=observed.activity,
                    certainty=observed.certainty,
                    evidence=evidence,
                    message=observed.message
                    if evidence or "conditional_hint" not in field.quality
                    else None,
                    observation={
                        "source": field.source.to_dict(),
                        "observed_at_monotonic": field.last_observed_monotonic,
                        "max_age": field.max_age,
                        "quality": field.quality,
                        "provider_generation": field.provider_generation,
                    },
                )
        result.append(state)
    return tuple(result)


def record_mode_write_result(
    definitions: Sequence[RadioOwnedModeDefinition],
    store: StateStore,
    *,
    receiver: str,
    command_id: str,
    refused: bool,
    now: float,
    generation: int,
    command_source: CommandSource | None = None,
    session_id: str | None = None,
) -> str:
    """Record one actual SET outcome without inferring physical activity."""
    target = FieldPath.active(receiver, "freq_mode", "mode")
    for definition in definitions:
        if not definition.applies_to(receiver):
            continue
        state = _unknown(definition)
        previous = project_radio_owned_modes((definition,), store.snapshot())[0]
        # A SET outcome does not refresh an explicit context observation.
        if previous.activity != "unknown":
            continue
        other_evidence = tuple(e for e in previous.evidence if e.target != str(target))
        if not refused:
            if previous.evidence == other_evidence:
                continue
            state = replace(
                state,
                evidence=other_evidence,
                message=previous.message if other_evidence else None,
            )
        if refused:
            hint = definition.refusal_hint
            state = replace(
                state,
                evidence=(
                    OperatingModeEvidence(
                        "write_refusal",
                        command_id,
                        str(target),
                        command_source,
                        session_id,
                        now,
                        generation,
                    ),
                ),
                message=" ".join(
                    part
                    for part in (
                        MODE_REFUSAL_MESSAGE,
                        hint,
                        definition.leave_instruction,
                    )
                    if part
                ),
            )
        store.apply(
            Observation(
                path=definition.path,
                value=state.to_dict(),
                source=SourceMetadata(
                    source="command_response",
                    provider="radio_owned_modes",
                    native_id=str(target),
                    command_source=command_source,
                    session_id=session_id,
                ),
                timestamp_monotonic=now,
                quality=("conditional_hint",),
                correlation_id=command_id,
                max_age=REFUSAL_MAX_AGE,
                provider_generation=generation,
            )
        )
    return mode_refusal_message(definitions, receiver)


@dataclass(frozen=True, slots=True)
class ModeWriteAttempt:
    """One backend SET, fenced to the state provider present at dispatch."""

    definitions: tuple[RadioOwnedModeDefinition, ...]
    store: StateStore | None
    receiver: str
    command_id: str
    generation: int
    command_source: CommandSource | None = None
    session_id: str | None = None

    @classmethod
    def begin(
        cls,
        definitions: tuple[RadioOwnedModeDefinition, ...],
        store: Any,
        receiver: int,
    ) -> ModeWriteAttempt:
        store = store if isinstance(store, StateStore) else None
        correlation = mode_write_correlation.get() or ModeWriteCorrelation(
            uuid4().hex, "public_api"
        )
        return cls(
            definitions,
            store,
            "main" if receiver == 0 else "sub",
            correlation.command_id,
            0 if store is None else store.provider_generation,
            correlation.source,
            correlation.session_id,
        )

    def finish(self, *, refused: bool, current: bool = True) -> str:
        store = self.store
        if store is None or not current:
            return mode_refusal_message(self.definitions, self.receiver)
        return record_mode_write_result(
            self.definitions,
            store,
            receiver=self.receiver,
            command_id=self.command_id,
            generation=self.generation,
            refused=refused,
            now=time.monotonic(),
            command_source=self.command_source,
            session_id=self.session_id,
        )
