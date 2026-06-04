"""Observation adapter coverage for the Yaesu CAT backend."""
# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.profiles import get_radio_profile
from rigplane.radio_state import RadioState

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio


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
        "vox",
        "compressor",
        "attenuator",
        "preamp",
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
    # RF front-end + AGC controls (MOR-443). The FTX-1 attenuator getter
    # returns a bool; the registry FieldPath is int, so the adapter coerces.
    radio.get_attenuator = AsyncMock(return_value=True)
    radio.read_attenuator = AsyncMock(return_value=True)
    radio.get_preamp = AsyncMock(return_value=2)
    radio.read_preamp = AsyncMock(return_value=2)
    radio.get_agc = AsyncMock(return_value=3)
    radio.read_agc = AsyncMock(return_value=3)
    # Global TX / operator-control setpoints (MOR-447).
    radio.get_power = AsyncMock(return_value=(2, 55))
    radio.read_power = AsyncMock(return_value=(2, 55))
    radio.get_mic_gain = AsyncMock(return_value=40)
    radio.read_mic_gain = AsyncMock(return_value=40)
    radio.get_processor = AsyncMock(return_value=True)
    radio.read_processor = AsyncMock(return_value=True)
    radio.get_processor_level = AsyncMock(return_value=25)
    radio.read_processor_level = AsyncMock(return_value=25)
    radio.get_vox = AsyncMock(return_value=True)
    radio.read_vox = AsyncMock(return_value=True)
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
        "vox",
        "compressor",
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
        self.radio_state.power_level = 13
        self.radio_state.mic_gain = 14
        self.radio_state.compressor_on = False
        self.radio_state.compressor_level = 15
        self.radio_state.vox_on = False
        self.radio_state.main.att = 16
        self.radio_state.main.preamp = 17
        self.radio_state.main.agc = 18

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

    async def read_power(self) -> tuple[int, int]:
        return (2, 55)

    async def get_power(self) -> tuple[int, int]:
        head, watts = await self.read_power()
        self.radio_state.power_level = watts
        return head, watts

    async def read_mic_gain(self) -> int:
        return 40

    async def get_mic_gain(self) -> int:
        value = await self.read_mic_gain()
        self.radio_state.mic_gain = value
        return value

    async def read_processor(self) -> bool:
        return True

    async def get_processor(self) -> bool:
        value = await self.read_processor()
        self.radio_state.compressor_on = value
        return value

    async def read_processor_level(self) -> int:
        return 25

    async def get_processor_level(self) -> int:
        value = await self.read_processor_level()
        self.radio_state.compressor_level = value
        return value

    async def read_vox(self) -> bool:
        return True

    async def get_vox(self) -> bool:
        value = await self.read_vox()
        self.radio_state.vox_on = value
        return value

    async def read_attenuator(self, receiver: int = 0) -> bool:
        return True

    async def get_attenuator(self, receiver: int = 0) -> bool:
        value = await self.read_attenuator(receiver)
        self.radio_state.main.att = int(value)
        return value

    async def read_preamp(self, receiver: int = 0) -> int:
        return 2

    async def get_preamp(self, band: int = 0) -> int:
        value = await self.read_preamp(band)
        self.radio_state.main.preamp = value
        return value

    async def read_agc(self, receiver: int = 0) -> int:
        return 3

    async def get_agc(self, receiver: int = 0) -> int:
        value = await self.read_agc(receiver)
        self.radio_state.main.agc = value
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

    # ATT/preamp/AGC are MAIN-only: the FTX-1 has no per-receiver CAT
    # command for these front-end controls (no RA1/PA1/GT1), matching the
    # legacy poller which only writes ``main.{att,preamp,agc}``. The
    # attenuator ``read`` returns a bool; the int registry path receives the
    # coerced ``int(True) == 1``.
    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.operator_controls.af_level", 128),
        ("receiver.main.operator_controls.rf_gain", 180),
        ("receiver.main.operator_controls.squelch", 12),
        ("receiver.sub.operator_controls.af_level", 64),
        ("receiver.sub.operator_controls.rf_gain", 90),
        ("receiver.sub.operator_controls.squelch", 8),
        ("receiver.main.operator_controls.att", 1),
        ("receiver.main.operator_controls.preamp", 2),
        ("receiver.main.operator_controls.agc", 3),
    ]
    assert all(item.source.source == "yaesu_poll_response" for item in observations)
    assert all(item.max_age == 120.0 for item in observations)
    assert radio.read_af_level.await_count == 2
    assert radio.read_rf_gain.await_count == 2
    assert radio.read_squelch.await_count == 2
    assert radio.read_attenuator.await_count == 1
    assert radio.read_preamp.await_count == 1
    assert radio.read_agc.await_count == 1
    assert all(isinstance(item.value, int) for item in observations)
    radio.get_af_level.assert_not_awaited()
    radio.get_rf_gain.assert_not_awaited()
    radio.get_squelch.assert_not_awaited()
    radio.get_attenuator.assert_not_awaited()
    radio.get_preamp.assert_not_awaited()
    radio.get_agc.assert_not_awaited()


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

    # ATT/preamp are gated by their runtime capabilities (dropped here);
    # AGC has no FTX-1 capability tag and mirrors the legacy poller's
    # unconditional poll, so it still emits when its policy is pollable.
    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.operator_controls.af_level", 128),
        ("receiver.sub.operator_controls.af_level", 64),
        ("receiver.main.operator_controls.agc", 3),
    ]
    assert radio.read_af_level.await_count == 2
    radio.read_rf_gain.assert_not_awaited()
    radio.read_squelch.assert_not_awaited()
    radio.read_attenuator.assert_not_awaited()
    radio.read_preamp.assert_not_awaited()
    assert radio.read_agc.await_count == 1
    radio.get_af_level.assert_not_awaited()
    radio.get_rf_gain.assert_not_awaited()
    radio.get_squelch.assert_not_awaited()


@pytest.mark.asyncio
async def test_slow_poll_coerces_attenuator_bool_to_registry_int() -> None:
    """The FTX-1 attenuator getter returns a bool; the int FieldPath gets 0/1."""
    radio = _make_radio()
    radio.read_attenuator = AsyncMock(return_value=False)
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_slow_controls()

    att = next(
        item
        for item in observations
        if str(item.path) == "receiver.main.operator_controls.att"
    )
    assert att.value == 0
    assert isinstance(att.value, int)
    assert not isinstance(att.value, bool)


@pytest.mark.asyncio
async def test_tx_controls_poll_emits_global_setpoints() -> None:
    radio = _make_radio()
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_tx_controls()

    assert [(str(item.path), item.value) for item in observations] == [
        ("global.operator_controls.power_level", 55),
        ("global.operator_controls.mic_gain", 40),
        ("global.tx_state.compressor_on", True),
        ("global.operator_controls.compressor_level", 25),
        ("global.tx_state.vox_on", True),
    ]
    assert all(item.source.source == "yaesu_poll_response" for item in observations)
    assert all(item.max_age == 120.0 for item in observations)
    # Power emits the watt SETPOINT (read_power), never the RM5 meter.
    radio.read_power.assert_awaited_once()
    radio.read_mic_gain.assert_awaited_once()
    radio.read_processor.assert_awaited_once()
    radio.read_processor_level.assert_awaited_once()
    radio.read_vox.assert_awaited_once()
    radio.get_power.assert_not_awaited()
    radio.get_mic_gain.assert_not_awaited()
    radio.get_processor.assert_not_awaited()
    radio.get_processor_level.assert_not_awaited()
    radio.get_vox.assert_not_awaited()


@pytest.mark.asyncio
async def test_tx_controls_poll_skips_fields_without_matching_runtime_capability() -> (
    None
):
    radio = _make_radio()
    # Drop tx/vox/compressor; mic_gain is unconditional, so it remains.
    radio.capabilities = {"dual_rx", "af_level", "rf_gain", "squelch", "meters"}
    adapter = YaesuObservationAdapter(
        radio,
        profile=_profile_state_acquisition(),
        clock=_clock,
    )

    observations = await adapter.poll_tx_controls()

    assert [(str(item.path), item.value) for item in observations] == [
        ("global.operator_controls.mic_gain", 40),
    ]
    radio.read_power.assert_not_awaited()
    radio.read_processor.assert_not_awaited()
    radio.read_processor_level.assert_not_awaited()
    radio.read_vox.assert_not_awaited()


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
        + await adapter.poll_tx_controls()
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
        # AGC has no FTX-1 capability tag → unconditional, MAIN-only; ATT and
        # preamp are skipped because this radio lacks those runtime caps.
        ("receiver.main.operator_controls.agc", 3),
        ("global.operator_controls.power_level", 55),
        ("global.operator_controls.mic_gain", 40),
        ("global.tx_state.compressor_on", True),
        ("global.operator_controls.compressor_level", 25),
        ("global.tx_state.vox_on", True),
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
    # The new read_* TX-control paths must not mutate legacy state either.
    assert radio.radio_state.power_level == 13
    assert radio.radio_state.mic_gain == 14
    assert radio.radio_state.compressor_on is False
    assert radio.radio_state.compressor_level == 15
    assert radio.radio_state.vox_on is False
    # RF front-end + AGC read_* paths must not mutate legacy state (MOR-443).
    assert radio.radio_state.main.att == 16
    assert radio.radio_state.main.preamp == 17
    assert radio.radio_state.main.agc == 18


@pytest.mark.asyncio
async def test_public_get_data_mode_returns_flat_value_without_state_synthesis() -> None:
    """MOR-434: a public ``get_*`` returns a flat value, not synthesized state.

    ``get_data_mode`` is the representative public read called out for the
    provider backends. It derives a flat ``bool`` from the existing mode and
    must not fabricate or hand out a synthesized ``RadioState`` as consumer
    state. The consumer pipeline is fed by :class:`YaesuObservationAdapter`
    (which uses the non-mutating ``read_*`` paths); the private ``self._state``
    mirror is legacy compat only.
    """
    # Real backend; only the USB audio driver is stubbed (not under test).
    radio = YaesuCatRadio("/dev/null", audio_driver=MagicMock())
    radio.radio_state.main.mode = "USB-D"
    state_before = radio.radio_state

    result = await radio.get_data_mode()

    # Flat derived bool, never a RadioState object.
    assert result is True
    assert isinstance(result, bool)
    # No synthesized RadioState handed back as consumer state.
    assert radio.radio_state is state_before
    # The read derives from the mirror without mutating it.
    assert radio.radio_state.main.mode == "USB-D"
