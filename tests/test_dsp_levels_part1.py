"""Unit tests for DSP level commands — Part 1: APF/NR/PBT/NB (Issue #130)."""

from pathlib import Path

import pytest

from rigplane import commands
from rigplane import IC_7610_ADDR
from rigplane.commands import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    RECEIVER_SUB,
    parse_level_response,
)
from rigplane.rig_loader import load_rig
from rigplane.types import CivFrame
from _command_test_helpers import bind_default_addr_globals

bind_default_addr_globals(globals(), to_addr=IC_7610_ADDR)

# commands/levels.py migrated onto the bound command map in MOR-2006 Steps
# 5..N (module 2): get_/set_apf_type_level, get_/set_nr_level,
# get_/set_pbt_inner, get_/set_pbt_outer and get_/set_nb_level now require
# cmd_map. This file exercises only IC-7610 (bind_default_addr_globals
# above), and IC-7610 declares byte-identical [0x14, sub] wire tuples for
# all five (no menu address, no divergence row for any of them), so every
# expected literal below is unchanged; only the cmd_map= wiring is new.
RIG_DIR = Path(__file__).resolve().parents[1] / "rigs"
_IC7610_CMD_MAP = load_rig(RIG_DIR / "ic7610.toml").to_command_map()

# CI-V frame constants
_PREAMBLE = b"\xfe\xfe"
_TERMINATOR = b"\xfd"
_CMD_LEVEL = 0x14
_CMD_CMD29 = 0x29

# Sub-command bytes under 0x14
_SUB_APF_TYPE_LEVEL = 0x05
_SUB_NR_LEVEL = 0x06
_SUB_PBT_INNER = 0x07
_SUB_PBT_OUTER = 0x08
_SUB_NB_LEVEL = 0x12


def _cmd29_level_get(sub: int, receiver: int = RECEIVER_MAIN) -> bytes:
    """Expected bytes for a cmd29-wrapped level get frame."""
    return (
        _PREAMBLE
        + bytes([IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CMD29, receiver, _CMD_LEVEL, sub])
        + _TERMINATOR
    )


def _level_bcd(value: int) -> bytes:
    """Encode 0-255 level as 2-byte BCD (mirrors _level_bcd_encode)."""
    d = f"{value:04d}"
    return bytes([(int(d[0]) << 4) | int(d[1]), (int(d[2]) << 4) | int(d[3])])


def _cmd29_level_set(sub: int, level: int, receiver: int = RECEIVER_MAIN) -> bytes:
    """Expected bytes for a cmd29-wrapped level set frame."""
    return (
        _PREAMBLE
        + bytes([IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CMD29, receiver, _CMD_LEVEL, sub])
        + _level_bcd(level)
        + _TERMINATOR
    )


def _level_response_frame(sub: int, level: int) -> CivFrame:
    """Build a CivFrame as a radio would return for a level command."""
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC_7610_ADDR,
        command=_CMD_LEVEL,
        sub=sub,
        data=_level_bcd(level),
    )


# ---------------------------------------------------------------------------
# APF Type Level (cmd29, int 0-255)
# ---------------------------------------------------------------------------


class TestAPFTypeLevel:
    """Tests for get_apf_type_level / set_apf_type_level."""

    def test_get_apf_type_level_main_receiver(self) -> None:
        assert commands.get_apf_type_level(
            receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_get(_SUB_APF_TYPE_LEVEL, RECEIVER_MAIN)

    def test_get_apf_type_level_sub_receiver(self) -> None:
        assert commands.get_apf_type_level(
            receiver=RECEIVER_SUB, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_get(_SUB_APF_TYPE_LEVEL, RECEIVER_SUB)

    def test_get_apf_type_level_default_is_main(self) -> None:
        assert commands.get_apf_type_level(
            cmd_map=_IC7610_CMD_MAP
        ) == commands.get_apf_type_level(
            receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        )

    def test_set_apf_type_level_main_receiver(self) -> None:
        assert commands.set_apf_type_level(
            128, receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_set(_SUB_APF_TYPE_LEVEL, 128, RECEIVER_MAIN)

    def test_set_apf_type_level_sub_receiver(self) -> None:
        assert commands.set_apf_type_level(
            64, receiver=RECEIVER_SUB, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_set(_SUB_APF_TYPE_LEVEL, 64, RECEIVER_SUB)

    def test_set_apf_type_level_boundary_zero(self) -> None:
        assert commands.set_apf_type_level(
            0, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_set(_SUB_APF_TYPE_LEVEL, 0)

    def test_set_apf_type_level_boundary_255(self) -> None:
        assert commands.set_apf_type_level(
            255, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_set(_SUB_APF_TYPE_LEVEL, 255)

    def test_set_apf_type_level_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            commands.set_apf_type_level(-1, cmd_map=_IC7610_CMD_MAP)

    def test_set_apf_type_level_rejects_over_255(self) -> None:
        with pytest.raises(ValueError):
            commands.set_apf_type_level(256, cmd_map=_IC7610_CMD_MAP)

    def test_parse_apf_type_level_response(self) -> None:
        frame = _level_response_frame(_SUB_APF_TYPE_LEVEL, 128)
        value = parse_level_response(frame, sub=_SUB_APF_TYPE_LEVEL)
        assert value == 128

    def test_parse_apf_type_level_response_zero(self) -> None:
        frame = _level_response_frame(_SUB_APF_TYPE_LEVEL, 0)
        value = parse_level_response(frame, sub=_SUB_APF_TYPE_LEVEL)
        assert value == 0

    def test_parse_apf_type_level_response_max(self) -> None:
        frame = _level_response_frame(_SUB_APF_TYPE_LEVEL, 255)
        value = parse_level_response(frame, sub=_SUB_APF_TYPE_LEVEL)
        assert value == 255


# ---------------------------------------------------------------------------
# NR Level (cmd29, int 0-255)
# ---------------------------------------------------------------------------


class TestNRLevel:
    """Tests for get_nr_level / set_nr_level."""

    def test_get_nr_level_main_receiver(self) -> None:
        assert commands.get_nr_level(
            receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_get(_SUB_NR_LEVEL, RECEIVER_MAIN)

    def test_get_nr_level_sub_receiver(self) -> None:
        assert commands.get_nr_level(
            receiver=RECEIVER_SUB, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_get(_SUB_NR_LEVEL, RECEIVER_SUB)

    def test_get_nr_level_default_is_main(self) -> None:
        assert commands.get_nr_level(cmd_map=_IC7610_CMD_MAP) == commands.get_nr_level(
            receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        )

    def test_set_nr_level_main_receiver(self) -> None:
        assert commands.set_nr_level(
            100, receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_set(_SUB_NR_LEVEL, 100, RECEIVER_MAIN)

    def test_set_nr_level_sub_receiver(self) -> None:
        assert commands.set_nr_level(
            200, receiver=RECEIVER_SUB, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_set(_SUB_NR_LEVEL, 200, RECEIVER_SUB)

    def test_set_nr_level_boundary_zero(self) -> None:
        assert commands.set_nr_level(0, cmd_map=_IC7610_CMD_MAP) == _cmd29_level_set(
            _SUB_NR_LEVEL, 0
        )

    def test_set_nr_level_boundary_255(self) -> None:
        assert commands.set_nr_level(255, cmd_map=_IC7610_CMD_MAP) == _cmd29_level_set(
            _SUB_NR_LEVEL, 255
        )

    def test_set_nr_level_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            commands.set_nr_level(-1, cmd_map=_IC7610_CMD_MAP)

    def test_set_nr_level_rejects_over_255(self) -> None:
        with pytest.raises(ValueError):
            commands.set_nr_level(256, cmd_map=_IC7610_CMD_MAP)

    def test_parse_nr_level_response(self) -> None:
        frame = _level_response_frame(_SUB_NR_LEVEL, 50)
        value = parse_level_response(frame, sub=_SUB_NR_LEVEL)
        assert value == 50

    def test_parse_nr_level_response_zero(self) -> None:
        frame = _level_response_frame(_SUB_NR_LEVEL, 0)
        value = parse_level_response(frame, sub=_SUB_NR_LEVEL)
        assert value == 0

    def test_parse_nr_level_response_max(self) -> None:
        frame = _level_response_frame(_SUB_NR_LEVEL, 255)
        value = parse_level_response(frame, sub=_SUB_NR_LEVEL)
        assert value == 255


# ---------------------------------------------------------------------------
# PBT Inner (cmd29, int 0-255)
# ---------------------------------------------------------------------------


class TestPBTInner:
    """Tests for get_pbt_inner / set_pbt_inner."""

    def test_get_pbt_inner_main_receiver(self) -> None:
        assert commands.get_pbt_inner(
            receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_get(_SUB_PBT_INNER, RECEIVER_MAIN)

    def test_get_pbt_inner_sub_receiver(self) -> None:
        assert commands.get_pbt_inner(
            receiver=RECEIVER_SUB, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_get(_SUB_PBT_INNER, RECEIVER_SUB)

    def test_get_pbt_inner_default_is_main(self) -> None:
        assert commands.get_pbt_inner(
            cmd_map=_IC7610_CMD_MAP
        ) == commands.get_pbt_inner(receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP)

    def test_set_pbt_inner_main_receiver(self) -> None:
        assert commands.set_pbt_inner(
            75, receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_set(_SUB_PBT_INNER, 75, RECEIVER_MAIN)

    def test_set_pbt_inner_sub_receiver(self) -> None:
        assert commands.set_pbt_inner(
            150, receiver=RECEIVER_SUB, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_set(_SUB_PBT_INNER, 150, RECEIVER_SUB)

    def test_set_pbt_inner_boundary_zero(self) -> None:
        assert commands.set_pbt_inner(0, cmd_map=_IC7610_CMD_MAP) == _cmd29_level_set(
            _SUB_PBT_INNER, 0
        )

    def test_set_pbt_inner_boundary_255(self) -> None:
        assert commands.set_pbt_inner(255, cmd_map=_IC7610_CMD_MAP) == _cmd29_level_set(
            _SUB_PBT_INNER, 255
        )

    def test_set_pbt_inner_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            commands.set_pbt_inner(-1, cmd_map=_IC7610_CMD_MAP)

    def test_set_pbt_inner_rejects_over_255(self) -> None:
        with pytest.raises(ValueError):
            commands.set_pbt_inner(256, cmd_map=_IC7610_CMD_MAP)

    def test_parse_pbt_inner_response(self) -> None:
        frame = _level_response_frame(_SUB_PBT_INNER, 75)
        value = parse_level_response(frame, sub=_SUB_PBT_INNER)
        assert value == 75

    def test_parse_pbt_inner_response_zero(self) -> None:
        frame = _level_response_frame(_SUB_PBT_INNER, 0)
        value = parse_level_response(frame, sub=_SUB_PBT_INNER)
        assert value == 0

    def test_parse_pbt_inner_response_max(self) -> None:
        frame = _level_response_frame(_SUB_PBT_INNER, 255)
        value = parse_level_response(frame, sub=_SUB_PBT_INNER)
        assert value == 255

    def test_pbt_inner_sub_distinct_from_pbt_outer(self) -> None:
        assert commands.get_pbt_inner(
            cmd_map=_IC7610_CMD_MAP
        ) != commands.get_pbt_outer(cmd_map=_IC7610_CMD_MAP)


# ---------------------------------------------------------------------------
# PBT Outer (cmd29, int 0-255)
# ---------------------------------------------------------------------------


class TestPBTOuter:
    """Tests for get_pbt_outer / set_pbt_outer."""

    def test_get_pbt_outer_main_receiver(self) -> None:
        assert commands.get_pbt_outer(
            receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_get(_SUB_PBT_OUTER, RECEIVER_MAIN)

    def test_get_pbt_outer_sub_receiver(self) -> None:
        assert commands.get_pbt_outer(
            receiver=RECEIVER_SUB, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_get(_SUB_PBT_OUTER, RECEIVER_SUB)

    def test_get_pbt_outer_default_is_main(self) -> None:
        assert commands.get_pbt_outer(
            cmd_map=_IC7610_CMD_MAP
        ) == commands.get_pbt_outer(receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP)

    def test_set_pbt_outer_main_receiver(self) -> None:
        assert commands.set_pbt_outer(
            90, receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_set(_SUB_PBT_OUTER, 90, RECEIVER_MAIN)

    def test_set_pbt_outer_sub_receiver(self) -> None:
        assert commands.set_pbt_outer(
            180, receiver=RECEIVER_SUB, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_set(_SUB_PBT_OUTER, 180, RECEIVER_SUB)

    def test_set_pbt_outer_boundary_zero(self) -> None:
        assert commands.set_pbt_outer(0, cmd_map=_IC7610_CMD_MAP) == _cmd29_level_set(
            _SUB_PBT_OUTER, 0
        )

    def test_set_pbt_outer_boundary_255(self) -> None:
        assert commands.set_pbt_outer(255, cmd_map=_IC7610_CMD_MAP) == _cmd29_level_set(
            _SUB_PBT_OUTER, 255
        )

    def test_set_pbt_outer_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            commands.set_pbt_outer(-1, cmd_map=_IC7610_CMD_MAP)

    def test_set_pbt_outer_rejects_over_255(self) -> None:
        with pytest.raises(ValueError):
            commands.set_pbt_outer(256, cmd_map=_IC7610_CMD_MAP)

    def test_parse_pbt_outer_response(self) -> None:
        frame = _level_response_frame(_SUB_PBT_OUTER, 90)
        value = parse_level_response(frame, sub=_SUB_PBT_OUTER)
        assert value == 90

    def test_parse_pbt_outer_response_zero(self) -> None:
        frame = _level_response_frame(_SUB_PBT_OUTER, 0)
        value = parse_level_response(frame, sub=_SUB_PBT_OUTER)
        assert value == 0

    def test_parse_pbt_outer_response_max(self) -> None:
        frame = _level_response_frame(_SUB_PBT_OUTER, 255)
        value = parse_level_response(frame, sub=_SUB_PBT_OUTER)
        assert value == 255


# ---------------------------------------------------------------------------
# NB Level (cmd29, int 0-255)
# ---------------------------------------------------------------------------


class TestNBLevel:
    """Tests for get_nb_level / set_nb_level."""

    def test_get_nb_level_main_receiver(self) -> None:
        assert commands.get_nb_level(
            receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_get(_SUB_NB_LEVEL, RECEIVER_MAIN)

    def test_get_nb_level_sub_receiver(self) -> None:
        assert commands.get_nb_level(
            receiver=RECEIVER_SUB, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_get(_SUB_NB_LEVEL, RECEIVER_SUB)

    def test_get_nb_level_default_is_main(self) -> None:
        assert commands.get_nb_level(cmd_map=_IC7610_CMD_MAP) == commands.get_nb_level(
            receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        )

    def test_set_nb_level_main_receiver(self) -> None:
        assert commands.set_nb_level(
            55, receiver=RECEIVER_MAIN, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_set(_SUB_NB_LEVEL, 55, RECEIVER_MAIN)

    def test_set_nb_level_sub_receiver(self) -> None:
        assert commands.set_nb_level(
            210, receiver=RECEIVER_SUB, cmd_map=_IC7610_CMD_MAP
        ) == _cmd29_level_set(_SUB_NB_LEVEL, 210, RECEIVER_SUB)

    def test_set_nb_level_boundary_zero(self) -> None:
        assert commands.set_nb_level(0, cmd_map=_IC7610_CMD_MAP) == _cmd29_level_set(
            _SUB_NB_LEVEL, 0
        )

    def test_set_nb_level_boundary_255(self) -> None:
        assert commands.set_nb_level(255, cmd_map=_IC7610_CMD_MAP) == _cmd29_level_set(
            _SUB_NB_LEVEL, 255
        )

    def test_set_nb_level_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            commands.set_nb_level(-1, cmd_map=_IC7610_CMD_MAP)

    def test_set_nb_level_rejects_over_255(self) -> None:
        with pytest.raises(ValueError):
            commands.set_nb_level(256, cmd_map=_IC7610_CMD_MAP)

    def test_parse_nb_level_response(self) -> None:
        frame = _level_response_frame(_SUB_NB_LEVEL, 55)
        value = parse_level_response(frame, sub=_SUB_NB_LEVEL)
        assert value == 55

    def test_parse_nb_level_response_zero(self) -> None:
        frame = _level_response_frame(_SUB_NB_LEVEL, 0)
        value = parse_level_response(frame, sub=_SUB_NB_LEVEL)
        assert value == 0

    def test_parse_nb_level_response_max(self) -> None:
        frame = _level_response_frame(_SUB_NB_LEVEL, 255)
        value = parse_level_response(frame, sub=_SUB_NB_LEVEL)
        assert value == 255

    def test_nb_level_sub_distinct_from_nr_level(self) -> None:
        assert commands.get_nb_level(cmd_map=_IC7610_CMD_MAP) != commands.get_nr_level(
            cmd_map=_IC7610_CMD_MAP
        )
