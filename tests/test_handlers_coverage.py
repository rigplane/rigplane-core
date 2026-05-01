from __future__ import annotations

import asyncio
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from _caps import FULL_ICOM_CAPS
from icom_lan.profiles import resolve_radio_profile
from icom_lan.scope import ScopeFrame
from icom_lan.types import AudioCodec
from icom_lan.web.handlers import (
    AudioBroadcaster,
    AudioHandler,
    ControlHandler,
    ScopeHandler,
)
from icom_lan.web.protocol import (
    AUDIO_CODEC_OPUS,
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    decode_json,
    encode_json,
)
from icom_lan.web.radio_poller import (
    PttOff,
    PttOn,
    QuickDwTrigger,
    QuickSplitTrigger,
    SelectVfo,
    SetAfLevel,
    SetAgc,
    SetAgcTimeConstant,
    SetAttenuator,
    SetAutoNotch,
    SetBand,
    SetCompressor,
    SetCompressorLevel,
    SetBreakIn,
    SetCwPitch,
    SetDataMode,
    SetKeySpeed,
    SetDialLock,
    SetDigiSel,
    SetDualWatch,
    SetFilter,
    SetFilterShape,
    SetFilterWidth,
    SetFreq,
    SetIpPlus,
    SetManualNotch,
    SetMicGain,
    SetMode,
    SetMonitor,
    SetMonitorGain,
    SetNB,
    SetNBLevel,
    SetNotchFilter,
    SetNR,
    SetNRLevel,
    SetPbtInner,
    SetPbtOuter,
    SetPower,
    SetPreamp,
    SetRfGain,
    SetRitFrequency,
    SetRitStatus,
    SetRitTxStatus,
    SetScopeEdge,
    SetScopeRbw,
    SetScopeVbw,
    SetSplit,
    SetSquelch,
    SetVox,
    SwitchScopeReceiver,
    VfoEqualize,
    VfoSwap,
)
from icom_lan.web.websocket import WS_OP_BINARY, WS_OP_TEXT


def _capable_radio() -> SimpleNamespace:
    """Radio mock that satisfies PowerControlCapable, LevelsCapable, ScopeCapable for enqueue tests.

    All ScopeCapable attrs must be explicitly set on SimpleNamespace; Python 3.12+
    runtime_checkable Protocol uses inspect.getattr_static which does not call __getattr__.
    """
    return SimpleNamespace(
        capabilities=set(FULL_ICOM_CAPS),
        profile=resolve_radio_profile(model="IC-7610"),
        set_rf_power=AsyncMock(),
        get_powerstat=AsyncMock(return_value=True),
        set_powerstat=AsyncMock(),
        set_rf_gain=AsyncMock(),
        set_af_level=AsyncMock(),
        set_squelch=AsyncMock(),
        get_nr_level=AsyncMock(return_value=0),
        get_nb_level=AsyncMock(return_value=0),
        get_mic_gain=AsyncMock(return_value=0),
        get_drive_gain=AsyncMock(return_value=0),
        set_drive_gain=AsyncMock(),
        get_compressor_level=AsyncMock(return_value=0),
        # ScopeCapable protocol attrs (all required for isinstance check)
        enable_scope=AsyncMock(),
        disable_scope=AsyncMock(),
        on_scope_data=MagicMock(),
        scope_stream=MagicMock(),
        capture_scope_frame=AsyncMock(),
        capture_scope_frames=AsyncMock(),
        get_scope_during_tx=AsyncMock(return_value=False),
        set_scope_during_tx=AsyncMock(),
        get_scope_center_type=AsyncMock(return_value=0),
        set_scope_center_type=AsyncMock(),
        get_scope_edge=AsyncMock(return_value=1),
        set_scope_edge=AsyncMock(),
        get_scope_fixed_edge=AsyncMock(),
        set_scope_fixed_edge=AsyncMock(),
        get_scope_vbw=AsyncMock(return_value=False),
        set_scope_vbw=AsyncMock(),
        get_scope_rbw=AsyncMock(return_value=0),
        set_scope_rbw=AsyncMock(),
        # AdvancedControlCapable protocol attrs
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
        # Phase 1 Protocol gap methods (#399)
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
        get_key_speed=AsyncMock(return_value=20),
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
    )


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object) -> None:
        self.items.append(item)


def _control_handler(
    ws: object | None = None,
    radio: object | None = None,
    server: object | None = None,
) -> ControlHandler:
    if ws is None:
        ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    return ControlHandler(ws, radio, "9.9.9", "IC-7610", server=server)


@pytest.mark.asyncio
async def test_handle_command_set_data_mode_enqueues_numeric_mode() -> None:
    queue = _QueueRecorder()
    server = SimpleNamespace(command_queue=queue)
    handler = _control_handler(radio=_capable_radio(), server=server)

    result = await handler._enqueue_command("set_data_mode", {"mode": 3, "receiver": 1})

    assert result == {"mode": 3, "receiver": 1}
    assert isinstance(queue.items[-1], SetDataMode)
    assert queue.items[-1].mode == 3
    assert queue.items[-1].receiver == 1


def _scope_frame() -> ScopeFrame:
    return ScopeFrame(
        receiver=0,
        mode=0,
        start_freq_hz=14_000_000,
        end_freq_hz=14_350_000,
        pixels=b"\x01\x02\x03",
        out_of_range=False,
    )


@pytest.mark.parametrize(
    ("name", "params", "expected_type", "expected_attrs", "expected_result"),
    [
        ("set_band", {"band": 3}, SetBand, {"band": 3}, {"band": 3}),
        (
            "set_freq",
            {"freq": 7_074_000, "receiver": 1},
            SetFreq,
            {"freq": 7_074_000, "receiver": 1},
            {"freq": 7_074_000, "receiver": 1},
        ),
        (
            "set_mode",
            {"mode": "LSB", "receiver": 1},
            SetMode,
            {"mode": "LSB", "receiver": 1},
            {"mode": "LSB", "receiver": 1},
        ),
        (
            "set_filter",
            {"filter": "FIL3", "receiver": 1},
            SetFilter,
            {"filter_num": 3, "receiver": 1},
            {"filter": "FIL3", "receiver": 1},
        ),
        (
            "set_filter",
            {"filter": "WIDE"},
            SetFilter,
            {"filter_num": 1, "receiver": 0},
            {"filter": "WIDE", "receiver": 0},
        ),
        (
            "set_filter_width",
            {"width": 1500, "receiver": 1},
            SetFilterWidth,
            {"width": 1500, "receiver": 1},
            {"width": 1500, "receiver": 1},
        ),
        (
            "set_filter_shape",
            {"shape": 1, "receiver": 1},
            SetFilterShape,
            {"shape": 1, "receiver": 1},
            {"shape": 1, "receiver": 1},
        ),
        ("ptt", {"state": True}, PttOn, {}, {"state": True}),
        ("ptt", {"state": False}, PttOff, {}, {"state": False}),
        (
            "set_rf_power",
            {"level": 88},
            SetPower,
            {"level": 88, "unit": "raw_255"},
            {"level": 88},
        ),
        (
            "set_rf_gain",
            {"level": 77, "receiver": 1},
            SetRfGain,
            {"level": 77, "receiver": 1},
            {"level": 77, "receiver": 1},
        ),
        (
            "set_af_level",
            {"level": 66, "receiver": 1},
            SetAfLevel,
            {"level": 66, "receiver": 1},
            {"level": 66, "receiver": 1},
        ),
        (
            "set_sql",
            {"level": 55, "receiver": 1},
            SetSquelch,
            {"level": 55, "receiver": 1},
            {"level": 55, "receiver": 1},
        ),
        (
            "set_nb",
            {"on": True, "receiver": 1},
            SetNB,
            {"on": True, "receiver": 1},
            {"on": True, "receiver": 1},
        ),
        (
            "set_nr",
            {"on": True, "receiver": 1},
            SetNR,
            {"on": True, "receiver": 1},
            {"on": True, "receiver": 1},
        ),
        (
            "set_nr_level",
            {"level": 42, "receiver": 1},
            SetNRLevel,
            {"level": 42, "receiver": 1},
            {"level": 42, "receiver": 1},
        ),
        (
            "set_nb_level",
            {"level": 17, "receiver": 1},
            SetNBLevel,
            {"level": 17, "receiver": 1},
            {"level": 17, "receiver": 1},
        ),
        (
            "set_auto_notch",
            {"on": True, "receiver": 1},
            SetAutoNotch,
            {"on": True, "receiver": 1},
            {"on": True, "receiver": 1},
        ),
        (
            "set_manual_notch",
            {"on": False, "receiver": 1},
            SetManualNotch,
            {"on": False, "receiver": 1},
            {"on": False, "receiver": 1},
        ),
        (
            "set_notch_filter",
            {"value": 91},
            SetNotchFilter,
            {"level": 91},
            {"value": 91},
        ),
        (
            "set_digisel",
            {"on": True, "receiver": 1},
            SetDigiSel,
            {"on": True, "receiver": 1},
            {"on": True, "receiver": 1},
        ),
        (
            "set_ip_plus",
            {"on": True, "receiver": 1},
            SetIpPlus,
            {"on": True, "receiver": 1},
            {"on": True, "receiver": 1},
        ),
        (
            "set_att",
            {"db": 12, "receiver": 1},
            SetAttenuator,
            {"db": 12, "receiver": 1},
            {"db": 12, "receiver": 1},
        ),
        (
            "set_attenuator",
            {"db": 12, "receiver": 1},
            SetAttenuator,
            {"db": 12, "receiver": 1},
            {"db": 12, "receiver": 1},
        ),
        (
            "set_preamp",
            {"level": 2, "receiver": 1},
            SetPreamp,
            {"level": 2, "receiver": 1},
            {"level": 2, "receiver": 1},
        ),
        (
            "set_pbt_inner",
            {"value": 150, "receiver": 1},
            SetPbtInner,
            {"level": 150, "receiver": 1},
            {"value": 150, "receiver": 1},
        ),
        (
            "set_pbt_outer",
            {"value": 200, "receiver": 1},
            SetPbtOuter,
            {"level": 200, "receiver": 1},
            {"value": 200, "receiver": 1},
        ),
        (
            "set_cw_pitch",
            {"value": 600},
            SetCwPitch,
            {"value": 600},
            {"value": 600},
        ),
        (
            "set_key_speed",
            {"speed": 24},
            SetKeySpeed,
            {"speed": 24},
            {"speed": 24},
        ),
        (
            "set_break_in",
            {"mode": 1},
            SetBreakIn,
            {"mode": 1},
            {"mode": 1},
        ),
        (
            "set_mic_gain",
            {"level": 123},
            SetMicGain,
            {"level": 123},
            {"level": 123},
        ),
        ("set_vox", {"on": True}, SetVox, {"on": True}, {"on": True}),
        (
            "set_compressor_level",
            {"level": 88},
            SetCompressorLevel,
            {"level": 88},
            {"level": 88},
        ),
        (
            "set_monitor",
            {"on": True},
            SetMonitor,
            {"on": True},
            {"on": True},
        ),
        (
            "set_monitor_gain",
            {"level": 55},
            SetMonitorGain,
            {"level": 55},
            {"level": 55},
        ),
        (
            "set_dial_lock",
            {"on": True},
            SetDialLock,
            {"on": True},
            {"on": True},
        ),
        (
            "set_agc_time_constant",
            {"value": 9, "receiver": 1},
            SetAgcTimeConstant,
            {"value": 9, "receiver": 1},
            {"value": 9, "receiver": 1},
        ),
        (
            "set_agc",
            {"mode": 2, "receiver": 1},
            SetAgc,
            {"mode": 2, "receiver": 1},
            {"mode": 2, "receiver": 1},
        ),
        (
            "set_rit_status",
            {"on": True},
            SetRitStatus,
            {"on": True},
            {"on": True},
        ),
        (
            "set_rit_status",
            {"on": False},
            SetRitStatus,
            {"on": False},
            {"on": False},
        ),
        (
            "set_rit_tx_status",
            {"on": True},
            SetRitTxStatus,
            {"on": True},
            {"on": True},
        ),
        (
            "set_rit_tx_status",
            {"on": False},
            SetRitTxStatus,
            {"on": False},
            {"on": False},
        ),
        (
            "set_rit_frequency",
            {"freq": 150},
            SetRitFrequency,
            {"freq": 150},
            {"freq": 150},
        ),
        (
            "set_rit_frequency",
            {"freq": -200},
            SetRitFrequency,
            {"freq": -200},
            {"freq": -200},
        ),
        ("set_split", {"on": True}, SetSplit, {"on": True}, {"on": True}),
        ("set_split", {"on": False}, SetSplit, {"on": False}, {"on": False}),
        ("set_vfo", {"vfo": "SUB"}, SelectVfo, {"vfo": "SUB"}, {"vfo": "SUB"}),
        ("ptt_on", {}, PttOn, {}, {}),
        ("ptt_off", {}, PttOff, {}, {}),
        ("vfo_swap", {}, VfoSwap, {}, {}),
        ("vfo_equalize", {}, VfoEqualize, {}, {}),
        (
            "switch_scope_receiver",
            {"receiver": 1},
            SwitchScopeReceiver,
            {"receiver": 1},
            {"receiver": 1},
        ),
        ("set_dual_watch", {"on": True}, SetDualWatch, {"on": True}, {"on": True}),
        ("set_dual_watch", {"on": False}, SetDualWatch, {"on": False}, {"on": False}),
        ("quick_dualwatch", {}, QuickDwTrigger, {}, {}),
        ("quick_split", {}, QuickSplitTrigger, {}, {}),
        ("set_compressor", {"on": True}, SetCompressor, {"on": True}, {"on": True}),
        ("set_scope_edge", {"edge": 2}, SetScopeEdge, {"edge": 2}, {"edge": 2}),
        (
            "set_scope_vbw",
            {"narrow": True},
            SetScopeVbw,
            {"narrow": True},
            {"narrow": True},
        ),
        (
            "set_scope_vbw",
            {"narrow": False},
            SetScopeVbw,
            {"narrow": False},
            {"narrow": False},
        ),
        ("set_scope_rbw", {"rbw": 1}, SetScopeRbw, {"rbw": 1}, {"rbw": 1}),
        ("set_scope_rbw", {"rbw": 2}, SetScopeRbw, {"rbw": 2}, {"rbw": 2}),
    ],
)
async def test_enqueue_command_variants(
    name: str,
    params: dict[str, object],
    expected_type: type,
    expected_attrs: dict[str, object],
    expected_result: dict[str, object],
) -> None:
    queue = _QueueRecorder()
    server = SimpleNamespace(command_queue=queue)
    handler = _control_handler(radio=_capable_radio(), server=server)
    result = await handler._enqueue_command(name, params)
    assert result == expected_result
    assert len(queue.items) == 1
    cmd = queue.items[0]
    assert isinstance(cmd, expected_type)
    for key, value in expected_attrs.items():
        assert getattr(cmd, key) == value


async def test_enqueue_set_rf_power_yaesu_tags_watts_unit() -> None:
    """Yaesu CAT backend → SetPower(unit='watts'); Icom default → 'raw_255'.

    The handler now reads ``radio.native_power_unit`` (the Capability
    Protocol property added in epic #1322) instead of the legacy
    ``backend_id == "yaesu_cat"`` discriminator.
    """
    queue = _QueueRecorder()
    server = SimpleNamespace(command_queue=queue)

    radio = _capable_radio()
    radio.native_power_unit = "watts"
    handler = _control_handler(radio=radio, server=server)
    await handler._enqueue_command("set_rf_power", {"level": 50})
    assert isinstance(queue.items[-1], SetPower)
    assert queue.items[-1].level == 50
    assert queue.items[-1].unit == "watts"

    queue2 = _QueueRecorder()
    server2 = SimpleNamespace(command_queue=queue2)
    radio2 = _capable_radio()
    radio2.native_power_unit = "raw_255"
    handler2 = _control_handler(radio=radio2, server=server2)
    await handler2._enqueue_command("set_rf_power", {"level": 200})
    assert isinstance(queue2.items[-1], SetPower)
    assert queue2.items[-1].level == 200
    assert queue2.items[-1].unit == "raw_255"


async def test_enqueue_command_errors() -> None:
    handler = _control_handler(server=None)
    with pytest.raises(RuntimeError, match="no command queue"):
        await handler._enqueue_command("set_freq", {"freq": 1})

    queue = _QueueRecorder()
    handler = _control_handler(server=SimpleNamespace(command_queue=queue))
    with pytest.raises(ValueError, match="unhandled command"):
        await handler._enqueue_command("definitely_unknown", {})

    radio = SimpleNamespace(
        capabilities={"dual_rx", "scope"},
        enable_scope=AsyncMock(),
        disable_scope=AsyncMock(),
        on_scope_data=MagicMock(),
        scope_stream=MagicMock(),
        capture_scope_frame=AsyncMock(),
        capture_scope_frames=AsyncMock(),
        get_scope_during_tx=AsyncMock(return_value=False),
        set_scope_during_tx=AsyncMock(),
        get_scope_center_type=AsyncMock(return_value=0),
        set_scope_center_type=AsyncMock(),
        get_scope_edge=AsyncMock(return_value=1),
        set_scope_edge=AsyncMock(),
        get_scope_fixed_edge=AsyncMock(),
        set_scope_fixed_edge=AsyncMock(),
        get_scope_vbw=AsyncMock(return_value=False),
        set_scope_vbw=AsyncMock(),
        get_scope_rbw=AsyncMock(return_value=0),
        set_scope_rbw=AsyncMock(),
        # ScopeCapable scope control methods
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
        # Canonical dual-RX VFO methods (DualReceiverCapable post-#1114).
        swap_main_sub=AsyncMock(),
        equalize_main_sub=AsyncMock(),
    )
    handler = _control_handler(
        radio=radio,
        server=SimpleNamespace(command_queue=queue),
    )
    with pytest.raises(ValueError, match="receiver=2"):
        await handler._enqueue_command("set_freq", {"freq": 7_074_000, "receiver": 2})
    with pytest.raises(ValueError, match="receiver=-1"):
        await handler._enqueue_command("switch_scope_receiver", {"receiver": -1})


async def test_control_run_registers_unregisters_and_sends_hello() -> None:
    ws = SimpleNamespace(
        send_text=AsyncMock(),
        recv=AsyncMock(side_effect=[(WS_OP_BINARY, b""), EOFError()]),
    )
    radio = SimpleNamespace(connected=True, radio_ready=True)
    server = SimpleNamespace(
        register_control_event_queue=MagicMock(),
        unregister_control_event_queue=MagicMock(),
    )
    handler = _control_handler(ws=ws, radio=radio, server=server)
    await handler.run()

    server.register_control_event_queue.assert_called_once()
    server.unregister_control_event_queue.assert_called_once()
    hello = decode_json(ws.send_text.await_args_list[0].args[0])
    assert hello["type"] == "hello"
    assert hello["connected"] is True
    assert hello["radio_ready"] is True


async def test_control_event_sender_loop_filters_by_subscription() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    handler = _control_handler(ws=ws)
    task = asyncio.create_task(handler._event_sender_loop())
    try:
        await handler._event_queue.put({"type": "event", "ignored": True})
        await asyncio.sleep(0)
        assert ws.send_text.await_count == 0

        handler._subscribed_streams.add("state")
        await handler._event_queue.put({"type": "event", "state": {"freq": 1}})
        await asyncio.sleep(0)
        assert ws.send_text.await_count == 1
    finally:
        task.cancel()
        await task


async def test_handle_text_dispatches_supported_types() -> None:
    handler = _control_handler()
    handler._handle_subscribe = AsyncMock()  # type: ignore[method-assign]
    handler._handle_unsubscribe = AsyncMock()  # type: ignore[method-assign]
    handler._handle_command = AsyncMock()  # type: ignore[method-assign]
    handler._handle_radio_connect = AsyncMock()  # type: ignore[method-assign]
    handler._handle_radio_disconnect = AsyncMock()  # type: ignore[method-assign]

    await handler._handle_text(encode_json({"type": "subscribe"}))
    await handler._handle_text(encode_json({"type": "unsubscribe"}))
    await handler._handle_text(encode_json({"type": "cmd"}))
    await handler._handle_text(encode_json({"type": "radio_connect"}))
    await handler._handle_text(encode_json({"type": "radio_disconnect"}))
    await handler._handle_text("not-json")
    await handler._handle_text(encode_json({"type": "unknown"}))

    handler._handle_subscribe.assert_awaited_once()
    handler._handle_unsubscribe.assert_awaited_once()
    handler._handle_command.assert_awaited_once()
    handler._handle_radio_connect.assert_awaited_once()
    handler._handle_radio_disconnect.assert_awaited_once()


async def test_subscribe_unsubscribe_and_subscribed_streams_property() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    handler = _control_handler(ws=ws)
    await handler._handle_subscribe({"streams": ["state", 1, "meters"]})
    assert handler.subscribed_streams == frozenset({"state", "1", "meters"})

    await handler._handle_unsubscribe({"streams": ["1"]})
    assert handler.subscribed_streams == frozenset({"state", "meters"})

    await handler._handle_subscribe({"streams": "not-a-list"})
    await handler._handle_unsubscribe({"streams": "not-a-list"})


async def test_send_state_snapshot_uses_server_public_state() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    payload = {
        "revision": 7,
        "updatedAt": "2026-03-17T12:00:00+00:00",
        "active": "MAIN",
        "ptt": True,
        "split": False,
        "dualWatch": False,
        "tunerStatus": 0,
        "main": {
            "freqHz": 14_074_000,
            "mode": "USB",
            "filter": 2,
            "dataMode": False,
            "att": 0,
            "preamp": 0,
            "nb": False,
            "nr": False,
            "afLevel": 0,
            "rfGain": 0,
            "squelch": 0,
            "sMeter": 0,
        },
        "sub": {
            "freqHz": 7_074_000,
            "mode": "LSB",
            "filter": 1,
            "dataMode": False,
            "att": 0,
            "preamp": 0,
            "nb": False,
            "nr": False,
            "afLevel": 0,
            "rfGain": 0,
            "squelch": 0,
            "sMeter": 0,
        },
        "connection": {
            "rigConnected": True,
            "radioReady": True,
            "controlConnected": False,
        },
    }
    handler = _control_handler(
        ws=ws,
        radio=SimpleNamespace(connected=True, radio_ready=True),
        server=SimpleNamespace(build_public_state=MagicMock(return_value=payload)),
    )
    await handler._send_state_snapshot()
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["type"] == "state_update"
    assert msg["data"] == payload


async def test_send_state_snapshot_uses_canonical_fallback_when_server_missing() -> (
    None
):
    ws = SimpleNamespace(send_text=AsyncMock())
    radio = SimpleNamespace(
        connected=True,
        radio_ready=False,
    )
    handler = _control_handler(
        ws=ws,
        radio=radio,
        server=None,
    )
    await handler._send_state_snapshot()
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["type"] == "state_update"
    assert msg["data"]["active"] == "MAIN"
    assert msg["data"]["connection"]["rigConnected"] is True
    assert msg["data"]["connection"]["radioReady"] is False
    assert "main" in msg["data"]


async def test_send_state_snapshot_builder_errors_are_ignored() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    handler = _control_handler(
        ws=ws,
        server=SimpleNamespace(
            build_public_state=MagicMock(side_effect=RuntimeError("boom"))
        ),
    )
    await handler._send_state_snapshot()
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["type"] == "state_update"
    assert msg["data"]["active"] == "MAIN"
    assert "connection" in msg["data"]


async def test_wait_radio_ready_returns_immediately_when_ready() -> None:
    """Gate returns instantly when radio is already ready."""
    handler = _control_handler(
        radio=SimpleNamespace(connected=True, radio_ready=True),
    )
    t0 = asyncio.get_event_loop().time()
    await handler._wait_radio_ready(timeout=2.0)
    elapsed = asyncio.get_event_loop().time() - t0
    assert elapsed < 0.05


async def test_wait_radio_ready_returns_immediately_when_no_radio() -> None:
    """Gate returns instantly when radio is None (offline mode)."""
    handler = _control_handler(radio=None)
    t0 = asyncio.get_event_loop().time()
    await handler._wait_radio_ready(timeout=2.0)
    elapsed = asyncio.get_event_loop().time() - t0
    assert elapsed < 0.05


async def test_wait_radio_ready_waits_then_succeeds() -> None:
    """Gate waits until radio becomes ready mid-poll."""
    radio = SimpleNamespace(connected=True, radio_ready=False)
    handler = _control_handler(radio=radio)

    async def _flip_ready() -> None:
        await asyncio.sleep(0.25)
        radio.radio_ready = True

    task = asyncio.create_task(_flip_ready())
    t0 = asyncio.get_event_loop().time()
    await handler._wait_radio_ready(timeout=2.0)
    elapsed = asyncio.get_event_loop().time() - t0
    assert 0.2 < elapsed < 1.0
    await task


async def test_wait_radio_ready_times_out_gracefully() -> None:
    """Gate gives up after timeout and does not raise."""
    handler = _control_handler(
        radio=SimpleNamespace(connected=True, radio_ready=False),
    )
    t0 = asyncio.get_event_loop().time()
    await handler._wait_radio_ready(timeout=0.3)
    elapsed = asyncio.get_event_loop().time() - t0
    assert 0.25 < elapsed < 0.6


async def test_handle_command_response_paths() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    handler = _control_handler(
        ws=ws, radio=SimpleNamespace(connected=True), server=None
    )

    await handler._handle_command({"id": "a", "name": "bad", "params": {}})
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["ok"] is False and msg["error"] == "unknown_command"

    handler = _control_handler(
        ws=ws, radio=None, server=SimpleNamespace(command_queue=_QueueRecorder())
    )
    await handler._handle_command(
        {"id": "b", "name": "set_freq", "params": {"freq": 1}}
    )
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["ok"] is False and msg["error"] == "no_radio"

    queue = _QueueRecorder()
    handler = _control_handler(
        ws=ws,
        radio=SimpleNamespace(connected=True),
        server=SimpleNamespace(command_queue=queue),
    )
    await handler._handle_command(
        {"id": "c", "name": "set_freq", "params": {"freq": 123}}
    )
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["ok"] is True and msg["result"]["freq"] == 123

    # Reset rate limiter so second command isn't throttled
    handler._cmd_last.clear()
    await handler._handle_command({"id": "d", "name": "set_freq", "params": {}})
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["ok"] is False and msg["error"] == "command_failed"


async def test_radio_connect_paths() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())

    h = _control_handler(ws=ws, radio=None)
    await h._handle_radio_connect({"id": "x"})
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["error"] == "no_radio"

    radio = SimpleNamespace(connected=True)
    h = _control_handler(ws=ws, radio=radio)
    await h._handle_radio_connect({"id": "x2"})
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["result"]["status"] == "already_connected"

    radio = SimpleNamespace(
        connected=False,
        soft_reconnect=AsyncMock(),
        connect=AsyncMock(),
    )
    h = _control_handler(ws=ws, radio=radio)
    await h._handle_radio_connect({"id": "x3"})
    msgs = [decode_json(c.args[0]) for c in ws.send_text.await_args_list[-2:]]
    assert msgs[0]["result"]["status"] == "connected"
    assert msgs[1]["type"] == "event" and msgs[1]["connected"] is True

    radio = SimpleNamespace(
        connected=False,
        soft_reconnect=AsyncMock(side_effect=RuntimeError("nope")),
        connect=AsyncMock(),
    )
    h = _control_handler(ws=ws, radio=radio)
    await h._handle_radio_connect({"id": "x4"})
    radio.connect.assert_awaited_once()

    class _RadioNoSoft:
        connected = False

        def __init__(self) -> None:
            self.connect = AsyncMock(side_effect=RuntimeError("fail"))

    h = _control_handler(ws=ws, radio=_RadioNoSoft())
    await h._handle_radio_connect({"id": "x5"})
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["ok"] is False and msg["error"] == "connect_failed"


async def test_radio_connect_rejected_while_backend_recovering() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    radio = SimpleNamespace(
        connected=True,
        radio_ready=False,
        soft_reconnect=AsyncMock(),
        connect=AsyncMock(),
    )
    h = _control_handler(ws=ws, radio=radio)
    await h._handle_radio_connect({"id": "busy"})
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["ok"] is False
    assert msg["error"] == "backend_recovering"
    radio.soft_reconnect.assert_not_awaited()
    radio.connect.assert_not_awaited()


async def test_radio_disconnect_paths() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())

    h = _control_handler(ws=ws, radio=None)
    await h._handle_radio_disconnect({"id": "d0"})
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["error"] == "no_radio"

    radio = SimpleNamespace(connected=False, soft_disconnect=AsyncMock())
    h = _control_handler(ws=ws, radio=radio)
    await h._handle_radio_disconnect({"id": "d1"})
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["result"]["status"] == "already_disconnected"

    radio = SimpleNamespace(
        connected=True,
        soft_disconnect=AsyncMock(),
        soft_reconnect=AsyncMock(),
    )
    h = _control_handler(ws=ws, radio=radio)
    await h._handle_radio_disconnect({"id": "d2"})
    msgs = [decode_json(c.args[0]) for c in ws.send_text.await_args_list[-2:]]
    assert msgs[0]["result"]["status"] == "disconnected"
    assert msgs[1]["event"] == "connection_state" and msgs[1]["connected"] is False

    radio = SimpleNamespace(
        connected=True,
        soft_disconnect=AsyncMock(side_effect=RuntimeError("boom")),
        soft_reconnect=AsyncMock(),
    )
    h = _control_handler(ws=ws, radio=radio)
    await h._handle_radio_disconnect({"id": "d3"})
    msg = decode_json(ws.send_text.await_args_list[-1].args[0])
    assert msg["ok"] is False and msg["error"] == "disconnect_failed"


async def test_scope_run_and_control_handling() -> None:
    ws = SimpleNamespace(
        recv=AsyncMock(
            side_effect=[
                (WS_OP_TEXT, encode_json({"type": "noop"}).encode("utf-8")),
                (WS_OP_TEXT, b"{"),
                EOFError(),
            ]
        ),
        send_binary=AsyncMock(),
    )
    server = SimpleNamespace(
        ensure_scope_enabled=AsyncMock(),
        unregister_scope_handler=MagicMock(),
    )
    handler = ScopeHandler(ws, None, server=server)
    handler._sender = AsyncMock(return_value=None)  # type: ignore[method-assign]
    await handler.run()
    server.ensure_scope_enabled.assert_awaited_once_with(handler)
    server.unregister_scope_handler.assert_called_once_with(handler)
    assert handler._running is False


async def test_scope_sender_timeout_and_error_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = SimpleNamespace(send_binary=AsyncMock())
    handler = ScopeHandler(ws, None)
    calls = {"count": 0}

    async def _fake_wait_for(coro: object, **_kwargs: object) -> bytes:
        calls["count"] += 1
        if calls["count"] == 1:
            if hasattr(coro, "close"):
                coro.close()
            raise TimeoutError
        if hasattr(coro, "close"):
            coro.close()
        raise RuntimeError("stop")

    monkeypatch.setattr("icom_lan.web.handlers.scope.asyncio.wait_for", _fake_wait_for)
    await handler._sender()
    assert ws.send_binary.await_count == 0


async def test_scope_sender_sends_and_stops_on_send_error() -> None:
    ws = SimpleNamespace(
        send_binary=AsyncMock(side_effect=[None, RuntimeError("boom")])
    )
    handler = ScopeHandler(ws, None)
    await handler._frame_queue.put(b"one")
    await handler._frame_queue.put(b"two")
    await handler._sender()
    assert ws.send_binary.await_count == 2


async def test_scope_enqueue_and_push_paths() -> None:
    handler = ScopeHandler(SimpleNamespace(send_binary=AsyncMock()), None)
    frame = _scope_frame()
    handler.enqueue_frame(frame)
    assert handler._frame_queue.qsize() == 0
    handler._running = True
    handler.push_frame(frame)
    assert handler._frame_queue.qsize() == 1


async def test_audio_broadcaster_subscribe_unsubscribe_lifecycle() -> None:
    from icom_lan.audio_bus import AudioBus

    radio = SimpleNamespace(
        capabilities={"audio"},
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        audio_sample_rate=48_000,
        start_audio_rx_opus=AsyncMock(),
        stop_audio_rx_opus=AsyncMock(),
        push_audio_tx_opus=AsyncMock(),
        start_audio_rx_pcm=AsyncMock(),
        stop_audio_rx_pcm=AsyncMock(),
        start_audio_tx_pcm=AsyncMock(),
        push_audio_tx_pcm=AsyncMock(),
        stop_audio_tx_pcm=AsyncMock(),
        get_audio_stats=AsyncMock(return_value={}),
        start_audio_tx_opus=AsyncMock(),
        stop_audio_tx_opus=AsyncMock(),
        audio_bus=None,
    )
    bus = AudioBus(radio)
    radio.audio_bus = bus

    broadcaster = AudioBroadcaster(radio)
    q1 = await broadcaster.subscribe()
    q2 = await broadcaster.subscribe()
    # AudioBus starts RX on first subscriber
    radio.start_audio_rx_opus.assert_awaited_once()
    await broadcaster.unsubscribe(q1)
    radio.stop_audio_rx_opus.assert_not_awaited()
    await broadcaster.unsubscribe(q2)
    # Give the scheduled stop task a chance to run
    await asyncio.sleep(0.05)
    radio.stop_audio_rx_opus.assert_awaited_once()


async def test_audio_broadcaster_codec_and_frame_metadata() -> None:
    from icom_lan.audio_bus import AudioBus

    radio = SimpleNamespace(
        capabilities={"audio"},
        audio_codec=AudioCodec.OPUS_2CH,
        audio_sample_rate=96_000,
        start_audio_rx_opus=AsyncMock(),
        stop_audio_rx_opus=AsyncMock(),
        push_audio_tx_opus=AsyncMock(),
        start_audio_rx_pcm=AsyncMock(),
        stop_audio_rx_pcm=AsyncMock(),
        start_audio_tx_pcm=AsyncMock(),
        push_audio_tx_pcm=AsyncMock(),
        stop_audio_tx_pcm=AsyncMock(),
        get_audio_stats=AsyncMock(return_value={}),
        start_audio_tx_opus=AsyncMock(),
        stop_audio_tx_opus=AsyncMock(),
        audio_bus=None,
    )
    bus = AudioBus(radio)
    radio.audio_bus = bus

    broadcaster = AudioBroadcaster(radio)
    queue = await broadcaster.subscribe()

    # Deliver a packet through the bus
    bus._on_opus_packet(None)  # should be skipped
    bus._on_opus_packet(SimpleNamespace(data=b"\xaa\xbb\xcc"))

    await asyncio.sleep(0.1)
    frame = queue.get_nowait()
    assert frame[1] == AUDIO_CODEC_OPUS
    assert struct.unpack_from("<H", frame, 4)[0] == 960
    assert frame[6] == 2

    await broadcaster.unsubscribe(queue)


async def test_audio_broadcaster_start_relay_failure() -> None:
    from icom_lan.audio_bus import AudioBus

    failing_radio = SimpleNamespace(
        capabilities={"audio"},
        audio_codec=AudioCodec.OPUS_1CH,
        audio_sample_rate=48_000,
        start_audio_rx_opus=AsyncMock(side_effect=RuntimeError("start fail")),
        stop_audio_rx_opus=AsyncMock(),
        push_audio_tx_opus=AsyncMock(),
    )
    bus = AudioBus(failing_radio)
    failing_radio.audio_bus = bus

    bad = AudioBroadcaster(failing_radio)
    await bad._start_relay()
    # Bus subscription exists but RX failed to start
    assert not bus.rx_active


async def test_audio_broadcaster_without_radio_noops() -> None:
    broadcaster = AudioBroadcaster(None)
    queue = await broadcaster.subscribe()
    assert isinstance(queue, asyncio.Queue)
    await broadcaster._stop_relay()


async def test_audio_broadcaster_reap_dead_clients_removes_dead_ws() -> None:
    """reap_dead_clients removes clients with dead WebSocket (#687)."""
    broadcaster = AudioBroadcaster(None)
    alive_ws = SimpleNamespace(is_alive=lambda: True)
    dead_ws = SimpleNamespace(is_alive=lambda: False)
    q1 = await broadcaster.subscribe(ws=alive_ws)
    q2 = await broadcaster.subscribe(ws=dead_ws)
    assert len(broadcaster._clients) == 2
    reaped = await broadcaster.reap_dead_clients()
    assert reaped == 1
    assert id(q1) in broadcaster._clients
    assert id(q2) not in broadcaster._clients


async def test_audio_broadcaster_reap_preserves_ws_less_clients() -> None:
    """reap_dead_clients must NOT remove ws-less clients (used by PCM tap consumers)."""
    broadcaster = AudioBroadcaster(None)
    q1 = await broadcaster.subscribe()  # no ws — legitimate internal consumer
    q2 = await broadcaster.subscribe(ws=SimpleNamespace(is_alive=lambda: True))
    assert len(broadcaster._clients) == 2
    reaped = await broadcaster.reap_dead_clients()
    assert reaped == 0
    assert id(q1) in broadcaster._clients
    assert id(q2) in broadcaster._clients


async def test_audio_broadcaster_reap_stops_relay_when_empty() -> None:
    """reap_dead_clients stops relay when last client is reaped (#687)."""
    broadcaster = AudioBroadcaster(None)
    dead_ws = SimpleNamespace(is_alive=lambda: False)
    await broadcaster.subscribe(ws=dead_ws)
    # Mark subscription as active so _stop_relay path triggers
    broadcaster._subscription = object()  # truthy sentinel
    broadcaster._stop_relay = AsyncMock()  # type: ignore[method-assign]
    reaped = await broadcaster.reap_dead_clients()
    assert reaped == 1
    assert len(broadcaster._clients) == 0
    broadcaster._stop_relay.assert_awaited_once()


async def test_audio_handler_reader_control_tx_and_sender_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from icom_lan.radio_protocol import AudioCapable

    broadcaster = SimpleNamespace(
        subscribe=AsyncMock(return_value=asyncio.Queue()),
        unsubscribe=AsyncMock(),
    )
    # Mock radio needs to pass isinstance(AudioCapable) check
    radio = MagicMock(spec=AudioCapable)
    radio.capabilities = {"audio"}
    radio.push_audio_tx_opus = AsyncMock()
    radio.start_audio_tx_opus = AsyncMock()
    radio.stop_audio_tx_opus = AsyncMock()
    ws = SimpleNamespace(
        recv=AsyncMock(
            side_effect=[
                (WS_OP_TEXT, b"invalid"),
                (
                    WS_OP_TEXT,
                    encode_json({"type": "audio_start", "direction": "rx"}).encode(
                        "utf-8"
                    ),
                ),
                (
                    WS_OP_TEXT,
                    encode_json({"type": "audio_start", "direction": "tx"}).encode(
                        "utf-8"
                    ),
                ),
                (WS_OP_BINARY, b"\x00" * AUDIO_HEADER_SIZE + b"\x11\x22"),
                (
                    WS_OP_TEXT,
                    encode_json({"type": "audio_stop", "direction": "tx"}).encode(
                        "utf-8"
                    ),
                ),
                EOFError(),
            ]
        ),
        send_binary=AsyncMock(),
    )
    handler = AudioHandler(ws, radio, broadcaster)
    await handler._reader_loop()
    assert handler._rx_active is True
    assert handler._tx_active is False
    radio.start_audio_tx_opus.assert_awaited_once()
    radio.push_audio_tx_opus.assert_awaited_once_with(b"\x11\x22")
    radio.stop_audio_tx_opus.assert_awaited_once()

    handler._done.clear()
    frame = b"frame"
    await handler._frame_queue.put(frame)

    async def _send_binary(data: bytes) -> None:
        assert data == frame
        handler._done.set()

    ws.send_binary = AsyncMock(side_effect=_send_binary)
    await handler._sender_loop()
    assert ws.send_binary.await_count == 1

    # Force timeout then exit with EOFError to hit sender exception path.
    calls = {"count": 0}

    async def _fake_wait_for(coro: object, timeout: float) -> bytes:
        del timeout
        calls["count"] += 1
        if calls["count"] == 1:
            if hasattr(coro, "close"):
                coro.close()
            raise TimeoutError
        return await coro  # type: ignore[misc]

    ws.send_binary = AsyncMock(side_effect=EOFError("closed"))
    handler._done.clear()
    await handler._frame_queue.put(b"x")
    monkeypatch.setattr("icom_lan.web.handlers.audio.asyncio.wait_for", _fake_wait_for)
    await handler._sender_loop()


async def test_audio_handler_control_and_tx_guard_paths() -> None:
    ws = SimpleNamespace(send_binary=AsyncMock(), recv=AsyncMock())
    from icom_lan.radio_protocol import AudioCapable

    class _FakeAudioRadio(AudioCapable):
        capabilities = {"audio"}
        push_audio_tx_opus = AsyncMock(side_effect=RuntimeError("boom"))
        start_audio_rx_opus = AsyncMock()
        stop_audio_rx_opus = AsyncMock()
        start_audio_tx_opus = AsyncMock()
        stop_audio_tx_opus = AsyncMock()
        audio_bus = None

    radio = _FakeAudioRadio()
    broadcaster = SimpleNamespace(
        subscribe=AsyncMock(return_value=asyncio.Queue()),
        unsubscribe=AsyncMock(),
    )
    handler = AudioHandler(ws, radio, broadcaster)

    await handler._start_rx()
    assert handler._rx_active is True
    await handler._handle_control({"type": "audio_stop", "direction": "rx"})
    assert handler._rx_active is False
    broadcaster.unsubscribe.assert_awaited_once()

    handler_no_broadcast = AudioHandler(ws, radio, None)
    await handler_no_broadcast._start_rx()
    await handler_no_broadcast._stop_rx()

    await handler._handle_tx_audio(b"\x00")
    await handler._handle_control({"type": "audio_start", "direction": "tx"})
    radio.start_audio_tx_opus.assert_awaited_once()
    await handler._handle_tx_audio(b"\x00" * (AUDIO_HEADER_SIZE - 1))
    await handler._handle_tx_audio(b"\x00" * AUDIO_HEADER_SIZE)
    await handler._handle_tx_audio(b"\x00" * AUDIO_HEADER_SIZE + b"\x99")
    radio.push_audio_tx_opus.assert_awaited_once_with(b"\x99")
    await handler._handle_control({"type": "audio_stop", "direction": "tx"})
    radio.stop_audio_tx_opus.assert_awaited_once()
    assert handler._tx_active is False


async def test_audio_handler_tx_already_transmitting_is_tolerated() -> None:
    """_handle_control tolerates 'Already transmitting' from start_audio_tx_opus (#684)."""
    from icom_lan.radio_protocol import AudioCapable

    radio = MagicMock(spec=AudioCapable)
    radio.capabilities = {"audio"}
    radio.start_audio_tx_opus = AsyncMock(
        side_effect=RuntimeError("Already transmitting")
    )

    ws = SimpleNamespace(recv=AsyncMock(), send_binary=AsyncMock())
    handler = AudioHandler(ws, radio, None)
    await handler._handle_control({"type": "audio_start", "direction": "tx"})
    # Handler must survive and set _tx_active = True
    assert handler._tx_active is True
    radio.start_audio_tx_opus.assert_awaited_once()


async def test_audio_handler_tx_other_runtime_error_propagates() -> None:
    """Non-'Already transmitting' RuntimeError still propagates."""
    from icom_lan.radio_protocol import AudioCapable

    radio = MagicMock(spec=AudioCapable)
    radio.capabilities = {"audio"}
    radio.start_audio_tx_opus = AsyncMock(side_effect=RuntimeError("Something else"))

    ws = SimpleNamespace(recv=AsyncMock(), send_binary=AsyncMock())
    handler = AudioHandler(ws, radio, None)
    with pytest.raises(RuntimeError, match="Something else"):
        await handler._handle_control({"type": "audio_start", "direction": "tx"})


async def test_audio_handler_run_resets_tx_active_on_exit() -> None:
    """run() finally block must reset _tx_active when TX was active (#684)."""
    ws = SimpleNamespace(
        recv=AsyncMock(side_effect=[EOFError()]),
        send_binary=AsyncMock(),
        close=AsyncMock(),
    )
    handler = AudioHandler(ws, SimpleNamespace(push_audio_tx_opus=AsyncMock()), None)
    handler._tx_active = True
    await handler.run()
    assert handler._done.is_set()
    assert handler._tx_active is False


async def test_audio_handler_run_calls_stop_rx_on_exit() -> None:
    ws = SimpleNamespace(
        recv=AsyncMock(side_effect=[EOFError()]), send_binary=AsyncMock()
    )
    broadcaster = SimpleNamespace(
        subscribe=AsyncMock(return_value=asyncio.Queue()),
        unsubscribe=AsyncMock(),
    )
    handler = AudioHandler(
        ws, SimpleNamespace(push_audio_tx_opus=AsyncMock()), broadcaster
    )
    handler._rx_active = True
    handler._frame_queue = asyncio.Queue()
    await handler.run()
    assert handler._done.is_set()
    broadcaster.unsubscribe.assert_awaited_once()


def test_audio_handler_constants_are_expected() -> None:
    assert AUDIO_CODEC_PCM16 != AUDIO_CODEC_OPUS


async def test_enqueue_command_get_dual_watch() -> None:
    """get_dual_watch is a read-only command — bypasses the command queue."""
    radio = _capable_radio()
    radio.get_dual_watch = AsyncMock(return_value=True)
    handler = _control_handler(radio=radio)
    result = await handler._enqueue_command("get_dual_watch", {})
    assert result == {"on": True}
    radio.get_dual_watch.assert_awaited_once()


async def test_enqueue_command_get_dual_watch_no_radio() -> None:
    """get_dual_watch raises when radio is not connected."""
    handler = _control_handler(radio=None)
    with pytest.raises(RuntimeError, match="radio connection not available"):
        await handler._enqueue_command("get_dual_watch", {})


@pytest.mark.asyncio
async def test_get_tuner_status_ws_command() -> None:
    """get_tuner_status is a read-only command — bypasses the command queue."""
    radio = _capable_radio()
    radio.get_tuner_status = AsyncMock(return_value=1)
    handler = _control_handler(radio=radio)
    result = await handler._enqueue_command("get_tuner_status", {})
    assert result == {"status": 1, "label": "ON"}
    radio.get_tuner_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_tuner_status_ws_command_no_radio() -> None:
    """get_tuner_status raises when radio is not connected."""
    handler = _control_handler(radio=None)
    with pytest.raises(RuntimeError, match="radio connection not available"):
        await handler._enqueue_command("get_tuner_status", {})


@pytest.mark.asyncio
async def test_set_tuner_status_ws_command() -> None:
    """set_tuner_status fires the radio method and returns label."""
    radio = _capable_radio()
    radio.set_tuner_status = AsyncMock()
    handler = _control_handler(radio=radio)
    result = await handler._enqueue_command("set_tuner_status", {"value": 2})
    assert result == {"value": 2, "label": "TUNING"}
    radio.set_tuner_status.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_set_tuner_status_ws_command_no_radio() -> None:
    """set_tuner_status raises when radio is not connected."""
    handler = _control_handler(radio=None)
    with pytest.raises(RuntimeError, match="no command queue available"):
        await handler._enqueue_command("set_tuner_status", {"value": 1})


@pytest.mark.asyncio
async def test_set_tuner_status_invalid_value() -> None:
    """set_tuner_status raises ValueError for out-of-range values."""
    radio = _capable_radio()
    radio.set_tuner_status = AsyncMock()
    handler = _control_handler(radio=radio)
    with pytest.raises(ValueError, match="tuner value must be 0, 1, or 2"):
        await handler._enqueue_command("set_tuner_status", {"value": 5})


@pytest.mark.asyncio
async def test_set_tuner_status_missing_value() -> None:
    """set_tuner_status raises ValueError when value param is missing."""
    radio = _capable_radio()
    radio.set_tuner_status = AsyncMock()
    handler = _control_handler(radio=radio)
    with pytest.raises(ValueError, match="missing required 'value' parameter"):
        await handler._enqueue_command("set_tuner_status", {})


@pytest.mark.asyncio
async def test_send_cw_text_calls_radio_method() -> None:
    """send_cw_text invokes radio.send_cw_text() and returns the text."""
    radio = _capable_radio()
    radio.send_cw_text = AsyncMock()
    handler = _control_handler(radio=radio)
    result = await handler._enqueue_command("send_cw_text", {"text": "CQ CQ DE KN4KYD"})
    assert result == {"text": "CQ CQ DE KN4KYD"}
    radio.send_cw_text.assert_awaited_once_with("CQ CQ DE KN4KYD")


@pytest.mark.asyncio
async def test_send_cw_text_multi_frame_text_reaches_radio_unchanged() -> None:
    """send_cw_text allows text longer than one CI-V keyer frame."""
    radio = _capable_radio()
    radio.send_cw_text = AsyncMock()
    handler = _control_handler(radio=radio)
    text = "CQ " * 20

    result = await handler._enqueue_command("send_cw_text", {"text": text})

    assert result == {"text": text}
    radio.send_cw_text.assert_awaited_once_with(text)


@pytest.mark.asyncio
async def test_send_cw_text_over_payload_limit_raises() -> None:
    """send_cw_text raises ValueError when text exceeds the web payload cap."""
    radio = _capable_radio()
    radio.send_cw_text = AsyncMock()
    handler = _control_handler(radio=radio)
    long_text = "A" * 513

    with pytest.raises(ValueError, match="CW text too long: max 512 characters"):
        await handler._enqueue_command("send_cw_text", {"text": long_text})
    radio.send_cw_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_cw_text_no_radio_raises() -> None:
    """send_cw_text raises when radio is not connected."""
    handler = _control_handler(radio=None)
    with pytest.raises(RuntimeError, match="radio connection not available"):
        await handler._enqueue_command("send_cw_text", {"text": "CQ"})


@pytest.mark.asyncio
async def test_stop_cw_text_calls_radio_method() -> None:
    """stop_cw_text invokes radio.stop_cw_text() and returns empty dict."""
    radio = _capable_radio()
    radio.stop_cw_text = AsyncMock()
    handler = _control_handler(radio=radio)
    result = await handler._enqueue_command("stop_cw_text", {})
    assert result == {}
    radio.stop_cw_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_cw_text_no_radio_raises() -> None:
    """stop_cw_text raises when radio is not connected."""
    handler = _control_handler(radio=None)
    with pytest.raises(RuntimeError, match="radio connection not available"):
        await handler._enqueue_command("stop_cw_text", {})


@pytest.mark.asyncio
async def test_get_break_in_delay_returns_level() -> None:
    """get_break_in_delay reads from radio and returns level."""
    radio = _capable_radio()
    radio.get_break_in_delay = AsyncMock(return_value=128)
    handler = _control_handler(radio=radio)
    result = await handler._enqueue_command("get_break_in_delay", {})
    assert result == {"level": 128}
    radio.get_break_in_delay.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_break_in_delay_no_radio_raises() -> None:
    """get_break_in_delay raises when radio is not connected."""
    handler = _control_handler(radio=None)
    with pytest.raises(RuntimeError, match="radio connection not available"):
        await handler._enqueue_command("get_break_in_delay", {})


@pytest.mark.asyncio
async def test_get_dash_ratio_returns_value() -> None:
    """get_dash_ratio reads from radio and returns value."""
    radio = _capable_radio()
    radio.get_dash_ratio = AsyncMock(return_value=30)
    handler = _control_handler(radio=radio)
    result = await handler._enqueue_command("get_dash_ratio", {})
    assert result == {"value": 30}
    radio.get_dash_ratio.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_dash_ratio_no_radio_raises() -> None:
    """get_dash_ratio raises when radio is not connected."""
    handler = _control_handler(radio=None)
    with pytest.raises(RuntimeError, match="radio connection not available"):
        await handler._enqueue_command("get_dash_ratio", {})


@pytest.mark.asyncio
async def test_event_sender_loop_forwards_notifications_without_subscription() -> None:
    """Notifications are sent even when client has not subscribed to 'state'."""
    ws = SimpleNamespace(send_text=AsyncMock())
    handler = _control_handler(ws=ws)
    # Do NOT add "state" to subscribed_streams

    task = asyncio.create_task(handler._event_sender_loop())
    try:
        notification = {
            "type": "notification",
            "level": "success",
            "message": "Radio connected",
            "category": "connection",
        }
        await handler._event_queue.put(notification)
        await asyncio.sleep(0)
        assert ws.send_text.await_count == 1

        # Regular event should still be blocked when not subscribed
        await handler._event_queue.put({"type": "event", "name": "freq_changed"})
        await asyncio.sleep(0)
        assert ws.send_text.await_count == 1  # unchanged
    finally:
        task.cancel()
        await task
