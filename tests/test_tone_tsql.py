"""Unit tests for repeater tone/TSQL commands (#134).

Commands tested:
  0x16 0x42 — Repeater Tone enable/disable
  0x16 0x43 — Repeater TSQL enable/disable
  0x1B 0x00 — CTCSS Tone frequency (get/set + parse)
  0x1B 0x01 — TSQL frequency (get/set + parse)

Builder-level, not IC-7610-specific despite the file's own historical
name: the ``cmd_map`` fixture below is IC-7300 (MOR-2008 batch 2 --
IC-7610 declares this whole family absent). ``command29`` is a
caller-supplied builder argument (default ``True``), independent of
which profile's map supplies the inner command/sub bytes, so every
case in this file still builds a cmd29-wrapped frame regardless of the
map -- that is a property of calling these builders with no explicit
``command29=False``, not a claim that IC-7300 (single-receiver, no
cmd29 routes) would ever really send one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane import commands
from rigplane import IC_7610_ADDR
from rigplane.commands import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    RECEIVER_SUB,
    _decode_tone_freq,
    parse_bool_response,
    parse_tone_freq_response,
    parse_tsql_freq_response,
)
from rigplane.rig_loader import load_rig
from rigplane.types import CivFrame
from _command_test_helpers import bind_default_addr_globals

bind_default_addr_globals(globals(), to_addr=IC_7610_ADDR)

RIG_DIR = Path(__file__).resolve().parents[1] / "rigs"


@pytest.fixture()
def cmd_map():
    """commands/tone.py migrated onto the bound command map in MOR-2008
    (batch 2): every builder in this file now requires cmd_map -- zero
    divergence, so every profile that declares this family agrees with
    the fallback's own bytes exactly, and the expected frames throughout
    this file are unchanged. Uses IC-7300, not IC-7610 (despite this
    file's own historical name): IC-7610 has no FM-repeater CTCSS tone
    feature and does not declare this family at all (MOR-660/661/682,
    re-checked and left that way at D2 MOR-2017 -- see
    ``rigs/ic7610.toml``'s own ``{ absent = ... }`` row for each of the
    eight commands), so calling any of these builders directly with a
    bare ``CommandMap`` built from it -- as this file does, not through
    ``BoundCommands``, which is what turns a declared-absent lookup into
    a ``CommandError`` -- still raises ``KeyError``. ``to_addr``
    still defaults to IC_7610_ADDR via ``bind_default_addr_globals``
    above -- unaffected, since a builder's address arguments are
    independent of which profile's map supplies its
    wire bytes.
    """
    rig = load_rig(RIG_DIR / "ic7300.toml")
    return rig.to_command_map()


# ---------------------------------------------------------------------------
# Frame-level constants
# ---------------------------------------------------------------------------

_PREAMBLE = b"\xfe\xfe"
_TERMINATOR = b"\xfd"
_CMD_PREAMP = 0x16
_CMD_TONE = 0x1B
_CMD_CMD29 = 0x29
_SUB_REPEATER_TONE = 0x42
_SUB_REPEATER_TSQL = 0x43
_SUB_TONE_FREQ = 0x00
_SUB_TSQL_FREQ = 0x01

# ---------------------------------------------------------------------------
# Frame-building helpers (expected byte sequences)
# ---------------------------------------------------------------------------


def _cmd29_preamp_get(sub: int, receiver: int = RECEIVER_MAIN) -> bytes:
    """Expected bytes for a cmd29-wrapped 0x16 get."""
    return (
        _PREAMBLE
        + bytes([IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CMD29, receiver, _CMD_PREAMP, sub])
        + _TERMINATOR
    )


def _cmd29_preamp_set(sub: int, value: int, receiver: int = RECEIVER_MAIN) -> bytes:
    """Expected bytes for a cmd29-wrapped 0x16 set."""
    return (
        _PREAMBLE
        + bytes(
            [
                IC_7610_ADDR,
                CONTROLLER_ADDR,
                _CMD_CMD29,
                receiver,
                _CMD_PREAMP,
                sub,
                value,
            ]
        )
        + _TERMINATOR
    )


def _cmd29_tone_get(sub: int, receiver: int = RECEIVER_MAIN) -> bytes:
    """Expected bytes for a cmd29-wrapped 0x1B get (no data)."""
    return (
        _PREAMBLE
        + bytes([IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CMD29, receiver, _CMD_TONE, sub])
        + _TERMINATOR
    )


def _cmd29_tone_set(sub: int, bcd: bytes, receiver: int = RECEIVER_MAIN) -> bytes:
    """Expected bytes for a cmd29-wrapped 0x1B set with BCD payload."""
    return (
        _PREAMBLE
        + bytes([IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CMD29, receiver, _CMD_TONE, sub])
        + bcd
        + _TERMINATOR
    )


# CivFrame helpers for response parsing tests (radio→controller direction).


def _preamp_response(sub: int, data: bytes, receiver: int = RECEIVER_MAIN) -> CivFrame:
    """Simulate a cmd29 response from the radio for 0x16 commands."""
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC_7610_ADDR,
        command=_CMD_PREAMP,
        sub=sub,
        data=data,
        receiver=receiver,
    )


def _tone_freq_response(bcd: bytes, receiver: int | None = None) -> CivFrame:
    """Simulate a response frame for 0x1B 0x00 (tone freq)."""
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC_7610_ADDR,
        command=_CMD_TONE,
        sub=_SUB_TONE_FREQ,
        data=bcd,
        receiver=receiver,
    )


def _tsql_freq_response(bcd: bytes, receiver: int | None = None) -> CivFrame:
    """Simulate a response frame for 0x1B 0x01 (TSQL freq)."""
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC_7610_ADDR,
        command=_CMD_TONE,
        sub=_SUB_TSQL_FREQ,
        data=bcd,
        receiver=receiver,
    )


# ---------------------------------------------------------------------------
# BCD encoding reference values
# ---------------------------------------------------------------------------

# freq_hz → expected 3-byte BCD encoding.
#
# Layout: 3 bytes hold six packed BCD digits, read as a decimal integer of
# tenths of a Hz -- [0][0][100Hz digit 0-2][10Hz digit][1Hz digit]
# [0.1Hz digit]. Confirmed identical, 2026-08-31, in all four local CI-V
# references (command 1B 00/1B 01, "Repeater tone/tone squelch frequency
# settings"): IC-705 CI-V Reference Guide 2020 p.21, IC-7300 Advanced
# Manual (rev. 11a) p.19-13, IC-9700 CI-V Reference Guide p.19, IC-7610
# CI-V Reference Guide 2021 p.13.
#
# 88.5 Hz (00 08 85) is a live capture, not computed: bench IC-7300,
# 2026-09-01, 115200 CI-V (owner-reported), bypassing RigPlane --
#   request fe fe 94 e0 1b 00 fd -> reply fe fe e0 94 1b 00 00 08 85 fd
# (see test_decode_matches_bench_ic7300_capture below). The other five are
# computed from the layout above for standard CTCSS chart frequencies.
#
# MOR-2091: this table previously held the output of the buggy encoder
# itself (e.g. 88.5 Hz paired with 00 88 05), so it round-tripped against
# the bug instead of catching it.
_BCD_TABLE: list[tuple[float, bytes]] = [
    (67.0, b"\x00\x06\x70"),
    (88.5, b"\x00\x08\x85"),  # live capture -- see comment above
    (110.9, b"\x00\x11\x09"),
    (136.5, b"\x00\x13\x65"),
    (167.9, b"\x00\x16\x79"),
    (254.1, b"\x00\x25\x41"),
]


def test_decode_matches_bench_ic7300_capture() -> None:
    """Anchor for _BCD_TABLE's 88.5 Hz row: a captured value, not a value
    derived from (and therefore blind to bugs in) the codec under test.

    Bench IC-7300, 2026-09-01, 115200 CI-V (owner-reported), bypassing
    RigPlane. The radio's tone was 88.5 Hz (confirmed in its own menu by
    the owner):
      request fe fe 94 e0 1b 00 fd
      reply   fe fe e0 94 1b 00 00 08 85 fd   (data = 00 08 85)
    """
    assert _decode_tone_freq(bytes([0x00, 0x08, 0x85])) == 88.5


# ===========================================================================
# Repeater Tone (0x16 0x42)
# ===========================================================================


class TestRepeaterTone:
    """Tests for get_repeater_tone / set_repeater_tone."""

    def test_get_main_receiver(self, cmd_map) -> None:
        assert commands.get_repeater_tone(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        ) == _cmd29_preamp_get(_SUB_REPEATER_TONE, RECEIVER_MAIN)

    def test_get_sub_receiver(self, cmd_map) -> None:
        assert commands.get_repeater_tone(
            receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_preamp_get(_SUB_REPEATER_TONE, RECEIVER_SUB)

    def test_get_default_is_main(self, cmd_map) -> None:
        assert commands.get_repeater_tone(
            cmd_map=cmd_map
        ) == commands.get_repeater_tone(receiver=RECEIVER_MAIN, cmd_map=cmd_map)

    def test_get_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        with pytest.raises(TypeError, match="MOR-2006"):
            commands.get_repeater_tone()  # type: ignore[call-arg]

    def test_set_on_main(self, cmd_map) -> None:
        assert commands.set_repeater_tone(True, cmd_map=cmd_map) == _cmd29_preamp_set(
            _SUB_REPEATER_TONE, 0x01, RECEIVER_MAIN
        )

    def test_set_off_main(self, cmd_map) -> None:
        assert commands.set_repeater_tone(False, cmd_map=cmd_map) == _cmd29_preamp_set(
            _SUB_REPEATER_TONE, 0x00, RECEIVER_MAIN
        )

    def test_set_on_sub(self, cmd_map) -> None:
        assert commands.set_repeater_tone(
            True, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_preamp_set(_SUB_REPEATER_TONE, 0x01, RECEIVER_SUB)

    def test_set_off_sub(self, cmd_map) -> None:
        assert commands.set_repeater_tone(
            False, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_preamp_set(_SUB_REPEATER_TONE, 0x00, RECEIVER_SUB)

    def test_set_rejects_explicit_none_the_same_way(self, cmd_map) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely."""
        with pytest.raises(TypeError, match="MOR-2006"):
            commands.set_repeater_tone(True, cmd_map=None)

    def test_parse_on(self) -> None:
        frame = _preamp_response(_SUB_REPEATER_TONE, b"\x01")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_REPEATER_TONE)
            is True
        )

    def test_parse_off(self) -> None:
        frame = _preamp_response(_SUB_REPEATER_TONE, b"\x00")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_REPEATER_TONE)
            is False
        )

    def test_custom_addresses(self, cmd_map) -> None:
        frame = commands.get_repeater_tone(
            to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map
        )
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_uses_cmd29_prefix(self, cmd_map) -> None:
        frame = commands.get_repeater_tone(cmd_map=cmd_map)
        assert frame[4] == _CMD_CMD29


# ===========================================================================
# Repeater TSQL (0x16 0x43)
# ===========================================================================


class TestRepeaterTSQL:
    """Tests for get_repeater_tsql / set_repeater_tsql."""

    def test_get_main_receiver(self, cmd_map) -> None:
        assert commands.get_repeater_tsql(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        ) == _cmd29_preamp_get(_SUB_REPEATER_TSQL, RECEIVER_MAIN)

    def test_get_sub_receiver(self, cmd_map) -> None:
        assert commands.get_repeater_tsql(
            receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_preamp_get(_SUB_REPEATER_TSQL, RECEIVER_SUB)

    def test_get_default_is_main(self, cmd_map) -> None:
        assert commands.get_repeater_tsql(
            cmd_map=cmd_map
        ) == commands.get_repeater_tsql(receiver=RECEIVER_MAIN, cmd_map=cmd_map)

    def test_set_on_main(self, cmd_map) -> None:
        assert commands.set_repeater_tsql(True, cmd_map=cmd_map) == _cmd29_preamp_set(
            _SUB_REPEATER_TSQL, 0x01, RECEIVER_MAIN
        )

    def test_set_off_main(self, cmd_map) -> None:
        assert commands.set_repeater_tsql(False, cmd_map=cmd_map) == _cmd29_preamp_set(
            _SUB_REPEATER_TSQL, 0x00, RECEIVER_MAIN
        )

    def test_set_on_sub(self, cmd_map) -> None:
        assert commands.set_repeater_tsql(
            True, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_preamp_set(_SUB_REPEATER_TSQL, 0x01, RECEIVER_SUB)

    def test_set_off_sub(self, cmd_map) -> None:
        assert commands.set_repeater_tsql(
            False, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_preamp_set(_SUB_REPEATER_TSQL, 0x00, RECEIVER_SUB)

    def test_parse_on(self) -> None:
        frame = _preamp_response(_SUB_REPEATER_TSQL, b"\x01")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_REPEATER_TSQL)
            is True
        )

    def test_parse_off(self) -> None:
        frame = _preamp_response(_SUB_REPEATER_TSQL, b"\x00")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_REPEATER_TSQL)
            is False
        )

    def test_custom_addresses(self, cmd_map) -> None:
        frame = commands.get_repeater_tsql(
            to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map
        )
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_uses_cmd29_prefix(self, cmd_map) -> None:
        frame = commands.get_repeater_tsql(cmd_map=cmd_map)
        assert frame[4] == _CMD_CMD29


# ===========================================================================
# Tone Frequency (0x1B 0x00)
# ===========================================================================


class TestToneFreqBCDEncoding:
    """BCD encoding of CTCSS tone frequencies."""

    @pytest.mark.parametrize("freq, bcd", _BCD_TABLE)
    def test_encode(self, freq: float, bcd: bytes, cmd_map) -> None:
        frame = commands.set_tone_freq(freq, cmd_map=cmd_map)
        assert bcd in frame

    def test_rejects_below_minimum(self, cmd_map) -> None:
        with pytest.raises(ValueError, match="67.0"):
            commands.set_tone_freq(50.0, cmd_map=cmd_map)

    def test_rejects_above_maximum(self, cmd_map) -> None:
        with pytest.raises(ValueError, match="254.1"):
            commands.set_tone_freq(300.0, cmd_map=cmd_map)

    def test_accepts_boundary_low(self, cmd_map) -> None:
        frame = commands.set_tone_freq(67.0, cmd_map=cmd_map)
        # 67.0 Hz -> 000670; see _BCD_TABLE's header comment for the layout
        # and manual sourcing.
        assert b"\x00\x06\x70" in frame

    def test_accepts_boundary_high(self, cmd_map) -> None:
        frame = commands.set_tone_freq(254.1, cmd_map=cmd_map)
        # 254.1 Hz -> 002541; see _BCD_TABLE's header comment.
        assert b"\x00\x25\x41" in frame

    def test_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        with pytest.raises(TypeError, match="MOR-2006"):
            commands.set_tone_freq(88.5)  # type: ignore[call-arg]


class TestGetToneFreq:
    """Frame construction for get_tone_freq (0x1B 0x00)."""

    def test_main_receiver(self, cmd_map) -> None:
        assert commands.get_tone_freq(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        ) == _cmd29_tone_get(_SUB_TONE_FREQ, RECEIVER_MAIN)

    def test_sub_receiver(self, cmd_map) -> None:
        assert commands.get_tone_freq(
            receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_tone_get(_SUB_TONE_FREQ, RECEIVER_SUB)

    def test_default_is_main(self, cmd_map) -> None:
        assert commands.get_tone_freq(cmd_map=cmd_map) == commands.get_tone_freq(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        )

    def test_uses_cmd29_prefix(self, cmd_map) -> None:
        frame = commands.get_tone_freq(cmd_map=cmd_map)
        assert frame[4] == _CMD_CMD29

    def test_contains_tone_command_and_sub(self, cmd_map) -> None:
        frame = commands.get_tone_freq(cmd_map=cmd_map)
        assert bytes([_CMD_TONE, _SUB_TONE_FREQ]) in frame

    def test_custom_addresses(self, cmd_map) -> None:
        frame = commands.get_tone_freq(to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


class TestSetToneFreq:
    """Frame construction for set_tone_freq (0x1B 0x00)."""

    @pytest.mark.parametrize("freq, bcd", _BCD_TABLE)
    def test_set_encodes_bcd(self, freq: float, bcd: bytes, cmd_map) -> None:
        assert commands.set_tone_freq(freq, cmd_map=cmd_map) == _cmd29_tone_set(
            _SUB_TONE_FREQ, bcd, RECEIVER_MAIN
        )

    def test_set_sub_receiver(self, cmd_map) -> None:
        assert commands.set_tone_freq(
            88.5, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_tone_set(
            _SUB_TONE_FREQ, b"\x00\x08\x85", RECEIVER_SUB
        )  # 88.5 Hz, see _BCD_TABLE

    def test_set_custom_addresses(self, cmd_map) -> None:
        frame = commands.set_tone_freq(
            88.5, to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map
        )
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


class TestParseToneFreqResponse:
    """Parsing of tone frequency responses."""

    @pytest.mark.parametrize("freq, bcd", _BCD_TABLE)
    def test_decode_main_receiver(self, freq: float, bcd: bytes) -> None:
        frame = _tone_freq_response(bcd, receiver=RECEIVER_MAIN)
        rx, decoded = parse_tone_freq_response(frame)
        assert rx == RECEIVER_MAIN
        assert decoded == pytest.approx(freq, abs=0.05)

    @pytest.mark.parametrize("freq, bcd", _BCD_TABLE)
    def test_decode_sub_receiver(self, freq: float, bcd: bytes) -> None:
        frame = _tone_freq_response(bcd, receiver=RECEIVER_SUB)
        rx, decoded = parse_tone_freq_response(frame)
        assert rx == RECEIVER_SUB
        assert decoded == pytest.approx(freq, abs=0.05)

    def test_decode_no_receiver(self) -> None:
        # 88.5 Hz, see _BCD_TABLE.
        frame = _tone_freq_response(b"\x00\x08\x85", receiver=None)
        rx, freq = parse_tone_freq_response(frame)
        assert rx is None
        assert freq == pytest.approx(88.5)

    def test_rejects_wrong_command(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=0x14,
            sub=_SUB_TONE_FREQ,
            data=b"\x00\x88\x05",
        )
        with pytest.raises(ValueError):
            parse_tone_freq_response(frame)

    def test_rejects_wrong_sub(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=_CMD_TONE,
            sub=_SUB_TSQL_FREQ,  # wrong sub for tone
            data=b"\x00\x88\x05",
        )
        with pytest.raises(ValueError):
            parse_tone_freq_response(frame)

    def test_rejects_short_data(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=_CMD_TONE,
            sub=_SUB_TONE_FREQ,
            data=b"\x00\x88",  # only 2 bytes
        )
        with pytest.raises(ValueError):
            parse_tone_freq_response(frame)


# ===========================================================================
# TSQL Frequency (0x1B 0x01)
# ===========================================================================


class TestTSQLFreqBCDEncoding:
    """BCD encoding of TSQL frequencies (shares codec with tone freq)."""

    @pytest.mark.parametrize("freq, bcd", _BCD_TABLE)
    def test_encode(self, freq: float, bcd: bytes, cmd_map) -> None:
        frame = commands.set_tsql_freq(freq, cmd_map=cmd_map)
        assert bcd in frame

    def test_rejects_below_minimum(self, cmd_map) -> None:
        with pytest.raises(ValueError, match="67.0"):
            commands.set_tsql_freq(50.0, cmd_map=cmd_map)

    def test_rejects_above_maximum(self, cmd_map) -> None:
        with pytest.raises(ValueError, match="254.1"):
            commands.set_tsql_freq(300.0, cmd_map=cmd_map)


class TestGetTSQLFreq:
    """Frame construction for get_tsql_freq (0x1B 0x01)."""

    def test_main_receiver(self, cmd_map) -> None:
        assert commands.get_tsql_freq(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        ) == _cmd29_tone_get(_SUB_TSQL_FREQ, RECEIVER_MAIN)

    def test_sub_receiver(self, cmd_map) -> None:
        assert commands.get_tsql_freq(
            receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_tone_get(_SUB_TSQL_FREQ, RECEIVER_SUB)

    def test_default_is_main(self, cmd_map) -> None:
        assert commands.get_tsql_freq(cmd_map=cmd_map) == commands.get_tsql_freq(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        )

    def test_uses_cmd29_prefix(self, cmd_map) -> None:
        frame = commands.get_tsql_freq(cmd_map=cmd_map)
        assert frame[4] == _CMD_CMD29

    def test_contains_tsql_sub(self, cmd_map) -> None:
        frame = commands.get_tsql_freq(cmd_map=cmd_map)
        assert bytes([_CMD_TONE, _SUB_TSQL_FREQ]) in frame


class TestSetTSQLFreq:
    """Frame construction for set_tsql_freq (0x1B 0x01)."""

    @pytest.mark.parametrize("freq, bcd", _BCD_TABLE)
    def test_set_encodes_bcd(self, freq: float, bcd: bytes, cmd_map) -> None:
        assert commands.set_tsql_freq(freq, cmd_map=cmd_map) == _cmd29_tone_set(
            _SUB_TSQL_FREQ, bcd, RECEIVER_MAIN
        )

    def test_set_sub_receiver(self, cmd_map) -> None:
        assert commands.set_tsql_freq(
            88.5, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_tone_set(
            _SUB_TSQL_FREQ, b"\x00\x08\x85", RECEIVER_SUB
        )  # 88.5 Hz, see _BCD_TABLE


class TestParseTSQLFreqResponse:
    """Parsing of TSQL frequency responses."""

    @pytest.mark.parametrize("freq, bcd", _BCD_TABLE)
    def test_decode_main_receiver(self, freq: float, bcd: bytes) -> None:
        frame = _tsql_freq_response(bcd, receiver=RECEIVER_MAIN)
        rx, decoded = parse_tsql_freq_response(frame)
        assert rx == RECEIVER_MAIN
        assert decoded == pytest.approx(freq, abs=0.05)

    def test_decode_no_receiver(self) -> None:
        # 88.5 Hz, see _BCD_TABLE.
        frame = _tsql_freq_response(b"\x00\x08\x85", receiver=None)
        rx, freq = parse_tsql_freq_response(frame)
        assert rx is None
        assert freq == pytest.approx(88.5)

    def test_rejects_wrong_sub(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=_CMD_TONE,
            sub=_SUB_TONE_FREQ,  # wrong sub for TSQL
            data=b"\x00\x88\x05",
        )
        with pytest.raises(ValueError):
            parse_tsql_freq_response(frame)

    def test_rejects_short_data(self) -> None:
        frame = CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=IC_7610_ADDR,
            command=_CMD_TONE,
            sub=_SUB_TSQL_FREQ,
            data=b"\x00\x88",
        )
        with pytest.raises(ValueError):
            parse_tsql_freq_response(frame)


# ===========================================================================
# Command distinctness
# ===========================================================================


class TestCommandDistinctness:
    """Different commands must produce distinct CI-V frames."""

    def test_repeater_tone_vs_tsql_get(self, cmd_map) -> None:
        assert commands.get_repeater_tone(
            cmd_map=cmd_map
        ) != commands.get_repeater_tsql(cmd_map=cmd_map)

    def test_repeater_tone_vs_tsql_set_on(self, cmd_map) -> None:
        assert commands.set_repeater_tone(
            True, cmd_map=cmd_map
        ) != commands.set_repeater_tsql(True, cmd_map=cmd_map)

    def test_tone_freq_vs_tsql_freq_get(self, cmd_map) -> None:
        assert commands.get_tone_freq(cmd_map=cmd_map) != commands.get_tsql_freq(
            cmd_map=cmd_map
        )

    def test_tone_freq_vs_tsql_freq_set(self, cmd_map) -> None:
        assert commands.set_tone_freq(88.5, cmd_map=cmd_map) != commands.set_tsql_freq(
            88.5, cmd_map=cmd_map
        )

    def test_tone_main_vs_sub_get(self, cmd_map) -> None:
        assert commands.get_tone_freq(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        ) != commands.get_tone_freq(receiver=RECEIVER_SUB, cmd_map=cmd_map)

    def test_tsql_main_vs_sub_get(self, cmd_map) -> None:
        assert commands.get_tsql_freq(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        ) != commands.get_tsql_freq(receiver=RECEIVER_SUB, cmd_map=cmd_map)

    def test_repeater_tone_main_vs_sub_get(self, cmd_map) -> None:
        assert commands.get_repeater_tone(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        ) != commands.get_repeater_tone(receiver=RECEIVER_SUB, cmd_map=cmd_map)

    def test_tone_on_vs_off(self, cmd_map) -> None:
        assert commands.set_repeater_tone(
            True, cmd_map=cmd_map
        ) != commands.set_repeater_tone(False, cmd_map=cmd_map)

    def test_tsql_on_vs_off(self, cmd_map) -> None:
        assert commands.set_repeater_tsql(
            True, cmd_map=cmd_map
        ) != commands.set_repeater_tsql(False, cmd_map=cmd_map)

    def test_different_tone_freqs(self, cmd_map) -> None:
        assert commands.set_tone_freq(88.5, cmd_map=cmd_map) != commands.set_tone_freq(
            110.9, cmd_map=cmd_map
        )

    def test_repeater_tone_distinct_from_freq_cmd(self, cmd_map) -> None:
        """0x16 and 0x1B commands are fundamentally different."""
        assert commands.get_repeater_tone(cmd_map=cmd_map) != commands.get_tone_freq(
            cmd_map=cmd_map
        )
