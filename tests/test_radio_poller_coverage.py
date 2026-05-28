"""Additional coverage tests for rigplane.web.radio_poller."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.exceptions import CommandError
from rigplane.profiles import resolve_radio_profile
from rigplane.radio_state import RadioState
from rigplane.rigctld.state_cache import StateCache
from rigplane.web.radio_poller import (
    CommandQueue,
    DisableScope,
    EnableScope,
    PttOff,
    PttOn,
    QuickDwTrigger,
    QuickSplitTrigger,
    RadioPoller,
    SelectVfo,
    SendCiv,
    SetAgc,
    SetAttenuator,
    SetDataMode,
    SetDigiSel,
    SetFilterShape,
    SetFilterWidth,
    SetFreq,
    SetIpPlus,
    SetMode,
    SetNB,
    SetNR,
    SetPower,
    SetPreamp,
    SetScopeEdge,
    SetScopeRbw,
    SetScopeVbw,
    SwitchScopeReceiver,
    VfoEqualize,
    VfoSwap,
)


def _make_radio(active: str = "MAIN") -> MagicMock:
    profile = resolve_radio_profile(model="IC-7610")
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio._radio_state = SimpleNamespace(active=active)
    radio.send_civ = AsyncMock()
    radio.set_freq = AsyncMock()
    radio.set_mode = AsyncMock()
    radio.set_filter = AsyncMock()
    radio.set_filter_shape = AsyncMock()
    radio.set_ptt = AsyncMock()
    radio.set_rf_power = AsyncMock()
    radio.set_rf_gain = AsyncMock()
    radio.set_af_level = AsyncMock()
    radio.set_squelch = AsyncMock()
    radio.set_data_mode = AsyncMock()
    radio.set_nb = AsyncMock()
    radio.set_nr = AsyncMock()
    radio.set_digisel = AsyncMock()
    radio.set_ip_plus = AsyncMock()
    radio.send_cw_text = AsyncMock()
    radio.set_attenuator = AsyncMock()
    radio.set_attenuator_level = AsyncMock()
    radio.get_attenuator_level = AsyncMock(return_value=0)
    radio.set_preamp = AsyncMock()
    radio.get_preamp = AsyncMock(return_value=0)
    radio.set_agc = AsyncMock()
    radio.set_antenna_1 = AsyncMock()
    radio.set_antenna_2 = AsyncMock()
    radio.set_rx_antenna_ant1 = AsyncMock()
    radio.set_rx_antenna_ant2 = AsyncMock()
    radio.get_antenna_1 = AsyncMock(return_value=False)
    radio.get_antenna_2 = AsyncMock(return_value=False)
    radio.get_rx_antenna_ant1 = AsyncMock(return_value=False)
    radio.get_rx_antenna_ant2 = AsyncMock(return_value=False)
    radio.set_system_date = AsyncMock()
    radio.get_system_date = AsyncMock(return_value=(2026, 1, 1))
    radio.set_system_time = AsyncMock()
    radio.get_system_time = AsyncMock(return_value=(0, 0))
    radio.set_dual_watch = AsyncMock()
    radio.set_split = AsyncMock()
    radio.equalize_main_sub = AsyncMock()
    radio.swap_main_sub = AsyncMock()

    # Receiver-tier capabilities (issue #1170 / #1172).  ``select_receiver``
    # mirrors the wire-level CI-V the runtime would emit so existing
    # ``send_civ(0x07, [0xD0/0xD1])`` assertions still apply.
    async def _select_receiver(which: object) -> None:
        name = str(which).strip().upper()
        code = 0xD1 if name in ("SUB", "1") else 0xD0
        await radio.send_civ(0x07, sub=None, data=bytes([code]), wait_response=False)
        radio._radio_state.active = "SUB" if code == 0xD1 else "MAIN"

    radio.select_receiver = AsyncMock(side_effect=_select_receiver)
    radio.set_vfo_slot = AsyncMock()
    radio.get_dual_watch = AsyncMock(return_value=False)
    radio.set_tuner_status = AsyncMock()
    radio.get_tuner_status = AsyncMock(return_value=0)
    radio.set_acc1_mod_level = AsyncMock()
    radio.set_usb_mod_level = AsyncMock()
    radio.set_lan_mod_level = AsyncMock()
    radio.set_compressor = AsyncMock()
    # Canonical dual-RX VFO methods (radio_poller calls these directly post-#1113)
    # ``equalize_main_sub`` / ``swap_main_sub`` are already wired above for
    # QuickDwTrigger / QuickSplitTrigger composites.
    radio.enable_scope = AsyncMock()
    radio.disable_scope = AsyncMock()
    radio.on_scope_data = MagicMock()
    radio.capture_scope_frame = AsyncMock()
    radio.capture_scope_frames = AsyncMock()
    radio.set_scope_during_tx = AsyncMock()
    radio.set_scope_center_type = AsyncMock()
    radio.set_scope_edge = AsyncMock()
    radio.set_scope_fixed_edge = AsyncMock()
    radio.set_scope_vbw = AsyncMock()
    radio.set_scope_rbw = AsyncMock()
    # DSP toggles (needed for AdvancedControlCapable protocol)
    radio.get_auto_notch = AsyncMock(return_value=False)
    radio.set_auto_notch = AsyncMock()
    radio.get_manual_notch = AsyncMock(return_value=False)
    radio.set_manual_notch = AsyncMock()
    radio.get_cw_pitch = AsyncMock(return_value=600)
    radio.set_cw_pitch = AsyncMock()
    radio.get_dial_lock = AsyncMock(return_value=False)
    radio.set_dial_lock = AsyncMock()
    radio.get_anti_vox_gain = AsyncMock(return_value=0)
    radio.set_anti_vox_gain = AsyncMock()
    radio.get_monitor = AsyncMock(return_value=False)
    radio.set_monitor = AsyncMock()
    # Ensure ALL AdvancedControlCapable protocol methods are explicitly set as
    # instance attributes so isinstance() succeeds on Python 3.12+ where
    # __getattr__-based attribute access no longer satisfies runtime-checkable
    # protocol isinstance checks.
    from rigplane.radio_protocol import (
        AdvancedControlCapable as _ACC,
        ScopeCapable as _SC,
    )

    try:
        from typing import get_protocol_members as _gpm  # Python 3.13+

        _proto_attrs = _gpm(_ACC) | _gpm(_SC)
    except ImportError:
        import typing as _typing

        _proto_attrs = _typing._get_protocol_attrs(_ACC) | _typing._get_protocol_attrs(
            _SC
        )  # type: ignore[attr-defined]
    for _attr in _proto_attrs:
        if _attr not in vars(radio):
            setattr(radio, _attr, AsyncMock())
    return radio


@pytest.mark.asyncio
async def test_execute_set_data_mode_updates_sub_receiver_state_and_sends_wire_value() -> (
    None
):
    events: list[tuple[str, dict]] = []
    radio = _make_radio(active="MAIN")
    state = RadioState()
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=lambda name, data: events.append((name, data)),
        radio_state=state,
    )

    await poller._execute(SetDataMode(3, receiver=1))  # noqa: SLF001

    radio.set_data_mode.assert_awaited_once_with(3, receiver=1)
    assert state.main.data_mode == 0
    assert state.sub.data_mode == 3
    assert ("data_mode_changed", {"mode": 3, "receiver": 1}) in events


@pytest.mark.asyncio
async def test_command_queue_wait_and_drain_behavior() -> None:
    q = CommandQueue()
    await q.wait(timeout=0.001)
    assert q.has_commands is False

    q.put(SetPower(1))
    q.put(SetPower(2))
    q.put(PttOn())
    q.put(PttOff())
    cmds = q.drain()
    assert q.has_commands is False
    assert sum(isinstance(c, SetPower) for c in cmds) == 1
    assert sum(isinstance(c, (PttOn, PttOff)) for c in cmds) == 2


@pytest.mark.asyncio
async def test_command_queue_ordered_lane_preserves_repeated_commands() -> None:
    q = CommandQueue()
    q.put_ordered(SetFreq(14_030_000))
    q.put_ordered(SetMode("FM"))
    q.put_ordered(SetFreq(144_030_000))
    q.put_ordered(PttOn())
    q.put_ordered(PttOff())

    cmds = q.drain()

    assert cmds == [
        SetFreq(14_030_000),
        SetMode("FM"),
        SetFreq(144_030_000),
        PttOn(),
        PttOff(),
    ]


@pytest.mark.asyncio
async def test_radio_poller_executes_raw_civ_fire_and_forget() -> None:
    radio = _make_radio()
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    await poller._execute(  # noqa: SLF001
        SendCiv(command=0x1A, sub=0x05, data=b"\x01\x53\x01")
    )

    radio.send_civ.assert_awaited_once_with(
        0x1A,
        sub=0x05,
        data=b"\x01\x53\x01",
        wait_response=False,
    )


@pytest.mark.asyncio
async def test_radio_poller_rejects_raw_civ_without_backend_support() -> None:
    radio = SimpleNamespace(profile=resolve_radio_profile(model="FTX-1"))
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    with pytest.raises(CommandError, match="send_civ is not supported"):
        await poller._execute(SendCiv(command=0x1A, data=b"\x01"))  # noqa: SLF001


@pytest.mark.asyncio
async def test_command_queue_ordered_lane_preserves_segment_order() -> None:
    q = CommandQueue()
    q.put(SetFreq(7_000_000))
    q.put(SetFreq(7_074_000))
    q.put_ordered(SetFreq(144_030_000))
    q.put(SetFreq(14_000_000))
    q.put(SetFreq(14_074_000))

    assert q.drain() == [
        SetFreq(7_074_000),
        SetFreq(144_030_000),
        SetFreq(14_074_000),
    ]


@pytest.mark.asyncio
async def test_radio_poller_skips_ordered_command_with_cancelled_future() -> None:
    radio = _make_radio(active="MAIN")
    q = CommandQueue()
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    future.cancel()
    q.put_ordered(SetFreq(144_030_000), future=future)
    poller = RadioPoller(radio, StateCache(), q)

    poller.start()
    await asyncio.sleep(0.05)
    poller.stop()

    radio.set_freq.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_active_defaults_and_setfreq_setmode_branches() -> None:
    radio = _make_radio(active="MAIN")
    poller = RadioPoller(radio, StateCache(), CommandQueue())
    assert poller._current_active() == "MAIN"  # noqa: SLF001

    radio._radio_state.active = 7
    assert poller._current_active() == "MAIN"  # noqa: SLF001

    radio._radio_state.active = "MAIN"
    await poller._execute(SetFreq(14_074_000, receiver=1))  # noqa: SLF001
    assert radio.send_civ.await_count >= 2
    radio.set_freq.assert_awaited_once_with(14_074_000)

    radio2 = _make_radio(active="SUB")
    poller2 = RadioPoller(radio2, StateCache(), CommandQueue())
    await poller2._execute(SetFreq(7_074_000, receiver=0))  # noqa: SLF001
    assert radio2.send_civ.await_count >= 2
    radio2.set_freq.assert_awaited_once_with(7_074_000)

    await poller._execute(SetMode("USB", filter_width=2, receiver=1))  # noqa: SLF001
    radio.set_mode.assert_awaited_once_with("USB", 2)


@pytest.mark.asyncio
async def test_execute_event_emitting_commands_and_vfo_paths() -> None:
    events: list[tuple[str, dict]] = []
    radio = _make_radio(active="MAIN")
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=lambda name, data: events.append((name, data)),
    )

    await poller._execute(SetNB(True, receiver=0))  # noqa: SLF001
    await poller._execute(SetNR(False, receiver=1))  # noqa: SLF001
    await poller._execute(SetDigiSel(True, receiver=1))  # noqa: SLF001
    await poller._execute(SetIpPlus(False, receiver=0))  # noqa: SLF001
    assert any(name == "nb_changed" for name, _ in events)
    assert any(name == "nr_changed" for name, _ in events)
    assert any(name == "digisel_changed" for name, _ in events)
    assert any(name == "ipplus_changed" for name, _ in events)

    await poller._execute(SelectVfo("SUB"))  # noqa: SLF001
    assert radio._radio_state.active == "SUB"
    radio.send_civ.assert_any_await(
        0x07, sub=None, data=bytes([0xD1]), wait_response=False
    )
    # Scope follows the selected receiver (0x27 0x12 0x01 = SUB).
    radio.send_civ.assert_any_await(
        0x27, sub=0x12, data=bytes([0x01]), wait_response=False
    )
    await poller._execute(SelectVfo("MAIN"))  # noqa: SLF001
    assert radio._radio_state.active == "MAIN"
    radio.send_civ.assert_any_await(
        0x07, sub=None, data=bytes([0xD0]), wait_response=False
    )
    radio.send_civ.assert_any_await(
        0x27, sub=0x12, data=bytes([0x00]), wait_response=False
    )
    # Re-clicking the active receiver is a no-op CI-V-wise but still emits
    # the state event so UI listeners can refresh.
    civ_calls_before = radio.send_civ.await_count
    await poller._execute(SelectVfo("MAIN"))  # noqa: SLF001
    assert radio.send_civ.await_count == civ_calls_before
    assert any(name == "vfo_changed" for name, _ in events)

    await poller._execute(VfoSwap())  # noqa: SLF001
    assert any(name == "vfo_swapped" for name, _ in events)
    # #1114: poller calls canonical ``swap_main_sub`` directly; the
    # deprecated wrapper has been removed.
    radio.swap_main_sub.assert_awaited_once_with()

    # #1114: VfoEqualize routes to canonical ``equalize_main_sub``; the
    # deprecated wrapper has been removed.
    eq_before = radio.equalize_main_sub.await_count
    await poller._execute(VfoEqualize())  # noqa: SLF001
    assert radio.equalize_main_sub.await_count == eq_before + 1

    await poller._execute(EnableScope(policy="fast"))  # noqa: SLF001
    await poller._execute(DisableScope())  # noqa: SLF001
    await poller._execute(SwitchScopeReceiver(1))  # noqa: SLF001
    radio.enable_scope.assert_awaited_once_with(policy="fast")
    radio.disable_scope.assert_awaited_once()
    with pytest.raises(CommandError, match="receiver=2"):
        await poller._execute(SwitchScopeReceiver(2))  # noqa: SLF001


@pytest.mark.asyncio
async def test_select_vfo_legacy_backend_falls_back_to_set_vfo() -> None:
    """SelectVfo on backends predating ReceiverBankCapable falls back to set_vfo.

    Issue #1189: backends like ``SerialMockRadio`` only expose the legacy
    ``set_vfo`` overload.  The poller must not AttributeError on
    ``radio.select_receiver(...)`` — it must fall back to ``set_vfo`` so
    SUB selection still reaches the radio.  The DeprecationWarning from
    ``IcomRadio.set_vfo`` (#1187) is intentional — it signals migration.
    """
    events: list[tuple[str, dict]] = []
    radio = _make_radio(active="MAIN")
    # Strip the new methods so the legacy fallback is exercised.  Using
    # ``del`` rather than rebuilding via ``spec=`` keeps the rest of the
    # ``_make_radio`` wiring (caps, profile, _radio_state) intact.
    del radio.select_receiver
    del radio.set_vfo_slot
    radio.set_vfo = AsyncMock()

    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=lambda name, data: events.append((name, data)),
    )

    await poller._execute(SelectVfo("SUB"))  # noqa: SLF001

    radio.set_vfo.assert_awaited_once_with("SUB")
    assert any(name == "vfo_changed" for name, _ in events)


@pytest.mark.asyncio
async def test_select_vfo_no_capability_logs_and_skips() -> None:
    """SelectVfo on a backend with neither new methods nor set_vfo: skip cleanly."""
    radio = _make_radio(active="MAIN")
    del radio.select_receiver
    del radio.set_vfo_slot
    # ``MagicMock`` auto-creates ``set_vfo`` on access; ``del`` removes
    # it so ``getattr(radio, "set_vfo", None)`` returns ``None``.
    del radio.set_vfo

    poller = RadioPoller(radio, StateCache(), CommandQueue())

    # Must not raise; just no-op + warning log.
    await poller._execute(SelectVfo("SUB"))  # noqa: SLF001


@pytest.mark.asyncio
async def test_execute_receiver_routed_set_commands_use_backend_receiver_and_target_state() -> (
    None
):
    events: list[tuple[str, dict]] = []
    radio = _make_radio(active="MAIN")
    state = RadioState()
    state.main.nb = False
    state.sub.nb = False
    state.main.nr = True
    state.sub.nr = False
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=lambda name, data: events.append((name, data)),
        radio_state=state,
    )

    await poller._execute(SetNB(True, receiver=1))  # noqa: SLF001
    await poller._execute(SetNR(True, receiver=1))  # noqa: SLF001
    await poller._execute(SetDataMode(3, receiver=1))  # noqa: SLF001

    radio.set_nb.assert_awaited_once_with(True, receiver=1)
    radio.set_nr.assert_awaited_once_with(True, receiver=1)
    radio.set_data_mode.assert_awaited_once_with(3, receiver=1)
    assert state.main.nb is False
    assert state.sub.nb is True
    assert state.main.nr is True
    assert state.sub.nr is True
    assert state.main.data_mode == 0
    assert state.sub.data_mode == 3
    assert ("nb_changed", {"on": True, "receiver": 1}) in events
    assert ("nr_changed", {"on": True, "receiver": 1}) in events
    assert ("data_mode_changed", {"mode": 3, "receiver": 1}) in events


@pytest.mark.asyncio
async def test_execute_set_attenuator_updates_sub_receiver_state_and_radio_call() -> (
    None
):
    events: list[tuple[str, dict]] = []
    radio = _make_radio(active="MAIN")
    state = RadioState()
    state.main.preamp = 2
    state.sub.preamp = 1
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=lambda name, data: events.append((name, data)),
        radio_state=state,
    )

    await poller._execute(SetAttenuator(12, receiver=1))  # noqa: SLF001

    radio.set_attenuator_level.assert_awaited_once_with(12, receiver=1)
    assert state.main.att == 0
    assert state.main.preamp == 2
    assert state.sub.att == 12
    assert state.sub.preamp == 0
    assert ("attenuator_changed", {"db": 12, "receiver": 1}) in events


@pytest.mark.asyncio
async def test_execute_set_preamp_updates_sub_receiver_state_and_radio_call() -> None:
    events: list[tuple[str, dict]] = []
    radio = _make_radio(active="MAIN")
    state = RadioState()
    state.main.att = 9
    state.sub.att = 12
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=lambda name, data: events.append((name, data)),
        radio_state=state,
    )

    await poller._execute(SetPreamp(2, receiver=1))  # noqa: SLF001

    radio.set_preamp.assert_awaited_once_with(2, receiver=1)
    assert state.main.preamp == 0
    assert state.main.att == 9
    assert state.sub.preamp == 2
    assert state.sub.att == 0
    assert ("preamp_changed", {"level": 2, "receiver": 1}) in events


@pytest.mark.asyncio
async def test_execute_set_filter_width_dispatches_to_radio_protocol() -> None:
    """Issue #1101: poller delegates Hz→index encoding to radio.set_filter_width."""
    events: list[tuple[str, dict]] = []
    radio = _make_radio(active="MAIN")
    state = RadioState()
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=lambda name, data: events.append((name, data)),
        radio_state=state,
    )
    state.sub.mode = "USB"

    await poller._execute(SetFilterWidth(1500, receiver=1))  # noqa: SLF001

    # Layering: protocol method, not raw CI-V (P2-04).
    radio.set_filter_width.assert_awaited_once_with(1500, receiver=1)
    assert state.main.filter_width is None
    assert state.sub.filter_width == 1500
    assert ("filter_width_changed", {"width": 1500, "receiver": 1}) in events


@pytest.mark.asyncio
async def test_execute_set_filter_shape_updates_sub_receiver_state_and_radio_call() -> (
    None
):
    events: list[tuple[str, dict]] = []
    radio = _make_radio(active="MAIN")
    state = RadioState()
    state.main.filter_shape = 0
    state.sub.filter_shape = 0
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=lambda name, data: events.append((name, data)),
        radio_state=state,
    )

    await poller._execute(SetFilterShape(1, receiver=1))  # noqa: SLF001

    radio.set_filter_shape.assert_awaited_once_with(1, receiver=1)
    assert state.main.filter_shape == 0
    assert state.sub.filter_shape == 1
    assert ("filter_shape_changed", {"shape": 1, "receiver": 1}) in events


@pytest.mark.asyncio
async def test_execute_set_agc_updates_sub_receiver_state_and_radio_call() -> None:
    events: list[tuple[str, dict]] = []
    radio = _make_radio(active="MAIN")
    state = RadioState()
    state.main.agc = 1
    state.sub.agc = 1
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=lambda name, data: events.append((name, data)),
        radio_state=state,
    )

    await poller._execute(SetAgc(2, receiver=1))  # noqa: SLF001

    radio.set_agc.assert_awaited_once_with(2, receiver=1)
    assert state.main.agc == 1
    assert state.sub.agc == 2
    assert ("agc_changed", {"mode": 2, "receiver": 1}) in events


@pytest.mark.asyncio
async def test_send_query_even_and_odd_branch_variants() -> None:
    radio = _make_radio()
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    poller._poll_index = 0  # even => fast meter query  # noqa: SLF001
    await poller._send_query()  # noqa: SLF001
    assert radio.send_civ.await_args.args[0] == 0x15

    poller._STATE_QUERIES = [
        (0x25, None, 0x01)
    ]  # receiver in data payload  # noqa: SLF001
    poller._poll_index = 1  # odd  # noqa: SLF001
    await poller._send_query()  # noqa: SLF001
    assert radio.send_civ.await_args.args[0] == 0x25
    assert radio.send_civ.await_args.kwargs["data"] == bytes([0x01])

    poller._STATE_QUERIES = [(0x16, 0x22, 0x01)]  # cmd29 wrapper path  # noqa: SLF001
    poller._poll_index = 1  # noqa: SLF001
    await poller._send_query()  # noqa: SLF001
    assert radio.send_civ.await_args.args[0] == 0x29

    poller._STATE_QUERIES = [(0x0F, None, None)]  # global query  # noqa: SLF001
    poller._poll_index = 1  # noqa: SLF001
    await poller._send_query()  # noqa: SLF001
    assert radio.send_civ.await_args.args[0] == 0x0F


@pytest.mark.asyncio
async def test_run_backoff_and_query_error_paths() -> None:
    queue = CommandQueue()
    queue.put(SetPower(10))
    poller = RadioPoller(_make_radio(), StateCache(), queue)

    poller._execute = AsyncMock(side_effect=ConnectionError("down"))  # noqa: SLF001
    poller._send_query = AsyncMock(return_value=None)  # noqa: SLF001
    poller._initial_state_fetch = AsyncMock()  # noqa: SLF001  — skip to test backoff path
    poller._queue.wait = AsyncMock(side_effect=asyncio.CancelledError())  # noqa: SLF001
    with patch("rigplane.web.radio_poller.asyncio.sleep", new=AsyncMock()):
        await poller._run()  # noqa: SLF001
    assert poller._send_query.await_count >= 2  # restore probe + normal query

    poller2 = RadioPoller(_make_radio(), StateCache(), CommandQueue())
    poller2._send_query = AsyncMock(side_effect=RuntimeError("query failed"))  # noqa: SLF001
    poller2._queue.wait = AsyncMock(side_effect=asyncio.CancelledError())  # noqa: SLF001
    with patch("rigplane.web.radio_poller.asyncio.sleep", new=AsyncMock()):
        await poller2._run()  # noqa: SLF001


def test_start_stop_running_and_emit_helpers() -> None:
    radio = _make_radio()
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    with patch("asyncio.get_running_loop") as get_loop:
        task = MagicMock()
        task.done.return_value = False

        def create_task(coro, name=None):
            del name
            coro.close()
            return task

        get_loop.return_value.create_task.side_effect = create_task
        poller.start()
        assert poller.running is True
        poller.start()  # idempotent
        poller.stop()
        task.cancel.assert_called_once()
        assert poller.running is False

    events: list[tuple[str, dict]] = []
    poller2 = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=lambda name, data: events.append((name, data)),
    )
    poller2._emit("x", {"a": 1})  # noqa: SLF001
    assert events == [("x", {"a": 1})]


def test_state_queries_include_operator_toggle_reads_for_ic7610() -> None:
    poller = RadioPoller(_make_radio(), StateCache(), CommandQueue())

    assert {
        (0x15, 0x01, 0x00),
        (0x15, 0x01, 0x01),
        (0x15, 0x07, None),
        (0x16, 0x12, None),
        (0x16, 0x32, 0x00),
        (0x16, 0x32, 0x01),
        (0x16, 0x41, 0x00),
        (0x16, 0x41, 0x01),
        (0x16, 0x44, None),
        (0x16, 0x45, None),
        (0x16, 0x46, None),
        (0x16, 0x47, None),
        (0x16, 0x48, 0x00),
        (0x16, 0x48, 0x01),
        (0x16, 0x4F, 0x00),
        (0x16, 0x4F, 0x01),
        (0x16, 0x50, None),
        (0x16, 0x56, 0x00),
        (0x16, 0x56, 0x01),
        (0x16, 0x58, None),
        (0x1A, 0x04, 0x00),
        (0x1A, 0x04, 0x01),
    }.issubset(set(poller._STATE_QUERIES))  # noqa: SLF001


def test_state_queries_include_transceiver_status_reads_for_ic7610() -> None:
    poller = RadioPoller(_make_radio(), StateCache(), CommandQueue())

    assert {
        (0x1C, 0x01, None),
        (0x1C, 0x03, None),
        (0x21, 0x00, None),
        (0x21, 0x01, None),
        (0x21, 0x02, None),
    }.issubset(set(poller._STATE_QUERIES))  # noqa: SLF001


def test_fast_cmds_include_comp_meter_for_ic7610() -> None:
    poller = RadioPoller(_make_radio(), StateCache(), CommandQueue())

    assert (0x15, 0x14) in poller._FAST_CMDS  # noqa: SLF001


@pytest.mark.asyncio
async def test_execute_quick_dw_trigger_equalizes_then_enables_dw() -> None:
    """QuickDwTrigger: composite equalize_main_sub() then set_dual_watch(True).

    Order matters — DW must enable on a state that already matches MAIN.
    """
    events: list[tuple[str, dict]] = []
    radio = _make_radio(active="MAIN")
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=lambda name, data: events.append((name, data)),
    )

    await poller._execute(QuickDwTrigger())  # noqa: SLF001

    radio.equalize_main_sub.assert_awaited_once_with()
    radio.set_dual_watch.assert_awaited_once_with(True)
    # Event is fired so UI listeners can refresh.
    assert ("dual_watch_changed", {"on": True}) in events


@pytest.mark.asyncio
async def test_execute_quick_split_trigger_equalizes_then_enables_split() -> None:
    """QuickSplitTrigger: composite equalize_main_sub() then set_split(True).

    Also flips RadioState.split so the UI reflects the change immediately.
    """
    events: list[tuple[str, dict]] = []
    radio = _make_radio(active="MAIN")
    state = RadioState()
    assert state.split is False
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        on_state_event=lambda name, data: events.append((name, data)),
        radio_state=state,
    )

    await poller._execute(QuickSplitTrigger())  # noqa: SLF001

    radio.equalize_main_sub.assert_awaited_once_with()
    radio.set_split.assert_awaited_once_with(True)
    assert state.split is True
    assert ("split_changed", {"on": True}) in events


@pytest.mark.asyncio
async def test_execute_set_scope_edge_updates_state() -> None:
    radio = _make_radio()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)

    await poller._execute(SetScopeEdge(edge=3))  # noqa: SLF001

    radio.set_scope_edge.assert_awaited_once_with(3)
    assert state.scope_controls.edge == 3


@pytest.mark.asyncio
async def test_execute_set_scope_vbw_updates_state() -> None:
    radio = _make_radio()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)

    await poller._execute(SetScopeVbw(narrow=True))  # noqa: SLF001

    radio.set_scope_vbw.assert_awaited_once_with(True)
    assert state.scope_controls.vbw_narrow is True


@pytest.mark.asyncio
async def test_execute_set_scope_rbw_updates_state() -> None:
    radio = _make_radio()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)

    await poller._execute(SetScopeRbw(rbw=2))  # noqa: SLF001

    radio.set_scope_rbw.assert_awaited_once_with(2)
    assert state.scope_controls.rbw == 2


@pytest.mark.asyncio
async def test_enable_scope_deferred_during_initial_fetch() -> None:
    """EnableScope must be re-queued (not block) when initial fetch is in progress.

    Regression test for deadlock in commit 6d385f3: EnableScope.await inside
    drain loop blocked _initial_state_fetch, which was the caller.
    """
    radio = _make_radio()
    queue = CommandQueue()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), queue, radio_state=state)

    # Simulate initial fetch in progress
    poller._initial_fetch_done.clear()  # noqa: SLF001

    # Execute EnableScope — must NOT block, should re-queue
    await poller._execute(EnableScope(policy="fast"))  # noqa: SLF001

    # enable_scope should NOT have been called (deferred)
    radio.enable_scope.assert_not_awaited()

    # Command should be re-queued
    assert queue.has_commands is True
    cmds = queue.drain()
    assert any(isinstance(c, EnableScope) for c in cmds)


@pytest.mark.asyncio
async def test_enable_scope_executes_after_initial_fetch_done() -> None:
    """EnableScope executes normally when initial fetch is complete."""
    radio = _make_radio()
    queue = CommandQueue()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), queue, radio_state=state)

    # Initial fetch done (default state)
    assert poller._initial_fetch_done.is_set()  # noqa: SLF001

    await poller._execute(EnableScope(policy="fast"))  # noqa: SLF001

    radio.enable_scope.assert_awaited_once_with(policy="fast")


@pytest.mark.asyncio
async def test_set_freq_not_blocked_by_deferred_enable_scope() -> None:
    """SetFreq must execute during initial fetch even when EnableScope is deferred.

    This is the user-facing symptom of the deadlock: tuning stops working
    while initial fetch is in progress.
    """
    radio = _make_radio()
    queue = CommandQueue()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), queue, radio_state=state)

    poller._initial_fetch_done.clear()  # noqa: SLF001

    # Defer EnableScope
    await poller._execute(EnableScope(policy="fast"))  # noqa: SLF001
    radio.enable_scope.assert_not_awaited()

    # SetFreq must still work (receiver=0 uses positional call without keyword)
    await poller._execute(SetFreq(freq=14_074_000, receiver=0))  # noqa: SLF001
    radio.set_freq.assert_awaited_once_with(14_074_000)


@pytest.mark.asyncio
async def test_command_error_propagates_from_execute() -> None:
    """CommandError propagates from _execute so the drain loop can catch it.

    The poller's drain loop wraps _execute in try/except, so errors don't
    kill the loop. This test verifies the error propagation contract.
    """
    radio = _make_radio()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)

    radio.set_freq.side_effect = CommandError("timeout")
    with pytest.raises(CommandError, match="timeout"):
        await poller._execute(SetFreq(freq=14_074_000, receiver=0))  # noqa: SLF001

    # After error, next command still works (simulates drain loop continuing)
    radio.set_freq.side_effect = None
    radio.set_freq.reset_mock()
    await poller._execute(SetFreq(freq=7_074_000, receiver=0))  # noqa: SLF001
    radio.set_freq.assert_awaited_once_with(7_074_000)


@pytest.mark.asyncio
async def test_multiple_commands_execute_in_order_after_fetch() -> None:
    """Multiple commands enqueued during initial fetch all execute after fetch completes."""
    radio = _make_radio()
    queue = CommandQueue()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), queue, radio_state=state)

    poller._initial_fetch_done.clear()  # noqa: SLF001

    # EnableScope is deferred
    await poller._execute(EnableScope(policy="fast"))  # noqa: SLF001

    # But other commands execute immediately
    await poller._execute(SetFreq(freq=14_074_000, receiver=0))  # noqa: SLF001
    await poller._execute(SetMode(mode="USB", receiver=0))  # noqa: SLF001

    radio.set_freq.assert_awaited_once()
    radio.set_mode.assert_awaited_once()

    # Now simulate fetch completing
    poller._initial_fetch_done.set()  # noqa: SLF001

    # Drain the re-queued EnableScope
    cmds = queue.drain()
    for cmd in cmds:
        await poller._execute(cmd)  # noqa: SLF001

    radio.enable_scope.assert_awaited_once()


def test_state_queries_include_scope_vbw_rbw_edge_for_ic7610() -> None:
    poller = RadioPoller(_make_radio(), StateCache(), CommandQueue())

    queries = set(poller._STATE_QUERIES)  # noqa: SLF001
    assert (0x27, 0x16, None) in queries  # edge number
    assert (0x27, 0x19, None) in queries  # REF level
    assert (0x27, 0x1B, None) in queries  # during TX
    assert (0x27, 0x1C, None) in queries  # center type
    assert (0x27, 0x1D, None) in queries  # VBW
    assert (0x27, 0x1F, None) in queries  # RBW


# ---------------------------------------------------------------------------
# _adaptive_gap tests
# ---------------------------------------------------------------------------


class TestAdaptiveGap:
    """Tests for RadioPoller._adaptive_gap backpressure method."""

    def _make_poller(self, pressure: float) -> RadioPoller:
        radio = _make_radio()
        radio.queue_pressure = pressure
        poller = RadioPoller(radio, StateCache(), CommandQueue())
        return poller

    def test_returns_base_gap_at_zero_pressure(self) -> None:
        poller = self._make_poller(0.0)
        base = poller._gap  # noqa: SLF001
        assert poller._adaptive_gap() == base  # noqa: SLF001

    def test_returns_base_gap_below_half(self) -> None:
        poller = self._make_poller(0.4)
        base = poller._gap  # noqa: SLF001
        assert poller._adaptive_gap() == base  # noqa: SLF001

    def test_interpolates_at_mid_pressure(self) -> None:
        poller = self._make_poller(0.6)
        base = poller._gap  # noqa: SLF001
        result = poller._adaptive_gap()  # noqa: SLF001
        assert result == pytest.approx(base * 1.5)

    def test_returns_double_gap_above_threshold(self) -> None:
        poller = self._make_poller(0.8)
        base = poller._gap  # noqa: SLF001
        assert poller._adaptive_gap() == base * 2.0  # noqa: SLF001

    def test_returns_double_gap_at_full_pressure(self) -> None:
        poller = self._make_poller(1.0)
        base = poller._gap  # noqa: SLF001
        assert poller._adaptive_gap() == base * 2.0  # noqa: SLF001


# Issue #937 — two-tier meter polling tests.


@pytest.mark.asyncio
async def test_high_tier_emits_s_meter_on_rx_for_consecutive_cycles() -> None:
    radio = _make_radio()
    poller = RadioPoller(radio, StateCache(), CommandQueue())
    poller._radio_state = SimpleNamespace(ptt=False)  # noqa: SLF001
    # _poll_index ∈ {2,4,6,8} → high_idx ∈ {1,2,3,4} (none multiple of 5).
    for poll_idx in (2, 4, 6, 8):
        radio.send_civ.reset_mock()
        poller._poll_index = poll_idx  # noqa: SLF001
        await poller._send_query()  # noqa: SLF001
        args = radio.send_civ.await_args.args
        kwargs = radio.send_civ.await_args.kwargs
        assert (args[0], kwargs.get("sub")) == (0x15, 0x02)


@pytest.mark.asyncio
async def test_high_tier_rotates_pwr_swr_alc_on_tx() -> None:
    radio = _make_radio()
    poller = RadioPoller(radio, StateCache(), CommandQueue())
    poller._radio_state = SimpleNamespace(ptt=True)  # noqa: SLF001
    emissions: set[tuple[int, int | None]] = set()
    for poll_idx in (2, 4, 6, 8, 10, 12):
        radio.send_civ.reset_mock()
        poller._poll_index = poll_idx  # noqa: SLF001
        await poller._send_query()  # noqa: SLF001
        args = radio.send_civ.await_args.args
        kwargs = radio.send_civ.await_args.kwargs
        emissions.add((args[0], kwargs.get("sub")))
    assert emissions == {(0x15, 0x11), (0x15, 0x12), (0x15, 0x13)}
    assert (0x15, 0x02) not in emissions


@pytest.mark.asyncio
async def test_low_tier_emits_at_expected_stride_for_lan() -> None:
    radio = _make_radio()
    poller = RadioPoller(radio, StateCache(), CommandQueue())
    poller._radio_state = SimpleNamespace(ptt=False)  # noqa: SLF001
    expected = [(0x15, 0x14), (0x15, 0x15), (0x15, 0x16)]
    for poll_idx, exp in zip((0, 10, 20), expected, strict=True):
        radio.send_civ.reset_mock()
        poller._poll_index = poll_idx  # noqa: SLF001
        await poller._send_query()  # noqa: SLF001
        args = radio.send_civ.await_args.args
        kwargs = radio.send_civ.await_args.kwargs
        assert (args[0], kwargs.get("sub")) == exp


def test_low_tier_contains_comp_vd_id_for_ic7610() -> None:
    assert set(RadioPoller._LOW_TIER) == {  # noqa: SLF001
        (0x15, 0x14),
        (0x15, 0x15),
        (0x15, 0x16),
    }
    assert RadioPoller._LOW_STRIDE == 5  # noqa: SLF001


@pytest.mark.asyncio
async def test_poll_index_monotonic_across_ptt_toggle() -> None:
    radio = _make_radio()
    poller = RadioPoller(radio, StateCache(), CommandQueue())
    state = SimpleNamespace(ptt=False)
    poller._radio_state = state  # noqa: SLF001
    for _ in range(4):
        await poller._send_query()  # noqa: SLF001
    state.ptt = True
    for _ in range(4):
        await poller._send_query()  # noqa: SLF001
    assert poller._poll_index == 8  # noqa: SLF001


@pytest.mark.asyncio
async def test_serial_backend_unchanged_on_ptt_toggle() -> None:
    radio = _make_radio()
    poller = RadioPoller(radio, StateCache(), CommandQueue())
    poller._is_serial = True  # noqa: SLF001
    poller._FAST_CMDS = list(RadioPoller._FAST_CMDS_SERIAL)  # noqa: SLF001
    state = SimpleNamespace(ptt=False)
    poller._radio_state = state  # noqa: SLF001

    serial_set = set(RadioPoller._FAST_CMDS_SERIAL)  # noqa: SLF001
    rx_emissions: dict[int, tuple[int, int | None]] = {}
    for poll_idx in (0, 2, 4, 6):
        radio.send_civ.reset_mock()
        poller._poll_index = poll_idx  # noqa: SLF001
        await poller._send_query()  # noqa: SLF001
        args = radio.send_civ.await_args.args
        kwargs = radio.send_civ.await_args.kwargs
        emission = (args[0], kwargs.get("sub"))
        assert emission in serial_set
        rx_emissions[poll_idx] = emission

    state.ptt = True
    for poll_idx in (0, 2, 4, 6):
        radio.send_civ.reset_mock()
        poller._poll_index = poll_idx  # noqa: SLF001
        await poller._send_query()  # noqa: SLF001
        args = radio.send_civ.await_args.args
        kwargs = radio.send_civ.await_args.kwargs
        emission = (args[0], kwargs.get("sub"))
        assert emission in serial_set
        # PTT state must not change which command is emitted at given index.
        assert emission == rx_emissions[poll_idx]


# ----------------------------------------------------------------------
# SetPower unit-tag (#1168)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setpower_icom_poller_accepts_raw_255() -> None:
    """Default SetPower(unit='raw_255') flows to radio.set_rf_power on Icom."""
    radio = _make_radio()
    poller = RadioPoller(radio, StateCache(), CommandQueue())
    await poller._execute(SetPower(level=128))  # noqa: SLF001
    radio.set_rf_power.assert_awaited_once_with(128)


@pytest.mark.asyncio
async def test_setpower_icom_poller_rejects_watts_unit() -> None:
    """Icom poller raises ValueError on unit='watts' and never calls set_rf_power."""
    radio = _make_radio()
    poller = RadioPoller(radio, StateCache(), CommandQueue())
    with pytest.raises(ValueError, match="raw_255"):
        await poller._execute(SetPower(level=50, unit="watts"))  # noqa: SLF001
    radio.set_rf_power.assert_not_awaited()


# ----------------------------------------------------------------------
# Scope poller: bounded latency on dropped responses (#1181)
# ----------------------------------------------------------------------


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_fetch_scope_controls_bounds_latency_on_dropped_response() -> None:
    """A getter that never resolves must not stall _fetch_scope_controls.

    Regression test for #1181: PR #1178 replaced fire-and-forget 0x27 sends
    with awaited get_scope_*() calls. A single dropped response could block
    the EnableScope hot path and the poller's command-queue drain for the
    full CI-V GET timeout (up to 2 s), and 12 misses compounded to ~24 s.
    Without the bounded ``_SCOPE_GETTER_TIMEOUT``, this test would hang.
    """
    radio = _make_radio()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)

    # Make every scope getter "hang" (await an event that is never set).
    never = asyncio.Event()

    async def _hang() -> None:
        await never.wait()

    for name in (
        "get_scope_receiver",
        "get_scope_dual",
        "get_scope_during_tx",
        "get_scope_center_type",
        "get_scope_mode",
        "get_scope_span",
        "get_scope_edge",
        "get_scope_hold",
        "get_scope_ref",
        "get_scope_speed",
        "get_scope_vbw",
        "get_scope_rbw",
    ):
        setattr(radio, name, AsyncMock(side_effect=_hang))

    # Tighten the timeout for the test so we don't wait 12 * 0.2 s = 2.4 s.
    poller._SCOPE_GETTER_TIMEOUT = 0.02  # noqa: SLF001

    start = asyncio.get_event_loop().time()
    await poller._fetch_scope_controls()  # noqa: SLF001
    elapsed = asyncio.get_event_loop().time() - start

    # 12 getters * (0.02 s timeout + ~0 s gap) ≈ 0.24 s.  Allow generous
    # slack so the test is not flaky on slow CI; the important property
    # is that we are NOT blocked for 12 * 2.0 s = 24 s.
    assert elapsed < 2.0, f"poller stalled for {elapsed:.2f}s on dropped responses"

    # Every getter was attempted exactly once even though they all hung.
    radio.get_scope_receiver.assert_awaited_once()
    radio.get_scope_rbw.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_scope_controls_normal_path_still_works() -> None:
    """Normal scope-control fetch path: every getter is awaited exactly once."""
    radio = _make_radio()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)

    await poller._fetch_scope_controls()  # noqa: SLF001

    # Each getter awaited once on the happy path.
    for name in (
        "get_scope_receiver",
        "get_scope_dual",
        "get_scope_during_tx",
        "get_scope_center_type",
        "get_scope_mode",
        "get_scope_span",
        "get_scope_edge",
        "get_scope_hold",
        "get_scope_ref",
        "get_scope_speed",
        "get_scope_vbw",
        "get_scope_rbw",
    ):
        getter = getattr(radio, name)
        getter.assert_awaited_once()


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_fetch_scope_controls_repeated_timeouts_do_not_accumulate() -> None:
    """Consecutive _fetch_scope_controls calls stay bounded across drops.

    If cancellation leaked tracker entries we would expect the per-call
    cost to grow.  We assert that the cost of N calls scales linearly
    with N (no accumulation between calls).
    """
    radio = _make_radio()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)

    never = asyncio.Event()

    async def _hang() -> None:
        await never.wait()

    for name in (
        "get_scope_receiver",
        "get_scope_dual",
        "get_scope_during_tx",
        "get_scope_center_type",
        "get_scope_mode",
        "get_scope_span",
        "get_scope_edge",
        "get_scope_hold",
        "get_scope_ref",
        "get_scope_speed",
        "get_scope_vbw",
        "get_scope_rbw",
    ):
        setattr(radio, name, AsyncMock(side_effect=_hang))

    poller._SCOPE_GETTER_TIMEOUT = 0.01  # noqa: SLF001

    loop = asyncio.get_event_loop()
    start = loop.time()
    for _ in range(3):
        await poller._fetch_scope_controls()  # noqa: SLF001
    elapsed = loop.time() - start

    # 3 calls * 12 getters * 0.01 s = 0.36 s nominal.  Generous upper
    # bound so the test is robust on slow CI but still rejects the
    # 3 * 24 s = 72 s blowup.
    assert elapsed < 3.0, f"3 successive calls took {elapsed:.2f}s — accumulated"

    # Each getter was attempted exactly 3 times (no early exit).
    assert radio.get_scope_receiver.await_count == 3
    assert radio.get_scope_rbw.await_count == 3
