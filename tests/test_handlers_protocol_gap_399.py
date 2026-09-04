"""Tests for new WS command handlers added in Issue #399 (protocol gap commands)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.command_dispatch import CommandUnsupportedError
from rigplane.core.state_pipeline_contracts import CommandIntent
from rigplane.profiles import resolve_radio_profile
from rigplane.web.handlers import ControlHandler
from rigplane.web.radio_poller import (
    SetAntiVoxGain,
    SetBreakInDelay,
    SetDashRatio,
    SetMainSubTracking,
    SetManualNotchWidth,
    SetNbDepth,
    SetNbWidth,
    SetRepeaterTone,
    SetRepeaterTsql,
    SetSsbTxBandwidth,
    SetToneFreq,
    SetTsqlFreq,
    SetVoxDelay,
    SetVoxGain,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capable_radio() -> SimpleNamespace:
    """Radio mock with IC-7610 profile (has all required capabilities)."""
    return SimpleNamespace(
        capabilities={
            "nb",
            "notch",
            "cw",
            "break_in",
            "vox",
            "repeater_tone",
            "tsql",
            "main_sub_tracking",
            "ssb_tx_bw",
            "rx_antenna",
            "dual_rx",
        },
        profile=resolve_radio_profile(model="IC-7610"),
        supports_command=MagicMock(return_value=True),
        # ScopeCapable attrs required for isinstance check
        enable_scope=AsyncMock(),
        disable_scope=AsyncMock(),
        on_scope_data=MagicMock(),
        capture_scope_frame=AsyncMock(),
        capture_scope_frames=AsyncMock(),
        set_scope_during_tx=AsyncMock(),
        set_scope_center_type=AsyncMock(),
        set_scope_fixed_edge=AsyncMock(),
        # AdvancedControlCapable attrs
        send_cw_text=AsyncMock(),
        stop_cw_text=AsyncMock(),
        set_attenuator=AsyncMock(),
        set_attenuator_level=AsyncMock(),
        get_attenuator_level=AsyncMock(return_value=0),
        set_preamp=AsyncMock(),
        get_preamp=AsyncMock(return_value=0),
        set_antenna_1=AsyncMock(),
        set_antenna_2=AsyncMock(),
        set_rx_antenna_ant1=AsyncMock(),
        set_rx_antenna_ant2=AsyncMock(),
        get_antenna_1=AsyncMock(return_value=0),
        get_antenna_2=AsyncMock(return_value=0),
        get_rx_antenna_ant1=AsyncMock(return_value=0),
        get_rx_antenna_ant2=AsyncMock(return_value=0),
        set_system_date=AsyncMock(),
        get_system_date=AsyncMock(return_value=(2026, 1, 1)),
        set_system_time=AsyncMock(),
        get_system_time=AsyncMock(return_value=(0, 0)),
        set_dual_watch=AsyncMock(),
        get_dual_watch=AsyncMock(return_value=False),
        set_tuner_status=AsyncMock(),
        get_tuner_status=AsyncMock(return_value=0),
        set_acc1_mod_level=AsyncMock(),
        set_usb_mod_level=AsyncMock(),
        set_lan_mod_level=AsyncMock(),
        set_agc=AsyncMock(),
        set_compressor=AsyncMock(),
        set_compressor_level=AsyncMock(),
        set_cw_pitch=AsyncMock(),
        set_key_speed=AsyncMock(),
        set_break_in=AsyncMock(),
        set_data_mode=AsyncMock(),
        set_dial_lock=AsyncMock(),
        set_mic_gain=AsyncMock(),
        set_monitor=AsyncMock(),
        set_monitor_gain=AsyncMock(),
        set_nb=AsyncMock(),
        set_nb_level=AsyncMock(),
        set_nr=AsyncMock(),
        set_nr_level=AsyncMock(),
        set_notch_filter=AsyncMock(),
        set_ip_plus=AsyncMock(),
        set_digisel=AsyncMock(),
        set_filter=AsyncMock(),
        set_filter_shape=AsyncMock(),
        set_auto_notch=AsyncMock(),
        set_manual_notch=AsyncMock(),
        get_auto_notch=AsyncMock(return_value=False),
        get_manual_notch=AsyncMock(return_value=False),
        set_agc_time_constant=AsyncMock(),
        set_vox=AsyncMock(),
        get_vox=AsyncMock(return_value=False),
        get_dial_lock=AsyncMock(return_value=False),
        get_monitor=AsyncMock(return_value=False),
        get_cw_pitch=AsyncMock(return_value=600),
        get_vox_gain=AsyncMock(return_value=128),
        set_vox_gain=AsyncMock(),
        get_anti_vox_gain=AsyncMock(return_value=128),
        set_anti_vox_gain=AsyncMock(),
        get_monitor_gain=AsyncMock(return_value=128),
        get_pbt_inner=AsyncMock(return_value=128),
        set_pbt_inner=AsyncMock(),
        get_pbt_outer=AsyncMock(return_value=128),
        set_pbt_outer=AsyncMock(),
        get_scope_receiver=AsyncMock(return_value=0),
        set_scope_receiver=AsyncMock(),
        get_scope_dual=AsyncMock(return_value=False),
        set_scope_dual=AsyncMock(),
        get_scope_mode=AsyncMock(return_value=0),
        set_scope_mode=AsyncMock(),
        get_scope_span=AsyncMock(return_value=0),
        set_scope_span=AsyncMock(),
        get_scope_speed=AsyncMock(return_value=0),
        set_scope_speed=AsyncMock(),
        get_scope_ref=AsyncMock(return_value=0),
        set_scope_ref=AsyncMock(),
        get_scope_hold=AsyncMock(return_value=False),
        set_scope_hold=AsyncMock(),
    )


def _incapable_radio() -> SimpleNamespace:
    """Radio mock with empty capabilities and no profile — all capability checks fail."""
    return SimpleNamespace(
        capabilities=set(),
        profile=None,
        supports_command=MagicMock(return_value=False),
    )


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object, **_metadata: object) -> None:
        self.items.append(item)

    def put_ordered(self, item: object, **_metadata: object) -> None:
        self.items.append(item)
        future = _metadata.get("future")
        if isinstance(future, asyncio.Future) and not future.done():
            future.set_result(None)


def _handler(
    radio: object | None = None,
    server: object | None = None,
) -> ControlHandler:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    return ControlHandler(ws, radio, "9.9.9", "IC-7610", server=server)


def _server() -> tuple[SimpleNamespace, _QueueRecorder]:
    q = _QueueRecorder()
    return SimpleNamespace(command_queue=q), q


# ---------------------------------------------------------------------------
# set_tone_freq
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_tone_freq_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_tone_freq", {"freq": 8800})
    assert result == {"freq": 8800, "receiver": 0}
    assert len(q.items) == 1
    assert isinstance(q.items[0], SetToneFreq)
    assert q.items[0].freq_hz == 8800
    assert q.items[0].receiver == 0


@pytest.mark.asyncio
async def test_set_tone_freq_sub_receiver() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_tone_freq", {"freq": 10000, "receiver": 1})
    assert result == {"freq": 10000, "receiver": 1}
    assert q.items[0].receiver == 1


@pytest.mark.asyncio
async def test_set_tone_freq_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="repeater_tone"):
        await h._enqueue_command("set_tone_freq", {"freq": 8800})


# ---------------------------------------------------------------------------
# set_tsql_freq
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_tsql_freq_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_tsql_freq", {"freq": 9700})
    assert result == {"freq": 9700, "receiver": 0}
    assert isinstance(q.items[0], SetTsqlFreq)
    assert q.items[0].freq_hz == 9700


@pytest.mark.asyncio
async def test_set_tsql_freq_sub_receiver() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_tsql_freq", {"freq": 9700, "receiver": 1})
    assert result["receiver"] == 1
    assert q.items[0].receiver == 1


@pytest.mark.asyncio
async def test_set_tsql_freq_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="tsql"):
        await h._enqueue_command("set_tsql_freq", {"freq": 9700})


# ---------------------------------------------------------------------------
# set_main_sub_tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_main_sub_tracking_on() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_main_sub_tracking", {"on": True})
    assert result == {"on": True}
    assert isinstance(q.items[0], SetMainSubTracking)
    assert q.items[0].on is True


@pytest.mark.asyncio
async def test_set_main_sub_tracking_off() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_main_sub_tracking", {"on": False})
    assert result == {"on": False}
    assert q.items[0].on is False


@pytest.mark.asyncio
async def test_set_main_sub_tracking_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="main_sub_tracking"):
        await h._enqueue_command("set_main_sub_tracking", {"on": True})


# ---------------------------------------------------------------------------
# set_ssb_tx_bw
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_ssb_tx_bw_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_ssb_tx_bw", {"value": 2})
    assert result == {"value": 2}
    assert isinstance(q.items[0], SetSsbTxBandwidth)
    assert q.items[0].value == 2


@pytest.mark.asyncio
async def test_set_ssb_tx_bw_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="ssb_tx_bw"):
        await h._enqueue_command("set_ssb_tx_bw", {"value": 1})


# ---------------------------------------------------------------------------
# set_manual_notch_width
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_manual_notch_width_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_manual_notch_width", {"value": 128})
    assert result == {"value": 128, "receiver": 0}
    assert isinstance(q.items[0], SetManualNotchWidth)
    assert q.items[0].value == 128
    assert q.items[0].receiver == 0


@pytest.mark.asyncio
async def test_set_manual_notch_width_sub_receiver() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command(
        "set_manual_notch_width", {"value": 64, "receiver": 1}
    )
    assert result["receiver"] == 1
    assert q.items[0].receiver == 1


@pytest.mark.asyncio
async def test_set_manual_notch_width_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="notch"):
        await h._enqueue_command("set_manual_notch_width", {"value": 128})


# ---------------------------------------------------------------------------
# set_break_in_delay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_break_in_delay_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_break_in_delay", {"level": 50})
    assert result == {"level": 50}
    assert isinstance(q.items[0], SetBreakInDelay)
    assert q.items[0].level == 50


@pytest.mark.asyncio
async def test_set_break_in_delay_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="break_in"):
        await h._enqueue_command("set_break_in_delay", {"level": 50})


# ---------------------------------------------------------------------------
# set_vox_gain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_vox_gain_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_vox_gain", {"level": 128})
    assert result == {"level": 128}
    assert isinstance(q.items[0], SetVoxGain)
    assert q.items[0].level == 128


@pytest.mark.asyncio
async def test_set_vox_gain_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="vox"):
        await h._enqueue_command("set_vox_gain", {"level": 128})


# ---------------------------------------------------------------------------
# set_anti_vox_gain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_anti_vox_gain_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_anti_vox_gain", {"level": 64})
    assert result == {"level": 64}
    assert isinstance(q.items[0], SetAntiVoxGain)
    assert q.items[0].level == 64


@pytest.mark.asyncio
async def test_set_anti_vox_gain_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="vox"):
        await h._enqueue_command("set_anti_vox_gain", {"level": 64})


# ---------------------------------------------------------------------------
# set_vox_delay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_vox_delay_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_vox_delay", {"level": 30})
    assert result == {"level": 30}
    assert isinstance(q.items[0], SetVoxDelay)
    assert q.items[0].level == 30


@pytest.mark.asyncio
async def test_set_vox_delay_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="vox"):
        await h._enqueue_command("set_vox_delay", {"level": 30})


# ---------------------------------------------------------------------------
# set_nb_depth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_nb_depth_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_nb_depth", {"level": 100})
    assert result == {"level": 100, "receiver": 0}
    assert isinstance(q.items[0], SetNbDepth)
    assert q.items[0].level == 100
    assert q.items[0].receiver == 0


@pytest.mark.asyncio
async def test_set_nb_depth_sub_receiver() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_nb_depth", {"level": 100, "receiver": 1})
    assert result["receiver"] == 1
    assert q.items[0].receiver == 1


@pytest.mark.asyncio
async def test_set_nb_depth_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="nb"):
        await h._enqueue_command("set_nb_depth", {"level": 100})


# ---------------------------------------------------------------------------
# set_nb_width
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_nb_width_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_nb_width", {"level": 50})
    assert result == {"level": 50, "receiver": 0}
    assert isinstance(q.items[0], SetNbWidth)
    assert q.items[0].level == 50


@pytest.mark.asyncio
async def test_set_nb_width_sub_receiver() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_nb_width", {"level": 50, "receiver": 1})
    assert result["receiver"] == 1
    assert q.items[0].receiver == 1


@pytest.mark.asyncio
async def test_set_nb_width_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="nb"):
        await h._enqueue_command("set_nb_width", {"level": 50})


# ---------------------------------------------------------------------------
# set_dash_ratio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_dash_ratio_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_dash_ratio", {"value": 3})
    assert result == {"value": 3}
    assert isinstance(q.items[0], SetDashRatio)
    assert q.items[0].value == 3


@pytest.mark.asyncio
async def test_set_dash_ratio_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="cw"):
        await h._enqueue_command("set_dash_ratio", {"value": 3})


# ---------------------------------------------------------------------------
# set_repeater_tone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_repeater_tone_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_repeater_tone", {"on": True})
    assert result == {"on": True, "receiver": 0}
    assert isinstance(q.items[0], SetRepeaterTone)
    assert q.items[0].on is True
    assert q.items[0].receiver == 0


@pytest.mark.asyncio
async def test_set_repeater_tone_sub_receiver() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_repeater_tone", {"on": False, "receiver": 1})
    assert result["receiver"] == 1
    assert q.items[0].receiver == 1


@pytest.mark.asyncio
async def test_set_repeater_tone_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="repeater_tone"):
        await h._enqueue_command("set_repeater_tone", {"on": True})


# ---------------------------------------------------------------------------
# set_repeater_tsql
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_repeater_tsql_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_repeater_tsql", {"on": True})
    assert result == {"on": True, "receiver": 0}
    assert isinstance(q.items[0], SetRepeaterTsql)
    assert q.items[0].on is True


@pytest.mark.asyncio
async def test_set_repeater_tsql_sub_receiver() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_repeater_tsql", {"on": True, "receiver": 1})
    assert result["receiver"] == 1
    assert q.items[0].receiver == 1


@pytest.mark.asyncio
async def test_set_repeater_tsql_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="tsql"):
        await h._enqueue_command("set_repeater_tsql", {"on": True})


# ---------------------------------------------------------------------------
# set_rx_antenna
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_rx_antenna_ant1() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_rx_antenna", {"antenna": 1, "on": True})
    assert result == {"antenna": 1, "on": True}
    assert isinstance(q.items[0], CommandIntent)
    assert q.items[0].params["antenna"] == 1
    assert q.items[0].params["on"] is True


@pytest.mark.asyncio
async def test_set_rx_antenna_ant2() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_rx_antenna", {"antenna": 2, "on": False})
    assert result == {"antenna": 2, "on": False}
    assert isinstance(q.items[0], CommandIntent)
    assert q.items[0].params["antenna"] == 2
    assert q.items[0].params["on"] is False


@pytest.mark.asyncio
async def test_set_rx_antenna_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(CommandUnsupportedError, match="not supported"):
        await h._enqueue_command("set_rx_antenna", {"antenna": 1, "on": True})
