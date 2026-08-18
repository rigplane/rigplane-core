"""Rigctld write-intent coverage for the shared TX interlock policy."""

import logging
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from rigplane.capabilities import CAP_RIT
from rigplane.core.command_service import CommandServiceResult
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    CommandLifecycleEvent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.core.tx_interlock_contract import TxInterlockDisposition
from rigplane.exceptions import TimeoutError as RigplaneTimeoutError
from rigplane.rigctld.contract import (
    COMMAND_TABLE,
    ClientSession,
    HamlibError,
    RigctldCommand,
    RigctldConfig,
    RigctldResponse,
)
from rigplane.rigctld.handler import RigctldHandler, _classify_rigctld_tx_intent
from rigplane.rigctld.protocol import format_response, parse_line
from rigplane.runtime import _poller_types as commands, tx_interlock


def _intent(name: str, **params: object) -> CommandIntent:
    return CommandIntent(id="test", name=name, params=params, source="rigctld")


def test_material_intents_use_shared_policy() -> None:
    cases = (
        (_intent("set_freq", freq_hz=1), commands.SetFreq, "defer", "frequency"),
        (_intent("set_mode", mode="USB"), commands.SetMode, "defer", "mode"),
        (_intent("set_ptt", ptt=False), commands.PttOff, "always-pass", "ptt-off"),
        (_intent("set_ptt", ptt=True), commands.PttOn, "block", "ptt-on"),
        (_intent("set_rit", hz=50), commands.SetRitFrequency, "defer", "rit-xit"),
        (_intent("set_xit", hz=-50), commands.SetRitFrequency, "defer", "rit-xit"),
        (_intent("set_vfo", vfo="VFOA"), commands.SelectVfo, "defer", "vfo-select"),
        (_intent("set_split_vfo", on=True), commands.SetSplit, "defer", "vfo-topology"),
        (
            _intent("send_raw", frame_bytes=b"\xfe\xfd"),
            commands.SendCiv,
            "block",
            "raw-civ",
        ),
    )
    for intent, command_type, disposition, family in cases:
        classified = _classify_rigctld_tx_intent(intent)
        assert isinstance(classified.command, command_type)
        assert classified.disposition.value == disposition
        assert classified.family is not None
        assert classified.family.value == family


def test_all_current_level_setters_are_typed_tx_safe() -> None:
    levels = "RFPOWER AF RF SQL NR NB COMP MICGAIN MONITOR_GAIN KEYSPD CWPITCH PREAMP ATT NOTCHF IFSHIFT".split()
    type_names = "SetPower SetAfLevel SetRfGain SetSquelch SetNRLevel SetNBLevel SetCompressorLevel SetMicGain SetMonitorGain SetKeySpeed SetCwPitch SetPreamp SetAttenuator SetNotchFilter SetIfShift".split()
    for level, type_name in zip(levels, type_names, strict=True):
        classified = _classify_rigctld_tx_intent(
            _intent("set_level", level=level, value=1)
        )
        assert isinstance(classified.command, getattr(commands, type_name))
        assert classified.disposition is TxInterlockDisposition.TX_SAFE
        assert classified.family is None


def test_all_current_function_setters_use_shared_policy() -> None:
    funcs = "NB NR COMP VOX TONE TSQL ANF LOCK MON APF AGC".split()
    type_names = "SetNB SetNR SetCompressor SetVox SetRepeaterTone SetRepeaterTsql SetAutoNotch SetDialLock SetMonitor SetAudioPeakFilter SetAgc".split()
    for func, type_name in zip(funcs, type_names, strict=True):
        classified = _classify_rigctld_tx_intent(
            _intent("set_func", func=func, on=True)
        )
        assert isinstance(classified.command, getattr(commands, type_name))
        assert classified.disposition is TxInterlockDisposition.TX_SAFE

    for func, on, disposition in (
        ("TUNER", False, "always-pass"),
        ("TUNER", True, "block"),
        ("SPLIT", True, "defer"),
    ):
        assert (
            _classify_rigctld_tx_intent(
                _intent("set_func", func=func, on=on)
            ).disposition.value
            == disposition
        )


def test_protocol_inventory_aliases_and_wire_output_remain_unchanged() -> None:
    material_names = "set_freq set_mode set_ptt set_vfo set_level set_func set_split_vfo send_raw".split()
    material_defs = {
        definition
        for definition in COMMAND_TABLE.values()
        if definition.is_set or definition.long == "send_raw"
    }
    assert {definition.long for definition in material_defs} == set(material_names)
    assert {
        key for key, definition in COMMAND_TABLE.items() if definition in material_defs
    } == {
        alias
        for definition in material_defs
        for alias in (definition.short, definition.long)
    }
    for wire in (b"F 1", b"\\set_freq 1", b"T 0", b"\\set_ptt 0"):
        assert (
            format_response(parse_line(wire), RigctldResponse(), ClientSession())
            == b"RPRT 0\n"
        )


def test_non_writes_and_future_material_intents_fail_closed() -> None:
    non_writes = {
        definition.long
        for definition in COMMAND_TABLE.values()
        if not definition.is_set
        and definition.long != "send_raw"
        and definition.long == definition.long.lower()
    }
    for name in (*non_writes, "future_write"):
        with pytest.raises(ValueError, match="unmapped rigctld TX policy intent"):
            _classify_rigctld_tx_intent(_intent(name))

    for name, key in (("set_level", "future"), ("set_func", "future")):
        with pytest.raises(ValueError, match="unmapped rigctld TX policy intent"):
            _classify_rigctld_tx_intent(
                _intent(name, level=key, func=key, value=1, on=True)
            )


_BLOCK_WIRES = (b"T 1", b"U TUNER 1", b"w FE FE 98 E0 03 FD")


def _store(case: str) -> tuple[StateStore, object | None]:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    if case == "absent":
        return store, None
    store.apply(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=1 if case == "value" else case == "tx",
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic={"timestamp": float("nan"), "future": 11.0}.get(
                case, 9.0
            ),
            max_age={"before": 1.1, "exact": 1.0, "after": 0.9}.get(
                case, float("inf") if case == "max-age" else 2.0
            ),
        )
    )
    if case == "stale":
        clock.advance(2.1)
    snapshot = store.snapshot()
    if case == "generation":
        field = replace(snapshot.fields[0], provider_generation=1)
        snapshot = replace(snapshot, fields=(field,))
        return store, snapshot
    return store, None


def _handler(store: StateStore, *, public_store: bool = True):
    radio = AsyncMock()
    radio.capabilities = {CAP_RIT}
    if public_store:
        radio.state_store = store
    else:
        del radio.state_store
    routing = Mock()
    routing.set_func = AsyncMock(return_value=RigctldResponse())
    routing.set_level = AsyncMock(return_value=RigctldResponse())
    radio.rigctld_routing = Mock(return_value=routing)
    radio._send_civ_raw = AsyncMock(return_value=None)
    return RigctldHandler(radio, RigctldConfig(), state_store=store), radio, routing


def _attempt(radio: AsyncMock, routing: Mock, wire: bytes) -> AsyncMock:
    if wire.startswith(b"T"):
        return radio.set_ptt
    if wire.startswith(b"U"):
        return routing.set_func
    return radio._send_civ_raw


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["tx", "absent", "stale", "generation"])
@pytest.mark.parametrize("wire", _BLOCK_WIRES)
@pytest.mark.parametrize(
    "public_store", [True, False], ids=["radio-store", "server-store"]
)
async def test_hard_blocks_require_fresh_known_rx(
    case: str, wire: bytes, public_store: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, forced = _store(case)
    handler, radio, routing = _handler(store, public_store=public_store)
    if forced is not None:
        monkeypatch.setattr(StateStore, "snapshot", lambda _: forced)
    command = parse_line(wire)
    response = await handler.execute(command)
    assert response.error is HamlibError.ERJCTED
    assert format_response(command, response, ClientSession()) == b"RPRT -9\n"
    _attempt(radio, routing, wire).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", _BLOCK_WIRES)
async def test_hard_blocks_dispatch_once_with_fresh_rx(wire: bytes) -> None:
    handler, radio, routing = _handler(_store("rx")[0])
    response = await handler.execute(parse_line(wire))
    assert response.ok
    _attempt(radio, routing, wire).assert_awaited_once()
    if wire.startswith(b"w"):
        radio._send_civ_raw.assert_awaited_once_with(b"\xfe\xfe\x98\xe0\x03\xfd")


@pytest.mark.parametrize(
    ("case", "expected"),
    [("before", tx_interlock.RfState.RX)]
    + [
        (case, tx_interlock.RfState.UNKNOWN)
        for case in ("exact", "after", "future", "timestamp", "max-age", "generation")
    ],
)
def test_rf_truth_enforces_strict_age_and_shape(
    case: str, expected: tx_interlock.RfState, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, forced = _store(case)
    handler, _, _ = _handler(store)
    if forced is not None:
        monkeypatch.setattr(StateStore, "snapshot", lambda _: forced)
    assert handler._resolve_rigctld_rf_state() is expected  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["tx", "rx", "absent", "stale", "generation"])
async def test_structural_exemptions_never_resolve_rf_truth(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-1881: only the genuinely structural exemptions skip RF truth.

    Before MOR-1881, this test (under its old name,
    ``test_non_block_dispositions_never_resolve_rf_truth``) asserted that
    *every* non-BLOCK disposition — including DEFER — never inspected RF
    truth and always dispatched immediately. That was the MOR-1629
    acceptance gap made concrete: DEFER-classified writes (freq/mode/vfo/
    split/RIT/XIT) executed unconditionally during known TX, because the
    rigctld seat never consumed the shared deferred-TX lane. DEFER writes
    now correctly consult RF truth (see the ``test_defer_*`` cases below);
    only the hard safety-barrier ALWAYS_PASS writes (PTT off, tuner off)
    and the untouched TX_SAFE level writes remain exempt, which this test
    now checks specifically. An unkey must never be blocked, deferred, or
    delayed by the TX interlock.
    """
    handler, radio, routing = _handler(_store(case)[0])
    resolver = Mock(side_effect=AssertionError("RF truth inspected"))
    monkeypatch.setattr(handler, "_resolve_rigctld_rf_state", resolver)
    for wire in (b"T 0", b"U TUNER 0", b"L AF 0.5"):
        assert (await handler.execute(parse_line(wire))).ok
    resolver.assert_not_called()
    radio.set_ptt.assert_awaited_once_with(False)
    routing.set_func.assert_awaited_once_with("TUNER", False, vfo=None)


# ── MOR-1881 (owner ruling 2026-08-17, superseding the deferred-TX-lane ─────
# design in PR #2755): DEFER-classified writes are silently dropped (RPRT 0,
# radio untouched) during known TX, and truthfully refused under UNKNOWN/
# stale RF -- never held in-band. See ``RigctldHandler._defer_write_gate``.


_DEFER_WIRES = (b"F 1", b"M USB 2400", b"V VFOA", b"S 1 VFOA", b"U SPLIT 1")


def _defer_wire_method(radio: AsyncMock, wire: bytes) -> AsyncMock:
    if wire.startswith(b"F"):
        return radio.set_freq
    if wire.startswith(b"M"):
        return radio.set_mode
    if wire.startswith(b"V"):
        return radio.set_vfo
    return radio.set_split  # b"S ..." (set_split_vfo) and b"U SPLIT ..."


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", _DEFER_WIRES)
async def test_defer_writes_in_known_rx_dispatch_immediately(wire: bytes) -> None:
    # Not asserting the specific underlying radio call here: which method
    # set_vfo/set_func(SPLIT) route to depends on profile/receiver routing
    # this generic mock radio doesn't model. The contrast that matters (the
    # write reaches the radio in RX but never does in TX) is asserted by
    # test_defer_writes_are_dropped_silently_during_known_tx below.
    handler, _radio, _routing = _handler(_store("rx")[0])
    response = await handler.execute(parse_line(wire))
    assert response.ok


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["absent", "stale", "generation"])
@pytest.mark.parametrize("wire", _DEFER_WIRES)
async def test_defer_writes_fail_closed_on_unknown_rf_state(
    wire: bytes, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, forced = _store(case)
    handler, radio, _routing = _handler(store)
    if forced is not None:
        monkeypatch.setattr(StateStore, "snapshot", lambda _: forced)
    response = await handler.execute(parse_line(wire))
    assert response.error is HamlibError.ERJCTED
    _defer_wire_method(radio, wire).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", _DEFER_WIRES)
async def test_defer_writes_are_dropped_silently_during_known_tx(
    wire: bytes,
) -> None:
    """The owner-ruled outcome (MOR-1881): during known TX, a DEFER write is
    not sent to the radio at all -- it is dropped and answered ``RPRT 0``,
    mirroring hamlib's own core (``rig_set_mode`` / ``rig_set_split_vfo`` /
    ``rig_set_split_mode`` / the ``set_freq`` RX-VFO skip in hamlib's
    ``src/rig.c``), which is written that way to avoid WSJT-X treating a
    non-zero RPRT mid-sequence as a hard rig-control failure.
    """
    handler, radio, _routing = _handler(_store("tx")[0])
    response = await handler.execute(parse_line(wire))
    assert response.ok
    assert format_response(parse_line(wire), response, ClientSession()) == b"RPRT 0\n"
    _defer_wire_method(radio, wire).assert_not_awaited()


@pytest.mark.asyncio
async def test_dropped_write_leaves_state_store_and_lifecycle_untouched() -> None:
    """The single most important test in this file (MOR-1881 AC2).

    A dropped write must be invisible to our own state model: no pending
    overlay, no lifecycle event of any kind, no observation -- nothing that
    could make our own UI show a value the radio never took. This is what
    "refuse above CommandService.execute" (rather than inside the injected
    executor) buys: ``_record_intent_overlay`` and the "accepted"/"queued"
    lifecycle events run INSIDE ``execute``, so a write that never reaches
    ``execute`` cannot produce any of them.
    """
    store = _store("tx")[0]
    handler, radio, _routing = _handler(store)

    response = await handler.execute(parse_line(b"F 7050000"))

    assert response.ok
    radio.set_freq.assert_not_awaited()
    assert handler._command_service.lifecycle_events() == ()  # noqa: SLF001
    assert (
        handler._command_service.pending_overlays(  # noqa: SLF001
            source="rigctld", session_id=None
        )
        == ()
    )
    with pytest.raises(KeyError):
        store.snapshot().field("receiver.main.active.freq_mode.freq_hz")


@pytest.mark.asyncio
async def test_known_tx_drop_is_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="rigplane.rigctld.handler")
    handler, _radio, _routing = _handler(_store("tx")[0])

    response = await handler.execute(parse_line(b"F 14074000"))

    assert response.ok
    drop_records = [r for r in caplog.records if "dropped" in r.getMessage()]
    assert len(drop_records) == 1
    assert drop_records[0].levelno == logging.WARNING
    message = drop_records[0].getMessage()
    assert "set_freq" in message
    assert "transmitting" in message


# ── RIT/XIT have no wire-level command definition at all (COMMAND_TABLE has
# no "set_rit" / "set_xit" / "get_xit" entry -- a pre-existing gap, not
# introduced here), so these tests build a RigctldCommand directly rather
# than going through parse_line. They still go through handler.execute(),
# which dispatches to _cmd_set_rit / _cmd_set_xit and therefore through
# _defer_write_gate exactly as the wire-reachable commands above do --
# unlike the previous version of these tests, which called
# CommandService.execute() directly and so bypassed the gate entirely.


def _rit_xit_cmd(name: str, hz: int) -> RigctldCommand:
    return RigctldCommand(short_cmd=name, long_cmd=name, args=(str(hz),), is_set=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["absent", "stale", "generation"])
@pytest.mark.parametrize("name", ["set_rit", "set_xit"])
async def test_rit_xit_fail_closed_on_unknown_rf_state(
    name: str, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, forced = _store(case)
    handler, radio, _routing = _handler(store)
    if forced is not None:
        monkeypatch.setattr(StateStore, "snapshot", lambda _: forced)
    response = await handler.execute(_rit_xit_cmd(name, 1))
    assert response.error is HamlibError.ERJCTED
    radio.set_rit_frequency.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["set_rit", "set_xit"])
async def test_rit_xit_dispatch_immediately_in_known_rx(name: str) -> None:
    handler, radio, _routing = _handler(_store("rx")[0])
    response = await handler.execute(_rit_xit_cmd(name, 1))
    assert response.ok
    radio.set_rit_frequency.assert_awaited_once_with(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["set_rit", "set_xit"])
async def test_rit_xit_are_dropped_silently_during_known_tx(name: str) -> None:
    handler, radio, _routing = _handler(_store("tx")[0])
    response = await handler.execute(_rit_xit_cmd(name, 1))
    assert response.ok
    radio.set_rit_frequency.assert_not_awaited()


# ── MOR-1882: the two-sided rule for answering a write with ``RPRT 0`` ─────
# Success may be claimed for a write the radio never saw in exactly one case:
# the deliberate pre-send policy drop above (known TX, MOR-1881), which is
# bounded because it ends when the operator unkeys, and which is taken before
# the command service is ever entered -- so it leaves no lifecycle record at
# all. Every other way a write can fail to land (timeout, in-flight
# invalidation, any terminal lifecycle state that is not evidence the write
# was applied) is reported truthfully. Without both halves pinned the
# carve-out widens into a loophole that swallows real failures.


class _TerminalStateService:
    """Real command service with one extra terminal lifecycle event appended.

    Models an executor seam that reports a non-applied terminal outcome by
    returning it rather than by raising -- the case the handler used to
    answer ``RPRT 0`` because it discarded the result entirely.
    """

    def __init__(self, inner: object, state: str) -> None:
        self._inner = inner
        self._state = state

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def execute(self, intent: CommandIntent) -> CommandServiceResult:
        result = await self._inner.execute(intent)  # type: ignore[attr-defined]
        return replace(
            result,
            lifecycle_events=result.lifecycle_events
            + (
                CommandLifecycleEvent(
                    command_id=intent.id,
                    state=self._state,  # type: ignore[arg-type]
                    timestamp_monotonic=0.0,
                    source="rigctld",
                ),
            ),
        )


@pytest.mark.asyncio
async def test_applied_write_still_answers_rprt_0() -> None:
    """Positive control: an ordinary write is unaffected by the new checks."""
    handler, radio, _routing = _handler(_store("rx")[0])

    response = await handler.execute(parse_line(b"F 14074000"))

    assert response.ok
    radio.set_freq.assert_awaited_once()
    states = [e.state for e in handler._command_service.lifecycle_events()]  # noqa: SLF001
    assert states[-1] == "acknowledged"


@pytest.mark.asyncio
async def test_write_timeout_answers_etimeout_not_rprt_0() -> None:
    handler, radio, _routing = _handler(_store("rx")[0])
    radio.set_freq = AsyncMock(side_effect=RigplaneTimeoutError("no CI-V reply"))

    response = await handler.execute(parse_line(b"F 14074000"))

    assert response.error is HamlibError.ETIMEOUT
    states = [e.state for e in handler._command_service.lifecycle_events()]  # noqa: SLF001
    assert states[-1] == "timed_out"


@pytest.mark.asyncio
async def test_write_invalidated_in_flight_is_neither_success_nor_einternal() -> None:
    """Supersession/invalidation is a real terminal outcome, not an internal bug.

    The write completed at the backend but was no longer the live command when
    it did, so nothing may be claimed about the radio's state.
    """
    handler, radio, _routing = _handler(_store("rx")[0])

    async def _invalidate(*_args: object, **_kwargs: object) -> None:
        service = handler._command_service  # noqa: SLF001
        sent = [e for e in service.lifecycle_events() if e.state == "sent"]
        service.fail_command(sent[-1].command_id, source="rigctld", session_id=None)

    radio.set_freq = AsyncMock(side_effect=_invalidate)

    response = await handler.execute(parse_line(b"F 14074000"))

    assert response.error is HamlibError.ERJCTED
    assert response.error is not HamlibError.EINTERNAL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("failed", HamlibError.ERJCTED),
        ("timed_out", HamlibError.ETIMEOUT),
        ("superseded", HamlibError.ERJCTED),
    ],
)
async def test_unapplied_terminal_state_is_never_answered_with_rprt_0(
    state: str, expected: HamlibError
) -> None:
    handler, _radio, _routing = _handler(_store("rx")[0])
    handler._command_service = _TerminalStateService(  # type: ignore[assignment]  # noqa: SLF001
        handler._command_service,  # noqa: SLF001
        state,
    )

    response = await handler.execute(parse_line(b"F 14074000"))

    assert response.error is expected


@pytest.mark.asyncio
async def test_raw_write_timeout_answers_etimeout_not_empty_success() -> None:
    """``w`` used to swallow its own timeout and answer ``RPRT 0`` with no data."""
    handler, radio, _routing = _handler(_store("rx")[0])
    radio._send_civ_raw = AsyncMock(side_effect=RigplaneTimeoutError("no reply"))

    response = await handler.execute(parse_line(b"w FE FE 98 E0 03 FD"))

    assert response.error is HamlibError.ETIMEOUT
    assert response.values == []
    states = [e.state for e in handler._command_service.lifecycle_events()]  # noqa: SLF001
    assert states[-1] == "timed_out"


@pytest.mark.asyncio
async def test_success_without_a_lifecycle_record_is_only_the_tx_drop() -> None:
    """The narrow half, stated as a property rather than as a single case.

    A write answered ``RPRT 0`` either never entered the command service at
    all (the known-TX policy drop) or ended in an applied terminal state.
    There is no third shape.
    """
    dropped_handler, dropped_radio, _ = _handler(_store("tx")[0])
    dropped = await dropped_handler.execute(parse_line(b"F 14074000"))
    assert dropped.ok
    dropped_radio.set_freq.assert_not_awaited()
    assert dropped_handler._command_service.lifecycle_events() == ()  # noqa: SLF001

    applied_handler, applied_radio, _ = _handler(_store("rx")[0])
    applied = await applied_handler.execute(parse_line(b"F 14074000"))
    assert applied.ok
    applied_radio.set_freq.assert_awaited_once()
    states = [
        e.state
        for e in applied_handler._command_service.lifecycle_events()  # noqa: SLF001
    ]
    assert states[-1] == "acknowledged"
