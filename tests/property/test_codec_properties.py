"""Property tests for the BCD codecs and the Yaesu CAT template engine."""

from __future__ import annotations

import re
import string
import tomllib
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rigplane.backends.yaesu_cat.parser import CatCommandParser, format_command
from rigplane.commands._codec import _bcd_decode_value, bcd_encode_value
from rigplane.core.types import bcd_decode, bcd_encode

pytestmark = pytest.mark.property

_MAX_BCD_FREQ = 10**10 - 1  # ten decimal digits in five BCD bytes

_valid_bcd_byte = st.builds(
    lambda high, low: (high << 4) | low,
    st.integers(0, 9),
    st.integers(0, 9),
)
_invalid_bcd_byte = st.integers(0, 255).filter(
    lambda byte: (byte >> 4) > 9 or (byte & 0x0F) > 9
)


@given(st.integers(0, _MAX_BCD_FREQ))
def test_frequency_bcd_round_trip(freq_hz: int) -> None:
    encoded = bcd_encode(freq_hz)
    assert len(encoded) == 5
    assert bcd_decode(encoded) == freq_hz


@given(st.lists(_valid_bcd_byte, min_size=5, max_size=5).map(bytes))
def test_every_valid_five_byte_bcd_decodes_and_re_encodes(data: bytes) -> None:
    assert bcd_encode(bcd_decode(data)) == data


@given(
    st.lists(_valid_bcd_byte, min_size=5, max_size=5),
    st.integers(0, 4),
    _invalid_bcd_byte,
)
def test_frequency_bcd_rejects_invalid_nibbles(
    valid: list[int], index: int, bad: int
) -> None:
    valid[index] = bad
    with pytest.raises(ValueError, match="Invalid BCD digit"):
        bcd_decode(bytes(valid))


@given(
    st.one_of(
        st.integers(max_value=-1),
        st.integers(min_value=_MAX_BCD_FREQ + 1),
    )
)
def test_frequency_bcd_rejects_out_of_range(freq_hz: int) -> None:
    with pytest.raises(ValueError):
        bcd_encode(freq_hz)


@given(
    st.integers(1, 5).flatmap(
        lambda n: st.tuples(st.just(n), st.integers(0, 100**n - 1))
    )
)
def test_packed_bcd_value_round_trip(width_and_value: tuple[int, int]) -> None:
    byte_count, value = width_and_value
    encoded = bcd_encode_value(value, byte_count=byte_count)
    assert len(encoded) == byte_count
    assert _bcd_decode_value(encoded) == value


@given(st.lists(_valid_bcd_byte, max_size=4), st.integers(0, 4), _invalid_bcd_byte)
def test_packed_bcd_rejects_invalid_nibbles(
    valid: list[int], index: int, bad: int
) -> None:
    valid.insert(min(index, len(valid)), bad)
    with pytest.raises(ValueError, match="Invalid BCD digit"):
        _bcd_decode_value(bytes(valid))


# --- Yaesu CAT: every ``parse`` template declared in rigs/ftx1.toml ---------

_FTX1_PROFILE = Path(__file__).resolve().parents[2] / "rigs" / "ftx1.toml"
_FIXED_WIDTH_INT = re.compile(r"0(\d+)d")
_CAT_CHAR = st.characters(min_codepoint=0x20, max_codepoint=0x7E)


def _parse_templates(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "parse" and isinstance(value, str):
                found.add(value)
            else:
                found |= _parse_templates(value)
    elif isinstance(node, list):
        for item in node:
            found |= _parse_templates(item)
    return found


_FTX1_PARSE_TEMPLATES = sorted(
    _parse_templates(tomllib.loads(_FTX1_PROFILE.read_text(encoding="utf-8")))
)


def _field_strategy(name: str, spec: str) -> st.SearchStrategy[Any]:
    width = _FIXED_WIDTH_INT.fullmatch(spec)
    if width is not None:
        return st.integers(0, 10 ** int(width.group(1)) - 1)
    if spec:
        raise AssertionError(f"no generator for placeholder {{{name}:{spec}}}")
    if name == "receiver":
        return st.sampled_from((0, 1))
    if name == "sign":
        return st.sampled_from(("+", "-"))
    if name == "chan":
        return st.text(_CAT_CHAR, min_size=5, max_size=5)
    return _CAT_CHAR


def test_ftx1_profile_declares_parse_templates() -> None:
    assert _FTX1_PARSE_TEMPLATES


@pytest.mark.parametrize("template", _FTX1_PARSE_TEMPLATES)
@given(data=st.data())
def test_ftx1_reply_parse_inverts_format(template: str, data: st.DataObject) -> None:
    fields = {
        name: data.draw(_field_strategy(name, spec), label=name)
        for _, name, spec, _ in string.Formatter().parse(template)
        if name
    }
    reply = format_command(template, **fields)
    assert CatCommandParser(template).parse(reply) == fields
