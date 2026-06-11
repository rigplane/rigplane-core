"""Tests for Yaesu CAT command formatter and parser.

Covers:
- format_command(): valid placeholders, unknown placeholder rejection
- CatCommandParser: roundtrip (format → parse), malformed response rejection
- Representative commands: FA (freq), MD0 (mode), SM0 (s-meter), CF001 (RIT)
- Compile-once behaviour: same parser instance reused across multiple parses
"""

from __future__ import annotations

import pytest

from rigplane.backends.yaesu_cat.parser import (
    CatCommandParser,
    CatFormatError,
    CatParseError,
    format_command,
)


# ---------------------------------------------------------------------------
# format_command
# ---------------------------------------------------------------------------


class TestFormatCommand:
    def test_freq_fa(self):
        assert format_command("FA{freq:09d};", freq=14074000) == "FA014074000;"

    def test_freq_fb(self):
        assert format_command("FB{freq:09d};", freq=430000000) == "FB430000000;"

    def test_freq_zero_padded(self):
        assert format_command("FA{freq:09d};", freq=1825000) == "FA001825000;"

    def test_mode(self):
        # MD0 with mode code '2' (USB)
        assert format_command("MD0{mode};", mode="2") == "MD02;"

    def test_raw_smeter(self):
        assert format_command("SM0{raw:03d};", raw=130) == "SM0130;"

    def test_level(self):
        assert format_command("AG0{level:03d};", level=200) == "AG0200;"

    def test_state_on(self):
        assert format_command("VX{state};", state="1") == "VX1;"

    def test_state_off(self):
        assert format_command("VX{state};", state="0") == "VX0;"

    def test_sign_positive(self):
        assert (
            format_command("IS00{sign}{offset:04d};", sign="+", offset=600)
            == "IS00+0600;"
        )

    def test_sign_negative(self):
        assert (
            format_command("IS00{sign}{offset:04d};", sign="-", offset=1200)
            == "IS00-1200;"
        )

    def test_offset(self):
        assert (
            format_command("CF001{sign}{offset:04d};", sign="+", offset=500)
            == "CF001+0500;"
        )

    def test_unknown_placeholder_raises(self):
        with pytest.raises(CatFormatError) as exc_info:
            format_command("XX{unknown};", unknown="val")
        assert "unknown" in str(exc_info.value)

    def test_multiple_unknown_placeholders_raises(self):
        with pytest.raises(CatFormatError) as exc_info:
            format_command("{foo}{bar};", foo=1, bar=2)
        assert "foo" in str(exc_info.value) or "bar" in str(exc_info.value)

    def test_missing_value_raises(self):
        with pytest.raises(CatFormatError):
            format_command("FA{freq:09d};")  # no freq kwarg

    def test_no_placeholders(self):
        # Commands like "AB;" have no parameters
        assert format_command("AB;") == "AB;"

    def test_wrong_type_for_format_spec_raises(self):
        # Passing a string for an integer format spec causes ValueError/TypeError
        # which should be wrapped as CatFormatError (line 211 branch)
        with pytest.raises(CatFormatError):
            format_command("FA{freq:09d};", freq="not_an_int")

    def test_none_value_for_format_spec_raises(self):
        # Passing None for a format spec that expects a number triggers TypeError
        with pytest.raises(CatFormatError):
            format_command("FA{freq:09d};", freq=None)


# ---------------------------------------------------------------------------
# CatCommandParser — basic parsing
# ---------------------------------------------------------------------------


class TestCatCommandParser:
    def test_parse_freq_fa(self):
        parser = CatCommandParser("FA{freq:09d};")
        result = parser.parse("FA014074000;")
        assert result == {"freq": 14074000}

    def test_parse_freq_type_is_int(self):
        parser = CatCommandParser("FA{freq:09d};")
        result = parser.parse("FA001825000;")
        assert isinstance(result["freq"], int)
        assert result["freq"] == 1825000

    def test_parse_mode(self):
        parser = CatCommandParser("MD0{mode};")
        result = parser.parse("MD02;")
        assert result == {"mode": "2"}

    def test_parse_mode_type_is_str(self):
        parser = CatCommandParser("MD0{mode};")
        result = parser.parse("MD0A;")
        assert isinstance(result["mode"], str)

    def test_parse_smeter(self):
        parser = CatCommandParser("SM{state}{raw:03d};")
        result = parser.parse("SM0130;")
        assert result == {"state": "0", "raw": 130}

    def test_parse_smeter_raw_type_is_int(self):
        parser = CatCommandParser("SM{state}{raw:03d};")
        result = parser.parse("SM0000;")
        assert isinstance(result["raw"], int)

    def test_parse_break_in_delay_two_digit(self):
        # MOR-561: the FTX-1 answers ``SD;`` with a 2-digit ``SD09;`` form
        # while the template is ``SD{delay:04d};`` (4-digit). The delay regex
        # must accept 2–4 digits so the startup read parses without warning.
        parser = CatCommandParser("SD{delay:04d};")
        assert parser.parse("SD09;") == {"delay": 9}

    def test_parse_break_in_delay_four_digit_still_parses(self):
        # MOR-561 regression: the widened delay regex must still accept the
        # canonical 4-digit ``SD0300;`` form (no behaviour change for radios
        # that answer in full width).
        parser = CatCommandParser("SD{delay:04d};")
        assert parser.parse("SD0300;") == {"delay": 300}

    def test_parse_rit_clarifier(self):
        # CF001+0500; — func=1, sign=+, offset=500
        parser = CatCommandParser("CF001{sign}{offset:04d};")
        result = parser.parse("CF001+0500;")
        assert result == {"sign": "+", "offset": 500}

    def test_parse_rit_negative(self):
        parser = CatCommandParser("CF001{sign}{offset:04d};")
        result = parser.parse("CF001-1200;")
        assert result == {"sign": "-", "offset": 1200}

    def test_parse_level(self):
        parser = CatCommandParser("AG0{level:03d};")
        result = parser.parse("AG0200;")
        assert result == {"level": 200}

    def test_parse_state(self):
        parser = CatCommandParser("VX{state};")
        result = parser.parse("VX1;")
        assert result == {"state": "1"}

    def test_parse_sql_type_single_digit(self):
        # MOR-473: the live FTX-1 answers ``CT0;`` with a SINGLE-digit P2
        # ("CT00;", not "CT000;"), so the read template is ``CT0{type};``.
        parser = CatCommandParser("CT0{type};")
        assert parser.parse("CT00;") == {"type": "0"}
        assert parser.parse("CT01;") == {"type": "1"}


# ---------------------------------------------------------------------------
# CatCommandParser — mismatch / error cases
# ---------------------------------------------------------------------------


class TestCatCommandParserErrors:
    def test_wrong_prefix_raises(self):
        parser = CatCommandParser("FA{freq:09d};")
        with pytest.raises(CatParseError) as exc_info:
            parser.parse("FB014074000;")
        assert "FA" in str(exc_info.value) or "pattern" in str(exc_info.value)

    def test_wrong_digit_count_raises(self):
        parser = CatCommandParser("FA{freq:09d};")
        with pytest.raises(CatParseError):
            parser.parse("FA14074000;")  # only 8 digits

    def test_missing_terminator_raises(self):
        parser = CatCommandParser("FA{freq:09d};")
        with pytest.raises(CatParseError):
            parser.parse("FA014074000")  # no semicolon

    def test_empty_response_raises(self):
        parser = CatCommandParser("FA{freq:09d};")
        with pytest.raises(CatParseError):
            parser.parse("")

    def test_extra_chars_raises(self):
        parser = CatCommandParser("FA{freq:09d};")
        with pytest.raises(CatParseError):
            parser.parse("FA014074000;X")  # trailing garbage


# ---------------------------------------------------------------------------
# Roundtrip: format → parse
# ---------------------------------------------------------------------------


class TestRoundtrip:
    def test_freq_roundtrip(self):
        template = "FA{freq:09d};"
        cmd = format_command(template, freq=14074000)
        parser = CatCommandParser(template)
        result = parser.parse(cmd)
        assert result["freq"] == 14074000

    def test_smeter_roundtrip(self):
        read_template = "SM{state};"
        parse_template = "SM{state}{raw:03d};"
        cmd = format_command(read_template, state="0")
        assert cmd == "SM0;"
        # Simulate radio response
        response = format_command("SM{state}{raw:03d};", state="0", raw=103)
        parser = CatCommandParser(parse_template)
        result = parser.parse(response)
        assert result == {"state": "0", "raw": 103}

    def test_level_roundtrip(self):
        template = "AG0{level:03d};"
        cmd = format_command(template, level=128)
        parser = CatCommandParser(template)
        result = parser.parse(cmd)
        assert result["level"] == 128

    def test_rit_roundtrip(self):
        write_template = "CF001{sign}{offset:04d};"
        parse_template = "CF001{sign}{offset:04d};"
        cmd = format_command(write_template, sign="+", offset=600)
        parser = CatCommandParser(parse_template)
        result = parser.parse(cmd)
        assert result == {"sign": "+", "offset": 600}


# ---------------------------------------------------------------------------
# Compile-once: same instance reused
# ---------------------------------------------------------------------------


class TestCompileOnce:
    def test_same_parser_instance_reused(self):
        parser = CatCommandParser("FA{freq:09d};")
        freqs = [14074000, 7100000, 3600000, 144000000, 1825000]
        for freq in freqs:
            cmd = format_command("FA{freq:09d};", freq=freq)
            result = parser.parse(cmd)
            assert result["freq"] == freq

    def test_same_parser_rejects_invalid_between_valid(self):
        parser = CatCommandParser("FA{freq:09d};")
        assert parser.parse("FA014074000;") == {"freq": 14074000}
        with pytest.raises(CatParseError):
            parser.parse("GARBAGE")
        # Parser still works after error
        assert parser.parse("FA007100000;") == {"freq": 7100000}

    def test_smeter_parser_reused(self):
        parser = CatCommandParser("SM{state}{raw:03d};")
        cases = [("0", 0), ("0", 130), ("1", 255), ("0", 103)]
        for state, raw in cases:
            cmd = format_command("SM{state}{raw:03d};", state=state, raw=raw)
            result = parser.parse(cmd)
            assert result["state"] == state
            assert result["raw"] == raw
