"""Canonical transmit-state observation contract."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from rigplane.core.state_pipeline_contracts import FieldPath, Observation
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
    """One meter value; ``calibrated`` says ratio/watts versus raw.

    ``expires_at`` is the observation deadline (``timestamp + max_age``);
    ``None`` means the producer set no deadline.
    """

    value: float
    calibrated: bool
    expires_at: float | None = None

    def __str__(self) -> str:
        return f"{self.value:g}{'' if self.calibrated else '(raw)'}"

    def current_at(self, now: float) -> MeterSample | None:
        if self.expires_at is not None and now >= self.expires_at:
            return None
        return self


def meter_sample_from(observation: Observation) -> MeterSample | None:
    """Type a SWR/power observation, or ``None`` for a non-numeric value."""

    value = observation.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not isfinite(value):
        return None
    expires_at = (
        None
        if observation.max_age is None
        else observation.timestamp_monotonic + observation.max_age
    )
    return MeterSample(float(value), "calibrated" in observation.quality, expires_at)


def _peak(
    current: MeterSample | None, sample: MeterSample | None
) -> MeterSample | None:
    if sample is None or (current is not None and current.value >= sample.value):
        return current
    return sample


class TxMeterLog:
    """Log SWR and TX power per transmission: start, ~every 2 s, peak at the end.

    Subscribed to the state store (``StateStore.subscribe_observations``), so it
    sees every accepted PTT/SWR/power sample before any broadcast throttling and
    adds no radio traffic: the meters are the ones the poller already reads
    while keyed. A transmission starts on observed ON and ends on observed OFF;
    a temporary UNKNOWN keeps it, with its accumulated peaks. A periodic line is
    written when a meter sample arrives and two seconds have passed, so it needs
    no timer. A reading past its observation deadline is logged as ``None``.
    """

    def __init__(self, interval: float = TX_METER_LOG_INTERVAL_SECONDS) -> None:
        self._interval = interval
        self._swr: MeterSample | None = None
        self._power: MeterSample | None = None
        self._keyed_at: float | None = None
        self._last_logged_at = 0.0
        self._peak_swr: MeterSample | None = None
        self._peak_power: MeterSample | None = None

    def on_observation(self, observation: Observation) -> None:
        path, now = observation.path, observation.timestamp_monotonic
        if path == OBSERVED_PTT_PATH:
            self._on_ptt(observation.value, now)
        elif path in (SWR_METER_PATH, POWER_METER_PATH):
            sample = meter_sample_from(observation)
            if path == SWR_METER_PATH:
                self._swr = sample
            else:
                self._power = sample
            self._on_meter(sample, path, now)

    def _on_ptt(self, value: object, now: float) -> None:
        ptt = normalize_observed_ptt(value)
        if ptt is ObservedPtt.ON and self._keyed_at is None:
            self._keyed_at = self._last_logged_at = now
            self._peak_swr = self._peak_power = None
            self._record("tx.meters.start", self._swr, self._power, now, now)
        elif ptt is ObservedPtt.OFF and self._keyed_at is not None:
            self._record(
                "tx.meters.end_peak",
                self._peak_swr,
                self._peak_power,
                now,
                self._keyed_at,
            )
            self._keyed_at = None

    def _on_meter(
        self, sample: MeterSample | None, path: FieldPath, now: float
    ) -> None:
        if self._keyed_at is None:
            return
        if path == SWR_METER_PATH:
            self._peak_swr = _peak(self._peak_swr, sample)
        else:
            self._peak_power = _peak(self._peak_power, sample)
        if now - self._last_logged_at >= self._interval:
            self._last_logged_at = now
            self._record("tx.meters", self._swr, self._power, now, self._keyed_at)

    @staticmethod
    def _record(
        event: str,
        swr: MeterSample | None,
        power: MeterSample | None,
        now: float,
        since: float,
    ) -> None:
        if event != "tx.meters.end_peak":
            swr = swr.current_at(now) if swr is not None else None
            power = power.current_at(now) if power is not None else None
        logger.info(
            "%s: elapsed_s=%.1f swr=%s power=%s", event, now - since, swr, power
        )
