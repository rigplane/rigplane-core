"""Observation adapter coverage for the Yaesu CAT backend."""
# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.profiles import get_radio_profile
from rigplane.radio_state import RadioState

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
    radio.read_freq = AsyncMock(
        side_effect=lambda receiver=0: 14_074_000 if receiver == 0 else 7_074_000
    )
    radio.get_mode = AsyncMock(
        side_effect=lambda receiver=0: ("USB", None) if receiver == 0 else ("LSB", None)
    )
    radio.read_mode = AsyncMock(
        side_effect=lambda receiver=0: ("USB", None) if receiver == 0 else ("LSB", None)
    )
    radio.get_ptt = AsyncMock(return_value=False)
    radio.read_ptt = AsyncMock(return_value=False)
    radio.get_af_level = AsyncMock(
        side_effect=lambda receiver=0: 128 if receiver == 0 else 64
    )
    radio.read_af_level = AsyncMock(
        side_effect=lambda receiver=0: 128 if receiver == 0 else 64
    )
    radio.get_rf_gain = AsyncMock(
        side_effect=lambda receiver=0: 180 if receiver == 0 else 90
    )
    radio.read_rf_gain = AsyncMock(
        side_effect=lambda receiver=0: 180 if receiver == 0 else 90
    )
    radio.get_squelch = AsyncMock(
        side_effect=lambda receiver=0: 12 if receiver == 0 else 8
    )
    radio.read_squelch = AsyncMock(
        side_effect=lambda receiver=0: 12 if receiver == 0 else 8
    )
    return radio


def _profile_state_acquisition() -> RadioAcquisitionProfile:
    profile = get_radio_profile("FTX-1")
    assert profile.state_acquisition is not None
    return profile.state_acquisition


class _SideEffectingYaesuRadio:
    capabilities = {
        "dual_rx",
        "af_level",
        "rf_gain",
        "squelch",
        "meters",
        "tx",
    }

    def __init__(self) -> None:
        self.radio_state = RadioState()
        self.radio_state.main.freq = 1
        self.radio_state.main.mode = "INIT-MAIN"
        self.radio_state.sub.freq = 2
        self.radio_state.sub.mode = "INIT-SUB"
        self.radio_state.main.s_meter = 3
        self.radio_state.sub.s_meter = 4
        self.radio_state.power_meter = 5
        self.radio_state.swr_meter = 6
        self.radio_state.main.af_level = 7
        self.radio_state.main.rf_gain = 8
        self.radio_state.main.squelch = 9
        self.radio_state.sub.af_level = 10
        self.radio_state.sub.rf_gain = 11
        self.radio_state.sub.squelch = 12

    async def read_freq(self, receiver: int = 0) -> int:
        return 14_074_000 if receiver == 0 else 7_074_000

    async def get_freq(self, receiver: int = 0) -> int:
        value = await self.read_freq(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.freq = value
        return value

    async def read_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        return ("USB" if receiver == 0 else "LSB"), None

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        value, filter_width = await self.read_mode(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.mode = value
        return value, filter_width

    async def read_ptt(self) -> bool:
        return True

    async def get_ptt(self) -> bool:
        value = await self.read_ptt()
        self.radio_state.ptt = value
        return value

    async def read_s_meter(self, receiver: int = 0) -> int:
        return 150 if receiver == 0 else 75

    async def get_s_meter(self, receiver: int = 0) -> int:
        value = await self.read_s_meter(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.s_meter = value
        return value

    async def read_power_meter(self) -> int:
        return 180

    async def get_power_meter(self) -> int:
        value = await self.read_power_meter()
        self.radio_state.power_meter = value
        return value

    async def read_swr_meter(self) -> int:
        return 120

    async def get_swr_meter(self) -> int:
        value = await self.read_swr_meter()
        self.radio_state.swr_meter = value
        return value

    async def read_af_level(self, receiver: int = 0) -> int:
        return 128 if receiver == 0 else 64

    async def get_af_level(self, receiver: int = 0) -> int:
        value = await self.read_af_level(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.af_level = value
        return value

    async def read_rf_gain(self, receiver: int = 0) -> int:
        return 180 if receiver == 0 else 90

    async def get_rf_gain(self, receiver: int = 0) -> int:
        value = await self.read_rf_gain(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.rf_gain = value
        return value

    async def read_squelch(self, receiver: int = 0) -> int:
        return 12 if receiver == 0 else 8

    async def get_squelch(self, receiver: int = 0) -> int:
        value = await self.read_squelch(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.squelch = value
        return value


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
    assert radio.read_af_level.await_count == 2
    assert radio.read_rf_gain.await_count == 2
    assert radio.read_squelch.await_count == 2
    radio.get_af_level.assert_not_awaited()
    radio.get_rf_gain.assert_not_awaited()
    radio.get_squelch.assert_not_awaited()


@pytest.mark.asyncio
async def test_slow_poll_skips_sub_controls_without_matching_runtime_capability() -> None:
    radio = _make_radio()
    radio.capabilities = {"dual_rx", "af_level", "tx"}
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()

    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.operator_controls.af_level", 128),
        ("receiver.sub.operator_controls.af_level", 64),
    ]
    assert radio.read_af_level.await_count == 2
    radio.read_rf_gain.assert_not_awaited()
    radio.read_squelch.assert_not_awaited()
    radio.get_af_level.assert_not_awaited()
    radio.get_rf_gain.assert_not_awaited()
    radio.get_squelch.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_uses_read_only_yaesu_paths_when_getters_mutate_state() -> None:
    radio = _SideEffectingYaesuRadio()
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = (
        await adapter.poll_medium()
        + await adapter.poll_rx_meters()
        + await adapter.poll_tx_meters()
        + await adapter.poll_slow_controls()
    )

    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.active.freq_mode.freq_hz", 14_074_000),
        ("receiver.main.active.freq_mode.mode", "USB"),
        ("receiver.sub.active.freq_mode.freq_hz", 7_074_000),
        ("receiver.sub.active.freq_mode.mode", "LSB"),
        ("global.tx_state.ptt", True),
        ("receiver.main.meters.s_meter", 150),
        ("receiver.sub.meters.s_meter", 75),
        ("global.meters.power", 180),
        ("global.meters.swr", 120),
        ("receiver.main.operator_controls.af_level", 128),
        ("receiver.main.operator_controls.rf_gain", 180),
        ("receiver.main.operator_controls.squelch", 12),
        ("receiver.sub.operator_controls.af_level", 64),
        ("receiver.sub.operator_controls.rf_gain", 90),
        ("receiver.sub.operator_controls.squelch", 8),
    ]
    assert radio.radio_state.main.freq == 1
    assert radio.radio_state.main.mode == "INIT-MAIN"
    assert radio.radio_state.sub.freq == 2
    assert radio.radio_state.sub.mode == "INIT-SUB"
    assert radio.radio_state.ptt is False
    assert radio.radio_state.main.s_meter == 3
    assert radio.radio_state.sub.s_meter == 4
    assert radio.radio_state.power_meter == 5
    assert radio.radio_state.swr_meter == 6
    assert radio.radio_state.main.af_level == 7
    assert radio.radio_state.main.rf_gain == 8
    assert radio.radio_state.main.squelch == 9
    assert radio.radio_state.sub.af_level == 10
    assert radio.radio_state.sub.rf_gain == 11
    assert radio.radio_state.sub.squelch == 12
