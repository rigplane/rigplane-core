"""Radio state capability and acquisition-policy metadata.

These schema objects describe what a provider can acquire and how future
schedulers should acquire it. They intentionally do not implement scheduling.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from rigplane.core.state_pipeline_contracts import (
    AcquisitionClass,
    FieldFamily,
    FieldPath,
    acquisition_class_for_path,
)

__all__ = [
    "ACQUISITION_BUDGET_MARGIN",
    "ACQUISITION_CLASS_TABLE",
    "AcquisitionClassPolicy",
    "AcquisitionPhase",
    "AcquisitionPolicy",
    "AdaptiveDecayPolicy",
    "AvailabilityClause",
    "AvailabilityOperator",
    "BudgetFit",
    "ExternalCatPauseBehavior",
    "FieldAvailability",
    "FieldCapability",
    "MeterCoalescingPolicy",
    "OPERATOR_SET_MAX_CADENCE_SECONDS",
    "RadioAcquisitionProfile",
    "ReconciliationPriority",
    "acquisition_policy_for_class",
    "fit_to_budget",
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


def _strict_string_sequence(value: Any, *, label: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"{label} must be a sequence of strings")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a sequence of strings")
    return tuple(value)


def _optional_positive_float(value: Any, *, label: str) -> float | None:
    if value is None:
        return None
    number = _strict_float(value, label=label)
    if number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


class AcquisitionPhase(StrEnum):
    """Where in the radio's cycle an acquisition class is cadence-polled."""

    RECEIVE = "receive"
    TRANSMIT = "transmit"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class AcquisitionClassPolicy:
    """Cadence envelope for one acquisition class (MOR-2574).

    ``ceiling_cadence_seconds`` is the slowest the budget fit may stretch
    the class to.
    """

    nominal_cadence_seconds: float
    ceiling_cadence_seconds: float
    polled_in: AcquisitionPhase = AcquisitionPhase.BOTH
    held_at_ceiling_in_tx: bool = False

    def __post_init__(self) -> None:
        nominal = _strict_float(
            self.nominal_cadence_seconds,
            label="nominal_cadence_seconds",
        )
        ceiling = _strict_float(
            self.ceiling_cadence_seconds,
            label="ceiling_cadence_seconds",
        )
        if nominal <= 0 or ceiling <= 0:
            raise ValueError("cadences must be positive")
        if nominal > ceiling:
            raise ValueError("nominal_cadence_seconds must be <= ceiling")
        polled_in = AcquisitionPhase(str(self.polled_in))
        object.__setattr__(self, "nominal_cadence_seconds", nominal)
        object.__setattr__(self, "ceiling_cadence_seconds", ceiling)
        object.__setattr__(self, "polled_in", polled_in)
        object.__setattr__(
            self,
            "held_at_ceiling_in_tx",
            _strict_bool(self.held_at_ceiling_in_tx, label="held_at_ceiling_in_tx"),
        )

    @property
    def freshness_ttl_seconds(self) -> float:
        """TTL for the class: ``max(2 x ceiling, ceiling + 0.7)`` seconds."""

        return max(
            2.0 * self.ceiling_cadence_seconds,
            self.ceiling_cadence_seconds + 0.7,
        )


#: The owner's single threshold for a panel change reaching the web
#: (2026-09-07); the panel class polls at this nominal cadence.
OPERATOR_SET_MAX_CADENCE_SECONDS: Final[float] = 5.0

#: One row per :class:`AcquisitionClass`, in rank order high -> low — the
#: iteration order of this table is the stretching order reversed. Values
#: are the owner-approved table of 2026-09-24.
ACQUISITION_CLASS_TABLE: Final[dict[AcquisitionClass, AcquisitionClassPolicy]] = {
    AcquisitionClass.KEYING: AcquisitionClassPolicy(0.3, 0.5),
    AcquisitionClass.TX_METER: AcquisitionClassPolicy(
        0.25,
        1.0,
        polled_in=AcquisitionPhase.TRANSMIT,
    ),
    AcquisitionClass.LIVE: AcquisitionClassPolicy(1.0, 1.0),
    AcquisitionClass.METER: AcquisitionClassPolicy(0.3, 0.4),
    AcquisitionClass.CONTROL: AcquisitionClassPolicy(2.0, 5.0),
    AcquisitionClass.PANEL: AcquisitionClassPolicy(
        OPERATOR_SET_MAX_CADENCE_SECONDS,
        10.0,
    ),
    AcquisitionClass.SETTING: AcquisitionClassPolicy(
        10.0,
        30.0,
        held_at_ceiling_in_tx=True,
    ),
    AcquisitionClass.MENU: AcquisitionClassPolicy(
        30.0,
        60.0,
        held_at_ceiling_in_tx=True,
    ),
}

#: Share of a transport budget the scheduler fits cadence polls into
#: (coordinator decision, MOR-2574).
ACQUISITION_BUDGET_MARGIN: Final[float] = 0.75

#: Float slack when deciding a fitted demand meets the margin-limited
#: budget, so a fit that closes exactly on the limit is not read as over.
_FIT_EPSILON: Final[float] = 1e-9


def acquisition_policy_for_class(klass: AcquisitionClass) -> AcquisitionPolicy:
    """Scheduler-facing policy one acquisition class resolves to (MOR-2574).

    The class's nominal cadence and its freshness TTL, TX-only when the
    table polls the class during transmit only, and adaptive decay off.
    """

    entry = ACQUISITION_CLASS_TABLE[klass]
    return AcquisitionPolicy(
        cadence_seconds=entry.nominal_cadence_seconds,
        freshness_ttl_seconds=entry.freshness_ttl_seconds,
        tx_only=entry.polled_in is AcquisitionPhase.TRANSMIT,
    )


@dataclass(frozen=True, slots=True)
class BudgetFit:
    """Result of :func:`fit_to_budget`."""

    #: Effective cadence per class present in the demand, in seconds.
    effective_cadence_seconds: dict[AcquisitionClass, float]
    #: Total demand the fit settled on, ``reserved_hz`` included, in
    #: queries per second.
    demand_hz: float
    #: Whether that demand fits within ``margin x budget_hz``.
    fits: bool


def fit_to_budget(
    counts_per_class: Mapping[AcquisitionClass, int],
    budget_hz: float,
    margin: float,
    tx: bool,
    reserved_hz: float = 0.0,
) -> BudgetFit:
    """Fit per-class poll demand to a transport budget (MOR-2574).

    ``counts_per_class`` maps each acquisition class to the number of
    polled fields in it. ``reserved_hz`` is demand the fit may not
    stretch, counted against the same limit. Starting from every class's
    nominal cadence (its ceiling when the class is held at the ceiling
    during TX and ``tx`` is set), the fit stretches the lowest-ranked
    classes first, up to their ceiling, until the total demand is at or
    below ``margin x budget_hz``. TX-only classes are excluded unless
    ``tx`` is set. A class never ends beyond its ceiling. When
    ``reserved_hz`` alone is at or above the limit, no class is stretched
    (coordinator decision, MOR-2586); otherwise, when the ceilings cannot
    bring the demand under the limit, ``fits`` is false and every
    stretched class sits at its ceiling. Pure function.
    """

    if budget_hz <= 0:
        raise ValueError("budget_hz must be positive")
    if margin <= 0:
        raise ValueError("margin must be positive")
    if reserved_hz < 0:
        raise ValueError("reserved_hz must not be negative")
    limit = margin * budget_hz

    def tx_only(klass: AcquisitionClass) -> bool:
        return ACQUISITION_CLASS_TABLE[klass].polled_in is AcquisitionPhase.TRANSMIT

    live = {
        klass: count
        for klass, count in counts_per_class.items()
        if tx or not tx_only(klass)
    }
    cadence = {
        klass: (
            ACQUISITION_CLASS_TABLE[klass].ceiling_cadence_seconds
            if tx and ACQUISITION_CLASS_TABLE[klass].held_at_ceiling_in_tx
            else ACQUISITION_CLASS_TABLE[klass].nominal_cadence_seconds
        )
        for klass in live
    }

    def demand() -> float:
        return reserved_hz + sum(
            count / cadence[klass] for klass, count in live.items()
        )

    # Lowest rank first: the table iterates high -> low.
    for klass in reversed(tuple(ACQUISITION_CLASS_TABLE)):
        if klass not in live or demand() <= limit or reserved_hz >= limit:
            continue
        # Queries per second this class must still contribute for the
        # total to close on the limit.
        need = live[klass] / cadence[klass] - (demand() - limit)
        ceiling = ACQUISITION_CLASS_TABLE[klass].ceiling_cadence_seconds
        cadence[klass] = ceiling if need <= 0 else min(ceiling, live[klass] / need)
    settled = demand()
    return BudgetFit(
        effective_cadence_seconds=cadence,
        demand_hz=settled,
        fits=settled <= limit + _FIT_EPSILON,
    )


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


class AvailabilityOperator(StrEnum):
    """Comparison an availability clause applies to its source field."""

    IN = "in"
    NOT_IN = "not_in"
    MIN = "min"
    MAX = "max"
    EQUALS = "equals"


_SEQUENCE_AVAILABILITY_OPERATORS = frozenset(
    {AvailabilityOperator.IN, AvailabilityOperator.NOT_IN}
)
_NUMERIC_AVAILABILITY_OPERATORS = frozenset(
    {AvailabilityOperator.MIN, AvailabilityOperator.MAX}
)


@dataclass(frozen=True, slots=True)
class AvailabilityClause:
    """One condition on another field's current value."""

    field: FieldPath
    operator: AvailabilityOperator | str
    value: Any

    def __post_init__(self) -> None:
        operator = AvailabilityOperator(str(self.operator))
        value = self.value
        if operator in _SEQUENCE_AVAILABILITY_OPERATORS:
            if isinstance(value, str) or not isinstance(value, Sequence):
                raise ValueError(f"{operator.value} must be a sequence of values")
            value = tuple(value)
        elif operator in _NUMERIC_AVAILABILITY_OPERATORS:
            value = _strict_float(value, label=operator.value)
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "value", value)

    def to_dict(self) -> dict[str, Any]:
        operator = AvailabilityOperator(str(self.operator))
        return {
            "field": str(self.field),
            "operator": operator.value,
            "value": (
                list(self.value)
                if operator in _SEQUENCE_AVAILABILITY_OPERATORS
                else self.value
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AvailabilityClause:
        _reject_unknown_keys(
            value,
            allowed=frozenset({"field", "operator", "value"}),
            label="availability clause",
        )
        return cls(
            field=FieldPath.parse(str(value["field"])),
            operator=AvailabilityOperator(str(value["operator"])),
            value=value["value"],
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
    #: MOR-1485: cadence-poll membership that only fires while the backend
    #: has observed PTT true (e.g. TX/PA meters that read meaningfully only
    #: during transmit). Honored by ``AcquisitionScheduler.due_requests``'s
    #: ``tx_active`` gate, not by ``ensure_fresh`` (an explicit caller-
    #: triggered read is never blocked by this flag). Inert when
    #: ``cadence_seconds`` is ``None`` (nothing left for it to gate).
    tx_only: bool = False
    #: Declared conditions, all of which must hold, for this field to exist
    #: on the radio in its current mode, band or state.
    available_when: tuple[AvailabilityClause, ...] = ()

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
        object.__setattr__(
            self,
            "tx_only",
            _strict_bool(self.tx_only, label="tx_only"),
        )
        clauses = tuple(self.available_when)
        for clause in clauses:
            if not isinstance(clause, AvailabilityClause):
                raise ValueError("available_when must hold AvailabilityClause entries")
        object.__setattr__(self, "available_when", clauses)

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
            "txOnly": self.tx_only,
            "availableWhen": [clause.to_dict() for clause in self.available_when],
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
                    "txOnly",
                    "availableWhen",
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
            tx_only=bool(value.get("txOnly", False)),
            available_when=tuple(
                AvailabilityClause.from_dict(clause)
                for clause in value.get("availableWhen", ())
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
    startup_required: bool = True
    supported_controls: Sequence[str] | None = ()
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
        startup_required = _strict_bool(
            self.startup_required,
            label="startupRequired",
        )
        controls = (
            ()
            if self.supported_controls is None
            else _strict_string_sequence(
                self.supported_controls,
                label="supported_controls",
            )
        )
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
        object.__setattr__(self, "startup_required", startup_required)
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
            "startupRequired": self.startup_required,
            "supportedControls": list(self.supported_controls or ()),
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
                    "startupRequired",
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
            startup_required=value.get("startupRequired", True),
            supported_controls=_strict_string_sequence(
                value.get("supportedControls", ()),
                label="supportedControls",
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
    #: Path-indexed view of :attr:`capabilities` for O(1) resolution;
    #: derived in ``__post_init__``, never passed, compared, or serialized.
    _capabilities_by_path: dict[FieldPath, FieldCapability] = field(
        init=False,
        compare=False,
        repr=False,
        default_factory=dict,
    )

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
        object.__setattr__(self, "_capabilities_by_path", by_path)

    def capability_for(self, path: FieldPath) -> FieldCapability:
        capability = self._capabilities_by_path.get(path)
        if capability is not None:
            return capability
        return FieldCapability(
            path=path,
            availability=FieldAvailability.UNKNOWN,
            diagnostic=f"{path}: missing capability metadata",
        )

    def policy_for(self, path: FieldPath) -> AcquisitionPolicy:
        """Effective acquisition policy for one path (MOR-2574 step 2).

        An explicit :attr:`field_policies` entry wins, unchanged. A path
        with no entry of its own but a pollable capability resolves to its
        acquisition class's policy — the same class
        :func:`~rigplane.core.state_pipeline_contracts.acquisition_class_for_path`
        stamps on the registry's ``FieldSpec.acquisition_class`` — via
        :func:`acquisition_policy_for_class`. Everything else (paths with
        no capability, or none that can poll) keeps :attr:`default_policy`.
        """

        declared = self.field_policies.get(path)
        if declared is not None:
            return declared
        capability = self._capabilities_by_path.get(path)
        if capability is not None and capability.can_poll:
            return acquisition_policy_for_class(acquisition_class_for_path(path))
        return self.default_policy

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
        provider = value["provider"]
        if not isinstance(provider, str):
            raise ValueError("provider must be a string")
        return cls(
            provider=provider,
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
