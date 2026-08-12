"""Additional coverage tests for rigplane.web.radio_poller."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from rigplane.commands.commander import IcomCommander, Priority
from rigplane.core.capabilities import CAP_SCOPE
from rigplane.core.acquisition_scheduler import (
    AcquisitionScheduler,
    MeterObservationCoalescer,
)
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    AdaptiveDecayPolicy,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.radio_protocol import RelativeVfoState
from rigplane.core.state_store import StateStore
from rigplane.core.types import CivFrame
from rigplane.core.command_service import (
    CommandExecutionResult,
    CommandService,
    command_intent_from_request,
)
from rigplane.core.exceptions import TimeoutError as RigplaneTimeoutError
from rigplane.core.state_pipeline_contracts import CommandIntent
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
    SetAfLevel,
    SetAgc,
    SetAttenuator,
    SetData1ModInput,
    SetDataMode,
    SetDigiSel,
    SetFilterShape,
    SetFilterWidth,
    SetFreq,
    SetIpPlus,
    SetMode,
    SetNB,
    SetNR,
    SetPbtInner,
    SetPbtOuter,
    SetPower,
    SetPreamp,
    SetRfGain,
    SetScopeEdge,
    SetScopeRbw,
    SetScopeRef,
    SetScopeSpan,
    SetScopeSpeed,
    SetScopeVbw,
    SetSplit,
    SetSquelch,
    SwitchScopeReceiver,
    VfoEqualize,
    VfoSwap,
)
from rigplane.core.tx_safety import (
    BACKEND_MAX_KEY_DOWN_SECONDS,
    TxOutcome,
    TxOwner,
    TxSource,
)
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.web_startup import stop_web_server

# MOR-1181 asserts on ordered ``radio.calls`` against a REAL supervisor, so it
# reuses MOR-1013's harness rather than re-mocking either of them.
from test_web_managed_tx_owner import _KEY, _TEARDOWN, _poller, _Radio, _Supervisor


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", (True, False), ids=("stale", "current"))
async def test_direct_getter_uses_generation_captured_before_await(stale: bool) -> None:
    store = StateStore()
    generation = store.begin_provider_generation()
    started, release = asyncio.Event(), asyncio.Event()
    radio = _make_radio(model="IC-7300")

    async def delayed_getter() -> int:
        started.set()
        await release.wait()
        return 2

    radio.get_data1_mod_input = delayed_getter
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    task = asyncio.create_task(poller._read_mod_input("data1_mod_input"))  # noqa: SLF001
    await started.wait()
    if stale:
        store.begin_provider_generation()
    release.set()
    await task

    if stale:
        assert "global.slow_state.data1_mod_input" not in store.snapshot().as_dict()
    else:
        field = store.snapshot().field("global.slow_state.data1_mod_input")
        assert (field.value, field.provider_generation) == (2, generation)


class _NoopCommandExecutor:
    async def execute(self, intent: object) -> CommandExecutionResult:
        del intent
        return CommandExecutionResult()


class _QueuedAckExecutor:
    def __init__(self, queue: CommandQueue) -> None:
        self.queue = queue
        self.command_service: CommandService | None = None

    async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
        assert self.command_service is not None
        if intent.name != "set_freq":
            raise AssertionError(f"unexpected intent {intent.name!r}")
        self.queue.put_ordered(
            SetFreq(
                int(intent.params["freq_hz"]),
                receiver=int(intent.params.get("receiver", 0)),
            ),
            command_id=intent.id,
            source=intent.source,
            session_id=None
            if intent.params.get("session_id") is None
            else str(intent.params["session_id"]),
            command_service=self.command_service,
        )
        return CommandExecutionResult(details={"queued": True})


class _InjectedAcquisitionExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[object, frozenset[FieldPath]]] = []

    async def execute(
        self,
        request: object,
        *,
        already_sent_paths: frozenset[FieldPath],
    ) -> object:
        self.calls.append((request, already_sent_paths))
        return SimpleNamespace(
            sent_paths=tuple(getattr(request, "paths")),
            failed_paths=(),
            failure_reason="",
        )


def _make_radio(active: str = "MAIN", *, model: str = "IC-7610") -> MagicMock:
    profile = resolve_radio_profile(model=model)
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio._radio_state = SimpleNamespace(active=active)
    # No managed TX runtime: this double stands in for an unmanaged provider,
    # so the legacy ``set_ptt`` path stays in charge. ``None`` reads unmanaged
    # on every interpreter; a bare Mock does not, because runtime-checkable
    # protocols use hasattr on 3.11 and getattr_static on 3.12+ (gh-102433).
    # Full note: the ``mock_radio`` fixture in tests/test_web_server.py.
    radio.managed_tx = None
    radio.send_civ = AsyncMock()
    radio.set_freq = AsyncMock()
    radio.set_mode = AsyncMock()
    radio.set_filter = AsyncMock()
    radio.set_filter_shape = AsyncMock()
    radio.set_ptt = AsyncMock()
    # The neutral AudioTransport surface the ``PttOn``/``PttOff`` arms drive
    # (MOR-543). Left as bare ``MagicMock`` attributes these are not awaitable,
    # which the arm read as a failed TX-audio arm — harmless while that failure
    # was swallowed, and a refused key once it stopped being (MOR-1178).
    radio.start_tx = AsyncMock()
    radio.stop_tx = AsyncMock()
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
    radio.set_pbt_inner = AsyncMock()
    radio.set_pbt_outer = AsyncMock()
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
    radio._set_vfo_slot_confirmed = AsyncMock()
    radio.read_relative_vfo = AsyncMock(
        side_effect=(
            RelativeVfoState(14_200_000, "USB", 1, 0),
            RelativeVfoState(7_100_000, "LSB", 2, 0),
        )
    )
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
    radio.get_scope_session_state = AsyncMock(return_value=(False, False))
    radio.restore_scope_session_state = AsyncMock()
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


def _acquisition_profile(
    *paths: FieldPath,
    policy: AcquisitionPolicy | None = None,
    provider: str = "icom_civ",
) -> RadioAcquisitionProfile:
    acquisition_policy = policy or AcquisitionPolicy(
        cadence_seconds=1.0,
        freshness_ttl_seconds=4.0,
    )
    return RadioAcquisitionProfile(
        provider=provider,
        capabilities=tuple(FieldCapability(path=path, polling=True) for path in paths),
        default_policy=acquisition_policy,
    )


@pytest.mark.asyncio
async def test_execute_set_data_mode_sends_wire_value_and_emits_event() -> None:
    # data_mode is observation-backed (0x1A 0x06); the legacy RadioState mirror
    # was removed (MOR-437). The poller must still send the wire value and emit
    # the change event, but it must not write the RadioState mirror.
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
    assert state.sub.data_mode == 0  # no legacy mirror write
    assert ("data_mode_changed", {"mode": 3, "receiver": 1}) in events


@pytest.mark.asyncio
async def test_scheduler_due_request_sends_supported_civ_query_once() -> None:
    radio = _make_radio(active="MAIN")
    path = FieldPath.receiver("main", "meters", "s_meter")
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path))
    radio._acquisition_scheduler = scheduler
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._send_query()  # noqa: SLF001
    await poller._send_query()  # noqa: SLF001

    radio.send_civ.assert_awaited_once_with(
        0x29,
        sub=None,
        data=b"\x00\x15\x02",
        wait_response=False,
        priority=Priority.BACKGROUND,
        wait_dispatch=False,
    )
    assert scheduler.pending_requests()[0].paths == (path,)


@pytest.mark.asyncio
async def test_x6200_scheduler_due_request_sends_civ_query_from_profile() -> None:
    radio = _make_radio(active="MAIN")
    profile = resolve_radio_profile(model="X6200")
    assert profile.state_acquisition is not None
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    scheduler = AcquisitionScheduler(profile=profile.state_acquisition)
    radio._acquisition_scheduler = scheduler
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._send_query()  # noqa: SLF001

    assert radio.send_civ.await_count == 4
    radio.send_civ.assert_any_await(
        0x25,
        sub=None,
        data=b"\x00",
        wait_response=False,
        priority=Priority.BACKGROUND,
        wait_dispatch=False,
    )
    radio.send_civ.assert_any_await(
        0x26,
        sub=None,
        data=b"\x00",
        wait_response=False,
        priority=Priority.BACKGROUND,
        wait_dispatch=False,
    )
    radio.send_civ.assert_any_await(
        0x15,
        sub=0x02,
        data=b"",
        wait_response=False,
        priority=Priority.BACKGROUND,
        wait_dispatch=False,
    )
    radio.send_civ.assert_any_await(
        0x15,
        sub=0x11,
        data=b"",
        wait_response=False,
        priority=Priority.BACKGROUND,
        wait_dispatch=False,
    )
    assert scheduler.pending_requests()


@pytest.mark.asyncio
async def test_xiegu_civ_scheduler_due_request_uses_civ_executor() -> None:
    radio = _make_radio(active="MAIN")
    path = FieldPath.active("main", "freq_mode", "mode")
    scheduler = AcquisitionScheduler(
        profile=_acquisition_profile(path, provider="xiegu_civ")
    )
    radio._acquisition_scheduler = scheduler
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._send_query()  # noqa: SLF001

    radio.send_civ.assert_awaited_once_with(
        0x26,
        sub=None,
        data=b"\x00",
        wait_response=False,
        priority=Priority.BACKGROUND,
        wait_dispatch=False,
    )


@pytest.mark.asyncio
async def test_ic7300_profile_scheduler_emits_only_passive_exact_wire_reads() -> None:
    """IC-7300 joins the existing poller lane without cmd29 or another service."""

    radio = _make_radio(active="MAIN", model="IC-7300")
    profile = resolve_radio_profile(model="IC-7300")
    assert profile.state_acquisition is not None
    scheduler = AcquisitionScheduler(profile=profile.state_acquisition)
    radio._acquisition_scheduler = scheduler
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    assert poller._acquisition_scheduler is scheduler  # noqa: SLF001
    await poller._send_query()  # noqa: SLF001

    radio.send_civ.assert_any_await(
        0x25,
        sub=None,
        data=b"\x00",
        wait_response=False,
        priority=Priority.BACKGROUND,
        wait_dispatch=False,
    )
    radio.send_civ.assert_any_await(
        0x25,
        sub=None,
        data=b"\x01",
        wait_response=False,
        priority=Priority.BACKGROUND,
        wait_dispatch=False,
    )
    radio.send_civ.assert_any_await(
        0x26,
        sub=None,
        data=b"\x00",
        wait_response=False,
        priority=Priority.BACKGROUND,
        wait_dispatch=False,
    )
    radio.send_civ.assert_any_await(
        0x26,
        sub=None,
        data=b"\x01",
        wait_response=False,
        priority=Priority.BACKGROUND,
        wait_dispatch=False,
    )
    for command, sub in ((0x14, 0x0A), (0x16, 0x44), (0x14, 0x0E)):
        radio.send_civ.assert_any_await(
            command,
            sub=sub,
            data=b"",
            wait_response=False,
            priority=Priority.BACKGROUND,
            wait_dispatch=False,
        )
    assert all(call_.args[0] != 0x29 for call_ in radio.send_civ.await_args_list)
    assert scheduler.pending_requests()


@pytest.mark.asyncio
async def test_scheduler_due_request_timeout_is_terminal_not_resent_each_tick() -> None:
    radio = _make_radio(active="MAIN")
    path = FieldPath.receiver("main", "meters", "s_meter")
    policy = AcquisitionPolicy(cadence_seconds=1.0, freshness_ttl_seconds=1.0)
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path, policy=policy))
    radio._acquisition_scheduler = scheduler
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    with patch(
        "rigplane.web.radio_poller.time.monotonic",
        side_effect=(100.0, 101.1, 101.2),
    ):
        await poller._send_query()  # noqa: SLF001
        await poller._send_query()  # noqa: SLF001
        await poller._send_query()  # noqa: SLF001

    radio.send_civ.assert_awaited_once()
    assert scheduler.pending_requests() == ()
    diagnostics = scheduler.diagnostics()
    assert diagnostics["failedRequestCount"] == 1
    assert diagnostics["failureCountByReason"]["acquisition_request_timeout"] == 1


def _healthy_radio(active: str = "MAIN", *, last_civ: float) -> MagicMock:
    # MOR-874: a radio whose CI-V link reads as healthy (recent data, not
    # recovering) for the poller's _civ_link_healthy gate.
    radio = _make_radio(active=active)
    radio._civ_recovering = False
    radio._last_civ_data_received = last_civ
    radio._civ_ready_idle_timeout = 2.0
    return radio


@pytest.mark.asyncio
async def test_sent_request_deadline_is_send_relative_not_enqueue_relative() -> None:
    # MOR-874 fix 1: a request that sat queued and sent late must be judged
    # from its SEND time, not enqueue time. With send_at far past
    # enqueue + max_age, the request must NOT yet be expired while still
    # inside its send-relative window.
    radio = _make_radio(active="MAIN")
    path = FieldPath.receiver("main", "meters", "s_meter")
    policy = AcquisitionPolicy(cadence_seconds=1.0, freshness_ttl_seconds=1.0)
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path, policy=policy))
    radio._acquisition_scheduler = scheduler
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())
    request = scheduler.due_requests(now=100.0)[0]

    # Enqueue-relative deadline is enqueue(100.0) + max_age(1.0) = 101.0, but
    # the request was actually SENT at 200.0; at 200.5 (0.5 s after send) it is
    # still well within the 1.0 s window and must not be expired.
    sent_relative = poller._acquisition_request_expired(  # noqa: SLF001
        request,
        sent_at=200.0,
        now=200.5,
    )
    assert sent_relative is False
    # Past the send-relative window (200.0 + 1.0) it does expire.
    assert (
        poller._acquisition_request_expired(  # noqa: SLF001
            request,
            sent_at=200.0,
            now=201.1,
        )
        is True
    )
    # A never-sent request (sent_at == 0) still uses the enqueue deadline.
    assert (
        poller._acquisition_request_expired(  # noqa: SLF001
            request,
            sent_at=0.0,
            now=101.5,
        )
        is True
    )


@pytest.mark.asyncio
async def test_credited_in_flight_request_is_cleared_and_does_not_expire() -> None:
    # MOR-874 fix 2: once the returning observation credits the request
    # (record_acquisition_result removes it from pending), the next send cycle
    # clears the in-flight entry — it never expires as a timeout.
    radio = _healthy_radio(last_civ=500.0)
    path = FieldPath.receiver("main", "meters", "s_meter")
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path))
    radio._acquisition_scheduler = scheduler
    executor = _InjectedAcquisitionExecutor()
    poller = RadioPoller(
        radio,
        CommandQueue(),
        radio_state=RadioState(),
        acquisition_executor=executor,
    )

    with patch("rigplane.web.radio_poller.time.monotonic", return_value=500.0):
        await poller._send_scheduler_requests()  # noqa: SLF001
    assert len(poller._acquisition_in_flight) == 1  # noqa: SLF001
    request = scheduler.pending_requests()[0]

    # Returning observation credits the request (removes it from pending).
    from rigplane.core.state_pipeline_contracts import ChangeSet, SourceMetadata

    scheduler.record_acquisition_result(
        request,
        ChangeSet(
            revision=1,
            freshness_revision=1,
            observation_seq=1,
            changes=(),
            timestamp_monotonic=500.0,
            sources=(SourceMetadata(source="poll_response", provider="icom_civ"),),
        ),
    )

    # Next cycle clears the in-flight entry; no timeout failure is recorded.
    with patch("rigplane.web.radio_poller.time.monotonic", return_value=500.1):
        await poller._send_scheduler_requests()  # noqa: SLF001
    assert poller._acquisition_in_flight == {}  # noqa: SLF001
    assert scheduler.diagnostics()["failedRequestCount"] == 0


@pytest.mark.asyncio
async def test_healthy_link_false_timeout_does_not_decay_freq_mode_cadence() -> None:
    # MOR-874 fix 3 (integration): under a healthy CI-V link, a deadline that
    # fires before the answer lands must NOT be counted as a timeout and must
    # keep freq_mode cadence at base (no decay toward the 30 s ceiling). The
    # request stays in flight for the late answer to credit.
    path = FieldPath.active("main", "freq_mode", "freq_hz")
    policy = AcquisitionPolicy(
        cadence_seconds=2.0,
        freshness_ttl_seconds=2.0,
        adaptive_decay=AdaptiveDecayPolicy(
            enabled=True,
            idle_multiplier=2.0,
            max_cadence_seconds=30.0,
        ),
    )
    radio = _healthy_radio(last_civ=700.0)
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path, policy=policy))
    radio._acquisition_scheduler = scheduler
    executor = _InjectedAcquisitionExecutor()
    poller = RadioPoller(
        radio,
        CommandQueue(),
        radio_state=RadioState(),
        acquisition_executor=executor,
    )

    # Cycle 1 sends the request. Cycle 2 advances 3 s — past the 2 s
    # send-relative window so the deadline fires — but the CI-V link stays
    # healthy (last-civ kept within the 2 s readiness window of the advancing
    # clock): a WSJT-X-style load with the radio answering fast but the deadline
    # racing. The expiry is suppressed (still inside the bounded grace window),
    # then the slightly-late answer arrives and CREDITS the request — the true
    # happy path the grace exists to protect.
    from rigplane.core.state_pipeline_contracts import (
        ChangeSet,
        FieldChange,
        SourceMetadata,
    )

    clock = {"t": 700.0}

    def _now() -> float:
        return clock["t"]

    with patch("rigplane.web.radio_poller.time.monotonic", side_effect=_now):
        # Cycle 1: send.
        radio._last_civ_data_received = clock["t"] - 0.1
        await poller._send_scheduler_requests()  # noqa: SLF001
        request = scheduler.pending_requests()[0]
        clock["t"] += 3.0

        # Cycle 2: deadline fires while healthy → suppressed within grace, no
        # re-send (executor still called exactly once).
        radio._last_civ_data_received = clock["t"] - 0.1
        await poller._send_scheduler_requests()  # noqa: SLF001
        assert len(executor.calls) == 1

        # The slightly-late answer lands and credits the request (still well
        # inside the 6 s grace window). It carries a value change, so cadence
        # resets to base — proving the credit path ran and the suppressed
        # false timeout never advanced/decayed cadence.
        scheduler.record_acquisition_result(
            request,
            ChangeSet(
                revision=1,
                freshness_revision=1,
                observation_seq=1,
                changes=(
                    FieldChange(path=path, previous=14_000_000, current=14_074_000),
                ),
                timestamp_monotonic=clock["t"],
                sources=(SourceMetadata(source="poll_response", provider="icom_civ"),),
            ),
        )
        clock["t"] += 0.1

        # Next cycle clears the in-flight + grace bookkeeping.
        radio._last_civ_data_received = clock["t"] - 0.1
        await poller._send_scheduler_requests()  # noqa: SLF001

    assert poller._acquisition_in_flight == {}  # noqa: SLF001
    assert poller._acquisition_healthy_grace_started == {}  # noqa: SLF001
    diagnostics = scheduler.diagnostics()
    # No false timeouts counted, cadence pinned at base 2.0 s (no decay).
    assert "acquisition_request_timeout" not in diagnostics["failureCountByReason"]
    assert diagnostics["failedRequestCount"] == 0
    assert diagnostics["cadenceByPath"][str(path)]["currentCadenceSeconds"] == 2.0


@pytest.mark.asyncio
async def test_healthy_link_uncredited_request_is_resent_and_eventually_fails() -> None:
    # MOR-874 regression (BLOCKING fix): the health gate reads the GLOBAL
    # last-CI-V timestamp, so under external-CAT load it reads healthy
    # ~permanently. A request whose SPECIFIC answer is genuinely lost (UDP drop
    # / radio silently ignores the command) must NOT be pinned in flight
    # forever. The bounded grace window
    # (_ACQUISITION_HEALTHY_GRACE_SECONDS) caps the false-timeout suppression:
    # once it elapses with the request still uncredited, the request is treated
    # as a REAL timeout — dropped so the scheduler re-queues/re-sends it, with
    # normal failure accounting. This proves recovery (executor called > 1) and
    # that the loss is eventually accounted as a real failure (not pinned).
    from rigplane.web.radio_poller import _ACQUISITION_HEALTHY_GRACE_SECONDS

    path = FieldPath.active("main", "freq_mode", "freq_hz")
    policy = AcquisitionPolicy(
        cadence_seconds=2.0,
        freshness_ttl_seconds=2.0,
        adaptive_decay=AdaptiveDecayPolicy(
            enabled=True,
            idle_multiplier=2.0,
            max_cadence_seconds=30.0,
        ),
    )
    radio = _healthy_radio(last_civ=900.0)
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path, policy=policy))
    radio._acquisition_scheduler = scheduler
    executor = _InjectedAcquisitionExecutor()
    poller = RadioPoller(
        radio,
        CommandQueue(),
        radio_state=RadioState(),
        acquisition_executor=executor,
    )

    # The answer is NEVER credited. The link reads healthy every cycle. The
    # clock advances past the grace window each lap, so each in-flight request
    # eventually exceeds grace, falls back to a real timeout, is dropped, and
    # the scheduler re-queues + re-sends it on a later cycle.
    clock = {"t": 900.0}

    def _now() -> float:
        return clock["t"]

    # Step well past the grace window per cycle so the bound is provably
    # crossed within a small, bounded number of cycles.
    step = _ACQUISITION_HEALTHY_GRACE_SECONDS + 2.0

    with patch("rigplane.web.radio_poller.time.monotonic", side_effect=_now):
        for _ in range(6):
            radio._last_civ_data_received = clock["t"] - 0.1
            await poller._send_scheduler_requests()  # noqa: SLF001
            clock["t"] += step

    # Recovery proven: the request was re-sent (executor called more than once).
    assert len(executor.calls) > 1
    # Eventually accounted as a real failure — NOT pinned forever.
    diagnostics = scheduler.diagnostics()
    assert diagnostics["failedRequestCount"] >= 1
    assert (
        diagnostics["failureCountByReason"].get("acquisition_request_timeout", 0) >= 1
    )
    # Grace bookkeeping for dropped requests does not leak.
    assert all(
        rid in {r.id for r in scheduler.pending_requests()}
        for rid in poller._acquisition_healthy_grace_started  # noqa: SLF001
    )


@pytest.mark.asyncio
async def test_scheduler_request_execution_uses_injected_executor_not_web_mapping() -> (
    None
):
    radio = _make_radio(active="MAIN")
    path = FieldPath.global_("slow_state", "value")
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path))
    radio._acquisition_scheduler = scheduler
    executor = _InjectedAcquisitionExecutor()
    poller = RadioPoller(
        radio,
        CommandQueue(),
        radio_state=RadioState(),
        acquisition_executor=executor,
    )

    await poller._send_query()  # noqa: SLF001

    radio.send_civ.assert_not_awaited()
    assert len(executor.calls) == 1
    request, already_sent_paths = executor.calls[0]
    assert getattr(request, "paths") == (path,)
    assert already_sent_paths == frozenset()
    assert scheduler.pending_requests()[0].paths == (path,)


@pytest.mark.asyncio
async def test_non_icom_scheduler_without_executor_fails_instead_of_web_civ_send() -> (
    None
):
    radio = _make_radio(active="MAIN")
    path = FieldPath.receiver("main", "meters", "s_meter")
    scheduler = AcquisitionScheduler(
        profile=RadioAcquisitionProfile(
            provider="test_provider",
            capabilities=(FieldCapability(path=path, polling=True),),
            default_policy=AcquisitionPolicy(
                cadence_seconds=1.0,
                freshness_ttl_seconds=4.0,
            ),
        )
    )
    radio._acquisition_scheduler = scheduler
    diagnostics = []
    poller = RadioPoller(
        radio,
        CommandQueue(),
        radio_state=RadioState(),
        diagnostics=SimpleNamespace(
            record=lambda *args, **kwargs: diagnostics.append((args, kwargs))
        ),  # type: ignore[arg-type]
    )

    await poller._send_query()  # noqa: SLF001

    radio.send_civ.assert_not_awaited()
    assert scheduler.pending_requests() == ()
    assert (
        scheduler.diagnostics()["failureCountByReason"]["acquisition_executor_missing"]
        == 1
    )
    assert any(
        args[:2] == ("acquisition_executor_missing", "web.radio_poller")
        and kwargs["provider"] == "test_provider"
        for args, kwargs in diagnostics
    )


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("path", "expected_cmd", "expected_receiver"),
    [
        (FieldPath.active("main", "freq_mode", "freq_hz"), 0x25, 0),
        (FieldPath.active("main", "freq_mode", "mode"), 0x26, 0),
        (FieldPath.active("sub", "freq_mode", "freq_hz"), 0x25, 1),
        (FieldPath.active("sub", "freq_mode", "mode"), 0x26, 1),
    ],
)
@pytest.mark.asyncio
async def test_scheduler_active_freq_mode_requests_use_receiver_payload(
    path: FieldPath,
    expected_cmd: int,
    expected_receiver: int,
) -> None:
    radio = _make_radio(active="MAIN")
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path))
    radio._acquisition_scheduler = scheduler
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._send_query()  # noqa: SLF001
    await poller._send_query()  # noqa: SLF001

    radio.send_civ.assert_awaited_once_with(
        expected_cmd,
        sub=None,
        data=bytes([expected_receiver]),
        wait_response=False,
        priority=Priority.BACKGROUND,
        wait_dispatch=False,
    )
    assert scheduler.pending_requests()[0].paths == (path,)


@pytest.mark.asyncio
async def test_scheduler_unknown_query_mapping_is_recorded_and_failed() -> None:
    radio = _make_radio(active="MAIN")
    path = FieldPath.global_("slow_state", "overflow")
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path))
    radio._acquisition_scheduler = scheduler
    diagnostics = []
    poller = RadioPoller(
        radio,
        CommandQueue(),
        radio_state=RadioState(),
        diagnostics=SimpleNamespace(
            record=lambda *args, **kwargs: diagnostics.append((args, kwargs))
        ),  # type: ignore[arg-type]
    )

    await poller._send_query()  # noqa: SLF001
    await poller._send_query()  # noqa: SLF001

    radio.send_civ.assert_not_awaited()
    assert scheduler.pending_requests() == ()
    assert scheduler.diagnostics()["failureCountByReason"]["no_civ_query_mapping"] == 1
    assert any(
        args[:2] == ("acquisition_request_failed", "web.radio_poller")
        and kwargs["request_id"] == "acq-1"
        and kwargs["reason"] == "no_civ_query_mapping"
        for args, kwargs in diagnostics
    )


@pytest.mark.asyncio
async def test_scheduler_ptt_request_sends_civ_ptt_query() -> None:
    radio = _make_radio(active="MAIN")
    path = FieldPath.global_("tx_state", "ptt")
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path))
    radio._acquisition_scheduler = scheduler
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._send_query()  # noqa: SLF001

    radio.send_civ.assert_awaited_once_with(
        0x1C,
        sub=0x00,
        data=b"",
        wait_response=False,
        priority=Priority.BACKGROUND,
        wait_dispatch=False,
    )
    assert scheduler.pending_requests()[0].paths == (path,)


@pytest.mark.asyncio
async def test_poller_falls_back_to_legacy_query_without_scheduler() -> None:
    radio = _make_radio(active="MAIN")
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._send_query()  # noqa: SLF001

    assert radio.send_civ.await_count == 1


@pytest.mark.asyncio
async def test_poller_flushes_due_meter_coalescer_on_query_tick() -> None:
    radio = _make_radio(active="MAIN")
    radio._meter_observation_coalescer = MeterObservationCoalescer()
    radio._civ_runtime = SimpleNamespace(flush_due_meter_observations=MagicMock())
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._send_query()  # noqa: SLF001

    radio._civ_runtime.flush_due_meter_observations.assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_polling_does_not_starve_user_command_queue() -> None:
    order: list[str] = []
    radio = _make_radio(active="MAIN")
    radio.set_freq = AsyncMock(side_effect=lambda *args, **kwargs: order.append("cmd"))

    async def _send_civ(*args: object, **kwargs: object) -> None:
        order.append("poll")

    radio.send_civ = AsyncMock(side_effect=_send_civ)
    path = FieldPath.receiver("main", "meters", "s_meter")
    radio._acquisition_scheduler = AcquisitionScheduler(
        profile=_acquisition_profile(path)
    )
    queue = CommandQueue()
    queue.put_ordered(SetFreq(14_074_000))
    poller = RadioPoller(radio, queue, radio_state=RadioState())

    async def _stop_after_first_wait(*args: object, **kwargs: object) -> None:
        raise asyncio.CancelledError

    queue.wait = _stop_after_first_wait  # type: ignore[method-assign]

    await poller._run()  # noqa: SLF001

    assert order[:2] == ["cmd", "poll"]
    assert radio.send_civ.await_count == 1


@pytest.mark.asyncio
async def test_run_does_not_raw_poll_unselected_slot_when_scheduler_attached() -> None:
    radio = _make_radio(active="MAIN")
    path = FieldPath.receiver("main", "meters", "s_meter")
    radio._acquisition_scheduler = AcquisitionScheduler(
        profile=_acquisition_profile(path)
    )
    queue = CommandQueue()
    poller = RadioPoller(radio, queue, radio_state=RadioState())
    poller._send_query = AsyncMock(return_value=None)  # noqa: SLF001
    poller._poll_unselected_slot = AsyncMock(return_value=None)  # noqa: SLF001
    poller._unselected_slot_gate = MagicMock(return_value=True)  # noqa: SLF001

    async def _stop_after_first_wait(*args: object, **kwargs: object) -> None:
        raise asyncio.CancelledError

    queue.wait = _stop_after_first_wait  # type: ignore[method-assign]

    await poller._run()  # noqa: SLF001

    poller._unselected_slot_gate.assert_not_called()  # type: ignore[attr-defined]
    poller._poll_unselected_slot.assert_not_awaited()  # type: ignore[attr-defined]


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


def test_command_queue_entries_preserve_command_correlation_metadata() -> None:
    q = CommandQueue()
    q.put(
        SetFreq(14_030_000),
        command_id="ws-freq",
        source="websocket",
        session_id="ws-a",
    )
    q.put_ordered(SetMode("USB"), command_id="http-mode", source="http")

    entries = q.drain_entries()

    assert entries[0].command == SetFreq(14_030_000)
    assert entries[0].command_id == "ws-freq"
    assert entries[0].source == "websocket"
    assert entries[0].session_id == "ws-a"
    assert entries[1].command == SetMode("USB")
    assert entries[1].command_id == "http-mode"
    assert entries[1].source == "http"
    assert entries[1].session_id is None


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
    # User-command path stays at NORMAL priority (not de-prioritized) and
    # blocking (wait_dispatch=True, never fire-and-forget).
    radio.send_civ.assert_any_await(
        0x27,
        sub=0x12,
        data=bytes([0x01]),
        wait_response=False,
        priority=Priority.NORMAL,
        wait_dispatch=True,
    )
    await poller._execute(SelectVfo("MAIN"))  # noqa: SLF001
    assert radio._radio_state.active == "MAIN"
    radio.send_civ.assert_any_await(
        0x07, sub=None, data=bytes([0xD0]), wait_response=False
    )
    radio.send_civ.assert_any_await(
        0x27,
        sub=0x12,
        data=bytes([0x00]),
        wait_response=False,
        priority=Priority.NORMAL,
        wait_dispatch=True,
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
    radio.restore_scope_session_state.assert_awaited_once_with((False, False))
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
async def test_single_receiver_vfo_b_selects_slot_without_sub_receiver() -> None:
    state = RadioState()
    state.active = "MAIN"
    state.main.active_slot = "A"
    radio = _make_radio(model="IC-7300")
    radio._radio_state = state
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)

    await poller._execute(SelectVfo("B"))  # noqa: SLF001

    radio._set_vfo_slot_confirmed.assert_awaited_once_with("B", receiver=0)
    radio.set_vfo_slot.assert_not_awaited()
    radio.select_receiver.assert_not_awaited()
    assert state.active == "MAIN"
    assert state.main.active_slot == "B"


@pytest.mark.asyncio
async def test_relative_vfo_ack_maps_selected_and_complement_then_rebinds() -> None:
    state = RadioState()
    state.active = "MAIN"
    radio = _make_radio(model="IC-7300")
    radio._radio_state = state
    radio.read_relative_vfo.side_effect = (
        RelativeVfoState(14_200_000, "USB", 1, 0),
        RelativeVfoState(7_100_000, "LSB", 2, 0),
        RelativeVfoState(7_200_000, "LSB", 2, 0),
        RelativeVfoState(14_250_000, "USB", 1, 0),
    )
    store = StateStore()
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), radio_state=state, state_store=store)

    assert "receiver.0.vfo.active_slot" not in store.snapshot().as_dict()
    await poller._execute(SelectVfo("B"))  # noqa: SLF001
    first = store.snapshot()
    assert first.field("receiver.0.vfo.active_slot").value == "B"
    assert first.field("receiver.0.slot.B.freq_mode.freq_hz").value == 14_200_000
    assert first.field("receiver.0.slot.A.freq_mode.freq_hz").value == 7_100_000

    await poller._execute(SelectVfo("A"))  # noqa: SLF001
    second = store.snapshot()
    assert second.field("receiver.0.vfo.active_slot").value == "A"
    assert second.field("receiver.0.slot.A.freq_mode.freq_hz").value == 7_200_000
    assert second.field("receiver.0.slot.B.freq_mode.freq_hz").value == 14_250_000
    assert radio._set_vfo_slot_confirmed.await_args_list == [
        call("B", receiver=0),
        call("A", receiver=0),
    ]
    assert radio.set_vfo_slot.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    (None, CommandError("stale select failed")),
    ids=("success", "exception"),
)
async def test_stale_select_vfo_preserves_replacement_generation_identity(
    failure: CommandError | None,
) -> None:
    store = StateStore()
    store.begin_provider_generation()
    state = RadioState()
    state.main.active_slot = "A"
    events: list[tuple[str, dict[str, Any]]] = []
    entered, release = asyncio.Event(), asyncio.Event()
    radio = _make_radio(model="IC-7300")
    radio._radio_state = state

    async def delayed_select(*_args: Any, **_kwargs: Any) -> None:
        entered.set()
        await release.wait()
        if failure is not None:
            raise failure

    radio._set_vfo_slot_confirmed = AsyncMock(side_effect=delayed_select)
    poller = RadioPoller(
        radio,
        CommandQueue(),
        radio_state=state,
        state_store=store,
        on_state_event=lambda name, data: events.append((name, data)),
    )

    task = asyncio.create_task(poller._execute(SelectVfo("B")))  # noqa: SLF001
    await entered.wait()
    replacement_generation = store.begin_provider_generation()
    expected = {
        FieldPath.active_slot("0"): "A",
        FieldPath.vfo_slot("0", "A", "freq_mode", "freq_hz"): 7_100_000,
    }
    for path, value in expected.items():
        store.apply(
            Observation(
                path=path,
                value=value,
                source=SourceMetadata(source="test", provider="replacement"),
                timestamp_monotonic=time.monotonic(),
                provider_generation=replacement_generation,
            )
        )
    release.set()
    if failure is None:
        await task
    else:
        with pytest.raises(CommandError, match="stale select failed"):
            await task

    snapshot = store.snapshot()
    assert {path: snapshot.field(path).value for path in expected} == expected
    assert state.main.active_slot == "A"
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    (
        CommandError("NAK"),
        RigplaneTimeoutError("select timeout"),
        asyncio.CancelledError(),
    ),
)
async def test_relative_vfo_failed_select_leaves_identity_unknown(
    failure: BaseException,
) -> None:
    radio = _make_radio(model="IC-7300")
    radio._radio_state = RadioState()
    radio._set_vfo_slot_confirmed.side_effect = failure
    store = StateStore()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    with pytest.raises(type(failure)):
        await poller._execute(SelectVfo("B"))  # noqa: SLF001

    assert "receiver.0.vfo.active_slot" not in store.snapshot().as_dict()
    radio.read_relative_vfo.assert_not_awaited()
    assert radio._relative_vfo_observations_suspended is False


@pytest.mark.asyncio
async def test_relative_vfo_readback_failure_retains_ack_identity_only() -> None:
    radio = _make_radio(model="IC-7300")
    radio._radio_state = RadioState()
    radio.read_relative_vfo.side_effect = RigplaneTimeoutError("read timeout")
    store = StateStore()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    with pytest.raises(RigplaneTimeoutError):
        await poller._execute(SelectVfo("B"))  # noqa: SLF001

    snapshot = store.snapshot()
    assert snapshot.field("receiver.0.vfo.active_slot").value == "B"
    assert "receiver.0.slot.B.freq_mode.freq_hz" not in snapshot.as_dict()


@pytest.mark.asyncio
async def test_relative_vfo_epoch_reset_discards_vfo_facts_but_not_ptt() -> None:
    radio = _make_radio(model="IC-7300")
    radio._radio_state = RadioState()
    store = StateStore()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    store.apply(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
            source=SourceMetadata(source="test", provider="test"),
            timestamp_monotonic=time.monotonic(),
        )
    )
    await poller._execute(SelectVfo("B"))  # noqa: SLF001

    poller.reset_vfo_session()

    fields = store.snapshot().as_dict()
    assert "receiver.0.vfo.active_slot" not in fields
    assert "receiver.0.active.freq_mode.freq_hz" not in fields
    assert "receiver.0.unselected.freq_mode.freq_hz" not in fields
    assert fields["global.tx_state.ptt"]["value"] is False


@pytest.mark.parametrize(
    ("model", "expected_seconds"),
    (("IC-7300", 8.0), ("IC-705", 11.1)),
)
def test_relative_vfo_retention_window_follows_provider_poll_cadence(
    model: str,
    expected_seconds: float,
) -> None:
    radio = _make_radio(model=model)
    radio._civ_ready_idle_timeout = 5.0

    poller = RadioPoller(radio, CommandQueue())

    acquisition = radio.profile.state_acquisition
    expected_rotation = (
        acquisition.default_policy.cadence_seconds
        if acquisition is not None
        else 2 * len(poller._STATE_QUERIES) * poller._fast_interval
    )
    assert expected_rotation is not None
    assert poller._relative_vfo_retention_max_age == pytest.approx(
        2 * expected_rotation + 5.0
    )
    assert poller._relative_vfo_retention_max_age == pytest.approx(expected_seconds)


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
    # nb/nr/data_mode are observation-backed (MOR-437): the poller routes the
    # wire command to the correct receiver and emits the change event, but it
    # no longer writes the legacy RadioState mirror. The pre-seeded values are
    # therefore left untouched.
    assert state.main.nb is False
    assert state.sub.nb is False
    assert state.main.nr is True
    assert state.sub.nr is False
    assert state.main.data_mode == 0
    assert state.sub.data_mode == 0
    assert ("nb_changed", {"on": True, "receiver": 1}) in events
    assert ("nr_changed", {"on": True, "receiver": 1}) in events
    assert ("data_mode_changed", {"mode": 3, "receiver": 1}) in events


@pytest.mark.asyncio
async def test_execute_set_attenuator_sends_wire_value_without_legacy_mirror() -> None:
    # att is observation-backed (0x11); the legacy RadioState mirror (and its
    # preamp/att mutual-exclusion side effect) was removed (MOR-437). The poller
    # routes the wire command to the correct receiver and emits the event but
    # leaves RadioState untouched — confirmation comes from the readback.
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
    assert state.sub.att == 0  # no legacy mirror write
    assert state.sub.preamp == 1  # preamp side effect no longer applied
    assert ("attenuator_changed", {"db": 12, "receiver": 1}) in events


@pytest.mark.asyncio
async def test_execute_set_preamp_sends_wire_value_without_legacy_mirror() -> None:
    # preamp is observation-backed (0x16 0x02); the legacy RadioState mirror
    # (and its att/preamp mutual-exclusion side effect) was removed (MOR-437).
    # The poller routes the wire command and emits the event but leaves
    # RadioState untouched.
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
    assert state.sub.preamp == 0  # no legacy mirror write
    assert state.sub.att == 12  # att side effect no longer applied
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
    # filter_width is observation-backed (0x1A 0x03); the legacy RadioState
    # mirror was removed (MOR-437). The poller emits the change event but does
    # not write the mirror — both slots stay at their default None.
    assert state.main.filter_width is None
    assert state.sub.filter_width is None
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
async def test_execute_set_agc_sends_wire_value_without_legacy_mirror() -> None:
    # agc is observation-backed (0x16 0x12); the legacy RadioState mirror was
    # removed (MOR-437). The poller routes the wire command and emits the event
    # but leaves the pre-seeded RadioState values untouched.
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
    assert state.sub.agc == 1  # no legacy mirror write
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


@pytest.mark.asyncio
async def test_run_backs_off_on_bare_timeout_when_radio_reports_disconnected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-1440: a dead serial link surfaces as a bare exception (the CI-V
    transport recovery-wait gate raises ``TimeoutError``, not
    ``ConnectionError``) once the radio's own state machine already knows
    the link is down. The poller must back off instead of silently
    retrying a doomed wire every cycle (previously logged at DEBUG only).
    """
    radio = _make_radio()
    radio.connected = False
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    call_count = {"n": 0}

    async def _send_query_side_effect(*_args: object, **_kwargs: object) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise TimeoutError("CI-V transport recovery timed out")
        # Stop the loop after observing the backoff branch once — a plain
        # CancelledError is a BaseException, so it isn't swallowed by the
        # generic ``except Exception`` the backoff-retry probe also runs.
        raise asyncio.CancelledError()

    poller._send_query = AsyncMock(side_effect=_send_query_side_effect)  # noqa: SLF001
    poller._queue.wait = AsyncMock(side_effect=asyncio.CancelledError())  # noqa: SLF001
    with (
        patch("rigplane.web.radio_poller.asyncio.sleep", new=AsyncMock()),
        caplog.at_level(logging.INFO, logger="rigplane.web.radio_poller"),
    ):
        await poller._run()  # noqa: SLF001

    backoff_lines = [
        r for r in caplog.records if "radio disconnected, backing off" in r.getMessage()
    ]
    assert backoff_lines, "expected a backoff log line when radio.connected is False"


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
        (0x16, 0x12, 0x00),
        (0x16, 0x12, 0x01),
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
async def test_execute_set_scope_span_updates_state_and_reconfirms() -> None:
    """MOR-1446: a span write must re-GET so the StateStore observation for
    ``scope_controls.span`` refreshes — otherwise the stale pre-write
    observation (last confirmed at ``EnableScope`` time) keeps overwriting
    the fresh optimistic value on every subsequent state snapshot, and the
    frontend readout desyncs from the radio's real span (MOR-1446 leg 1)."""
    radio = _make_radio()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)

    await poller._execute(SetScopeSpan(span=6))  # noqa: SLF001

    radio.set_scope_span.assert_awaited_once_with(6)
    assert state.scope_controls.span == 6
    radio.get_scope_span.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_execute_set_scope_speed_updates_state_and_reconfirms() -> None:
    """MOR-1446 leg 3: SPEED reads as inert without the reconfirm — the
    dispatch reaches the radio, but the readout never advances past its
    pre-write reading."""
    radio = _make_radio()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)

    await poller._execute(SetScopeSpeed(speed=2))  # noqa: SLF001

    radio.set_scope_speed.assert_awaited_once_with(2)
    assert state.scope_controls.speed == 2
    radio.get_scope_speed.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_execute_set_scope_ref_updates_state_and_reconfirms() -> None:
    """MOR-1446 leg 2: REF stays stuck at 0 without the reconfirm — the radio
    applies the level (waterfall visibly changes) but the readout keeps
    replaying the stale pre-write observation."""
    radio = _make_radio()
    state = RadioState()
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)

    await poller._execute(SetScopeRef(ref=5))  # noqa: SLF001

    radio.set_scope_ref.assert_awaited_once_with(5)
    assert state.scope_controls.ref_db == 5.0
    radio.get_scope_ref.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_execute_set_scope_span_reconfirm_timeout_does_not_raise() -> None:
    """A dropped confirm response (busy scope stream) must not fail the
    command — `_reconfirm_scope_field` bounds and swallows it exactly like
    `_fetch_scope_controls` already does for the same class of getter."""
    radio = _make_radio()
    state = RadioState()

    async def _never_resolves() -> int:
        await asyncio.sleep(10)
        return 0

    radio.get_scope_span = _never_resolves
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=state)

    await poller._execute(SetScopeSpan(span=6))  # noqa: SLF001

    radio.set_scope_span.assert_awaited_once_with(6)
    assert state.scope_controls.span == 6


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
async def test_stale_deferred_enable_cannot_requeue_after_newer_disable() -> None:
    radio = _make_radio()
    queue = CommandQueue()
    poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())
    poller._initial_fetch_done.clear()  # noqa: SLF001

    queue.put(EnableScope(policy="fast", generation=1))
    (old_enable,) = queue.drain()
    await poller._execute(old_enable)  # noqa: SLF001
    assert queue.has_commands is True

    queue.put(DisableScope(generation=2))
    for command in queue.drain():
        await poller._execute(command)  # noqa: SLF001

    radio.enable_scope.assert_not_awaited()
    radio.restore_scope_session_state.assert_not_awaited()
    assert queue.has_commands is False


@pytest.mark.asyncio
async def test_stale_disable_is_inert_after_newer_enable_demand() -> None:
    radio = _make_radio()
    queue = CommandQueue()
    poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

    queue.put(DisableScope(generation=3))
    (old_disable,) = queue.drain()
    queue.put(EnableScope(policy="fast", generation=4))

    await poller._execute(old_disable)  # noqa: SLF001
    for command in queue.drain():
        await poller._execute(command)  # noqa: SLF001

    radio.disable_scope.assert_not_awaited()
    radio.enable_scope.assert_awaited_once_with(policy="fast")


@pytest.mark.asyncio
async def test_current_scope_generation_preserves_recovery_enable() -> None:
    radio = _make_radio()
    queue = CommandQueue()
    poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())

    await poller._execute(EnableScope(generation=5))  # noqa: SLF001
    await poller._execute(EnableScope(generation=5))  # noqa: SLF001

    assert radio.enable_scope.await_count == 2
    radio.get_scope_session_state.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [(True, False), (False, False)])
async def test_scope_session_restores_exact_initial_state(
    initial: tuple[bool, bool],
) -> None:
    radio = _make_radio()
    radio.get_scope_session_state.return_value = initial
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=RadioState())

    await poller._execute(EnableScope(generation=1))  # noqa: SLF001
    await poller._execute(EnableScope(generation=1))  # noqa: SLF001
    await poller._execute(DisableScope(generation=2))  # noqa: SLF001

    radio.get_scope_session_state.assert_awaited_once()
    assert radio.enable_scope.await_count == 2
    radio.restore_scope_session_state.assert_awaited_once_with(initial)


@pytest.mark.asyncio
async def test_failed_scope_enable_rolls_back_captured_state() -> None:
    radio = _make_radio()
    radio.enable_scope.side_effect = ConnectionError("scope unavailable at low baud")
    poller = RadioPoller(radio, StateCache(), CommandQueue(), radio_state=RadioState())

    with pytest.raises(ConnectionError, match="low baud"):
        await poller._execute(EnableScope(generation=1))  # noqa: SLF001
    await poller._execute(DisableScope(generation=2))  # noqa: SLF001

    radio.restore_scope_session_state.assert_awaited_once_with((False, False))


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
async def test_set_freq_success_does_not_apply_confirmed_state_store_observation() -> (
    None
):
    radio = _make_radio()
    state = RadioState()
    store = StateStore()
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        radio_state=state,
        state_store=store,
    )

    await poller._execute(SetFreq(freq=14_074_000, receiver=0))  # noqa: SLF001

    with pytest.raises(KeyError):
        store.snapshot().field("receiver.0.freq_mode.freq_hz")
    assert state.main.freq == 14_074_000


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("command", "expected_path"),
    [
        (SetMode(mode="USB", receiver=0), "receiver.0.freq_mode.mode"),
        (PttOn(), "global.tx_state.ptt"),
        (PttOff(), "global.tx_state.ptt"),
        (SetSplit(on=True), "global.tx_state.split"),
        (SelectVfo(vfo="SUB"), "receiver.0.vfo.active_slot"),
    ],
)
@pytest.mark.asyncio
async def test_release_critical_web_setters_do_not_confirm_state_without_readback(
    command: object,
    expected_path: str,
) -> None:
    radio = _make_radio()
    store = StateStore()
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        radio_state=RadioState(),
        state_store=store,
    )

    await poller._execute(command)  # noqa: SLF001

    with pytest.raises(KeyError):
        store.snapshot().field(expected_path)


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("command", "expected_path"),
    [
        (SetRfGain(level=120, receiver=1), "receiver.1.operator_controls.rf_gain"),
        (SetAfLevel(level=90, receiver=1), "receiver.1.operator_controls.af_level"),
        (SetSquelch(level=33, receiver=1), "receiver.1.operator_controls.squelch"),
        (SetNB(on=True, receiver=1), "receiver.1.operator_toggles.nb"),
        (SetNR(on=False, receiver=1), "receiver.1.operator_toggles.nr"),
        (
            SetPbtInner(level=140, receiver=1),
            "receiver.1.operator_controls.pbt_inner",
        ),
        (
            SetPbtOuter(level=116, receiver=1),
            "receiver.1.operator_controls.pbt_outer",
        ),
    ],
)
@pytest.mark.asyncio
async def test_non_readback_queue_commands_do_not_apply_state_store_observations(
    command: object,
    expected_path: str,
) -> None:
    radio = _make_radio()
    state = RadioState()
    store = StateStore()
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        radio_state=state,
        state_store=store,
    )

    await poller._execute(command)  # noqa: SLF001

    with pytest.raises(KeyError):
        store.snapshot().field(expected_path)


# MOR-437: families whose legacy RadioState mirror was removed because they
# are now observation-backed in ``_civ_rx.py``. Read-after-write for these is
# guaranteed by the CommandService scoped pending overlay (proven below by the
# overlay value) plus the StateStore observation emitted on the next poll
# readback — not by a poller-side ``RadioState`` write. ``mirror_present`` is
# False for them; pbt_inner/pbt_outer keep a deferred compatibility mirror
# because their 0x14 sub-commands are not yet on the observation mirror-skip
# list.
@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("name", "params", "command", "expected_path", "expected_mirror", "mirror_present"),
    [
        (
            "set_filter_width",
            {"width": 1500, "receiver": 1},
            SetFilterWidth(1500, receiver=1),
            "receiver.1.freq_mode.filter_width",
            ("filter_width", 1500),
            False,
        ),
        (
            "set_nb",
            {"on": True, "receiver": 1},
            SetNB(True, receiver=1),
            "receiver.1.operator_toggles.nb",
            ("nb", True),
            False,
        ),
        (
            "set_nr",
            {"on": False, "receiver": 1},
            SetNR(False, receiver=1),
            "receiver.1.operator_toggles.nr",
            ("nr", False),
            False,
        ),
        (
            "set_att",
            {"db": 12, "receiver": 1},
            SetAttenuator(12, receiver=1),
            "receiver.1.operator_controls.att",
            ("att", 12),
            False,
        ),
        (
            "set_preamp",
            {"level": 2, "receiver": 1},
            SetPreamp(2, receiver=1),
            "receiver.1.operator_controls.preamp",
            ("preamp", 2),
            False,
        ),
        (
            "set_pbt_inner",
            {"level": 140, "receiver": 1},
            SetPbtInner(140, receiver=1),
            "receiver.1.operator_controls.pbt_inner",
            ("pbt_inner", 140),
            True,
        ),
        (
            "set_pbt_outer",
            {"level": 116, "receiver": 1},
            SetPbtOuter(116, receiver=1),
            "receiver.1.operator_controls.pbt_outer",
            ("pbt_outer", 116),
            True,
        ),
    ],
)
@pytest.mark.asyncio
async def test_compatibility_mirror_commands_do_not_confirm_state_without_readback(
    name: str,
    params: dict[str, object],
    command: object,
    expected_path: str,
    expected_mirror: tuple[str, object],
    mirror_present: bool,
) -> None:
    radio = _make_radio()
    state = RadioState()
    store = StateStore()
    service = CommandService(executor=_NoopCommandExecutor(), state_store=store)
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        radio_state=state,
        state_store=store,
    )
    await service.execute(
        command_intent_from_request(
            name,
            params,
            source="websocket",
            command_id="ws-compat",
        )
    )
    assert service.pending_overlays(source="websocket", session_id=None) != ()

    await poller._execute(  # noqa: SLF001
        command,
        command_id="ws-compat",
        source="websocket",
        command_service=service,
    )

    # Setter success never confirms StateStore — confirmation requires readback.
    with pytest.raises(KeyError):
        store.snapshot().field(expected_path)

    # Read-after-write stays guaranteed by the scoped pending overlay, which
    # still carries the written value after the poller executes the command.
    overlays = service.pending_overlays(source="websocket", session_id=None)
    assert overlays != ()
    overlay_values = {
        str(overlay.path): overlay.value
        for overlay in overlays
        if str(overlay.path) == expected_path
    }
    assert overlay_values.get(expected_path) == expected_mirror[1]

    field, value = expected_mirror
    if mirror_present:
        # Deferred compatibility families still write the legacy RadioState
        # mirror until their observation mirror-skip migration lands.
        assert getattr(state.sub, field) == value
    else:
        # Migrated families no longer write the legacy RadioState mirror; the
        # field stays at its fresh-RadioState default (the poller did not
        # touch it). The observation pipeline + overlay above own
        # read-after-write.
        assert getattr(state.sub, field) == getattr(RadioState().sub, field)


@pytest.mark.asyncio
async def test_set_freq_success_keeps_pending_overlay_until_observation() -> None:
    radio = _make_radio()
    state = RadioState()
    store = StateStore()
    service = CommandService(
        executor=_NoopCommandExecutor(),
        state_store=store,
    )
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        radio_state=state,
        state_store=store,
    )
    await service.execute(
        command_intent_from_request(
            "set_freq",
            {"freq": 14_074_000, "receiver": 0},
            source="websocket",
            command_id="ws-set-freq",
        )
    )
    assert service.pending_overlays(source="websocket", session_id=None) != ()

    await poller._execute(  # noqa: SLF001
        SetFreq(freq=14_074_000, receiver=0),
        command_id="ws-set-freq",
        source="websocket",
        command_service=service,
    )

    with pytest.raises(KeyError):
        store.snapshot().field("receiver.0.freq_mode.freq_hz")
    assert service.pending_overlays(source="websocket", session_id=None) != ()


@pytest.mark.asyncio
async def test_set_freq_success_does_not_reconcile_reused_command_id_without_readback() -> (
    None
):
    radio = _make_radio()
    store = StateStore()
    queue = CommandQueue()
    executor = _QueuedAckExecutor(queue)
    service = CommandService(executor=executor, state_store=store)
    executor.command_service = service
    poller = RadioPoller(
        radio,
        StateCache(),
        queue,
        radio_state=RadioState(),
        state_store=store,
    )

    for session_id in ("ws-a", "ws-b"):
        await service.execute(
            command_intent_from_request(
                "set_freq",
                {"freq": 14_074_000, "receiver": 0},
                source="websocket",
                command_id="shared-id",
                session_id=session_id,
            )
        )

    assert len(service.pending_overlays(source="websocket", session_id="ws-a")) == 1
    assert len(service.pending_overlays(source="websocket", session_id="ws-b")) == 1

    entry = queue.drain_entries()[0]
    await poller._execute(  # noqa: SLF001
        entry.command,
        command_id=entry.command_id,
        source=entry.source or "websocket",
        session_id=entry.session_id,
        command_service=entry.command_service,
    )

    assert len(service.pending_overlays(source="websocket", session_id="ws-a")) == 1
    assert len(service.pending_overlays(source="websocket", session_id="ws-b")) == 1
    with pytest.raises(KeyError):
        store.snapshot().field("receiver.0.freq_mode.freq_hz")
    assert (
        service.pending_overlays(source="websocket", session_id="ws-b")[0].value
        == 14_074_000
    )


@pytest.mark.asyncio
async def test_set_split_success_keeps_pending_overlay_until_observation() -> None:
    radio = _make_radio()
    state = RadioState()
    store = StateStore()
    service = CommandService(
        executor=_NoopCommandExecutor(),
        state_store=store,
    )
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        radio_state=state,
        state_store=store,
    )
    await service.execute(
        command_intent_from_request(
            "set_split",
            {"on": True},
            source="websocket",
            command_id="ws-set-split",
        )
    )
    assert service.pending_overlays(source="websocket", session_id=None) != ()

    await poller._execute(  # noqa: SLF001
        SetSplit(on=True),
        command_id="ws-set-split",
        source="websocket",
        command_service=service,
    )

    with pytest.raises(KeyError):
        store.snapshot().field("global.tx_state.split")
    assert service.pending_overlays(source="websocket", session_id=None) != ()


@pytest.mark.asyncio
async def test_queued_command_failure_emits_failed_lifecycle_and_expires_overlay() -> (
    None
):
    radio = _make_radio()
    radio.set_freq.side_effect = ConnectionError("down")
    state = RadioState()
    store = StateStore()
    queue = CommandQueue()
    executor = _QueuedAckExecutor(queue)
    service = CommandService(executor=executor, state_store=store)
    executor.command_service = service
    poller = RadioPoller(
        radio,
        StateCache(),
        queue,
        radio_state=state,
        state_store=store,
    )

    await service.execute(
        command_intent_from_request(
            "set_freq",
            {"freq": 14_074_000, "receiver": 0},
            source="websocket",
            command_id="ws-fail",
        )
    )
    assert service.pending_overlays(source="websocket", session_id=None) != ()

    poller._send_query = AsyncMock(return_value=None)  # noqa: SLF001
    poller._queue.wait = AsyncMock(side_effect=asyncio.CancelledError())  # noqa: SLF001
    with patch("rigplane.web.radio_poller.asyncio.sleep", new=AsyncMock()):
        await poller._run()  # noqa: SLF001

    events = [
        event for event in service.lifecycle_events() if event.command_id == "ws-fail"
    ]
    assert [event.state for event in events] == [
        "accepted",
        "queued",
        "sent",
        "acknowledged",
        "failed",
    ]
    assert service.pending_overlays(source="websocket", session_id=None) == ()


@pytest.mark.asyncio
async def test_queued_core_timeout_emits_timed_out_lifecycle_and_expires_overlay() -> (
    None
):
    radio = _make_radio()
    radio.set_freq.side_effect = RigplaneTimeoutError("backend timed out")
    state = RadioState()
    store = StateStore()
    queue = CommandQueue()
    executor = _QueuedAckExecutor(queue)
    service = CommandService(executor=executor, state_store=store)
    executor.command_service = service
    poller = RadioPoller(
        radio,
        StateCache(),
        queue,
        radio_state=state,
        state_store=store,
    )

    await service.execute(
        command_intent_from_request(
            "set_freq",
            {"freq": 14_074_000, "receiver": 0},
            source="websocket",
            command_id="ws-timeout",
        )
    )
    assert service.pending_overlays(source="websocket", session_id=None) != ()

    poller._send_query = AsyncMock(return_value=None)  # noqa: SLF001
    poller._queue.wait = AsyncMock(side_effect=asyncio.CancelledError())  # noqa: SLF001
    with patch("rigplane.web.radio_poller.asyncio.sleep", new=AsyncMock()):
        await poller._run()  # noqa: SLF001

    events = [
        event
        for event in service.lifecycle_events()
        if event.command_id == "ws-timeout"
    ]
    assert [event.state for event in events] == [
        "accepted",
        "queued",
        "sent",
        "acknowledged",
        "timed_out",
    ]
    assert service.pending_overlays(source="websocket", session_id=None) == ()


@pytest.mark.asyncio
async def test_mark_queued_command_failed_scopes_reused_command_ids_by_source() -> None:
    radio = _make_radio()
    store = StateStore()
    queue = CommandQueue()
    executor = _QueuedAckExecutor(queue)
    service = CommandService(executor=executor, state_store=store)
    executor.command_service = service
    poller = RadioPoller(
        radio,
        StateCache(),
        queue,
        radio_state=RadioState(),
        state_store=store,
    )

    await service.execute(
        command_intent_from_request(
            "set_freq",
            {"freq": 14_074_000, "receiver": 0},
            source="websocket",
            command_id="shared-id",
        )
    )
    await service.execute(
        command_intent_from_request(
            "set_freq",
            {"freq": 7_074_000, "receiver": 0},
            source="http",
            command_id="shared-id",
        )
    )

    entries = queue.drain_entries()
    poller._mark_queued_command_failed(  # noqa: SLF001
        entries[0],
        RuntimeError("radio rejected command"),
    )

    assert service.pending_overlays(source="websocket", session_id=None) == ()
    assert len(service.pending_overlays(source="http", session_id=None)) == 1
    assert (
        service.pending_overlays(source="http", session_id=None)[0].value == 7_074_000
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timed_out", "expected_state"),
    [(False, "failed"), (True, "timed_out")],
)
async def test_mark_queued_command_failed_scopes_reused_command_ids_by_session(
    timed_out: bool,
    expected_state: str,
) -> None:
    radio = _make_radio()
    store = StateStore()
    queue = CommandQueue()
    executor = _QueuedAckExecutor(queue)
    service = CommandService(executor=executor, state_store=store)
    executor.command_service = service
    poller = RadioPoller(
        radio,
        StateCache(),
        queue,
        radio_state=RadioState(),
        state_store=store,
    )

    for session_id in ("ws-a", "ws-b"):
        await service.execute(
            command_intent_from_request(
                "set_freq",
                {"freq": 14_074_000, "receiver": 0},
                source="websocket",
                command_id="shared-id",
                session_id=session_id,
            )
        )

    entries = queue.drain_entries()
    poller._mark_queued_command_failed(  # noqa: SLF001
        entries[0],
        TimeoutError("command timed out")
        if timed_out
        else RuntimeError("radio rejected command"),
        timed_out=timed_out,
    )

    assert service.pending_overlays(source="websocket", session_id="ws-a") == ()
    assert len(service.pending_overlays(source="websocket", session_id="ws-b")) == 1
    assert (
        service.pending_overlays(source="websocket", session_id="ws-b")[0].value
        == 14_074_000
    )

    terminal_events = [
        event
        for event in service.lifecycle_events()
        if event.command_id == "shared-id" and event.state == expected_state
    ]
    assert len(terminal_events) == 1
    assert terminal_events[0].source == "websocket"
    assert terminal_events[0].details["session_id"] == "ws-a"


@pytest.mark.asyncio
async def test_select_vfo_success_keeps_pending_overlay_until_observation() -> None:
    radio = _make_radio()
    state = RadioState()
    store = StateStore()
    service = CommandService(
        executor=_NoopCommandExecutor(),
        state_store=store,
    )
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        radio_state=state,
        state_store=store,
    )
    await service.execute(
        command_intent_from_request(
            "set_vfo",
            {"vfo": "SUB", "receiver_count": 2},
            source="websocket",
            command_id="ws-set-vfo",
        )
    )
    assert service.pending_overlays(source="websocket", session_id=None) != ()

    await poller._execute(  # noqa: SLF001
        SelectVfo(vfo="SUB"),
        command_id="ws-set-vfo",
        source="websocket",
        command_service=service,
    )

    with pytest.raises(KeyError):
        store.snapshot().field("receiver.0.vfo.active_slot")
    assert service.pending_overlays(source="websocket", session_id=None) != ()


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


@pytest.mark.asyncio
async def test_command_preempts_in_flight_poll_burst() -> None:
    """MOR-497(ii) end-to-end analogue: with a real ``IcomCommander``, a burst
    of fire-and-forget BACKGROUND poll sends followed by a NORMAL command must
    let the NORMAL command dispatch before the remaining BACKGROUND polls.

    Because the poll sends are ``wait_dispatch=False`` they return immediately
    (do not park the caller), so the NORMAL command is enqueued promptly and
    the PriorityQueue preempts the queued backgrounds.
    """
    order: list[bytes] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        # Park the worker on the gate item so the burst + command all queue
        # together before any of them dispatch.
        if cmd == b"gate":
            started.set()
            await release.wait()
        order.append(cmd)
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    try:
        gate = asyncio.create_task(c.send(b"gate", priority=Priority.NORMAL))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        # Fire-and-forget BACKGROUND poll burst: each returns immediately.
        for i in range(5):
            await asyncio.wait_for(
                c.send(
                    f"bg-{i}".encode(),
                    priority=Priority.BACKGROUND,
                    wait_response=False,
                    wait_dispatch=False,
                ),
                timeout=0.5,
            )

        # A NORMAL command enqueued AFTER the burst.
        command = asyncio.create_task(c.send(b"cmd", priority=Priority.NORMAL))
        await asyncio.sleep(0.01)  # let it enqueue

        release.set()
        await asyncio.gather(gate, command)
        await asyncio.sleep(0.02)  # drain remaining backgrounds
    finally:
        await c.stop()

    assert order[0] == b"gate"
    cmd_idx = order.index(b"cmd")
    bg_indices = [order.index(f"bg-{i}".encode()) for i in range(5)]
    assert all(cmd_idx < bg_idx for bg_idx in bg_indices), (
        f"NORMAL command did not preempt queued backgrounds: order={order}"
    )


@pytest.mark.asyncio
async def test_poll_demand_does_not_pile_duplicates() -> None:
    """MOR-497(ii): scheduler backpressure holds when poll sends return
    immediately.  Running several ``_send_scheduler_requests`` cycles without
    delivering responses must NOT re-queue the in-flight group — the in-flight
    set and pending requests stay bounded (no duplicate re-queue)."""
    radio = _make_radio(active="MAIN")
    path = FieldPath.receiver("main", "meters", "s_meter")
    scheduler = AcquisitionScheduler(profile=_acquisition_profile(path))
    radio._acquisition_scheduler = scheduler
    executor = _InjectedAcquisitionExecutor()
    poller = RadioPoller(
        radio,
        CommandQueue(),
        radio_state=RadioState(),
        acquisition_executor=executor,
    )

    # Run many cycles WITHOUT delivering any response (scheduler clears
    # in-flight only on response, so an undelivered group must not re-queue).
    for _ in range(6):
        await poller._send_scheduler_requests()  # noqa: SLF001

    # Exactly one request stays pending (the single cadence group), and the
    # executor was invoked at most once for it (the in-flight guard prevents
    # re-sending the same already-sent paths).
    pending = scheduler.pending_requests()
    assert len(pending) == 1
    assert len(poller._acquisition_in_flight) <= 1  # noqa: SLF001
    assert len(executor.calls) == 1, (
        f"in-flight group re-queued: {len(executor.calls)} executor calls"
    )


# ---------------------------------------------------------------------------
# MOR-491-B: NB depth/width initial-connect readback (Option B, direct getter).
#
# NB depth/width are global menu items (0x1A 05 02 90/91) whose 4-byte READ
# query cannot ride the poll-query envelope, so the continuous poller never
# tracks them. ``_fetch_nb_controls`` reads them once at connect via the direct
# getters and applies the real values as StateStore observations so the web
# sliders seed correctly. A purpose-built fake radio (not MagicMock) is used so
# a getter-signature regression would surface as a real TypeError.
# ---------------------------------------------------------------------------


class _FakeNbRadio:
    """Minimal NB-capable radio fake for ``_fetch_nb_controls`` tests.

    ``get_nb_*`` either return a canned value or raise to exercise the
    resilient error path; calls are counted so the test can assert each
    getter is invoked exactly once.
    """

    def __init__(
        self,
        *,
        depth: int | Exception = 0,
        width: int | Exception = 0,
        capabilities: set[str] | None = None,
    ) -> None:
        self.model = "IC-7610"
        self.capabilities = (
            capabilities if capabilities is not None else {"nb", "dual_rx"}
        )
        self._depth = depth
        self._width = width
        self.depth_calls = 0
        self.width_calls = 0

    async def get_nb_depth(self) -> int:
        self.depth_calls += 1
        if isinstance(self._depth, Exception):
            raise self._depth
        return self._depth

    async def get_nb_width(self) -> int:
        self.width_calls += 1
        if isinstance(self._width, Exception):
            raise self._width
        return self._width


@pytest.mark.asyncio
async def test_fetch_nb_controls_seeds_state_store_and_public_state() -> None:
    """Initial fetch reads both getters and the values reach web state."""
    from rigplane.web.runtime_helpers import (
        build_public_state_payload_from_snapshot,
    )

    radio = _FakeNbRadio(depth=7, width=200)
    store = StateStore()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    await poller._fetch_nb_controls()  # noqa: SLF001

    assert radio.depth_calls == 1
    assert radio.width_calls == 1

    snapshot = store.snapshot()
    assert snapshot.field(FieldPath.global_("operator_controls", "nb_depth")).value == 7
    assert (
        snapshot.field(FieldPath.global_("operator_controls", "nb_width")).value == 200
    )

    # The public projection (what /api/v1/state serves) exposes the values.
    payload = build_public_state_payload_from_snapshot(
        snapshot,
        radio=None,
        receiver_count=2,
    )
    assert payload["nbDepth"] == 7
    assert payload["nbWidth"] == 200


@pytest.mark.asyncio
async def test_fetch_nb_controls_skips_without_nb_capability() -> None:
    """No NB capability => no getter calls, no observations."""
    radio = _FakeNbRadio(depth=7, width=200, capabilities={"dual_rx"})
    store = StateStore()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    await poller._fetch_nb_controls()  # noqa: SLF001

    assert radio.depth_calls == 0
    assert radio.width_calls == 0
    with pytest.raises(KeyError):
        store.snapshot().field(FieldPath.global_("operator_controls", "nb_depth"))


@pytest.mark.asyncio
async def test_fetch_nb_controls_is_resilient_to_getter_failure() -> None:
    """A failing depth getter must not block the width readback."""
    radio = _FakeNbRadio(depth=RuntimeError("boom"), width=120)
    store = StateStore()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    await poller._fetch_nb_controls()  # noqa: SLF001

    assert radio.depth_calls == 1
    assert radio.width_calls == 1
    snapshot = store.snapshot()
    # Depth read failed => no observation applied.
    with pytest.raises(KeyError):
        snapshot.field(FieldPath.global_("operator_controls", "nb_depth"))
    # Width readback still succeeded.
    assert (
        snapshot.field(FieldPath.global_("operator_controls", "nb_width")).value == 120
    )


# ---------------------------------------------------------------------------
# MOR-615: current MOD-input source readback (DATA OFF/1/2/3 MOD, 0x1A 05 00
# 0x91-0x94). Same Option-B direct-getter route as the NB controls above: the
# four values are global menu items the continuous poller never tracks, so the
# poller reads them at connect, when data_mode changes, and after a web set,
# and applies the confirmed values as ``global.slow_state`` observations plus
# legacy RadioState mirror writes. Gated on CAP_DATA_MODE so non-IC-7610
# radios are unaffected.
# ---------------------------------------------------------------------------


class _FakeModInputRadio:
    """Minimal DATA-mode-capable radio fake for MOD-input readback tests.

    Getters either return a canned source value or raise to exercise the
    resilient error path; calls are counted so tests can assert each getter
    is invoked exactly the expected number of times.
    """

    def __init__(
        self,
        *,
        sources: dict[str, int | Exception] | None = None,
        capabilities: set[str] | None = None,
    ) -> None:
        self.model = "IC-7610"
        self.capabilities = (
            capabilities if capabilities is not None else {"data_mode", "dual_rx"}
        )
        self._sources: dict[str, int | Exception] = {
            "data_off_mod_input": 0,
            "data1_mod_input": 3,
            "data2_mod_input": 3,
            "data3_mod_input": 5,
        }
        if sources:
            self._sources.update(sources)
        self.get_calls: dict[str, int] = dict.fromkeys(self._sources, 0)
        self.set_calls: list[tuple[str, int]] = []

    def _get(self, name: str) -> int:
        self.get_calls[name] += 1
        value = self._sources[name]
        if isinstance(value, Exception):
            raise value
        return value

    async def get_data_off_mod_input(self) -> int:
        return self._get("data_off_mod_input")

    async def get_data1_mod_input(self) -> int:
        return self._get("data1_mod_input")

    async def get_data2_mod_input(self) -> int:
        return self._get("data2_mod_input")

    async def get_data3_mod_input(self) -> int:
        return self._get("data3_mod_input")

    async def set_data1_mod_input(self, source: int) -> None:
        self.set_calls.append(("data1_mod_input", source))
        self._sources["data1_mod_input"] = source


_MOD_INPUT_FIELD_NAMES = (
    "data_off_mod_input",
    "data1_mod_input",
    "data2_mod_input",
    "data3_mod_input",
)


@pytest.mark.asyncio
async def test_fetch_mod_inputs_seeds_state_store_mirror_and_public_state() -> None:
    """Initial fetch reads all four getters; values reach store + web state."""
    from rigplane.web.runtime_helpers import (
        build_public_state_payload_from_snapshot,
    )

    radio = _FakeModInputRadio(
        sources={
            "data_off_mod_input": 0,
            "data1_mod_input": 3,
            "data2_mod_input": 1,
            "data3_mod_input": 5,
        }
    )
    store = StateStore()
    state = RadioState()
    poller = RadioPoller(radio, CommandQueue(), radio_state=state, state_store=store)

    await poller._fetch_mod_inputs()  # noqa: SLF001

    assert all(count == 1 for count in radio.get_calls.values())
    snapshot = store.snapshot()
    expected = {
        "data_off_mod_input": 0,
        "data1_mod_input": 3,
        "data2_mod_input": 1,
        "data3_mod_input": 5,
    }
    for name, value in expected.items():
        assert snapshot.field(FieldPath.global_("slow_state", name)).value == value
    # Legacy RadioState mirror stays coherent for compatibility consumers.
    assert state.data_off_mod_input == 0
    assert state.data1_mod_input == 3
    assert state.data2_mod_input == 1
    assert state.data3_mod_input == 5

    # The public projection (the ``state_update`` snapshot body) exposes them.
    payload = build_public_state_payload_from_snapshot(
        snapshot,
        radio=None,
        receiver_count=2,
    )
    assert payload["dataOffModInput"] == 0
    assert payload["data1ModInput"] == 3
    assert payload["data2ModInput"] == 1
    assert payload["data3ModInput"] == 5


@pytest.mark.asyncio
async def test_fetch_mod_inputs_skips_without_data_mode_capability() -> None:
    """No data_mode capability => no getter calls, no observations."""
    radio = _FakeModInputRadio(capabilities={"dual_rx"})
    store = StateStore()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    await poller._fetch_mod_inputs()  # noqa: SLF001

    assert all(count == 0 for count in radio.get_calls.values())
    with pytest.raises(KeyError):
        store.snapshot().field(FieldPath.global_("slow_state", "data1_mod_input"))


@pytest.mark.asyncio
async def test_fetch_mod_inputs_is_resilient_to_getter_failure() -> None:
    """A failing getter must not block the remaining readbacks."""
    radio = _FakeModInputRadio(sources={"data1_mod_input": RuntimeError("boom")})
    store = StateStore()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    await poller._fetch_mod_inputs()  # noqa: SLF001

    assert all(count == 1 for count in radio.get_calls.values())
    snapshot = store.snapshot()
    # data1 read failed => no observation applied.
    with pytest.raises(KeyError):
        snapshot.field(FieldPath.global_("slow_state", "data1_mod_input"))
    # The other readbacks still succeeded.
    assert snapshot.field(FieldPath.global_("slow_state", "data3_mod_input")).value == 5


def test_unread_mod_inputs_project_as_null_with_missing_status() -> None:
    """The public payload always carries the MOD-input keys (null until read)."""
    from rigplane.web.runtime_helpers import (
        build_public_state_payload_from_snapshot,
    )

    payload = build_public_state_payload_from_snapshot(
        StateStore().snapshot(),
        radio=None,
        receiver_count=2,
    )
    for key in ("dataOffModInput", "data1ModInput", "data2ModInput", "data3ModInput"):
        assert key in payload
        assert payload[key] is None
        assert payload["fieldStatus"][key]["availability"] == "missing"


@pytest.mark.asyncio
async def test_data_mode_change_triggers_mod_input_refetch() -> None:
    """A data_mode change observed in the mirror (0x26 poll) => refetch."""
    radio = _FakeModInputRadio()
    store = StateStore()
    state = RadioState()
    poller = RadioPoller(radio, CommandQueue(), radio_state=state, state_store=store)

    await poller._fetch_mod_inputs()  # noqa: SLF001
    assert all(count == 1 for count in radio.get_calls.values())

    # No data_mode change => no refetch.
    await poller._refresh_mod_inputs_on_data_mode_change()  # noqa: SLF001
    assert all(count == 1 for count in radio.get_calls.values())

    # data_mode change (0x26 poll response landing in the mirror) => refetch.
    state.main.data_mode = 1
    await poller._refresh_mod_inputs_on_data_mode_change()  # noqa: SLF001
    assert all(count == 2 for count in radio.get_calls.values())

    # Stable again afterwards => no further refetch.
    await poller._refresh_mod_inputs_on_data_mode_change()  # noqa: SLF001
    assert all(count == 2 for count in radio.get_calls.values())


@pytest.mark.asyncio
async def test_set_data1_mod_input_dispatch_reads_back_confirmed_value() -> None:
    """SetData1ModInput sends the set, then reads back the confirmed value."""
    radio = _FakeModInputRadio(sources={"data1_mod_input": 0})
    store = StateStore()
    state = RadioState()
    poller = RadioPoller(radio, CommandQueue(), radio_state=state, state_store=store)

    await poller._execute(SetData1ModInput(5))  # noqa: SLF001

    assert radio.set_calls == [("data1_mod_input", 5)]
    assert radio.get_calls["data1_mod_input"] == 1
    snapshot = store.snapshot()
    assert snapshot.field(FieldPath.global_("slow_state", "data1_mod_input")).value == 5
    assert state.data1_mod_input == 5


# --- MOR-1181: the shutdown TX-safety drain --------------------------------


def _tx_poller(
    supervisor: _Supervisor | None,
) -> tuple[RadioPoller, _Radio, CommandQueue]:
    poller, radio = _poller(supervisor)
    return poller, radio, poller._queue  # noqa: SLF001


def _cancel_run_with_queued(queue: CommandQueue, *pending: object) -> None:
    """Cancel the real ``_run`` at its wait, leaving *pending* queued behind it —
    the file's own cancellation idiom, enqueuing where production does."""

    async def _wait(*args: object, **kwargs: object) -> None:
        for cmd in pending:
            queue.put(cmd, source="websocket", session_id="ws-1")  # type: ignore[arg-type]
        raise asyncio.CancelledError

    queue.wait = _wait  # type: ignore[method-assign]


@pytest.mark.parametrize("managed", [True, False])
@pytest.mark.asyncio
async def test_shutdown_drain_delivers_an_unkey_queued_at_cancellation(
    managed: bool,
) -> None:
    """MOR-1181: a cancelled poller must not abandon a queued PttOff — the
    handler was ``pass``, so the entry died with the task and left a keyed
    transmitter behind a gone process. Both paths matter: the managed one gives
    the lease back, and the legacy one (kill switch off, or a backend publishing
    no supervisor) is the residual with no other de-key at all."""
    supervisor = _Supervisor() if managed else None
    poller, radio, queue = _tx_poller(supervisor)
    await poller._execute(PttOn(), session_id="ws-1")  # noqa: SLF001
    _cancel_run_with_queued(queue, PttOff())

    await poller._run()  # noqa: SLF001

    assert queue.has_commands is False
    if supervisor is not None:
        assert supervisor.entries[-1] == (False, TxOwner(TxSource.WEBSOCKET, "ws-1"))
        assert supervisor.outcomes[-1] is TxOutcome.ACCEPTED
        assert radio.calls == ["start_tx", *_TEARDOWN]
    else:
        assert radio.calls == [*_KEY, "set_ptt(False)", *_TEARDOWN]


@pytest.mark.asyncio
async def test_shutdown_drain_never_executes_a_pending_key() -> None:
    """The safety-critical negative: keying a rig this process is abandoning is
    the worst outcome this path can produce, so a pending PttOn is discarded."""
    supervisor = _Supervisor()
    poller, radio, queue = _tx_poller(supervisor)
    _cancel_run_with_queued(queue, PttOn())

    await poller._run()  # noqa: SLF001

    assert radio.calls == []  # no start_tx, no key, no lease attempt
    assert supervisor.entries == []
    assert queue.has_commands is False


@pytest.mark.asyncio
async def test_shutdown_drain_discards_and_reports_non_tx_commands(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stale freq/mode/level writes must not fire on the way out — but they are
    counted and named, because a silently dropped command is its own defect."""
    poller, radio, queue = _tx_poller(None)
    _cancel_run_with_queued(queue, SetFreq(14_074_000), PttOff())

    with caplog.at_level(logging.INFO, logger="rigplane.web.radio_poller"):
        await poller._run()  # noqa: SLF001

    assert radio.calls == ["set_ptt(False)", *_TEARDOWN]
    assert "discarded 1 pending command(s)" in caplog.text
    assert "SetFreq" in caplog.text


@pytest.mark.asyncio
async def test_shutdown_drain_abandons_the_wait_not_the_write_on_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A wedged rig must not hold shutdown open; the bound reports what it could
    not confirm, and the shield leaves the OFF still trying behind it."""
    poller, radio, queue = _tx_poller(None)
    gate = asyncio.Event()

    async def _wedged_set_ptt(on: bool) -> None:
        radio.calls.append(f"set_ptt({on})")
        await gate.wait()

    radio.set_ptt = _wedged_set_ptt  # type: ignore[method-assign]
    queue.put(PttOff(), command_id="teardown-ptt-off-ws-1", source="websocket")

    started = time.monotonic()
    with caplog.at_level(logging.ERROR, logger="rigplane.web.radio_poller"):
        await poller.drain_tx_safety_commands(timeout=0.05)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert "may still be keyed" in caplog.text
    assert "teardown-ptt-off-ws-1" in caplog.text
    assert radio.calls == ["set_ptt(False)"]
    gate.set()
    await asyncio.sleep(0.01)  # the shielded write completes behind the bound
    assert radio.calls == ["set_ptt(False)", *_TEARDOWN]


def _writable_control_session(queue: CommandQueue) -> ControlHandler:
    """A writable control session whose ``run()`` ends only when cancelled —
    which is exactly when ``stop_web_server`` cancels its client tasks."""

    async def recv() -> tuple[int, bytes]:
        await asyncio.Event().wait()
        raise EOFError  # pragma: no cover - unreachable, keeps the return type

    return ControlHandler(
        ws=SimpleNamespace(send_text=AsyncMock(), recv=recv),
        radio=SimpleNamespace(connected=True, radio_ready=True),
        server_version="test",
        radio_model="IC-7610",
        server=SimpleNamespace(
            command_queue=queue,
            register_control_event_queue=MagicMock(),
            unregister_control_event_queue=MagicMock(),
            build_state_update_envelope=MagicMock(return_value={}),
        ),
    )


def _shutdown_server(
    poller: RadioPoller | None,
    client_tasks: list[asyncio.Task[None]],
    *,
    scope_enabled: bool = False,
    scope_enable_failed: bool = False,
) -> SimpleNamespace:
    """The subset of ``WebServer`` that ``stop_web_server`` actually touches."""
    return SimpleNamespace(
        _radio_poller=poller,
        _state_poller=None,
        _state_store_freshness_task=None,
        _audio_broadcaster=SimpleNamespace(_stop_relay=AsyncMock()),
        _webrtc_sessions=None,
        _diagnostics=SimpleNamespace(stop=AsyncMock()),
        _audio_bridge=None,
        _discovery=None,
        _dx_client=None,
        _dx_client_task=None,
        _zombie_reaper_task=None,
        _scope_health_task=None,
        _scope_reenable_task=None,
        _bg_tasks=[],
        _client_tasks=client_tasks,
        _server=None,
        _server_was_running=True,
        _scope_enabled=scope_enabled,
        _scope_enable_failed=scope_enable_failed,
    )


@pytest.mark.asyncio
async def test_server_shutdown_delivers_the_unkey_its_client_enqueues_late() -> None:
    """MOR-1181's production case, and why the drain alone is not enough:
    ``stop_web_server`` cancels the poller at step 1, but the teardown PttOff
    does not exist until ``ControlHandler.run()``'s finally runs at step 6. The
    poller's own CancelledError handler therefore finds an empty queue every
    time, deterministically — only the awaited final drain delivers the unkey."""
    supervisor = _Supervisor()
    radio = _Radio(supervisor)
    queue = CommandQueue()
    poller = RadioPoller(radio, queue)  # type: ignore[arg-type]
    handler = _writable_control_session(queue)
    client: asyncio.Task[None] = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)  # run() reaches the recv loop and publishes itself
    await poller._execute(PttOn(), session_id=handler._session_id)  # noqa: SLF001
    poller.start()
    await asyncio.sleep(0.01)  # the loop is genuinely running when it is stopped

    await stop_web_server(_shutdown_server(poller, [client]))  # type: ignore[arg-type]

    owner = TxOwner(TxSource.WEBSOCKET, handler._session_id)
    assert supervisor.entries == [(True, owner), (False, owner)]
    assert supervisor.outcomes[-1] is TxOutcome.ACCEPTED
    assert radio.calls == ["start_tx", *_TEARDOWN]
    assert queue.has_commands is False


def _scope_shutdown_poller(
    *, external_cat_session_active: bool = False
) -> tuple[RadioPoller, _Radio, CommandQueue]:
    """Stopped-poller restore harness over the same executor as the TX drain."""
    radio = _Radio(None)
    radio.capabilities.add(CAP_SCOPE)
    radio.external_cat_session_active = external_cat_session_active
    queue = CommandQueue()
    poller = RadioPoller(radio, queue)  # type: ignore[arg-type]
    poller._scope_session_state = (True, False)  # noqa: SLF001
    poller._scope_session_active = True  # noqa: SLF001
    return poller, radio, queue


@pytest.mark.asyncio
async def test_shutdown_restores_scope_after_final_unkey_on_same_executor() -> None:
    """Scope restoration stays on the sole poller executor and follows PTT OFF."""
    poller, radio, queue = _scope_shutdown_poller()

    async def _restore_scope(state: tuple[bool, bool]) -> None:
        radio.calls.append(f"restore_scope{state}")

    radio.restore_scope_session_state = _restore_scope  # type: ignore[attr-defined]
    queue.put(PttOff(), command_id="shutdown-off", source="websocket")
    server = _shutdown_server(poller, [], scope_enabled=True)

    with patch.object(poller, "_execute", wraps=poller._execute) as execute:  # noqa: SLF001
        await stop_web_server(server)  # type: ignore[arg-type]

    # Both writes entered through this one stopped poller's existing executor;
    # no parallel queue consumer or second radio-control lane is created.
    assert [type(call.args[0]) for call in execute.await_args_list] == [
        PttOff,
        DisableScope,
    ]
    assert radio.calls == [
        "set_ptt(False)",
        *_TEARDOWN,
        "restore_scope(True, False)",
    ]
    assert not server._scope_enabled
    assert not server._scope_enable_failed
    assert poller._scope_session_state is None  # noqa: SLF001
    assert poller._scope_session_active is False  # noqa: SLF001
    assert queue.has_commands is False


@pytest.mark.asyncio
async def test_shutdown_external_cat_defers_scope_but_still_delivers_unkey(
    caplog: pytest.LogCaptureFixture,
) -> None:
    poller, radio, queue = _scope_shutdown_poller(external_cat_session_active=True)
    restore = AsyncMock()
    radio.restore_scope_session_state = restore  # type: ignore[attr-defined]
    queue.put(PttOff(), command_id="shutdown-off", source="websocket")
    server = _shutdown_server(poller, [], scope_enabled=True)

    with caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"):
        await stop_web_server(server)  # type: ignore[arg-type]

    assert radio.calls == ["set_ptt(False)", *_TEARDOWN]
    restore.assert_not_awaited()
    assert server._scope_enabled
    assert poller._scope_session_state == (True, False)  # noqa: SLF001
    assert poller._scope_session_active is True  # noqa: SLF001
    assert "external CAT session owns the wire" in caplog.text
    assert "state remains pending" in caplog.text


@pytest.mark.parametrize("failure", ["error", "timeout"])
@pytest.mark.asyncio
async def test_shutdown_scope_restore_failure_never_delays_final_unkey(
    failure: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    poller, radio, queue = _scope_shutdown_poller()
    restore_started = asyncio.Event()

    async def _restore_scope(_state: tuple[bool, bool]) -> None:
        restore_started.set()
        if failure == "error":
            raise ConnectionError("scope link failed")
        await asyncio.Event().wait()

    radio.restore_scope_session_state = _restore_scope  # type: ignore[attr-defined]
    queue.put(PttOff(), command_id="shutdown-off", source="websocket")
    server = _shutdown_server(poller, [], scope_enabled=True)

    started = time.monotonic()
    with (
        patch("rigplane.web.web_startup._SHUTDOWN_SCOPE_RESTORE_TIMEOUT_S", 0.02),
        caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"),
    ):
        await stop_web_server(server)  # type: ignore[arg-type]
    elapsed = time.monotonic() - started

    assert restore_started.is_set()
    assert elapsed < 1.0
    assert radio.calls == ["set_ptt(False)", *_TEARDOWN]
    assert server._scope_enabled
    assert poller._scope_session_state == (True, False)  # noqa: SLF001
    assert poller._scope_session_active is True  # noqa: SLF001
    expected = "timed out" if failure == "timeout" else "failed"
    assert expected in caplog.text
    assert "state remains pending" in caplog.text


# --- MOR-1220: the unmanaged max-key-down backstop -------------------------
# MOR-1165 finding A1-1: shipped serial/USB Icom backends arm no supervisor, and
# MOR-1011/1012 deleted the frontend's 3-minute PTT timers, so a latched web TX
# on one had no key-down limit anywhere. These pin the bound that restores it —
# at the poller, legacy arm only, through the queue MOR-1181's drain reads.

_BACKSTOP = 0.05  # test-scale stand-in for BACKEND_MAX_KEY_DOWN_SECONDS


async def _until(ready: Callable[[], bool], timeout: float = 2.0) -> None:
    """Wait on *ready*, so no test here sleeps out the real 180 s bound."""
    deadline = time.monotonic() + timeout
    while not ready():
        assert time.monotonic() < deadline, "condition never became true"
        await asyncio.sleep(0.005)


def _keyed(supervisor: _Supervisor | None) -> tuple[RadioPoller, _Radio, CommandQueue]:
    """A poller on a test-scale bound, its real loop running, PTT ON queued."""
    poller, radio, queue = _tx_poller(supervisor)
    poller._max_key_down_seconds = _BACKSTOP  # noqa: SLF001
    poller.start()
    queue.put(PttOn(), source="websocket", session_id="ws-1")
    return poller, radio, queue


@pytest.mark.asyncio
async def test_backstop_forces_the_unkey_a_latched_legacy_key_never_sends(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A1-1's case: an unmanaged rig keyed and never unkeyed. The bound takes it
    off the air through the real loop, names it at ERROR, and surfaces it on the
    state-event lane the operator's UI reads."""
    events: list[tuple[str, dict[str, Any]]] = []
    poller, radio, _ = _keyed(None)
    poller._on_state_event = lambda n, d: events.append((n, d))  # noqa: SLF001

    with caplog.at_level(logging.ERROR, logger="rigplane.web.radio_poller"):
        await _until(lambda: radio.calls[-1:] == ["restart_rx"])  # incl. teardown
    poller.stop()

    assert radio.calls == [*_KEY, "set_ptt(False)", *_TEARDOWN]
    assert "max key-down (0.05s) exceeded on unmanaged radio; forcing" in caplog.text
    assert events[-1] == (
        "tx_max_key_down",
        {"seconds": _BACKSTOP, "session_id": "ws-1"},
    )


@pytest.mark.asyncio
async def test_an_operator_unkey_disarms_the_backstop() -> None:
    """A backstop, not a second unkey: a forced unkey after the operator's own
    would, on a re-key, take the NEXT transmission off the air."""
    poller, radio, queue = _keyed(None)
    queue.put(PttOff(), source="websocket", session_id="ws-1")

    await _until(lambda: "set_ptt(False)" in radio.calls)
    await asyncio.sleep(_BACKSTOP * 4)  # well past the disarmed expiry
    poller.stop()

    assert radio.calls == [*_KEY, "set_ptt(False)", *_TEARDOWN]  # exactly one
    assert poller._max_key_down_timer is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_the_managed_path_arms_no_backstop() -> None:
    """The supervisor owns the managed bound (``BACKEND_MAX_KEY_DOWN``, driven by
    the runtime ticker); a second timer here de-keys a lease it does not hold."""
    supervisor = _Supervisor()
    poller, radio, _ = _keyed(supervisor)

    await _until(lambda: radio.calls == ["start_tx"])
    await asyncio.sleep(_BACKSTOP * 4)  # long enough for a wrongly-armed bound
    poller.stop()

    assert poller._max_key_down_timer is None  # noqa: SLF001
    assert radio.calls == ["start_tx"]  # no raw unkey behind the supervisor
    assert supervisor.entries == [(True, TxOwner(TxSource.WEBSOCKET, "ws-1"))]


@pytest.mark.asyncio
async def test_an_expiry_that_races_shutdown_is_delivered_by_the_drain() -> None:
    """Why the expiry ENQUEUES: shutdown stops the loop at step 1 and drains at
    step 9 (MOR-1181), so an unkey already minted rides that drain out."""
    poller, radio, queue = _tx_poller(None)
    poller._max_key_down_seconds = _BACKSTOP  # noqa: SLF001
    await poller._execute(PttOn(), session_id="ws-1")  # noqa: SLF001

    await _until(lambda: queue.has_commands)  # the expiry minted it, undrained
    poller.stop()  # shutdown races it
    assert radio.calls == _KEY  # nothing delivered it yet
    await poller.drain_tx_safety_commands()

    assert radio.calls == [*_KEY, "set_ptt(False)", *_TEARDOWN]


@pytest.mark.asyncio
async def test_a_retired_pollers_backstop_does_not_leak_into_the_next_connect() -> None:
    """A new connect builds a new poller (``start_web_server``); the retired
    one's unfired bound must not outlive it — nothing reads that queue again."""
    retired, radio, queue = _tx_poller(None)
    retired._max_key_down_seconds = _BACKSTOP  # noqa: SLF001
    await retired._execute(PttOn(), session_id="ws-1")  # noqa: SLF001
    retired.stop()

    fresh = RadioPoller(radio, queue)  # type: ignore[arg-type]
    await asyncio.sleep(_BACKSTOP * 4)  # past the retired bound

    assert retired._max_key_down_timer is None  # noqa: SLF001
    assert fresh._max_key_down_timer is None  # noqa: SLF001
    assert queue.has_commands is False  # the retired timer minted nothing
    assert radio.calls == _KEY
    bound = fresh._max_key_down_seconds  # noqa: SLF001  (production, not test)
    assert bound == BACKEND_MAX_KEY_DOWN_SECONDS == 180.0


@pytest.mark.asyncio
async def test_a_lost_operator_unkey_keeps_the_backstop_armed() -> None:
    """Why the disarm sits BELOW the write: an unkey that RAISED did not reach the
    rig, and dropping the bound on it strands a keyed transmitter."""
    poller, radio, queue = _keyed(None)
    poller._max_key_down_seconds = 0.4  # noqa: SLF001  (outlast the lost unkey)
    eaten: list[bool] = []

    async def _lossy_set_ptt(on: bool) -> None:  # the rig eats exactly ONE unkey
        if not on and not eaten:
            eaten.append(True)
            radio.calls.append("set_ptt(False:LOST)")
            raise ConnectionError("unkey never reached the rig")
        await _Radio.set_ptt(radio, on)

    radio.set_ptt = _lossy_set_ptt  # type: ignore[method-assign]
    queue.put(PttOff(), source="websocket", session_id="ws-1")  # drains after the key
    await _until(lambda: "set_ptt(False:LOST)" in radio.calls)

    await _until(lambda: "set_ptt(False)" in radio.calls, timeout=6.0)
    poller.stop()

    lost, forced = ["set_ptt(False:LOST)", *_TEARDOWN], ["set_ptt(False)", *_TEARDOWN]
    assert radio.calls == [*_KEY, *lost, *forced]


# ---------------------------------------------------------------------------
# MOR-1443: auto-command VFO A once when active-VFO identity is unqueryable.
# ---------------------------------------------------------------------------


async def _run_once(poller: RadioPoller) -> None:
    """Drive ``_run`` through its one-time startup fetches, then stop.

    Mirrors the established harness above (e.g.
    ``test_scheduler_polling_does_not_starve_user_command_queue``): make the
    queue's ``wait`` raise ``CancelledError`` so the main poll loop exits
    after the once-per-connect startup sequence runs.
    """

    poller._queue.wait = AsyncMock(side_effect=asyncio.CancelledError())  # noqa: SLF001
    with patch("rigplane.web.radio_poller.asyncio.sleep", new=AsyncMock()):
        await poller._run()  # noqa: SLF001


@pytest.mark.asyncio
async def test_run_auto_commands_vfo_a_when_identity_unqueryable() -> None:
    """IC-7300 (``vfo_readback == "selected_unselected"``) cannot passively
    report which slot (A/B) is active (issue #2303 forbids probing via
    swap). MOR-1443: on connect, with activeSlot still unobserved, the
    poller must command VFO A exactly once through the normal confirmed
    select path so identity becomes known."""

    radio = _make_radio(model="IC-7300")
    store = StateStore()
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    assert "receiver.0.vfo.active_slot" not in store.snapshot().as_dict()

    await _run_once(poller)

    radio._set_vfo_slot_confirmed.assert_awaited_once_with("A", receiver=0)
    assert store.snapshot().field("receiver.0.vfo.active_slot").value == "A"


@pytest.mark.asyncio
async def test_run_does_not_auto_command_vfo_when_identity_queryable() -> None:
    """A profile that CAN report active-VFO identity (any ``vfo_readback``
    other than ``selected_unselected`` — e.g. absolute CI-V readback, or a
    Yaesu CAT / rigctld backend that reads it directly) must never have the
    poller emit an uncommanded VFO write; it should keep reading."""

    radio = _make_radio(model="IC-7610")  # vfo_readback defaults to "none"
    assert radio.profile.vfo_readback != "selected_unselected"
    store = StateStore()
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    await _run_once(poller)

    radio._set_vfo_slot_confirmed.assert_not_awaited()
    assert "receiver.0.vfo.active_slot" not in store.snapshot().as_dict()


@pytest.mark.asyncio
async def test_run_skips_auto_vfo_command_when_identity_already_observed() -> None:
    """activeSlot is already known when the establish check runs — no
    reconnect path retains it across a drop, ``reset_vfo_session()``
    unconditionally discards it (MOR-1443 review R2, finding 3). This
    models the window it legitimately reappears in: an operator-issued
    "Select VFO A/B" drained by the still-running poll loop between
    ``reset_vfo_session()`` and the reconnect-path establish call can
    observe identity again first. The poller must not re-command VFO A
    over that observation — no write when identity is already
    established."""

    radio = _make_radio(model="IC-7300")
    store = StateStore()
    generation = store.begin_provider_generation()
    store.apply(
        Observation(
            path=FieldPath.active_slot("0"),
            value="B",
            source=SourceMetadata(
                source="command_response",
                provider="vfo_binding",
                native_id="explicit_slot_ack_readback",
            ),
            timestamp_monotonic=time.monotonic(),
            provider_generation=generation,
        )
    )
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    await _run_once(poller)

    radio._set_vfo_slot_confirmed.assert_not_awaited()
    assert store.snapshot().field("receiver.0.vfo.active_slot").value == "B"


@pytest.mark.asyncio
async def test_establish_vfo_identity_refires_after_reconnect_reset() -> None:
    """MOR-1443 review R2, finding 1: ``RadioPoller._run()`` only fires its
    one-time startup section once, so after a soft-reconnect discards
    ``active_slot`` via ``reset_vfo_session()`` identity would otherwise
    stay unknown until process restart. ``WebServer._on_radio_reconnect()``
    /``_refetch_and_reenable()`` (server.py) now call the public
    ``establish_vfo_identity()`` itself, in the ``finally`` right after
    re-setting the poller readiness gate. This test reproduces that exact
    sequence directly against ``RadioPoller`` — run startup once, reset
    the VFO session, clear+set ``_initial_fetch_done`` the way the server
    does, then invoke the reconnect-path establish call — and asserts a
    second confirmed ``SelectVfo("A")`` emits, with ``active_slot``
    observed again afterward."""

    radio = _make_radio(model="IC-7300")
    store = StateStore()
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    await _run_once(poller)
    radio._set_vfo_slot_confirmed.assert_awaited_once_with("A", receiver=0)
    assert store.snapshot().field("receiver.0.vfo.active_slot").value == "A"

    # Reconnect sequence: reset_vfo_session() discards the connection-epoch
    # identity fact, then the server clears and re-sets the poller
    # readiness gate around the refetch — mirroring
    # WebServer._on_radio_reconnect() / _refetch_and_reenable() exactly.
    poller.reset_vfo_session()
    assert "receiver.0.vfo.active_slot" not in store.snapshot().as_dict()
    poller._initial_fetch_done.clear()  # noqa: SLF001
    poller._initial_fetch_done.set()  # noqa: SLF001

    # The first establish call already drained the fixture's two-value
    # ``read_relative_vfo`` side_effect (selected + unselected); the
    # reconnect-path establish drives another confirmed select-and-bind
    # round, which reads both again.
    radio.read_relative_vfo = AsyncMock(
        side_effect=(
            RelativeVfoState(14_200_000, "USB", 1, 0),
            RelativeVfoState(7_100_000, "LSB", 2, 0),
        )
    )

    await poller.establish_vfo_identity()

    assert radio._set_vfo_slot_confirmed.await_args_list == [
        call("A", receiver=0),
        call("A", receiver=0),
    ]
    assert store.snapshot().field("receiver.0.vfo.active_slot").value == "A"


@pytest.mark.asyncio
async def test_establish_vfo_identity_skips_during_external_cat_session() -> None:
    """MOR-1443 review R2, finding 2: the poll loop's external-CAT
    ownership pause (:1256, ``is True`` on ``external_cat_session_active``)
    must also gate the establish-once write, or this PR's write escapes
    it. An external CAT session (e.g. Hamlib A1 bridge) owns the wire, so
    the auto-commanded VFO A must not fire while it is active."""

    radio = _make_radio(model="IC-7300")
    radio.external_cat_session_active = True
    store = StateStore()
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)

    await poller.establish_vfo_identity()

    radio._set_vfo_slot_confirmed.assert_not_awaited()
    assert "receiver.0.vfo.active_slot" not in store.snapshot().as_dict()
