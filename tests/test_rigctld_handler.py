"""Tests for RigctldHandler — command dispatch, cache, read-only, exceptions."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from _caps import FULL_ICOM_CAPS
from rigplane.exceptions import ConnectionError as IcomConnectionError
from rigplane.exceptions import TimeoutError as IcomTimeoutError
from rigplane.radio_state import RadioState
from rigplane.rigctld.contract import HamlibError, RigctldCommand, RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.types import CivFrame, Mode

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> RigctldConfig:
    return RigctldConfig()


@pytest.fixture
def mock_radio() -> AsyncMock:
    """Radio mock; implements MetersCapable (get_s_meter, get_swr, get_rf_power) for get_level tests."""
    radio = AsyncMock()
    radio.capabilities = set(FULL_ICOM_CAPS)
    radio.get_data_mode.return_value = False
    radio.get_s_meter = AsyncMock(return_value=0)
    radio.get_swr = AsyncMock(return_value=0.0)
    radio.get_rf_power = AsyncMock(return_value=0)
    radio.get_comp_meter = AsyncMock(return_value=0)
    radio.get_id_meter = AsyncMock(return_value=0)
    radio.get_vd_meter = AsyncMock(return_value=0)
    return radio


@pytest.fixture
def handler(mock_radio: AsyncMock, config: RigctldConfig) -> RigctldHandler:
    return RigctldHandler(mock_radio, config)


def get_cmd(long_cmd: str, *args: str) -> RigctldCommand:
    return RigctldCommand(
        short_cmd="", long_cmd=long_cmd, args=tuple(args), is_set=False
    )


def set_cmd(long_cmd: str, *args: str) -> RigctldCommand:
    return RigctldCommand(
        short_cmd="", long_cmd=long_cmd, args=tuple(args), is_set=True
    )


class _ContractModeRadio:
    def __init__(
        self,
        *,
        mode: str = "USB",
        filter_width: int | None = 1,
        data_mode: bool = False,
    ) -> None:
        self.mode = mode
        self.filter_width = filter_width
        self.data_mode = data_mode
        self.set_mode_calls: list[tuple[str, int | None, int]] = []
        self.set_data_mode_calls: list[int | bool] = []

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        assert receiver == 0
        return self.mode, self.filter_width

    async def set_mode(
        self,
        mode: str,
        filter_width: int | None = None,
        receiver: int = 0,
    ) -> None:
        self.set_mode_calls.append((mode, filter_width, receiver))
        self.mode = mode
        self.filter_width = filter_width

    async def get_data_mode(self) -> bool:
        return self.data_mode

    async def set_data_mode(self, on: int | bool) -> None:
        self.set_data_mode_calls.append(on)
        self.data_mode = bool(on)


# ---------------------------------------------------------------------------
# get_freq / set_freq
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_freq_returns_frequency(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_freq.return_value = 14_074_000
    resp = await handler.execute(get_cmd("get_freq"))
    assert resp.ok
    assert resp.values == ["14074000"]
    mock_radio.get_freq.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_freq_served_from_cache(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_freq.return_value = 14_074_000
    cmd = get_cmd("get_freq")
    resp1 = await handler.execute(cmd)
    resp2 = await handler.execute(cmd)
    assert resp1.values == resp2.values
    mock_radio.get_freq.assert_awaited_once()  # only one real call


@pytest.mark.asyncio
async def test_get_freq_prefers_radio_state(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    state = RadioState()
    state.main.freq = 14_074_000
    mock_radio.radio_state = state

    resp = await handler.execute(get_cmd("get_freq"))

    assert resp.ok
    assert resp.values == ["14074000"]
    mock_radio.get_freq.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_freq_cache_expires(mock_radio: AsyncMock) -> None:
    config = RigctldConfig(cache_ttl=0.0)  # zero TTL → always expired
    h = RigctldHandler(mock_radio, config)
    mock_radio.get_freq.side_effect = [14_074_000, 7_050_000]
    cmd = get_cmd("get_freq")
    r1 = await h.execute(cmd)
    r2 = await h.execute(cmd)
    assert r1.values == ["14074000"]
    assert r2.values == ["7050000"]
    assert mock_radio.get_freq.await_count == 2


@pytest.mark.asyncio
async def test_set_freq_calls_radio(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_freq", "14074000"))
    assert resp.ok
    mock_radio.set_freq.assert_awaited_once_with(14_074_000, receiver=0)


@pytest.mark.asyncio
async def test_set_freq_invalidates_cache(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_freq.return_value = 14_074_000
    await handler.execute(get_cmd("get_freq"))  # populate cache

    await handler.execute(set_cmd("set_freq", "7050000"))

    resp = await handler.execute(get_cmd("get_freq"))
    assert resp.values == ["7050000"]
    assert mock_radio.get_freq.await_count == 1


@pytest.mark.asyncio
async def test_set_freq_invalid_arg(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_freq", "not_a_number"))
    assert resp.error == HamlibError.EINVAL
    mock_radio.set_freq.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_freq_no_args(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_freq"))
    assert resp.error == HamlibError.EINVAL


# ---------------------------------------------------------------------------
# get_mode / set_mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mode_returns_mode_and_passband(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_mode_info.return_value = (Mode.USB, 2)
    resp = await handler.execute(get_cmd("get_mode"))
    assert resp.ok
    assert resp.values[0] == "USB"
    assert resp.values[1] == "2400"  # FIL2 → 2400 Hz


@pytest.mark.asyncio
async def test_get_mode_none_filter_returns_zero_passband(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_mode_info.return_value = (Mode.CW, None)
    resp = await handler.execute(get_cmd("get_mode"))
    assert resp.values[0] == "CW"
    assert resp.values[1] == "0"


@pytest.mark.asyncio
async def test_get_mode_falls_back_to_core_radio_contract() -> None:
    radio = _ContractModeRadio(mode="LSB", filter_width=2)
    h = RigctldHandler(radio, RigctldConfig())
    resp = await h.execute(get_cmd("get_mode"))
    assert resp.ok
    assert resp.values == ["LSB", "2400"]


@pytest.mark.asyncio
async def test_get_mode_served_from_cache(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_mode_info.return_value = (Mode.USB, 1)
    cmd = get_cmd("get_mode")
    await handler.execute(cmd)
    await handler.execute(cmd)
    mock_radio.get_mode_info.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_mode_prefers_radio_state(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    state = RadioState()
    state.main.freq = 14_074_000
    state.main.mode = "USB"
    state.main.filter = 2
    state.main.data_mode = True
    mock_radio.radio_state = state

    resp = await handler.execute(get_cmd("get_mode"))

    assert resp.ok
    assert resp.values == ["PKTUSB", "2400"]
    mock_radio.get_mode_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_mode_cache_expires(mock_radio: AsyncMock) -> None:
    config = RigctldConfig(cache_ttl=0.0)
    h = RigctldHandler(mock_radio, config)
    mock_radio.get_mode_info.side_effect = [(Mode.USB, 1), (Mode.LSB, 1)]
    await h.execute(get_cmd("get_mode"))
    await h.execute(get_cmd("get_mode"))
    assert mock_radio.get_mode_info.await_count == 2


@pytest.mark.asyncio
async def test_set_mode_calls_radio(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_mode", "USB", "2400"))
    assert resp.ok
    mock_radio.set_mode.assert_awaited_once_with("USB", filter_width=2)


@pytest.mark.asyncio
async def test_set_mode_without_passband_uses_none_filter(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_mode", "LSB"))
    assert resp.ok
    mock_radio.set_mode.assert_awaited_once_with("LSB", filter_width=None)


@pytest.mark.asyncio
async def test_set_mode_non_packet_does_not_force_data_change(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_mode", "LSB"))
    assert resp.ok
    mock_radio.set_data_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_mode_passband_zero_uses_none_filter(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_mode", "FM", "0"))
    assert resp.ok
    mock_radio.set_mode.assert_awaited_once_with("FM", filter_width=None)


@pytest.mark.asyncio
async def test_set_mode_pktrtty_maps_to_rtty_and_sets_data(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_mode", "PKTRTTY"))
    assert resp.ok
    mock_radio.set_mode.assert_awaited_once_with("RTTY", filter_width=None)
    mock_radio.set_data_mode.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_mode_uses_core_contract_string_values() -> None:
    radio = _ContractModeRadio(mode="USB", filter_width=1, data_mode=False)
    h = RigctldHandler(radio, RigctldConfig())
    resp = await h.execute(set_cmd("set_mode", "PKTUSB", "2400"))
    assert resp.ok
    assert radio.set_mode_calls == [("USB", 2, 0)]
    assert radio.set_data_mode_calls == [True]


@pytest.mark.asyncio
async def test_set_mode_packet_uses_configured_wsjtx_data_mode() -> None:
    radio = AsyncMock()
    radio.set_mode = AsyncMock()
    radio.set_data_mode = AsyncMock()
    radio.set_data2_mod_input = AsyncMock()
    radio.get_mode_info = AsyncMock(return_value=(Mode.USB, 1))
    radio.get_data_mode = AsyncMock(return_value=True)
    radio.profile = type("Profile", (), {"data_mode_count": 3})()
    h = RigctldHandler(
        radio,
        RigctldConfig(wsjtx_data_mode=2, wsjtx_data_mod_input=5),
    )

    resp = await h.execute(set_cmd("set_mode", "PKTUSB", "2400"))

    assert resp.ok
    radio.set_mode.assert_awaited_once_with("USB", filter_width=2)
    radio.set_data2_mod_input.assert_awaited_once_with(5)
    radio.set_data_mode.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_set_mode_packet_configured_data2_falls_back_on_single_data_profile() -> (
    None
):
    radio = AsyncMock()
    radio.set_mode = AsyncMock()
    radio.set_data_mode = AsyncMock()
    radio.set_data2_mod_input = AsyncMock()
    radio.get_mode_info = AsyncMock(return_value=(Mode.USB, 1))
    radio.get_data_mode = AsyncMock(return_value=True)
    radio.profile = type("Profile", (), {"data_mode_count": 1})()
    h = RigctldHandler(
        radio,
        RigctldConfig(wsjtx_data_mode=2, wsjtx_data_mod_input=5),
    )

    resp = await h.execute(set_cmd("set_mode", "PKTUSB", "2400"))

    assert resp.ok
    radio.set_data2_mod_input.assert_not_called()
    radio.set_data_mode.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_mode_packet_without_wsjtx_data_config_does_not_touch_data_mod_input() -> (
    None
):
    radio = AsyncMock()
    radio.set_mode = AsyncMock()
    radio.set_data_mode = AsyncMock()
    radio.set_data1_mod_input = AsyncMock()
    radio.set_data2_mod_input = AsyncMock()
    radio.get_mode_info = AsyncMock(return_value=(Mode.USB, 1))
    radio.get_data_mode = AsyncMock(return_value=True)
    radio.profile = type("Profile", (), {"data_mode_count": 3})()
    h = RigctldHandler(radio, RigctldConfig())

    resp = await h.execute(set_cmd("set_mode", "PKTUSB", "2400"))

    assert resp.ok
    radio.set_data1_mod_input.assert_not_called()
    radio.set_data2_mod_input.assert_not_called()
    radio.set_data_mode.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_mode_invalid_mode(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_mode", "INVALID"))
    assert resp.error == HamlibError.EINVAL
    mock_radio.set_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_mode_refreshes_cache_immediately(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_mode_info.return_value = (Mode.USB, 1)
    await handler.execute(get_cmd("get_mode"))  # populate from radio

    await handler.execute(set_cmd("set_mode", "LSB"))  # updates cache directly

    resp = await handler.execute(get_cmd("get_mode"))
    assert resp.values[0] == "LSB"
    # No extra radio read needed after set_mode.
    assert mock_radio.get_mode_info.await_count == 1


@pytest.mark.asyncio
async def test_get_freq_keeps_optimistic_value_until_radio_state_catches_up(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    state = RadioState()
    state.main.freq = 14_074_000
    mock_radio.radio_state = state

    await handler.execute(set_cmd("set_freq", "7050000"))

    resp = await handler.execute(get_cmd("get_freq"))
    assert resp.values == ["7050000"]

    state.main.freq = 7_050_000
    resp = await handler.execute(get_cmd("get_freq"))
    assert resp.values == ["7050000"]


@pytest.mark.asyncio
async def test_get_mode_keeps_optimistic_value_until_radio_state_catches_up(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    state = RadioState()
    state.main.freq = 14_074_000
    state.main.mode = "USB"
    state.main.filter = 1
    state.main.data_mode = False
    mock_radio.radio_state = state

    await handler.execute(set_cmd("set_mode", "LSB", "2400"))

    resp = await handler.execute(get_cmd("get_mode"))
    assert resp.values == ["LSB", "2400"]

    state.main.mode = "LSB"
    state.main.filter = 2
    resp = await handler.execute(get_cmd("get_mode"))
    assert resp.values == ["LSB", "2400"]


# ---------------------------------------------------------------------------
# get_ptt / set_ptt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ptt_defaults_off(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(get_cmd("get_ptt"))
    assert resp.ok
    assert resp.values == ["0"]


@pytest.mark.asyncio
async def test_get_ptt_reads_radio_state(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    state = RadioState()
    state.ptt = True
    mock_radio.radio_state = state

    resp = await handler.execute(get_cmd("get_ptt"))

    assert resp.ok
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_set_ptt_on(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_ptt", "1"))
    assert resp.ok
    mock_radio.set_ptt.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_ptt_off(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    await handler.execute(set_cmd("set_ptt", "1"))
    resp = await handler.execute(set_cmd("set_ptt", "0"))
    assert resp.ok
    mock_radio.set_ptt.assert_awaited_with(False)


@pytest.mark.asyncio
async def test_ptt_state_reflected_in_get(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    await handler.execute(set_cmd("set_ptt", "1"))
    resp = await handler.execute(get_cmd("get_ptt"))
    assert resp.values == ["1"]

    await handler.execute(set_cmd("set_ptt", "0"))
    resp = await handler.execute(get_cmd("get_ptt"))
    assert resp.values == ["0"]


@pytest.mark.asyncio
async def test_get_ptt_keeps_optimistic_state_until_radio_state_catches_up(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    state = RadioState()
    state.ptt = False
    mock_radio.radio_state = state

    await handler.execute(set_cmd("set_ptt", "1"))
    resp = await handler.execute(get_cmd("get_ptt"))
    assert resp.values == ["1"]

    state.ptt = True
    resp = await handler.execute(get_cmd("get_ptt"))
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_set_ptt_invalid_arg(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_ptt", "x"))
    assert resp.error == HamlibError.EINVAL


# ---------------------------------------------------------------------------
# get_vfo / set_vfo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_vfo_returns_vfoa(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(get_cmd("get_vfo"))
    assert resp.ok
    assert resp.values == ["VFOA"]


@pytest.mark.asyncio
async def test_set_vfo_accepts_any_name(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_vfo", "VFOB"))
    assert resp.ok


# ---------------------------------------------------------------------------
# get_level
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_level_strength(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_s_meter.return_value = 120  # ≈ S9 → 0 dB
    resp = await handler.execute(get_cmd("get_level", "STRENGTH"))
    assert resp.ok
    strength = int(resp.values[0])
    assert -60 <= strength <= 70  # reasonable dB range


@pytest.mark.asyncio
async def test_get_level_strength_prefers_radio_state(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    state = RadioState()
    state.main.freq = 14_074_000
    state.main.s_meter = 120
    mock_radio.radio_state = state

    resp = await handler.execute(get_cmd("get_level", "STRENGTH"))

    assert resp.ok
    assert int(resp.values[0]) == pytest.approx(3, abs=1)
    mock_radio.get_s_meter.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_level_strength_s0(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_s_meter.return_value = 0
    resp = await handler.execute(get_cmd("get_level", "STRENGTH"))
    assert resp.ok
    assert int(resp.values[0]) == -54


@pytest.mark.asyncio
async def test_get_level_rfpower(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_rf_power.return_value = 255
    resp = await handler.execute(get_cmd("get_level", "RFPOWER"))
    assert resp.ok
    value = float(resp.values[0])
    assert abs(value - 1.0) < 0.001


@pytest.mark.asyncio
async def test_get_level_rfpower_prefers_radio_state(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    state = RadioState()
    state.main.freq = 14_074_000
    state.power_level = 128
    mock_radio.radio_state = state

    resp = await handler.execute(get_cmd("get_level", "RFPOWER"))

    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(128 / 255.0, rel=1e-6)
    mock_radio.get_rf_power.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_level_rfpower_zero(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_rf_power.return_value = 0
    resp = await handler.execute(get_cmd("get_level", "RFPOWER"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_get_level_swr(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    # ``get_swr`` is contracted as a calibrated ratio (>= 1.0) — the
    # rigctld handler now passes the float through without remapping.
    mock_radio.get_swr.return_value = 1.0
    resp = await handler.execute(get_cmd("get_level", "SWR"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_get_level_swr_max(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_swr.return_value = 6.0
    resp = await handler.execute(get_cmd("get_level", "SWR"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(6.0)


@pytest.mark.asyncio
async def test_get_level_no_args(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(get_cmd("get_level"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_get_level_unknown(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(get_cmd("get_level", "NOSUCHLEVEL"))
    assert resp.error == HamlibError.EINVAL


# ---------------------------------------------------------------------------
# get_split_vfo / set_split_vfo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_split_vfo(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(get_cmd("get_split_vfo"))
    assert resp.ok
    assert resp.values[0] == "0"
    assert resp.values[1] == "VFOA"


@pytest.mark.asyncio
async def test_get_split_vfo_reads_radio_state(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    state = RadioState()
    state.split = True
    mock_radio.radio_state = state

    resp = await handler.execute(get_cmd("get_split_vfo"))

    assert resp.ok
    assert resp.values == ["1", "VFOA"]


@pytest.mark.asyncio
async def test_set_split_vfo(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_split_vfo", "0", "VFOA"))
    assert resp.ok


# ---------------------------------------------------------------------------
# Info / control commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dump_state(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(get_cmd("dump_state"))
    assert resp.ok
    lines = resp.values

    # Line 0: protocol version (atol parseable)
    assert lines[0] == "0"
    # Line 1: rig model — IC-7610 hamlib model number
    assert lines[1] == "3078"
    # Line 2: ITU region
    assert lines[2] == "1"
    # Line 3: RX range (7 fields: startf endf modes low_power high_power vfo ant)
    assert lines[3] == "100000.000000 60000000.000000 0x1ff -1 -1 0x3 0xf"
    # Line 4: end of RX ranges sentinel
    assert lines[4] == "0 0 0 0 0 0 0"
    # Line 5: TX range
    assert lines[5] == "1800000.000000 60000000.000000 0x1ff 5000 100000 0x3 0xf"
    # Line 6: end of TX ranges sentinel
    assert lines[6] == "0 0 0 0 0 0 0"
    # Line 7: tuning step (modes ts)
    assert lines[7] == "0x1ff 1"
    # Line 8: end of tuning steps sentinel
    assert lines[8] == "0 0"
    # Lines 9-11: filters (modes width)
    assert lines[9] == "0x1ff 3000"
    assert lines[10] == "0x1ff 2400"
    assert lines[11] == "0x1ff 1800"
    # Line 12: end of filters sentinel
    assert lines[12] == "0 0"
    # Lines 13-16: bare scalars — no 'key: value' prefix
    assert lines[13] == "9999"  # max_rit
    assert lines[14] == "9999"  # max_xit
    assert lines[15] == "0"  # max_ifshift
    assert lines[16] == "0"  # announces
    # Lines 17-18: preamp/attenuator — space-separated ints, 0-terminated
    assert lines[17] == "12 20 0"
    assert lines[18] == "6 12 18 0"
    # Lines 19-24: capability bitmasks — bare hex/int, no label prefix
    assert lines[19] == "0x00011B3E"  # has_get_func
    assert lines[20] == "0x00011B3E"  # has_set_func
    assert lines[21] == "0x5401791B"  # has_get_level
    assert lines[22] == "0x0001791B"  # has_set_level
    assert lines[23] == "0"  # has_get_parm
    assert lines[24] == "0"  # has_set_parm
    assert len(lines) == 25


@pytest.mark.asyncio
async def test_dump_caps_same_as_dump_state(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    r1 = await handler.execute(get_cmd("dump_state"))
    r2 = await handler.execute(get_cmd("dump_caps"))
    assert r1.values == r2.values


@pytest.mark.asyncio
async def test_get_info(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(get_cmd("get_info"))
    assert resp.ok
    assert "IC-7610" in resp.values[0]


@pytest.mark.asyncio
async def test_get_info_uses_runtime_model(config: RigctldConfig) -> None:
    radio = AsyncMock()
    radio.model = "IC-9700"
    h = RigctldHandler(radio, config)
    resp = await h.execute(get_cmd("get_info"))
    assert resp.ok
    assert "IC-9700" in resp.values[0]


@pytest.mark.asyncio
async def test_chk_vfo(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(get_cmd("chk_vfo"))
    assert resp.ok
    assert resp.values == ["0"]


@pytest.mark.asyncio
async def test_get_powerstat(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_powerstat = AsyncMock(return_value=True)
    resp = await handler.execute(get_cmd("get_powerstat"))
    assert resp.ok
    assert resp.values == ["1"]
    mock_radio.get_powerstat.assert_awaited_once()


@pytest.mark.asyncio
async def test_rigctld_get_powerstat_returns_real_value(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """get_powerstat dispatches to radio and reflects ON vs STANDBY."""
    # Radio reports STANDBY (off).
    mock_radio.get_powerstat = AsyncMock(return_value=False)
    resp = await handler.execute(get_cmd("get_powerstat"))
    assert resp.ok
    assert resp.values == ["0"]
    mock_radio.get_powerstat.assert_awaited_once()

    # Radio reports ON.
    mock_radio.get_powerstat = AsyncMock(return_value=True)
    resp = await handler.execute(get_cmd("get_powerstat"))
    assert resp.ok
    assert resp.values == ["1"]
    mock_radio.get_powerstat.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_rit(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(get_cmd("get_rit"))
    assert resp.ok
    assert resp.values == ["0"]


@pytest.mark.asyncio
async def test_get_rit_reads_radio_state(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    state = RadioState()
    state.rit_freq = -250
    mock_radio.radio_state = state

    resp = await handler.execute(get_cmd("get_rit"))

    assert resp.ok
    assert resp.values == ["-250"]


@pytest.mark.asyncio
async def test_set_rit_calls_radio(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.set_rit_frequency = AsyncMock()
    mock_radio.set_rit_status = AsyncMock()
    resp = await handler.execute(set_cmd("set_rit", "500"))
    assert resp.ok
    mock_radio.set_rit_frequency.assert_awaited_once_with(500)
    mock_radio.set_rit_status.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_rit_zero_disables(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.set_rit_frequency = AsyncMock()
    mock_radio.set_rit_status = AsyncMock()
    resp = await handler.execute(set_cmd("set_rit", "0"))
    assert resp.ok
    mock_radio.set_rit_frequency.assert_awaited_once_with(0)
    mock_radio.set_rit_status.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_set_rit_negative_hz(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.set_rit_frequency = AsyncMock()
    mock_radio.set_rit_status = AsyncMock()
    resp = await handler.execute(set_cmd("set_rit", "-250"))
    assert resp.ok
    mock_radio.set_rit_frequency.assert_awaited_once_with(-250)
    mock_radio.set_rit_status.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_rit_missing_arg_returns_einval(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_rit"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_set_rit_invalid_arg_returns_einval(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_rit", "bad"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_set_rit_no_cap_returns_enimpl(
    mock_radio: AsyncMock, config: RigctldConfig
) -> None:
    mock_radio.capabilities = set()
    h = RigctldHandler(mock_radio, config)
    resp = await h.execute(set_cmd("set_rit", "100"))
    assert resp.error == HamlibError.ENIMPL


@pytest.mark.asyncio
async def test_get_xit_reads_radio_state(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    state = RadioState()
    state.rit_freq = 300
    mock_radio.radio_state = state
    resp = await handler.execute(get_cmd("get_xit"))
    assert resp.ok
    assert resp.values == ["300"]


@pytest.mark.asyncio
async def test_get_xit_default_zero(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(get_cmd("get_xit"))
    assert resp.ok
    assert resp.values == ["0"]


@pytest.mark.asyncio
async def test_set_xit_calls_radio(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.set_rit_frequency = AsyncMock()
    mock_radio.set_rit_tx_status = AsyncMock()
    resp = await handler.execute(set_cmd("set_xit", "750"))
    assert resp.ok
    mock_radio.set_rit_frequency.assert_awaited_once_with(750)
    mock_radio.set_rit_tx_status.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_xit_zero_disables(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.set_rit_frequency = AsyncMock()
    mock_radio.set_rit_tx_status = AsyncMock()
    resp = await handler.execute(set_cmd("set_xit", "0"))
    assert resp.ok
    mock_radio.set_rit_frequency.assert_awaited_once_with(0)
    mock_radio.set_rit_tx_status.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_set_xit_missing_arg_returns_einval(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_xit"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_set_xit_no_cap_returns_enimpl(
    mock_radio: AsyncMock, config: RigctldConfig
) -> None:
    mock_radio.capabilities = set()
    h = RigctldHandler(mock_radio, config)
    resp = await h.execute(set_cmd("set_xit", "100"))
    assert resp.error == HamlibError.ENIMPL


@pytest.mark.asyncio
async def test_quit_returns_ok_with_echo(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(get_cmd("quit"))
    assert resp.ok
    assert resp.cmd_echo == "quit"


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_command_returns_enimpl(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    cmd = RigctldCommand(short_cmd="?", long_cmd="totally_unknown_cmd")
    resp = await handler.execute(cmd)
    assert resp.error == HamlibError.ENIMPL


# ---------------------------------------------------------------------------
# Read-only mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_rejects_set_freq(mock_radio: AsyncMock) -> None:
    h = RigctldHandler(mock_radio, RigctldConfig(read_only=True))
    resp = await h.execute(set_cmd("set_freq", "14074000"))
    assert resp.error == HamlibError.EACCESS
    mock_radio.set_freq.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_only_rejects_set_mode(mock_radio: AsyncMock) -> None:
    h = RigctldHandler(mock_radio, RigctldConfig(read_only=True))
    resp = await h.execute(set_cmd("set_mode", "USB"))
    assert resp.error == HamlibError.EACCESS


@pytest.mark.asyncio
async def test_read_only_rejects_set_ptt(mock_radio: AsyncMock) -> None:
    h = RigctldHandler(mock_radio, RigctldConfig(read_only=True))
    resp = await h.execute(set_cmd("set_ptt", "1"))
    assert resp.error == HamlibError.EACCESS
    mock_radio.set_ptt.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_only_allows_get_freq(mock_radio: AsyncMock) -> None:
    h = RigctldHandler(mock_radio, RigctldConfig(read_only=True))
    mock_radio.get_freq.return_value = 14_074_000
    resp = await h.execute(get_cmd("get_freq"))
    assert resp.ok


@pytest.mark.asyncio
async def test_read_only_allows_get_mode(mock_radio: AsyncMock) -> None:
    h = RigctldHandler(mock_radio, RigctldConfig(read_only=True))
    mock_radio.get_mode_info.return_value = (Mode.USB, None)
    resp = await h.execute(get_cmd("get_mode"))
    assert resp.ok


# ---------------------------------------------------------------------------
# Exception translation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_error_becomes_eio(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_freq.side_effect = IcomConnectionError("lost")
    resp = await handler.execute(get_cmd("get_freq"))
    assert resp.error == HamlibError.EIO


@pytest.mark.asyncio
async def test_timeout_error_becomes_etimeout(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_freq.side_effect = IcomTimeoutError("timeout")
    resp = await handler.execute(get_cmd("get_freq"))
    assert resp.error == HamlibError.ETIMEOUT


@pytest.mark.asyncio
async def test_value_error_becomes_einval(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_freq.side_effect = ValueError("bad value")
    resp = await handler.execute(get_cmd("get_freq"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_unexpected_exception_becomes_einternal(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_freq.side_effect = RuntimeError("unexpected")
    resp = await handler.execute(get_cmd("get_freq"))
    assert resp.error == HamlibError.EINTERNAL


@pytest.mark.asyncio
async def test_connection_error_on_set_freq_becomes_eio(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.set_freq.side_effect = IcomConnectionError("lost")
    resp = await handler.execute(set_cmd("set_freq", "14074000"))
    assert resp.error == HamlibError.EIO


@pytest.mark.asyncio
async def test_timeout_error_on_set_ptt_becomes_etimeout(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.set_ptt.side_effect = IcomTimeoutError("timeout")
    resp = await handler.execute(set_cmd("set_ptt", "1"))
    assert resp.error == HamlibError.ETIMEOUT


# ---------------------------------------------------------------------------
# Passband / filter mapping helpers (unit tests)
# ---------------------------------------------------------------------------


def test_passband_to_filter_zero_gives_none() -> None:
    from rigplane.rigctld.handler import _passband_to_filter

    assert _passband_to_filter(0) is None


def test_passband_to_filter_negative_gives_none() -> None:
    from rigplane.rigctld.handler import _passband_to_filter

    assert _passband_to_filter(-1) is None


def test_passband_to_filter_wide_gives_fil1() -> None:
    from rigplane.rigctld.handler import _passband_to_filter

    assert _passband_to_filter(3000) == 1


def test_passband_to_filter_medium_gives_fil2() -> None:
    from rigplane.rigctld.handler import _passband_to_filter

    assert _passband_to_filter(2400) == 2


def test_passband_to_filter_narrow_gives_fil3() -> None:
    from rigplane.rigctld.handler import _passband_to_filter

    assert _passband_to_filter(1800) == 3


def test_filter_to_passband_none_gives_zero() -> None:
    from rigplane.rigctld.handler import _filter_to_passband

    assert _filter_to_passband(None) == 0


def test_filter_to_passband_fil1() -> None:
    from rigplane.rigctld.handler import _filter_to_passband

    assert _filter_to_passband(1) == 3000


def test_filter_to_passband_fil2() -> None:
    from rigplane.rigctld.handler import _filter_to_passband

    assert _filter_to_passband(2) == 2400


def test_filter_to_passband_fil3() -> None:
    from rigplane.rigctld.handler import _filter_to_passband

    assert _filter_to_passband(3) == 1800
    assert _filter_to_passband(3) == 1800


# ---------------------------------------------------------------------------
# get_level — new levels (AF, RF, NR, NB, COMP, MICGAIN, MONITOR_GAIN,
#              KEYSPD, CWPITCH, PREAMP, ATT, RFPOWER_METER, COMP_METER,
#              ID_METER, VD_METER)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_level_af(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_af_level = AsyncMock(return_value=128)
    resp = await handler.execute(get_cmd("get_level", "AF"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(128 / 255.0, rel=1e-6)


@pytest.mark.asyncio
async def test_get_level_rf_gain(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_rf_gain = AsyncMock(return_value=255)
    resp = await handler.execute(get_cmd("get_level", "RF"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_rigctld_get_level_sql_icom(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """get_level SQL on Icom uses LevelsCapable.get_squelch — no AttributeError (issue #1093)."""
    mock_radio.get_squelch = AsyncMock(return_value=128)
    resp = await handler.execute(get_cmd("get_level", "SQL"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(128 / 255.0, rel=1e-6)
    mock_radio.get_squelch.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_get_level_nr(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_nr_level = AsyncMock(return_value=0)
    resp = await handler.execute(get_cmd("get_level", "NR"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_get_level_nb(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_nb_level = AsyncMock(return_value=51)  # 51/255 ≈ 0.2
    resp = await handler.execute(get_cmd("get_level", "NB"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(51 / 255.0, rel=1e-5)


@pytest.mark.asyncio
async def test_get_level_comp(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_compressor_level = AsyncMock(return_value=255)
    resp = await handler.execute(get_cmd("get_level", "COMP"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_get_level_micgain(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_mic_gain = AsyncMock(return_value=0)
    resp = await handler.execute(get_cmd("get_level", "MICGAIN"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_get_level_monitor_gain(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_monitor_gain = AsyncMock(return_value=128)
    resp = await handler.execute(get_cmd("get_level", "MONITOR_GAIN"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(128 / 255.0, rel=1e-5)


@pytest.mark.asyncio
async def test_get_level_keyspd(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_key_speed = AsyncMock(return_value=20)
    resp = await handler.execute(get_cmd("get_level", "KEYSPD"))
    assert resp.ok
    assert resp.values[0] == "20"


@pytest.mark.asyncio
async def test_get_level_cwpitch(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_cw_pitch = AsyncMock(return_value=600)
    resp = await handler.execute(get_cmd("get_level", "CWPITCH"))
    assert resp.ok
    assert resp.values[0] == "600"


@pytest.mark.asyncio
async def test_get_level_preamp_off(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_preamp = AsyncMock(return_value=0)
    resp = await handler.execute(get_cmd("get_level", "PREAMP"))
    assert resp.ok
    assert resp.values[0] == "0"


@pytest.mark.asyncio
async def test_get_level_preamp_1(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_preamp = AsyncMock(return_value=1)
    resp = await handler.execute(get_cmd("get_level", "PREAMP"))
    assert resp.ok
    assert resp.values[0] == "12"


@pytest.mark.asyncio
async def test_get_level_preamp_2(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_preamp = AsyncMock(return_value=2)
    resp = await handler.execute(get_cmd("get_level", "PREAMP"))
    assert resp.ok
    assert resp.values[0] == "20"


@pytest.mark.asyncio
async def test_get_level_att_off(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_attenuator_level = AsyncMock(return_value=0)
    resp = await handler.execute(get_cmd("get_level", "ATT"))
    assert resp.ok
    assert resp.values[0] == "0"


@pytest.mark.asyncio
async def test_get_level_att_18db(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_attenuator_level = AsyncMock(return_value=18)
    resp = await handler.execute(get_cmd("get_level", "ATT"))
    assert resp.ok
    assert resp.values[0] == "18"


@pytest.mark.asyncio
async def test_get_level_rfpower_meter(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_power_meter = AsyncMock(return_value=255)
    resp = await handler.execute(get_cmd("get_level", "RFPOWER_METER"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_get_level_comp_meter(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_comp_meter = AsyncMock(return_value=128)
    resp = await handler.execute(get_cmd("get_level", "COMP_METER"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(128 / 255.0, rel=1e-5)


@pytest.mark.asyncio
async def test_get_level_id_meter(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_id_meter = AsyncMock(return_value=0)
    resp = await handler.execute(get_cmd("get_level", "ID_METER"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_get_level_vd_meter(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    mock_radio.get_vd_meter = AsyncMock(return_value=200)
    resp = await handler.execute(get_cmd("get_level", "VD_METER"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(200 / 255.0, rel=1e-5)


# ---------------------------------------------------------------------------
# set_level (L command)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_level_af(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_level", "AF", "0.500000"))
    assert resp.ok
    mock_radio.set_af_level.assert_awaited_once_with(128)


@pytest.mark.asyncio
async def test_set_level_rf(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_level", "RF", "1.000000"))
    assert resp.ok
    mock_radio.set_rf_gain.assert_awaited_once_with(255)


@pytest.mark.asyncio
async def test_set_level_nr(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_level", "NR", "0.000000"))
    assert resp.ok
    mock_radio.set_nr_level.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_rigctld_set_level_sql_icom(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """set_level SQL on Icom dispatches to set_squelch — symmetric to #1093
    get-side fix (#1118 get_squelch). (#1163)"""
    mock_radio.set_squelch = AsyncMock()
    resp = await handler.execute(set_cmd("set_level", "SQL", "0.500000"))
    assert resp.ok
    # 0.5 * 255 = 127.5, rounds to 128 (banker's rounding: round-half-to-even)
    mock_radio.set_squelch.assert_awaited_once_with(128)


@pytest.mark.asyncio
async def test_set_level_nb(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_level", "NB", "0.200000"))
    assert resp.ok
    mock_radio.set_nb_level.assert_awaited_once_with(51)


@pytest.mark.asyncio
async def test_set_level_notchf_icom_no_attribute_error(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """rigctld smoke (#1102, closes P0-03 from #1091): set_level NOTCHF on
    an Icom-typed radio must not raise AttributeError. The Icom fallback
    path has no NOTCHF case, so the handler is expected to return EINVAL —
    crucially, without crashing on a Yaesu-only attribute."""
    resp = await handler.execute(set_cmd("set_level", "NOTCHF", "1500"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_set_level_comp(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_level", "COMP", "1.0"))
    assert resp.ok
    mock_radio.set_compressor_level.assert_awaited_once_with(255)


@pytest.mark.asyncio
async def test_set_level_micgain(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_level", "MICGAIN", "0.0"))
    assert resp.ok
    mock_radio.set_mic_gain.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_set_level_keyspd(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_level", "KEYSPD", "25"))
    assert resp.ok
    mock_radio.set_key_speed.assert_awaited_once_with(25)


@pytest.mark.asyncio
async def test_set_level_cwpitch(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_level", "CWPITCH", "700"))
    assert resp.ok
    mock_radio.set_cw_pitch.assert_awaited_once_with(700)


@pytest.mark.asyncio
async def test_set_level_preamp_off(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_level", "PREAMP", "0"))
    assert resp.ok
    mock_radio.set_preamp.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_set_level_preamp_12db(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_level", "PREAMP", "12"))
    assert resp.ok
    mock_radio.set_preamp.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_set_level_preamp_20db(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_level", "PREAMP", "20"))
    assert resp.ok
    mock_radio.set_preamp.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_set_level_att(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_level", "ATT", "18"))
    assert resp.ok
    mock_radio.set_attenuator_level.assert_awaited_once_with(18)


@pytest.mark.asyncio
async def test_set_level_att_rounds_to_nearest(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    # 10 dB is closest to 12 dB
    resp = await handler.execute(set_cmd("set_level", "ATT", "10"))
    assert resp.ok
    mock_radio.set_attenuator_level.assert_awaited_once_with(12)


@pytest.mark.asyncio
async def test_set_level_rfpower(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_level", "RFPOWER", "1.0"))
    assert resp.ok
    mock_radio.set_rf_power.assert_awaited_once_with(255)


@pytest.mark.asyncio
async def test_set_level_no_args(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_level"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_set_level_unknown(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_level", "NOSUCHLEVEL", "1.0"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_set_level_invalid_value(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_level", "AF", "notafloat"))
    assert resp.error == HamlibError.EINVAL


# ---------------------------------------------------------------------------
# get_func / set_func (u/U commands)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_func_nb_off(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_nb = AsyncMock(return_value=False)
    resp = await handler.execute(get_cmd("get_func", "NB"))
    assert resp.ok
    assert resp.values[0] == "0"


@pytest.mark.asyncio
async def test_get_func_nb_on(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_nb = AsyncMock(return_value=True)
    resp = await handler.execute(get_cmd("get_func", "NB"))
    assert resp.ok
    assert resp.values[0] == "1"


@pytest.mark.asyncio
async def test_get_func_nr(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_nr = AsyncMock(return_value=True)
    resp = await handler.execute(get_cmd("get_func", "NR"))
    assert resp.ok
    assert resp.values[0] == "1"


@pytest.mark.asyncio
async def test_get_func_comp(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_compressor = AsyncMock(return_value=False)
    resp = await handler.execute(get_cmd("get_func", "COMP"))
    assert resp.ok
    assert resp.values[0] == "0"


@pytest.mark.asyncio
async def test_get_func_vox(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_vox = AsyncMock(return_value=True)
    resp = await handler.execute(get_cmd("get_func", "VOX"))
    assert resp.ok
    assert resp.values[0] == "1"


@pytest.mark.asyncio
async def test_get_func_tone(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_repeater_tone = AsyncMock(return_value=False)
    resp = await handler.execute(get_cmd("get_func", "TONE"))
    assert resp.ok
    assert resp.values[0] == "0"


@pytest.mark.asyncio
async def test_get_func_tsql(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_repeater_tsql = AsyncMock(return_value=True)
    resp = await handler.execute(get_cmd("get_func", "TSQL"))
    assert resp.ok
    assert resp.values[0] == "1"


@pytest.mark.asyncio
async def test_get_func_anf(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_auto_notch = AsyncMock(return_value=False)
    resp = await handler.execute(get_cmd("get_func", "ANF"))
    assert resp.ok
    assert resp.values[0] == "0"


@pytest.mark.asyncio
async def test_get_func_lock(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_dial_lock = AsyncMock(return_value=True)
    resp = await handler.execute(get_cmd("get_func", "LOCK"))
    assert resp.ok
    assert resp.values[0] == "1"


@pytest.mark.asyncio
async def test_rigctld_get_func_lock_icom(config: RigctldConfig) -> None:
    """get_func LOCK on Icom backend uses canonical get_dial_lock (issue #1092).

    Icom radios implement only the canonical SystemControlCapable name
    (get_dial_lock); they do NOT expose get_lock. A spec'd mock that lacks
    get_lock would raise AttributeError if routing called the wrong method.
    """

    class _IcomLikeRadio:
        backend_id = "icom7610"

        async def get_dial_lock(self) -> bool:
            return True

    handler = RigctldHandler(_IcomLikeRadio(), config)  # type: ignore[arg-type]
    resp = await handler.execute(get_cmd("get_func", "LOCK"))
    assert resp.ok
    assert resp.values[0] == "1"


@pytest.mark.asyncio
async def test_rigctld_set_func_lock_icom(config: RigctldConfig) -> None:
    """set_func LOCK 1 on Icom backend uses canonical set_dial_lock (issue #1092)."""

    calls: list[bool] = []

    class _IcomLikeRadio:
        backend_id = "icom7610"

        async def set_dial_lock(self, on: bool) -> None:
            calls.append(on)

    handler = RigctldHandler(_IcomLikeRadio(), config)  # type: ignore[arg-type]
    resp = await handler.execute(set_cmd("set_func", "LOCK", "1"))
    assert resp.ok
    assert calls == [True]


@pytest.mark.asyncio
async def test_get_func_mon(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_monitor = AsyncMock(return_value=False)
    resp = await handler.execute(get_cmd("get_func", "MON"))
    assert resp.ok
    assert resp.values[0] == "0"


@pytest.mark.asyncio
async def test_get_func_apf_off(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_audio_peak_filter = AsyncMock(return_value=0)
    resp = await handler.execute(get_cmd("get_func", "APF"))
    assert resp.ok
    assert resp.values[0] == "0"


@pytest.mark.asyncio
async def test_get_func_apf_on(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    mock_radio.get_audio_peak_filter = AsyncMock(return_value=1)
    resp = await handler.execute(get_cmd("get_func", "APF"))
    assert resp.ok
    assert resp.values[0] == "1"


@pytest.mark.asyncio
async def test_get_func_no_args(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(get_cmd("get_func"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_get_func_unknown(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(get_cmd("get_func", "NOSUCHFUNC"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_set_func_nb_on(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "NB", "1"))
    assert resp.ok
    mock_radio.set_nb.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_func_nb_off(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "NB", "0"))
    assert resp.ok
    mock_radio.set_nb.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_set_func_nr(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "NR", "1"))
    assert resp.ok
    mock_radio.set_nr.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_func_comp(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "COMP", "0"))
    assert resp.ok
    mock_radio.set_compressor.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_set_func_vox(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "VOX", "1"))
    assert resp.ok
    mock_radio.set_vox.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_func_tone(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "TONE", "1"))
    assert resp.ok
    mock_radio.set_repeater_tone.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_func_tsql(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "TSQL", "0"))
    assert resp.ok
    mock_radio.set_repeater_tsql.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_set_func_anf(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "ANF", "1"))
    assert resp.ok
    mock_radio.set_auto_notch.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_func_lock(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "LOCK", "0"))
    assert resp.ok
    mock_radio.set_dial_lock.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_set_func_mon(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "MON", "1"))
    assert resp.ok
    mock_radio.set_monitor.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_set_func_apf_on(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "APF", "1"))
    assert resp.ok
    mock_radio.set_audio_peak_filter.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_set_func_apf_off(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "APF", "0"))
    assert resp.ok
    mock_radio.set_audio_peak_filter.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_set_func_no_args(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_set_func_unknown(handler: RigctldHandler, mock_radio: AsyncMock) -> None:
    resp = await handler.execute(set_cmd("set_func", "NOSUCHFUNC", "1"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_set_func_invalid_value(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    resp = await handler.execute(set_cmd("set_func", "NB", "notanint"))
    assert resp.error == HamlibError.EINVAL


# ---------------------------------------------------------------------------
# send_raw ('w' command) — raw CI-V passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_raw_space_separated_hex(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """Space-separated hex tokens are parsed and sent as raw bytes."""
    response = CivFrame(
        to_addr=0xE0, from_addr=0x98, command=0x03, data=b"\x00\x60\x00\x00\x00"
    )
    mock_radio._send_civ_raw = AsyncMock(return_value=response)

    resp = await handler.execute(
        get_cmd("send_raw", "FE", "FE", "98", "E0", "03", "FD")
    )

    assert resp.ok
    mock_radio._send_civ_raw.assert_awaited_once_with(b"\xfe\xfe\x98\xe0\x03\xfd")


@pytest.mark.asyncio
async def test_send_raw_backslash_escaped_hex(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """Backslash-escaped single-arg hex is parsed and sent as raw bytes."""
    response = CivFrame(to_addr=0xE0, from_addr=0x98, command=0x03, data=b"")
    mock_radio._send_civ_raw = AsyncMock(return_value=response)

    resp = await handler.execute(get_cmd("send_raw", "\\xFE\\xFE\\x98\\xE0\\x03\\xFD"))

    assert resp.ok
    mock_radio._send_civ_raw.assert_awaited_once_with(b"\xfe\xfe\x98\xe0\x03\xfd")


@pytest.mark.asyncio
async def test_send_raw_returns_hex_response(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """Response CivFrame bytes are returned as space-separated uppercase hex."""
    response = CivFrame(
        to_addr=0xE0, from_addr=0x98, command=0x03, data=b"\x00\x60\x00\x00\x00"
    )
    mock_radio._send_civ_raw = AsyncMock(return_value=response)

    resp = await handler.execute(
        get_cmd("send_raw", "FE", "FE", "98", "E0", "03", "FD")
    )

    assert resp.ok
    assert resp.values == ["FE FE E0 98 03 00 60 00 00 00 FD"]


@pytest.mark.asyncio
async def test_send_raw_icom_timeout_returns_empty(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """IcomTimeoutError produces empty ok response (not ETIMEOUT)."""
    mock_radio._send_civ_raw = AsyncMock(side_effect=IcomTimeoutError("timeout"))

    resp = await handler.execute(
        get_cmd("send_raw", "FE", "FE", "98", "E0", "03", "FD")
    )

    assert resp.ok
    assert resp.values == []


@pytest.mark.asyncio
async def test_send_raw_asyncio_timeout_returns_empty(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """asyncio.TimeoutError also produces empty ok response."""
    mock_radio._send_civ_raw = AsyncMock(side_effect=asyncio.TimeoutError())

    resp = await handler.execute(
        get_cmd("send_raw", "FE", "FE", "98", "E0", "03", "FD")
    )

    assert resp.ok
    assert resp.values == []


@pytest.mark.asyncio
async def test_send_raw_none_response_returns_empty(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """None response (fire-and-forget or no response) returns empty ok."""
    mock_radio._send_civ_raw = AsyncMock(return_value=None)

    resp = await handler.execute(
        get_cmd("send_raw", "FE", "FE", "98", "E0", "17", "FD")
    )

    assert resp.ok
    assert resp.values == []


@pytest.mark.asyncio
async def test_send_raw_no_args_returns_einval(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """Missing args returns EINVAL."""
    resp = await handler.execute(get_cmd("send_raw"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_send_raw_invalid_hex_returns_einval(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """Malformed hex token returns EINVAL."""
    resp = await handler.execute(get_cmd("send_raw", "GG"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_send_raw_no_send_civ_raw_returns_enimpl(config: RigctldConfig) -> None:
    """Radio without _send_civ_raw attribute returns ENIMPL."""

    class _NoRawRadio:
        pass

    handler = RigctldHandler(_NoRawRadio(), config)  # type: ignore[arg-type]
    resp = await handler.execute(
        get_cmd("send_raw", "FE", "FE", "98", "E0", "03", "FD")
    )
    assert resp.error == HamlibError.ENIMPL


# ---------------------------------------------------------------------------
# Yaesu-specific level/func routing
# ---------------------------------------------------------------------------

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio  # noqa: E402


class _FakeYaesuRadio(YaesuCatRadio):
    """A YaesuCatRadio subclass that bypasses __init__ for testing."""

    def __init__(self) -> None:
        # Skip real __init__ — we only need isinstance() to pass
        pass


@pytest.fixture
def yaesu_radio() -> AsyncMock:
    """AsyncMock of a Yaesu CAT radio with backend_id discriminator.

    The mock is also wired to forward ``rigctld_routing`` to a real
    :class:`YaesuRouting` so the handler's routing dispatch works
    end-to-end (``AsyncMock(spec=…)`` would otherwise return a bare
    MagicMock with non-awaitable ``get_level``/``get_func`` methods).
    """
    from rigplane.rigctld.routing import YaesuRouting

    mock = AsyncMock(spec=_FakeYaesuRadio)
    mock.backend_id = "yaesu_cat"
    mock.rigctld_routing = lambda cache, max_power_w=100.0: YaesuRouting(
        mock, cache, max_power_w
    )
    return mock


@pytest.fixture
def yaesu_handler(yaesu_radio: AsyncMock, config: RigctldConfig) -> RigctldHandler:
    return RigctldHandler(yaesu_radio, config)


# -- Yaesu get_level ----------------------------------------------------------


@pytest.mark.asyncio
async def test_yaesu_get_level_strength(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_s_meter.return_value = 128
    resp = await yaesu_handler.execute(get_cmd("get_level", "STRENGTH"))
    assert resp.ok
    db = int(resp.values[0])
    assert -60 <= db <= 70
    yaesu_radio.get_s_meter.assert_awaited_once()


@pytest.mark.asyncio
async def test_yaesu_get_level_rawstr(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_s_meter.return_value = 200
    resp = await yaesu_handler.execute(get_cmd("get_level", "RAWSTR"))
    assert resp.ok
    assert resp.values == ["200"]


@pytest.mark.asyncio
async def test_yaesu_get_level_rfpower(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_rf_power.return_value = 50  # 50 watts
    resp = await yaesu_handler.execute(get_cmd("get_level", "RFPOWER"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(0.5, abs=0.01)


@pytest.mark.asyncio
async def test_yaesu_get_level_swr(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_swr.return_value = 2.5
    resp = await yaesu_handler.execute(get_cmd("get_level", "SWR"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_yaesu_get_level_af(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_af_level.return_value = 128
    resp = await yaesu_handler.execute(get_cmd("get_level", "AF"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(128 / 255.0, abs=0.001)


@pytest.mark.asyncio
async def test_yaesu_get_level_micgain(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_mic_gain.return_value = 75  # 0-100 scale
    resp = await yaesu_handler.execute(get_cmd("get_level", "MICGAIN"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(0.75, abs=0.01)


@pytest.mark.asyncio
async def test_yaesu_get_level_nb(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_nb_level.return_value = 5  # 0-10 scale
    resp = await yaesu_handler.execute(get_cmd("get_level", "NB"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(0.5, abs=0.01)


@pytest.mark.asyncio
async def test_yaesu_get_level_nr(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_nr_level.return_value = 8  # 0-15 scale
    resp = await yaesu_handler.execute(get_cmd("get_level", "NR"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(8 / 15.0, abs=0.001)


@pytest.mark.asyncio
async def test_yaesu_get_level_cwpitch(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    """Routing passes Hz through; backend converts idx → Hz internally. (#1162)"""
    yaesu_radio.get_cw_pitch.return_value = 700  # backend returns Hz directly
    resp = await yaesu_handler.execute(get_cmd("get_level", "CWPITCH"))
    assert resp.ok
    assert resp.values == ["700"]


@pytest.mark.asyncio
async def test_yaesu_get_level_keyspd(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_key_speed.return_value = 25
    resp = await yaesu_handler.execute(get_cmd("get_level", "KEYSPD"))
    assert resp.ok
    assert resp.values == ["25"]


@pytest.mark.asyncio
async def test_yaesu_get_level_notchf(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_manual_notch.return_value = (True, 150)
    resp = await yaesu_handler.execute(get_cmd("get_level", "NOTCHF"))
    assert resp.ok
    assert resp.values == ["150"]


@pytest.mark.asyncio
async def test_yaesu_get_level_ifshift(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_if_shift.return_value = -200
    resp = await yaesu_handler.execute(get_cmd("get_level", "IFSHIFT"))
    assert resp.ok
    assert resp.values == ["-200"]


@pytest.mark.asyncio
async def test_yaesu_get_level_monitor_gain(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_monitor_level.return_value = 50  # 0-100
    resp = await yaesu_handler.execute(get_cmd("get_level", "MONITOR_GAIN"))
    assert resp.ok
    assert float(resp.values[0]) == pytest.approx(0.5, abs=0.01)


@pytest.mark.asyncio
async def test_yaesu_get_level_unknown_returns_einval(
    yaesu_handler: RigctldHandler,
) -> None:
    resp = await yaesu_handler.execute(get_cmd("get_level", "PBT_IN"))
    assert resp.error == HamlibError.EINVAL


# -- Yaesu set_level ----------------------------------------------------------


@pytest.mark.asyncio
async def test_yaesu_set_level_rfpower(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_level", "RFPOWER", "0.5"))
    assert resp.ok
    yaesu_radio.set_power.assert_awaited_once_with(50)  # 0.5 * 100W


@pytest.mark.asyncio
async def test_yaesu_set_level_af(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_level", "AF", "0.5"))
    assert resp.ok
    yaesu_radio.set_af_level.assert_awaited_once_with(128)  # 0.5 * 255 rounded


@pytest.mark.asyncio
async def test_yaesu_set_level_micgain(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_level", "MICGAIN", "0.75"))
    assert resp.ok
    yaesu_radio.set_mic_gain.assert_awaited_once_with(75)  # 0.75 * 100


@pytest.mark.asyncio
async def test_yaesu_set_level_nb(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_level", "NB", "0.5"))
    assert resp.ok
    yaesu_radio.set_nb_level.assert_awaited_once_with(5)  # 0.5 * 10


@pytest.mark.asyncio
async def test_yaesu_set_level_nr(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_level", "NR", "0.5"))
    assert resp.ok
    yaesu_radio.set_nr_level.assert_awaited_once_with(8)  # round(0.5 * 15)


@pytest.mark.asyncio
async def test_yaesu_set_level_cwpitch(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    """Routing passes Hz through; backend converts Hz → idx internally. (#1162)"""
    resp = await yaesu_handler.execute(set_cmd("set_level", "CWPITCH", "700"))
    assert resp.ok
    yaesu_radio.set_cw_pitch.assert_awaited_once_with(700)  # Hz pass-through


@pytest.mark.asyncio
async def test_yaesu_set_level_keyspd(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_level", "KEYSPD", "30"))
    assert resp.ok
    yaesu_radio.set_key_speed.assert_awaited_once_with(30)


@pytest.mark.asyncio
async def test_yaesu_set_level_notchf(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_level", "NOTCHF", "150"))
    assert resp.ok
    # NOTCHF now routes through the cross-vendor set_notch_filter alias
    # (closes P0-03 from hotfix epic #1091).
    yaesu_radio.set_notch_filter.assert_awaited_once_with(150)


@pytest.mark.asyncio
async def test_yaesu_set_level_ifshift(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_level", "IFSHIFT", "-200"))
    assert resp.ok
    yaesu_radio.set_if_shift.assert_awaited_once_with(-200)


@pytest.mark.asyncio
async def test_yaesu_set_level_unknown_returns_einval(
    yaesu_handler: RigctldHandler,
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_level", "PBT_IN", "0.5"))
    assert resp.error == HamlibError.EINVAL


# -- Yaesu get_func -----------------------------------------------------------


@pytest.mark.asyncio
async def test_yaesu_get_func_vox(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_vox.return_value = True
    resp = await yaesu_handler.execute(get_cmd("get_func", "VOX"))
    assert resp.ok
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_yaesu_get_func_tuner(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_tuner_status.return_value = 1  # ON
    resp = await yaesu_handler.execute(get_cmd("get_func", "TUNER"))
    assert resp.ok
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_yaesu_get_func_tuner_off(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_tuner_status.return_value = 0  # OFF
    resp = await yaesu_handler.execute(get_cmd("get_func", "TUNER"))
    assert resp.ok
    assert resp.values == ["0"]


@pytest.mark.asyncio
async def test_yaesu_get_func_comp(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_processor.return_value = True
    resp = await yaesu_handler.execute(get_cmd("get_func", "COMP"))
    assert resp.ok
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_yaesu_get_func_nb(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_nb_level.return_value = 5  # > 0 means ON
    resp = await yaesu_handler.execute(get_cmd("get_func", "NB"))
    assert resp.ok
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_yaesu_get_func_nb_off(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_nb_level.return_value = 0
    resp = await yaesu_handler.execute(get_cmd("get_func", "NB"))
    assert resp.ok
    assert resp.values == ["0"]


@pytest.mark.asyncio
async def test_yaesu_get_func_nr(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_nr_level.return_value = 3
    resp = await yaesu_handler.execute(get_cmd("get_func", "NR"))
    assert resp.ok
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_yaesu_get_func_lock(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_dial_lock.return_value = True
    resp = await yaesu_handler.execute(get_cmd("get_func", "LOCK"))
    assert resp.ok
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_yaesu_get_func_split(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_split.return_value = True
    resp = await yaesu_handler.execute(get_cmd("get_func", "SPLIT"))
    assert resp.ok
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_yaesu_get_func_agc(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.get_agc.return_value = 3  # SLOW, > 0 means active
    resp = await yaesu_handler.execute(get_cmd("get_func", "AGC"))
    assert resp.ok
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_yaesu_get_func_mon(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(get_cmd("get_func", "MON"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_yaesu_get_func_unknown_returns_einval(
    yaesu_handler: RigctldHandler,
) -> None:
    resp = await yaesu_handler.execute(get_cmd("get_func", "APF"))
    assert resp.error == HamlibError.EINVAL


# -- Yaesu set_func -----------------------------------------------------------


@pytest.mark.asyncio
async def test_yaesu_set_func_vox(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_func", "VOX", "1"))
    assert resp.ok
    yaesu_radio.set_vox.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_yaesu_set_func_tuner(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_func", "TUNER", "1"))
    assert resp.ok
    yaesu_radio.set_tuner_status.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_rigctld_state_tune_icom(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    """get_func TUNER must call canonical get_tuner_status (not get_tuner).

    The canonical SystemControlCapable name is implemented on both Icom
    and Yaesu backends — using it here keeps the rigctld layer compatible
    with both without raising AttributeError on the Icom path.
    Refs #1094.
    """
    yaesu_radio.get_tuner_status.return_value = 2  # tuning in progress
    resp = await yaesu_handler.execute(get_cmd("get_func", "TUNER"))
    assert resp.ok
    assert resp.values == ["1"]
    yaesu_radio.get_tuner_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_rigctld_set_func_tune_icom(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    """set_func TUNER 1 must call canonical set_tuner_status (not set_tuner).

    The canonical SystemControlCapable name is implemented on both Icom
    and Yaesu backends — using it here keeps the rigctld layer compatible
    with both without raising AttributeError on the Icom path.
    Refs #1094.
    """
    resp = await yaesu_handler.execute(set_cmd("set_func", "TUNER", "1"))
    assert resp.ok
    yaesu_radio.set_tuner_status.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_yaesu_set_func_comp(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_func", "COMP", "1"))
    assert resp.ok
    yaesu_radio.set_processor.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_yaesu_set_func_nb(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_func", "NB", "1"))
    assert resp.ok
    yaesu_radio.set_nb.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_yaesu_set_func_nr(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_func", "NR", "0"))
    assert resp.ok
    yaesu_radio.set_nr.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_yaesu_set_func_lock(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_func", "LOCK", "1"))
    assert resp.ok
    yaesu_radio.set_dial_lock.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_yaesu_set_func_split(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_func", "SPLIT", "1"))
    assert resp.ok
    yaesu_radio.set_split.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_yaesu_set_func_agc_on(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_func", "AGC", "1"))
    assert resp.ok
    yaesu_radio.set_agc.assert_awaited_once_with(1)  # ON → FAST


@pytest.mark.asyncio
async def test_yaesu_set_func_agc_off(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_func", "AGC", "0"))
    assert resp.ok
    yaesu_radio.set_agc.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_yaesu_set_func_mon(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_func", "MON", "1"))
    assert resp.error == HamlibError.EINVAL


@pytest.mark.asyncio
async def test_yaesu_set_func_unknown_returns_einval(
    yaesu_handler: RigctldHandler,
) -> None:
    resp = await yaesu_handler.execute(set_cmd("set_func", "APF", "1"))
    assert resp.error == HamlibError.EINVAL


# -- Yaesu dump_state / get_info ----------------------------------------------


@pytest.mark.asyncio
async def test_yaesu_dump_state(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    # Rig model now comes from the radio's TOML config via
    # `hamlib_model_id` (closes #441) — mock it explicitly.
    yaesu_radio.hamlib_model_id = 2028
    resp = await yaesu_handler.execute(get_cmd("dump_state"))
    assert resp.ok
    assert resp.values[1] == "2028"  # Yaesu rig model (from TOML config)


async def test_yaesu_dump_state_honors_toml_model_id(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    """Non-default rig model from TOML appears in dump_state (closes #441)."""
    yaesu_radio.hamlib_model_id = 1042  # RIG_MODEL_FTDX101D (example)
    resp = await yaesu_handler.execute(get_cmd("dump_state"))
    assert resp.ok
    assert resp.values[1] == "1042"


@pytest.mark.asyncio
async def test_yaesu_get_info(
    yaesu_handler: RigctldHandler, yaesu_radio: AsyncMock
) -> None:
    yaesu_radio.model = "FTDX10"
    resp = await yaesu_handler.execute(get_cmd("get_info"))
    assert resp.ok
    assert "Yaesu" in resp.values[0]
    assert "FTDX10" in resp.values[0]


# -- Verify Icom routing is NOT broken ----------------------------------------


@pytest.mark.asyncio
async def test_icom_get_level_still_works(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """Ensure adding Yaesu branches didn't break Icom routing."""
    mock_radio.get_s_meter.return_value = 120
    resp = await handler.execute(get_cmd("get_level", "STRENGTH"))
    assert resp.ok


@pytest.mark.asyncio
async def test_icom_get_func_still_works(
    handler: RigctldHandler, mock_radio: AsyncMock
) -> None:
    """Ensure adding Yaesu branches didn't break Icom func routing."""
    mock_radio.get_vox.return_value = True
    resp = await handler.execute(get_cmd("get_func", "VOX"))
    assert resp.ok
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_icom_dump_state_unchanged(
    handler: RigctldHandler,
) -> None:
    resp = await handler.execute(get_cmd("dump_state"))
    assert resp.ok
    assert resp.values[1] == "3078"  # IC-7610 model


# ---------------------------------------------------------------------------
# Profile-aware VFO protocol (issue #722)
# ---------------------------------------------------------------------------


class _FakeProfile:
    """Minimal stand-in for ``rigplane.profiles.RadioProfile`` for tests."""

    def __init__(self, *, receiver_count: int, vfo_scheme: str) -> None:
        self.receiver_count = receiver_count
        self.vfo_scheme = vfo_scheme


@pytest.fixture
def dual_rx_radio() -> AsyncMock:
    """IC-7610-style radio mock: dual-RX profile, main/sub VFO scheme."""
    radio = AsyncMock()
    radio.capabilities = set(FULL_ICOM_CAPS)
    radio.profile = _FakeProfile(receiver_count=2, vfo_scheme="main_sub")
    return radio


@pytest.fixture
def dual_rx_handler(dual_rx_radio: AsyncMock, config: RigctldConfig) -> RigctldHandler:
    return RigctldHandler(dual_rx_radio, config)


@pytest.fixture
def single_rx_radio() -> AsyncMock:
    """IC-7300-style radio mock: single-RX profile, A/B VFO scheme."""
    radio = AsyncMock()
    radio.capabilities = set(FULL_ICOM_CAPS)
    radio.profile = _FakeProfile(receiver_count=1, vfo_scheme="ab")
    return radio


@pytest.fixture
def single_rx_handler(
    single_rx_radio: AsyncMock, config: RigctldConfig
) -> RigctldHandler:
    return RigctldHandler(single_rx_radio, config)


# -- chk_vfo ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chk_vfo_returns_1_for_dual_rx(
    dual_rx_handler: RigctldHandler,
) -> None:
    """``chk_vfo`` returns ``"1"`` for dual-RX profiles (issue #1346).

    Re-enabled in Variant A 5/5 after the full ``vfo_opt`` stack landed:
    parser support (#1343), per-VFO routing for freq/mode/PTT (#1344),
    per-VFO split/RIT/level/func (#1345). Variant B's unconditional
    ``"0"`` (PR #1340) is now correctly superseded for dual-RX models.
    """
    resp = await dual_rx_handler.execute(get_cmd("chk_vfo"))
    assert resp.ok
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_chk_vfo_returns_0_for_single_rx(
    single_rx_handler: RigctldHandler,
) -> None:
    """``chk_vfo`` stays ``"0"`` on single-RX profiles (issue #1346).

    Single-receiver radios (IC-7300, IC-705, …) do not need ``vfo_opt``
    advertising — their A/B VFO scheme is handled by the bare-form
    Hamlib path. Avoids needlessly forcing VFO tokenisation on clients
    that were happy without it.
    """
    resp = await single_rx_handler.execute(get_cmd("chk_vfo"))
    assert resp.ok
    assert resp.values == ["0"]


# -- get_vfo reflects radio state ---------------------------------------------


@pytest.mark.asyncio
async def test_get_vfo_dual_rx_main_is_vfoa(
    dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
) -> None:
    state = RadioState()
    state.active = "MAIN"
    dual_rx_radio.radio_state = state
    resp = await dual_rx_handler.execute(get_cmd("get_vfo"))
    assert resp.values == ["VFOA"]


@pytest.mark.asyncio
async def test_get_vfo_dual_rx_sub_is_vfob(
    dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
) -> None:
    state = RadioState()
    state.active = "SUB"
    dual_rx_radio.radio_state = state
    resp = await dual_rx_handler.execute(get_cmd("get_vfo"))
    assert resp.values == ["VFOB"]


@pytest.mark.asyncio
async def test_get_vfo_single_rx_reflects_slot_b(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    state = RadioState()
    state.main.active_slot = "B"
    single_rx_radio.radio_state = state
    resp = await single_rx_handler.execute(get_cmd("get_vfo"))
    assert resp.values == ["VFOB"]


# -- set_vfo sends correct CI-V selection -------------------------------------


@pytest.mark.asyncio
async def test_set_vfo_dual_rx_vfob_selects_sub(
    dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
) -> None:
    resp = await dual_rx_handler.execute(set_cmd("set_vfo", "VFOB"))
    assert resp.ok
    # Issue #1172: dual-RX VFOB → ``select_receiver("SUB")`` (CI-V
    # 0x07 0xD1).  No fallback through legacy ``set_vfo`` overload.
    dual_rx_radio.select_receiver.assert_awaited_once_with("SUB")
    dual_rx_radio.set_vfo.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_vfo_dual_rx_vfoa_selects_main(
    dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
) -> None:
    resp = await dual_rx_handler.execute(set_cmd("set_vfo", "VFOA"))
    assert resp.ok
    dual_rx_radio.select_receiver.assert_awaited_once_with("MAIN")
    dual_rx_radio.set_vfo.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_vfo_single_rx_vfob_selects_slot_b(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    resp = await single_rx_handler.execute(set_cmd("set_vfo", "VFOB"))
    assert resp.ok
    # Issue #1172: single-RX VFOB → ``set_vfo_slot("B")`` (CI-V
    # 0x07 0x01).  No fallback through legacy ``set_vfo`` overload.
    single_rx_radio.set_vfo_slot.assert_awaited_once_with("B")
    single_rx_radio.set_vfo.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_vfo_single_rx_vfoa_selects_slot_a(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    resp = await single_rx_handler.execute(set_cmd("set_vfo", "VFOA"))
    assert resp.ok
    single_rx_radio.set_vfo_slot.assert_awaited_once_with("A")
    single_rx_radio.set_vfo.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_vfo_unknown_name_is_backward_compat_ok(
    dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
) -> None:
    # Unknown VFO names from legacy clients should be silently accepted,
    # never sent to the radio.
    resp = await dual_rx_handler.execute(set_cmd("set_vfo", "VFO-C"))
    assert resp.ok
    dual_rx_radio.set_vfo.assert_not_awaited()
    dual_rx_radio.select_receiver.assert_not_awaited()
    dual_rx_radio.set_vfo_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_vfo_no_args_returns_einval(
    dual_rx_handler: RigctldHandler,
) -> None:
    resp = await dual_rx_handler.execute(set_cmd("set_vfo"))
    assert resp.error == HamlibError.EINVAL


# -- legacy backend fallback (issue #1189) ------------------------------------


@pytest.fixture
def legacy_dual_rx_radio() -> AsyncMock:
    """Legacy dual-RX backend lacking ReceiverBankCapable / VfoSlotCapable.

    Issue #1189: backends predating #1170/#1172 (e.g. ``SerialMockRadio``,
    3rd-party ``Radio`` implementers) only expose the legacy ``set_vfo``
    overload.  ``_cmd_set_vfo`` must fall back to it instead of returning
    a silent ``RPRT 0``.
    """
    radio = AsyncMock(spec_set=["capabilities", "profile", "set_vfo"])
    radio.capabilities = set(FULL_ICOM_CAPS)
    radio.profile = _FakeProfile(receiver_count=2, vfo_scheme="main_sub")
    radio.set_vfo = AsyncMock()
    return radio


@pytest.fixture
def legacy_single_rx_radio() -> AsyncMock:
    """Legacy single-RX backend lacking VfoSlotCapable."""
    radio = AsyncMock(spec_set=["capabilities", "profile", "set_vfo"])
    radio.capabilities = set(FULL_ICOM_CAPS)
    radio.profile = _FakeProfile(receiver_count=1, vfo_scheme="ab")
    radio.set_vfo = AsyncMock()
    return radio


@pytest.fixture
def legacy_no_vfo_radio() -> AsyncMock:
    """Backend with no VFO support at all — neither new nor legacy methods."""
    radio = AsyncMock(spec_set=["capabilities", "profile"])
    radio.capabilities = set(FULL_ICOM_CAPS)
    radio.profile = _FakeProfile(receiver_count=2, vfo_scheme="main_sub")
    return radio


@pytest.mark.asyncio
async def test_set_vfo_legacy_dual_rx_falls_back_to_set_vfo(
    legacy_dual_rx_radio: AsyncMock, config: RigctldConfig
) -> None:
    handler = RigctldHandler(legacy_dual_rx_radio, config)
    resp = await handler.execute(set_cmd("set_vfo", "VFOB"))
    assert resp.ok
    # Legacy overload receives MAIN/SUB on dual-RX (matches pre-#1187 mapping).
    legacy_dual_rx_radio.set_vfo.assert_awaited_once_with("SUB")


@pytest.mark.asyncio
async def test_set_vfo_legacy_dual_rx_vfoa_falls_back_to_set_vfo(
    legacy_dual_rx_radio: AsyncMock, config: RigctldConfig
) -> None:
    handler = RigctldHandler(legacy_dual_rx_radio, config)
    resp = await handler.execute(set_cmd("set_vfo", "VFOA"))
    assert resp.ok
    legacy_dual_rx_radio.set_vfo.assert_awaited_once_with("MAIN")


@pytest.mark.asyncio
async def test_set_vfo_legacy_single_rx_falls_back_to_set_vfo(
    legacy_single_rx_radio: AsyncMock, config: RigctldConfig
) -> None:
    handler = RigctldHandler(legacy_single_rx_radio, config)
    resp = await handler.execute(set_cmd("set_vfo", "VFOB"))
    assert resp.ok
    # Legacy overload receives A/B on single-RX.
    legacy_single_rx_radio.set_vfo.assert_awaited_once_with("B")


@pytest.mark.asyncio
async def test_set_vfo_no_capability_returns_enavail(
    legacy_no_vfo_radio: AsyncMock, config: RigctldConfig
) -> None:
    # Backend without select_receiver / set_vfo_slot / set_vfo:
    # rigctld must surface ENAVAIL rather than silent RPRT 0 success.
    handler = RigctldHandler(legacy_no_vfo_radio, config)
    resp = await handler.execute(set_cmd("set_vfo", "VFOA"))
    assert resp.error == HamlibError.ENAVAIL


# -- set_split_vfo ------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_split_vfo_dual_rx_enables_and_routes_to_sub(
    dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
) -> None:
    resp = await dual_rx_handler.execute(set_cmd("set_split_vfo", "1", "VFOB"))
    assert resp.ok
    dual_rx_radio.set_split.assert_awaited_once_with(True)
    dual_rx_radio.set_vfo.assert_awaited_once_with("SUB")


@pytest.mark.asyncio
async def test_set_split_vfo_dual_rx_disables_does_not_switch_receiver(
    dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
) -> None:
    resp = await dual_rx_handler.execute(set_cmd("set_split_vfo", "0", "VFOA"))
    assert resp.ok
    dual_rx_radio.set_split.assert_awaited_once_with(False)
    dual_rx_radio.set_vfo.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_split_vfo_rolls_back_split_on_set_vfo_connection_error(
    dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
) -> None:
    """If set_vfo fails with ConnectionError after set_split(True),
    the handler must roll back by calling set_split(False) and
    return EIO — never leaving the radio in split-on / TX-not-routed."""
    dual_rx_radio.set_vfo.side_effect = IcomConnectionError("lost")

    resp = await dual_rx_handler.execute(set_cmd("set_split_vfo", "1", "VFOB"))

    assert resp.error == HamlibError.EIO
    # Two calls: first to enable split, then rollback to disable.
    assert dual_rx_radio.set_split.await_args_list == [
        ((True,), {}),
        ((False,), {}),
    ]
    dual_rx_radio.set_vfo.assert_awaited_once_with("SUB")


@pytest.mark.asyncio
async def test_set_split_vfo_rolls_back_split_on_set_vfo_timeout(
    dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
) -> None:
    """TimeoutError on the follow-up set_vfo → rollback + ETIMEOUT."""
    dual_rx_radio.set_vfo.side_effect = IcomTimeoutError("timeout")

    resp = await dual_rx_handler.execute(set_cmd("set_split_vfo", "1", "VFOB"))

    assert resp.error == HamlibError.ETIMEOUT
    assert dual_rx_radio.set_split.await_args_list == [
        ((True,), {}),
        ((False,), {}),
    ]


@pytest.mark.asyncio
async def test_set_split_vfo_rolls_back_split_on_unexpected_error(
    dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
) -> None:
    """Unexpected exception → rollback + EINTERNAL."""
    dual_rx_radio.set_vfo.side_effect = RuntimeError("boom")

    resp = await dual_rx_handler.execute(set_cmd("set_split_vfo", "1", "VFOB"))

    assert resp.error == HamlibError.EINTERNAL
    assert dual_rx_radio.set_split.await_args_list == [
        ((True,), {}),
        ((False,), {}),
    ]


@pytest.mark.asyncio
async def test_set_split_vfo_rollback_swallows_rollback_failure(
    dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
) -> None:
    """If the rollback set_split(False) itself fails, the handler
    must still return the ORIGINAL failure code — not raise."""
    dual_rx_radio.set_vfo.side_effect = IcomConnectionError("lost")
    # set_split succeeds on True, fails on False (the rollback).
    dual_rx_radio.set_split.side_effect = [
        None,
        IcomConnectionError("rollback failed too"),
    ]

    resp = await dual_rx_handler.execute(set_cmd("set_split_vfo", "1", "VFOB"))

    assert resp.error == HamlibError.EIO
    # Both calls were attempted — the rollback failure is swallowed
    # (logged only); the original error code wins.
    assert dual_rx_radio.set_split.await_count == 2


@pytest.mark.asyncio
async def test_get_split_vfo_dual_rx_returns_tracked_tx_vfo(
    dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
) -> None:
    """``s`` returns the TX_VFO label set by the most recent ``S`` command.

    Issue #1345 (research finding #2 in #1319): the prior implementation
    returned ``self._active_vfo_name()`` here, but the Hamlib protocol
    expects the TX_VFO that the client itself selected via ``S`` — not
    the active receiver. After #1345 the handler tracks the requested
    TX VFO in ``_split_tx_vfo`` (handler-local Hamlib-protocol state).
    """
    # Drive the handler through ``S 1 VFOB`` so the TX VFO is recorded.
    set_resp = await dual_rx_handler.execute(set_cmd("set_split_vfo", "1", "VFOB"))
    assert set_resp.ok
    state = RadioState()
    state.split = True
    state.active = "SUB"
    dual_rx_radio.radio_state = state
    resp = await dual_rx_handler.execute(get_cmd("get_split_vfo"))
    assert resp.ok
    assert resp.values == ["1", "VFOB"]


# ---------------------------------------------------------------------------
# Yaesu routing — ATT level (issue #1105 regression guard)
# ---------------------------------------------------------------------------


def _yaesu_handler(get_attenuator_value: bool) -> RigctldHandler:
    """Build a handler with a Yaesu-tagged radio so create_routing returns YaesuRouting."""
    from rigplane.rigctld.routing import YaesuRouting

    radio = AsyncMock(spec=_FakeYaesuRadio)
    radio.backend_id = "yaesu_cat"
    radio.capabilities = set()
    radio.get_attenuator = AsyncMock(return_value=get_attenuator_value)
    radio.rigctld_routing = lambda cache, max_power_w=100.0: YaesuRouting(
        radio, cache, max_power_w
    )
    return RigctldHandler(radio, RigctldConfig())


@pytest.mark.asyncio
async def test_yaesu_routing_get_level_att_on_returns_one() -> None:
    """get_level ATT returns "1" for True — bool must be cast to int (#1105)."""
    handler = _yaesu_handler(get_attenuator_value=True)
    resp = await handler.execute(get_cmd("get_level", "ATT"))
    assert resp.ok
    assert resp.values == ["1"]


@pytest.mark.asyncio
async def test_yaesu_routing_get_level_att_off_returns_zero() -> None:
    """get_level ATT returns "0" for False — bool must be cast to int (#1105)."""
    handler = _yaesu_handler(get_attenuator_value=False)
    resp = await handler.execute(get_cmd("get_level", "ATT"))
    assert resp.ok
    assert resp.values == ["0"]


# ---------------------------------------------------------------------------
# Per-VFO routing for f/F, m/M, t/T (issue #1344, Variant A 3/5)
# ---------------------------------------------------------------------------


def _vfo_get_cmd(long_cmd: str, vfo_arg: str | None, *args: str) -> RigctldCommand:
    """``RigctldCommand`` with an explicit ``vfo_arg`` (chk_vfo=1 path)."""
    return RigctldCommand(
        short_cmd="",
        long_cmd=long_cmd,
        args=tuple(args),
        is_set=False,
        vfo_arg=vfo_arg,
    )


def _vfo_set_cmd(long_cmd: str, vfo_arg: str | None, *args: str) -> RigctldCommand:
    return RigctldCommand(
        short_cmd="",
        long_cmd=long_cmd,
        args=tuple(args),
        is_set=True,
        vfo_arg=vfo_arg,
    )


def _dual_rx_state(
    *,
    main_freq: int = 14_250_000,
    sub_freq: int = 7_100_000,
    main_mode: str = "USB",
    sub_mode: str = "CW",
    main_filter: int | None = 1,
    sub_filter: int | None = 2,
) -> RadioState:
    """Build a populated RadioState with distinct MAIN / SUB values."""
    state = RadioState()
    state.main.freq = main_freq
    state.main.mode = main_mode
    state.main.filter = main_filter
    state.sub.freq = sub_freq
    state.sub.mode = sub_mode
    state.sub.filter = sub_filter
    return state


class TestPerVfoRoutingFreq:
    """`f`/`F` per-VFO routing under chk_vfo=1 (issue #1344)."""

    @pytest.mark.asyncio
    async def test_dual_rx_get_freq_vfoa_returns_main(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        dual_rx_radio.radio_state = _dual_rx_state()
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_freq", "VFOA"))
        assert resp.ok
        assert resp.values == ["14250000"]

    @pytest.mark.asyncio
    async def test_dual_rx_get_freq_vfob_returns_sub(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        dual_rx_radio.radio_state = _dual_rx_state()
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_freq", "VFOB"))
        assert resp.ok
        assert resp.values == ["7100000"]

    @pytest.mark.asyncio
    async def test_dual_rx_get_freq_currvfo_follows_active(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        state = _dual_rx_state()
        state.active = "SUB"
        dual_rx_radio.radio_state = state
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_freq", "currVFO"))
        assert resp.ok
        assert resp.values == ["7100000"]

    @pytest.mark.asyncio
    async def test_dual_rx_get_freq_no_arg_uses_legacy_main_path(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        # Without a VFO arg the active VFO is MAIN by default (state.active),
        # so behaviour matches the pre-#1344 single-VFO path: MAIN freq.
        dual_rx_radio.radio_state = _dual_rx_state()
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_freq", None))
        assert resp.ok
        assert resp.values == ["14250000"]

    @pytest.mark.asyncio
    async def test_single_rx_get_freq_vfob_returns_evfo(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        # A single-receiver profile cannot satisfy a VFOB request — Hamlib's
        # chk_vfo=1 path expects EVFO so the client falls back gracefully.
        single_rx_radio.radio_state = RadioState()
        resp = await single_rx_handler.execute(_vfo_get_cmd("get_freq", "VFOB"))
        assert resp.error == HamlibError.EVFO

    @pytest.mark.asyncio
    async def test_dual_rx_set_freq_vfoa_routes_to_main(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        resp = await dual_rx_handler.execute(
            _vfo_set_cmd("set_freq", "VFOA", "14080000")
        )
        assert resp.ok
        dual_rx_radio.set_freq.assert_awaited_once_with(14_080_000, receiver=0)

    @pytest.mark.asyncio
    async def test_dual_rx_set_freq_vfob_routes_to_sub(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        resp = await dual_rx_handler.execute(
            _vfo_set_cmd("set_freq", "VFOB", "7080000")
        )
        assert resp.ok
        dual_rx_radio.set_freq.assert_awaited_once_with(7_080_000, receiver=1)

    @pytest.mark.asyncio
    async def test_single_rx_set_freq_vfob_returns_evfo(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        resp = await single_rx_handler.execute(
            _vfo_set_cmd("set_freq", "VFOB", "7080000")
        )
        assert resp.error == HamlibError.EVFO
        single_rx_radio.set_freq.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dual_rx_get_freq_unknown_vfo_arg_returns_evfo(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        dual_rx_radio.radio_state = _dual_rx_state()
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_freq", "VFOC"))
        assert resp.error == HamlibError.EVFO


class TestPerVfoRoutingMode:
    """`m`/`M` per-VFO routing under chk_vfo=1 (issue #1344)."""

    @pytest.mark.asyncio
    async def test_dual_rx_get_mode_vfoa_returns_main(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        dual_rx_radio.radio_state = _dual_rx_state()
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_mode", "VFOA"))
        assert resp.ok
        # MAIN: USB, filter 1 → 3000 Hz passband.
        assert resp.values == ["USB", "3000"]

    @pytest.mark.asyncio
    async def test_dual_rx_get_mode_vfob_returns_sub(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        dual_rx_radio.radio_state = _dual_rx_state()
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_mode", "VFOB"))
        assert resp.ok
        # SUB: CW, filter 2 → 2400 Hz passband.
        assert resp.values == ["CW", "2400"]

    @pytest.mark.asyncio
    async def test_single_rx_get_mode_vfob_returns_evfo(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        single_rx_radio.radio_state = RadioState()
        resp = await single_rx_handler.execute(_vfo_get_cmd("get_mode", "VFOB"))
        assert resp.error == HamlibError.EVFO

    @pytest.mark.asyncio
    async def test_dual_rx_set_mode_vfoa_routes_to_main(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        resp = await dual_rx_handler.execute(
            _vfo_set_cmd("set_mode", "VFOA", "USB", "2400")
        )
        assert resp.ok
        # MAIN path: ``set_mode`` called WITHOUT receiver kwarg (legacy default).
        dual_rx_radio.set_mode.assert_awaited_once_with("USB", filter_width=2)

    @pytest.mark.asyncio
    async def test_dual_rx_set_mode_vfob_routes_to_sub(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        resp = await dual_rx_handler.execute(
            _vfo_set_cmd("set_mode", "VFOB", "CW", "1800")
        )
        assert resp.ok
        # SUB path uses receiver=1 explicitly; passband 1800 Hz → filter 3.
        dual_rx_radio.set_mode.assert_awaited_once_with(
            "CW", filter_width=3, receiver=1
        )

    @pytest.mark.asyncio
    async def test_single_rx_set_mode_vfob_returns_evfo(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        resp = await single_rx_handler.execute(
            _vfo_set_cmd("set_mode", "VFOB", "USB", "2400")
        )
        assert resp.error == HamlibError.EVFO
        single_rx_radio.set_mode.assert_not_awaited()


class TestPerVfoRoutingPtt:
    """`t`/`T` per-VFO routing under chk_vfo=1 (issue #1344).

    The Icom radio exposes a single global PTT state, so the answer for
    ``t VFOA`` and ``t VFOB`` is the same once the request is accepted.
    The VFO arg is validated only against the profile (single-RX rejects
    VFOB with EVFO; dual-RX accepts both).
    """

    @pytest.mark.asyncio
    async def test_dual_rx_get_ptt_vfoa(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        state = RadioState()
        state.ptt = True
        dual_rx_radio.radio_state = state
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_ptt", "VFOA"))
        assert resp.ok
        assert resp.values == ["1"]

    @pytest.mark.asyncio
    async def test_dual_rx_get_ptt_vfob_returns_global_ptt(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        # Radio PTT is global — VFOB query returns the same global state.
        state = RadioState()
        state.ptt = True
        dual_rx_radio.radio_state = state
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_ptt", "VFOB"))
        assert resp.ok
        assert resp.values == ["1"]

    @pytest.mark.asyncio
    async def test_single_rx_get_ptt_vfob_returns_evfo(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        single_rx_radio.radio_state = RadioState()
        resp = await single_rx_handler.execute(_vfo_get_cmd("get_ptt", "VFOB"))
        assert resp.error == HamlibError.EVFO

    @pytest.mark.asyncio
    async def test_dual_rx_set_ptt_vfob_keys_radio_pt_t(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        # ``T VFOB 1`` is honoured: Hamlib expects per-VFO PTT but Icom only
        # has one PTT path — pragmatic choice is to key it regardless.
        resp = await dual_rx_handler.execute(_vfo_set_cmd("set_ptt", "VFOB", "1"))
        assert resp.ok
        dual_rx_radio.set_ptt.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_single_rx_set_ptt_vfob_returns_evfo(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        resp = await single_rx_handler.execute(_vfo_set_cmd("set_ptt", "VFOB", "1"))
        assert resp.error == HamlibError.EVFO
        single_rx_radio.set_ptt.assert_not_awaited()


# ---------------------------------------------------------------------------
# Per-VFO routing for s/S, j, l/L, u/U (issue #1345, Variant A 4/5)
# ---------------------------------------------------------------------------


class TestPerVfoSplit:
    """`s`/`S` per-VFO routing under chk_vfo=1 (issue #1345).

    Split is a Hamlib-protocol concept — the handler tracks the requested
    TX_VFO label in handler-local state (``_split_tx_vfo``), set by the
    most recent ``S`` command and returned by ``s``. This fixes a standing
    bug (research finding #2 in #1319) where ``s`` previously returned
    the active receiver label rather than the TX_VFO.
    """

    @pytest.mark.asyncio
    async def test_get_split_vfo_default_tx_vfo_is_vfoa(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        # No prior S command — the default Hamlib TX_VFO label is VFOA.
        state = RadioState()
        state.split = False
        dual_rx_radio.radio_state = state
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_split_vfo", "VFOA"))
        assert resp.ok
        assert resp.values == ["0", "VFOA"]

    @pytest.mark.asyncio
    async def test_set_split_vfo_records_tx_vfo_for_get(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        # ``S VFOA 1 VFOB`` — split on, TX VFO = VFOB.  The follow-up
        # ``s`` request must return the recorded TX VFO label, not the
        # active receiver.
        set_resp = await dual_rx_handler.execute(
            _vfo_set_cmd("set_split_vfo", "VFOA", "1", "VFOB")
        )
        assert set_resp.ok
        dual_rx_radio.set_split.assert_awaited_once_with(True)

        state = RadioState()
        state.split = True
        state.active = "MAIN"  # active receiver is MAIN — TX_VFO is still VFOB
        dual_rx_radio.radio_state = state
        get_resp = await dual_rx_handler.execute(_vfo_get_cmd("get_split_vfo", "VFOA"))
        assert get_resp.ok
        assert get_resp.values == ["1", "VFOB"]

    @pytest.mark.asyncio
    async def test_get_split_vfo_unknown_vfo_arg_returns_evfo(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_split_vfo", "VFOC"))
        assert resp.error == HamlibError.EVFO

    @pytest.mark.asyncio
    async def test_get_split_vfo_single_rx_vfob_returns_evfo(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        single_rx_radio.radio_state = RadioState()
        resp = await single_rx_handler.execute(_vfo_get_cmd("get_split_vfo", "VFOB"))
        assert resp.error == HamlibError.EVFO

    @pytest.mark.asyncio
    async def test_set_split_vfo_single_rx_vfob_returns_evfo(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        resp = await single_rx_handler.execute(
            _vfo_set_cmd("set_split_vfo", "VFOB", "1", "VFOA")
        )
        assert resp.error == HamlibError.EVFO
        single_rx_radio.set_split.assert_not_awaited()


class TestPerVfoRit:
    """`j` (get_rit) per-VFO routing (issue #1345).

    Most Icom radios expose a single global RIT register (CI-V 0x21 0x00),
    so the answer is the same for VFOA / VFOB.  Hamlib clients tolerate
    this. The VFO arg is validated only for profile compatibility.
    """

    @pytest.mark.asyncio
    async def test_get_rit_vfoa_returns_global_rit(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        state = RadioState()
        state.rit_freq = 250
        dual_rx_radio.radio_state = state
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_rit", "VFOA"))
        assert resp.ok
        assert resp.values == ["250"]

    @pytest.mark.asyncio
    async def test_get_rit_vfob_returns_same_global_rit(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        # Per-VFO RIT is not modelled in RadioState — VFOB returns the
        # same global value as VFOA. Documented limitation.
        state = RadioState()
        state.rit_freq = 250
        dual_rx_radio.radio_state = state
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_rit", "VFOB"))
        assert resp.ok
        assert resp.values == ["250"]

    @pytest.mark.asyncio
    async def test_get_rit_single_rx_vfob_returns_evfo(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        single_rx_radio.radio_state = RadioState()
        resp = await single_rx_handler.execute(_vfo_get_cmd("get_rit", "VFOB"))
        assert resp.error == HamlibError.EVFO


class TestPerVfoLevel:
    """`l`/`L` per-VFO routing (issue #1345).

    Per-receiver levels (STRENGTH, NB, NR) route via ``receiver=`` kwarg.
    Radio-global levels (RFPOWER, MICGAIN, etc.) ignore the VFO arg but
    still validate it against the profile.
    """

    @pytest.mark.asyncio
    async def test_get_strength_vfoa_reads_main(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        state = RadioState()
        state.main.freq = 14_250_000
        state.main.s_meter = 120  # S9
        state.sub.s_meter = 60  # different value to detect mis-routing
        dual_rx_radio.radio_state = state
        resp = await dual_rx_handler.execute(
            _vfo_get_cmd("get_level", "VFOA", "STRENGTH")
        )
        assert resp.ok
        # 120 / 241 * 114 - 54 ≈ 2.78 → rounds to 3
        assert resp.values == [str(round((120 / 241.0) * 114.0 - 54.0))]

    @pytest.mark.asyncio
    async def test_get_strength_vfob_reads_sub(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        state = RadioState()
        state.main.freq = 14_250_000
        state.main.s_meter = 120
        state.sub.s_meter = 60
        dual_rx_radio.radio_state = state
        resp = await dual_rx_handler.execute(
            _vfo_get_cmd("get_level", "VFOB", "STRENGTH")
        )
        assert resp.ok
        assert resp.values == [str(round((60 / 241.0) * 114.0 - 54.0))]

    @pytest.mark.asyncio
    async def test_get_rfpower_ignores_vfo_arg(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        # RFPOWER is radio-global — VFOB request must succeed and return
        # the global value, not error out (defeating vfo_opt).
        state = RadioState()
        state.main.freq = 14_250_000
        state.power_level = 128
        dual_rx_radio.radio_state = state
        resp = await dual_rx_handler.execute(
            _vfo_get_cmd("get_level", "VFOB", "RFPOWER")
        )
        assert resp.ok
        assert resp.values == [f"{128 / 255.0:.6f}"]

    @pytest.mark.asyncio
    async def test_get_nb_level_vfob_routes_to_sub(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        dual_rx_radio.get_nb_level = AsyncMock(return_value=120)
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_level", "VFOB", "NB"))
        assert resp.ok
        dual_rx_radio.get_nb_level.assert_awaited_once_with(receiver=1)

    @pytest.mark.asyncio
    async def test_get_nb_level_vfoa_no_receiver_kwarg(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        dual_rx_radio.get_nb_level = AsyncMock(return_value=120)
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_level", "VFOA", "NB"))
        assert resp.ok
        # MAIN path: legacy call without receiver kwarg.
        dual_rx_radio.get_nb_level.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_get_level_unknown_vfo_arg_returns_evfo(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        resp = await dual_rx_handler.execute(
            _vfo_get_cmd("get_level", "VFOC", "STRENGTH")
        )
        assert resp.error == HamlibError.EVFO

    @pytest.mark.asyncio
    async def test_get_strength_single_rx_vfob_returns_evfo(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        single_rx_radio.radio_state = RadioState()
        resp = await single_rx_handler.execute(
            _vfo_get_cmd("get_level", "VFOB", "STRENGTH")
        )
        assert resp.error == HamlibError.EVFO

    @pytest.mark.asyncio
    async def test_set_rfpower_ignores_vfo_arg(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        resp = await dual_rx_handler.execute(
            _vfo_set_cmd("set_level", "VFOB", "RFPOWER", "0.5")
        )
        assert resp.ok
        # RFPOWER is global — call without receiver kwarg.
        dual_rx_radio.set_rf_power.assert_awaited_once_with(round(0.5 * 255))

    @pytest.mark.asyncio
    async def test_set_nb_level_vfob_routes_to_sub(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        resp = await dual_rx_handler.execute(
            _vfo_set_cmd("set_level", "VFOB", "NB", "0.5")
        )
        assert resp.ok
        dual_rx_radio.set_nb_level.assert_awaited_once_with(
            round(0.5 * 255), receiver=1
        )

    @pytest.mark.asyncio
    async def test_set_level_single_rx_vfob_returns_evfo(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        resp = await single_rx_handler.execute(
            _vfo_set_cmd("set_level", "VFOB", "RFPOWER", "0.5")
        )
        assert resp.error == HamlibError.EVFO
        single_rx_radio.set_rf_power.assert_not_awaited()


class TestPerVfoFunc:
    """`u`/`U` per-VFO routing (issue #1345).

    Per-receiver funcs (NB, NR) route via ``receiver=`` kwarg.  Radio-
    global funcs (VOX, TUNER, COMP, etc.) ignore the VFO arg.
    """

    @pytest.mark.asyncio
    async def test_get_nb_vfob_routes_to_sub(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        dual_rx_radio.get_nb = AsyncMock(return_value=True)
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_func", "VFOB", "NB"))
        assert resp.ok
        dual_rx_radio.get_nb.assert_awaited_once_with(receiver=1)

    @pytest.mark.asyncio
    async def test_get_nb_vfoa_no_receiver_kwarg(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        dual_rx_radio.get_nb = AsyncMock(return_value=True)
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_func", "VFOA", "NB"))
        assert resp.ok
        dual_rx_radio.get_nb.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_get_vox_ignores_vfo_arg(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        # VOX is radio-global.
        dual_rx_radio.get_vox = AsyncMock(return_value=True)
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_func", "VFOB", "VOX"))
        assert resp.ok
        dual_rx_radio.get_vox.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_set_nb_vfob_routes_to_sub(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        resp = await dual_rx_handler.execute(
            _vfo_set_cmd("set_func", "VFOB", "NB", "1")
        )
        assert resp.ok
        dual_rx_radio.set_nb.assert_awaited_once_with(True, receiver=1)

    @pytest.mark.asyncio
    async def test_set_vox_ignores_vfo_arg(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        resp = await dual_rx_handler.execute(
            _vfo_set_cmd("set_func", "VFOB", "VOX", "1")
        )
        assert resp.ok
        # VOX is global — no receiver kwarg.
        dual_rx_radio.set_vox.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_get_func_unknown_vfo_arg_returns_evfo(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_func", "VFOC", "NB"))
        assert resp.error == HamlibError.EVFO

    @pytest.mark.asyncio
    async def test_set_func_single_rx_vfob_returns_evfo(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        resp = await single_rx_handler.execute(
            _vfo_set_cmd("set_func", "VFOB", "NB", "1")
        )
        assert resp.error == HamlibError.EVFO
        single_rx_radio.set_nb.assert_not_awaited()


# ---------------------------------------------------------------------------
# Issue #1354 — single-RX active_slot="B" must not route to receiver=1
# ---------------------------------------------------------------------------
#
# Regression introduced by #1344 (per-VFO routing). When the active slot on
# a single-receiver radio is "B", ``_active_vfo_name()`` returns "VFOB" —
# which is correct for the get_vfo question. But the per-VFO routing then
# treated VFOB as ``receiver=1``, i.e. the SUB receiver of a *dual-RX*
# rig, which does not exist on a single-RX backend. Slot selection on
# single-RX is via ``set_vfo_slot`` and lives on the same ``receiver=0``.


class TestSingleRxActiveSlotBRouting:
    """Bare ``F``/``M``/``T`` on single-RX with active_slot="B" routes to
    receiver=0 (issue #1354). Without a VFO arg the handler may legitimately
    resolve target to VFOB via the active-slot mirror, but the I/O kwarg
    must still target the single available receiver (receiver=0)."""

    @pytest.mark.asyncio
    async def test_set_freq_no_vfo_arg_routes_to_receiver_0(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        state = RadioState()
        state.main.active_slot = "B"
        single_rx_radio.radio_state = state

        resp = await single_rx_handler.execute(
            _vfo_set_cmd("set_freq", None, "14074000")
        )

        assert resp.ok
        single_rx_radio.set_freq.assert_awaited_once_with(14_074_000, receiver=0)

    @pytest.mark.asyncio
    async def test_set_mode_no_vfo_arg_routes_to_receiver_0(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        state = RadioState()
        state.main.active_slot = "B"
        single_rx_radio.radio_state = state

        resp = await single_rx_handler.execute(
            _vfo_set_cmd("set_mode", None, "USB", "2400")
        )

        assert resp.ok
        # MAIN path: ``set_mode`` called WITHOUT a receiver kwarg (legacy
        # default == receiver=0). Critically, NOT ``receiver=1``.
        single_rx_radio.set_mode.assert_awaited_once_with("USB", filter_width=2)

    @pytest.mark.asyncio
    async def test_set_ptt_no_vfo_arg_keys_radio(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        # PTT is global on the Icom CI-V — no receiver kwarg is involved.
        # Regression-guard: bare ``T 1`` under active_slot="B" on single-RX
        # must succeed (not EVFO) and reach the radio.
        state = RadioState()
        state.main.active_slot = "B"
        single_rx_radio.radio_state = state

        resp = await single_rx_handler.execute(_vfo_set_cmd("set_ptt", None, "1"))

        assert resp.ok
        single_rx_radio.set_ptt.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_get_freq_no_vfo_arg_reads_main_path(
        self, single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
    ) -> None:
        # The single-RX MAIN slot mirror is wfview's contract — main.freq
        # always reflects the *currently selected* slot (A or B) on a
        # single-RX backend (see SerialMockRadio). So the legacy MAIN path
        # is correct even when active_slot="B" — what we must NOT do is
        # branch into the SUB-state read intended for dual-RX VFOB.
        state = RadioState()
        state.main.active_slot = "B"
        state.main.freq = 14_074_000
        # state.sub.freq stays 0 to detect mis-routing through SUB.
        single_rx_radio.radio_state = state

        resp = await single_rx_handler.execute(_vfo_get_cmd("get_freq", None))

        assert resp.ok
        assert resp.values == ["14074000"]

    @pytest.mark.asyncio
    async def test_dual_rx_vfob_still_routes_to_receiver_1(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        # Dual-RX path is unchanged: explicit ``F VFOB <freq>`` still
        # uses receiver=1. This guards against the helper accidentally
        # collapsing dual-RX routing to receiver=0.
        resp = await dual_rx_handler.execute(
            _vfo_set_cmd("set_freq", "VFOB", "7080000")
        )

        assert resp.ok
        dual_rx_radio.set_freq.assert_awaited_once_with(7_080_000, receiver=1)


# ---------------------------------------------------------------------------
# Issue #1355 — explicit VFOB get with no radio_state must not fall through
# ---------------------------------------------------------------------------
#
# When ``chk_vfo=1`` and a client sends ``f VFOB`` / ``m VFOB`` against a
# backend whose ``radio_state`` is ``None``, the handler used to silently
# fall through to the MAIN read path and return MAIN data labelled as
# VFOB. That is a contract violation. After #1355 the handler returns
# ``ENIMPL`` for *explicit* VFOB requests so the client surfaces the
# failure rather than acting on wrong data. Implicit/legacy paths
# (``vfo_arg is None`` / ``"currVFO"``) keep the forgiving MAIN
# fall-through.


class TestExplicitVfobNoStateReturnsEnimpl:
    @pytest.mark.asyncio
    async def test_get_freq_explicit_vfob_no_state_returns_enimpl(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        # Dual-RX profile but no RadioState wired up.
        dual_rx_radio.radio_state = None

        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_freq", "VFOB"))

        assert resp.error == HamlibError.ENIMPL
        # Critically, the handler must NOT call ``get_freq()`` and return
        # MAIN data labelled as VFOB.
        dual_rx_radio.get_freq.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_mode_explicit_vfob_no_state_returns_enimpl(
        self, dual_rx_handler: RigctldHandler, dual_rx_radio: AsyncMock
    ) -> None:
        dual_rx_radio.radio_state = None

        resp = await dual_rx_handler.execute(_vfo_get_cmd("get_mode", "VFOB"))

        assert resp.error == HamlibError.ENIMPL
        dual_rx_radio.get_mode.assert_not_awaited()
