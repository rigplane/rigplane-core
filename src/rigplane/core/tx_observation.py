"""Canonical transmit-state observation contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import FreshnessState, StateSnapshot


TX_READ_DEADLINE_SECONDS: float = 0.3

RADIO_READBACK_SOURCES: frozenset[str] = frozenset(
    {"poll_response", "civ_unsolicited", "hamlib_response", "yaesu_poll_response"}
)

OBSERVED_PTT_PATH = FieldPath.global_("tx_state", "observed_ptt")


class ObservedPtt(StrEnum):
    """Canonical observed radio PTT state."""

    OFF = "off"
    ON = "on"
    UNKNOWN = "unknown"


def normalize_observed_ptt(value: object) -> ObservedPtt:
    """Normalize strict backend PTT evidence without truthy coercion."""

    if type(value) is ObservedPtt:
        return value
    if value is True:
        return ObservedPtt.ON
    if value is False:
        return ObservedPtt.OFF
    return ObservedPtt.UNKNOWN


def project_observed_ptt(snapshot: StateSnapshot) -> ObservedPtt:
    """Project current qualified evidence, or an honest unknown state."""

    try:
        field = snapshot.field(OBSERVED_PTT_PATH)
    except KeyError:
        return ObservedPtt.UNKNOWN

    timing = (
        snapshot.generated_at_monotonic,
        field.last_observed_monotonic,
        field.max_age,
    )
    if (
        field.freshness is not FreshnessState.FRESH
        or type(field.value) is not ObservedPtt
        or type(field.provider_generation) is not int
        or type(snapshot.provider_generation) is not int
        or field.provider_generation != snapshot.provider_generation
        or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not isfinite(value)
            for value in timing
        )
        or field.max_age is None
        or field.max_age <= 0
        or field.last_observed_monotonic < 0
        or snapshot.generated_at_monotonic < field.last_observed_monotonic
        or snapshot.generated_at_monotonic - field.last_observed_monotonic
        >= field.max_age
    ):
        return ObservedPtt.UNKNOWN
    return field.value


def legacy_ptt_bool(state: ObservedPtt) -> bool:
    """Return the deprecated lossy boolean projection of observed PTT."""

    return state is ObservedPtt.ON


@dataclass(frozen=True, slots=True)
class TxStateReading:
    """One solicited transmit-state observation."""

    value: bool | None
    attributed: str | None = None
    source: str | None = None
    verified_readback: bool = False
    failure: str | None = None
