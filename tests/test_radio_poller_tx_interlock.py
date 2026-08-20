import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.capabilities import CAP_ANTENNA, CAP_AUDIO, CAP_POWER_CONTROL, CAP_TUNER
from rigplane.core.command_service import CommandExecutionResult, CommandService
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.exceptions import CommandError
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime._poller_types import (
    PttOff,
    PttOn,
    ScanStart,
    ScanStop,
    SelectVfo,
    SendCiv,
    SetAntenna1,
    SetFreq,
    SetPowerstat,
    SetTunerStatus,
)
from rigplane.runtime.tx_interlock import (
    RfState,
    TxInterlockCommandFamily,
    TxInterlockDisposition,
    evaluate_tx_interlock,
    get_tx_interlock_command_family_metadata,
)
from rigplane.web.radio_poller import (
    _WEB_IMMEDIATE_BLOCK_FAMILIES,
    CommandQueue,
    CommandQueueEntry,
    RadioPoller,
    TxInterlockRefusal,
)


_PTT = FieldPath.global_("tx_state", "ptt")
_FREQ = FieldPath.active("main", "freq_mode", "freq_hz")


def _radio() -> SimpleNamespace:
    return SimpleNamespace(
        profile=resolve_radio_profile(model="IC-7300"),
        capabilities={CAP_ANTENNA, CAP_POWER_CONTROL, CAP_TUNER},
        send_civ=AsyncMock(),
        scan_start=AsyncMock(),
        scan_stop=AsyncMock(),
        set_antenna_1=AsyncMock(),
        set_freq=AsyncMock(),
        set_tuner_status=AsyncMock(),
        set_powerstat=AsyncMock(),
        set_ptt=AsyncMock(),
    )


def _observe_ptt(
    store: StateStore,
    value: bool,
    *,
    observed_at: float | None = None,
    generation: int | None = None,
) -> None:
    observed_at = time.monotonic() if observed_at is None else observed_at
    generation = store.provider_generation if generation is None else generation
    store.apply(
        Observation(
            path=_PTT,
            value=value,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=observed_at,
            max_age=1.0,
            provider_generation=generation,
        )
    )


def _poller() -> tuple[RadioPoller, SimpleNamespace, StateStore]:
    radio, store = _radio(), StateStore()
    store.begin_provider_generation()
    return RadioPoller(radio, CommandQueue(), state_store=store), radio, store


def _service(clock: FreshnessClock, store: StateStore) -> CommandService:
    executor = SimpleNamespace(execute=AsyncMock(return_value=CommandExecutionResult()))
    return CommandService(executor=executor, state_store=store, clock=clock.now)


async def _lifecycle_entry(
    service: CommandService,
    *,
    command_id: str,
    freq: int,
) -> CommandQueueEntry:
    await service.execute(
        CommandIntent(
            id=command_id,
            name="set_freq",
            params={"freq_hz": freq, "session_id": "ws-a"},
            source="websocket",
            target=_FREQ,
            timeout=3.0,
            pending_policy="scoped",
            expected_observations=(_FREQ,),
        )
    )
    return CommandQueueEntry(
        SetFreq(freq),
        future=asyncio.get_running_loop().create_future(),
        command_id=command_id,
        source="websocket",
        session_id="ws-a",
        command_service=service,
    )


async def _dispatch(poller: RadioPoller, cmd: object) -> None:
    poller._enforce_tx_interlock(cmd)  # type: ignore[arg-type] # noqa: SLF001
    await poller._execute(cmd)  # type: ignore[arg-type] # noqa: SLF001


_BLOCK_CASES = (
    (SendCiv(command=0x1A, data=b"\x01"), "send_civ"),
    (ScanStart(scan_type=1), "scan_start"),
    (SetAntenna1(on=True), "set_antenna_1"),
    (SetTunerStatus(value=1), "set_tuner_status"),
)


@pytest.mark.parametrize(("cmd", "method"), _BLOCK_CASES)
@pytest.mark.parametrize("ptt", (None, True), ids=("unknown", "tx"))
async def test_disruptive_write_is_blocked_before_transport(
    cmd: object, method: str, ptt: bool | None
) -> None:
    poller, radio, store = _poller()
    if ptt is not None:
        _observe_ptt(store, ptt)

    with pytest.raises(CommandError, match="RF state is (unknown|TX)"):
        await _dispatch(poller, cmd)

    getattr(radio, method).assert_not_awaited()


@pytest.mark.parametrize(("cmd", "method"), _BLOCK_CASES)
async def test_disruptive_write_dispatches_once_in_fresh_rx(
    cmd: object, method: str
) -> None:
    poller, radio, store = _poller()
    _observe_ptt(store, False)

    await _dispatch(poller, cmd)

    getattr(radio, method).assert_awaited_once()


async def test_fresh_rx_preserves_truthful_unsupported_failure() -> None:
    poller, radio, store = _poller()
    _observe_ptt(store, False)
    del radio.send_civ
    with pytest.raises(CommandError, match="send_civ is not supported"):
        await _dispatch(poller, SendCiv(command=0x1A, data=b"\x01"))


@pytest.mark.parametrize(
    ("cmd", "method"),
    (
        (PttOff(), "set_ptt"),
        (ScanStop(), "scan_stop"),
        (SetPowerstat(on=False), "set_powerstat"),
        (SetTunerStatus(value=0), "set_tuner_status"),
    ),
)
async def test_safety_stop_or_off_is_always_attempted(cmd: object, method: str) -> None:
    poller, radio, _store = _poller()
    poller._current_rf_state = lambda: pytest.fail("stop/off inspected RF state")  # type: ignore[method-assign] # noqa: SLF001
    await _dispatch(poller, cmd)

    getattr(radio, method).assert_awaited_once()


def test_manual_clock_ttl_generation_and_recovery() -> None:
    clock = FreshnessClock(start=10.0)
    radio, store = _radio(), StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    _observe_ptt(store, False, observed_at=clock.now())
    assert store.snapshot().generated_at_monotonic == clock.now()
    assert poller._current_rf_state().value == "rx"  # noqa: SLF001
    clock.advance(0.999)
    assert poller._current_rf_state().value == "rx"  # noqa: SLF001
    clock.advance(0.001)
    assert poller._current_rf_state().value == "unknown"  # noqa: SLF001
    clock.advance(0.001)
    assert poller._current_rf_state().value == "unknown"  # noqa: SLF001
    old_generation = store.provider_generation
    store.begin_provider_generation()
    _observe_ptt(store, True, observed_at=clock.now(), generation=old_generation)
    assert poller._current_rf_state().value == "unknown"  # noqa: SLF001
    _observe_ptt(store, True, observed_at=clock.now())
    assert poller._current_rf_state().value == "tx"  # noqa: SLF001
    clock.advance(0.1)
    _observe_ptt(store, False, observed_at=clock.now())
    assert poller._current_rf_state().value == "rx"  # noqa: SLF001


async def test_deferred_entry_releases_once_with_its_original_future() -> None:
    clock = FreshnessClock(start=10.0)
    radio, store, queue = _radio(), StateStore(freshness_clock=clock), CommandQueue()
    store.begin_provider_generation()
    poller = RadioPoller(radio, queue, state_store=store)
    _observe_ptt(store, True, observed_at=clock.now())
    future = asyncio.get_running_loop().create_future()
    entry = CommandQueueEntry(SetFreq(14_074_000), future=future)

    assert poller._stage_tx_interlocked_entries([entry]) == []  # noqa: SLF001
    assert future.done() is False
    radio.set_freq.assert_not_awaited()

    clock.advance(0.1)
    _observe_ptt(store, False, observed_at=clock.now())
    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    clock.advance(0.5)
    _observe_ptt(store, False, observed_at=clock.now())
    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    clock.advance(0.5)
    _observe_ptt(store, False, observed_at=clock.now())
    assert poller._stage_tx_interlocked_entries([]) == [entry]  # noqa: SLF001

    await poller._execute_queued_entry(entry)  # noqa: SLF001
    assert future.result() is None
    radio.set_freq.assert_awaited_once_with(14_074_000)
    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    radio.set_freq.assert_awaited_once()


async def test_supersession_keeps_deadline_and_expiry_wins_over_release() -> None:
    clock = FreshnessClock(start=20.0)
    radio, store, queue = _radio(), StateStore(freshness_clock=clock), CommandQueue()
    store.begin_provider_generation()
    poller = RadioPoller(radio, queue, state_store=store)
    _observe_ptt(store, True, observed_at=clock.now())
    old_future = asyncio.get_running_loop().create_future()
    old = CommandQueueEntry(SetFreq(7_074_000), future=old_future)
    assert poller._stage_tx_interlocked_entries([old]) == []  # noqa: SLF001

    clock.advance(2.5)
    _observe_ptt(store, False, observed_at=clock.now())
    new_future = asyncio.get_running_loop().create_future()
    new = CommandQueueEntry(SetFreq(14_074_000), future=new_future)
    assert poller._stage_tx_interlocked_entries([new]) == []  # noqa: SLF001
    assert "superseded" in str(old_future.exception())
    radio.set_freq.assert_not_awaited()

    clock.advance(0.5)
    _observe_ptt(store, False, observed_at=clock.now())
    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    assert "expired" in str(new_future.exception())
    radio.set_freq.assert_not_awaited()


async def test_unknown_deferred_command_fails_closed_without_entering_lane() -> None:
    clock = FreshnessClock(start=10.0)
    radio, store = _radio(), StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    service = _service(clock, store)
    entry = await _lifecycle_entry(service, command_id="unknown", freq=14_074_000)
    before = service.lifecycle_events()

    assert poller._stage_tx_interlocked_entries([entry]) == [entry]  # noqa: SLF001
    with pytest.raises(CommandError, match="RF state is unknown"):
        await poller._execute_queued_entry(entry)  # noqa: SLF001

    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    assert service.lifecycle_events() == before
    radio.set_freq.assert_not_awaited()


async def test_fresh_rx_deferred_class_dispatches_immediately_once() -> None:
    poller, radio, store = _poller()
    _observe_ptt(store, False)
    future = asyncio.get_running_loop().create_future()
    entry = CommandQueueEntry(SetFreq(14_074_000), future=future)

    assert poller._stage_tx_interlocked_entries([entry]) == [entry]  # noqa: SLF001
    await poller._execute_queued_entry(entry)  # noqa: SLF001

    assert future.result() is None
    radio.set_freq.assert_awaited_once_with(14_074_000)


async def test_deferred_hold_lifecycle_is_single_and_release_stays_unconfirmed() -> (
    None
):
    clock = FreshnessClock(start=10.0)
    radio, store = _radio(), StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    service = _service(clock, store)
    entry = await _lifecycle_entry(service, command_id="held", freq=14_074_000)
    _observe_ptt(store, True, observed_at=clock.now())

    assert poller._stage_tx_interlocked_entries([entry]) == []  # noqa: SLF001
    held = service.lifecycle_events()[-1]
    assert (held.command_id, held.state, held.source, held.target) == (
        "held",
        "queued",
        "websocket",
        _FREQ,
    )
    assert held.details == {
        "heldBy": "tx_interlock",
        "reason": "tx_active",
        "expiresAt": 13.0,
        "session_id": "ws-a",
    }
    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    assert service.lifecycle_events()[-1] is held

    clock.advance(0.5)
    _observe_ptt(store, False, observed_at=clock.now())
    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    clock.advance(1.0)
    _observe_ptt(store, False, observed_at=clock.now())
    assert poller._stage_tx_interlocked_entries([]) == [entry]  # noqa: SLF001
    await poller._execute_queued_entry(entry)  # noqa: SLF001

    assert service.lifecycle_events()[-1] is held
    assert service.pending_overlays(source="websocket", session_id="ws-a")
    radio.set_freq.assert_awaited_once_with(14_074_000)


async def test_deferred_replacement_and_expiry_emit_ordered_terminal_truth() -> None:
    clock = FreshnessClock(start=20.0)
    radio, store = _radio(), StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    service = _service(clock, store)
    first = await _lifecycle_entry(service, command_id="first", freq=7_074_000)
    _observe_ptt(store, True, observed_at=clock.now())
    assert poller._stage_tx_interlocked_entries([first]) == []  # noqa: SLF001

    clock.advance(2.5)
    replacement = await _lifecycle_entry(
        service, command_id="replacement", freq=14_074_000
    )
    _observe_ptt(store, True, observed_at=clock.now())
    snapshots: list[tuple[str, str, tuple[str, ...]]] = []
    service.subscribe_lifecycle(
        lambda event: snapshots.append(
            (
                event.command_id,
                event.state,
                tuple(
                    overlay.command_id
                    for overlay in service.pending_overlays(
                        source="websocket", session_id="ws-a"
                    )
                ),
            )
        )
    )
    assert poller._stage_tx_interlocked_entries([replacement]) == []  # noqa: SLF001

    assert [
        (event.command_id, event.state) for event in service.lifecycle_events()[-2:]
    ] == [
        ("first", "superseded"),
        ("replacement", "queued"),
    ]
    assert snapshots == [
        ("first", "superseded", ("replacement",)),
        ("replacement", "queued", ("replacement",)),
    ]
    assert isinstance(first.future.exception(), CommandError)
    assert service.lifecycle_events()[-1].details["expiresAt"] == 23.0

    clock.advance(0.5)
    _observe_ptt(store, True, observed_at=clock.now())
    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    assert service.lifecycle_events()[-1].state == "timed_out"
    assert isinstance(replacement.future.exception(), CommandError)
    terminal_count = len(service.lifecycle_events())
    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    assert len(service.lifecycle_events()) == terminal_count
    radio.set_freq.assert_not_awaited()


# ── MOR-1884 (MOR-1500 B1-e): the seat guards _execute itself ────────────────


@pytest.mark.parametrize("ptt", (None, True), ids=("unknown", "tx"))
async def test_direct_defer_family_emit_is_refused_at_the_execute_seat(
    ptt: bool | None,
) -> None:
    """An uncommanded internal emit shares the queued commands' seat."""
    poller, radio, store = _poller()
    radio.set_vfo_slot = AsyncMock()
    if ptt is not None:
        _observe_ptt(store, ptt)

    with pytest.raises(CommandError, match="RF state is (unknown|TX)"):
        await poller._execute(SelectVfo(vfo="A"))  # noqa: SLF001

    radio.set_vfo_slot.assert_not_awaited()


async def test_bootstrap_exemption_passes_the_seat_under_unknown_rf() -> None:
    """At connection start RF is structurally UNKNOWN; the one exempted write
    must reach the dispatch body instead of being refused (MOR-1443).

    Discriminated by WHICH failure it hits: the same command without the
    exemption never leaves the seat ("RF state is unknown"), while the
    exempted one gets all the way into the SelectVfo dispatch and fails on
    that command's own readback contract — proof it passed the interlock.
    """
    poller, _radio, _store = _poller()

    with pytest.raises(CommandError, match="RF state is unknown"):
        await poller._execute(SelectVfo(vfo="A"))  # noqa: SLF001

    with pytest.raises(CommandError) as exempted:
        await poller._execute(  # noqa: SLF001
            SelectVfo(vfo="A"), connection_epoch_bootstrap=True
        )
    assert "RF state" not in str(exempted.value)
    assert "VFO selection" in str(exempted.value)


def test_bootstrap_exemption_has_exactly_one_production_call_site() -> None:
    from pathlib import Path

    import rigplane.web.radio_poller as radio_poller_module

    source = Path(radio_poller_module.__file__).read_text()
    assert source.count("connection_epoch_bootstrap=True") == 1


async def test_teardown_drain_unkey_stays_outside_the_execute_seat() -> None:
    """The drain's PttOff is structurally ALWAYS_PASS — no exemption needed."""
    poller, radio, store = _poller()
    _observe_ptt(store, True)
    poller._queue.put(PttOff())  # noqa: SLF001

    await poller.drain_tx_safety_commands(timeout=1.0)

    radio.set_ptt.assert_awaited_once_with(False)


# ── MOR-1879 (MOR-1500 slice 1): Web ptt_on is server-gated ──────────────────


def test_ptt_on_is_a_web_immediate_block_family() -> None:
    assert TxInterlockCommandFamily.PTT_ON in _WEB_IMMEDIATE_BLOCK_FAMILIES


def test_web_and_yaesu_seats_share_the_ptt_on_block_policy() -> None:
    metadata = get_tx_interlock_command_family_metadata(PttOn())
    assert metadata is not None
    assert metadata.family is TxInterlockCommandFamily.PTT_ON
    assert metadata.base_disposition is TxInterlockDisposition.BLOCK
    for rf in (RfState.UNKNOWN, RfState.TX):
        assert evaluate_tx_interlock(PttOn(), rf_state=rf).allowed is False
    assert evaluate_tx_interlock(PttOn(), rf_state=RfState.RX).allowed is True


@pytest.mark.parametrize(
    ("ptt", "reason", "code"),
    (
        (
            None,
            "RF state is unknown; this command must not be attempted yet.",
            "rf_state_unknown",
        ),
        (True, "RF state is TX; command is blocked.", "radio_transmitting"),
    ),
    ids=("unknown", "tx"),
)
async def test_ptt_on_refused_fail_closed_leaves_no_armed_audio_leg(
    ptt: bool | None, reason: str, code: str
) -> None:
    radio, store = _radio(), StateStore()
    radio.capabilities.add(CAP_AUDIO)
    radio.start_tx = AsyncMock()
    radio.stop_tx = AsyncMock()
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    if ptt is not None:
        _observe_ptt(store, ptt)

    with pytest.raises(TxInterlockRefusal) as excinfo:
        await _dispatch(poller, PttOn())

    assert str(excinfo.value) == reason
    assert excinfo.value.reason_code == code
    radio.set_ptt.assert_not_awaited()
    # Design-doc R7: the refusal precedes the audio-leg arm entirely — nothing
    # was armed, so nothing needed disarming.
    radio.start_tx.assert_not_awaited()
    radio.stop_tx.assert_not_awaited()


async def test_ptt_on_dispatches_in_fresh_rx() -> None:
    poller, radio, store = _poller()
    _observe_ptt(store, False)

    await _dispatch(poller, PttOn())

    radio.set_ptt.assert_awaited_once_with(True)


@pytest.mark.parametrize("ptt", (None, True), ids=("unknown", "tx"))
async def test_ptt_off_always_attempts_even_with_corrupted_family_table(
    monkeypatch: pytest.MonkeyPatch, ptt: bool | None
) -> None:
    poller, radio, store = _poller()
    if ptt is not None:
        _observe_ptt(store, ptt)
    corrupt = get_tx_interlock_command_family_metadata(PttOn())
    assert corrupt is not None
    monkeypatch.setattr(
        "rigplane.web.radio_poller.get_tx_interlock_command_family_metadata",
        lambda _cmd: corrupt,
    )

    await _dispatch(poller, PttOff())

    radio.set_ptt.assert_awaited_once_with(False)


async def test_refused_ptt_on_emits_machine_readable_failed_lifecycle() -> None:
    clock = FreshnessClock(start=10.0)
    radio, store = _radio(), StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    service = _service(clock, store)
    await service.execute(
        CommandIntent(
            id="ptt-refused",
            name="ptt_on",
            params={"session_id": "ws-a"},
            source="websocket",
            target=None,
            timeout=3.0,
            pending_policy="none",
            expected_observations=(),
        )
    )
    entry = CommandQueueEntry(
        PttOn(),
        future=asyncio.get_running_loop().create_future(),
        command_id="ptt-refused",
        source="websocket",
        session_id="ws-a",
        command_service=service,
    )

    with pytest.raises(TxInterlockRefusal) as excinfo:
        await poller._execute_queued_entry(entry)  # noqa: SLF001
    poller._mark_queued_command_failed(entry, excinfo.value)  # noqa: SLF001

    event = service.lifecycle_events()[-1]
    assert event.state == "failed"
    assert event.details == {
        "session_id": "ws-a",
        "blockedBy": "tx_interlock",
        "reason": "rf_state_unknown",
    }
    assert event.message == (
        "RF state is unknown; this command must not be attempted yet."
    )
    radio.set_ptt.assert_not_awaited()
