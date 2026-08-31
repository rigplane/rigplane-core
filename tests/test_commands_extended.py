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
    """commands/dsp.py migrated onto the bound command map in MOR-2008
    (batch 3): set_attenuator/set_preamp now require cmd_map -- IC-7610's
    own tuples are byte-identical to the deleted fallback's, so the
    expected frames below are unchanged.
    """

    def test_att_on(self, cmd_map):
        frame = set_attenuator(True, cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        # Command29-wrapped: real command is 0x11, data is BCD 18
        assert parsed.command == 0x11
        assert parsed.data == b"\x18"

    def test_att_off(self, cmd_map):
        frame = set_attenuator(False, cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.data == b"\x00"

    def test_set_attenuator_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break.

        Representative for the whole of dsp.py's 37 migrated builders
        (MOR-2008 batch 3): the contract is enforced once, centrally, by
        `_frame.py: require_cmd_map`, so this spot check mirrors the one
        `TestCw`/`TestPowerOnOff` above already carry for their own
        modules rather than repeating it per builder.
        """
        with pytest.raises(TypeError, match="MOR-2006"):
            set_attenuator(True)  # type: ignore[call-arg]

    def test_set_attenuator_rejects_explicit_none_the_same_way(self) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely."""
        with pytest.raises(TypeError, match="MOR-2006"):
            set_attenuator(True, cmd_map=None)


class TestPreamp:
    def test_preamp_off(self, cmd_map):
        frame = set_preamp(0, cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x16
        assert parsed.data == bytes([0])

    def test_preamp_1(self, cmd_map):
        frame = set_preamp(1, cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.data == bytes([1])

    def test_preamp_2(self, cmd_map):
        frame = set_preamp(2, cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.data == bytes([2])


class TestCw:
    """commands/cw.py migrated onto the bound command map in MOR-2008
    (batch 1): both builders now require ``cmd_map``. IC-7610's own
    ``send_cw``/``stop_cw`` tuples are byte-identical to the deleted
    fallback's ``0x17``, so the expected frames below are unchanged.
    """

    def test_send_short(self, cmd_map):
        frames = send_cw("CQ CQ", cmd_map=cmd_map)
        assert len(frames) == 1
        parsed = parse_civ_frame(frames[0])
        assert parsed.command == 0x17
        assert parsed.data == b"CQ CQ"

    def test_send_long_splits(self, cmd_map):
        text = "A" * 65
        frames = send_cw(text, cmd_map=cmd_map)
        assert len(frames) == 3
        # First chunk 30, second 30, third 5
        assert len(parse_civ_frame(frames[0]).data) == 30
        assert len(parse_civ_frame(frames[1]).data) == 30
        assert len(parse_civ_frame(frames[2]).data) == 5

    def test_send_uppercased(self, cmd_map):
        frames = send_cw("cq cq", cmd_map=cmd_map)
        parsed = parse_civ_frame(frames[0])
        assert parsed.data == b"CQ CQ"

    def test_stop_cw(self, cmd_map):
        frame = stop_cw(cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x17
        assert parsed.data == b"\xff"

    def test_send_cw_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        with pytest.raises(TypeError, match="MOR-2006"):
            send_cw("CQ")  # type: ignore[call-arg]

    def test_stop_cw_rejects_explicit_none_the_same_way(self) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely."""
        with pytest.raises(TypeError, match="MOR-2006"):
            stop_cw(cmd_map=None)


class TestPowerOnOff:
    """commands/power.py migrated onto the bound command map in MOR-2008
    (batch 1): all three builders now require ``cmd_map``. IC-7610's own
    tuples are byte-identical to the deleted fallback's ``0x18``, so the
    expected frames below are unchanged.
    """

    def test_power_on(self, cmd_map):
        frame = power_on(cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x18
        assert parsed.data == b"\x01"

    def test_power_off(self, cmd_map):
        frame = power_off(cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x18
        assert parsed.data == b"\x00"

    def test_power_on_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        with pytest.raises(TypeError, match="MOR-2006"):
            power_on()  # type: ignore[call-arg]

    def test_power_off_rejects_explicit_none_the_same_way(self) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely."""
        with pytest.raises(TypeError, match="MOR-2006"):
            power_off(cmd_map=None)


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
