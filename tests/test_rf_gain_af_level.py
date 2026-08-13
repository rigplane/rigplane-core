"""Tests for RF Gain and AF Level CI-V commands."""

import pytest
import rigplane.commands as raw_commands

from rigplane import IC_7610_ADDR
from rigplane.commands import (
    get_rf_gain,
    set_rf_gain,
    get_af_level,
    set_af_level,
    build_cmd29_frame,
)
from _command_test_helpers import bind_default_addr_globals, bind_default_addr_module

bind_default_addr_module(raw_commands, to_addr=IC_7610_ADDR)
bind_default_addr_globals(globals(), to_addr=IC_7610_ADDR)

# IC-7610 declares cmd29 routes for 0x14/0x02 (RF gain) and 0x14/0x01 (AF
# level), so the builders' command29=True default now wraps MAIN too
# (MOR-1543) — these expected frames are cmd29-wrapped for receiver 0.


class TestRfGainCommands:
    """Test RF Gain CI-V frame encoding."""

    def test_get_rf_gain_frame(self) -> None:
        frame = get_rf_gain()
        # 0x14 = level command, 0x02 = RF gain sub
        expected = build_cmd29_frame(0x98, 0xE0, 0x14, sub=0x02, receiver=0)
        assert frame == expected

    def test_set_rf_gain_128(self) -> None:
        frame = set_rf_gain(128)
        # 128 -> BCD 0x01 0x28
        expected = build_cmd29_frame(
            0x98, 0xE0, 0x14, sub=0x02, data=b"\x01\x28", receiver=0
        )
        assert frame == expected

    def test_set_rf_gain_zero(self) -> None:
        frame = set_rf_gain(0)
        expected = build_cmd29_frame(
            0x98, 0xE0, 0x14, sub=0x02, data=b"\x00\x00", receiver=0
        )
        assert frame == expected

    def test_set_rf_gain_max(self) -> None:
        frame = set_rf_gain(255)
        expected = build_cmd29_frame(
            0x98, 0xE0, 0x14, sub=0x02, data=b"\x02\x55", receiver=0
        )
        assert frame == expected

    def test_set_rf_gain_out_of_range_high(self) -> None:
        with pytest.raises(ValueError):
            set_rf_gain(256)

    def test_set_rf_gain_out_of_range_low(self) -> None:
        with pytest.raises(ValueError):
            set_rf_gain(-1)


class TestAfLevelCommands:
    """Test AF Level CI-V frame encoding."""

    def test_get_af_level_frame(self) -> None:
        frame = get_af_level()
        # 0x14 = level command, 0x01 = AF level sub
        expected = build_cmd29_frame(0x98, 0xE0, 0x14, sub=0x01, receiver=0)
        assert frame == expected

    def test_set_af_level_128(self) -> None:
        frame = set_af_level(128)
        expected = build_cmd29_frame(
            0x98, 0xE0, 0x14, sub=0x01, data=b"\x01\x28", receiver=0
        )
        assert frame == expected

    def test_set_af_level_zero(self) -> None:
        frame = set_af_level(0)
        expected = build_cmd29_frame(
            0x98, 0xE0, 0x14, sub=0x01, data=b"\x00\x00", receiver=0
        )
        assert frame == expected

    def test_set_af_level_max(self) -> None:
        frame = set_af_level(255)
        expected = build_cmd29_frame(
            0x98, 0xE0, 0x14, sub=0x01, data=b"\x02\x55", receiver=0
        )
        assert frame == expected

    def test_set_af_level_out_of_range_high(self) -> None:
        with pytest.raises(ValueError):
            set_af_level(256)

    def test_set_af_level_out_of_range_low(self) -> None:
        with pytest.raises(ValueError):
            set_af_level(-1)


class TestRfGainAfLevelSubCommandDifference:
    """Verify RF Gain and AF Level produce different frames."""

    def test_get_commands_differ(self) -> None:
        assert get_rf_gain() != get_af_level()

    def test_set_commands_differ(self) -> None:
        assert set_rf_gain(128) != set_af_level(128)

    def test_rf_gain_uses_sub_02(self) -> None:
        frame = get_rf_gain()
        # cmd29-wrapped: FE FE to from 29 <receiver> 14 <sub>
        assert frame[7] == 0x02

    def test_af_level_uses_sub_01(self) -> None:
        frame = get_af_level()
        assert frame[7] == 0x01
