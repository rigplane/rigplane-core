"""Tests for WS command handlers added for issues #410 and #411.

Issue #410: system/config commands (ref_adjust, civ_transceive, civ_output_ant,
            af_mute, tuning_step, utc_offset)
Issue #411: band/split advanced commands (band_edge_freq, xfc_status,
            tx_freq_monitor, quick_split, quick_dual_watch)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from _caps import FULL_ICOM_CAPS
from rigplane.profiles import resolve_radio_profile
from rigplane.web.handlers import ControlHandler
from rigplane.web.radio_poller import (
    QuickDualWatch,
    QuickSplit,
    SetAfMute,
    SetCivOutputAnt,
    SetCivTransceive,
    SetRefAdjust,
    SetTuningStep,
    SetTxFreqMonitor,
    SetUtcOffset,
    SetXfcStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capable_radio() -> SimpleNamespace:
    """Radio mock satisfying AdvancedControlCapable + TransceiverStatusCapable."""
    return SimpleNamespace(
        capabilities=set(FULL_ICOM_CAPS),
        profile=resolve_radio_profile(model="IC-7610"),
        # ScopeCapable attrs required for isinstance check
        enable_scope=AsyncMock(),
        disable_scope=AsyncMock(),
        on_scope_data=MagicMock(),
        capture_scope_frame=AsyncMock(),
        capture_scope_frames=AsyncMock(),
        set_scope_during_tx=AsyncMock(),
        set_scope_center_type=AsyncMock(),
        set_scope_fixed_edge=AsyncMock(),
        # AdvancedControlCapable required attrs (subset needed for isinstance)
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
        get_antenna_1=AsyncMock(return_value=True),
        get_antenna_2=AsyncMock(return_value=False),
        get_rx_antenna_ant1=AsyncMock(return_value=False),
        get_rx_antenna_ant2=AsyncMock(return_value=False),
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
        get_key_speed=AsyncMock(return_value=20),
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
        set_audio_peak_filter=AsyncMock(),
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
        set_band=AsyncMock(),
        scan_start=AsyncMock(),
        scan_stop=AsyncMock(),
        set_repeater_tone=AsyncMock(),
        get_repeater_tone=AsyncMock(return_value=False),
        set_repeater_tsql=AsyncMock(),
        get_repeater_tsql=AsyncMock(return_value=False),
        set_tone_freq=AsyncMock(),
        get_tone_freq=AsyncMock(return_value=8850),
        set_tsql_freq=AsyncMock(),
        get_tsql_freq=AsyncMock(return_value=8850),
        set_main_sub_tracking=AsyncMock(),
        get_main_sub_tracking=AsyncMock(return_value=False),
        # New methods for #410/#411
        get_ref_adjust=AsyncMock(return_value=256),
        set_ref_adjust=AsyncMock(),
        get_civ_transceive=AsyncMock(return_value=True),
        set_civ_transceive=AsyncMock(),
        get_civ_output_ant=AsyncMock(return_value=False),
        set_civ_output_ant=AsyncMock(),
        get_af_mute=AsyncMock(return_value=False),
        set_af_mute=AsyncMock(),
        get_tuning_step=AsyncMock(return_value=3),
        set_tuning_step=AsyncMock(),
        get_utc_offset=AsyncMock(return_value=(9, 0, False)),
        set_utc_offset=AsyncMock(),
        get_band_edge_freq=AsyncMock(return_value=14_350_000),
        get_xfc_status=AsyncMock(return_value=False),
        set_xfc_status=AsyncMock(),
        # TransceiverStatusCapable methods
        get_rit_frequency=AsyncMock(return_value=0),
        set_rit_frequency=AsyncMock(),
        get_rit_status=AsyncMock(return_value=False),
        set_rit_status=AsyncMock(),
        get_rit_tx_status=AsyncMock(return_value=False),
        set_rit_tx_status=AsyncMock(),
        get_tx_freq_monitor=AsyncMock(return_value=True),
        set_tx_freq_monitor=AsyncMock(),
        # Persistent menu toggles (MOR-2007 ruling 2 renamed these from the
        # dead one-shot quick_split()/quick_dual_watch() triggers)
        get_quick_split=AsyncMock(return_value=False),
        set_quick_split=AsyncMock(),
        get_quick_dual_watch=AsyncMock(return_value=False),
        set_quick_dual_watch=AsyncMock(),
    )


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object) -> None:
        self.items.append(item)


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
# Issue #410: get_ref_adjust / set_ref_adjust
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ref_adjust() -> None:
    radio = _capable_radio()
    h = _handler(radio=radio)
    result = await h._enqueue_command("get_ref_adjust", {})
    assert result == {"value": 256}
    radio.get_ref_adjust.assert_called_once()


@pytest.mark.asyncio
async def test_get_ref_adjust_no_radio() -> None:
    h = _handler(radio=None)
    with pytest.raises(RuntimeError, match="radio connection not available"):
        await h._enqueue_command("get_ref_adjust", {})


@pytest.mark.asyncio
async def test_set_ref_adjust() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_ref_adjust", {"value": 300})
    assert result == {"value": 300}
    assert len(q.items) == 1
    assert isinstance(q.items[0], SetRefAdjust)
    assert q.items[0].value == 300


# ---------------------------------------------------------------------------
# Issue #410: get_civ_transceive / set_civ_transceive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_civ_transceive() -> None:
    radio = _capable_radio()
    h = _handler(radio=radio)
    result = await h._enqueue_command("get_civ_transceive", {})
    assert result == {"on": True}
    radio.get_civ_transceive.assert_called_once()


@pytest.mark.asyncio
async def test_set_civ_transceive_on() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_civ_transceive", {"on": True})
    assert result == {"on": True}
    assert isinstance(q.items[0], SetCivTransceive)
    assert q.items[0].on is True


@pytest.mark.asyncio
async def test_set_civ_transceive_off() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_civ_transceive", {"on": False})
    assert result == {"on": False}
    assert q.items[0].on is False


# ---------------------------------------------------------------------------
# Issue #410: get_civ_output_ant / set_civ_output_ant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_civ_output_ant() -> None:
    radio = _capable_radio()
    h = _handler(radio=radio)
    result = await h._enqueue_command("get_civ_output_ant", {})
    assert result == {"on": False}
    radio.get_civ_output_ant.assert_called_once()


@pytest.mark.asyncio
async def test_set_civ_output_ant() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_civ_output_ant", {"on": True})
    assert result == {"on": True}
    assert isinstance(q.items[0], SetCivOutputAnt)
    assert q.items[0].on is True


# ---------------------------------------------------------------------------
# Issue #410: get_af_mute / set_af_mute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_af_mute_main() -> None:
    radio = _capable_radio()
    h = _handler(radio=radio)
    result = await h._enqueue_command("get_af_mute", {})
    assert result == {"on": False, "receiver": 0}
    radio.get_af_mute.assert_called_once_with(receiver=0)


@pytest.mark.asyncio
async def test_get_af_mute_sub() -> None:
    radio = _capable_radio()
    h = _handler(radio=radio)
    result = await h._enqueue_command("get_af_mute", {"receiver": 1})
    assert result == {"on": False, "receiver": 1}
    radio.get_af_mute.assert_called_once_with(receiver=1)


@pytest.mark.asyncio
async def test_set_af_mute_main() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_af_mute", {"on": True})
    assert result == {"on": True, "receiver": 0}
    assert isinstance(q.items[0], SetAfMute)
    assert q.items[0].on is True
    assert q.items[0].receiver == 0


@pytest.mark.asyncio
async def test_set_af_mute_sub() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_af_mute", {"on": False, "receiver": 1})
    assert result == {"on": False, "receiver": 1}
    assert q.items[0].receiver == 1


# ---------------------------------------------------------------------------
# Issue #410: get_tuning_step / set_tuning_step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tuning_step() -> None:
    radio = _capable_radio()
    h = _handler(radio=radio)
    result = await h._enqueue_command("get_tuning_step", {})
    assert result == {"step": 3}
    radio.get_tuning_step.assert_called_once()


@pytest.mark.asyncio
async def test_set_tuning_step() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_tuning_step", {"step": 5})
    assert result == {"step": 5}
    assert isinstance(q.items[0], SetTuningStep)
    assert q.items[0].step == 5


# ---------------------------------------------------------------------------
# Issue #410: get_utc_offset / set_utc_offset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_utc_offset() -> None:
    radio = _capable_radio()
    h = _handler(radio=radio)
    result = await h._enqueue_command("get_utc_offset", {})
    assert result == {"hours": 9, "minutes": 0, "is_negative": False}
    radio.get_utc_offset.assert_called_once()


@pytest.mark.asyncio
async def test_set_utc_offset() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command(
        "set_utc_offset", {"hours": 5, "minutes": 30, "is_negative": True}
    )
    assert result == {"hours": 5, "minutes": 30, "is_negative": True}
    assert isinstance(q.items[0], SetUtcOffset)
    assert q.items[0].hours == 5
    assert q.items[0].minutes == 30
    assert q.items[0].is_negative is True


# ---------------------------------------------------------------------------
# Issue #411: get_band_edge_freq
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_band_edge_freq() -> None:
    radio = _capable_radio()
    h = _handler(radio=radio)
    result = await h._enqueue_command("get_band_edge_freq", {})
    assert result == {"freq": 14_350_000}
    radio.get_band_edge_freq.assert_called_once()


@pytest.mark.asyncio
async def test_get_band_edge_freq_no_radio() -> None:
    h = _handler(radio=None)
    with pytest.raises(RuntimeError, match="radio connection not available"):
        await h._enqueue_command("get_band_edge_freq", {})


# ---------------------------------------------------------------------------
# Issue #411: get_xfc_status / set_xfc_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_xfc_status() -> None:
    radio = _capable_radio()
    h = _handler(radio=radio)
    result = await h._enqueue_command("get_xfc_status", {})
    assert result == {"on": False}
    radio.get_xfc_status.assert_called_once()


@pytest.mark.asyncio
async def test_set_xfc_status_on() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_xfc_status", {"on": True})
    assert result == {"on": True}
    assert isinstance(q.items[0], SetXfcStatus)
    assert q.items[0].on is True


@pytest.mark.asyncio
async def test_set_xfc_status_off() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_xfc_status", {"on": False})
    assert result == {"on": False}
    assert q.items[0].on is False


# ---------------------------------------------------------------------------
# Issue #411: get_tx_freq_monitor / set_tx_freq_monitor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tx_freq_monitor() -> None:
    radio = _capable_radio()
    h = _handler(radio=radio)
    result = await h._enqueue_command("get_tx_freq_monitor", {})
    assert result == {"on": True}
    radio.get_tx_freq_monitor.assert_called_once()


@pytest.mark.asyncio
async def test_get_tx_freq_monitor_no_radio() -> None:
    h = _handler(radio=None)
    with pytest.raises(RuntimeError, match="radio connection not available"):
        await h._enqueue_command("get_tx_freq_monitor", {})


@pytest.mark.asyncio
async def test_set_tx_freq_monitor_on() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_tx_freq_monitor", {"on": True})
    assert result == {"on": True}
    assert isinstance(q.items[0], SetTxFreqMonitor)
    assert q.items[0].on is True


@pytest.mark.asyncio
async def test_set_tx_freq_monitor_off() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_tx_freq_monitor", {"on": False})
    assert result == {"on": False}
    assert q.items[0].on is False


# ---------------------------------------------------------------------------
# Issue #411: get_quick_split / set_quick_split
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_quick_split() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("get_quick_split", {})
    assert result == {}
    assert len(q.items) == 1
    assert isinstance(q.items[0], QuickSplit)


@pytest.mark.asyncio
async def test_set_quick_split() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_quick_split", {})
    assert result == {}
    assert isinstance(q.items[0], QuickSplit)


# ---------------------------------------------------------------------------
# Issue #411: get_quick_dual_watch / set_quick_dual_watch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_quick_dual_watch() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("get_quick_dual_watch", {})
    assert result == {}
    assert len(q.items) == 1
    assert isinstance(q.items[0], QuickDualWatch)


@pytest.mark.asyncio
async def test_set_quick_dual_watch() -> None:
    srv, q = _server()
    h = _handler(radio=_capable_radio(), server=srv)
    result = await h._enqueue_command("set_quick_dual_watch", {})
    assert result == {}
    assert isinstance(q.items[0], QuickDualWatch)


# ---------------------------------------------------------------------------
# Verify all new commands are in _COMMANDS
# ---------------------------------------------------------------------------


def test_new_commands_registered() -> None:
    from rigplane.web.handlers import ControlHandler

    expected = {
        "get_ref_adjust",
        "set_ref_adjust",
        "get_civ_transceive",
        "set_civ_transceive",
        "get_civ_output_ant",
        "set_civ_output_ant",
        "get_af_mute",
        "set_af_mute",
        "get_tuning_step",
        "set_tuning_step",
        "get_utc_offset",
        "set_utc_offset",
        "get_band_edge_freq",
        "get_xfc_status",
        "set_xfc_status",
        "get_tx_freq_monitor",
        "set_tx_freq_monitor",
        "get_quick_split",
        "set_quick_split",
        "get_quick_dual_watch",
        "set_quick_dual_watch",
    }
    assert expected <= ControlHandler._COMMANDS
