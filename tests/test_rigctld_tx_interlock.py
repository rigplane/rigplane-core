"""Rigctld write-intent coverage for the shared TX interlock policy."""

import pytest

from rigplane.core.state_pipeline_contracts import CommandIntent
from rigplane.core.tx_interlock_contract import TxInterlockDisposition
from rigplane.rigctld.contract import COMMAND_TABLE, ClientSession, RigctldResponse
from rigplane.rigctld.handler import _classify_rigctld_tx_intent
from rigplane.rigctld.protocol import format_response, parse_line
from rigplane.runtime import _poller_types as commands


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
