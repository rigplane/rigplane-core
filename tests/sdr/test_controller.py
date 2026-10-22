"""Tests for rigplane.sdr.controller — SdrScopeController (MOR-3156)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from rigplane.scope import ScopeFrame
from rigplane.sdr import FakeIqSource, IqBlock, IqScopeSink, IqSource, SdrConfig
from rigplane.sdr.controller import (
    DEFAULT_SPAN_DIVISOR,
    RETUNE_DEBOUNCE_S,
    SdrScopeController,
    TX_HOLD_S,
)

SAMPLE_RATE_HZ = 2_400_000
QUARTER_SR_HZ = SAMPLE_RATE_HZ // 4
SPAN_HZ = SAMPLE_RATE_HZ // DEFAULT_SPAN_DIVISOR


class FakeClock:
    """Monotonic clock advanced by hand."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CountingSource(FakeIqSource):
    """FakeIqSource that records every set_center_freq call."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.retune_calls: list[int] = []

    def set_center_freq(self, hz: int) -> None:
        self.retune_calls.append(int(hz))
        super().set_center_freq(hz)


class RecordingSink:
    """IqScopeSink double that records every call in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int | bool | None]] = []
        self.frame_callback: Callable[[ScopeFrame], None] | None = None

    def feed(self, block: IqBlock) -> None:
        self.calls.append(("feed", len(block.samples)))

    def set_view_center(self, hz: int) -> None:
        self.calls.append(("view", hz))

    def set_span(self, hz: int | None) -> None:
        self.calls.append(("span", hz))

    def set_tx_active(self, active: bool) -> None:
        self.calls.append(("tx", active))

    def on_frame(self, callback: Callable[[ScopeFrame], None] | None) -> None:
        self.frame_callback = callback

    def of(self, name: str) -> list[int | bool | None]:
        return [value for event, value in self.calls if event == name]


def make_controller(
    *,
    frequency_range: tuple[int, int] = (100_000, 6_000_000_000),
    config: SdrConfig | None = None,
    tx_hold_s: float = TX_HOLD_S,
) -> tuple[SdrScopeController, CountingSource, RecordingSink, FakeClock]:
    clock = FakeClock()
    source = CountingSource(
        sample_rate_hz=SAMPLE_RATE_HZ,
        frequency_range=frequency_range,
    )
    sink = RecordingSink()
    controller = SdrScopeController(
        source,
        sink,
        config if config is not None else SdrConfig(device_args="driver=rtlsdr"),
        clock,
        tx_hold_s=tx_hold_s,
    )
    return controller, source, sink, clock


def test_construction_sends_default_span_to_sink() -> None:
    controller, source, sink, _clock = make_controller()

    assert isinstance(source, IqSource)
    assert isinstance(sink, IqScopeSink)
    assert controller.span_hz == SAMPLE_RATE_HZ // DEFAULT_SPAN_DIVISOR
    assert sink.calls == [("span", controller.span_hz)]


def test_construction_sends_configured_span_to_sink() -> None:
    config = SdrConfig(device_args="driver=rtlsdr", span_hz=300_000)
    controller, _source, sink, _clock = make_controller(config=config)

    assert controller.span_hz == 300_000
    assert sink.calls == [("span", 300_000)]


@pytest.mark.parametrize(
    ("target_hz", "expect_retune"),
    [
        (14_026_000, False),
        (13_974_000, False),
        (14_060_000, False),
        (14_061_000, True),
        (12_740_000, False),
        (12_600_000, True),
        (21_000_000, True),
    ],
)
def test_vfo_move_view_shift_vs_retune(target_hz: int, expect_retune: bool) -> None:
    controller, source, sink, clock = make_controller()
    controller.on_radio_state(14_000_000, False)

    assert source.retune_calls == [13_400_000]
    sink.calls.clear()
    clock.advance(1.0)
    controller.on_radio_state(target_hz, False)

    assert (source.center_freq_hz != 13_400_000) is expect_retune
    if expect_retune:
        assert source.retune_calls == [13_400_000, target_hz - QUARTER_SR_HZ]
    assert sink.calls == [("view", target_hz)]
    assert controller.retune_pending is False


def test_band_change_retunes_with_quarter_placement() -> None:
    controller, source, sink, clock = make_controller()
    controller.on_radio_state(14_074_000, False)

    clock.advance(1.0)
    controller.on_radio_state(21_225_000, False)

    assert source.retune_calls == [13_474_000, 20_625_000]
    assert source.center_freq_hz == 20_625_000
    assert sink.of("view") == [14_074_000, 21_225_000]


def test_rapid_dial_spin_debounces_to_one_retune() -> None:
    controller, source, sink, clock = make_controller()
    controller.on_radio_state(14_000_000, False)

    for delay, spin_hz in [
        (0.010, 14_500_000),
        (0.010, 15_000_000),
        (0.010, 15_300_000),
    ]:
        clock.advance(delay)
        controller.on_radio_state(spin_hz, False)

    assert source.retune_calls == [13_400_000]
    assert controller.retune_pending is True
    assert sink.of("view") == [14_000_000]

    clock.advance(RETUNE_DEBOUNCE_S - 0.030 - 0.001)
    controller.tick()
    assert source.retune_calls == [13_400_000]

    clock.advance(0.002)
    controller.tick()
    assert source.retune_calls == [13_400_000, 14_700_000]
    assert sink.of("view") == [14_000_000, 15_300_000]
    assert controller.retune_pending is False


def test_pending_retune_superseded_by_view_shift() -> None:
    controller, source, sink, clock = make_controller()
    controller.on_radio_state(14_000_000, False)

    clock.advance(0.020)
    controller.on_radio_state(14_500_000, False)
    assert controller.retune_pending is True

    clock.advance(0.020)
    controller.on_radio_state(14_030_000, False)
    assert controller.retune_pending is False
    assert source.retune_calls == [13_400_000]
    assert sink.of("view") == [14_000_000, 14_030_000]

    clock.advance(1.0)
    controller.tick()
    assert source.retune_calls == [13_400_000]
    assert sink.of("view") == [14_000_000, 14_030_000]


def test_debounced_retune_applies_on_next_radio_state() -> None:
    controller, source, sink, clock = make_controller()
    controller.on_radio_state(14_000_000, False)

    clock.advance(0.020)
    controller.on_radio_state(14_500_000, False)
    assert source.retune_calls == [13_400_000]

    clock.advance(RETUNE_DEBOUNCE_S)
    controller.on_radio_state(14_500_000, False)

    assert source.retune_calls == [13_400_000, 13_900_000]
    assert sink.of("view") == [14_000_000, 14_500_000]


@pytest.mark.parametrize("out_of_range_hz", [31_000_000, 50_000])
def test_out_of_range_frequency_never_retunes_or_moves_view(
    out_of_range_hz: int,
) -> None:
    controller, source, sink, clock = make_controller(
        frequency_range=(100_000, 30_000_000)
    )
    controller.on_radio_state(14_000_000, False)
    assert controller.frequency_in_range is True

    sink.calls.clear()
    clock.advance(1.0)
    controller.on_radio_state(out_of_range_hz, False)
    assert controller.frequency_in_range is False
    assert source.retune_calls == [13_400_000]
    assert sink.calls == []

    clock.advance(1.0)
    controller.tick()
    assert controller.frequency_in_range is False
    assert source.retune_calls == [13_400_000]
    assert sink.calls == []


def test_frequency_back_in_range_recovers() -> None:
    controller, source, sink, clock = make_controller(
        frequency_range=(100_000, 30_000_000)
    )
    controller.on_radio_state(31_000_000, False)
    assert controller.frequency_in_range is False

    clock.advance(1.0)
    controller.on_radio_state(14_500_000, False)

    assert controller.frequency_in_range is True
    assert source.retune_calls == [13_900_000]
    assert sink.of("view") == [14_500_000]


def test_band_edge_retune_clamps_center_to_source_range() -> None:
    controller, source, sink, _clock = make_controller(
        frequency_range=(20_000_000, 30_000_000)
    )
    controller.on_radio_state(20_100_000, False)

    assert source.retune_calls == [20_000_000]
    assert source.center_freq_hz == 20_000_000
    assert sink.of("view") == [20_100_000]


def test_tx_rising_edge_freezes_immediately() -> None:
    controller, _source, sink, _clock = make_controller()
    controller.on_radio_state(14_000_000, True)

    assert sink.calls == [("span", SPAN_HZ), ("tx", True), ("view", 14_000_000)]


def test_tx_release_holds_then_applies_on_tick() -> None:
    controller, _source, sink, clock = make_controller()
    controller.on_radio_state(14_000_000, True)
    clock.advance(10.0)
    controller.on_radio_state(14_000_000, False)

    sink.calls.clear()
    clock.advance(TX_HOLD_S - 0.001)
    controller.tick()
    assert sink.calls == []

    clock.advance(0.002)
    controller.tick()
    assert sink.calls == [("tx", False)]


def test_tx_release_applies_on_next_radio_state() -> None:
    controller, _source, sink, clock = make_controller()
    controller.on_radio_state(14_000_000, True)
    clock.advance(10.0)
    controller.on_radio_state(14_000_000, False)

    sink.calls.clear()
    clock.advance(TX_HOLD_S + 0.1)
    controller.on_radio_state(14_000_000, False)
    assert sink.calls == [("tx", False)]


def test_tx_rekey_within_hold_never_releases() -> None:
    controller, _source, sink, clock = make_controller()
    controller.on_radio_state(14_000_000, True)
    clock.advance(1.0)
    controller.on_radio_state(14_000_000, False)
    clock.advance(0.100)
    controller.on_radio_state(14_000_000, True)

    clock.advance(0.400)
    controller.tick()
    assert sink.of("tx") == [True]

    clock.advance(0.100)
    controller.on_radio_state(14_000_000, False)
    clock.advance(TX_HOLD_S + 0.001)
    controller.tick()
    assert sink.of("tx") == [True, False]


def test_tx_hold_is_configurable() -> None:
    controller, _source, sink, clock = make_controller(tx_hold_s=1.0)
    controller.on_radio_state(14_000_000, True)
    clock.advance(1.0)
    controller.on_radio_state(14_000_000, False)

    clock.advance(0.999)
    controller.tick()
    assert sink.of("tx") == [True]

    clock.advance(0.002)
    controller.tick()
    assert sink.of("tx") == [True, False]


def test_none_frequency_leaves_tuning_untouched() -> None:
    controller, source, sink, clock = make_controller()
    controller.tick()
    assert sink.calls == [("span", SPAN_HZ)]

    controller.on_radio_state(14_000_000, False)
    sink.calls.clear()

    clock.advance(0.010)
    controller.on_radio_state(None, False)
    controller.tick()

    assert sink.calls == []
    assert source.retune_calls == [13_400_000]
    assert controller.frequency_in_range is True

    controller.on_radio_state(None, True)
    assert sink.of("tx") == [True]


@pytest.mark.parametrize(
    ("offset_hz", "radio_hz", "expected_center_hz", "expected_view_hz"),
    [
        (9_000_000, 14_074_000, 22_474_000, 23_074_000),
        (-8_215_000, 14_074_000, 5_259_000, 5_859_000),
        (0, 14_074_000, 13_474_000, 14_074_000),
    ],
)
def test_if_tap_offset_shifts_sdr_rf_space(
    offset_hz: int, radio_hz: int, expected_center_hz: int, expected_view_hz: int
) -> None:
    config = SdrConfig(device_args="driver=rtlsdr", freq_offset_hz=offset_hz)
    controller, source, sink, _clock = make_controller(config=config)

    controller.on_radio_state(radio_hz, False)

    assert source.retune_calls == [expected_center_hz]
    assert sink.of("view") == [expected_view_hz]


def test_controller_source_has_no_threads_or_io() -> None:
    source = (
        Path(__file__)
        .parents[2]
        .joinpath("src", "rigplane", "sdr", "controller.py")
        .read_text()
    )

    for banned in ("threading", "Thread(", "asyncio", "socket", "subprocess"):
        assert banned not in source
