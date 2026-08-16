"""Rigctld write-intent coverage for the shared TX interlock policy."""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.core.tx_interlock_contract import TxInterlockDisposition
from rigplane.rigctld.contract import (
    COMMAND_TABLE,
    ClientSession,
    HamlibError,
    RigctldConfig,
    RigctldResponse,
)
from rigplane.rigctld.handler import RigctldHandler, _classify_rigctld_tx_intent
from rigplane.rigctld.protocol import format_response, parse_line
from rigplane.runtime import _poller_types as commands
from rigplane.runtime.tx_interlock import RfState


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


_PTT = FieldPath.global_("tx_state", "ptt")
_BLOCK_WIRES = (b"T 1", b"U TUNER 1", b"w FE FE 98 E0 03 FD")


def _store(case: str) -> tuple[StateStore, object | None]:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    if case == "absent":
        return store, None
    store.apply(
        Observation(
            path=_PTT,
            value=1 if case == "value" else case == "tx",
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=(
                float("nan")
                if case == "timestamp"
                else 11.0
                if case == "future"
                else 9.0
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


def _handler(
    store: StateStore, *, public_store: bool = True
) -> tuple[RigctldHandler, AsyncMock, Mock]:
    radio = AsyncMock()
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
    [("before", RfState.RX)]
    + [
        (case, RfState.UNKNOWN)
        for case in ("exact", "after", "future", "timestamp", "max-age", "generation")
    ],
)
def test_rf_truth_enforces_strict_age_and_shape(
    case: str, expected: RfState, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, forced = _store(case)
    handler, _, _ = _handler(store)
    if forced is not None:
        monkeypatch.setattr(StateStore, "snapshot", lambda _: forced)
    assert handler._resolve_rigctld_rf_state() is expected  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["tx", "rx", "absent", "stale", "generation"])
async def test_non_block_dispositions_never_resolve_rf_truth(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler, radio, routing = _handler(_store(case)[0])
    resolver = Mock(side_effect=AssertionError("RF truth inspected"))
    monkeypatch.setattr(handler, "_resolve_rigctld_rf_state", resolver)
    for wire in (
        b"T 0",
        b"U TUNER 0",
        b"F 1",
        b"M USB 2400",
        b"V VFOA",
        b"S 1 VFOA",
        b"L AF 0.5",
    ):
        assert (await handler.execute(parse_line(wire))).ok
    for intent in (_intent("set_rit", hz=1), _intent("set_xit", hz=1)):
        await handler._command_service.execute(intent)  # noqa: SLF001
    resolver.assert_not_called()
    radio.set_ptt.assert_awaited_once_with(False)
    routing.set_func.assert_awaited_once_with("TUNER", False, vfo=None)
