import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.capabilities import CAP_ANTENNA, CAP_AUDIO, CAP_POWER_CONTROL, CAP_TUNER
from rigplane.core.command_dispatch import bind_command_intent
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
    SetBand,
    SetFreq,
    SetMode,
    SetPowerstat,
    SetSplit,
    SetTunerStatus,
    VfoEqualize,
    VfoSwap,
    validate_command_queue_entry_currency,
)
from rigplane.runtime.radio import CoreRadio
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
_SPLIT = FieldPath.global_("tx_state", "split")


def _radio() -> SimpleNamespace:
    return SimpleNamespace(
        _civ_epoch=1,
        profile=resolve_radio_profile(model="IC-7300"),
        capabilities={CAP_ANTENNA, CAP_POWER_CONTROL, CAP_TUNER},
        send_civ=AsyncMock(),
        scan_start=AsyncMock(),
        scan_stop=AsyncMock(),
        set_antenna_1=AsyncMock(),
        set_freq=AsyncMock(),
        set_mode=AsyncMock(),
        set_split=AsyncMock(),
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
    on: bool,
) -> CommandQueueEntry:
    await service.execute(
        CommandIntent(
            id=command_id,
            name="set_split",
            params={"split": on, "session_id": "ws-a"},
            source="websocket",
            target=_SPLIT,
            timeout=3.0,
            pending_policy="scoped",
            expected_observations=(_SPLIT,),
        )
    )
    return CommandQueueEntry(
        SetSplit(on),
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
    entry = CommandQueueEntry(SetSplit(True), future=future)

    assert poller._stage_tx_interlocked_entries([entry]) == []  # noqa: SLF001
    assert future.done() is False
    radio.set_split.assert_not_awaited()

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
    radio.set_split.assert_awaited_once_with(True)
    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    radio.set_split.assert_awaited_once()


async def test_supersession_keeps_deadline_and_expiry_wins_over_release() -> None:
    clock = FreshnessClock(start=20.0)
    radio, store, queue = _radio(), StateStore(freshness_clock=clock), CommandQueue()
    store.begin_provider_generation()
    poller = RadioPoller(radio, queue, state_store=store)
    _observe_ptt(store, True, observed_at=clock.now())
    old_future = asyncio.get_running_loop().create_future()
    old = CommandQueueEntry(SetSplit(False), future=old_future)
    assert poller._stage_tx_interlocked_entries([old]) == []  # noqa: SLF001

    clock.advance(2.5)
    _observe_ptt(store, False, observed_at=clock.now())
    new_future = asyncio.get_running_loop().create_future()
    new = CommandQueueEntry(SetSplit(True), future=new_future)
    assert poller._stage_tx_interlocked_entries([new]) == []  # noqa: SLF001
    assert "superseded" in str(old_future.exception())
    radio.set_split.assert_not_awaited()

    clock.advance(0.5)
    _observe_ptt(store, False, observed_at=clock.now())
    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    assert "expired" in str(new_future.exception())
    radio.set_split.assert_not_awaited()


async def test_unknown_deferred_command_fails_closed_without_entering_lane() -> None:
    clock = FreshnessClock(start=10.0)
    radio, store = _radio(), StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    service = _service(clock, store)
    entry = await _lifecycle_entry(service, command_id="unknown", on=True)
    before = service.lifecycle_events()

    assert poller._stage_tx_interlocked_entries([entry]) == [entry]  # noqa: SLF001
    with pytest.raises(CommandError, match="RF state is unknown") as excinfo:
        await poller._execute_queued_entry(entry)  # noqa: SLF001
    assert entry.future is not None
    assert entry.future.exception() is excinfo.value

    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    assert service.lifecycle_events() == before
    radio.set_split.assert_not_awaited()


async def test_fresh_rx_deferred_class_dispatches_immediately_once() -> None:
    poller, radio, store = _poller()
    _observe_ptt(store, False)
    future = asyncio.get_running_loop().create_future()
    entry = CommandQueueEntry(SetSplit(True), future=future)

    assert poller._stage_tx_interlocked_entries([entry]) == [entry]  # noqa: SLF001
    await poller._execute_queued_entry(entry)  # noqa: SLF001

    assert future.result() is None
    radio.set_split.assert_awaited_once_with(True)


def test_authority_approved_commands_do_not_inspect_observed_rf() -> None:
    commands = [
        CommandQueueEntry(SetFreq(14_074_000)),
        CommandQueueEntry(SetMode("USB")),
        CommandQueueEntry(SetBand(5)),
        CommandQueueEntry(SelectVfo("A")),
        CommandQueueEntry(VfoSwap()),
        CommandQueueEntry(VfoEqualize()),
    ]
    poller, _radio, _store = _poller()
    poller._current_rf_state = lambda *_: pytest.fail(  # type: ignore[method-assign] # noqa: SLF001
        "observed RF was inspected"
    )

    for entry in commands:
        poller._enforce_tx_interlock(entry.command)  # type: ignore[arg-type] # noqa: SLF001
    assert poller._stage_tx_interlocked_entries(commands) == commands  # noqa: SLF001


@pytest.mark.asyncio
async def test_managed_non_ptt_bypasses_legacy_observed_rf_and_deferred_lane() -> None:
    radio, store, queue = _radio(), StateStore(), CommandQueue()
    store.begin_provider_generation()
    authority = SimpleNamespace(admit_managed_write=AsyncMock(return_value=True))
    poller = RadioPoller(
        radio,
        queue,
        state_store=store,
        managed_tx_authority=authority,
    )
    poller._current_rf_state = lambda *_: pytest.fail(  # type: ignore[method-assign] # noqa: SLF001
        "managed non-PTT dispatch inspected legacy RF state"
    )
    future = asyncio.get_running_loop().create_future()
    entry = CommandQueueEntry(SetSplit(True), future=future)

    assert poller._stage_tx_interlocked_entries([entry]) == [entry]  # noqa: SLF001
    await poller._execute_queued_entry(entry)  # noqa: SLF001

    assert future.result() is None
    radio.set_split.assert_awaited_once_with(True)
    assert poller._deferred_tx_lane.pending is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_descriptor_intent_uses_bound_authority_before_radio_write() -> None:
    radio, store, queue = _radio(), StateStore(), CommandQueue()
    store.begin_provider_generation()
    _observe_ptt(store, True)
    authority = MagicMock()
    authority.admit_managed_write = AsyncMock(side_effect=(False, True))
    poller = RadioPoller(
        radio,
        queue,
        state_store=store,
        managed_tx_authority=authority,
    )
    intent = bind_command_intent("set_antenna_1", {"on": True}, source="websocket")

    with pytest.raises(CommandError, match="transmit authority"):
        await poller._execute(intent)  # noqa: SLF001
    radio.set_antenna_1.assert_not_awaited()

    await poller._execute(intent)  # noqa: SLF001

    assert authority.admit_managed_write.await_args_list == [
        ((intent,), {}),
        ((intent,), {}),
    ]
    radio.set_antenna_1.assert_awaited_once_with(on=True)


@pytest.mark.parametrize(
    ("entry_kwargs", "message"),
    (
        ({"expires_at_monotonic": 9.0}, "expired"),
        ({"session_id": "gone"}, "session"),
        ({"provider_generation": 6}, "provider generation"),
        ({"connection_generation": "old"}, "connection generation"),
    ),
)
def test_dispatch_currency_rejects_each_captured_causal_mismatch(
    entry_kwargs: dict[str, object], message: str
) -> None:
    entry = CommandQueueEntry(SetFreq(14_074_000), **entry_kwargs)
    with pytest.raises(CommandError, match=message):
        validate_command_queue_entry_currency(
            entry,
            now=10.0,
            provider_generation=7,
            connection_generation="new",
            session_is_live=lambda _session_id: False,
        )


def test_managed_dispatch_currency_rejects_a_missing_connection_stamp() -> None:
    entry = CommandQueueEntry(SetFreq(14_074_000))
    with pytest.raises(CommandError, match="connection generation is missing"):
        validate_command_queue_entry_currency(
            entry,
            now=10.0,
            provider_generation=7,
            connection_generation="current",
            session_is_live=lambda _session_id: True,
            require_connection_generation=True,
        )


async def test_shared_queue_finishes_descriptor_wire_before_positive_tx() -> None:
    log: list[str] = []

    class Admission:
        async def admit_managed_write(self, _intent):
            log.append("write-admitted")
            return True

    class Receipt:
        outcome = SimpleNamespace(value="accepted")

        async def wait_settlement(self):
            log.append("positive-wire-settled")

    async def positive(ready: asyncio.Future[None]):
        await ready
        log.append("positive-admitted")
        return Receipt()

    radio, store, queue = _radio(), StateStore(), CommandQueue()
    store.begin_provider_generation()
    radio.set_antenna_1.side_effect = lambda **_kwargs: log.append("write-finished")
    poller = RadioPoller(
        radio, queue, state_store=store, managed_tx_authority=Admission()
    )
    intent = bind_command_intent("set_antenna_1", {"on": True}, source="websocket")
    ready = asyncio.get_running_loop().create_future()
    submission = asyncio.create_task(positive(ready))
    connection_generation = queue.capture_connection_generation()
    queue.put_ordered(
        intent,
        provider_generation=store.provider_generation,
        connection_generation=connection_generation,
    )
    queue.put_ordered(
        None,
        source="http",
        provider_generation=store.provider_generation,
        connection_generation=connection_generation,
        positive_tx_ready=ready,
        positive_tx_submission=submission,
    )

    while (entry := queue.take_entry()) is not None:
        await poller._execute_queued_entry(entry)  # noqa: SLF001

    assert log == [
        "write-admitted",
        "write-finished",
        "positive-admitted",
        "positive-wire-settled",
    ]


async def test_reverse_positive_then_descriptor_is_refused_before_wire() -> None:
    managed = False

    class Admission:
        async def admit_managed_write(self, _intent):
            return not managed

    class Receipt:
        outcome = SimpleNamespace(value="accepted")

        async def wait_settlement(self):
            nonlocal managed
            managed = True

    async def positive(ready: asyncio.Future[None]):
        await ready
        return Receipt()

    radio, store, queue = _radio(), StateStore(), CommandQueue()
    store.begin_provider_generation()
    poller = RadioPoller(
        radio, queue, state_store=store, managed_tx_authority=Admission()
    )
    ready = asyncio.get_running_loop().create_future()
    connection_generation = queue.capture_connection_generation()
    queue.put_ordered(
        None,
        source="rigctld",
        provider_generation=store.provider_generation,
        connection_generation=connection_generation,
        positive_tx_ready=ready,
        positive_tx_submission=asyncio.create_task(positive(ready)),
    )
    queue.put_ordered(
        bind_command_intent("set_antenna_1", {"on": True}, source="websocket"),
        provider_generation=store.provider_generation,
        connection_generation=connection_generation,
    )

    first = queue.take_entry()
    second = queue.take_entry()
    assert first is not None and second is not None
    await poller._execute_queued_entry(first)  # noqa: SLF001
    with pytest.raises(CommandError, match="transmit authority"):
        await poller._execute_queued_entry(second)  # noqa: SLF001
    radio.set_antenna_1.assert_not_awaited()


async def test_frequency_dispatches_without_entering_the_deferred_lane() -> None:
    clock = FreshnessClock(start=10.0)
    radio, store, queue = _radio(), StateStore(freshness_clock=clock), CommandQueue()
    store.begin_provider_generation()
    poller = RadioPoller(radio, queue, state_store=store)
    _observe_ptt(store, True, observed_at=clock.now())
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
    entry = await _lifecycle_entry(service, command_id="held", on=True)
    _observe_ptt(store, True, observed_at=clock.now())

    assert poller._stage_tx_interlocked_entries([entry]) == []  # noqa: SLF001
    held = service.lifecycle_events()[-1]
    assert (held.command_id, held.state, held.source, held.target) == (
        "held",
        "queued",
        "websocket",
        _SPLIT,
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
    radio.set_split.assert_awaited_once_with(True)


async def test_deferred_replacement_and_expiry_emit_ordered_terminal_truth() -> None:
    clock = FreshnessClock(start=20.0)
    radio, store = _radio(), StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    service = _service(clock, store)
    first = await _lifecycle_entry(service, command_id="first", on=False)
    _observe_ptt(store, True, observed_at=clock.now())
    assert poller._stage_tx_interlocked_entries([first]) == []  # noqa: SLF001

    clock.advance(2.5)
    replacement = await _lifecycle_entry(service, command_id="replacement", on=True)
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
    radio.set_split.assert_not_awaited()


# ── MOR-1884 (MOR-1500 B1-e): the seat guards _execute itself ────────────────


@pytest.mark.parametrize("ptt", (None, True), ids=("unknown", "tx"))
async def test_direct_defer_family_emit_is_refused_at_the_execute_seat(
    ptt: bool | None,
) -> None:
    """An uncommanded internal emit shares the queued commands' seat."""
    poller, radio, store = _poller()
    if ptt is not None:
        _observe_ptt(store, ptt)

    with pytest.raises(CommandError, match="RF state is (unknown|TX)"):
        await poller._execute(SetSplit(on=True))  # noqa: SLF001

    radio.set_split.assert_not_awaited()


async def test_vfo_selection_is_not_observed_rf_gated() -> None:
    poller, _radio, _store = _poller()
    with pytest.raises(CommandError) as dispatched:
        await poller._execute(SelectVfo(vfo="A"))  # noqa: SLF001
    assert "RF state" not in str(dispatched.value)
    assert "VFO selection" in str(dispatched.value)


class ConnectVfoRadio(CoreRadio):
    connected = True
    radio_ready = True
    control_connected = True

    def __init__(self, *, selected: str = "B") -> None:
        from serial_stub import DeterministicSerialCivLink

        super().__init__("127.0.0.1", model="IC-7300")
        self.wire = DeterministicSerialCivLink()
        self.selected = selected
        self.ack = asyncio.Event()
        self.ack.set()
        self.reject = False
        self.refetches = 0

    def _check_connected(self) -> None:
        pass

    async def _send_civ_expect(self, frame: bytes, **kwargs: object) -> object:
        from rigplane.core.civ import CivFrame
        from rigplane.core.types import bcd_encode

        await self.wire.send(frame)
        command, data = frame[4], frame[5:-1]
        if command == 0x07:
            await self.ack.wait()
            if self.reject:
                return CivFrame(0xE0, 0x94, 0xFA)
            self.selected = "A" if data == b"\x00" else "B"
            return CivFrame(0xE0, 0x94, 0xFB)
        slot = self.selected if data[0] == 0 else ("B" if self.selected == "A" else "A")
        if command == 0x25:
            payload = data + bcd_encode(14_200_000 if slot == "A" else 7_100_000)
        elif command == 0x26:
            payload = data + (b"\x01\x00\x01" if slot == "A" else b"\x00\x00\x02")
        else:
            raise AssertionError(f"Unexpected write/read: {frame.hex()}")
        return CivFrame(0xE0, 0x94, command, data=payload)

    async def _fetch_initial_state(self) -> None:
        self.refetches += 1
        _observe_ptt(self.state_store, False)


def connect_vfo_poller(
    *, selected: str = "B"
) -> tuple[RadioPoller, ConnectVfoRadio, StateStore]:
    radio = ConnectVfoRadio(selected=selected)
    store = radio.state_store
    store.begin_provider_generation()
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    return poller, radio, store


@pytest.mark.parametrize("selected", ("A", "B"))
async def test_application_connect_selects_a_once_and_binds_real_wire(
    selected: str,
) -> None:
    poller, radio, store = connect_vfo_poller(selected=selected)
    _observe_ptt(store, False)
    await poller.select_vfo_a_on_connect(read_only=False)
    await poller.select_vfo_a_on_connect(read_only=False)
    await poller._send_query()
    assert radio.wire.sent_frames == [
        bytes.fromhex(frame)
        for frame in (
            "fefe94e00700fd",
            "fefe94e02500fd",
            "fefe94e02600fd",
            "fefe94e02501fd",
            "fefe94e02601fd",
        )
    ]
    assert radio.selected == "A"
    snapshot = store.snapshot()
    assert snapshot.field(FieldPath.active_slot("0")).value == "A"
    assert (
        snapshot.field(FieldPath.vfo_slot("0", "A", "freq_mode", "freq_hz")).value
        == 14_200_000
    )
    assert (
        snapshot.field(FieldPath.vfo_slot("0", "B", "freq_mode", "freq_hz")).value
        == 7_100_000
    )


@pytest.mark.parametrize(
    "state", ("read_only", "tx", "unknown", "stale", "old_generation")
)
async def test_application_connect_refuses_without_fresh_rx(state: str) -> None:
    poller, radio, store = connect_vfo_poller()
    if state != "unknown":
        _observe_ptt(
            store,
            state == "tx",
            observed_at=time.monotonic() - (5 if state == "stale" else 0),
        )
    if state == "old_generation":
        store.begin_provider_generation()
    if state in ("read_only", "tx"):
        radio.send_civ = AsyncMock()
    await poller.select_vfo_a_on_connect(read_only=state == "read_only")
    if state in ("read_only", "tx"):
        radio.send_civ.assert_not_awaited()
    _observe_ptt(store, False)
    await poller._send_query()
    assert radio.wire.sent_frames == []
    assert str(FieldPath.active_slot("0")) not in store.snapshot().as_dict()


@pytest.mark.parametrize(
    "overrides",
    ({"receiver_count": 2}, {"vfo_scheme": "main_sub"}, {"vfo_readback": "absolute"}),
)
async def test_application_connect_leaves_other_topologies_alone(
    overrides: dict,
) -> None:
    from dataclasses import replace

    poller, radio, store = connect_vfo_poller()
    poller._profile = replace(poller._profile, **overrides)
    radio.send_civ = AsyncMock()
    _observe_ptt(store, False)
    await poller.select_vfo_a_on_connect(read_only=False)
    radio.send_civ.assert_not_awaited()
    assert radio.wire.sent_frames == []


@pytest.mark.parametrize("reject", (False, True))
async def test_application_connect_ack_is_honest_and_inflight_calls_do_not_resend(
    reject: bool,
) -> None:
    poller, radio, store = connect_vfo_poller()
    _observe_ptt(store, False)
    radio.ack.clear()
    radio.reject = reject
    task = asyncio.create_task(poller.select_vfo_a_on_connect(read_only=False))
    await asyncio.sleep(0)
    await poller.select_vfo_a_on_connect(read_only=False)
    assert str(FieldPath.active_slot("0")) not in store.snapshot().as_dict()
    assert len(radio.wire.sent_frames) == 1
    radio.ack.set()
    await task
    await poller.select_vfo_a_on_connect(read_only=False)
    assert sum(frame[4] == 0x07 for frame in radio.wire.sent_frames) == 1
    assert (str(FieldPath.active_slot("0")) in store.snapshot().as_dict()) is not reject


async def test_connection_reset_requires_new_rx_and_allows_one_new_select() -> None:
    poller, radio, store = connect_vfo_poller()
    _observe_ptt(store, False)
    await poller.select_vfo_a_on_connect(read_only=False)
    radio._civ_epoch += 1
    poller.reset_vfo_session(connection_recovery=True)
    await poller.select_vfo_a_on_connect(read_only=False)
    assert len(radio.wire.sent_frames) == 5
    # An unsafe connection attempt has no automatic retry, even after RX arrives.
    _observe_ptt(store, False)
    await poller.select_vfo_a_on_connect(read_only=False)
    assert len(radio.wire.sent_frames) == 5
    radio._civ_epoch += 1
    poller.reset_vfo_session(connection_recovery=True)
    _observe_ptt(store, False)
    await poller.select_vfo_a_on_connect(read_only=False)
    poller.reset_vfo_session(connection_recovery=True)
    await poller.select_vfo_a_on_connect(read_only=False)
    assert len(radio.wire.sent_frames) == 10
    assert store.snapshot().field(FieldPath.active_slot("0")).value == "A"


async def observe_connect_ptt_read(radio: ConnectVfoRadio, value: bool | None) -> None:
    from rigplane.core.civ import CivFrame
    from rigplane.commands import build_civ_frame

    await radio.wire.send(build_civ_frame(0x94, 0xE0, 0x1C, sub=0))
    if value is not None:
        await radio._civ_runtime._route_civ_frame(
            CivFrame(0xE0, 0x94, 0x1C, sub=0, data=bytes([int(value)])),
            generation=radio._civ_epoch,
            store_provider_generation=radio.state_store.provider_generation,
        )


@pytest.mark.parametrize("value", (False, True, None))
@pytest.mark.parametrize("stale_ptt", (False, True))
async def test_connect_rechecks_genuine_ptt_after_one_bounded_read(
    value: bool | None,
    stale_ptt: bool,
) -> None:
    poller, radio, store = connect_vfo_poller()
    _observe_ptt(store, stale_ptt, observed_at=time.monotonic() - 5.0)

    async def read(command: int, **kwargs: object) -> bool:
        assert command == 0x1C
        assert kwargs == {"sub": 0, "data": b"", "wait_response": True}
        await observe_connect_ptt_read(radio, value)
        return False

    radio.send_civ = AsyncMock(side_effect=read)
    await poller.select_vfo_a_on_connect(read_only=False)
    await poller.select_vfo_a_on_connect(read_only=False)
    radio.send_civ.assert_awaited_once()
    assert radio.wire.sent_frames[0] == bytes.fromhex("fefe94e01c00fd")
    assert len(radio.wire.sent_frames) == (6 if value is False else 1)
    assert (str(FieldPath.active_slot("0")) in store.snapshot().as_dict()) is (
        value is False
    )


@pytest.mark.parametrize("outcome", ("timeout", "new_generation"))
async def test_connect_read_timeout_or_epoch_change_never_selects(outcome: str) -> None:
    poller, radio, store = connect_vfo_poller()
    entered, release = asyncio.Event(), asyncio.Event()
    radio.send_civ = AsyncMock()

    async def read(*args: object, **kwargs: object) -> None:
        entered.set()
        if outcome == "timeout":
            await asyncio.Event().wait()
        await release.wait()
        radio._civ_epoch += 1
        store.begin_provider_generation()
        await observe_connect_ptt_read(radio, False)

    radio.send_civ.side_effect = read
    poller._VFO_CONNECT_PTT_TIMEOUT = 0.01
    task = asyncio.create_task(poller.select_vfo_a_on_connect(read_only=False))
    await asyncio.wait_for(entered.wait(), timeout=0.5)
    await poller.select_vfo_a_on_connect(read_only=False)
    release.set()
    await task
    radio.send_civ.assert_awaited_once()
    assert all(frame[4] != 0x07 for frame in radio.wire.sent_frames)
    assert str(FieldPath.active_slot("0")) not in store.snapshot().as_dict()


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


async def test_managed_typed_ptt_on_fails_before_raw_write_or_legacy_timer() -> None:
    radio, store, queue = _radio(), StateStore(), CommandQueue()
    store.begin_provider_generation()
    _observe_ptt(store, False)
    authority = SimpleNamespace(admit_managed_write=AsyncMock(return_value=True))
    poller = RadioPoller(
        radio,
        queue,
        state_store=store,
        managed_tx_authority=authority,
    )
    poller._arm_max_key_down = MagicMock()  # type: ignore[method-assign] # noqa: SLF001

    with pytest.raises(CommandError, match="positive TX queue submission"):
        await poller._execute(PttOn())  # noqa: SLF001

    radio.set_ptt.assert_not_awaited()
    poller._arm_max_key_down.assert_not_called()  # type: ignore[attr-defined] # noqa: SLF001


async def test_managed_typed_ptt_off_remains_unconditionally_attemptable() -> None:
    radio, store, queue = _radio(), StateStore(), CommandQueue()
    store.begin_provider_generation()
    authority = SimpleNamespace(admit_managed_write=AsyncMock(return_value=True))
    poller = RadioPoller(
        radio,
        queue,
        state_store=store,
        managed_tx_authority=authority,
    )

    await poller._execute(PttOff())  # noqa: SLF001

    radio.set_ptt.assert_awaited_once_with(False)


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
    assert entry.future is not None
    assert entry.future.exception() is excinfo.value
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
