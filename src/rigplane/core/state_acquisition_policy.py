"""Radio state capability and acquisition-policy metadata.

These schema objects describe what a provider can acquire and how future
schedulers should acquire it. They intentionally do not implement scheduling.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from rigplane.core.state_pipeline_contracts import FieldFamily, FieldPath

__all__ = [
    "AcquisitionPolicy",
    "AdaptiveDecayPolicy",
    "ExternalCatPauseBehavior",
    "FieldAvailability",
    "FieldCapability",
    "MeterCoalescingPolicy",
    "RadioAcquisitionProfile",
    "ReconciliationPriority",
]


class FieldAvailability(StrEnum):
    """Whether a state field is available from a provider."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class ReconciliationPriority(StrEnum):
    """Preferred source when several observations can update the same field."""

    UNSOLICITED = "unsolicited"
    COMMAND_RESPONSE = "command_response"
    POLL = "poll"
    LAST_OBSERVATION = "last_observation"


class ExternalCatPauseBehavior(StrEnum):
    """How acquisition reacts while an external CAT owner controls the radio."""

    PAUSE_POLLING = "pause_polling"
    COALESCE_METERS_ONLY = "coalesce_meters_only"
    CONTINUE = "continue"


_TOKEN_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


def _reject_unknown_keys(
    value: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    label: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} unknown keys: {', '.join(unknown)}")


def _validate_token(value: str, *, label: str) -> str:
    if not value:
        raise ValueError(f"{label} must not be empty")
    if any(ch not in _TOKEN_ALPHABET for ch in value):
        raise ValueError(f"{label} must use lowercase snake-case tokens: {value!r}")
    return value


def _strict_bool(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a bool")
    return value


def _strict_float(value: Any, *, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    return float(value)


def _strict_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _optional_positive_float(value: Any, *, label: str) -> float | None:
    if value is None:
        return None
    number = _strict_float(value, label=label)
    if number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


@dataclass(frozen=True, slots=True)
class AdaptiveDecayPolicy:
    """Cadence widening policy for idle or low-value fields."""

    enabled: bool = False
    idle_multiplier: float = 1.0
    max_cadence_seconds: float | None = None

    def __post_init__(self) -> None:
        enabled = _strict_bool(self.enabled, label="enabled")
        idle_multiplier = _strict_float(
            self.idle_multiplier,
            label="idle_multiplier",
        )
        if idle_multiplier < 1.0:
            raise ValueError("idle_multiplier must be >= 1.0")
        max_cadence = _optional_positive_float(
            self.max_cadence_seconds,
            label="max_cadence_seconds",
        )
        if enabled and idle_multiplier <= 1.0:
            raise ValueError("enabled adaptive decay requires idle_multiplier > 1.0")
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "idle_multiplier", idle_multiplier)
        object.__setattr__(self, "max_cadence_seconds", max_cadence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "idleMultiplier": self.idle_multiplier,
            "maxCadenceSeconds": self.max_cadence_seconds,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> AdaptiveDecayPolicy:
        if value is None:
            return cls()
        _reject_unknown_keys(
            value,
            allowed=frozenset(
                {
                    "enabled",
                    "idleMultiplier",
                    "maxCadenceSeconds",
                }
            ),
            label="adaptiveDecay",
        )
        return cls(
            enabled=value.get("enabled", False),
            idle_multiplier=value.get("idleMultiplier", 1.0),
            max_cadence_seconds=(
                None
                if value.get("maxCadenceSeconds") is None
                else value["maxCadenceSeconds"]
            ),
        )


@dataclass(frozen=True, slots=True)
class MeterCoalescingPolicy:
    """Short-window coalescing policy for stream-like meter updates."""

    window_seconds: float
    max_samples: int | None = None

    def __post_init__(self) -> None:
        window_seconds = _strict_float(
            self.window_seconds,
            label="window_seconds",
        )
        max_samples = (
            None
            if self.max_samples is None
            else _strict_int(self.max_samples, label="max_samples")
        )
        if window_seconds < 0:
            raise ValueError("window_seconds must be non-negative")
        if max_samples is not None and max_samples <= 0:
            raise ValueError("max_samples must be positive")
        object.__setattr__(self, "window_seconds", window_seconds)
        object.__setattr__(self, "max_samples", max_samples)

    def to_dict(self) -> dict[str, Any]:
        return {
            "windowSeconds": self.window_seconds,
            "maxSamples": self.max_samples,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any] | None,
    ) -> MeterCoalescingPolicy | None:
        if value is None:
            return None
        _reject_unknown_keys(
            value,
            allowed=frozenset({"windowSeconds", "maxSamples"}),
            label="meterCoalescing",
        )
        return cls(
            window_seconds=value["windowSeconds"],
            max_samples=(
                None if value.get("maxSamples") is None else value["maxSamples"]
            ),
        )


@dataclass(frozen=True, slots=True)
class AcquisitionPolicy:
    """Scheduler-facing policy for acquiring one or more fields."""

    cadence_seconds: float | None = 5.0
    freshness_ttl_seconds: float | None = 15.0
    reconciliation_priority: ReconciliationPriority | str = ReconciliationPriority.POLL
    adaptive_decay: AdaptiveDecayPolicy = field(default_factory=AdaptiveDecayPolicy)
    external_cat_pause: ExternalCatPauseBehavior | str = (
        ExternalCatPauseBehavior.PAUSE_POLLING
    )
    meter_coalescing: MeterCoalescingPolicy | None = None

    def __post_init__(self) -> None:
        cadence = _optional_positive_float(
            self.cadence_seconds,
            label="cadence_seconds",
        )
        ttl = _optional_positive_float(
            self.freshness_ttl_seconds,
            label="freshness_ttl_seconds",
        )
        if cadence is not None and ttl is not None and ttl < cadence:
            raise ValueError("freshness_ttl_seconds must be >= cadence_seconds")
        object.__setattr__(self, "cadence_seconds", cadence)
        object.__setattr__(self, "freshness_ttl_seconds", ttl)
        object.__setattr__(
            self,
            "reconciliation_priority",
            ReconciliationPriority(str(self.reconciliation_priority)),
        )
        object.__setattr__(
            self,
            "external_cat_pause",
            ExternalCatPauseBehavior(str(self.external_cat_pause)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cadenceSeconds": self.cadence_seconds,
            "freshnessTtlSeconds": self.freshness_ttl_seconds,
            "reconciliationPriority": ReconciliationPriority(
                str(self.reconciliation_priority)
            ).value,
            "adaptiveDecay": self.adaptive_decay.to_dict(),
            "externalCatPause": ExternalCatPauseBehavior(
                str(self.external_cat_pause)
            ).value,
            "meterCoalescing": (
                None
                if self.meter_coalescing is None
                else self.meter_coalescing.to_dict()
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AcquisitionPolicy:
        _reject_unknown_keys(
            value,
            allowed=frozenset(
                {
                    "cadenceSeconds",
                    "freshnessTtlSeconds",
                    "reconciliationPriority",
                    "adaptiveDecay",
                    "externalCatPause",
                    "meterCoalescing",
                }
            ),
            label="acquisition policy",
        )
        cadence_seconds = (
            None
            if value.get("cadenceSeconds") is None
            else _strict_float(value["cadenceSeconds"], label="cadenceSeconds")
        )
        freshness_ttl_seconds = (
            None
            if value.get("freshnessTtlSeconds") is None
            else _strict_float(
                value["freshnessTtlSeconds"],
                label="freshnessTtlSeconds",
            )
        )
        return cls(
            cadence_seconds=cadence_seconds,
            freshness_ttl_seconds=freshness_ttl_seconds,
            reconciliation_priority=ReconciliationPriority(
                str(value.get("reconciliationPriority", ReconciliationPriority.POLL))
            ),
            adaptive_decay=AdaptiveDecayPolicy.from_dict(value.get("adaptiveDecay")),
            external_cat_pause=ExternalCatPauseBehavior(
                str(
                    value.get(
                        "externalCatPause",
                        ExternalCatPauseBehavior.PAUSE_POLLING,
                    )
                )
            ),
            meter_coalescing=MeterCoalescingPolicy.from_dict(
                value.get("meterCoalescing")
            ),
        )


@dataclass(frozen=True, slots=True)
class FieldCapability:
    """Provider capability metadata for one field path."""

    path: FieldPath
    availability: FieldAvailability | str = FieldAvailability.SUPPORTED
    unsolicited_push: bool = False
    polling: bool = False
    stream_like: bool = False
    command_response_observable: bool = False
    supported_controls: tuple[str, ...] = ()
    diagnostic: str = ""

    def __post_init__(self) -> None:
        availability = FieldAvailability(str(self.availability))
        unsolicited_push = _strict_bool(
            self.unsolicited_push,
            label="unsolicitedPush",
        )
        polling = _strict_bool(self.polling, label="polling")
        stream_like = _strict_bool(self.stream_like, label="streamLike")
        command_response_observable = _strict_bool(
            self.command_response_observable,
            label="commandResponseObservable",
        )
        controls = tuple(str(control) for control in self.supported_controls)
        for control in controls:
            _validate_token(control, label="supported control")
        if availability is not FieldAvailability.SUPPORTED and (
            unsolicited_push
            or polling
            or stream_like
            or command_response_observable
            or controls
        ):
            raise ValueError(
                f"{self.path}: unavailable fields cannot be acquired or controlled"
            )
        if stream_like and self.path.family is not FieldFamily.METERS:
            raise ValueError(f"{self.path}: stream_like fields must be meters")
        object.__setattr__(self, "availability", availability)
        object.__setattr__(self, "unsolicited_push", unsolicited_push)
        object.__setattr__(self, "polling", polling)
        object.__setattr__(self, "stream_like", stream_like)
        object.__setattr__(
            self,
            "command_response_observable",
            command_response_observable,
        )
        object.__setattr__(self, "supported_controls", controls)

    @property
    def can_poll(self) -> bool:
        return self.availability is FieldAvailability.SUPPORTED and self.polling

    @property
    def is_unavailable(self) -> bool:
        return self.availability in (
            FieldAvailability.UNSUPPORTED,
            FieldAvailability.UNKNOWN,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "availability": FieldAvailability(str(self.availability)).value,
            "unsolicitedPush": self.unsolicited_push,
            "polling": self.polling,
            "streamLike": self.stream_like,
            "commandResponseObservable": self.command_response_observable,
            "supportedControls": list(self.supported_controls),
            "diagnostic": self.diagnostic,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FieldCapability:
        _reject_unknown_keys(
            value,
            allowed=frozenset(
                {
                    "path",
                    "availability",
                    "unsolicitedPush",
                    "polling",
                    "streamLike",
                    "commandResponseObservable",
                    "supportedControls",
                    "diagnostic",
                }
            ),
            label="field capability",
        )
        return cls(
            path=FieldPath.parse(str(value["path"])),
            availability=FieldAvailability(
                str(value.get("availability", FieldAvailability.SUPPORTED))
            ),
            unsolicited_push=value.get("unsolicitedPush", False),
            polling=value.get("polling", False),
            stream_like=value.get("streamLike", False),
            command_response_observable=value.get(
                "commandResponseObservable",
                False,
            ),
            supported_controls=tuple(
                str(control) for control in value.get("supportedControls", ())
            ),
            diagnostic=str(value.get("diagnostic", "")),
        )


@dataclass(frozen=True, slots=True)
class RadioAcquisitionProfile:
    """Provider-specific state acquisition metadata for one radio profile."""

    provider: str
    capabilities: tuple[FieldCapability, ...] = ()
    default_policy: AcquisitionPolicy = field(default_factory=AcquisitionPolicy)
    field_policies: Mapping[FieldPath, AcquisitionPolicy] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_token(self.provider, label="provider")
        by_path: dict[FieldPath, FieldCapability] = {}
        for capability in self.capabilities:
            if capability.path in by_path:
                raise ValueError(f"duplicate capability path: {capability.path}")
            by_path[capability.path] = capability
        policies = dict(self.field_policies)
        for path, policy in policies.items():
            if (
                policy.meter_coalescing is not None
                and path.family is not FieldFamily.METERS
            ):
                raise ValueError(f"{path}: meter_coalescing requires meter fields")
        object.__setattr__(self, "capabilities", tuple(by_path.values()))
        object.__setattr__(self, "field_policies", policies)

    def capability_for(self, path: FieldPath) -> FieldCapability:
        for capability in self.capabilities:
            if capability.path == path:
                return capability
        return FieldCapability(
            path=path,
            availability=FieldAvailability.UNKNOWN,
            diagnostic=f"{path}: missing capability metadata",
        )

    def policy_for(self, path: FieldPath) -> AcquisitionPolicy:
        return self.field_policies.get(path, self.default_policy)

    def pollable_paths(self) -> tuple[FieldPath, ...]:
        return tuple(
            capability.path for capability in self.capabilities if capability.can_poll
        )

    def unavailable_paths(self) -> tuple[FieldPath, ...]:
        return tuple(
            capability.path
            for capability in self.capabilities
            if capability.is_unavailable
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "capabilities": [capability.to_dict() for capability in self.capabilities],
            "defaultPolicy": self.default_policy.to_dict(),
            "fieldPolicies": {
                str(path): policy.to_dict()
                for path, policy in sorted(
                    self.field_policies.items(),
                    key=lambda item: str(item[0]),
                )
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RadioAcquisitionProfile:
        _reject_unknown_keys(
            value,
            allowed=frozenset(
                {
                    "provider",
                    "capabilities",
                    "defaultPolicy",
                    "fieldPolicies",
                }
            ),
            label="radio acquisition profile",
        )
        return cls(
            provider=str(value["provider"]),
            capabilities=tuple(
                FieldCapability.from_dict(item)
                for item in value.get("capabilities", ())
            ),
            default_policy=AcquisitionPolicy.from_dict(value.get("defaultPolicy", {})),
            field_policies={
                FieldPath.parse(str(path)): AcquisitionPolicy.from_dict(policy)
                for path, policy in value.get("fieldPolicies", {}).items()
            },
        )
