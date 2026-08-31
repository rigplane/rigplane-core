"""Tests for Main/Sub Tracking command (CI-V 0x16 0x5E)."""

import pytest

import rigplane.commands as raw_commands

from pathlib import Path

from rigplane import IC_7610_ADDR
from rigplane.commands import (
    get_main_sub_tracking,
    set_main_sub_tracking,
    parse_civ_frame,
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


class TestGetMainSubTracking:
    """Test get_main_sub_tracking frame builder.

    commands/mode.py migrated onto the bound command map in MOR-2008
    (batch 2): get_main_sub_tracking now requires cmd_map -- zero
    divergence, so IC-7610's own map declares the identical bytes the
    fallback used to build, and the expected frames below are unchanged.
    """

    def test_get_frame_bytes(self, cmd_map) -> None:
        frame = get_main_sub_tracking(cmd_map=cmd_map)
        # CI-V: FE FE 98 E0 16 5E FD
        assert frame == b"\xfe\xfe\x98\xe0\x16\x5e\xfd"

    def test_get_frame_custom_addr(self, cmd_map) -> None:
        frame = get_main_sub_tracking(to_addr=0x94, from_addr=0xE1, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x94\xe1\x16\x5e\xfd"

    def test_get_frame_parsed(self, cmd_map) -> None:
        frame = get_main_sub_tracking(cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x16
        assert parsed.sub == 0x5E
        assert parsed.data == b""

    def test_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        with pytest.raises(TypeError, match="MOR-2006"):
            get_main_sub_tracking()  # type: ignore[call-arg]


class TestSetMainSubTracking:
    """Test set_main_sub_tracking frame builder."""

    def test_set_on_frame_bytes(self, cmd_map) -> None:
        frame = set_main_sub_tracking(True, cmd_map=cmd_map)
        # CI-V: FE FE 98 E0 16 5E 01 FD
        assert frame == b"\xfe\xfe\x98\xe0\x16\x5e\x01\xfd"

    def test_set_off_frame_bytes(self, cmd_map) -> None:
        frame = set_main_sub_tracking(False, cmd_map=cmd_map)
        # CI-V: FE FE 98 E0 16 5E 00 FD
        assert frame == b"\xfe\xfe\x98\xe0\x16\x5e\x00\xfd"

    def test_set_on_parsed(self, cmd_map) -> None:
        frame = set_main_sub_tracking(True, cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x16
        assert parsed.sub == 0x5E
        assert parsed.data == b"\x01"

    def test_set_off_parsed(self, cmd_map) -> None:
        frame = set_main_sub_tracking(False, cmd_map=cmd_map)
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0x16
        assert parsed.sub == 0x5E
        assert parsed.data == b"\x00"

    def test_set_custom_addr(self, cmd_map) -> None:
        frame = set_main_sub_tracking(
            True, to_addr=0x94, from_addr=0xE1, cmd_map=cmd_map
        )
        assert frame == b"\xfe\xfe\x94\xe1\x16\x5e\x01\xfd"

    def test_rejects_explicit_none_the_same_way(self, cmd_map) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely."""
        with pytest.raises(TypeError, match="MOR-2006"):
            set_main_sub_tracking(True, cmd_map=None)


class TestMainSubTrackingState:
    """Test that RadioState and _CivRxMixin handle 0x16 0x5E frames."""

    def test_radio_state_has_field(self) -> None:
        from rigplane.radio_state import RadioState

        rs = RadioState()
        assert hasattr(rs, "main_sub_tracking")
        assert rs.main_sub_tracking is False

    def test_radio_state_to_dict_includes_field(self) -> None:
        from rigplane.radio_state import RadioState

        rs = RadioState()
        d = rs.to_dict()
        assert "main_sub_tracking" in d
        assert d["main_sub_tracking"] is False

    def test_radio_state_field_set(self) -> None:
        from rigplane.radio_state import RadioState

        rs = RadioState()
        rs.main_sub_tracking = True
        assert rs.main_sub_tracking is True
