"""Ambiguous operating-frequency ingress must not overwrite the RX frequency."""

from dataclasses import replace

import pytest

from rigplane.core.state_pipeline_contracts import FieldPath, Observation, SourceMetadata
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.core.tx_observation import OBSERVED_PTT_PATH, ObservedPtt
from rigplane.core.tx_target import KnownTxTarget, UnknownTxTarget
from rigplane.core.types import CivFrame, bcd_encode
from rigplane.radio_state import RadioState
from rigplane.runtime.radio import IcomRadio
from rigplane.testing.timing import VirtualClock


def _observe(radio: IcomRadio, path: FieldPath, value: object) -> None:
    radio._state_store.apply(
        Observation(
            path=path,
            value=value,
            source=SourceMetadata(source="poll_response", provider="icom_civ"),
            timestamp_monotonic=radio._state_store.snapshot().generated_at_monotonic,
            max_age=5.0,
            provider_generation=radio._state_store.provider_generation,
        )
    )


def _radio(
    model: str,
    clock: VirtualClock,
    *,
    ptt: ObservedPtt = ObservedPtt.ON,
    split: bool = True,
) -> IcomRadio:
    radio = IcomRadio("127.0.0.1", model=model)
    radio._state_store = StateStore(freshness_clock=FreshnessClock(start=clock.now))
    radio._radio_state = RadioState()
    radio._radio_state.main.freq = 14_074_000
    radio._state_cache.update_freq(14_074_000)
    _observe(radio, FieldPath.active_slot("0"), "A")
    _observe(radio, FieldPath.active("0", "freq_mode", "freq_hz"), 14_074_000)
    _observe(radio, FieldPath.unselected("0", "freq_mode", "freq_hz"), 14_075_000)
    radio._radio_state.main.vfo_b = replace(
        radio._radio_state.main.vfo_b, freq_hz=14_075_000
    )
    _observe(radio, OBSERVED_PTT_PATH, ptt)
    _observe(radio, FieldPath.global_("tx_state", "split"), split)
    return radio


def _frame(radio: IcomRadio, command: int, *, selector: int | None = None) -> CivFrame:
    data = bcd_encode(14_076_000)
    if selector is not None:
        data = bytes([selector]) + data
    return CivFrame(0xE0, radio.profile.civ_addr, command, None, data)


@pytest.mark.parametrize("model", ["IC-7300", "IC-705", "IC-7610"])
@pytest.mark.parametrize("command", [0x00, 0x03])
@pytest.mark.parametrize("contradictory", [False, True])
async def test_split_tx_operating_frequency_drops_even_with_known_target(
    model: str, command: int, contradictory: bool, virtual_clock: VirtualClock
) -> None:
    radio = _radio(model, virtual_clock)
    target = KnownTxTarget(
        receiver="SUB" if model == "IC-7610" and not contradictory else "MAIN",
        slot=None if model == "IC-7610" else "A" if contradictory else "B",
        frequency_hz=14_075_000,
    )
    _observe(radio, FieldPath.global_("tx_state", "tx_target"), target)
    observations = radio._civ_runtime._observations_from_frame(_frame(radio, command))
    assert observations == ()
    radio._civ_runtime._update_state_cache_from_frame(_frame(radio, command))
    assert (
        radio._state_store.snapshot()
        .field(FieldPath.unselected("0", "freq_mode", "freq_hz"))
        .value
        == 14_075_000
    )
    assert (
        radio._state_store.snapshot()
        .field(FieldPath.active("0", "freq_mode", "freq_hz"))
        .value
        == 14_074_000
    )
    assert radio._radio_state.main.freq == 14_074_000
    assert radio._state_cache.freq == 14_074_000


@pytest.mark.parametrize("model", ["IC-7300", "IC-705"])
@pytest.mark.parametrize("command", [0x00, 0x03])
async def test_split_tx_ambiguous_operating_frequency_leaves_rx_unchanged(
    model: str, command: int, virtual_clock: VirtualClock
) -> None:
    radio = _radio(model, virtual_clock)
    _observe(
        radio,
        FieldPath.global_("tx_state", "tx_target"),
        UnknownTxTarget("not-observed"),
    )
    frame = _frame(radio, command)
    assert radio._civ_runtime._observations_from_frame(frame) == ()
    radio._civ_runtime._update_state_cache_from_frame(frame)
    assert radio._radio_state.main.freq == 14_074_000
    assert radio._state_cache.freq == 14_074_000


@pytest.mark.parametrize(
    "ptt,split",
    [(ObservedPtt.OFF, True), (ObservedPtt.ON, False), (ObservedPtt.UNKNOWN, False)],
)
async def test_operating_frequency_outside_split_tx_keeps_active_path(
    ptt: ObservedPtt, split: bool, virtual_clock: VirtualClock
) -> None:
    radio = _radio("IC-7300", virtual_clock, ptt=ptt, split=split)
    observations = radio._civ_runtime._observations_from_frame(_frame(radio, 0x03))
    assert observations[0].path == FieldPath.active("0", "freq_mode", "freq_hz")


@pytest.mark.parametrize("selector", [0, 1])
async def test_split_tx_explicit_frequency_selector_keeps_profile_contract(
    selector: int, virtual_clock: VirtualClock
) -> None:
    radio = _radio("IC-7300", virtual_clock)
    observations = radio._civ_runtime._observations_from_frame(
        _frame(radio, 0x25, selector=selector)
    )
    expected = FieldPath.active if selector == 0 else FieldPath.unselected
    assert observations[0].path == expected("0", "freq_mode", "freq_hz")


@pytest.mark.parametrize("model", ["IC-7300", "IC-705", "IC-7610"])
@pytest.mark.parametrize("command", [0x00, 0x03])
@pytest.mark.parametrize(
    "state",
    ["missing_split", "stale_split", "missing_ptt", "stale_ptt", "unknown_ptt"],
)
async def test_on_evidence_with_ambiguous_counterpart_preserves_rx(
    model: str, command: int, state: str, virtual_clock: VirtualClock
) -> None:
    radio = _radio(model, virtual_clock)
    split_path = FieldPath.global_("tx_state", "split")
    path = split_path if state.endswith("split") else OBSERVED_PTT_PATH
    if state.startswith("missing"):
        radio._state_store.discard((path,))
    elif state.startswith("stale"):
        now = radio._state_store.snapshot().generated_at_monotonic
        radio._state_store.mark_stale_due(now=now + 6.0)
        other_path = OBSERVED_PTT_PATH if path == split_path else split_path
        _observe(radio, other_path, ObservedPtt.ON if path == split_path else True)
    else:
        _observe(radio, OBSERVED_PTT_PATH, ObservedPtt.UNKNOWN)
    frame = _frame(radio, command)
    assert radio._civ_runtime._observations_from_frame(frame) == ()
    radio._civ_runtime._update_state_cache_from_frame(frame)
    assert (
        radio._state_store.snapshot()
        .field(FieldPath.active("0", "freq_mode", "freq_hz"))
        .value
        == 14_074_000
    )
    assert radio._radio_state.main.freq == 14_074_000
    assert radio._state_cache.freq == 14_074_000


async def test_operating_frequency_before_on_evidence_keeps_startup_path(
    virtual_clock: VirtualClock,
) -> None:
    radio = _radio("IC-7300", virtual_clock)
    radio._state_store.discard(
        (OBSERVED_PTT_PATH, FieldPath.global_("tx_state", "split"))
    )
    observations = radio._civ_runtime._observations_from_frame(_frame(radio, 0x03))
    assert observations[0].path == FieldPath.active("0", "freq_mode", "freq_hz")


@pytest.mark.parametrize("command", [0x00, 0x03])
async def test_receiver_tagged_frequency_keeps_explicit_target(
    command: int, virtual_clock: VirtualClock
) -> None:
    radio = _radio("IC-7610", virtual_clock)
    frame = replace(_frame(radio, command), receiver=1)
    observations = radio._civ_runtime._observations_from_frame(frame)
    assert observations[0].path == FieldPath.active("1", "freq_mode", "freq_hz")
