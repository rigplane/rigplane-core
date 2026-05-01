"""Tests for src/icom_lan/rigctld/protocol.py.

Covers:
- parse_line: short commands, long commands, arg validation, unknown commands
- parse_line: \\r\\n tolerance, empty lines, extra whitespace
- format_response: normal mode (GET / SET / error)
- format_response: extended mode
- format_error
"""

from __future__ import annotations

import pytest

from icom_lan.rigctld.contract import (
    ClientSession,
    HamlibError,
    RigctldCommand,
    RigctldResponse,
)
from icom_lan.rigctld.protocol import format_error, format_response, parse_line


# ── helpers ──────────────────────────────────────────────────────────────────


def _session(*, extended: bool = False) -> ClientSession:
    return ClientSession(extended_mode=extended)


# ── parse_line: short commands ────────────────────────────────────────────────


class TestParseLineShort:
    def test_get_freq(self) -> None:
        cmd = parse_line(b"f")
        assert cmd.short_cmd == "f"
        assert cmd.long_cmd == "get_freq"
        assert cmd.args == ()
        assert cmd.is_set is False

    def test_get_mode(self) -> None:
        cmd = parse_line(b"m")
        assert cmd.short_cmd == "m"
        assert cmd.long_cmd == "get_mode"
        assert cmd.is_set is False

    def test_get_ptt(self) -> None:
        cmd = parse_line(b"t")
        assert cmd.short_cmd == "t"
        assert cmd.long_cmd == "get_ptt"

    def test_get_vfo(self) -> None:
        cmd = parse_line(b"v")
        assert cmd.short_cmd == "v"
        assert cmd.long_cmd == "get_vfo"

    def test_get_rit(self) -> None:
        cmd = parse_line(b"j")
        assert cmd.short_cmd == "j"
        assert cmd.long_cmd == "get_rit"

    def test_get_split_vfo(self) -> None:
        cmd = parse_line(b"s")
        assert cmd.short_cmd == "s"
        assert cmd.long_cmd == "get_split_vfo"

    def test_get_level_with_arg(self) -> None:
        cmd = parse_line(b"l STRENGTH")
        assert cmd.short_cmd == "l"
        assert cmd.long_cmd == "get_level"
        assert cmd.args == ("STRENGTH",)
        assert cmd.is_set is False

    def test_set_freq(self) -> None:
        cmd = parse_line(b"F 14074000")
        assert cmd.short_cmd == "F"
        assert cmd.long_cmd == "set_freq"
        assert cmd.args == ("14074000",)
        assert cmd.is_set is True

    def test_set_mode_with_passband(self) -> None:
        cmd = parse_line(b"M USB 3000")
        assert cmd.short_cmd == "M"
        assert cmd.long_cmd == "set_mode"
        assert cmd.args == ("USB", "3000")
        assert cmd.is_set is True

    def test_set_mode_without_passband(self) -> None:
        cmd = parse_line(b"M LSB")
        assert cmd.short_cmd == "M"
        assert cmd.args == ("LSB",)

    def test_set_ptt_on(self) -> None:
        cmd = parse_line(b"T 1")
        assert cmd.short_cmd == "T"
        assert cmd.args == ("1",)
        assert cmd.is_set is True

    def test_set_ptt_off(self) -> None:
        cmd = parse_line(b"T 0")
        assert cmd.args == ("0",)

    def test_set_vfo(self) -> None:
        cmd = parse_line(b"V VFOA")
        assert cmd.short_cmd == "V"
        assert cmd.long_cmd == "set_vfo"
        assert cmd.args == ("VFOA",)

    def test_set_split_vfo(self) -> None:
        cmd = parse_line(b"S 1 VFOB")
        assert cmd.short_cmd == "S"
        assert cmd.long_cmd == "set_split_vfo"
        assert cmd.args == ("1", "VFOB")

    def test_quit(self) -> None:
        cmd = parse_line(b"q")
        assert cmd.short_cmd == "q"
        assert cmd.long_cmd == "quit"
        assert cmd.is_set is False

    def test_dump_caps_numeric(self) -> None:
        cmd = parse_line(b"1")
        assert cmd.short_cmd == "1"
        assert cmd.long_cmd == "dump_caps"
        assert cmd.is_set is False


# ── parse_line: long commands ─────────────────────────────────────────────────


class TestParseLineLong:
    def test_get_freq(self) -> None:
        cmd = parse_line(b"\\get_freq")
        assert cmd.short_cmd == "f"
        assert cmd.long_cmd == "get_freq"
        assert cmd.is_set is False

    def test_set_freq(self) -> None:
        cmd = parse_line(b"\\set_freq 14074000")
        assert cmd.short_cmd == "F"
        assert cmd.long_cmd == "set_freq"
        assert cmd.args == ("14074000",)
        assert cmd.is_set is True

    def test_get_mode(self) -> None:
        cmd = parse_line(b"\\get_mode")
        assert cmd.long_cmd == "get_mode"

    def test_set_mode(self) -> None:
        cmd = parse_line(b"\\set_mode USB 3000")
        assert cmd.long_cmd == "set_mode"
        assert cmd.args == ("USB", "3000")

    def test_get_ptt(self) -> None:
        cmd = parse_line(b"\\get_ptt")
        assert cmd.long_cmd == "get_ptt"

    def test_set_ptt(self) -> None:
        cmd = parse_line(b"\\set_ptt 1")
        assert cmd.long_cmd == "set_ptt"
        assert cmd.args == ("1",)

    def test_dump_state(self) -> None:
        cmd = parse_line(b"\\dump_state")
        assert cmd.long_cmd == "dump_state"
        assert cmd.is_set is False

    def test_get_info(self) -> None:
        cmd = parse_line(b"\\get_info")
        assert cmd.long_cmd == "get_info"

    def test_chk_vfo(self) -> None:
        cmd = parse_line(b"\\chk_vfo")
        assert cmd.long_cmd == "chk_vfo"

    def test_get_powerstat(self) -> None:
        cmd = parse_line(b"\\get_powerstat")
        assert cmd.long_cmd == "get_powerstat"

    def test_get_vfo_long(self) -> None:
        cmd = parse_line(b"\\get_vfo")
        assert cmd.long_cmd == "get_vfo"

    def test_set_vfo_long(self) -> None:
        cmd = parse_line(b"\\set_vfo VFOB")
        assert cmd.long_cmd == "set_vfo"
        assert cmd.args == ("VFOB",)

    def test_quit_long(self) -> None:
        cmd = parse_line(b"\\quit")
        assert cmd.long_cmd == "quit"


# ── parse_line: tolerance and edge cases ──────────────────────────────────────


class TestParseLineEdgeCases:
    def test_strips_trailing_cr(self) -> None:
        cmd = parse_line(b"f\r")
        assert cmd.short_cmd == "f"

    def test_strips_cr_with_args(self) -> None:
        cmd = parse_line(b"F 14074000\r")
        assert cmd.args == ("14074000",)

    def test_long_cmd_strips_cr(self) -> None:
        cmd = parse_line(b"\\get_freq\r")
        assert cmd.long_cmd == "get_freq"

    def test_leading_whitespace(self) -> None:
        cmd = parse_line(b"  f")
        assert cmd.short_cmd == "f"

    def test_trailing_whitespace(self) -> None:
        cmd = parse_line(b"f  ")
        assert cmd.short_cmd == "f"

    def test_extra_whitespace_between_args(self) -> None:
        cmd = parse_line(b"M  USB  3000")
        assert cmd.args == ("USB", "3000")

    def test_large_frequency(self) -> None:
        cmd = parse_line(b"F 432100000")
        assert cmd.args == ("432100000",)

    def test_set_freq_zero(self) -> None:
        # Arg validation only checks count, not value semantics.
        cmd = parse_line(b"F 0")
        assert cmd.args == ("0",)


# ── parse_line: errors ────────────────────────────────────────────────────────


class TestParseLineErrors:
    def test_unknown_short(self) -> None:
        with pytest.raises(ValueError, match="[Uu]nknown"):
            parse_line(b"z")

    def test_unknown_long(self) -> None:
        with pytest.raises(ValueError):
            parse_line(b"\\get_bogus")

    def test_unknown_bare_word(self) -> None:
        with pytest.raises(ValueError):
            parse_line(b"noop")

    def test_empty_bytes(self) -> None:
        with pytest.raises(ValueError):
            parse_line(b"")

    def test_cr_only(self) -> None:
        with pytest.raises(ValueError):
            parse_line(b"\r")

    def test_whitespace_only(self) -> None:
        with pytest.raises(ValueError):
            parse_line(b"   ")

    def test_set_freq_no_arg(self) -> None:
        # F requires exactly 1 arg.
        with pytest.raises(ValueError, match="at least"):
            parse_line(b"F")

    def test_set_freq_too_many_args(self) -> None:
        with pytest.raises(ValueError, match="at most"):
            parse_line(b"F 14074000 extra")

    def test_set_ptt_too_many_args(self) -> None:
        with pytest.raises(ValueError):
            parse_line(b"T 1 2")

    def test_get_level_no_arg(self) -> None:
        # l requires exactly 1 arg.
        with pytest.raises(ValueError, match="at least"):
            parse_line(b"l")

    def test_set_split_too_few_args(self) -> None:
        # S requires 2 args.
        with pytest.raises(ValueError, match="at least"):
            parse_line(b"S 1")

    def test_get_freq_with_arg(self) -> None:
        # f accepts 0 args.
        with pytest.raises(ValueError, match="at most"):
            parse_line(b"f extra")

    def test_dump_state_with_arg(self) -> None:
        with pytest.raises(ValueError, match="at most"):
            parse_line(b"\\dump_state extra")


# ── parse_line: VFO-prefix support (Variant A — #1342, #1343) ────────────────


class TestParseLineVfoPrefix:
    """Parser accepts leading VFO arg under chk_vfo=1 (#1319, A2/#1343).

    The 13 wire forms below correspond 1:1 with the
    wsjtx/fldigi/JS8Call init traces under chk_vfo=1, per the
    `rigctl(1) <https://hamlib.sourceforge.net/manuals/4.5.5/rigctl.1.html>`_
    "Reading"/"Writing" command groups. After #1343 the parser strips
    the leading ``VFOA``/``VFOB``/``currVFO`` token and stashes it on
    ``cmd.vfo_arg``. Handlers in #1343 still ignore ``vfo_arg`` and
    route to the active VFO; per-VFO routing arrives in #1344 (A3).
    """

    @pytest.mark.parametrize(
        "wire,expected_short,expected_long,expected_vfo,expected_args",
        [
            # GET commands — Hamlib prefixes VFO under chk_vfo=1.
            (b"f VFOA", "f", "get_freq", "VFOA", ()),
            (b"f VFOB", "f", "get_freq", "VFOB", ()),
            (b"f currVFO", "f", "get_freq", "currVFO", ()),
            (b"m VFOA", "m", "get_mode", "VFOA", ()),
            (b"m VFOB", "m", "get_mode", "VFOB", ()),
            (b"t VFOA", "t", "get_ptt", "VFOA", ()),
            (b"j VFOA", "j", "get_rit", "VFOA", ()),
            (b"s VFOA", "s", "get_split_vfo", "VFOA", ()),
            (b"l VFOA STRENGTH", "l", "get_level", "VFOA", ("STRENGTH",)),
            (b"u VFOA NB", "u", "get_func", "VFOA", ("NB",)),
            # SET commands — Hamlib prefixes VFO under chk_vfo=1.
            (b"F VFOA 14250000", "F", "set_freq", "VFOA", ("14250000",)),
            (b"M VFOA USB 2400", "M", "set_mode", "VFOA", ("USB", "2400")),
            (b"T VFOA 1", "T", "set_ptt", "VFOA", ("1",)),
            (
                b"L VFOA RFPOWER 0.5",
                "L",
                "set_level",
                "VFOA",
                ("RFPOWER", "0.5"),
            ),
            (b"U VFOA NB 1", "U", "set_func", "VFOA", ("NB", "1")),
            (b"S VFOA 1 VFOB", "S", "set_split_vfo", "VFOA", ("1", "VFOB")),
        ],
    )
    def test_parses_vfo_prefix(
        self,
        wire: bytes,
        expected_short: str,
        expected_long: str,
        expected_vfo: str,
        expected_args: tuple[str, ...],
    ) -> None:
        """Parser strips leading VFO token onto ``cmd.vfo_arg`` and
        validates min/max against the *remaining* args.
        """
        cmd = parse_line(wire)
        assert cmd.short_cmd == expected_short
        assert cmd.long_cmd == expected_long
        assert cmd.vfo_arg == expected_vfo
        assert cmd.args == expected_args


class TestParseLineBareFormStillWorks:
    """Bare-form (chk_vfo=0) commands must keep parsing on `main`.

    These are the regression-guard for A2-A5: the parser changes to
    accept VFO prefix MUST NOT break the bare form used by single-RX
    profiles. NOT xfailed — passes on `main` already.
    """

    def test_bare_get_freq_still_parses(self) -> None:
        cmd = parse_line(b"f")
        assert cmd.short_cmd == "f"
        assert cmd.args == ()

    def test_bare_get_mode_still_parses(self) -> None:
        cmd = parse_line(b"m")
        assert cmd.short_cmd == "m"
        assert cmd.args == ()

    def test_bare_get_ptt_still_parses(self) -> None:
        cmd = parse_line(b"t")
        assert cmd.short_cmd == "t"
        assert cmd.args == ()

    def test_bare_set_freq_still_parses(self) -> None:
        cmd = parse_line(b"F 14250000")
        assert cmd.short_cmd == "F"
        assert cmd.args == ("14250000",)

    def test_bare_set_split_vfo_still_parses(self) -> None:
        cmd = parse_line(b"S 1 VFOB")
        assert cmd.short_cmd == "S"
        assert cmd.args == ("1", "VFOB")


# ── format_error ──────────────────────────────────────────────────────────────


class TestFormatError:
    def test_ok(self) -> None:
        assert format_error(0) == b"RPRT 0\n"

    def test_einval(self) -> None:
        assert format_error(-1) == b"RPRT -1\n"

    def test_etimeout(self) -> None:
        assert format_error(-5) == b"RPRT -5\n"

    def test_enimpl_enum(self) -> None:
        assert format_error(HamlibError.ENIMPL) == b"RPRT -4\n"

    def test_eaccess_enum(self) -> None:
        assert format_error(HamlibError.EACCESS) == b"RPRT -22\n"

    def test_newline_terminated(self) -> None:
        assert format_error(0).endswith(b"\n")
        assert format_error(-7).endswith(b"\n")


# ── format_response: normal mode ─────────────────────────────────────────────


class TestFormatResponseNormal:
    def test_get_single_value(self) -> None:
        cmd = RigctldCommand("f", "get_freq")
        resp = RigctldResponse(values=["14074000"])
        assert format_response(cmd, resp, _session()) == b"14074000\n"

    def test_get_multi_value(self) -> None:
        cmd = RigctldCommand("m", "get_mode")
        resp = RigctldResponse(values=["USB", "3000"])
        assert format_response(cmd, resp, _session()) == b"USB\n3000\n"

    def test_get_empty_values(self) -> None:
        cmd = RigctldCommand("f", "get_freq")
        resp = RigctldResponse(values=[])
        assert format_response(cmd, resp, _session()) == b""

    def test_set_success(self) -> None:
        cmd = RigctldCommand("F", "set_freq", args=("14074000",), is_set=True)
        resp = RigctldResponse()
        assert format_response(cmd, resp, _session()) == b"RPRT 0\n"

    def test_set_ptt_success(self) -> None:
        cmd = RigctldCommand("T", "set_ptt", args=("1",), is_set=True)
        resp = RigctldResponse()
        assert format_response(cmd, resp, _session()) == b"RPRT 0\n"

    def test_get_error(self) -> None:
        cmd = RigctldCommand("f", "get_freq")
        resp = RigctldResponse(values=["14074000"], error=HamlibError.ETIMEOUT)
        assert format_response(cmd, resp, _session()) == b"RPRT -5\n"

    def test_set_error(self) -> None:
        cmd = RigctldCommand("F", "set_freq", args=("0",), is_set=True)
        resp = RigctldResponse(error=HamlibError.EINVAL)
        assert format_response(cmd, resp, _session()) == b"RPRT -1\n"

    def test_set_read_only_error(self) -> None:
        cmd = RigctldCommand("F", "set_freq", args=("14074000",), is_set=True)
        resp = RigctldResponse(error=HamlibError.EACCESS)
        assert format_response(cmd, resp, _session()) == b"RPRT -22\n"

    def test_response_newline_terminated(self) -> None:
        cmd = RigctldCommand("f", "get_freq")
        resp = RigctldResponse(values=["14074000"])
        assert format_response(cmd, resp, _session()).endswith(b"\n")


# ── format_response: extended mode ───────────────────────────────────────────


class TestFormatResponseExtended:
    def test_get_freq(self) -> None:
        cmd = RigctldCommand("f", "get_freq")
        resp = RigctldResponse(values=["14074000"], cmd_echo="get_freq")
        result = format_response(cmd, resp, _session(extended=True))
        lines = result.decode().splitlines()
        assert lines[0] == "get_freq:"
        assert "14074000" in lines
        assert lines[-1] == "RPRT 0"

    def test_get_mode(self) -> None:
        cmd = RigctldCommand("m", "get_mode")
        resp = RigctldResponse(values=["USB", "3000"], cmd_echo="get_mode")
        result = format_response(cmd, resp, _session(extended=True))
        text = result.decode()
        assert text.startswith("get_mode:\n")
        assert "USB\n" in text
        assert "3000\n" in text
        assert text.endswith("RPRT 0\n")

    def test_set_freq(self) -> None:
        cmd = RigctldCommand("F", "set_freq", args=("14074000",), is_set=True)
        resp = RigctldResponse(cmd_echo="set_freq")
        result = format_response(cmd, resp, _session(extended=True))
        text = result.decode()
        assert text.startswith("set_freq:\n")
        assert "RPRT 0" in text

    def test_error_in_extended(self) -> None:
        cmd = RigctldCommand("f", "get_freq")
        resp = RigctldResponse(error=HamlibError.ETIMEOUT, cmd_echo="get_freq")
        result = format_response(cmd, resp, _session(extended=True))
        assert b"RPRT -5" in result

    def test_fallback_to_long_cmd_echo(self) -> None:
        """When cmd_echo is empty, long_cmd is used as the echo."""
        cmd = RigctldCommand("f", "get_freq")
        resp = RigctldResponse(values=["14074000"])  # cmd_echo=""
        result = format_response(cmd, resp, _session(extended=True))
        assert result.startswith(b"get_freq:")

    def test_always_has_rprt_footer(self) -> None:
        cmd = RigctldCommand("f", "get_freq")
        resp = RigctldResponse(values=["14074000"], cmd_echo="get_freq")
        result = format_response(cmd, resp, _session(extended=True))
        assert b"RPRT" in result

    def test_newline_terminated(self) -> None:
        cmd = RigctldCommand("f", "get_freq")
        resp = RigctldResponse(values=["14074000"], cmd_echo="get_freq")
        result = format_response(cmd, resp, _session(extended=True))
        assert result.endswith(b"\n")

    def test_dump_state_echo(self) -> None:
        cmd = RigctldCommand("\\dump_state", "dump_state")
        resp = RigctldResponse(values=["rig info line"], cmd_echo="dump_state")
        result = format_response(cmd, resp, _session(extended=True))
        assert result.startswith(b"dump_state:")
