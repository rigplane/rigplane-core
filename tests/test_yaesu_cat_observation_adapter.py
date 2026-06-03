"""Observation adapter coverage for the Yaesu CAT backend."""
# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.profiles import get_radio_profile

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter


def _clock() -> float:
    return 123.456


def _make_radio() -> MagicMock:
    radio = MagicMock()
    radio.capabilities = {
        "dual_rx",
        "af_level",
        "rf_gain",
        "squelch",
        "meters",
        "filter_width",
        "tx",
    }
    radio.get_freq = AsyncMock(
        side_effect=lambda receiver=0: 14_074_000 if receiver == 0 else 7_074_000
    )
    radio.get_mode = AsyncMock(
        side_effect=lambda receiver=0: ("USB", None) if receiver == 0 else ("LSB", None)
    )
    radio.get_ptt = AsyncMock(return_value=False)
    radio.get_af_level = AsyncMock(
        side_effect=lambda receiver=0: 128 if receiver == 0 else 64
    )
    radio.get_rf_gain = AsyncMock(
        side_effect=lambda receiver=0: 180 if receiver == 0 else 90
    )
    radio.get_squelch = AsyncMock(
        side_effect=lambda receiver=0: 12 if receiver == 0 else 8
    )
    return radio


def _profile_state_acquisition() -> RadioAcquisitionProfile:
    profile = get_radio_profile("FTX-1")
    assert profile.state_acquisition is not None
    return profile.state_acquisition


@pytest.mark.asyncio
async def test_medium_poll_emits_frequency_mode_and_ptt_observations() -> None:
    radio = _make_radio()
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_medium()

    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.active.freq_mode.freq_hz", 14_074_000),
        ("receiver.main.active.freq_mode.mode", "USB"),
        ("receiver.sub.active.freq_mode.freq_hz", 7_074_000),
        ("receiver.sub.active.freq_mode.mode", "LSB"),
        ("global.tx_state.ptt", False),
    ]
    assert {item.source.source for item in observations} == {"yaesu_poll_response"}
    assert {item.source.provider for item in observations} == {"yaesu_cat"}
    assert {item.source.transport for item in observations} == {"serial"}
    assert all(item.timestamp_monotonic == 123.456 for item in observations)
    assert all(item.max_age == 8.0 for item in observations)
    assert all(item.source.capability_id == str(item.path) for item in observations)


@pytest.mark.asyncio
async def test_slow_poll_emits_declared_control_observations_only() -> None:
    radio = _make_radio()
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()

    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.operator_controls.af_level", 128),
        ("receiver.main.operator_controls.rf_gain", 180),
        ("receiver.main.operator_controls.squelch", 12),
        ("receiver.sub.operator_controls.af_level", 64),
        ("receiver.sub.operator_controls.rf_gain", 90),
        ("receiver.sub.operator_controls.squelch", 8),
    ]
    assert all(item.source.source == "yaesu_poll_response" for item in observations)
    assert all(item.max_age == 120.0 for item in observations)
    assert radio.get_af_level.await_count == 2
    assert radio.get_rf_gain.await_count == 2
    assert radio.get_squelch.await_count == 2
