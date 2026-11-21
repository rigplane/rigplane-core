"""Tests for Web handlers added in issues #407 (data mod inputs/levels) and #408 (DSP advanced)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from _caps import FULL_ICOM_CAPS
from rigplane.profiles import resolve_radio_profile
from rigplane.web.handlers import ControlHandler
from rigplane.web.radio_poller import (
    SetAudioPeakFilter,
    SetData1ModInput,
    SetData2ModInput,
    SetData3ModInput,
    SetDataOffModInput,
    SetDigiselShift,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capable_radio() -> SimpleNamespace:
    return SimpleNamespace(
        capabilities=set(FULL_ICOM_CAPS),
        profile=resolve_radio_profile(model="IC-7610"),
        # ScopeCapable attrs
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
        get_acc1_mod_level=AsyncMock(return_value=128),
        get_usb_mod_level=AsyncMock(return_value=64),
        get_lan_mod_level=AsyncMock(return_value=200),
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
        # AdvancedControlCapable — extended protocol methods
        get_audio_peak_filter=AsyncMock(return_value=0),
        set_twin_peak_filter=AsyncMock(),
        get_twin_peak_filter=AsyncMock(return_value=False),
        set_ssb_tx_bandwidth=AsyncMock(),
        get_ssb_tx_bandwidth=AsyncMock(return_value=0),
        set_manual_notch_width=AsyncMock(),
        get_manual_notch_width=AsyncMock(return_value=0),
        set_break_in_delay=AsyncMock(),
        get_break_in_delay=AsyncMock(return_value=0),
        set_vox_delay=AsyncMock(),
        get_vox_delay=AsyncMock(return_value=0),
        set_nb_depth=AsyncMock(),
        get_nb_depth=AsyncMock(return_value=0),
        set_nb_width=AsyncMock(),
        get_nb_width=AsyncMock(return_value=0),
        set_dash_ratio=AsyncMock(),
        get_dash_ratio=AsyncMock(return_value=30),
        get_key_speed=AsyncMock(return_value=20),
        set_band=AsyncMock(),
        scan_start=AsyncMock(),
        scan_stop=AsyncMock(),
        set_repeater_tone=AsyncMock(),
        get_repeater_tone=AsyncMock(return_value=False),
        set_repeater_tsql=AsyncMock(),
        get_repeater_tsql=AsyncMock(return_value=False),
        set_tone_freq=AsyncMock(),
        get_tone_freq=AsyncMock(return_value=8800),
        set_tsql_freq=AsyncMock(),
        get_tsql_freq=AsyncMock(return_value=8800),
        set_main_sub_tracking=AsyncMock(),
        get_main_sub_tracking=AsyncMock(return_value=False),
        # #407 data mod input methods
        get_data_off_mod_input=AsyncMock(return_value=0),
        set_data_off_mod_input=AsyncMock(),
        get_data1_mod_input=AsyncMock(return_value=1),
        set_data1_mod_input=AsyncMock(),
        get_data2_mod_input=AsyncMock(return_value=2),
        set_data2_mod_input=AsyncMock(),
        get_data3_mod_input=AsyncMock(return_value=3),
        set_data3_mod_input=AsyncMock(),
        # #408 DSP advanced methods
        set_audio_peak_filter=AsyncMock(),
        set_digisel_shift=AsyncMock(),
    )


def _incapable_radio() -> SimpleNamespace:
    return SimpleNamespace(
        capabilities=set(),
        profile=None,
    )


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object) -> None:
        self.items.append(item)


def _handler(
    radio: object | None = None, server: object | None = None
) -> ControlHandler:
    ws = SimpleNamespace(outbound_progress=0, send_text=AsyncMock(), recv=AsyncMock())
    return ControlHandler(ws, radio, "9.9.9", "IC-7610", server=server)


def _server() -> tuple[SimpleNamespace, _QueueRecorder]:
    q = _QueueRecorder()
    return SimpleNamespace(command_queue=q), q


# ---------------------------------------------------------------------------
# Issue #407 — get modulation levels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_acc1_mod_level() -> None:
    srv, _ = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("get_acc1_mod_level", {})
    assert result == {"level": 128}


@pytest.mark.asyncio
async def test_get_usb_mod_level() -> None:
    srv, _ = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("get_usb_mod_level", {})
    assert result == {"level": 64}


@pytest.mark.asyncio
async def test_get_lan_mod_level() -> None:
    srv, _ = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("get_lan_mod_level", {})
    assert result == {"level": 200}


@pytest.mark.asyncio
async def test_get_mod_level_no_radio() -> None:
    srv, _ = _server()
    h = _handler(radio=None, server=srv)
    with pytest.raises(RuntimeError, match="radio connection not available"):
        await h._enqueue_command("get_acc1_mod_level", {})


# ---------------------------------------------------------------------------
# Issue #407 — get data mod inputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_data_off_mod_input() -> None:
    srv, _ = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("get_data_off_mod_input", {})
    assert result == {"source": 0}


@pytest.mark.asyncio
async def test_get_data1_mod_input() -> None:
    srv, _ = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("get_data1_mod_input", {})
    assert result == {"source": 1}


@pytest.mark.asyncio
async def test_get_data2_mod_input() -> None:
    srv, _ = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("get_data2_mod_input", {})
    assert result == {"source": 2}


@pytest.mark.asyncio
async def test_get_data3_mod_input() -> None:
    srv, _ = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("get_data3_mod_input", {})
    assert result == {"source": 3}


@pytest.mark.asyncio
async def test_get_data_mod_input_no_radio() -> None:
    srv, _ = _server()
    h = _handler(radio=None, server=srv)
    with pytest.raises(RuntimeError, match="radio connection not available"):
        await h._enqueue_command("get_data1_mod_input", {})


# ---------------------------------------------------------------------------
# Issue #407 — set data mod inputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_data_off_mod_input_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_data_off_mod_input", {"source": 2})
    assert result == {"source": 2}
    assert len(q.items) == 1
    assert isinstance(q.items[0], SetDataOffModInput)
    assert q.items[0].source == 2


@pytest.mark.asyncio
async def test_set_data1_mod_input_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_data1_mod_input", {"source": 1})
    assert result == {"source": 1}
    assert isinstance(q.items[0], SetData1ModInput)
    assert q.items[0].source == 1


@pytest.mark.asyncio
async def test_set_data2_mod_input_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_data2_mod_input", {"source": 3})
    assert result == {"source": 3}
    assert isinstance(q.items[0], SetData2ModInput)
    assert q.items[0].source == 3


@pytest.mark.asyncio
async def test_set_data3_mod_input_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_data3_mod_input", {"source": 0})
    assert result == {"source": 0}
    assert isinstance(q.items[0], SetData3ModInput)
    assert q.items[0].source == 0


# ---------------------------------------------------------------------------
# Issue #408 — set_audio_peak_filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_audio_peak_filter_on() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_audio_peak_filter", {"on": True})
    assert result == {"on": True, "receiver": 0}
    assert len(q.items) == 1
    assert isinstance(q.items[0], SetAudioPeakFilter)
    assert q.items[0].on is True
    assert q.items[0].receiver == 0


@pytest.mark.asyncio
async def test_set_audio_peak_filter_off() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_audio_peak_filter", {"on": False})
    assert result == {"on": False, "receiver": 0}
    assert q.items[0].on is False


@pytest.mark.asyncio
async def test_set_audio_peak_filter_sub_receiver() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command(
        "set_audio_peak_filter", {"on": True, "receiver": 1}
    )
    assert result == {"on": True, "receiver": 1}
    assert q.items[0].receiver == 1


@pytest.mark.asyncio
async def test_set_audio_peak_filter_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="apf"):
        await h._enqueue_command("set_audio_peak_filter", {"on": True})


# ---------------------------------------------------------------------------
# Issue #408 — set_digisel_shift
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_digisel_shift_happy_path() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_digisel_shift", {"level": 128})
    assert result == {"level": 128, "receiver": 0}
    assert len(q.items) == 1
    assert isinstance(q.items[0], SetDigiselShift)
    assert q.items[0].level == 128
    assert q.items[0].receiver == 0


@pytest.mark.asyncio
async def test_set_digisel_shift_sub_receiver() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command(
        "set_digisel_shift", {"level": 255, "receiver": 1}
    )
    assert result == {"level": 255, "receiver": 1}
    assert q.items[0].receiver == 1


@pytest.mark.asyncio
async def test_set_digisel_shift_missing_capability() -> None:
    srv, _ = _server()
    h = _handler(radio=_incapable_radio(), server=srv)
    with pytest.raises(ValueError, match="digisel"):
        await h._enqueue_command("set_digisel_shift", {"level": 100})
