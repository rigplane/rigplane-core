"""MOR-3633: repeater writes in the TX interlock and the PTT-on TX-target guard.

Two behaviours, one policy module:

* Every repeater-related write (shift, tone/TSQL toggles, tone/TSQL
  frequencies) is DEFER-classified while the radio is keyed and passes in
  known RX — the same single-slot deferred lane the memory and VFO-topology
  families already use.  Descriptor-backed intents (``set_repeater_shift``
  and friends, WS/HTTP) classify directly by intent name through the one
  table in ``tx_interlock`` (the descriptor is the only name source for
  migrated commands, so no typed command exists for them); the rigctld
  ``set_func TONE``/``TSQL`` tokens resolve to the same names.
* PTT-on is refused while the TX side's frequency is unknown, resolved
  through the one ``global.tx_state.tx_target`` observation
  (``core/tx_target.py``); the refusal names the receiver that would
  transmit when the target knows it.

Fakes, never hardware: fake radios and a fake CI-V peer against the real
HTTP/WS listener, ``RadioPoller`` and ``ManagedTxAuthority``, and a real
rigctld TCP server.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from rigplane._bounded_queue import BoundedQueue
from rigplane.core.command_dispatch import (
    bind_command_intent,
    command_descriptor,
    execute_command_intent,
)
from rigplane.core.command_service import CommandExecutionResult, CommandService
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, FreshnessState, StateStore
from rigplane.core.tx_target import KnownTxTarget, UnknownTxTarget
from rigplane.exceptions import CommandError
from rigplane.profiles import RadioProfile, resolve_radio_profile
from rigplane.runtime import _poller_types
from rigplane.runtime._poller_types import (
    PttOff,
    PttOn,
    SetRepeaterTone,
    SetRepeaterTsql,
    execute_command_queue_entry,
)
from rigplane.runtime._state_queries import profile_publishes_tx_target
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority, RadioTxOrigin
from rigplane.runtime.managed_tx_state import (
    ActuationResult,
    ManagedTxIntentKind,
    ManagedTxOutcome,
)
from rigplane.runtime.tx_interlock import (
    RfState,
    TxInterlockCommandFamily,
    TxInterlockDisposition,
    TxInterlockRefusal,
    classify_tx_interlock,
    evaluate_tx_interlock,
    get_tx_interlock_command_family_metadata,
    ptt_on_tx_target_refusal,
    tx_frequency_unknown_refusal,
)
from rigplane.web.radio_poller import CommandQueue, CommandQueueEntry, RadioPoller
from rigplane.web.server import WebConfig, WebServer
from support.tx_target import seed_tx_target_value
from test_controller_sessions import radio_tx_authority
from test_managed_tx_authority import authority
from test_rigctld_managed_ingress_scheduling import (  # noqa: F401
    _clear_release_debt,
    _key,
    _replies,
    _send,
)
from test_rigctld_managed_ingress_scheduling import managed as managed_rig  # noqa: F401
from test_rigctld_wsjtx_split_mk2 import _exchange, _peer
from test_web_server import (
    _read_http_response,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)

_TX_TARGET = FieldPath.global_("tx_state", "tx_target")
_PTT = FieldPath.global_("tx_state", "ptt")

# Descriptor-backed repeater writes (WS/HTTP) and the rigctld ``set_func``
# tokens that stand for two of them; every ingress must reach one verdict.
_DESCRIPTOR_INTENTS = (
    ("set_repeater_shift", {"direction": 1}),
    ("set_repeater_tone", {"on": True}),
    ("set_repeater_tsql", {"on": True}),
    ("set_tone_freq", {"freq": 8850}),
    ("set_tsql_freq", {"freq": 8850}),
)
_REPEATER_NAMES = tuple(name for name, _params in _DESCRIPTOR_INTENTS)
_RIGCTLD_TOKENS = (SetRepeaterTone(on=True), SetRepeaterTsql(on=True))


def _intent(name: str, params: dict) -> CommandIntent:
    return bind_command_intent(name, params, source="test")


_REPEATER_COMMANDS = tuple(
    _intent(name, params) for name, params in _DESCRIPTOR_INTENTS
)
_REPEATER_COMMANDS += _RIGCTLD_TOKENS
_REPEATER_IDS = (*_REPEATER_NAMES, "token-set_func-TONE", "token-set_func-TSQL")


def test_ptt_off_passes_in_every_rf_state_first() -> None:
    """The safety exit stays open before any repeater row is consulted."""

    for rf_state in (RfState.RX, RfState.TX, RfState.UNKNOWN):
        decision = evaluate_tx_interlock(PttOff(), rf_state=rf_state)
        assert decision.disposition is TxInterlockDisposition.ALWAYS_PASS
        assert decision.allowed is True


@pytest.mark.parametrize("command", _REPEATER_COMMANDS, ids=_REPEATER_IDS)
def test_repeater_writes_defer_while_keyed_and_pass_in_known_rx(command) -> None:
    decision_tx = evaluate_tx_interlock(command, rf_state=RfState.TX)
    assert decision_tx.disposition is TxInterlockDisposition.DEFER
    assert decision_tx.allowed is False

    decision_unknown = evaluate_tx_interlock(command, rf_state=RfState.UNKNOWN)
    assert decision_unknown.disposition is TxInterlockDisposition.DEFER
    assert decision_unknown.allowed is False

    decision_rx = evaluate_tx_interlock(command, rf_state=RfState.RX)
    assert decision_rx.disposition is TxInterlockDisposition.DEFER
    assert decision_rx.allowed is True

    assert classify_tx_interlock(command) is TxInterlockDisposition.DEFER


@pytest.mark.parametrize("command", _REPEATER_COMMANDS, ids=_REPEATER_IDS)
def test_repeater_writes_carry_repeater_family_metadata(command) -> None:
    metadata = get_tx_interlock_command_family_metadata(command)

    assert metadata is not None
    assert metadata.family is TxInterlockCommandFamily.REPEATER
    assert metadata.base_disposition is TxInterlockDisposition.DEFER


@pytest.mark.parametrize("name", _REPEATER_NAMES)
def test_repeater_names_are_canonical_descriptor_names(name: str) -> None:
    """The classification table is keyed by descriptor name, never an alias.

    A bound intent carries the canonical descriptor name; this pins that the
    five table names are exactly that, so classification by ``intent.name``
    cannot drift from the descriptor that owns the name.
    """

    descriptor = command_descriptor(name)

    assert descriptor is not None
    assert descriptor.name == name


def test_descriptor_is_the_only_name_source_for_repeater_commands() -> None:
    """No typed command exists for a migrated repeater write."""

    for typed_name in ("SetRepeaterShift", "SetToneFreq", "SetTsqlFreq"):
        assert not hasattr(_poller_types, typed_name)


def test_classification_leaves_non_repeater_commands_alone() -> None:
    """Only the table's names defer; everything else keeps its verdict."""

    level = _intent("set_af_level", {"level": 100})

    assert classify_tx_interlock(level) is TxInterlockDisposition.TX_SAFE
    assert get_tx_interlock_command_family_metadata(level) is None
    # A bare string, an arbitrary object and None are not intents.
    for not_an_intent in ("set_repeater_shift", object(), None):
        assert classify_tx_interlock(not_an_intent) is TxInterlockDisposition.TX_SAFE
        assert get_tx_interlock_command_family_metadata(not_an_intent) is None


# ---------------------------------------------------------------------------
# PTT-on guard: the TX side's frequency must be known (MOR-3633)
# ---------------------------------------------------------------------------


def _tx_target_store(
    value: object,
    *,
    max_age: float = 3.0,
    advance: float = 0.0,
) -> tuple[StateStore, FreshnessClock]:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    seed_tx_target_value(store, value, timestamp_monotonic=clock.now(), max_age=max_age)
    if advance:
        clock.advance(advance)
    return store, clock


def test_ptt_on_is_allowed_with_a_fresh_known_tx_target_frequency() -> None:
    store, _clock = _tx_target_store(
        KnownTxTarget(receiver="SUB", slot="A", frequency_hz=145_500_000)
    )

    assert ptt_on_tx_target_refusal(store.snapshot(), tx_target_supported=True) is None


@pytest.mark.parametrize(
    ("value", "expected_reason"),
    (
        (KnownTxTarget(receiver="SUB", slot="A", frequency_hz=None), None),
        (UnknownTxTarget(reason="not-observed"), "not-observed"),
        (UnknownTxTarget(reason="stale"), "stale"),
        (UnknownTxTarget(reason="unsupported"), "unsupported"),
        (UnknownTxTarget(reason="contradiction"), "contradiction"),
    ),
    ids=(
        "known-target-without-frequency",
        "unknown-not-observed",
        "unknown-stale",
        "unknown-unsupported",
        "unknown-contradiction",
    ),
)
def test_ptt_on_is_refused_when_the_tx_frequency_is_unknown(
    value: object, expected_reason: str | None
) -> None:
    store, _clock = _tx_target_store(value)

    refusal = ptt_on_tx_target_refusal(store.snapshot(), tx_target_supported=True)

    assert refusal is not None
    assert refusal.reason_code == "ptt_tx_frequency_unknown"
    reason = expected_reason if expected_reason is not None else "not-observed"
    assert refusal.details["txTargetReason"] == reason
    receiver = value.receiver if isinstance(value, KnownTxTarget) else None
    assert refusal.details.get("receiver") == receiver
    if receiver is not None:
        assert receiver in str(refusal)


def test_ptt_on_refusal_names_the_sub_receiver_that_would_transmit() -> None:
    store, _clock = _tx_target_store(
        KnownTxTarget(receiver="SUB", slot="A", frequency_hz=None)
    )

    refusal = ptt_on_tx_target_refusal(store.snapshot(), tx_target_supported=True)

    assert refusal is not None
    assert refusal.details["receiver"] == "SUB"
    assert "SUB" in str(refusal)


def test_ptt_on_is_refused_without_any_tx_target_observation() -> None:
    store = StateStore(freshness_clock=FreshnessClock(start=10.0))

    refusal = ptt_on_tx_target_refusal(store.snapshot(), tx_target_supported=True)

    assert refusal is not None
    assert refusal.reason_code == "ptt_tx_frequency_unknown"
    assert refusal.details["txTargetReason"] == "not-observed"


def test_ptt_on_is_refused_when_the_tx_target_observation_is_stale() -> None:
    store, clock = _tx_target_store(
        KnownTxTarget(receiver="MAIN", slot=None, frequency_hz=7_100_000),
        advance=3.1,
    )
    snapshot = store.snapshot()
    assert (
        snapshot.field(_TX_TARGET).freshness is FreshnessState.FRESH
    )  # the TTL recompute, not the flag, catches it

    refusal = ptt_on_tx_target_refusal(snapshot, tx_target_supported=True)

    assert refusal is not None
    assert refusal.details["txTargetReason"] == "stale"


def test_ptt_on_is_refused_on_a_foreign_provider_generation() -> None:
    store, _clock = _tx_target_store(
        KnownTxTarget(receiver="MAIN", slot=None, frequency_hz=7_100_000)
    )
    snapshot = store.snapshot()
    field = replace(snapshot.field(_TX_TARGET), provider_generation=1)
    shifted = replace(snapshot, fields=(field,), provider_generation=2)

    refusal = ptt_on_tx_target_refusal(shifted, tx_target_supported=True)

    assert refusal is not None
    assert refusal.details["txTargetReason"] == "stale"


def test_ptt_on_guard_skips_radios_that_never_publish_tx_target() -> None:
    """A profile that cannot report the target (IC-9700) keeps keying."""

    store = StateStore(freshness_clock=FreshnessClock(start=10.0))

    assert ptt_on_tx_target_refusal(store.snapshot(), tx_target_supported=False) is None


def test_tx_frequency_value_core_answers_without_a_snapshot() -> None:
    """The value core serves seats that retain the observation, not a store."""

    assert (
        tx_frequency_unknown_refusal(
            KnownTxTarget(receiver="MAIN", slot="A", frequency_hz=7_100_000)
        )
        is None
    )

    missing = tx_frequency_unknown_refusal(None)
    assert missing is not None
    assert missing.reason_code == "ptt_tx_frequency_unknown"
    assert missing.details == {"txTargetReason": "not-observed"}

    contradictory = tx_frequency_unknown_refusal("not-a-tx-target")
    assert contradictory is not None
    assert contradictory.details == {"txTargetReason": "contradiction"}


@pytest.mark.parametrize(
    ("model", "expected"),
    (
        ("IC-705", True),  # derived (selected_unselected readback)
        ("IC-7300", True),  # derived
        ("IC-7300MK2", True),  # derived
        ("IC-7610", True),  # native 1C 03 read
        ("FTX-1", True),  # native FT read
        ("IC-9700", False),  # no readback, no declared capability
        ("TX-500", False),  # no VFO readback to derive a target from
        ("X6100", False),
        ("X6200", False),
    ),
)
def test_profile_publishes_tx_target_rows(model: str, expected: bool) -> None:
    profile = resolve_radio_profile(model=model)

    assert isinstance(profile, RadioProfile)
    assert profile_publishes_tx_target(profile) is expected


def test_profile_publishes_tx_target_answers_false_without_a_profile() -> None:
    """Support must be proven from profile data; doubles never claim it."""

    assert profile_publishes_tx_target(None) is False  # type: ignore[arg-type]
    assert profile_publishes_tx_target(MagicMock()) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Icom seat: the shared deferred lane holds repeater writes while keyed
# ---------------------------------------------------------------------------


def _radio(model: str = "IC-705") -> SimpleNamespace:
    return SimpleNamespace(
        _civ_epoch=1,
        profile=resolve_radio_profile(model=model),
        capabilities=set(),
        send_civ=AsyncMock(),
        set_ptt=AsyncMock(),
        **{name: AsyncMock() for name in _REPEATER_NAMES},
    )


def _observe_ptt(
    store: StateStore,
    value: bool,
    *,
    observed_at: float,
    generation: int | None = None,
) -> None:
    store.apply(
        Observation(
            path=_PTT,
            value=value,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=observed_at,
            max_age=1.0,
            provider_generation=(
                store.provider_generation if generation is None else generation
            ),
        )
    )


def _poller(
    model: str = "IC-705",
) -> tuple[RadioPoller, SimpleNamespace, StateStore, FreshnessClock]:
    clock = FreshnessClock(start=10.0)
    radio = _radio(model)
    store = StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    return (
        RadioPoller(radio, CommandQueue(), state_store=store),
        radio,
        store,
        clock,
    )


@pytest.mark.parametrize("command", _REPEATER_COMMANDS, ids=_REPEATER_IDS)
def test_repeater_writes_are_held_while_keyed_and_ready_in_quiet_rx(
    command: object,
) -> None:
    """PTT flipped against a fake IC-705: keyed holds, quiet RX readies.

    Queued descriptor intents (WS/HTTP) and the rigctld tokens share the
    memory family's single-slot lane; staging executes nothing.
    """

    poller, radio, store, clock = _poller()
    _observe_ptt(store, True, observed_at=clock.now())  # PTT flipped: keyed
    entry = CommandQueueEntry(command)

    # The entry stays out of the ready list while the radio is keyed.
    assert poller._stage_tx_interlocked_entries([entry]) == []  # noqa: SLF001
    radio.set_ptt.assert_not_awaited()

    # One second of quiet known RX readies it.
    clock.advance(0.1)
    _observe_ptt(store, False, observed_at=clock.now())
    assert poller._stage_tx_interlocked_entries([]) == []  # noqa: SLF001
    clock.advance(1.0)
    _observe_ptt(store, False, observed_at=clock.now())
    assert poller._stage_tx_interlocked_entries([]) == [entry]  # noqa: SLF001
    radio.set_ptt.assert_not_awaited()
    radio.send_civ.assert_not_awaited()


@pytest.mark.parametrize("command", _REPEATER_COMMANDS, ids=_REPEATER_IDS)
def test_repeater_execute_seat_fails_closed_on_unknown_rf(command: object) -> None:
    """Unknown RF and TX refuse a repeater write; known RX does not."""

    poller, _radio, store, clock = _poller()

    with pytest.raises(TxInterlockRefusal) as unknown:
        poller._enforce_tx_interlock(command)  # noqa: SLF001
    assert unknown.value.reason_code == "rf_state_unknown"

    _observe_ptt(store, True, observed_at=clock.now())
    with pytest.raises(TxInterlockRefusal) as keyed:
        poller._enforce_tx_interlock(command)  # noqa: SLF001
    assert keyed.value.reason_code == "radio_transmitting"

    _observe_ptt(store, False, observed_at=clock.now())
    assert poller._enforce_tx_interlock(command) is None  # noqa: SLF001


@pytest.mark.parametrize(("name", "params"), _DESCRIPTOR_INTENTS)
async def test_descriptor_repeater_write_reaches_the_radio_only_in_known_rx(
    name: str, params: dict
) -> None:
    """The WS/HTTP descriptor path is gated at the execute seat too.

    Refused while the RF state is unknown or keyed — before any Radio
    method runs — and executed exactly once in known RX.  Nothing else
    keys the radio on any branch.
    """

    poller, radio, store, clock = _poller()
    intent = _intent(name, params)
    write = getattr(radio, name)

    with pytest.raises(TxInterlockRefusal) as unknown:
        await poller._execute(intent)  # noqa: SLF001
    assert unknown.value.reason_code == "rf_state_unknown"

    _observe_ptt(store, True, observed_at=clock.now())
    with pytest.raises(TxInterlockRefusal) as keyed:
        await poller._execute(intent)  # noqa: SLF001
    assert keyed.value.reason_code == "radio_transmitting"
    write.assert_not_awaited()

    _observe_ptt(store, False, observed_at=clock.now())
    await poller._execute(intent)  # noqa: SLF001
    write.assert_awaited_once()
    radio.set_ptt.assert_not_awaited()
    radio.send_civ.assert_not_awaited()


@pytest.mark.parametrize("model", ("IC-705", "IC-7300", "IC-7610"))
def test_ptt_on_is_refused_without_a_tx_target_observation(model: str) -> None:
    poller, radio, store, clock = _poller(model)
    _observe_ptt(store, False, observed_at=clock.now())

    with pytest.raises(TxInterlockRefusal) as excinfo:
        poller._enforce_tx_interlock(PttOn())  # noqa: SLF001

    assert excinfo.value.reason_code == "ptt_tx_frequency_unknown"
    assert excinfo.value.details["txTargetReason"] == "not-observed"
    assert "receiver" not in excinfo.value.details
    radio.set_ptt.assert_not_awaited()


def test_ptt_on_passes_with_a_fresh_known_tx_target() -> None:
    poller, _radio, store, clock = _poller()
    _observe_ptt(store, False, observed_at=clock.now())
    seed_tx_target_value(
        store,
        KnownTxTarget(receiver="MAIN", slot="A", frequency_hz=7_100_000),
        timestamp_monotonic=clock.now(),
        max_age=3.0,
    )

    assert poller._enforce_tx_interlock(PttOn()) is None  # noqa: SLF001


def test_ptt_on_refusal_names_the_receiver_that_would_transmit() -> None:
    poller, radio, store, clock = _poller()
    _observe_ptt(store, False, observed_at=clock.now())
    seed_tx_target_value(
        store,
        KnownTxTarget(receiver="SUB", slot="A", frequency_hz=None),
        timestamp_monotonic=clock.now(),
        max_age=3.0,
    )

    with pytest.raises(TxInterlockRefusal) as excinfo:
        poller._enforce_tx_interlock(PttOn())  # noqa: SLF001

    assert excinfo.value.details["receiver"] == "SUB"
    assert "SUB" in str(excinfo.value)
    radio.set_ptt.assert_not_awaited()


@pytest.mark.parametrize("model", ("IC-9700", "TX-500", "X6100", "X6200"))
def test_ptt_on_passes_without_a_target_on_profiles_that_cannot_publish(
    model: str,
) -> None:
    """A profile that never publishes tx_target must not be bricked by the guard.

    The applicability is profile data (``profile_publishes_tx_target``), never a
    model name in code: the same poller seat that refuses the publishing
    profiles above lets these key.
    """

    poller, _radio, store, clock = _poller(model)
    assert profile_publishes_tx_target(_radio.profile) is False
    _observe_ptt(store, False, observed_at=clock.now())

    assert poller._enforce_tx_interlock(PttOn()) is None  # noqa: SLF001


@pytest.mark.parametrize("model", ("IC-705", "IC-7300", "IC-7300MK2", "IC-7610"))
@pytest.mark.parametrize(
    ("value", "advance", "reason"),
    (
        (UnknownTxTarget(reason="contradiction"), 0.0, "contradiction"),
        (UnknownTxTarget(reason="unsupported"), 0.0, "unsupported"),
        (UnknownTxTarget(reason="stale"), 0.0, "stale"),
        (
            KnownTxTarget(receiver="MAIN", slot="A", frequency_hz=7_100_000),
            3.5,
            "stale",
        ),
    ),
    ids=("contradiction", "unsupported", "unknown-stale", "aged-past-ttl"),
)
def test_ptt_on_is_refused_for_an_unknown_or_aged_target(
    model: str, value: object, advance: float, reason: str
) -> None:
    poller, radio, store, clock = _poller(model)
    assert profile_publishes_tx_target(radio.profile) is True
    seed_tx_target_value(store, value, timestamp_monotonic=clock.now(), max_age=3.0)
    clock.advance(advance)
    _observe_ptt(store, False, observed_at=clock.now())  # RF is known RX

    with pytest.raises(TxInterlockRefusal) as excinfo:
        poller._enforce_tx_interlock(PttOn())  # noqa: SLF001

    assert excinfo.value.reason_code == "ptt_tx_frequency_unknown"
    assert excinfo.value.details["txTargetReason"] == reason
    radio.set_ptt.assert_not_awaited()
    radio.send_civ.assert_not_awaited()


def test_keyed_ptt_on_keeps_the_rf_refusal_first() -> None:
    """The TX-target guard runs after the RF verdict, never ahead of it."""

    poller, _radio, store, clock = _poller()
    _observe_ptt(store, True, observed_at=clock.now())

    with pytest.raises(TxInterlockRefusal) as excinfo:
        poller._enforce_tx_interlock(PttOn())  # noqa: SLF001

    assert excinfo.value.reason_code == "radio_transmitting"


def _service(clock: FreshnessClock, store: StateStore) -> CommandService:
    executor = SimpleNamespace(execute=AsyncMock(return_value=CommandExecutionResult()))
    return CommandService(executor=executor, state_store=store, clock=clock.now)


async def test_refused_ptt_on_reports_the_tx_receiver_in_lifecycle_details() -> None:
    clock = FreshnessClock(start=10.0)
    radio, store = _radio(), StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    _observe_ptt(store, False, observed_at=clock.now())
    seed_tx_target_value(
        store,
        KnownTxTarget(receiver="SUB", slot="A", frequency_hz=None),
        timestamp_monotonic=clock.now(),
        max_age=3.0,
    )
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
        "reason": "ptt_tx_frequency_unknown",
        "txTargetReason": "not-observed",
        "receiver": "SUB",
    }
    assert "SUB" in (event.message or "")
    radio.set_ptt.assert_not_awaited()


# ---------------------------------------------------------------------------
# Dispatch seat: managed admission consults the one interlock policy
# ---------------------------------------------------------------------------


async def _finish(managed: ManagedTxAuthority) -> None:
    state = (await managed.snapshot()).state
    if state.release_required or state.intent.kind is not ManagedTxIntentKind.RX:
        await managed.force_off()
    await managed.close()


@pytest.mark.parametrize(("name", "params"), _DESCRIPTOR_INTENTS)
async def test_managed_transmit_refuses_repeater_writes(
    name: str, params: dict
) -> None:
    managed, _, _, _, _, _ = authority()
    try:
        assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
        intent = _intent(name, params)
        before = intent.to_dict()

        assert await managed.admit_managed_write(intent) is False
        assert intent.to_dict() == before
    finally:
        await _finish(managed)


@pytest.mark.parametrize(("name", "params"), _DESCRIPTOR_INTENTS)
async def test_managed_receive_admits_repeater_writes(name: str, params: dict) -> None:
    managed, _, _, _, _, _ = authority()
    try:
        intent = _intent(name, params)

        assert await managed.admit_managed_write(intent) is True
    finally:
        await _finish(managed)


async def test_managed_transmit_still_admits_non_repeater_writes() -> None:
    managed, _, _, _, _, _ = authority()
    try:
        assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
        intent = bind_command_intent("set_af_level", {"level": 100}, source="test")

        assert await managed.admit_managed_write(intent) is True
    finally:
        await _finish(managed)


async def test_dispatch_seam_refuses_shift_before_the_radio_write() -> None:
    """bind → admit → Radio: the refusal lands before any wire traffic."""

    managed, _, _, _, _, _ = authority()
    radio = SimpleNamespace(set_repeater_shift=AsyncMock())
    try:
        assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
        intent = bind_command_intent(
            "set_repeater_shift", {"direction": 1}, source="test"
        )

        with pytest.raises(CommandError, match="transmit authority"):
            await execute_command_intent(radio, intent, managed_tx_authority=managed)
        radio.set_repeater_shift.assert_not_awaited()
    finally:
        await _finish(managed)


async def test_dispatch_seam_executes_shift_outside_transmit() -> None:
    managed, _, _, _, _, _ = authority()
    radio = SimpleNamespace(set_repeater_shift=AsyncMock())
    try:
        intent = bind_command_intent(
            "set_repeater_shift", {"direction": 1}, source="test"
        )

        await execute_command_intent(radio, intent, managed_tx_authority=managed)

        assert radio.set_repeater_shift.await_args_list == [
            call(direction=1, receiver=0)
        ]
    finally:
        await _finish(managed)


# ---------------------------------------------------------------------------
# Managed admission: held, owed and radio-reported TX all refuse repeater writes
# ---------------------------------------------------------------------------


def _release_outstanding_state(kind: str):
    """A managed authority whose intent is RX while a release is still owed."""

    if kind == "offline-force-off":
        return authority(generation=None)
    managed, clock, wakeup, store, fence, lane = authority()
    lane.results.extend((ActuationResult.ACCEPTED, ActuationResult.REJECTED))
    return managed, clock, wakeup, store, fence, lane


@pytest.mark.parametrize("kind", ("offline-force-off", "rejected-unkey"))
@pytest.mark.parametrize(("name", "params"), _DESCRIPTOR_INTENTS)
async def test_managed_outstanding_release_refuses_repeater_writes(
    kind: str, name: str, params: dict
) -> None:
    """A rejected unkey leaves the intent RX but the transmitter maybe keyed."""

    managed, _clock, _wakeup, _store, _fence, _lane = _release_outstanding_state(kind)
    try:
        if kind == "rejected-unkey":
            assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
        await managed.force_off()
        state = (await managed.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
        assert state.release_required

        assert await managed.admit_managed_write(_intent(name, params)) is False
        # A write that cannot move the TX frequency stays admitted.
        level = bind_command_intent("set_af_level", {"level": 100}, source="test")
        assert await managed.admit_managed_write(level) is True
    finally:
        if kind == "offline-force-off":
            await managed.provider_available(8)
        await _finish(managed)


@pytest.mark.parametrize(("name", "params"), _DESCRIPTOR_INTENTS)
async def test_managed_radio_reported_tx_refuses_repeater_writes(
    name: str, params: dict
) -> None:
    """The operator's own PTT, or Core's TX the radio did not release."""

    managed, clock, _lane, probe = radio_tx_authority()
    intent = _intent(name, params)
    try:
        assert await managed.admit_managed_write(intent) is True  # radio at RX

        probe.tx_at = clock.now  # keyed outside Core
        assert await managed.admit_managed_write(intent) is False

        probe.origin = RadioTxOrigin.UNRELEASED  # Core's own TX, not released
        assert await managed.admit_managed_write(intent) is False

        probe.tx_at = None  # back to RX
        assert await managed.admit_managed_write(intent) is True
    finally:
        await managed.close()


async def test_managed_failing_radio_tx_probe_refuses_repeater_writes() -> None:
    managed, _clock, _lane, _probe = radio_tx_authority()

    class _Raising:
        def radio_released(self, keyed_at: float) -> None:
            return None

        def radio_tx_since(self, since: float) -> RadioTxOrigin | None:
            raise RuntimeError("readback lost")

    managed._radio_tx = _Raising()  # type: ignore[assignment]  # noqa: SLF001
    try:
        intent = _intent("set_repeater_shift", {"direction": 1})
        assert await managed.admit_managed_write(intent) is False
    finally:
        await managed.close()


# ---------------------------------------------------------------------------
# Managed rigctld over TCP: U TONE / U TSQL go through the same admission
# ---------------------------------------------------------------------------

_RIGCTLD_REPEATER_WIRES = (
    ("U TONE 1", "set_repeater_tone"),
    ("U TSQL 1", "set_repeater_tsql"),
)


def _repeater_spies(
    rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> dict[str, AsyncMock]:
    """Spy the repeater Radio methods and declare the profile's capability tags."""

    spies = {name: AsyncMock() for _wire, name in _RIGCTLD_REPEATER_WIRES}
    for name, spy in spies.items():
        setattr(rig.radio, name, spy)
    monkeypatch.setattr(
        type(rig.radio),
        "capabilities",
        property(lambda _self: {"repeater_tone", "tsql"}),
    )
    return spies


@pytest.mark.parametrize(("wire", "method"), _RIGCTLD_REPEATER_WIRES)
async def test_managed_rigctld_admits_a_repeater_function_when_quiet(
    managed_rig: SimpleNamespace,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    wire: str,
    method: str,
) -> None:
    spies = _repeater_spies(managed_rig, monkeypatch)

    await _send(managed_rig, wire)

    assert await _replies(managed_rig, 1) == [b"RPRT 0\n"]
    spies[method].assert_awaited_once_with(on=True, receiver=0)


@pytest.mark.parametrize(("wire", "method"), _RIGCTLD_REPEATER_WIRES)
async def test_managed_rigctld_refuses_a_repeater_function_while_keyed(
    managed_rig: SimpleNamespace,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    wire: str,
    method: str,
) -> None:
    spies = _repeater_spies(managed_rig, monkeypatch)
    await _key(managed_rig)

    await _send(managed_rig, wire)

    assert await _replies(managed_rig, 1) == [b"RPRT -9\n"]
    for spy in spies.values():
        spy.assert_not_awaited()
    await _clear_release_debt(managed_rig)


@pytest.mark.parametrize(("wire", "method"), _RIGCTLD_REPEATER_WIRES)
async def test_managed_rigctld_refuses_a_repeater_function_with_a_release_owed(
    managed_rig: SimpleNamespace,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    wire: str,
    method: str,
) -> None:
    spies = _repeater_spies(managed_rig, monkeypatch)
    await _key(managed_rig)
    managed_rig.lane.results.append(ActuationResult.UNCERTAIN)
    await _send(managed_rig, "T 0")
    assert await _replies(managed_rig, 1) == [b"RPRT 0\n"]
    while (await managed_rig.authority.snapshot()).state.pending_effect is not None:
        await asyncio.sleep(0)
    state = (await managed_rig.authority.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX and state.release_required

    await _send(managed_rig, wire)

    assert await _replies(managed_rig, 1) == [b"RPRT -9\n"]
    for spy in spies.values():
        spy.assert_not_awaited()
    await _clear_release_debt(managed_rig)


@pytest.mark.parametrize(("wire", "method"), _RIGCTLD_REPEATER_WIRES)
async def test_managed_rigctld_answers_enavail_without_the_capability_tag(
    managed_rig: SimpleNamespace,  # noqa: F811
    wire: str,
    method: str,
) -> None:
    spy = AsyncMock()
    setattr(managed_rig.radio, method, spy)

    await _send(managed_rig, wire)

    assert await _replies(managed_rig, 1) == [b"RPRT -11\n"]
    spy.assert_not_awaited()


# ---------------------------------------------------------------------------
# Standalone rigctld: nothing publishes a TX target there, so none is required
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ("IC-705", "IC-7300"))
async def test_standalone_rigctld_keys_without_a_published_tx_target(
    model: str,
) -> None:
    """`rigplane serve` has no TX-target publisher (the web poller is absent).

    A guard that demanded a published target there would refuse every
    ``T 1`` forever; PTT-on keys as it did before the guard existed, with
    the fake CI-V peer recording the keying write.
    """

    async with _peer(model) as (_radio, peer, server, reader, writer):
        assert server._rig_handler is not None  # noqa: SLF001
        snapshot = server._rig_handler._state_store.snapshot()  # noqa: SLF001
        with pytest.raises(KeyError):
            snapshot.field(_TX_TARGET)

        assert await _exchange(writer, reader, b"T 1\n") == b"RPRT 0\n"
        assert await _exchange(writer, reader, b"T 0\n") == b"RPRT 0\n"
        assert peer.ptt_writes == [True, False]


# ---------------------------------------------------------------------------
# Web HTTP/WS ingress: queue, production execution, client-visible outcome
# ---------------------------------------------------------------------------

_WEB_REPEATER_WRITES = (
    ("set_repeater_shift", {"direction": 1}),
    ("set_repeater_tone", {"on": True}),
    ("set_repeater_tsql", {"on": True}),
    ("set_tone_freq", {"freq": 8850}),
    ("set_tsql_freq", {"freq": 8850}),
)


def _web_rf(web: WebServer, value: bool | None) -> None:
    """Publish (or withdraw, for ``None``) the radio's PTT truth in the store."""

    if value is None:
        web.command_state_store.discard([_PTT])
        return
    web.command_state_store.apply_current(
        Observation(
            path=_PTT,
            value=value,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=time.monotonic(),
            max_age=60,
        )
    )


@asynccontextmanager
async def _web_ingress(model: str):
    """A real WebServer socket in front of the production poller turn.

    The CI-V peer is the fake hardware; the HTTP/WS listener, the command
    queue, ``RadioPoller`` staging/execution and failure marking are the
    production code (the loop below is ``RadioPoller._run``'s turn minus its
    polling).
    """

    async with _peer(model) as (radio, peer, _server, _reader, _writer):
        radio._civ_stream_ready = True
        radio._last_civ_data_received = time.monotonic()
        web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        poller = RadioPoller(
            radio, web.command_queue, state_store=web.command_state_store
        )
        web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)

        async def action(entry: CommandQueueEntry) -> object:
            try:
                return await poller._execute_queued_entry_action(entry)  # noqa: SLF001
            except Exception as exc:
                poller._mark_queued_command_failed(entry, exc)  # noqa: SLF001
                raise

        async def run_turn() -> None:
            while True:
                await web.command_queue.wait(0.02)
                for _ in range(web.command_queue.pending_count):
                    entry = web.command_queue.take_entry()
                    if entry is None:
                        break
                    for ready in poller._stage_tx_interlocked_entries(  # noqa: SLF001
                        [entry], advance_held=False
                    ):
                        await execute_command_queue_entry(ready, action)
                for ready in poller._stage_tx_interlocked_entries([]):  # noqa: SLF001
                    await execute_command_queue_entry(ready, action)

        consumer = asyncio.create_task(run_turn())
        try:
            yield web, peer
        finally:
            web._server.close()
            await web._server.wait_closed()
            web._server = None
            tasks = (consumer, *tuple(web._client_tasks))
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def _http_command(web: WebServer, name: str, params: dict) -> tuple[int, dict]:
    reader, writer = await asyncio.open_connection("127.0.0.1", web.port)
    try:
        body = json.dumps({"id": f"{name}-http", "name": name, "params": params})
        payload = body.encode()
        writer.write(
            (
                f"POST /api/v1/commands HTTP/1.1\r\nHost: 127.0.0.1:{web.port}\r\n"
                "Connection: close\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\n\r\n"
            ).encode()
            + payload
        )
        await writer.drain()
        status, _headers, response = await asyncio.wait_for(
            _read_http_response(reader), 5
        )
        return status, json.loads(response)
    finally:
        writer.close()
        await writer.wait_closed()


async def _ws_command(
    web: WebServer, name: str, params: dict, *, until_state: str | None = None
) -> list[dict]:
    """Send one WS command; collect its response and lifecycle frames.

    Stops at the first lifecycle frame in ``until_state`` (after the command
    response), or at the command response when none is named.
    """

    command_id = f"{name}-ws"
    reader, writer, _ = await _ws_connect("127.0.0.1", web.port, "/api/v1/ws")
    frames: list[dict] = []
    try:
        await _ws_send_text(
            writer,
            json.dumps(
                {"type": "cmd", "id": command_id, "name": name, "params": params}
            ),
        )
        while True:
            try:
                _opcode, raw = await _ws_recv_frame(reader, timeout=3)
            except (TimeoutError, asyncio.TimeoutError):
                kinds = [
                    (f.get("type"), f.get("state"), f.get("details")) for f in frames
                ]
                pytest.fail(f"no {until_state or 'response'} frame; saw {kinds}")
            frame = json.loads(raw)
            frames.append(frame)
            if until_state is None:
                if frame.get("type") == "response" and frame.get("id") == command_id:
                    return frames
            elif (
                frame.get("type") == "command_lifecycle"
                and frame.get("state") == until_state
            ):
                return frames
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize("protocol", ("http", "ws"))
@pytest.mark.parametrize(("name", "params"), _WEB_REPEATER_WRITES)
async def test_web_repeater_write_is_refused_on_unknown_rf_with_no_wire_traffic(
    protocol: str, name: str, params: dict
) -> None:
    async with _web_ingress("IC-705") as (web, peer):
        _web_rf(web, None)

        if protocol == "http":
            status, result = await _http_command(web, name, params)
            assert status >= 400
        else:
            result = (await _ws_command(web, name, params))[-1]

        assert result.get("ok", False) is False
        assert "unknown" in json.dumps(result)
        assert peer.repeater_writes == []


@pytest.mark.parametrize("protocol", ("http", "ws"))
@pytest.mark.parametrize(("name", "params"), _WEB_REPEATER_WRITES)
async def test_web_repeater_write_reaches_the_radio_in_known_rx(
    protocol: str, name: str, params: dict
) -> None:
    async with _web_ingress("IC-705") as (web, peer):
        _web_rf(web, False)

        if protocol == "http":
            status, result = await _http_command(web, name, params)
            assert status == 200
        else:
            result = (await _ws_command(web, name, params))[-1]

        assert result["ok"] is True
        assert len(peer.repeater_writes) == 1


@pytest.mark.parametrize(("name", "params"), _WEB_REPEATER_WRITES)
async def test_web_repeater_write_is_held_while_keyed_then_runs_in_quiet_rx(
    name: str, params: dict
) -> None:
    async with _web_ingress("IC-705") as (web, peer):
        _web_rf(web, True)

        reply = asyncio.create_task(_ws_command(web, name, params))
        await asyncio.sleep(0.4)
        assert not reply.done()  # held: the client has no answer yet
        assert peer.repeater_writes == []

        # The operator unkeys: a continuous quiet RX releases the write.
        for _ in range(60):
            _web_rf(web, False)
            await asyncio.sleep(0.05)
            if reply.done():
                break
        result = (await asyncio.wait_for(reply, 2))[-1]
        assert result["ok"] is True
        assert len(peer.repeater_writes) == 1


# ---------------------------------------------------------------------------
# Failed-lifecycle delivery of the new refusal envelope reaches only its issuer
# ---------------------------------------------------------------------------


async def test_ptt_tx_frequency_refusal_reaches_only_its_issuing_session() -> None:
    web = WebServer(None, WebConfig(radio_model="IC-705"))
    issuer_q: BoundedQueue[dict[str, object]] = BoundedQueue(maxsize=16)
    bystander_q: BoundedQueue[dict[str, object]] = BoundedQueue(maxsize=16)
    web.register_control_event_queue(issuer_q, session_id="session-issuer")
    web.register_control_event_queue(bystander_q, session_id="session-bystander")
    for queue in (issuer_q, bystander_q):
        while not queue.empty():
            queue.get_nowait()

    store = web.command_state_store
    _web_rf(web, False)
    seed_tx_target_value(
        store,
        KnownTxTarget(receiver="SUB", slot="A", frequency_hz=None),
        max_age=60,
    )
    poller = RadioPoller(_radio(), web.command_queue, state_store=store)
    web.command_service._executor = SimpleNamespace(  # noqa: SLF001
        execute=AsyncMock(return_value=CommandExecutionResult())
    )
    await web.command_service.execute(
        CommandIntent(
            id="ptt-refused",
            name="ptt_on",
            params={"session_id": "session-issuer"},
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
        session_id="session-issuer",
        command_service=web.command_service,
    )

    with pytest.raises(TxInterlockRefusal) as refusal:
        await poller._execute_queued_entry_action(entry)  # noqa: SLF001
    poller._mark_queued_command_failed(entry, refusal.value)  # noqa: SLF001

    delivered = []
    while not issuer_q.empty():
        delivered.append(issuer_q.get_nowait())
    lifecycle = [m for m in delivered if m["type"] == "command_lifecycle"]
    assert len(lifecycle) == 1
    assert lifecycle[0]["state"] == "failed"
    assert lifecycle[0]["details"] == {
        "blockedBy": "tx_interlock",
        "reason": "ptt_tx_frequency_unknown",
        "txTargetReason": "not-observed",
        "receiver": "SUB",
    }
    assert bystander_q.empty()


@pytest.mark.parametrize(
    "details",
    (
        {"txTargetReason": "made-up"},
        {"txTargetReason": "stale", "receiver": "TOP"},
        {"txTargetReason": "stale", "receiver": ["MAIN"]},
        {"txTargetReason": "stale", "extra": 1},
        {"receiver": "SUB"},
    ),
    ids=(
        "unknown-target-reason",
        "unknown-receiver",
        "mistyped-receiver",
        "extra-key",
        "no-reason",
    ),
)
async def test_forged_ptt_tx_frequency_refusal_details_are_not_delivered(
    details: dict,
) -> None:
    web = WebServer(None, WebConfig(radio_model="IC-705"))
    issuer_q: BoundedQueue[dict[str, object]] = BoundedQueue(maxsize=16)
    web.register_control_event_queue(issuer_q, session_id="session-issuer")
    while not issuer_q.empty():
        issuer_q.get_nowait()
    web.command_service._executor = SimpleNamespace(  # noqa: SLF001
        execute=AsyncMock(return_value=CommandExecutionResult())
    )
    await web.command_service.execute(
        CommandIntent(
            id="forged",
            name="ptt_on",
            params={"session_id": "session-issuer"},
            source="websocket",
            target=None,
            timeout=3.0,
            pending_policy="none",
            expected_observations=(),
        )
    )
    while not issuer_q.empty():
        issuer_q.get_nowait()

    web.command_service.fail_command(
        "forged",
        message="forged",
        timed_out=False,
        session_id="session-issuer",
        source="websocket",
        details={
            "blockedBy": "tx_interlock",
            "reason": "ptt_tx_frequency_unknown",
            **details,
        },
    )

    delivered = [m for m in _drain(issuer_q) if m["type"] == "command_lifecycle"]
    assert delivered == []


def _drain(queue: BoundedQueue[dict[str, object]]) -> list[dict[str, object]]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items
