"""MOR-3633: repeater writes in the TX interlock.

Every repeater-related write (shift, tone/TSQL toggles, tone/TSQL
frequencies) is DEFER-classified while the radio is keyed and passes in
known RX — the same single-slot deferred lane the memory and VFO-topology
families already use.  Descriptor-backed intents (``set_repeater_shift``
and friends, WS/HTTP) classify directly by intent name through the one
table in ``tx_interlock`` (the descriptor is the only name source for
migrated commands, so no typed command exists for them); the rigctld
``set_func TONE``/``TSQL`` tokens resolve to the same names.

Fakes, never hardware: fake radios and a fake CI-V peer against the real
HTTP/WS listener, ``RadioPoller`` and ``ManagedTxAuthority``, and a real
rigctld TCP server.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from rigplane.core.command_dispatch import (
    bind_command_intent,
    command_descriptor,
    execute_command_intent,
)
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.exceptions import CommandError
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime import _poller_types
from rigplane.runtime._poller_types import (
    PttOff,
    SetRepeaterTone,
    SetRepeaterTsql,
    execute_command_queue_entry,
)
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority, RadioTxOrigin
from rigplane.runtime.managed_tx_composition import ManagedTxComposition
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
    resolve_rf_state,
)
from rigplane.web.radio_poller import CommandQueue, CommandQueueEntry, RadioPoller
from rigplane.web.server import WebConfig, WebServer
from test_controller_sessions import radio_tx_authority
from test_managed_tx_production_composition import RecordingActuator
from test_managed_tx_authority import authority
from test_rigctld_managed_ingress_scheduling import (  # noqa: F401
    _clear_release_debt,
    _key,
    _replies,
    _send,
)
from test_rigctld_managed_ingress_scheduling import managed as managed_rig  # noqa: F401
from test_rigctld_wsjtx_split_mk2 import _peer
from test_web_server import (
    _read_http_response,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)

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

    # The RX reading ages past its TTL: the PTT may be keyed again, unseen.
    clock.advance(2.0)
    with pytest.raises(TxInterlockRefusal) as stale:
        poller._enforce_tx_interlock(command)  # noqa: SLF001
    assert stale.value.reason_code == "rf_state_unknown"


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
async def test_managed_known_receive_admits_repeater_writes(
    name: str, params: dict
) -> None:
    managed, _clock, _lane, _probe = radio_tx_authority()  # the radio reads RX
    try:
        intent = _intent(name, params)

        assert await managed.admit_managed_write(intent) is True
    finally:
        await _finish(managed)


@pytest.mark.parametrize(("name", "params"), _DESCRIPTOR_INTENTS)
async def test_managed_authority_without_a_radio_probe_refuses_repeater_writes(
    name: str, params: dict
) -> None:
    """No readback means no known RX: the write is refused, not assumed safe."""

    managed, _, _, _, _, _ = authority()
    try:
        intent = _intent(name, params)

        assert await managed.admit_managed_write(intent) is False
        # A write that cannot move the TX frequency stays admitted.
        level = bind_command_intent("set_af_level", {"level": 100}, source="test")
        assert await managed.admit_managed_write(level) is True
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


async def test_dispatch_seam_executes_shift_in_known_receive() -> None:
    managed, _clock, _lane, _probe = radio_tx_authority()
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

        def radio_rf_state(self) -> RfState:
            raise RuntimeError("readback lost")

    managed._radio_tx = _Raising()  # type: ignore[assignment]  # noqa: SLF001
    try:
        intent = _intent("set_repeater_shift", {"direction": 1})
        assert await managed.admit_managed_write(intent) is False
    finally:
        await managed.close()


# ---------------------------------------------------------------------------
# Production composition: only KNOWN, FRESH receive admits a repeater write
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _composition(tmp_path):
    """The production managed graph over a real store and its freshness clock."""

    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    composition = ManagedTxComposition(
        RecordingActuator(), config_path=tmp_path / "managed-tx.json"
    )
    await composition.transport_ready(object())
    await composition.bind_state_store(store)
    try:
        yield composition, store, clock
    finally:
        await composition.shutdown(asyncio.Event())


@pytest.mark.parametrize(("name", "params"), _DESCRIPTOR_INTENTS)
async def test_composition_admits_a_repeater_write_only_in_known_fresh_receive(
    tmp_path, name: str, params: dict
) -> None:
    """A missing, stale or keyed PTT observation refuses; fresh RX admits.

    ``ManagedTxComposition.radio_tx_since`` reads "no TX" for a missing or
    aged observation too, so it cannot stand for "receiving": the external
    PTT may still be keyed behind an expired reading.
    """

    intent = _intent(name, params)
    async with _composition(tmp_path) as (composition, store, clock):
        managed = composition.authority

        # No PTT observation at all.
        assert await managed.admit_managed_write(intent) is False

        # Fresh RX.
        _observe_ptt(store, False, observed_at=clock.now())
        assert await managed.admit_managed_write(intent) is True

        # Fresh TX keyed outside Core.
        clock.advance(0.1)
        _observe_ptt(store, True, observed_at=clock.now())
        assert await managed.admit_managed_write(intent) is False

        # RX again, then the reading ages past its TTL: the PTT may still be
        # keyed, so the write is refused until a new reading arrives.
        clock.advance(0.1)
        _observe_ptt(store, False, observed_at=clock.now())
        assert await managed.admit_managed_write(intent) is True
        clock.advance(2.0)
        assert await managed.admit_managed_write(intent) is False
        _observe_ptt(store, False, observed_at=clock.now())
        assert await managed.admit_managed_write(intent) is True

        # A write that cannot move the TX frequency never needs the reading.
        clock.advance(2.0)
        level = bind_command_intent("set_af_level", {"level": 100}, source="test")
        assert await managed.admit_managed_write(level) is True


@pytest.mark.parametrize(("name", "params"), _DESCRIPTOR_INTENTS)
async def test_composition_dispatch_seam_writes_only_in_known_fresh_receive(
    tmp_path, name: str, params: dict
) -> None:
    """The shared dispatch leaf (web and rigctld) over the production graph."""

    async with _composition(tmp_path) as (composition, store, clock):
        radio = SimpleNamespace(**{name: AsyncMock()})
        write = getattr(radio, name)
        intent = _intent(name, params)

        with pytest.raises(CommandError, match="transmit authority"):
            await execute_command_intent(
                radio, intent, managed_tx_authority=composition.authority
            )
        write.assert_not_awaited()

        _observe_ptt(store, False, observed_at=clock.now())
        clock.advance(2.0)  # the only reading is now stale
        with pytest.raises(CommandError, match="transmit authority"):
            await execute_command_intent(
                radio, intent, managed_tx_authority=composition.authority
            )
        write.assert_not_awaited()

        _observe_ptt(store, False, observed_at=clock.now())
        await execute_command_intent(
            radio, intent, managed_tx_authority=composition.authority
        )
        write.assert_awaited_once()


# ---------------------------------------------------------------------------
# Managed rigctld over TCP: U TONE / U TSQL go through the same admission
# ---------------------------------------------------------------------------

_RIGCTLD_REPEATER_WIRES = (
    ("U TONE 1", "set_repeater_tone"),
    ("U TSQL 1", "set_repeater_tsql"),
)


@pytest.fixture
def rf_rig(managed_rig: SimpleNamespace) -> SimpleNamespace:  # noqa: F811
    """The managed rigctld rig with the radio's PTT readback wired in.

    The shared rig builds its authority without a radio probe; here the probe
    reads the rig's own store through the production RF resolver, so the
    store's PTT observation decides whether the radio is known to be at RX.
    """

    store = managed_rig.store
    managed_rig.authority._radio_tx = SimpleNamespace(  # noqa: SLF001
        radio_released=lambda _keyed_at: None,
        radio_tx_since=lambda _since: None,
        radio_rf_state=lambda: resolve_rf_state(store.snapshot()),
    )
    return managed_rig


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
    rf_rig: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    wire: str,
    method: str,
) -> None:
    spies = _repeater_spies(rf_rig, monkeypatch)

    await _send(rf_rig, wire)

    assert await _replies(rf_rig, 1) == [b"RPRT 0\n"]
    spies[method].assert_awaited_once_with(on=True, receiver=0)


@pytest.mark.parametrize(("wire", "method"), _RIGCTLD_REPEATER_WIRES)
async def test_managed_rigctld_refuses_a_repeater_function_while_keyed(
    rf_rig: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    wire: str,
    method: str,
) -> None:
    spies = _repeater_spies(rf_rig, monkeypatch)
    await _key(rf_rig)

    await _send(rf_rig, wire)

    assert await _replies(rf_rig, 1) == [b"RPRT -9\n"]
    for spy in spies.values():
        spy.assert_not_awaited()
    await _clear_release_debt(rf_rig)


@pytest.mark.parametrize(("wire", "method"), _RIGCTLD_REPEATER_WIRES)
async def test_managed_rigctld_refuses_a_repeater_function_with_a_release_owed(
    rf_rig: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    wire: str,
    method: str,
) -> None:
    spies = _repeater_spies(rf_rig, monkeypatch)
    await _key(rf_rig)
    rf_rig.lane.results.append(ActuationResult.UNCERTAIN)
    await _send(rf_rig, "T 0")
    assert await _replies(rf_rig, 1) == [b"RPRT 0\n"]
    while (await rf_rig.authority.snapshot()).state.pending_effect is not None:
        await asyncio.sleep(0)
    state = (await rf_rig.authority.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX and state.release_required

    await _send(rf_rig, wire)

    assert await _replies(rf_rig, 1) == [b"RPRT -9\n"]
    for spy in spies.values():
        spy.assert_not_awaited()
    await _clear_release_debt(rf_rig)


@pytest.mark.parametrize(("wire", "method"), _RIGCTLD_REPEATER_WIRES)
async def test_managed_rigctld_refuses_a_repeater_function_without_a_fresh_rx_reading(
    rf_rig: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    wire: str,
    method: str,
) -> None:
    """A missing PTT observation is not RX: the write is refused, nothing written."""

    spies = _repeater_spies(rf_rig, monkeypatch)
    rf_rig.store.discard([_PTT])

    await _send(rf_rig, wire)

    assert await _replies(rf_rig, 1) == [b"RPRT -9\n"]
    for spy in spies.values():
        spy.assert_not_awaited()


@pytest.mark.parametrize(("wire", "method"), _RIGCTLD_REPEATER_WIRES)
async def test_managed_rigctld_answers_enavail_without_the_capability_tag(
    rf_rig: SimpleNamespace,
    wire: str,
    method: str,
) -> None:
    spy = AsyncMock()
    setattr(rf_rig.radio, method, spy)

    await _send(rf_rig, wire)

    assert await _replies(rf_rig, 1) == [b"RPRT -11\n"]
    spy.assert_not_awaited()


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
