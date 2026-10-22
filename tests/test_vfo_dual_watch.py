"""Unit tests for VFO/dual-watch/scanning commands (Issue #132).

commands/vfo.py migrated onto the bound command map in MOR-2007 Steps 5..N
(module 3, docs/plans/2026-08-29-profile-driven-command-bytes.md §4):
every builder now requires cmd_map, with no hardcoded fallback left. The
frames pinned below are built against a real IC-7610 CommandMap
(``cmd_map`` fixture) instead of hand-typed CI-V literals, per MOR-2006's
"prefer load_rig(...) maps over hand literals" convention -- for every
builder this file exercises, IC-7610's declared bytes are byte-identical
to what the deleted fallback used to build, so the expected literals
below are unchanged from before the migration.

Two classes of pre-migration test do not survive unchanged:

- ``scan_start_type``/``scan_set_resume``'s domain validation
  (``VALID_SCAN_TYPES``/``VALID_SCAN_RESUME``) moved out of the builder
  into the profile-aware caller (MOR-2007 ruling 4) -- the builder-level
  ``rejects_invalid`` tests that pinned it here are replaced by
  ``TestScanTypeDomainValidation``/``TestScanResumeDomainValidation``
  below, which pin it at ``CoreRadio.scan_start``/``scan_set_resume``
  instead, against the real IC-7610 profile domain.
- ``quick_dual_watch()``/``quick_split()`` (the bare, one-shot-trigger
  builders) are deleted (MOR-2007 ruling 2): they always sent a bare-GET
  frame and their only caller never read the reply, so they fired
  nothing. ``TestQuickCommands`` now pins the real ``get_quick_split``/
  ``get_quick_dual_watch``/``set_quick_split``/``set_quick_dual_watch``
  pair that replaced them.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from rigplane import commands
from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, parse_bool_response, parse_level_response
from rigplane.exceptions import CommandError
from rigplane.radio import IcomRadio
from rigplane.rig_loader import load_rig
from rigplane.types import CivFrame
from _command_test_helpers import bind_default_addr_globals

bind_default_addr_globals(globals(), to_addr=IC_7610_ADDR)

RIG_DIR = Path(__file__).resolve().parents[1] / "rigs"


@pytest.fixture()
def cmd_map():
    rig = load_rig(RIG_DIR / "ic7610.toml")
    return rig.to_command_map()


# CI-V frame constants
_PREAMBLE = b"\xfe\xfe"
_TERMINATOR = b"\xfd"

# Command bytes
_CMD_TUNING_STEP = 0x10
_CMD_SCANNING = 0x0E
_CMD_VFO = 0x07
_CMD_CTL_MEM = 0x1A
_SUB_CTL_MEM = 0x05

# VFO sub-codes
_VFO_DUAL_WATCH_OFF = 0xC0
_VFO_DUAL_WATCH_ON = 0xC1
_VFO_DUAL_WATCH_QUERY = 0xC2

# Quick command memory indices
_QUICK_DUAL_WATCH_IDX = b"\x00\x32"
_QUICK_SPLIT_IDX = b"\x00\x33"


def _frame(*payload: int) -> bytes:
    """Build a CI-V frame from raw payload bytes (to, from, cmd, ...)."""
    return _PREAMBLE + bytes([IC_7610_ADDR, CONTROLLER_ADDR, *payload]) + _TERMINATOR


def _response_frame_vfo(sub_byte: int, value: int | None = None) -> CivFrame:
    """Build a CivFrame as the radio returns for command 0x07.

    Since 0x07 is not in _COMMANDS_WITH_SUB, parse_civ_frame stores
    all payload bytes (including the sub-code) in frame.data.
    """
    data = bytes([sub_byte]) if value is None else bytes([sub_byte, value])
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC_7610_ADDR,
        command=_CMD_VFO,
        sub=None,
        data=data,
    )


def _response_frame_ctl_mem(idx: bytes, value: int) -> CivFrame:
    """Build a CivFrame as the radio returns for command 0x1A sub 0x05."""
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC_7610_ADDR,
        command=_CMD_CTL_MEM,
        sub=_SUB_CTL_MEM,
        data=idx + bytes([value]),
    )


def _response_frame_tuning_step(bcd_value: int) -> CivFrame:
    """Build a CivFrame as the radio returns for command 0x10."""
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC_7610_ADDR,
        command=_CMD_TUNING_STEP,
        sub=None,
        data=bytes([bcd_value]),
    )


# ---------------------------------------------------------------------------
# cmd_map is required (Q6's API break, MOR-2006/MOR-2007)
# ---------------------------------------------------------------------------


class TestRequiresCmdMap:
    """Every public builder in vfo.py requires cmd_map -- omitting it, or
    passing it explicitly as None, raises the same self-explaining
    TypeError (config.py's test_get_acc1_mod_level_requires_cmd_map is
    the template)."""

    def test_get_dual_watch_requires_cmd_map(self) -> None:
        with pytest.raises(TypeError, match="MOR-2006"):
            commands.get_dual_watch()  # type: ignore[call-arg]

    def test_get_dual_watch_rejects_explicit_none_the_same_way(self) -> None:
        with pytest.raises(TypeError, match="MOR-2006"):
            commands.get_dual_watch(cmd_map=None)

    def test_set_dual_watch_off_requires_cmd_map(self) -> None:
        with pytest.raises(TypeError, match="MOR-2006"):
            commands.set_dual_watch_off()  # type: ignore[call-arg]

    def test_set_dual_watch_requires_cmd_map(self) -> None:
        """The delegate itself raises too, not just the branches it picks
        between (MOR-2007 ruling 1)."""
        with pytest.raises(TypeError, match="MOR-2006"):
            commands.set_dual_watch(True)  # type: ignore[call-arg]

    def test_get_quick_split_requires_cmd_map(self) -> None:
        with pytest.raises(TypeError, match="MOR-2006"):
            commands.get_quick_split()  # type: ignore[call-arg]

    def test_set_quick_split_requires_cmd_map(self) -> None:
        with pytest.raises(TypeError, match="MOR-2006"):
            commands.set_quick_split(True)  # type: ignore[call-arg]

    def test_scan_start_type_requires_cmd_map(self) -> None:
        with pytest.raises(TypeError, match="MOR-2006"):
            commands.scan_start_type(0x01)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Tuning Step (0x10)
# ---------------------------------------------------------------------------


class TestTuningStep:
    """Tests for get_tuning_step / set_tuning_step."""

    def test_get_tuning_step_builds_correct_frame(self, cmd_map) -> None:
        assert commands.get_tuning_step(cmd_map=cmd_map) == _frame(_CMD_TUNING_STEP)

    def test_set_tuning_step_index_0_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_tuning_step(0, cmd_map=cmd_map) == _frame(
            _CMD_TUNING_STEP, 0x00
        )

    def test_set_tuning_step_index_1_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_tuning_step(1, cmd_map=cmd_map) == _frame(
            _CMD_TUNING_STEP, 0x01
        )

    def test_set_tuning_step_index_5_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_tuning_step(5, cmd_map=cmd_map) == _frame(
            _CMD_TUNING_STEP, 0x05
        )

    def test_set_tuning_step_index_8_builds_correct_frame(self, cmd_map) -> None:
        # Max value per IC-7610.rig
        assert commands.set_tuning_step(8, cmd_map=cmd_map) == _frame(
            _CMD_TUNING_STEP, 0x08
        )

    def test_set_tuning_step_rejects_value_above_maximum(self, cmd_map) -> None:
        with pytest.raises(ValueError):
            commands.set_tuning_step(9, cmd_map=cmd_map)

    def test_set_tuning_step_rejects_negative_value(self, cmd_map) -> None:
        with pytest.raises(ValueError):
            commands.set_tuning_step(-1, cmd_map=cmd_map)

    def test_get_tuning_step_starts_with_preamble(self, cmd_map) -> None:
        assert commands.get_tuning_step(cmd_map=cmd_map).startswith(_PREAMBLE)

    def test_get_tuning_step_ends_with_terminator(self, cmd_map) -> None:
        assert commands.get_tuning_step(cmd_map=cmd_map).endswith(_TERMINATOR)

    def test_get_tuning_step_contains_command_byte(self, cmd_map) -> None:
        assert _CMD_TUNING_STEP in commands.get_tuning_step(cmd_map=cmd_map)

    def test_get_tuning_step_custom_addresses(self, cmd_map) -> None:
        frame = commands.get_tuning_step(to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_set_tuning_step_custom_addresses(self, cmd_map) -> None:
        frame = commands.set_tuning_step(
            3, to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map
        )
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_parse_tuning_step_response_index_0(self) -> None:
        frame = _response_frame_tuning_step(0x00)
        value = parse_level_response(
            frame, command=_CMD_TUNING_STEP, sub=None, bcd_bytes=1
        )
        assert value == 0

    def test_parse_tuning_step_response_index_5(self) -> None:
        frame = _response_frame_tuning_step(0x05)
        value = parse_level_response(
            frame, command=_CMD_TUNING_STEP, sub=None, bcd_bytes=1
        )
        assert value == 5

    def test_parse_tuning_step_response_index_8(self) -> None:
        frame = _response_frame_tuning_step(0x08)
        value = parse_level_response(
            frame, command=_CMD_TUNING_STEP, sub=None, bcd_bytes=1
        )
        assert value == 8

    def test_get_and_set_produce_distinct_frames(self, cmd_map) -> None:
        assert commands.get_tuning_step(cmd_map=cmd_map) != commands.set_tuning_step(
            0, cmd_map=cmd_map
        )


# ---------------------------------------------------------------------------
# Scanning (0x0E)
# ---------------------------------------------------------------------------


class TestScanning:
    """Tests for start_scan / stop_scan."""

    def test_start_scan_builds_correct_frame(self, cmd_map) -> None:
        assert commands.scan_start(cmd_map=cmd_map) == _frame(_CMD_SCANNING, 0x01)

    def test_stop_scan_builds_correct_frame(self, cmd_map) -> None:
        assert commands.scan_stop(cmd_map=cmd_map) == _frame(_CMD_SCANNING, 0x00)

    def test_start_scan_starts_with_preamble(self, cmd_map) -> None:
        assert commands.scan_start(cmd_map=cmd_map).startswith(_PREAMBLE)

    def test_stop_scan_ends_with_terminator(self, cmd_map) -> None:
        assert commands.scan_stop(cmd_map=cmd_map).endswith(_TERMINATOR)

    def test_start_and_stop_scan_differ_only_in_data_byte(self, cmd_map) -> None:
        start = commands.scan_start(cmd_map=cmd_map)
        stop = commands.scan_stop(cmd_map=cmd_map)
        # Same command byte, different data
        assert start != stop
        assert start[4] == _CMD_SCANNING
        assert stop[4] == _CMD_SCANNING

    def test_start_scan_data_byte_is_one(self, cmd_map) -> None:
        frame = commands.scan_start(cmd_map=cmd_map)
        assert frame[5] == 0x01

    def test_stop_scan_data_byte_is_zero(self, cmd_map) -> None:
        frame = commands.scan_stop(cmd_map=cmd_map)
        assert frame[5] == 0x00

    def test_start_scan_custom_addresses(self, cmd_map) -> None:
        frame = commands.scan_start(to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_stop_scan_custom_addresses(self, cmd_map) -> None:
        frame = commands.scan_stop(to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


class TestScanTypes:
    """Tests for extended scan type commands (0x0E with various sub-bytes).

    Which sub-bytes are a radio's legal scan types is now a per-profile
    domain checked by the caller (MOR-2007 ruling 4,
    TestScanTypeDomainValidation below) -- this builder encodes whatever
    byte it is given, unconditionally.
    """

    def test_scan_start_type_programmed(self, cmd_map) -> None:
        """scan_start_type(0x01) builds programmed scan frame."""
        assert commands.scan_start_type(0x01, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0x01
        )

    def test_scan_start_type_programmed_p2(self, cmd_map) -> None:
        """scan_start_type(0x02) builds programmed scan P2 frame."""
        assert commands.scan_start_type(0x02, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0x02
        )

    def test_scan_start_type_df(self, cmd_map) -> None:
        """scan_start_type(0x03) builds delta-F scan frame."""
        assert commands.scan_start_type(0x03, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0x03
        )

    def test_scan_start_type_fine_programmed(self, cmd_map) -> None:
        """scan_start_type(0x12) builds fine programmed scan frame."""
        assert commands.scan_start_type(0x12, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0x12
        )

    def test_scan_start_type_fine_df(self, cmd_map) -> None:
        """scan_start_type(0x13) builds fine dF scan frame -- the byte
        VALID_SCAN_TYPES used to omit (MOR-2007 ruling 4)."""
        assert commands.scan_start_type(0x13, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0x13
        )

    def test_scan_start_type_memory(self, cmd_map) -> None:
        """scan_start_type(0x22) builds memory scan frame."""
        assert commands.scan_start_type(0x22, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0x22
        )

    def test_scan_start_type_select_memory(self, cmd_map) -> None:
        """scan_start_type(0x23) builds select memory scan frame."""
        assert commands.scan_start_type(0x23, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0x23
        )

    def test_scan_start_type_custom_addresses(self, cmd_map) -> None:
        frame = commands.scan_start_type(
            0x03, to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map
        )
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


class TestScanDfSpan:
    """Tests for ΔF scan span selection (0x0E 0xA1-0xA7)."""

    def test_scan_set_df_span_5k(self, cmd_map) -> None:
        assert commands.scan_set_df_span(0xA1, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0xA1
        )

    def test_scan_set_df_span_10k(self, cmd_map) -> None:
        assert commands.scan_set_df_span(0xA2, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0xA2
        )

    def test_scan_set_df_span_20k(self, cmd_map) -> None:
        assert commands.scan_set_df_span(0xA3, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0xA3
        )

    def test_scan_set_df_span_50k(self, cmd_map) -> None:
        assert commands.scan_set_df_span(0xA4, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0xA4
        )

    def test_scan_set_df_span_100k(self, cmd_map) -> None:
        assert commands.scan_set_df_span(0xA5, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0xA5
        )

    def test_scan_set_df_span_500k(self, cmd_map) -> None:
        assert commands.scan_set_df_span(0xA6, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0xA6
        )

    def test_scan_set_df_span_1m(self, cmd_map) -> None:
        assert commands.scan_set_df_span(0xA7, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0xA7
        )

    def test_scan_set_df_span_rejects_invalid(self, cmd_map) -> None:
        with pytest.raises(ValueError, match="df_span"):
            commands.scan_set_df_span(0xA0, cmd_map=cmd_map)

    def test_scan_set_df_span_custom_addresses(self, cmd_map) -> None:
        frame = commands.scan_set_df_span(
            0xA3, to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map
        )
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


class TestScanResume:
    """Tests for scan resume mode (0x0E 0xD0-0xD3).

    Which sub-bytes are a radio's legal resume modes is now a per-profile
    domain checked by the caller (MOR-2007 ruling 4,
    TestScanResumeDomainValidation below) -- this builder encodes whatever
    byte it is given, unconditionally, including 0xD1/0xD2 which IC-7610
    does not actually support (per its CI-V guide).
    """

    def test_scan_set_resume_off(self, cmd_map) -> None:
        assert commands.scan_set_resume(0xD0, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0xD0
        )

    def test_scan_set_resume_5s(self, cmd_map) -> None:
        assert commands.scan_set_resume(0xD1, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0xD1
        )

    def test_scan_set_resume_10s(self, cmd_map) -> None:
        assert commands.scan_set_resume(0xD2, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0xD2
        )

    def test_scan_set_resume_close_and_delay(self, cmd_map) -> None:
        """0xD3 -- "Close&Delay" per the CI-V guides, not "15sec" (MOR-2007
        ruling 4 corrected the stale label; the builder still just encodes
        the byte)."""
        assert commands.scan_set_resume(0xD3, cmd_map=cmd_map) == _frame(
            _CMD_SCANNING, 0xD3
        )

    def test_scan_set_resume_custom_addresses(self, cmd_map) -> None:
        frame = commands.scan_set_resume(
            0xD1, to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map
        )
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


# ---------------------------------------------------------------------------
# Dual Watch (0x07 0xC0 / 0xC1 / 0xC2)
# ---------------------------------------------------------------------------


class TestDualWatch:
    """Tests for set_dual_watch_off / set_dual_watch_on / get_dual_watch.

    set_dual_watch_off/set_dual_watch_on resolve the split, get_/set_-
    prefixed keys (MOR-2007 ruling 1) -- IC-7610 already declares them at
    the same bytes the old shared ``set_dual_watch`` fallback built, so
    the expected frames below are unchanged.
    """

    def test_set_dual_watch_off_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_dual_watch_off(cmd_map=cmd_map) == _frame(
            _CMD_VFO, _VFO_DUAL_WATCH_OFF
        )

    def test_set_dual_watch_on_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_dual_watch_on(cmd_map=cmd_map) == _frame(
            _CMD_VFO, _VFO_DUAL_WATCH_ON
        )

    def test_get_dual_watch_builds_correct_frame(self, cmd_map) -> None:
        assert commands.get_dual_watch(cmd_map=cmd_map) == _frame(
            _CMD_VFO, _VFO_DUAL_WATCH_QUERY
        )

    def test_set_dual_watch_wrapper_true_equals_set_on(self, cmd_map) -> None:
        assert commands.set_dual_watch(
            True, cmd_map=cmd_map
        ) == commands.set_dual_watch_on(cmd_map=cmd_map)

    def test_set_dual_watch_wrapper_false_equals_set_off(self, cmd_map) -> None:
        assert commands.set_dual_watch(
            False, cmd_map=cmd_map
        ) == commands.set_dual_watch_off(cmd_map=cmd_map)

    def test_dual_watch_on_and_off_are_distinct(self, cmd_map) -> None:
        assert commands.set_dual_watch_on(
            cmd_map=cmd_map
        ) != commands.set_dual_watch_off(cmd_map=cmd_map)

    def test_dual_watch_on_and_query_are_distinct(self, cmd_map) -> None:
        assert commands.set_dual_watch_on(cmd_map=cmd_map) != commands.get_dual_watch(
            cmd_map=cmd_map
        )

    def test_dual_watch_off_and_query_are_distinct(self, cmd_map) -> None:
        assert commands.set_dual_watch_off(cmd_map=cmd_map) != commands.get_dual_watch(
            cmd_map=cmd_map
        )

    def test_set_dual_watch_off_starts_with_preamble(self, cmd_map) -> None:
        assert commands.set_dual_watch_off(cmd_map=cmd_map).startswith(_PREAMBLE)

    def test_set_dual_watch_on_ends_with_terminator(self, cmd_map) -> None:
        assert commands.set_dual_watch_on(cmd_map=cmd_map).endswith(_TERMINATOR)

    def test_get_dual_watch_command_byte(self, cmd_map) -> None:
        frame = commands.get_dual_watch(cmd_map=cmd_map)
        assert frame[4] == _CMD_VFO

    def test_set_dual_watch_off_custom_addresses(self, cmd_map) -> None:
        frame = commands.set_dual_watch_off(
            to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map
        )
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_set_dual_watch_on_custom_addresses(self, cmd_map) -> None:
        frame = commands.set_dual_watch_on(
            to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map
        )
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_get_dual_watch_custom_addresses(self, cmd_map) -> None:
        frame = commands.get_dual_watch(to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_parse_dual_watch_response_on(self) -> None:
        # Radio responds: FE FE E0 98 07 C2 01 FD
        # Since 0x07 not in _COMMANDS_WITH_SUB, sub=None, data=b"\xC2\x01"
        frame = _response_frame_vfo(_VFO_DUAL_WATCH_QUERY, 0x01)
        result = parse_bool_response(
            frame, command=_CMD_VFO, prefix=bytes([_VFO_DUAL_WATCH_QUERY])
        )
        assert result is True

    def test_parse_dual_watch_response_off(self) -> None:
        frame = _response_frame_vfo(_VFO_DUAL_WATCH_QUERY, 0x00)
        result = parse_bool_response(
            frame, command=_CMD_VFO, prefix=bytes([_VFO_DUAL_WATCH_QUERY])
        )
        assert result is False


# ---------------------------------------------------------------------------
# Quick Commands (0x1A 0x05 0x00 0x32/0x33) -- persistent menu toggles
# ---------------------------------------------------------------------------


class TestQuickCommands:
    """Tests for get_/set_quick_split / get_/set_quick_dual_watch.

    MOR-2007 ruling 2: these replace the deleted ``quick_dual_watch()``/
    ``quick_split()`` one-shot triggers, which always sent a bare-GET
    frame and never read the reply. The getters below build the identical
    bare-GET frame the deleted triggers used to (bench-confirmed readable/
    writable/persistent on the live IC-7300); the setters are new --
    they append the caller's BCD-encoded boolean as a data byte, the
    same shape ``commands/config.py: set_civ_transceive`` uses for its
    own 0x1A 0x05 boolean toggle.
    """

    def test_get_quick_dual_watch_builds_correct_frame(self, cmd_map) -> None:
        """get_quick_dual_watch builds the same bare 0x1A 0x05 0x00 0x32
        frame the deleted quick_dual_watch() trigger used to."""
        expected = (
            _PREAMBLE
            + bytes([IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CTL_MEM, 0x05, 0x00, 0x32])
            + _TERMINATOR
        )
        assert commands.get_quick_dual_watch(cmd_map=cmd_map) == expected

    def test_get_quick_split_builds_correct_frame(self, cmd_map) -> None:
        """get_quick_split builds the same bare 0x1A 0x05 0x00 0x33 frame
        the deleted quick_split() trigger used to."""
        expected = (
            _PREAMBLE
            + bytes([IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CTL_MEM, 0x05, 0x00, 0x33])
            + _TERMINATOR
        )
        assert commands.get_quick_split(cmd_map=cmd_map) == expected

    def test_set_quick_split_true_appends_data_byte(self, cmd_map) -> None:
        expected = (
            _PREAMBLE
            + bytes(
                [IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CTL_MEM, 0x05, 0x00, 0x33, 0x01]
            )
            + _TERMINATOR
        )
        assert commands.set_quick_split(True, cmd_map=cmd_map) == expected

    def test_set_quick_split_false_appends_data_byte(self, cmd_map) -> None:
        expected = (
            _PREAMBLE
            + bytes(
                [IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CTL_MEM, 0x05, 0x00, 0x33, 0x00]
            )
            + _TERMINATOR
        )
        assert commands.set_quick_split(False, cmd_map=cmd_map) == expected

    def test_set_quick_dual_watch_true_appends_data_byte(self, cmd_map) -> None:
        expected = (
            _PREAMBLE
            + bytes(
                [IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CTL_MEM, 0x05, 0x00, 0x32, 0x01]
            )
            + _TERMINATOR
        )
        assert commands.set_quick_dual_watch(True, cmd_map=cmd_map) == expected

    def test_get_quick_dual_watch_distinct_from_get_quick_split(self, cmd_map) -> None:
        assert commands.get_quick_dual_watch(
            cmd_map=cmd_map
        ) != commands.get_quick_split(cmd_map=cmd_map)

    def test_get_quick_dual_watch_custom_addresses(self, cmd_map) -> None:
        frame = commands.get_quick_dual_watch(
            to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map
        )
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_get_quick_split_custom_addresses(self, cmd_map) -> None:
        frame = commands.get_quick_split(to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


# ---------------------------------------------------------------------------
# Command Distinctness
# ---------------------------------------------------------------------------


class TestCommandDistinctness:
    """Verify that different commands produce distinct byte sequences."""

    def test_dual_watch_distinct_from_quick_dual_watch(self, cmd_map) -> None:
        """Regular dual watch != quick dual watch."""
        assert commands.get_dual_watch(
            cmd_map=cmd_map
        ) != commands.get_quick_dual_watch(cmd_map=cmd_map)

    def test_scanning_distinct_from_tuning_step(self, cmd_map) -> None:
        """Scanning commands != tuning step commands."""
        assert commands.scan_start(cmd_map=cmd_map) != commands.get_tuning_step(
            cmd_map=cmd_map
        )


# ---------------------------------------------------------------------------
# swap_main_sub / swap_vfo_ab distinct methods (Issue #714)
# ---------------------------------------------------------------------------


def _make_radio(model: str) -> IcomRadio:
    """Build a connected IcomRadio for ``model`` with a recording stub for sends."""
    r = IcomRadio("127.0.0.1", model=model, timeout=0.05)
    r._connected = True
    r._check_connected = lambda: None  # type: ignore[method-assign]
    return r


class _RecordingSend:
    """Stand-in for ``_send_civ_raw`` that captures CI-V payloads."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    async def __call__(self, civ: bytes, **_: object) -> None:
        self.frames.append(civ)
        return None


class TestSwapMainSubVsSwapVfoAb:
    """Issue #714 — distinct MAIN/SUB vs A/B swap/equalize methods."""

    @pytest.mark.asyncio
    async def test_ic7610_swap_main_sub_sends_0x07_0xb0(self) -> None:
        r = _make_radio("IC-7610")
        send = _RecordingSend()
        with patch.object(r, "_send_civ_raw", send):
            await r.swap_main_sub()
        assert len(send.frames) == 1
        # Frame tail is 07 B0 FD (command + data + terminator)
        assert send.frames[0].endswith(b"\x07\xb0\xfd")

    @pytest.mark.asyncio
    async def test_ic7610_equalize_main_sub_sends_0x07_0xb1(self) -> None:
        r = _make_radio("IC-7610")
        send = _RecordingSend()
        with patch.object(r, "_send_civ_raw", send):
            await r.equalize_main_sub()
        assert len(send.frames) == 1
        assert send.frames[0].endswith(b"\x07\xb1\xfd")

    @pytest.mark.asyncio
    async def test_ic7610_swap_vfo_ab_raises_command_error(self) -> None:
        """On IC-7610 ``swap_ab_code`` is ``None``; swap_vfo_ab must NOT
        silently fall back to ``swap_main_sub_code`` — that would swap
        MAIN↔SUB instead of A↔B within the receiver.  It must raise.
        """
        r = _make_radio("IC-7610")
        set_vfo_calls: list[str] = []

        async def _fake_set_vfo(vfo: str = "A") -> None:
            set_vfo_calls.append(vfo.upper())

        send = _RecordingSend()
        with (
            patch.object(r, "_set_vfo_wire", _fake_set_vfo),
            patch.object(r, "_send_civ_raw", send),
        ):
            with pytest.raises(CommandError) as exc_info:
                await r.swap_vfo_ab(receiver=0)

        msg = str(exc_info.value)
        assert "swap_vfo_ab not supported" in msg
        assert "IC-7610" in msg
        assert "swap_ab_code" in msg
        # Error message must point the caller at the correct alternative.
        assert "swap_main_sub" in msg
        # No receiver select or CI-V frame emitted on failure.
        assert set_vfo_calls == []
        assert send.frames == []

    @pytest.mark.asyncio
    async def test_ic7610_equalize_vfo_ab_raises_command_error(self) -> None:
        """Same contract as swap_vfo_ab — no silent MAIN→SUB fallback."""
        r = _make_radio("IC-7610")
        set_vfo_calls: list[str] = []

        async def _fake_set_vfo(vfo: str = "A") -> None:
            set_vfo_calls.append(vfo.upper())

        send = _RecordingSend()
        with (
            patch.object(r, "_set_vfo_wire", _fake_set_vfo),
            patch.object(r, "_send_civ_raw", send),
        ):
            with pytest.raises(CommandError) as exc_info:
                await r.equalize_vfo_ab(receiver=0)

        msg = str(exc_info.value)
        assert "equalize_vfo_ab not supported" in msg
        assert "IC-7610" in msg
        assert "equal_ab_code" in msg
        assert "equalize_main_sub" in msg
        assert set_vfo_calls == []
        assert send.frames == []

    @pytest.mark.asyncio
    async def test_ic7300_swap_main_sub_raises(self) -> None:
        """Single-RX profile rejects swap_main_sub with CommandError."""
        r = _make_radio("IC-7300")
        with pytest.raises(CommandError, match="not dual-RX"):
            await r.swap_main_sub()

    @pytest.mark.asyncio
    async def test_ic7300_equalize_main_sub_raises(self) -> None:
        r = _make_radio("IC-7300")
        with pytest.raises(CommandError, match="not dual-RX"):
            await r.equalize_main_sub()

    @pytest.mark.asyncio
    async def test_ic7300_swap_vfo_ab_sends_directly(self) -> None:
        """Single-RX: no receiver select, just the swap opcode."""
        r = _make_radio("IC-7300")
        set_vfo_calls: list[str] = []

        async def _fake_set_vfo(vfo: str = "A") -> None:
            set_vfo_calls.append(vfo.upper())

        send = _RecordingSend()
        with (
            patch.object(r, "_set_vfo_wire", _fake_set_vfo),
            patch.object(r, "_send_civ_raw", send),
        ):
            await r.swap_vfo_ab(receiver=0)

        # No receiver-select step on 1-Rx.
        assert set_vfo_calls == []
        assert len(send.frames) == 1
        # IC-7300 profile declares swap_ab_code = 0xB0 via legacy mapping.
        assert send.frames[0].endswith(b"\x07\xb0\xfd")

    @pytest.mark.asyncio
    async def test_ic7300_equalize_vfo_ab_sends_0x07_0xa0(self) -> None:
        r = _make_radio("IC-7300")
        send = _RecordingSend()
        with patch.object(r, "_send_civ_raw", send):
            await r.equalize_vfo_ab(receiver=0)
        assert len(send.frames) == 1
        # IC-7300 declares equal = [0xA0] in scheme=ab → equal_ab_code.
        assert send.frames[0].endswith(b"\x07\xa0\xfd")

    def test_vfo_exchange_alias_removed(self) -> None:
        """v0.19: deprecated ``vfo_exchange`` alias is gone (raises AttributeError)."""
        r = _make_radio("IC-7610")
        assert not hasattr(r, "vfo_exchange")
        with pytest.raises(AttributeError):
            r.vfo_exchange  # noqa: B018

    def test_vfo_equalize_alias_removed(self) -> None:
        """v0.19: deprecated ``vfo_equalize`` alias is gone (raises AttributeError)."""
        r = _make_radio("IC-7300")
        assert not hasattr(r, "vfo_equalize")
        with pytest.raises(AttributeError):
            r.vfo_equalize  # noqa: B018

    def test_dual_rx_satisfies_dual_receiver_capable_protocol(self) -> None:
        """IC-7610 backend still satisfies ``DualReceiverCapable`` after the
        legacy ``vfo_exchange`` / ``vfo_equalize`` declarations were replaced
        with the canonical ``swap_main_sub`` / ``equalize_main_sub`` methods.
        """
        from rigplane.radio_protocol import DualReceiverCapable

        r = _make_radio("IC-7610")
        assert isinstance(r, DualReceiverCapable)
        # Canonical methods are present and callable.
        assert callable(r.swap_main_sub)
        assert callable(r.equalize_main_sub)
        assert callable(r.set_main_sub_tracking)
        assert callable(r.get_main_sub_tracking)


# ---------------------------------------------------------------------------
# Scan-type / scan-resume domain validation (MOR-2007 ruling 4)
# ---------------------------------------------------------------------------


class TestScanTypeDomainValidation:
    """CoreRadio.scan_start validates ``mode`` against the profile's
    declared ``[scan_types] values`` -- commands/vfo.py: scan_start_type
    itself no longer does (it only encodes the byte it is given)."""

    @pytest.mark.asyncio
    async def test_ic7610_rejects_undeclared_scan_type(self) -> None:
        r = _make_radio("IC-7610")
        send = _RecordingSend()
        with patch.object(r, "_send_civ_raw", send):
            with pytest.raises(ValueError, match="Scan type"):
                await r.scan_start(mode=0xFF)
        assert send.frames == []

    @pytest.mark.asyncio
    async def test_ic7610_accepts_fine_df_scan(self) -> None:
        """0x13 (fine dF scan) -- the byte VALID_SCAN_TYPES used to omit
        on every radio, IC-7610 included (MOR-2007 ruling 4)."""
        r = _make_radio("IC-7610")
        send = _RecordingSend()
        with patch.object(r, "_send_civ_raw", send):
            await r.scan_start(mode=0x13)
        assert len(send.frames) == 1
        assert send.frames[0].endswith(b"\x0e\x13\xfd")

    @pytest.mark.asyncio
    async def test_ic7610_accepts_programmed_scan(self) -> None:
        r = _make_radio("IC-7610")
        send = _RecordingSend()
        with patch.object(r, "_send_civ_raw", send):
            await r.scan_start(mode=0x01)
        assert len(send.frames) == 1
        assert send.frames[0].endswith(b"\x0e\x01\xfd")


class TestScanResumeDomainValidation:
    """CoreRadio.scan_set_resume validates ``mode`` against the profile's
    declared ``[scan_resume] values`` -- commands/vfo.py: scan_set_resume
    itself no longer does."""

    @pytest.mark.asyncio
    async def test_ic7610_rejects_undeclared_resume_mode(self) -> None:
        """0xD1 ("5s") is not in IC-7610's declared domain -- its CI-V
        guide documents only 0xD0/0xD3 (MOR-2007 ruling 4), unlike the old
        code-level VALID_SCAN_RESUME frozenset, which accepted it on every
        radio regardless."""
        r = _make_radio("IC-7610")
        send = _RecordingSend()
        with patch.object(r, "_send_civ_raw", send):
            with pytest.raises(ValueError, match="Scan resume mode"):
                await r.scan_set_resume(0xD1)
        assert send.frames == []

    @pytest.mark.asyncio
    async def test_ic7610_accepts_off(self) -> None:
        r = _make_radio("IC-7610")
        send = _RecordingSend()
        with patch.object(r, "_send_civ_raw", send):
            await r.scan_set_resume(0xD0)
        assert len(send.frames) == 1
        assert send.frames[0].endswith(b"\x0e\xd0\xfd")

    @pytest.mark.asyncio
    async def test_ic7610_accepts_close_and_delay(self) -> None:
        r = _make_radio("IC-7610")
        send = _RecordingSend()
        with patch.object(r, "_send_civ_raw", send):
            await r.scan_set_resume(0xD3)
        assert len(send.frames) == 1
        assert send.frames[0].endswith(b"\x0e\xd3\xfd")
