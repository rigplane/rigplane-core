"""Extended tests for commands module — VFO, split, att, preamp, CW, power."""

from pathlib import Path

import pytest

import rigplane.commands as raw_commands

from rigplane import IC_7610_ADDR
from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    parse_civ_frame,
    select_vfo,
    vfo_a_equals_b,
    vfo_swap,
    set_split,
    set_attenuator,
    set_preamp,
    send_cw,
    stop_cw,
    power_on,
    power_off,
    parse_ack_nak,
)
from rigplane.rig_loader import load_rig
from _command_test_helpers import bind_default_addr_globals, bind_default_addr_module

bind_default_addr_module(raw_commands, to_addr=IC_7610_ADDR)
bind_default_addr_globals(globals(), to_addr=IC_7610_ADDR)

RIG_DIR = Path(__file__).resolve().parents[1] / "rigs"


@pytest.fixture()
def cmd_map():
    rig = load_rig(RIG_DIR / "ic7610.toml")
    return rig.to_command_map()


class TestSelectVfo:
    """The builder sends the selector byte it is handed, and nothing else.

    Which byte names which VFO is declared per rig as ``[vfo] main_select``
    / ``sub_select`` and resolved by ``runtime/radio.py:
    CoreRadio._set_vfo_wire``; the builder used to hold its own
    name-to-byte table instead, which ignored those declarations.
    """

    @pytest.mark.parametrize("code", [0x00, 0x01, 0xD0, 0xD1])
    def test_code_reaches_the_wire_unchanged(self, code, cmd_map):
        parsed = parse_civ_frame(select_vfo(code, cmd_map=cmd_map))
        assert parsed.command == 0x07
        assert parsed.data == bytes([code])

    def test_a_name_is_not_accepted(self, cmd_map):
        # cmd_map is passed so the TypeError below is discriminating on
        # "MAIN" being a str where bytes([code]) needs an int, not merely
        # on cmd_map being omitted (both now raise TypeError post-MOR-2007).
        with pytest.raises(TypeError):
            select_vfo("MAIN", cmd_map=cmd_map)


class TestVfoCommands:
    def test_vfo_a_equals_b(self, cmd_map):
        frame = vfo_a_equals_b(cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x07
        assert parsed.data == b"\xa0"

    def test_vfo_swap(self, cmd_map):
        frame = vfo_swap(cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x07
        assert parsed.data == b"\xb0"


class TestSplit:
    def test_split_on(self, cmd_map):
        frame = set_split(True, cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x0F
        assert parsed.data == b"\x01"

    def test_split_off(self, cmd_map):
        frame = set_split(False, cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.data == b"\x00"


class TestAttenuator:
    def test_att_on(self):
        frame = set_attenuator(True)
        parsed = parse_civ_frame(frame)
        # Command29-wrapped: real command is 0x11, data is BCD 18
        assert parsed.command == 0x11
        assert parsed.data == b"\x18"

    def test_att_off(self):
        frame = set_attenuator(False)
        parsed = parse_civ_frame(frame)
        assert parsed.data == b"\x00"


class TestPreamp:
    def test_preamp_off(self):
        frame = set_preamp(0)
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x16
        assert parsed.data == bytes([0])

    def test_preamp_1(self):
        frame = set_preamp(1)
        parsed = parse_civ_frame(frame)
        assert parsed.data == bytes([1])

    def test_preamp_2(self):
        frame = set_preamp(2)
        parsed = parse_civ_frame(frame)
        assert parsed.data == bytes([2])


class TestCw:
    def test_send_short(self):
        frames = send_cw("CQ CQ")
        assert len(frames) == 1
        parsed = parse_civ_frame(frames[0])
        assert parsed.command == 0x17
        assert parsed.data == b"CQ CQ"

    def test_send_long_splits(self):
        text = "A" * 65
        frames = send_cw(text)
        assert len(frames) == 3
        # First chunk 30, second 30, third 5
        assert len(parse_civ_frame(frames[0]).data) == 30
        assert len(parse_civ_frame(frames[1]).data) == 30
        assert len(parse_civ_frame(frames[2]).data) == 5

    def test_send_uppercased(self):
        frames = send_cw("cq cq")
        parsed = parse_civ_frame(frames[0])
        assert parsed.data == b"CQ CQ"

    def test_stop_cw(self):
        frame = stop_cw()
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x17
        assert parsed.data == b"\xff"


class TestPowerOnOff:
    def test_power_on(self):
        frame = power_on()
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x18
        assert parsed.data == b"\x01"

    def test_power_off(self):
        frame = power_off()
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x18
        assert parsed.data == b"\x00"


class TestParseAckNak:
    def test_ack(self):
        civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0xFB)
        frame = parse_civ_frame(civ)
        assert parse_ack_nak(frame) is True

    def test_nak(self):
        civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0xFA)
        frame = parse_civ_frame(civ)
        assert parse_ack_nak(frame) is False

    def test_other(self):
        civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x03)
        frame = parse_civ_frame(civ)
        assert parse_ack_nak(frame) is None
