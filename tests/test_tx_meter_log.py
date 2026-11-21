"""MOR-4592: SWR and TX power reach the log at TX start, during TX and at TX end.

One shared path for every radio: ``TxMeterLog`` subscribes to the StateStore and
reads the neutral paths ``global.meters.swr`` / ``global.meters.power`` that the
Icom CI-V and Yaesu CAT backends both fill (calibrated when the profile ships a
table, raw otherwise), so it sees every sample before broadcast throttling.
"""

from __future__ import annotations

import logging

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.core.tx_observation import (
    OBSERVED_PTT_PATH,
    POWER_METER_PATH,
    SWR_METER_PATH,
    ObservedPtt,
    TxMeterLog,
)
from rigplane.profiles import get_radio_profile
from rigplane.runtime.meter_cal import interpolate_meter
from rigplane.web.server import WebConfig, WebServer

_SOURCE = SourceMetadata(source="poll_response", provider="test")
_LOGGER = "rigplane.core.tx_observation"
_CAL = ("confirmed", "calibrated")
_RAW = ("confirmed", "uncalibrated")


class _Bench:
    """A real StateStore on a manual clock with a TxMeterLog subscribed."""

    def __init__(self, store: StateStore | None = None) -> None:
        self.clock = FreshnessClock(start=100.0)
        self.store = store or StateStore(freshness_clock=self.clock)
        self.store.subscribe_observations(TxMeterLog().on_observation)

    def put(
        self,
        path: FieldPath,
        value: object,
        quality: tuple[str, ...] = _CAL,
        max_age: float | None = 2.0,
    ) -> None:
        self.store.apply_current(
            Observation(
                path=path,
                value=value,
                source=_SOURCE,
                timestamp_monotonic=self.clock.now(),
                quality=quality,
                max_age=max_age,
            )
        )

    def ptt(self, state: ObservedPtt) -> None:
        self.put(OBSERVED_PTT_PATH, state, ("confirmed",), 60.0)

    def swr(self, value: float, quality: tuple[str, ...] = _CAL) -> None:
        self.put(SWR_METER_PATH, value, quality)

    def power(self, value: float, quality: tuple[str, ...] = _CAL) -> None:
        self.put(POWER_METER_PATH, value, quality)


def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _LOGGER]


@pytest.fixture(autouse=True)
def _info(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)


@pytest.mark.parametrize("model", ["IC-7300", "IC-7610", "FTX-1"])
def test_logs_the_profile_scaled_value_for_each_radio(
    model: str, caplog: pytest.LogCaptureFixture
) -> None:
    # Icom CI-V and Yaesu CAT both scale through the profile's meter table with
    # interpolate_meter and publish the result with a "calibrated" quality.
    calibrations = get_radio_profile(model).meter_calibrations
    swr, swr_cal = interpolate_meter(255, calibrations, "swr")
    power, power_cal = interpolate_meter(120, calibrations, "power")
    assert swr_cal, model
    bench = _Bench()
    bench.ptt(ObservedPtt.ON)
    bench.swr(swr)
    bench.power(power, _CAL if power_cal else _RAW)
    bench.ptt(ObservedPtt.OFF)
    lines = _lines(caplog)
    assert f"swr={swr:g} power={power:g}" + ("" if power_cal else "(raw)") in lines[-1]


def test_logs_start_then_every_two_seconds_then_peak_at_end(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bench = _Bench()
    bench.ptt(ObservedPtt.OFF)
    bench.swr(1.1)
    bench.clock.advance(1.0)
    bench.swr(1.2)
    bench.power(90.0)
    bench.ptt(ObservedPtt.ON)  # start, sees the fresh RX-side values
    bench.clock.advance(1.0)
    bench.swr(3.5)  # peak, not due
    bench.clock.advance(1.0)
    bench.swr(1.5)  # +2 s: periodic
    bench.power(95.0)
    bench.clock.advance(0.5)
    bench.swr(1.4)  # not due
    bench.clock.advance(0.5)
    bench.ptt(ObservedPtt.OFF)

    lines = _lines(caplog)
    assert [line.split(":")[0] for line in lines] == [
        "tx.meters.start",
        "tx.meters",
        "tx.meters.end_peak",
    ]
    assert "elapsed_s=0.0 swr=1.2 power=90" in lines[0]
    assert "elapsed_s=2.0 swr=1.5 power=None" in lines[1]  # power expired
    assert "elapsed_s=3.0 swr=3.5 power=95" in lines[2]


def test_high_swr_between_broadcasts_survives_a_quick_dekey(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Store-level feed: ON, high SWR and OFF inside one 50 ms broadcast window.
    bench = _Bench()
    bench.ptt(ObservedPtt.ON)
    bench.swr(1.2)
    bench.clock.advance(0.01)
    bench.swr(3.5)
    bench.clock.advance(0.02)
    bench.ptt(ObservedPtt.OFF)
    assert "swr=3.5" in _lines(caplog)[-1]


def test_steady_reading_still_produces_periodic_lines(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bench = _Bench()
    bench.ptt(ObservedPtt.ON)
    for _ in range(5):
        bench.clock.advance(1.0)
        bench.swr(1.3)
    assert [line.split(":")[0] for line in _lines(caplog)] == [
        "tx.meters.start",
        "tx.meters",
        "tx.meters",
    ]


def test_unknown_ptt_keeps_the_transmission_and_its_peak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bench = _Bench()
    bench.ptt(ObservedPtt.ON)
    bench.swr(4.0)
    bench.clock.advance(0.2)
    bench.ptt(ObservedPtt.UNKNOWN)
    bench.clock.advance(0.2)
    bench.ptt(ObservedPtt.ON)
    bench.swr(1.5)
    bench.clock.advance(0.2)
    bench.ptt(ObservedPtt.OFF)
    lines = _lines(caplog)
    assert [line.split(":")[0] for line in lines] == [
        "tx.meters.start",
        "tx.meters.end_peak",
    ]
    assert "swr=4" in lines[1]


def test_expired_meter_is_logged_as_none_without_a_freshness_tick(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bench = _Bench()
    bench.ptt(ObservedPtt.ON)
    bench.swr(2.0)
    bench.clock.advance(2.5)
    bench.power(80.0)  # due line; the SWR deadline (2 s) has passed
    assert "swr=None power=80" in _lines(caplog)[-1]


def test_raw_value_is_marked(caplog: pytest.LogCaptureFixture) -> None:
    bench = _Bench()
    bench.ptt(ObservedPtt.ON)
    bench.swr(140.0, _RAW)
    bench.ptt(ObservedPtt.OFF)
    assert _lines(caplog)[-1].endswith("swr=140(raw) power=None")


def test_no_log_without_transmission(caplog: pytest.LogCaptureFixture) -> None:
    bench = _Bench()
    bench.ptt(ObservedPtt.OFF)
    bench.swr(2.0)
    bench.clock.advance(5.0)
    bench.swr(2.0)
    bench.ptt(ObservedPtt.UNKNOWN)
    assert _lines(caplog) == []


def test_web_server_subscribes_the_log_to_its_store(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = WebServer(None, WebConfig(host="127.0.0.1", port=0, radio_model="IC-7300"))
    bench = _Bench(server.command_state_store)
    bench.clock = FreshnessClock(start=1.0)  # observation stamps; the store is real
    bench.ptt(ObservedPtt.ON)
    bench.swr(2.5)
    bench.power(88, _RAW)
    bench.ptt(ObservedPtt.OFF)
    # One log from the server's own subscription, one from the bench's.
    assert sum("swr=2.5 power=88(raw)" in line for line in _lines(caplog)) == 2
