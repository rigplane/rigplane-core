"""Tests for YaesuCatPoller."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.state_acquisition_policy import RadioAcquisitionProfile
from rigplane.core.state_pipeline_contracts import FieldPath, Observation
from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.profiles import get_radio_profile
from rigplane.radio_state import RadioState
from rigplane.web.radio_poller import CommandQueue, SetFreq


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_radio(
    *,
    s_meter_main: int = 100,
    s_meter_sub: int = 50,
    freq_main: int = 14_074_000,
    freq_sub: int = 7_074_000,
    mode_main: tuple = ("USB", None),
    mode_sub: tuple = ("LSB", None),
    ptt: bool = False,
    agc: int = 2,
    af_level: int = 128,
    rf_gain: int = 200,
    squelch: int = 0,
    clarifier: tuple[bool, bool] = (False, False),
    clarifier_freq: int = 0,
    manual_notch: tuple[bool, int] = (False, 0),
    narrow: bool = False,
    vfo_select: int = 0,
) -> MagicMock:
    """Return a mock YaesuCatRadio with sensible defaults."""
    radio = MagicMock()
    radio.radio_state = RadioState()
    radio.capabilities = {
        "audio",
        "dual_rx",
        "af_level",
        "rf_gain",
        "squelch",
        "attenuator",
        "preamp",
        "nb",
        "nr",
        "notch",
        "if_shift",
        "contour",
        "filter_width",
        "tx",
        "split",
        "vox",
        "compressor",
        "cw",
        "rit",
        "tuner",
        "meters",
        "repeater_tone",
        "tsql",
        "data_mode",
        "scan",
        "dial_lock",
    }

    radio.get_s_meter = AsyncMock(
        side_effect=lambda r=0: s_meter_main if r == 0 else s_meter_sub
    )
    radio.read_s_meter = AsyncMock(
        side_effect=lambda r=0: s_meter_main if r == 0 else s_meter_sub
    )
    radio.get_freq = AsyncMock(
        side_effect=lambda r=0: freq_main if r == 0 else freq_sub
    )
    radio.read_freq = AsyncMock(
        side_effect=lambda r=0: freq_main if r == 0 else freq_sub
    )
    radio.get_mode = AsyncMock(
        side_effect=lambda r=0: mode_main if r == 0 else mode_sub
    )
    radio.read_mode = AsyncMock(
        side_effect=lambda r=0: mode_main if r == 0 else mode_sub
    )
    radio.get_ptt = AsyncMock(return_value=ptt)
    radio.read_ptt = AsyncMock(return_value=ptt)
    radio.get_agc = AsyncMock(return_value=agc)
    radio.get_af_level = AsyncMock(return_value=af_level)
    radio.read_af_level = AsyncMock(return_value=af_level)
    radio.get_rf_gain = AsyncMock(return_value=rf_gain)
    radio.read_rf_gain = AsyncMock(return_value=rf_gain)
    radio.get_squelch = AsyncMock(return_value=squelch)
    radio.read_squelch = AsyncMock(return_value=squelch)
    radio.get_clarifier = AsyncMock(return_value=clarifier)
    radio.get_clarifier_freq = AsyncMock(return_value=clarifier_freq)
    radio.get_manual_notch = AsyncMock(return_value=manual_notch)
    radio.get_narrow = AsyncMock(return_value=narrow)
    radio.read_narrow = AsyncMock(return_value=narrow)
    # Filter / IF-shift DSP control reads (MOR-445).
    radio.get_filter_width = AsyncMock(return_value=2400)
    radio.read_filter_width = AsyncMock(return_value=2400)
    radio.get_if_shift = AsyncMock(return_value=0)
    radio.read_if_shift = AsyncMock(return_value=0)
    radio.get_vfo_select = AsyncMock(return_value=vfo_select)
    radio.read_vfo_select = AsyncMock(return_value=vfo_select)
    radio.get_split = AsyncMock(return_value=False)
    radio.read_split = AsyncMock(return_value=False)
    radio.get_alc_meter = AsyncMock(return_value=0)
    radio.read_alc_meter = AsyncMock(return_value=0)
    radio.get_power_meter = AsyncMock(return_value=0)
    radio.read_power_meter = AsyncMock(return_value=0)
    radio.get_comp_meter = AsyncMock(return_value=0)
    radio.get_swr_meter = AsyncMock(return_value=0)
    radio.read_swr_meter = AsyncMock(return_value=0)
    radio._read_meter = AsyncMock(return_value=(0, 0))
    radio.get_keyer_speed = AsyncMock(return_value=20)
    radio.get_key_pitch = AsyncMock(return_value=30)  # idx — Yaesu-internal API
    radio.get_cw_pitch = AsyncMock(return_value=600)  # Hz — Icom-spelled API (#1162)
    radio.get_break_in = AsyncMock(return_value=False)
    radio.get_break_in_delay = AsyncMock(return_value=0)
    radio.get_cw_spot = AsyncMock(return_value=False)
    radio.get_rx_func = AsyncMock(return_value=0)
    radio.get_tx_func = AsyncMock(return_value=0)
    return radio


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
        self.radio_state.main.af_level = 5
        self.radio_state.main.rf_gain = 6
        self.radio_state.main.squelch = 7
        self.radio_state.sub.af_level = 8
        self.radio_state.sub.rf_gain = 9
        self.radio_state.sub.squelch = 10
        self.radio_state.power_level = 11
        self.radio_state.mic_gain = 12
        self.radio_state.compressor_on = False
        self.radio_state.compressor_level = 13
        self.radio_state.vox_on = False
        self.radio_state.main.att = 14
        self.radio_state.main.preamp = 15
        self.radio_state.main.agc = 16
        self.radio_state.main.filter_width = 17
        self.radio_state.main.if_shift = 18
        self.radio_state.main.narrow = False
        self.profile = get_radio_profile("FTX-1")
        self.legacy_getter_calls = 0

    async def read_freq(self, receiver: int = 0) -> int:
        return 14_074_000 if receiver == 0 else 7_074_000

    async def get_freq(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_freq(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.freq = value
        return value

    async def read_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        return ("USB" if receiver == 0 else "LSB"), None

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        self.legacy_getter_calls += 1
        value, filter_width = await self.read_mode(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.mode = value
        return value, filter_width

    async def read_ptt(self) -> bool:
        return False

    async def get_ptt(self) -> bool:
        self.legacy_getter_calls += 1
        value = await self.read_ptt()
        self.radio_state.ptt = value
        return value

    async def read_s_meter(self, receiver: int = 0) -> int:
        return 150 if receiver == 0 else 75

    async def get_s_meter(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_s_meter(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.s_meter = value
        return value

    async def read_af_level(self, receiver: int = 0) -> int:
        return 128 if receiver == 0 else 64

    async def get_af_level(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_af_level(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.af_level = value
        return value

    async def read_rf_gain(self, receiver: int = 0) -> int:
        return 180 if receiver == 0 else 90

    async def get_rf_gain(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_rf_gain(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.rf_gain = value
        return value

    async def read_squelch(self, receiver: int = 0) -> int:
        return 12 if receiver == 0 else 8

    async def get_squelch(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_squelch(receiver)
        target = self.radio_state.main if receiver == 0 else self.radio_state.sub
        target.squelch = value
        return value

    async def read_power(self) -> tuple[int, int]:
        return (2, 55)

    async def get_power(self) -> tuple[int, int]:
        self.legacy_getter_calls += 1
        head, watts = await self.read_power()
        self.radio_state.power_level = watts
        return head, watts

    async def read_mic_gain(self) -> int:
        return 40

    async def get_mic_gain(self) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_mic_gain()
        self.radio_state.mic_gain = value
        return value

    async def read_processor(self) -> bool:
        return True

    async def get_processor(self) -> bool:
        self.legacy_getter_calls += 1
        value = await self.read_processor()
        self.radio_state.compressor_on = value
        return value

    async def read_processor_level(self) -> int:
        return 25

    async def get_processor_level(self) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_processor_level()
        self.radio_state.compressor_level = value
        return value

    async def read_vox(self) -> bool:
        return True

    async def get_vox(self) -> bool:
        self.legacy_getter_calls += 1
        value = await self.read_vox()
        self.radio_state.vox_on = value
        return value

    async def read_attenuator(self, receiver: int = 0) -> bool:
        return True

    async def get_attenuator(self, receiver: int = 0) -> bool:
        self.legacy_getter_calls += 1
        value = await self.read_attenuator(receiver)
        self.radio_state.main.att = int(value)
        return value

    async def read_preamp(self, receiver: int = 0) -> int:
        return 2

    async def get_preamp(self, band: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_preamp(band)
        self.radio_state.main.preamp = value
        return value

    async def read_agc(self, receiver: int = 0) -> int:
        return 3

    async def get_agc(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_agc(receiver)
        self.radio_state.main.agc = value
        return value

    async def read_filter_width(self, receiver: int = 0) -> int:
        return 2400

    async def get_filter_width(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_filter_width(receiver)
        self.radio_state.main.filter_width = value
        return value

    async def read_if_shift(self, receiver: int = 0) -> int:
        return 200

    async def get_if_shift(self, receiver: int = 0) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_if_shift(receiver)
        self.radio_state.main.if_shift = value
        return value

    async def read_narrow(self, receiver: int = 0) -> bool:
        return True

    async def get_narrow(self, receiver: int = 0) -> bool:
        self.legacy_getter_calls += 1
        value = await self.read_narrow(receiver)
        self.radio_state.main.narrow = value
        return value

    async def read_vfo_select(self) -> int:
        return 1

    async def get_vfo_select(self) -> int:
        self.legacy_getter_calls += 1
        value = await self.read_vfo_select()
        self.radio_state.vfo_select = value
        self.radio_state.active = "SUB" if value else "MAIN"
        return value


def _profile_state_acquisition() -> RadioAcquisitionProfile:
    profile = get_radio_profile("FTX-1")
    assert profile.state_acquisition is not None
    return profile.state_acquisition


def _state_write_target(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name) and current.id == "state" and parts:
        return ".".join(reversed(parts))
    if (
        len(parts) >= 2
        and parts[-1] == "_radio"
        and parts[-2] == "radio_state"
        and isinstance(current, ast.Name)
        and current.id == "self"
    ):
        state_parts = parts[:-2]
        if not state_parts:
            return None
        return ".".join(reversed(state_parts))
    return None


def _yaesu_poller_state_write_targets() -> set[str]:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/rigplane/backends/yaesu_cat/poller.py").read_text()
    module = ast.parse(source)
    targets: set[str] = set()
    for item in module.body:
        if not isinstance(item, ast.ClassDef) or item.name != "YaesuCatPoller":
            continue
        for method in item.body:
            if not isinstance(method, ast.AsyncFunctionDef):
                continue
            if method.name not in {"_poll_fast", "_poll_medium", "_poll_slow"}:
                continue
            for node in ast.walk(method):
                assignment_targets: list[ast.AST] = []
                if isinstance(node, ast.Assign):
                    assignment_targets.extend(node.targets)
                elif isinstance(node, ast.AnnAssign):
                    assignment_targets.append(node.target)
                elif isinstance(node, ast.AugAssign):
                    assignment_targets.append(node.target)
                for target in assignment_targets:
                    name = _state_write_target(target)
                    if name is not None:
                        targets.add(name)
    return targets


# ---------------------------------------------------------------------------
# Start / stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_tasks() -> None:
    radio = make_radio()
    calls: list[RadioState] = []
    poller = YaesuCatPoller(radio, callback=calls.append, fast_interval=0.01)

    await poller.start()
    assert poller.running
    assert len(poller._tasks) == 3

    await poller.stop()
    assert not poller.running
    assert poller._tasks == []


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    radio = make_radio()
    poller = YaesuCatPoller(radio, callback=lambda s: None, fast_interval=0.01)

    await poller.start()
    tasks_first = list(poller._tasks)
    await poller.start()  # second call — no-op
    assert poller._tasks is tasks_first or poller._tasks == tasks_first

    await poller.stop()


@pytest.mark.asyncio
async def test_stop_cancels_tasks() -> None:
    radio = make_radio()
    poller = YaesuCatPoller(radio, callback=lambda s: None, fast_interval=10.0)

    await poller.start()
    await poller.stop()

    assert not poller.running


# ---------------------------------------------------------------------------
# Callback invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_poll_invokes_callback() -> None:
    radio = make_radio(s_meter_main=120)
    calls: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,  # no smoothing so raw == smoothed
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    assert len(calls) >= 1
    # Callback receives the RadioState object
    assert isinstance(calls[0], RadioState)


@pytest.mark.asyncio
async def test_medium_poll_invokes_callback() -> None:
    radio = make_radio()
    calls: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=10.0,
        medium_interval=0.01,
        slow_interval=10.0,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    assert len(calls) >= 1


@pytest.mark.asyncio
async def test_slow_poll_invokes_callback() -> None:
    radio = make_radio()
    calls: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    assert len(calls) >= 1


# ---------------------------------------------------------------------------
# State updates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_poll_updates_s_meter() -> None:
    radio = make_radio(s_meter_main=150, s_meter_sub=75)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,  # raw pass-through
    )
    await poller.start()
    await asyncio.sleep(0.03)
    await poller.stop()

    assert radio.radio_state.main.s_meter == 150
    assert radio.radio_state.sub.s_meter == 75


@pytest.mark.asyncio
async def test_medium_poll_updates_freq_mode_ptt() -> None:
    radio = make_radio(freq_main=14_074_000, ptt=True)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=0.01,
        slow_interval=10.0,
    )
    await poller.start()
    await asyncio.sleep(0.03)
    await poller.stop()

    radio.get_freq.assert_called()
    radio.get_mode.assert_called()
    radio.get_ptt.assert_called()


@pytest.mark.asyncio
async def test_medium_poll_emits_observations_without_legacy_state_callback() -> None:
    radio = make_radio(freq_main=14_074_000, ptt=True)
    radio.profile.state_acquisition = _profile_state_acquisition()
    legacy_calls: list[RadioState] = []
    observations: list[Observation] = []

    poller = YaesuCatPoller(
        radio,
        callback=legacy_calls.append,
        observation_callback=observations.extend,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=10.0,
    )

    await poller._poll_medium()  # noqa: SLF001

    assert legacy_calls == []
    # filter_width shares the freq/mode lane (MOR-445); ``make_radio`` declares
    # the ``filter_width`` cap, so it emits after PTT, MAIN-only.
    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.active.freq_mode.freq_hz", 14_074_000),
        ("receiver.main.active.freq_mode.mode", "USB"),
        ("receiver.sub.active.freq_mode.freq_hz", 7_074_000),
        ("receiver.sub.active.freq_mode.mode", "LSB"),
        ("global.tx_state.ptt", True),
        ("receiver.main.active.freq_mode.filter_width", 2400),
    ]
    assert {item.source.source for item in observations} == {"yaesu_poll_response"}


@pytest.mark.asyncio
async def test_fast_poll_emits_rx_meter_observations_without_legacy_state_callback() -> None:
    radio = make_radio(s_meter_main=150, s_meter_sub=75, ptt=False)
    radio.profile.state_acquisition = _profile_state_acquisition()
    legacy_calls: list[RadioState] = []
    observations: list[Observation] = []

    poller = YaesuCatPoller(
        radio,
        callback=legacy_calls.append,
        observation_callback=observations.extend,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )

    await poller._poll_fast()  # noqa: SLF001

    assert legacy_calls == []
    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.meters.s_meter", 150),
        ("receiver.sub.meters.s_meter", 75),
    ]
    assert radio.radio_state.main.s_meter == 0
    assert radio.radio_state.sub.s_meter == 0


@pytest.mark.asyncio
async def test_observation_poller_uses_read_only_paths_when_getters_mutate_state() -> None:
    radio = _SideEffectingYaesuRadio()
    legacy_calls: list[RadioState] = []
    observations: list[Observation] = []

    poller = YaesuCatPoller(
        radio,
        callback=legacy_calls.append,
        observation_callback=observations.extend,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )

    await poller._poll_medium()  # noqa: SLF001
    await poller._poll_fast()  # noqa: SLF001
    await poller._poll_slow()  # noqa: SLF001

    assert legacy_calls == []
    assert [(str(item.path), item.value) for item in observations] == [
        ("receiver.main.active.freq_mode.freq_hz", 14_074_000),
        ("receiver.main.active.freq_mode.mode", "USB"),
        ("receiver.sub.active.freq_mode.freq_hz", 7_074_000),
        ("receiver.sub.active.freq_mode.mode", "LSB"),
        ("global.tx_state.ptt", False),
        ("receiver.main.meters.s_meter", 150),
        ("receiver.sub.meters.s_meter", 75),
        ("receiver.main.operator_controls.af_level", 128),
        ("receiver.main.operator_controls.rf_gain", 180),
        ("receiver.main.operator_controls.squelch", 12),
        ("receiver.sub.operator_controls.af_level", 64),
        ("receiver.sub.operator_controls.rf_gain", 90),
        ("receiver.sub.operator_controls.squelch", 8),
        # ATT/preamp need their runtime caps (absent here); AGC is
        # unconditional and MAIN-only, mirroring the legacy poller.
        ("receiver.main.operator_controls.agc", 3),
        # filter_width/if_shift need their runtime caps (absent here); narrow
        # is unconditional and MAIN-only, like AGC (MOR-445).
        ("receiver.main.operator_toggles.narrow", True),
        # active-slot (MOR-446) closes the slow-control lane; unconditional like
        # AGC/narrow, the SUB index coerces to the neutral "SUB" str. split is
        # skipped: this radio lacks the ``split`` runtime cap.
        ("global.slow_state.active", "SUB"),
        ("global.operator_controls.power_level", 55),
        ("global.operator_controls.mic_gain", 40),
        ("global.tx_state.compressor_on", True),
        ("global.operator_controls.compressor_level", 25),
        ("global.tx_state.vox_on", True),
    ]
    assert radio.legacy_getter_calls == 0
    assert radio.radio_state.main.freq == 1
    assert radio.radio_state.main.mode == "INIT-MAIN"
    assert radio.radio_state.sub.freq == 2
    assert radio.radio_state.sub.mode == "INIT-SUB"
    assert radio.radio_state.ptt is False
    assert radio.radio_state.main.s_meter == 3
    assert radio.radio_state.sub.s_meter == 4
    assert radio.radio_state.main.af_level == 5
    assert radio.radio_state.main.rf_gain == 6
    assert radio.radio_state.main.squelch == 7
    assert radio.radio_state.sub.af_level == 8
    assert radio.radio_state.sub.rf_gain == 9
    assert radio.radio_state.sub.squelch == 10
    assert radio.radio_state.power_level == 11
    assert radio.radio_state.mic_gain == 12
    assert radio.radio_state.compressor_on is False
    assert radio.radio_state.compressor_level == 13
    assert radio.radio_state.vox_on is False
    assert radio.radio_state.main.att == 14
    assert radio.radio_state.main.preamp == 15
    assert radio.radio_state.main.agc == 16
    # Filter / IF-shift / narrow read_* paths must not mutate legacy state
    # (MOR-445), including read_filter_width which reads but never writes mode.
    assert radio.radio_state.main.filter_width == 17
    assert radio.radio_state.main.if_shift == 18
    assert radio.radio_state.main.narrow is False
    # Split + active-slot read_* paths must not mutate legacy state (MOR-446).
    assert radio.radio_state.split is False
    assert radio.radio_state.active == "MAIN"
    assert radio.radio_state.vfo_select == 0


@pytest.mark.asyncio
async def test_fast_poll_emits_profiled_tx_meter_observations_only() -> None:
    radio = make_radio(ptt=True)
    radio.profile.state_acquisition = _profile_state_acquisition()
    radio.read_alc_meter = AsyncMock(return_value=42)
    radio.read_power_meter = AsyncMock(return_value=180)
    radio.read_swr_meter = AsyncMock(return_value=120)
    radio.get_alc_meter = AsyncMock(return_value=42)
    radio.get_power_meter = AsyncMock(return_value=180)
    radio.get_comp_meter = AsyncMock(return_value=30)
    radio.get_swr_meter = AsyncMock(return_value=120)
    observations: list[Observation] = []

    poller = YaesuCatPoller(
        radio,
        observation_callback=observations.extend,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=10.0,
    )

    await poller._poll_medium()  # noqa: SLF001
    observations.clear()
    await poller._poll_fast()  # noqa: SLF001

    # ALC is now an observation-backed stream-like meter (MOR-448); COMP has
    # no neutral FieldPath and stays legacy-only, so it never emits here.
    assert [(str(item.path), item.value) for item in observations] == [
        ("global.meters.alc", 42),
        ("global.meters.power", 180),
        ("global.meters.swr", 120),
    ]
    radio.get_alc_meter.assert_not_awaited()
    radio.get_power_meter.assert_not_awaited()
    radio.get_comp_meter.assert_not_awaited()
    radio.get_swr_meter.assert_not_awaited()
    assert radio.radio_state.alc_meter == 0
    assert radio.radio_state.power_meter == 0
    assert radio.radio_state.swr_meter == 0


def test_legacy_yaesu_state_writes_are_observed_or_explicit_limitations() -> None:
    decisions = {
        "main.s_meter": "observation:receiver.main.meters.s_meter",
        "sub.s_meter": "observation:receiver.sub.meters.s_meter",
        "power_meter": "observation:global.meters.power",
        "swr_meter": "observation:global.meters.swr",
        "main.af_level": "observation:receiver.main.operator_controls.af_level",
        "main.rf_gain": "observation:receiver.main.operator_controls.rf_gain",
        "main.squelch": "observation:receiver.main.operator_controls.squelch",
        "sub.af_level": "observation:receiver.sub.operator_controls.af_level",
        "sub.rf_gain": "observation:receiver.sub.operator_controls.rf_gain",
        "sub.squelch": "observation:receiver.sub.operator_controls.squelch",
        "alc_meter": "observation:global.meters.alc",
        "comp_meter": "limitation: no canonical comp meter FieldPath in DEFAULT_FIELD_REGISTRY",
        "main.filter_width": "observation:receiver.main.active.freq_mode.filter_width",
        "main.agc": "observation:receiver.main.operator_controls.agc",
        "main.nb_level": "observation:receiver.main.operator_controls.nb_level",
        "main.nb": "observation:receiver.main.operator_toggles.nb",
        "main.nr_level": "observation:receiver.main.operator_controls.nr_level",
        "main.nr": "observation:receiver.main.operator_toggles.nr",
        "main.auto_notch": "observation:receiver.main.operator_toggles.auto_notch",
        "power_level": "observation:global.operator_controls.power_level",
        "mic_gain": "observation:global.operator_controls.mic_gain",
        "split": "observation:global.tx_state.split",
        "vox_on": "observation:global.tx_state.vox_on",
        "dial_lock": "limitation: dial lock lacks canonical acquisition profile coverage",
        "compressor_on": "observation:global.tx_state.compressor_on",
        "compressor_level": "observation:global.operator_controls.compressor_level",
        "main.att": "observation:receiver.main.operator_controls.att",
        "main.preamp": "observation:receiver.main.operator_controls.preamp",
        "tuner_status": "limitation: tuner status lacks canonical acquisition profile coverage",
        "main.contour": "limitation: contour lacks canonical acquisition profile coverage",
        "main.if_shift": "observation:receiver.main.operator_controls.if_shift",
        "rit_on": "limitation: RIT state lacks canonical acquisition profile coverage",
        "rit_tx": "limitation: XIT state lacks canonical acquisition profile coverage",
        "rit_freq": "limitation: clarifier offset lacks canonical acquisition profile coverage",
        "main.manual_notch": "observation:receiver.main.operator_toggles.manual_notch",
        "main.manual_notch_freq": "observation:receiver.main.operator_controls.manual_notch_freq",
        "main.narrow": "observation:receiver.main.operator_toggles.narrow",
        "key_speed": "limitation: CW key speed lacks canonical acquisition profile coverage",
        "cw_pitch": "limitation: CW pitch lacks canonical acquisition profile coverage",
        "break_in": "limitation: break-in lacks canonical acquisition profile coverage",
        "break_in_delay": "limitation: break-in delay lacks canonical acquisition profile coverage",
        "cw_spot": "limitation: CW spot lacks canonical acquisition profile coverage",
        "yaesu": "limitation: Yaesu extension namespace is backend-specific compatibility state",
        "yaesu.rx_func_mode": "limitation: Yaesu FR mode is backend-specific compatibility state",
        "yaesu.tx_func_mode": "limitation: Yaesu FT mode is backend-specific compatibility state",
        "vfo_select": "observation:global.slow_state.active",
    }

    assert _yaesu_poller_state_write_targets() == set(decisions)

    profile = _profile_state_acquisition()
    canonical_paths = [
        FieldPath.parse(decision.removeprefix("observation:"))
        for decision in decisions.values()
        if decision.startswith("observation:")
    ]
    assert all(profile.capability_for(path).can_poll for path in canonical_paths)


@pytest.mark.asyncio
async def test_slow_poll_updates_agc_and_levels() -> None:
    radio = make_radio(agc=3, af_level=200, rf_gain=180, squelch=20)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.03)
    await poller.stop()

    assert radio.radio_state.main.agc == 3
    assert radio.radio_state.main.af_level == 200
    assert radio.radio_state.main.rf_gain == 180
    assert radio.radio_state.main.squelch == 20


# ---------------------------------------------------------------------------
# EMA smoothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ema_smoothing_applied() -> None:
    """With alpha=0.5 two identical samples should converge to the value."""
    radio = make_radio(s_meter_main=100)
    states: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=states.append,
        fast_interval=0.005,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=0.5,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    # After several samples of 100, EMA should converge to 100.
    assert states, "No callbacks received"
    final = states[-1].main.s_meter
    assert 90 <= final <= 110, f"EMA didn't converge: {final}"


@pytest.mark.asyncio
async def test_ema_zero_alpha_no_smoothing() -> None:
    """alpha=0 means EMA always returns the first sample."""
    radio = make_radio(s_meter_main=77)
    states: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=states.append,
        fast_interval=0.005,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=0,
    )
    await poller.start()
    await asyncio.sleep(0.03)
    await poller.stop()

    # alpha=0: formula returns float(raw) on first call, then 0*raw + 1*prev = prev
    # but first call always returns float(raw) = 77
    assert states[0].main.s_meter == 77


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_stops_callbacks() -> None:
    radio = make_radio()
    calls: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
    )
    await poller.start()
    await asyncio.sleep(0.03)

    before = len(calls)
    await poller.pause()
    await asyncio.sleep(0.05)
    after = len(calls)

    # At most one in-flight request completes after pause().
    assert after - before <= 1

    await poller.stop()


@pytest.mark.asyncio
async def test_resume_restarts_callbacks() -> None:
    radio = make_radio()
    calls: list[RadioState] = []

    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
    )
    await poller.start()
    await poller.pause()
    await asyncio.sleep(0.03)

    before = len(calls)
    await poller.resume()
    await asyncio.sleep(0.05)
    after = len(calls)

    assert after > before

    await poller.stop()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_poll_continues_after_error() -> None:
    """A transient get_s_meter error must not crash the poller."""
    call_count = 0

    async def flaky_s_meter(receiver: int = 0) -> int:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise RuntimeError("timeout")
        return 100

    radio = make_radio()
    radio.get_s_meter = AsyncMock(side_effect=flaky_s_meter)

    calls: list[RadioState] = []
    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )
    await poller.start()
    await asyncio.sleep(0.08)
    await poller.stop()

    # Should have recovered and fired callbacks after early errors.
    assert len(calls) >= 1


@pytest.mark.asyncio
async def test_sub_receiver_unavailable_does_not_crash() -> None:
    """If sub S-meter raises, main polling must still work."""
    radio = make_radio()

    async def s_meter_side_effect(receiver: int = 0) -> int:
        if receiver == 1:
            raise RuntimeError("sub not supported")
        return 80

    radio.get_s_meter = AsyncMock(side_effect=s_meter_side_effect)

    calls: list[RadioState] = []
    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    assert len(calls) >= 1
    assert calls[-1].main.s_meter == 80


@pytest.mark.asyncio
async def test_slow_poll_continues_after_partial_error() -> None:
    """Even if get_agc raises, the remaining slow-poll commands run."""
    radio = make_radio(af_level=99)
    radio.get_agc = AsyncMock(side_effect=RuntimeError("agc error"))

    calls: list[RadioState] = []
    poller = YaesuCatPoller(
        radio,
        callback=calls.append,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    # get_af_level should still have run.
    radio.get_af_level.assert_called()
    assert calls[-1].main.af_level == 99


# ---------------------------------------------------------------------------
# Polling rates (rough verification)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_polls_more_than_slow() -> None:
    """Fast loop should fire at least 3× more often than slow."""
    radio = make_radio()
    fast_count = 0
    slow_count = 0

    _original_fast = radio.get_s_meter

    async def count_fast(receiver: int = 0) -> int:
        nonlocal fast_count
        if receiver == 0:
            fast_count += 1
        return 0

    async def count_slow(receiver: int = 0) -> int:
        nonlocal slow_count
        slow_count += 1
        return 0

    radio.get_s_meter = AsyncMock(side_effect=count_fast)
    radio.get_agc = AsyncMock(side_effect=count_slow)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=0.02,
        medium_interval=10.0,
        slow_interval=0.1,
    )
    await poller.start()
    await asyncio.sleep(0.25)
    await poller.stop()

    assert fast_count > 0
    assert slow_count > 0
    assert fast_count >= slow_count * 3, (
        f"fast={fast_count} should be >= 3×slow={slow_count}"
    )


# ---------------------------------------------------------------------------
# TX meter polling (#559)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_poll_reads_tx_meters_when_ptt_active() -> None:
    """When PTT is on, fast poll should read ALC, Power, COMP, SWR meters."""
    radio = make_radio(ptt=True)
    radio.radio_state.ptt = True
    radio.get_alc_meter = AsyncMock(return_value=42)
    radio.get_power_meter = AsyncMock(return_value=180)
    radio.get_comp_meter = AsyncMock(return_value=30)
    radio.get_swr_meter = AsyncMock(return_value=120)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_alc_meter.assert_called()
    radio.get_power_meter.assert_called()
    radio.get_comp_meter.assert_called()
    radio.get_swr_meter.assert_called()
    assert radio.radio_state.alc_meter == 42
    assert radio.radio_state.power_meter == 180
    assert radio.radio_state.comp_meter == 30
    assert radio.radio_state.swr_meter == 120


# ---------------------------------------------------------------------------
# Command queue: future exception propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_commands_sets_future_exception_on_execution_failure() -> None:
    """_drain_commands must set_exception on the future when _execute_command raises."""
    radio = make_radio()
    boom = RuntimeError("rig error")
    radio.set_freq = AsyncMock(side_effect=boom)

    queue = CommandQueue()
    poller = YaesuCatPoller(radio, callback=lambda s: None, command_queue=queue)

    loop = asyncio.get_running_loop()
    future: asyncio.Future[None] = loop.create_future()
    queue.put_ordered(SetFreq(144_030_000, receiver=0), future=future)

    await poller._drain_commands()

    assert future.done()
    assert not future.cancelled()
    assert future.exception() is boom


@pytest.mark.asyncio
async def test_fast_poll_skips_tx_meters_when_ptt_off() -> None:
    """When PTT is off, fast poll should NOT read TX meters."""
    radio = make_radio(ptt=False)
    radio.radio_state.ptt = False

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_alc_meter.assert_not_called()
    radio.get_power_meter.assert_not_called()


@pytest.mark.asyncio
async def test_tx_meter_partial_failure_does_not_block_others() -> None:
    """If one TX meter fails, the rest must still be polled."""
    radio = make_radio(ptt=True)
    radio.radio_state.ptt = True
    radio.get_alc_meter = AsyncMock(side_effect=RuntimeError("ALC timeout"))
    radio.get_power_meter = AsyncMock(return_value=200)
    radio.get_comp_meter = AsyncMock(return_value=15)
    radio.get_swr_meter = AsyncMock(return_value=80)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=0.01,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    # ALC failed, but power/comp/swr should still have been read
    radio.get_power_meter.assert_called()
    radio.get_comp_meter.assert_called()
    radio.get_swr_meter.assert_called()
    assert radio.radio_state.power_meter == 200
    assert radio.radio_state.comp_meter == 15
    assert radio.radio_state.swr_meter == 80


# ---------------------------------------------------------------------------
# CW parameter polling (#560)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_poll_reads_cw_params() -> None:
    """Slow poll should read keyer speed, CW pitch (Hz), and break-in when CW capable."""
    radio = make_radio()
    radio.get_keyer_speed = AsyncMock(return_value=25)
    radio.get_cw_pitch = AsyncMock(return_value=700)  # Hz (#1162)
    radio.get_break_in = AsyncMock(return_value=True)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_keyer_speed.assert_called()
    radio.get_cw_pitch.assert_called()
    radio.get_break_in.assert_called()
    assert radio.radio_state.key_speed == 25
    assert radio.radio_state.cw_pitch == 700
    assert radio.radio_state.break_in == 1


@pytest.mark.asyncio
async def test_slow_poll_skips_cw_without_capability() -> None:
    """Without 'cw' capability, CW params should not be polled."""
    radio = make_radio()
    radio.capabilities.discard("cw")

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_keyer_speed.assert_not_called()
    radio.get_cw_pitch.assert_not_called()
    radio.get_break_in.assert_not_called()


@pytest.mark.asyncio
async def test_cw_partial_failure_does_not_block_others() -> None:
    """If get_keyer_speed fails, pitch and break-in must still be polled."""
    radio = make_radio()
    radio.get_keyer_speed = AsyncMock(side_effect=RuntimeError("CAT timeout"))
    radio.get_cw_pitch = AsyncMock(return_value=700)  # Hz (#1162)
    radio.get_break_in = AsyncMock(return_value=False)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_cw_pitch.assert_called()
    radio.get_break_in.assert_called()
    assert radio.radio_state.cw_pitch == 700
    assert radio.radio_state.break_in == 0


# ---------------------------------------------------------------------------
# SUB receiver level polling (#563)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_poll_reads_sub_levels_when_dual_rx() -> None:
    """Slow poll should read SUB AF/RF/squelch and assign to state."""
    radio = make_radio()
    radio.get_af_level = AsyncMock(side_effect=lambda r=0: 128 if r == 0 else 200)
    radio.get_rf_gain = AsyncMock(side_effect=lambda r=0: 180 if r == 0 else 160)
    radio.get_squelch = AsyncMock(side_effect=lambda r=0: 0 if r == 0 else 30)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    # SUB receiver levels should be polled
    assert any(
        call.args == (1,) or call.kwargs.get("receiver") == 1
        for call in radio.get_af_level.call_args_list
    ), "get_af_level(1) was never called"

    # SUB receiver levels must be assigned to RadioState
    state = radio.radio_state
    assert state.sub.af_level == 200, f"sub.af_level={state.sub.af_level}, expected 200"
    assert state.sub.rf_gain == 160, f"sub.rf_gain={state.sub.rf_gain}, expected 160"
    assert state.sub.squelch == 30, f"sub.squelch={state.sub.squelch}, expected 30"


@pytest.mark.asyncio
async def test_slow_poll_skips_sub_levels_without_dual_rx() -> None:
    """Without dual_rx, SUB levels should not be polled."""
    radio = make_radio()
    radio.capabilities.discard("dual_rx")

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    # Only receiver=0 calls should exist for all three SUB level methods
    for method_name in ("get_af_level", "get_rf_gain", "get_squelch"):
        for call in getattr(radio, method_name).call_args_list:
            assert call.args == (0,) or call.args == (), (
                f"SUB receiver was polled via {method_name}"
            )


# ---------------------------------------------------------------------------
# New RadioState fields in to_dict() (#551)
# ---------------------------------------------------------------------------


def test_new_fields_in_to_dict() -> None:
    """All #551 fields must appear in RadioState.to_dict() output."""
    state = RadioState()
    d = state.to_dict()
    for key in (
        "cw_spot",
        "yaesu",
        "break_in_delay",
        "key_speed",
        "cw_pitch",
        "break_in",
    ):
        assert key in d, f"{key} missing from to_dict()"
    # ReceiverState fields live under main/sub
    assert "apf_on" in d["main"]
    assert "apf_freq" in d["main"]


# ---------------------------------------------------------------------------
# CW polling block populates all fields (#551)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_poll_reads_full_cw_block() -> None:
    """Slow poll populates key_speed, cw_pitch, break_in, break_in_delay, cw_spot."""
    radio = make_radio()
    radio.get_keyer_speed = AsyncMock(return_value=30)
    radio.get_cw_pitch = AsyncMock(return_value=750)  # Hz (#1162)
    radio.get_break_in = AsyncMock(return_value=True)
    radio.get_break_in_delay = AsyncMock(return_value=42)
    radio.get_cw_spot = AsyncMock(return_value=True)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    assert radio.radio_state.key_speed == 30
    assert radio.radio_state.cw_pitch == 750
    assert radio.radio_state.break_in == 1
    assert radio.radio_state.break_in_delay == 42
    assert radio.radio_state.cw_spot is True


# ---------------------------------------------------------------------------
# FR/FT polling populates rx_func_mode / tx_func_mode (#551)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_poll_reads_rx_tx_func_mode() -> None:
    """FR/FT polling populates rx_func_mode and tx_func_mode."""
    radio = make_radio()
    radio.get_rx_func = AsyncMock(return_value=1)
    radio.get_tx_func = AsyncMock(return_value=1)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_rx_func.assert_called()
    radio.get_tx_func.assert_called()
    assert radio.radio_state.yaesu is not None
    assert radio.radio_state.yaesu.rx_func_mode == 1
    assert radio.radio_state.yaesu.tx_func_mode == 1


@pytest.mark.asyncio
async def test_slow_poll_skips_fr_ft_without_dual_rx() -> None:
    """Without dual_rx capability, FR/FT should not be polled."""
    radio = make_radio()
    radio.capabilities.discard("dual_rx")
    radio.get_rx_func = AsyncMock(return_value=1)
    radio.get_tx_func = AsyncMock(return_value=1)

    poller = YaesuCatPoller(
        radio,
        callback=lambda s: None,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=0.01,
    )
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()

    radio.get_rx_func.assert_not_called()
    radio.get_tx_func.assert_not_called()


# ---------------------------------------------------------------------------
# Command dispatch — SetApf (formerly dropped as "Icom-only DSP feature")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_command_set_apf_dispatches_to_radio() -> None:
    """SetApf must reach radio.set_audio_peak_filter — used to be silently dropped."""
    from rigplane.runtime._poller_types import SetApf

    radio = make_radio()
    radio.set_audio_peak_filter = AsyncMock()
    poller = YaesuCatPoller(radio, callback=lambda s: None, fast_interval=10.0)

    await poller._execute_command(SetApf(mode=1, receiver=0))

    radio.set_audio_peak_filter.assert_awaited_once_with(1, receiver=0)


@pytest.mark.asyncio
async def test_execute_command_set_apf_off_dispatches_to_radio() -> None:
    """SetApf(mode=0) reaches the canonical entry point too."""
    from rigplane.runtime._poller_types import SetApf

    radio = make_radio()
    radio.set_audio_peak_filter = AsyncMock()
    poller = YaesuCatPoller(radio, callback=lambda s: None, fast_interval=10.0)

    await poller._execute_command(SetApf(mode=0, receiver=0))

    radio.set_audio_peak_filter.assert_awaited_once_with(0, receiver=0)


# ---------------------------------------------------------------------------
# Command dispatch — SetPower unit-tag (#1168)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_command_set_power_watts_unit_dispatches_to_radio() -> None:
    """SetPower(unit='watts') flows directly to radio.set_power(watts)."""
    from rigplane.runtime._poller_types import SetPower

    radio = make_radio()
    radio.set_power = AsyncMock()
    poller = YaesuCatPoller(radio, callback=lambda s: None, fast_interval=10.0)

    await poller._execute_command(SetPower(level=50, unit="watts"))

    radio.set_power.assert_awaited_once_with(50)


@pytest.mark.asyncio
async def test_execute_command_set_power_raw_255_unit_rejected() -> None:
    """SetPower with wrong unit raises ValueError so the caller can report failure."""
    from rigplane.runtime._poller_types import SetPower

    radio = make_radio()
    radio.set_power = AsyncMock()
    poller = YaesuCatPoller(radio, callback=lambda s: None, fast_interval=10.0)

    with pytest.raises(ValueError, match="unit='raw_255'"):
        await poller._execute_command(SetPower(level=200))  # default unit='raw_255'

    radio.set_power.assert_not_awaited()
