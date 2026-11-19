"""Canonical transmit-state observation contract."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import FreshnessState, StateSnapshot


logger = logging.getLogger(__name__)

TX_READ_DEADLINE_SECONDS: float = 0.3
TX_METER_LOG_INTERVAL_SECONDS: float = 2.0

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


SWR_METER_PATH = FieldPath.global_("meters", "swr")
POWER_METER_PATH = FieldPath.global_("meters", "power")


@dataclass(frozen=True, slots=True)
class MeterSample:
    """One fresh meter value; ``calibrated`` says ratio/watts versus raw."""

    value: float
    calibrated: bool

    def __str__(self) -> str:
        return f"{self.value:g}{'' if self.calibrated else '(raw)'}"


@dataclass(frozen=True, slots=True)
class TxMeterReading:
    """Fresh SWR and power meter samples; ``None`` means no fresh reading."""

    swr: MeterSample | None = None
    power: MeterSample | None = None


def _project_meter(snapshot: StateSnapshot, path: FieldPath) -> MeterSample | None:
    try:
        field = snapshot.field(path)
    except KeyError:
        return None
    value = field.value
    if (
        field.freshness is not FreshnessState.FRESH
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
    ):
        return None
    return MeterSample(float(value), "calibrated" in field.quality)


def project_tx_meters(snapshot: StateSnapshot) -> TxMeterReading:
    """Project the fresh SWR and power meters every backend writes to the store."""

    return TxMeterReading(
        swr=_project_meter(snapshot, SWR_METER_PATH),
        power=_project_meter(snapshot, POWER_METER_PATH),
    )


def _peak(
    current: MeterSample | None, sample: MeterSample | None
) -> MeterSample | None:
    if sample is None or (current is not None and current.value >= sample.value):
        return current
    return sample


class TxMeterLog:
    """Log SWR and TX power per transmission: start, ~every 2 s, peak at the end.

    Fed from each state broadcast with the projected PTT and meters, so it adds
    no radio traffic: the meters are the ones the poller already reads while
    keyed.
    """

    def __init__(self, interval: float = TX_METER_LOG_INTERVAL_SECONDS) -> None:
        self._interval = interval
        self._keyed_at: float | None = None
        self._last_logged_at = 0.0
        self._peak_swr: MeterSample | None = None
        self._peak_power: MeterSample | None = None

    def observe(self, ptt: ObservedPtt, reading: TxMeterReading, now: float) -> None:
        if ptt is ObservedPtt.ON:
            if self._keyed_at is None:
                self._keyed_at = self._last_logged_at = now
                self._peak_swr = self._peak_power = None
                self._record("tx.meters.start", reading, 0.0)
            self._peak_swr = _peak(self._peak_swr, reading.swr)
            self._peak_power = _peak(self._peak_power, reading.power)
            if now - self._last_logged_at >= self._interval:
                self._last_logged_at = now
                self._record("tx.meters", reading, now - self._keyed_at)
        elif self._keyed_at is not None:
            summary = TxMeterReading(self._peak_swr, self._peak_power)
            self._record("tx.meters.end_peak", summary, now - self._keyed_at)
            self._keyed_at = None

    @staticmethod
    def _record(event: str, reading: TxMeterReading, elapsed: float) -> None:
        logger.info(
            "%s: elapsed_s=%.1f swr=%s power=%s",
            event,
            elapsed,
            reading.swr,
            reading.power,
        )
