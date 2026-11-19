"""MOR-4592: SWR and TX power reach the log at TX start, during TX and at TX end.

One shared path for every radio: the log reads the neutral store paths
``global.meters.swr`` / ``global.meters.power`` that the Icom CI-V and Yaesu CAT
backends both fill (calibrated when the profile ships a table, raw otherwise).
"""

from __future__ import annotations

import logging

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FieldSnapshot, FreshnessState, StateSnapshot
from rigplane.core.tx_observation import (
    OBSERVED_PTT_PATH,
    POWER_METER_PATH,
    SWR_METER_PATH,
    MeterSample,
    ObservedPtt,
    TxMeterLog,
    TxMeterReading,
    project_tx_meters,
)
from rigplane.profiles import get_radio_profile
from rigplane.runtime.meter_cal import interpolate_meter
from rigplane.web.server import WebConfig, WebServer

_SOURCE = SourceMetadata(source="poll_response", provider="test")
_LOGGER = "rigplane.core.tx_observation"


def _field(
    path: FieldPath,
    value: object,
    quality: tuple[str, ...] = ("confirmed", "calibrated"),
    freshness: FreshnessState = FreshnessState.FRESH,
) -> FieldSnapshot:
    return FieldSnapshot(
        path=path,
        value=value,
        freshness=freshness,
        last_observed_monotonic=1.0,
        max_age=2.0,
        source=_SOURCE,
        quality=quality,
    )


def _snapshot(*fields: FieldSnapshot) -> StateSnapshot:
    return StateSnapshot(
        state_revision=1,
        freshness_revision=0,
        observation_seq=1,
        generated_at_monotonic=2.0,
        fields=fields,
    )


def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _LOGGER]


@pytest.mark.parametrize("model", ["IC-7300", "IC-7610", "FTX-1"])
def test_projection_reports_the_profile_scaled_value_for_each_radio(model: str) -> None:
    # The producers (Icom CI-V and Yaesu CAT) both scale through the profile's
    # meter table with interpolate_meter; the log must carry that value.
    calibrations = get_radio_profile(model).meter_calibrations
    swr, swr_cal = interpolate_meter(255, calibrations, "swr")
    power, power_cal = interpolate_meter(120, calibrations, "power")
    assert swr_cal, model
    quality = lambda cal: ("confirmed", "calibrated" if cal else "uncalibrated")  # noqa: E731
    reading = project_tx_meters(
        _snapshot(
            _field(SWR_METER_PATH, swr, quality(swr_cal)),
            _field(POWER_METER_PATH, power, quality(power_cal)),
        )
    )
    assert reading == TxMeterReading(
        MeterSample(swr, True), MeterSample(power, power_cal)
    )


def test_projection_marks_raw_and_drops_stale_or_missing_meters() -> None:
    raw = _field(SWR_METER_PATH, 97, ("confirmed", "uncalibrated"))
    stale = _field(POWER_METER_PATH, 50, freshness=FreshnessState.STALE)
    assert project_tx_meters(_snapshot(raw, stale)) == TxMeterReading(
        MeterSample(97.0, False), None
    )
    assert project_tx_meters(StateSnapshot.empty()) == TxMeterReading()


def test_logs_start_then_every_two_seconds_then_peak_at_end(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    log = TxMeterLog()
    on, off = ObservedPtt.ON, ObservedPtt.OFF
    sample = lambda swr, power: TxMeterReading(  # noqa: E731
        MeterSample(swr, True), MeterSample(power, True)
    )
    log.observe(off, sample(1.0, 0.0), 9.0)
    log.observe(on, sample(1.2, 90.0), 10.0)  # start
    log.observe(on, sample(3.5, 100.0), 11.0)  # peak swr, not logged yet
    log.observe(on, sample(1.5, 95.0), 12.0)  # +2 s: periodic line
    log.observe(on, sample(1.4, 99.0), 13.0)  # not due
    log.observe(off, sample(1.0, 0.0), 14.5)  # end

    lines = _lines(caplog)
    assert [line.split(":")[0] for line in lines] == [
        "tx.meters.start",
        "tx.meters",
        "tx.meters.end_peak",
    ]
    assert "swr=1.2 power=90" in lines[0]
    assert "elapsed_s=2.0 swr=1.5 power=95" in lines[1]
    assert "elapsed_s=4.5 swr=3.5 power=100" in lines[2]


def test_raw_value_is_marked_and_missing_reading_is_logged_as_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    log = TxMeterLog()
    log.observe(ObservedPtt.ON, TxMeterReading(), 1.0)
    log.observe(ObservedPtt.ON, TxMeterReading(MeterSample(140.0, False), None), 2.0)
    log.observe(ObservedPtt.OFF, TxMeterReading(), 3.0)
    lines = _lines(caplog)
    assert lines[0].endswith("swr=None power=None")
    assert lines[1] == "tx.meters.end_peak: elapsed_s=2.0 swr=140(raw) power=None"


def test_no_log_without_transmission(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    log = TxMeterLog()
    for now in (1.0, 5.0, 9.0):
        log.observe(ObservedPtt.OFF, TxMeterReading(MeterSample(2.0, True), None), now)
    log.observe(ObservedPtt.UNKNOWN, TxMeterReading(), 10.0)
    assert _lines(caplog) == []


def test_web_state_broadcast_feeds_the_log_from_the_store(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    server = WebServer(None, WebConfig(host="127.0.0.1", port=0, radio_model="IC-7300"))

    def observe(path: FieldPath, value: object, quality: tuple[str, ...]) -> None:
        server.command_state_store.apply_current(
            Observation(
                path=path,
                value=value,
                source=_SOURCE,
                timestamp_monotonic=server.command_state_store.snapshot().generated_at_monotonic,
                max_age=60.0,
                quality=quality,
            )
        )

    observe(SWR_METER_PATH, 2.5, ("confirmed", "calibrated"))
    observe(POWER_METER_PATH, 88, ("confirmed", "uncalibrated"))
    observe(OBSERVED_PTT_PATH, ObservedPtt.ON, ("confirmed",))
    server._broadcast_state_update(force=True)
    observe(OBSERVED_PTT_PATH, ObservedPtt.OFF, ("confirmed",))
    server._broadcast_state_update(force=True)

    lines = _lines(caplog)
    assert lines[0].startswith("tx.meters.start:")
    assert lines[-1].startswith("tx.meters.end_peak:")
    assert "swr=2.5 power=88(raw)" in lines[-1]
