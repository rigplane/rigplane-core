"""Backend-neutral contracts for the radio state pipeline.

These contracts describe state paths, observations, changes, and command
events. They intentionally do not implement a production StateStore.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, cast, get_args

from rigplane.core.tx_target import tx_target_from_dict, validate_tx_target

__all__ = [
    "CapabilityMetadata",
    "ChangeSet",
    "LOCAL_MONOTONIC_CLOCK_DOMAIN",
    "CommandIntent",
    "CommandLifecycleEvent",
    "CommandLifecycleState",
    "CommandPriority",
    "CommandSource",
    "FieldChange",
    "FieldFamily",
    "FieldPath",
    "FieldRegistry",
    "FieldScope",
    "FieldSpec",
    "Observation",
    "ObservationSource",
    "PendingPolicy",
    "SourceMetadata",
    "VfoSlot",
]

LOCAL_MONOTONIC_CLOCK_DOMAIN = "local_monotonic"


ObservationSource = Literal[
    "civ_unsolicited",
    "command_response",
    "poll_response",
    "state_poller",
    "hamlib_response",
    "yaesu_poll_response",
    "local_reconcile",
    "test",
]
CommandSource = Literal[
    "websocket",
    "http",
    "rigctld",
    "public_api",
    "internal_policy",
    "diagnostics",
    "test",
]
CommandPriority = Literal["user", "normal", "background"]
PendingPolicy = Literal["none", "scoped", "global"]
CommandLifecycleState = Literal[
    "accepted",
    "queued",
    "sent",
    "acknowledged",
    "failed",
    "timed_out",
    "confirmed",
    "reconciled",
    "superseded",
]

_OBSERVATION_SOURCES: frozenset[str] = frozenset(get_args(ObservationSource))
_COMMAND_SOURCES: frozenset[str] = frozenset(get_args(CommandSource))
_COMMAND_PRIORITIES: frozenset[str] = frozenset(get_args(CommandPriority))
_PENDING_POLICIES: frozenset[str] = frozenset(get_args(PendingPolicy))
_COMMAND_STATES: frozenset[str] = frozenset(get_args(CommandLifecycleState))
_TX_TARGET_PATH = "global.tx_state.tx_target"


class FieldScope(StrEnum):
    """Top-level state path scope."""

    GLOBAL = "global"
    RECEIVER = "receiver"
    SCOPE_CONTROLS = "scope_controls"
    CONNECTION = "connection"
    HEALTH = "health"


class VfoSlot(StrEnum):
    """VFO slot dimension for receiver-scoped frequency/mode fields."""

    ACTIVE = "active"
    UNSELECTED = "unselected"
    A = "A"
    B = "B"


class FieldFamily(StrEnum):
    """Canonical family for a radio state field."""

    FREQ_MODE = "freq_mode"
    VFO = "vfo"
    OPERATOR_TOGGLES = "operator_toggles"
    OPERATOR_CONTROLS = "operator_controls"
    METERS = "meters"
    TX_STATE = "tx_state"
    SLOW_STATE = "slow_state"
    DISPLAY = "display"
    CONNECTION = "connection"
    HEALTH = "health"


_TOKEN_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


def _validate_token(value: str, *, label: str) -> str:
    if not value:
        raise ValueError(f"{label} must not be empty")
    if any(ch not in _TOKEN_ALPHABET for ch in value):
        raise ValueError(f"{label} must use lowercase snake-case tokens: {value!r}")
    return value


# Calibrated-domain unit vocabulary (MOR-464 Phase 1). ``v`` (volts) and ``a``
# (amps) are included for the supply-voltage / drain-current meters.
_FIELD_UNITS = frozenset({"hz", "centihz", "normalized", "db", "w", "ratio", "v", "a"})


def _validate_family(value: str) -> FieldFamily:
    _validate_token(value, label="family")
    try:
        return FieldFamily(value)
    except ValueError as exc:
        raise ValueError(f"unknown field family: {value!r}") from exc


def _validate_source(value: str, allowed: frozenset[str], *, label: str) -> str:
    if value not in allowed:
        raise ValueError(f"unknown {label}: {value!r}")
    return value


def _required(value: Mapping[str, Any], key: str) -> Any:
    if key not in value:
        raise KeyError(key)
    return value[key]


def _optional_bool(value: Mapping[str, Any], key: str, *, default: bool) -> bool:
    if key not in value:
        return default
    item = value[key]
    if not isinstance(item, bool):
        raise TypeError(f"{key} must be a boolean")
    return item


def _observation_source(value: Any) -> ObservationSource:
    text = str(value)
    _validate_source(text, _OBSERVATION_SOURCES, label="observation source")
    return cast(ObservationSource, text)


def _command_source(value: Any) -> CommandSource:
    text = str(value)
    _validate_source(text, _COMMAND_SOURCES, label="command source")
    return cast(CommandSource, text)


def _command_priority(value: Any) -> CommandPriority:
    text = str(value)
    _validate_source(text, _COMMAND_PRIORITIES, label="command priority")
    return cast(CommandPriority, text)


def _pending_policy(value: Any) -> PendingPolicy:
    text = str(value)
    _validate_source(text, _PENDING_POLICIES, label="pending policy")
    return cast(PendingPolicy, text)


def _command_state(value: Any) -> CommandLifecycleState:
    text = str(value)
    _validate_source(text, _COMMAND_STATES, label="command lifecycle state")
    return cast(CommandLifecycleState, text)


@dataclass(frozen=True, order=True, slots=True)
class FieldPath:
    """Stable, serializable path to one radio state field.

    Canonical string forms are:

    - ``receiver.<rx>.slot.<A|B>.<family>.<name>``
    - ``receiver.<rx>.active.<family>.<name>``
    - ``receiver.<rx>.<family>.<name>``
    - ``receiver.<rx>.vfo.active_slot``
    - ``global.<family>.<name>``
    - ``scope_controls.receiver.<rx>.<family>.<name>``
    - ``scope_controls.global.<family>.<name>``
    """

    scope: FieldScope
    family: FieldFamily
    name: str
    receiver_id: str | None = None
    slot: VfoSlot | None = None

    def __post_init__(self) -> None:
        scope = FieldScope(str(self.scope))
        family = _validate_family(str(self.family))
        name = _validate_token(self.name, label="name")
        receiver_id = (
            None
            if self.receiver_id is None
            else _validate_token(self.receiver_id, label="receiver_id")
        )
        slot = None if self.slot is None else VfoSlot(str(self.slot))

        if scope is FieldScope.RECEIVER and receiver_id is None:
            raise ValueError("receiver field paths require receiver_id")
        if scope in (FieldScope.GLOBAL, FieldScope.CONNECTION, FieldScope.HEALTH):
            if receiver_id is not None or slot is not None:
                raise ValueError(
                    f"{scope.value} field paths cannot include receiver or slot"
                )
        if scope is FieldScope.SCOPE_CONTROLS and slot is not None:
            raise ValueError("scope_controls field paths cannot include VFO slot")
        if scope is FieldScope.SCOPE_CONTROLS and family is not FieldFamily.DISPLAY:
            raise ValueError("scope_controls field paths must use display family")
        if (
            slot in (VfoSlot.ACTIVE, VfoSlot.UNSELECTED)
            and scope is not FieldScope.RECEIVER
        ):
            raise ValueError("relative VFO slot is only valid for receiver fields")
        if slot is not None and family is not FieldFamily.FREQ_MODE:
            raise ValueError("VFO slot paths are only valid for freq_mode fields")

        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "family", family)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "receiver_id", receiver_id)
        object.__setattr__(self, "slot", slot)

    @classmethod
    def parse(cls, value: str) -> FieldPath:
        parts = value.split(".")
        if len(parts) < 3:
            raise ValueError(f"field path is too short: {value!r}")

        scope = parts[0]
        if scope == FieldScope.RECEIVER.value:
            return cls._parse_receiver(parts, raw=value)
        if scope == FieldScope.GLOBAL.value:
            if len(parts) != 3:
                raise ValueError(f"global field path must have 3 parts: {value!r}")
            return cls.global_(parts[1], parts[2])
        if scope == FieldScope.SCOPE_CONTROLS.value:
            return cls._parse_scope_controls(parts, raw=value)
        if scope in (FieldScope.CONNECTION.value, FieldScope.HEALTH.value):
            if len(parts) != 3:
                raise ValueError(f"{scope} field path must have 3 parts: {value!r}")
            return cls(FieldScope(scope), _validate_family(parts[1]), parts[2])
        raise ValueError(f"unknown field path scope: {scope!r}")

    @classmethod
    def _parse_receiver(cls, parts: list[str], *, raw: str) -> FieldPath:
        if len(parts) < 4:
            raise ValueError(f"receiver field path is too short: {raw!r}")
        receiver = parts[1]
        marker = parts[2]
        if marker == "slot":
            if len(parts) != 6:
                raise ValueError(f"receiver slot path must have 6 parts: {raw!r}")
            return cls.vfo_slot(receiver, parts[3], parts[4], parts[5])
        if marker in (VfoSlot.ACTIVE.value, VfoSlot.UNSELECTED.value):
            if len(parts) != 5:
                raise ValueError(f"receiver relative path must have 5 parts: {raw!r}")
            builder = cls.active if marker == VfoSlot.ACTIVE.value else cls.unselected
            return builder(receiver, parts[3], parts[4])
        if len(parts) != 4:
            raise ValueError(f"receiver field path must have 4 parts: {raw!r}")
        return cls.receiver(receiver, marker, parts[3])

    @classmethod
    def _parse_scope_controls(cls, parts: list[str], *, raw: str) -> FieldPath:
        if parts[1] == "receiver":
            if len(parts) != 5:
                raise ValueError(
                    f"receiver scope_controls path must have 5 parts: {raw!r}"
                )
            return cls.scope_control(parts[3], parts[4], receiver_id=parts[2])
        if parts[1] == FieldScope.GLOBAL.value:
            if len(parts) != 4:
                raise ValueError(
                    f"global scope_controls path must have 4 parts: {raw!r}"
                )
            return cls.scope_control(parts[2], parts[3])
        raise ValueError(f"unknown scope_controls target: {parts[1]!r}")

    @classmethod
    def receiver(cls, receiver_id: str, family: str, name: str) -> FieldPath:
        return cls(
            scope=FieldScope.RECEIVER,
            receiver_id=receiver_id,
            slot=None,
            family=_validate_family(family),
            name=name,
        )

    @classmethod
    def vfo_slot(
        cls,
        receiver_id: str,
        slot: str,
        family: str,
        name: str,
    ) -> FieldPath:
        if slot not in {VfoSlot.A.value, VfoSlot.B.value}:
            raise ValueError(f"VFO slot must be A or B: {slot!r}")
        return cls(
            scope=FieldScope.RECEIVER,
            receiver_id=receiver_id,
            slot=VfoSlot(slot),
            family=_validate_family(family),
            name=name,
        )

    @classmethod
    def active(cls, receiver_id: str, family: str, name: str) -> FieldPath:
        return cls(
            scope=FieldScope.RECEIVER,
            receiver_id=receiver_id,
            slot=VfoSlot.ACTIVE,
            family=_validate_family(family),
            name=name,
        )

    @classmethod
    def unselected(cls, receiver_id: str, family: str, name: str) -> FieldPath:
        return cls(
            scope=FieldScope.RECEIVER,
            receiver_id=receiver_id,
            slot=VfoSlot.UNSELECTED,
            family=_validate_family(family),
            name=name,
        )

    @classmethod
    def active_slot(cls, receiver_id: str) -> FieldPath:
        return cls.receiver(receiver_id, FieldFamily.VFO.value, "active_slot")

    @classmethod
    def global_(cls, family: str, name: str) -> FieldPath:
        return cls(
            scope=FieldScope.GLOBAL,
            receiver_id=None,
            slot=None,
            family=_validate_family(family),
            name=name,
        )

    @classmethod
    def scope_control(
        cls,
        family: str,
        name: str,
        *,
        receiver_id: str | None = None,
    ) -> FieldPath:
        return cls(
            scope=FieldScope.SCOPE_CONTROLS,
            receiver_id=receiver_id,
            slot=None,
            family=_validate_family(family),
            name=name,
        )

    def __str__(self) -> str:
        if self.scope is FieldScope.RECEIVER:
            assert self.receiver_id is not None
            if self.slot in (VfoSlot.ACTIVE, VfoSlot.UNSELECTED):
                return ".".join(
                    [
                        self.scope.value,
                        self.receiver_id,
                        self.slot.value,
                        self.family.value,
                        self.name,
                    ]
                )
            if self.slot in (VfoSlot.A, VfoSlot.B):
                return ".".join(
                    [
                        self.scope.value,
                        self.receiver_id,
                        "slot",
                        self.slot.value,
                        self.family.value,
                        self.name,
                    ]
                )
            return ".".join(
                [self.scope.value, self.receiver_id, self.family.value, self.name]
            )
        if self.scope is FieldScope.SCOPE_CONTROLS:
            if self.receiver_id is None:
                return ".".join(
                    [
                        self.scope.value,
                        FieldScope.GLOBAL.value,
                        self.family.value,
                        self.name,
                    ]
                )
            return ".".join(
                [
                    self.scope.value,
                    "receiver",
                    self.receiver_id,
                    self.family.value,
                    self.name,
                ]
            )
        return ".".join([self.scope.value, self.family.value, self.name])

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self),
            "scope": self.scope.value,
            "receiverId": self.receiver_id,
            "slot": None if self.slot is None else self.slot.value,
            "family": self.family.value,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | str) -> FieldPath:
        if isinstance(value, str):
            return cls.parse(value)
        path = value.get("path")
        if isinstance(path, str):
            return cls.parse(path)
        return cls(
            scope=FieldScope(str(value["scope"])),
            receiver_id=(
                None if value.get("receiverId") is None else str(value["receiverId"])
            ),
            slot=None if value.get("slot") is None else VfoSlot(str(value["slot"])),
            family=_validate_family(str(value["family"])),
            name=str(value["name"]),
        )


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Registry metadata for one canonical field path."""

    path: FieldPath
    family: FieldFamily
    value_type: str
    readable: bool = True
    writable: bool = False
    unit: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if self.family is not self.path.family:
            raise ValueError(
                f"field spec family {self.family.value!r} does not match "
                f"path family {self.path.family.value!r}"
            )
        _validate_token(self.value_type, label="value_type")
        if self.unit is not None and self.unit not in _FIELD_UNITS:
            raise ValueError(f"unknown field unit: {self.unit!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "family": self.family.value,
            "valueType": self.value_type,
            "readable": self.readable,
            "writable": self.writable,
            "unit": self.unit,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class FieldRegistry:
    """Immutable registry for canonical field paths."""

    fields: tuple[FieldSpec, ...]

    def __post_init__(self) -> None:
        by_path: set[str] = set()
        ambiguity_keys: dict[
            tuple[FieldScope, str | None, VfoSlot | None, str], str
        ] = {}
        for spec in self.fields:
            serialized = str(spec.path)
            if serialized in by_path:
                raise ValueError(f"duplicate field path: {serialized}")
            by_path.add(serialized)

            key = (
                spec.path.scope,
                spec.path.receiver_id,
                spec.path.slot,
                spec.path.name,
            )
            previous = ambiguity_keys.get(key)
            if previous is not None and previous != spec.path.family.value:
                raise ValueError(
                    "ambiguous field name for path target: "
                    f"{spec.path.name!r} in {spec.path.scope.value}"
                )
            ambiguity_keys[key] = spec.path.family.value

    @classmethod
    def from_paths(cls, paths: Iterable[FieldPath]) -> FieldRegistry:
        return cls(
            tuple(
                FieldSpec(
                    path=path,
                    family=path.family,
                    value_type="object",
                )
                for path in paths
            )
        )

    def require(self, path: FieldPath | str) -> FieldSpec:
        needle = FieldPath.parse(path) if isinstance(path, str) else path
        for spec in self.fields:
            if spec.path == needle:
                return spec
        raise KeyError(str(needle))

    def paths(self) -> tuple[FieldPath, ...]:
        return tuple(spec.path for spec in self.fields)

    def to_dict(self) -> dict[str, Any]:
        return {"fields": [field.to_dict() for field in self.fields]}


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Origin metadata for an observation or command-facing state sample."""

    source: ObservationSource
    provider: str
    transport: str | None = None
    native_id: str | None = None
    capability_id: str | None = None
    command_source: CommandSource | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        _validate_source(self.source, _OBSERVATION_SOURCES, label="observation source")
        _validate_token(self.provider, label="provider")
        if self.command_source is not None:
            _validate_source(
                self.command_source,
                _COMMAND_SOURCES,
                label="command source",
            )
        if self.session_id is not None and not self.session_id:
            raise ValueError("session_id must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "provider": self.provider,
            "transport": self.transport,
            "nativeId": self.native_id,
            "capabilityId": self.capability_id,
            "commandSource": self.command_source,
            "sessionId": self.session_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SourceMetadata:
        return cls(
            source=_observation_source(value["source"]),
            provider=str(value["provider"]),
            transport=None
            if value.get("transport") is None
            else str(value["transport"]),
            native_id=None if value.get("nativeId") is None else str(value["nativeId"]),
            capability_id=(
                None
                if value.get("capabilityId") is None
                else str(value["capabilityId"])
            ),
            command_source=(
                None
                if value.get("commandSource") is None
                else _command_source(value["commandSource"])
            ),
            session_id=(
                None if value.get("sessionId") is None else str(value["sessionId"])
            ),
        )


@dataclass(frozen=True, slots=True)
class CapabilityMetadata:
    """Backend capability metadata for one field path."""

    path: FieldPath
    sources: tuple[ObservationSource, ...]
    readable: bool = True
    writable: bool = False
    unsolicited: bool = False
    max_age: float | None = None

    def __post_init__(self) -> None:
        for source in self.sources:
            _validate_source(source, _OBSERVATION_SOURCES, label="observation source")
        if self.max_age is not None and self.max_age < 0:
            raise ValueError("max_age must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sources": list(self.sources),
            "readable": self.readable,
            "writable": self.writable,
            "unsolicited": self.unsolicited,
            "maxAge": self.max_age,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilityMetadata:
        return cls(
            path=FieldPath.parse(str(value["path"])),
            sources=tuple(_observation_source(source) for source in value["sources"]),
            readable=_optional_bool(value, "readable", default=True),
            writable=_optional_bool(value, "writable", default=False),
            unsolicited=_optional_bool(value, "unsolicited", default=False),
            max_age=None if value.get("maxAge") is None else float(value["maxAge"]),
        )


@dataclass(frozen=True, slots=True)
class Observation:
    """One decoded state-bearing sample from an acquisition source."""

    path: FieldPath
    value: Any
    source: SourceMetadata
    timestamp_monotonic: float
    quality: tuple[str, ...] = ("confirmed",)
    correlation_id: str | None = None
    max_age: float | None = None
    # Fixed bootstrap token for source compatibility; never late-bound by Store.apply.
    provider_generation: int | None = 0
    clock_domain: str | None = None

    def __post_init__(self) -> None:
        if self.max_age is not None and self.max_age < 0:
            raise ValueError("max_age must be non-negative")
        if self.provider_generation is not None and (
            not isinstance(self.provider_generation, int)
            or isinstance(self.provider_generation, bool)
            or self.provider_generation < 0
        ):
            raise ValueError("provider_generation must be a non-negative integer")
        if self.clock_domain is not None and (
            not isinstance(self.clock_domain, str) or not self.clock_domain
        ):
            raise ValueError("clock_domain must not be empty")
        for item in self.quality:
            _validate_token(item, label="quality")
        if str(self.path) == _TX_TARGET_PATH:
            target = (
                tx_target_from_dict(self.value)
                if isinstance(self.value, Mapping)
                else validate_tx_target(self.value)
            )
            object.__setattr__(self, "value", target)

    def to_dict(self) -> dict[str, Any]:
        value = (
            validate_tx_target(self.value).to_dict()
            if str(self.path) == _TX_TARGET_PATH
            else self.value
        )
        return {
            "path": str(self.path),
            "value": value,
            "source": self.source.to_dict(),
            "timestampMonotonic": self.timestamp_monotonic,
            "quality": list(self.quality),
            "correlationId": self.correlation_id,
            "maxAge": self.max_age,
            "providerGeneration": self.provider_generation,
            "clockDomain": self.clock_domain,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Observation:
        return cls(
            path=FieldPath.from_dict(str(value["path"])),
            value=_required(value, "value"),
            source=SourceMetadata.from_dict(value["source"]),
            timestamp_monotonic=float(value["timestampMonotonic"]),
            quality=tuple(str(item) for item in value.get("quality", ("confirmed",))),
            correlation_id=(
                None
                if value.get("correlationId") is None
                else str(value["correlationId"])
            ),
            max_age=None if value.get("maxAge") is None else float(value["maxAge"]),
            provider_generation=(
                0
                if "providerGeneration" not in value
                else None
                if value["providerGeneration"] is None
                else int(value["providerGeneration"])
            ),
            clock_domain=(
                None
                if "clockDomain" not in value
                else None
                if value["clockDomain"] is None
                else str(value["clockDomain"])
            ),
        )


@dataclass(frozen=True, slots=True)
class FieldChange:
    """One confirmed state value change."""

    path: FieldPath
    previous: Any
    current: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "previous": self.previous,
            "current": self.current,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FieldChange:
        return cls(
            path=FieldPath.from_dict(str(value["path"])),
            previous=_required(value, "previous"),
            current=_required(value, "current"),
        )


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """Result of applying observations to a future production StateStore."""

    revision: int
    freshness_revision: int
    observation_seq: int
    changes: tuple[FieldChange, ...]
    timestamp_monotonic: float
    sources: tuple[SourceMetadata, ...]
    coalesced: bool = False
    observed_paths: tuple[FieldPath, ...] = ()
    freshness_paths: tuple[FieldPath, ...] = ()

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        if self.freshness_revision < 0:
            raise ValueError("freshness_revision must be non-negative")
        if self.observation_seq < 0:
            raise ValueError("observation_seq must be non-negative")
        object.__setattr__(
            self,
            "observed_paths",
            tuple(
                FieldPath.from_dict(path) if isinstance(path, str) else path
                for path in self.observed_paths
            ),
        )
        object.__setattr__(
            self,
            "freshness_paths",
            tuple(
                FieldPath.from_dict(path) if isinstance(path, str) else path
                for path in self.freshness_paths
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "freshnessRevision": self.freshness_revision,
            "observationSeq": self.observation_seq,
            "changes": [change.to_dict() for change in self.changes],
            "timestampMonotonic": self.timestamp_monotonic,
            "sources": [source.to_dict() for source in self.sources],
            "coalesced": self.coalesced,
            "observedPaths": [str(path) for path in self.observed_paths],
            "freshnessPaths": [str(path) for path in self.freshness_paths],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ChangeSet:
        return cls(
            revision=int(value["revision"]),
            freshness_revision=int(value["freshnessRevision"]),
            observation_seq=int(value["observationSeq"]),
            changes=tuple(FieldChange.from_dict(item) for item in value["changes"]),
            timestamp_monotonic=float(value["timestampMonotonic"]),
            sources=tuple(SourceMetadata.from_dict(item) for item in value["sources"]),
            coalesced=_optional_bool(value, "coalesced", default=False),
            observed_paths=tuple(
                FieldPath.from_dict(item) for item in value.get("observedPaths", ())
            ),
            freshness_paths=tuple(
                FieldPath.from_dict(item) for item in value.get("freshnessPaths", ())
            ),
        )


@dataclass(frozen=True, slots=True)
class CommandIntent:
    """Backend-neutral command or query request from a consumer."""

    id: str
    name: str
    params: Mapping[str, Any]
    source: CommandSource
    target: FieldPath | None = None
    priority: CommandPriority = "normal"
    timeout: float | None = None
    pending_policy: PendingPolicy = "none"
    expected_observations: tuple[FieldPath, ...] = ()

    def __post_init__(self) -> None:
        _validate_token(self.name, label="command name")
        _validate_source(self.source, _COMMAND_SOURCES, label="command source")
        _validate_source(self.priority, _COMMAND_PRIORITIES, label="command priority")
        _validate_source(self.pending_policy, _PENDING_POLICIES, label="pending policy")
        if self.timeout is not None and self.timeout < 0:
            raise ValueError("timeout must be non-negative")
        object.__setattr__(self, "params", dict(self.params))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "params": dict(self.params),
            "source": self.source,
            "target": None if self.target is None else str(self.target),
            "priority": self.priority,
            "timeout": self.timeout,
            "pendingPolicy": self.pending_policy,
            "expectedObservations": [str(path) for path in self.expected_observations],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CommandIntent:
        return cls(
            id=str(value["id"]),
            name=str(value["name"]),
            params=dict(value.get("params", {})),
            source=_command_source(value["source"]),
            target=(
                None
                if value.get("target") is None
                else FieldPath.parse(str(value["target"]))
            ),
            priority=_command_priority(value.get("priority", "normal")),
            timeout=None if value.get("timeout") is None else float(value["timeout"]),
            pending_policy=_pending_policy(value.get("pendingPolicy", "none")),
            expected_observations=tuple(
                FieldPath.parse(str(path))
                for path in value.get("expectedObservations", ())
            ),
        )


@dataclass(frozen=True, slots=True)
class CommandLifecycleEvent:
    """Lifecycle event for a command intent, separate from state changes."""

    command_id: str
    state: CommandLifecycleState
    timestamp_monotonic: float
    source: CommandSource
    target: FieldPath | None = None
    message: str | None = None
    details: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _validate_source(self.state, _COMMAND_STATES, label="command lifecycle state")
        _validate_source(self.source, _COMMAND_SOURCES, label="command source")
        object.__setattr__(
            self,
            "details",
            {} if self.details is None else dict(self.details),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "commandId": self.command_id,
            "state": self.state,
            "timestampMonotonic": self.timestamp_monotonic,
            "source": self.source,
            "target": None if self.target is None else str(self.target),
            "message": self.message,
            "details": dict(self.details or {}),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CommandLifecycleEvent:
        return cls(
            command_id=str(value["commandId"]),
            state=_command_state(value["state"]),
            timestamp_monotonic=float(value["timestampMonotonic"]),
            source=_command_source(value["source"]),
            target=(
                None
                if value.get("target") is None
                else FieldPath.parse(str(value["target"]))
            ),
            message=None if value.get("message") is None else str(value["message"]),
            details=dict(value.get("details", {})),
        )


def _receiver_specs(receiver_id: str) -> tuple[FieldSpec, ...]:
    def spec(
        path: FieldPath,
        value_type: str,
        *,
        writable: bool = False,
        unit: str | None = None,
    ) -> FieldSpec:
        return FieldSpec(
            path=path,
            family=path.family,
            value_type=value_type,
            writable=writable,
            unit=unit,
        )

    return (
        spec(
            FieldPath.active(receiver_id, "freq_mode", "freq_hz"),
            "int",
            writable=True,
            unit="hz",
        ),
        spec(FieldPath.active(receiver_id, "freq_mode", "mode"), "str", writable=True),
        spec(
            FieldPath.unselected(receiver_id, "freq_mode", "freq_hz"),
            "int",
            unit="hz",
        ),
        spec(FieldPath.unselected(receiver_id, "freq_mode", "mode"), "str"),
        spec(FieldPath.unselected(receiver_id, "freq_mode", "filter_num"), "int"),
        spec(FieldPath.unselected(receiver_id, "freq_mode", "data_mode"), "int"),
        *(
            spec(
                FieldPath.vfo_slot(receiver_id, slot, "freq_mode", name),
                "int",
                writable=True,
            )
            for slot in ("A", "B")
            for name in ("filter_num", "data_mode")
        ),
        spec(
            FieldPath.vfo_slot(receiver_id, "A", "freq_mode", "freq_hz"),
            "int",
            writable=True,
            unit="hz",
        ),
        spec(
            FieldPath.vfo_slot(receiver_id, "A", "freq_mode", "mode"),
            "str",
            writable=True,
        ),
        spec(
            FieldPath.vfo_slot(receiver_id, "B", "freq_mode", "freq_hz"),
            "int",
            writable=True,
            unit="hz",
        ),
        spec(
            FieldPath.vfo_slot(receiver_id, "B", "freq_mode", "mode"),
            "str",
            writable=True,
        ),
        spec(FieldPath.active_slot(receiver_id), "str", writable=True),
        spec(
            FieldPath.receiver(receiver_id, "meters", "s_meter"),
            "int",
            unit="db",
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_toggles", "nr"),
            "bool",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_toggles", "nb"),
            "bool",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "af_level"),
            "float",
            writable=True,
            unit="normalized",
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "rf_gain"),
            "float",
            writable=True,
            unit="normalized",
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "pbt_inner"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "pbt_outer"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "squelch"),
            "float",
            writable=True,
            unit="normalized",
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "att"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "preamp"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "agc"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "agc_time_constant"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "nr_level"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "nb_level"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "if_shift"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "manual_notch_freq"),
            "int",
            writable=True,
        ),
        *(
            spec(
                FieldPath.receiver(receiver_id, "operator_controls", name),
                "int",
                writable=True,
            )
            for name in (
                "manual_notch_width",
                "filter_shape",
                "digisel_shift",
                "apf_freq",
            )
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_toggles", "auto_notch"),
            "bool",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_toggles", "narrow"),
            "bool",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_toggles", "manual_notch"),
            "bool",
            writable=True,
        ),
        *(
            spec(
                FieldPath.receiver(receiver_id, "operator_toggles", name),
                "bool",
                writable=True,
            )
            for name in ("af_mute", "apf_on")
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_toggles", "repeater_tone"),
            "bool",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_toggles", "repeater_tsql"),
            "bool",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "tone_freq"),
            "int",
            writable=True,
            unit="centihz",
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "tsql_freq"),
            "int",
            writable=True,
            unit="centihz",
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "audio_peak_filter"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_controls", "apf_type_level"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_toggles", "twin_peak_filter"),
            "bool",
            writable=True,
        ),
        # Squelch-open / DCD (RX-busy) status: read-only observation, the RX
        # counterpart of the first-class TX ``ptt`` (MOR-466).
        spec(
            FieldPath.receiver(receiver_id, "operator_toggles", "dcd"),
            "bool",
        ),
        # DIGI-SEL and IP+ receiver toggles: read-WRITE bools — unlike ``dcd``
        # these have CAT set commands (``set_digisel``/``set_ip_plus``), so the
        # command-overlay path needs a writable spec to observe + overlay them
        # (MOR-477).
        spec(
            FieldPath.receiver(receiver_id, "operator_toggles", "digisel"),
            "bool",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "operator_toggles", "ipplus"),
            "bool",
            writable=True,
        ),
        spec(
            FieldPath.active(receiver_id, "freq_mode", "data_mode"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.active(receiver_id, "freq_mode", "filter_width"),
            "int",
            writable=True,
            unit="hz",
        ),
        spec(
            FieldPath.active(receiver_id, "freq_mode", "filter_num"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.receiver(receiver_id, "slow_state", "contour"),
            "int",
            writable=True,
        ),
    )


def _global_specs() -> tuple[FieldSpec, ...]:
    def spec(
        path: FieldPath,
        value_type: str,
        *,
        writable: bool = False,
        unit: str | None = None,
    ) -> FieldSpec:
        return FieldSpec(
            path=path,
            family=path.family,
            value_type=value_type,
            writable=writable,
            unit=unit,
        )

    return (
        spec(FieldPath.global_("tx_state", "tx_target"), "object"),
        spec(FieldPath.global_("tx_state", "ptt"), "bool", writable=True),
        spec(FieldPath.global_("tx_state", "power_on"), "bool", writable=True),
        spec(FieldPath.global_("tx_state", "rit_on"), "bool", writable=True),
        spec(FieldPath.global_("tx_state", "rit_tx"), "bool", writable=True),
        spec(FieldPath.global_("tx_state", "dial_lock"), "bool", writable=True),
        spec(FieldPath.global_("tx_state", "split"), "bool", writable=True),
        spec(FieldPath.global_("tx_state", "dual_watch"), "bool", writable=True),
        spec(FieldPath.global_("tx_state", "compressor_on"), "bool", writable=True),
        spec(FieldPath.global_("tx_state", "monitor_on"), "bool", writable=True),
        spec(FieldPath.global_("tx_state", "vox_on"), "bool", writable=True),
        spec(FieldPath.global_("tx_state", "tx_freq_monitor"), "bool", writable=True),
        spec(FieldPath.global_("tx_state", "main_sub_tracking"), "bool", writable=True),
        spec(
            FieldPath.global_("operator_controls", "power_level"),
            "float",
            writable=True,
            unit="normalized",
        ),
        spec(
            FieldPath.global_("operator_controls", "rit_freq"),
            "int",
            writable=True,
            unit="hz",
        ),
        spec(FieldPath.global_("operator_controls", "mic_gain"), "int", writable=True),
        spec(
            FieldPath.global_("operator_controls", "compressor_level"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.global_("operator_controls", "monitor_gain"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.global_("operator_controls", "cw_pitch"),
            "int",
            writable=True,
            unit="hz",
        ),
        spec(
            FieldPath.global_("operator_controls", "tuner_status"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.global_("operator_controls", "key_speed"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.global_("operator_controls", "break_in"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.global_("operator_controls", "break_in_delay"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.global_("operator_controls", "vox_gain"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.global_("operator_controls", "anti_vox_gain"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.global_("operator_controls", "vox_delay"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.global_("operator_controls", "tx_antenna"),
            "int",
            writable=True,
        ),
        *(
            spec(
                FieldPath.global_("operator_controls", name),
                "int",
                writable=True,
            )
            for name in (
                "notch_filter",
                "nb_depth",
                "nb_width",
                "drive_gain",
                "dash_ratio",
                "ref_adjust",
                "ssb_tx_bandwidth",
            )
        ),
        spec(FieldPath.global_("slow_state", "active"), "str"),
        spec(FieldPath.global_("slow_state", "cw_spot"), "bool"),
        *(
            spec(FieldPath.global_("slow_state", name), value_type, writable=True)
            for name, value_type in (
                ("scanning", "bool"),
                ("scan_type", "int"),
                ("scan_resume_mode", "int"),
                ("data_off_mod_input", "int"),
                ("data1_mod_input", "int"),
                ("data2_mod_input", "int"),
                ("data3_mod_input", "int"),
                ("tx_band_edges", "object"),
            )
        ),
        spec(
            FieldPath.global_("slow_state", "tuning_step"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.global_("slow_state", "rx_antenna_1"),
            "bool",
            writable=True,
        ),
        spec(
            FieldPath.global_("slow_state", "rx_antenna_2"),
            "bool",
            writable=True,
        ),
        spec(FieldPath.global_("meters", "alc"), "float", unit="normalized"),
        spec(FieldPath.global_("meters", "power"), "float", unit="w"),
        spec(FieldPath.global_("meters", "swr"), "float", unit="ratio"),
        spec(FieldPath.global_("meters", "comp"), "float", unit="db"),
        spec(FieldPath.global_("meters", "vd"), "float", unit="v"),
        spec(FieldPath.global_("meters", "id"), "float", unit="a"),
        spec(
            FieldPath.scope_control("display", "span", receiver_id="main"),
            "int",
            writable=True,
        ),
        spec(
            FieldPath.scope_control("display", "span", receiver_id="sub"),
            "int",
            writable=True,
        ),
        # Global scope-control display leaves (MOR-557): read-only ingress
        # observations decoded from CI-V 0x27 answers; writes go through the
        # scope command path, not the store.
        spec(FieldPath.scope_control("display", "receiver"), "int"),
        spec(FieldPath.scope_control("display", "dual"), "bool"),
        spec(FieldPath.scope_control("display", "mode"), "int"),
        spec(FieldPath.scope_control("display", "span"), "int"),
        spec(FieldPath.scope_control("display", "edge"), "int"),
        spec(FieldPath.scope_control("display", "hold"), "bool"),
        spec(FieldPath.scope_control("display", "ref_db"), "float", unit="db"),
        spec(FieldPath.scope_control("display", "speed"), "int"),
        # MOR-1302: the five ScopeSettingsPopover leaves on ScopeControlsPublic
        # that had no registered spec (verified against the 0x27 sub-command
        # decoders in runtime/_civ_rx.py: 0x1B during_tx, 0x1C center_type,
        # 0x1D vbw_narrow, 0x1E fixed_edge, 0x1F rbw). Read-only, same as the
        # eight leaves above -- writes go through the scope command path.
        spec(FieldPath.scope_control("display", "during_tx"), "bool"),
        spec(FieldPath.scope_control("display", "center_type"), "int"),
        spec(FieldPath.scope_control("display", "vbw_narrow"), "bool"),
        spec(FieldPath.scope_control("display", "rbw"), "int"),
        spec(FieldPath.scope_control("display", "fixed_edge"), "object"),
        *(
            spec(
                FieldPath(scope=scope, family=family, name=name),
                value_type,
            )
            for scope, family, rows in (
                (
                    FieldScope.CONNECTION,
                    FieldFamily.CONNECTION,
                    (
                        ("connected", "bool"),
                        ("radio_ready", "bool"),
                        ("control_connected", "bool"),
                        ("status", "str"),
                    ),
                ),
                (
                    FieldScope.HEALTH,
                    FieldFamily.HEALTH,
                    (
                        ("server_reachable", "bool"),
                        ("radio_link", "str"),
                        ("readiness", "str"),
                        ("likely_cause", "str"),
                        ("since_ms", "int"),
                        ("last_error", "str"),
                    ),
                ),
            )
            for name, value_type in rows
        ),
    )


DEFAULT_FIELD_REGISTRY = FieldRegistry(
    _receiver_specs("main") + _receiver_specs("sub") + _global_specs()
)
