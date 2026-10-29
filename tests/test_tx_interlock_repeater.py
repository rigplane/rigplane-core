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

Fakes, never hardware: fake radios against the real
``RadioPoller``/``YaesuCatPoller``/``ManagedTxAuthority``/rigctld seats.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, call

import pytest

from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
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
from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import HamlibError, RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.protocol import parse_line
from rigplane.runtime import _poller_types
from rigplane.runtime._poller_types import (
    PttOff,
    PttOn,
    SetRepeaterTone,
    SetRepeaterTsql,
)
from rigplane.runtime._state_queries import profile_publishes_tx_target
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind, ManagedTxOutcome
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
from support.tx_target import seed_tx_target_value
from test_managed_tx_authority import authority

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
# Yaesu seat: the FTX-1 keeps its own TX-target observation (MOR-3633)
# ---------------------------------------------------------------------------


def _yaesu_radio() -> YaesuCatRadio:
    """A real FTX-1 radio object (no I/O until connect); keying is a spy."""

    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    radio.set_ptt = AsyncMock()  # type: ignore[method-assign]
    return radio


def _stage_yaesu_ptt(poller: YaesuCatPoller, active: bool, *, now: float) -> None:
    poller._ptt_observation = Observation(  # noqa: SLF001
        path=_PTT,
        value=active,
        source=SourceMetadata(source="test", provider="tests"),
        timestamp_monotonic=now,
        max_age=1.0,
    )
    poller._ptt_connection_generation = (  # noqa: SLF001
        poller._current_tx_target_generation()  # noqa: SLF001
    )


def _stage_yaesu_tx_target(
    poller: YaesuCatPoller, value: object, *, now: float, max_age: float = 5.0
) -> None:
    poller._tx_target_observation = Observation(  # noqa: SLF001
        path=_TX_TARGET,
        value=value,
        source=SourceMetadata(source="test", provider="tests"),
        timestamp_monotonic=now,
        max_age=max_age,
    )
    poller._tx_target_connection_generation = (  # noqa: SLF001
        poller._current_tx_target_generation()  # noqa: SLF001
    )


async def test_ftx1_ptt_on_is_refused_without_a_tx_target_observation() -> None:
    radio = _yaesu_radio()
    poller = YaesuCatPoller(radio)
    _stage_yaesu_ptt(poller, False, now=asyncio.get_running_loop().time())

    with pytest.raises(TxInterlockRefusal) as excinfo:
        await poller._execute_command(PttOn())  # noqa: SLF001

    assert excinfo.value.reason_code == "ptt_tx_frequency_unknown"
    assert excinfo.value.details["txTargetReason"] == "not-observed"
    radio.set_ptt.assert_not_awaited()


async def test_ftx1_ptt_on_passes_the_guard_with_a_fresh_known_target() -> None:
    poller = YaesuCatPoller(_yaesu_radio())
    now = asyncio.get_running_loop().time()
    _stage_yaesu_ptt(poller, False, now=now)
    _stage_yaesu_tx_target(
        poller,
        KnownTxTarget(receiver="MAIN", slot=None, frequency_hz=7_100_000),
        now=now,
    )

    # The guard passes; Yaesu has no typed-PttOn dispatch arm (managed PTT
    # keys through the actuator), so the dispatcher itself declines next.
    with pytest.raises(NotImplementedError, match="Yaesu CAT dispatcher"):
        await poller._execute_command(PttOn())  # noqa: SLF001


async def test_yaesu_guard_skips_radios_without_a_real_profile() -> None:
    radio = SimpleNamespace(
        radio_state=SimpleNamespace(ptt=False),
        capabilities={"tx"},
        set_ptt=AsyncMock(),
    )
    poller = YaesuCatPoller(radio)  # type: ignore[arg-type]
    _stage_yaesu_ptt(poller, False, now=asyncio.get_running_loop().time())

    with pytest.raises(NotImplementedError, match="Yaesu CAT dispatcher"):
        await poller._execute_command(PttOn())  # noqa: SLF001


def test_yaesu_tx_target_observation_accessor_fails_closed() -> None:
    poller = YaesuCatPoller(_yaesu_radio())

    assert poller._current_tx_target_observation(now=100.0) is None  # noqa: SLF001

    _stage_yaesu_tx_target(
        poller,
        KnownTxTarget(receiver="SUB", slot="A", frequency_hz=145_500_000),
        now=100.0,
    )
    fresh = poller._current_tx_target_observation(now=100.0)  # noqa: SLF001
    assert fresh is not None
    assert isinstance(fresh.value, KnownTxTarget)

    assert poller._current_tx_target_observation(now=106.0) is None  # noqa: SLF001
    assert poller._tx_target_observation is None  # noqa: SLF001


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
# rigctld seat: unmanaged T1 answers ERJCTED while the target is unknown
# ---------------------------------------------------------------------------


def _rigctld_store(*, ptt: bool, tx_target: object | None = None) -> StateStore:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.apply(
        Observation(
            path=_PTT,
            value=ptt,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=10.0,
            max_age=2.0,
        )
    )
    if tx_target is not None:
        seed_tx_target_value(store, tx_target, timestamp_monotonic=10.0, max_age=3.0)
    return store


def _rigctld_handler(
    store: StateStore, *, model: str = "IC-7300"
) -> tuple[RigctldHandler, AsyncMock]:
    radio = AsyncMock(spec=IcomRadio)
    radio.capabilities = set()
    routing = Mock()
    routing.set_func = AsyncMock()
    routing.set_level = AsyncMock()
    radio.rigctld_routing = Mock(return_value=routing)
    radio._send_civ_raw = AsyncMock()
    radio.get_data_mode = AsyncMock(return_value=False)
    handler = RigctldHandler(radio, RigctldConfig(), state_store=store)
    # Assigned after construction so handler wiring matches the existing
    # mock-radio tests; the seat reads the profile at execution time.
    radio.profile = resolve_radio_profile(model=model)
    return handler, radio


async def test_rigctld_ptt_on_is_refused_while_the_target_is_unknown() -> None:
    handler, radio = _rigctld_handler(_rigctld_store(ptt=False))

    response = await handler.execute(parse_line(b"T 1"))

    assert response.error is HamlibError.ERJCTED
    radio.set_ptt.assert_not_awaited()


async def test_rigctld_ptt_on_dispatches_with_a_fresh_known_target() -> None:
    handler, radio = _rigctld_handler(
        _rigctld_store(
            ptt=False,
            tx_target=KnownTxTarget(receiver="MAIN", slot="A", frequency_hz=7_100_000),
        )
    )

    response = await handler.execute(parse_line(b"T 1"))

    assert response.ok
    radio.set_ptt.assert_awaited_once()


async def test_rigctld_ptt_on_is_not_refused_on_profiles_that_cannot_publish() -> None:
    handler, radio = _rigctld_handler(_rigctld_store(ptt=False), model="IC-9700")

    response = await handler.execute(parse_line(b"T 1"))

    assert response.ok
    radio.set_ptt.assert_awaited_once()
