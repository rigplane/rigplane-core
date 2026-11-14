"""Parser/emitter tests for the N1MM+ external spectrum datagram (MOR-4394)."""

from __future__ import annotations

import random
import re
import xml.etree.ElementTree as ET

import pytest

from rigplane.scope.n1mm_spectrum import (
    MAX_DATAGRAM_BYTES,
    N1mmSpectrumPacket,
    N1mmSpectrumRejected,
    emit_n1mm_spectrum,
    parse_n1mm_spectrum,
)

EXAMPLE = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b"<Spectrum><app>Thetis</app><Name>Thetis_1</Name>"
    b"<LowScopeFrequency>14000.000</LowScopeFrequency>\n"
    b"<HighScopeFrequency>14055.000</HighScopeFrequency>"
    b"<ScalingFactor>1</ScalingFactor>\n"
    b"<DataCount>4</DataCount>"
    b"<SpectrumData>10,20,65535,0</SpectrumData></Spectrum>"
)


def _without_tag(xml: bytes, tag: str) -> bytes:
    return re.sub(rf"<{tag}>.*?</{tag}>".encode(), b"", xml, count=1)


def _rep(old: bytes, new: bytes) -> bytes:
    return EXAMPLE.replace(old, new, 1)


def test_valid_example_parses_with_hz_edges() -> None:
    result = parse_n1mm_spectrum(EXAMPLE)
    assert result == N1mmSpectrumPacket(
        app="Thetis",
        name="Thetis_1",
        low_hz=14_000_000.0,
        high_hz=14_055_000.0,
        scaling_factor=1.0,
        levels=(10, 20, 65535, 0),
    )


@pytest.mark.parametrize(
    ("text", "expected_hz"),
    [
        ("7074.123", 7_074_123.0),
        ("14000,000", 14_000_000.0),
        ("14000.5", 14_000_500.0),
        ("  14000.000  ", 14_000_000.0),
    ],
)
def test_scalar_decimal_forms(text: str, expected_hz: float) -> None:
    xml = EXAMPLE.replace(b"14000.000", text.encode(), 1)
    result = parse_n1mm_spectrum(xml)
    assert isinstance(result, N1mmSpectrumPacket)
    assert result.low_hz == expected_hz


def test_shuffled_elements_and_unknown_extra_are_ignored() -> None:
    root = ET.fromstring(EXAMPLE)
    order = list(root)
    root[:] = sorted(order, key=lambda el: el.tag, reverse=True)
    extra = ET.SubElement(root, "FutureField")
    extra.text = "whatever"
    result = parse_n1mm_spectrum(ET.tostring(root))
    assert isinstance(result, N1mmSpectrumPacket)
    assert (result.low_hz, result.high_hz) == (14_000_000.0, 14_055_000.0)


def test_predefined_escapes_decode_in_name() -> None:
    xml = EXAMPLE.replace(b"Thetis_1", b"Thetis &amp; 1 &lt;2&gt;", 1)
    result = parse_n1mm_spectrum(xml)
    assert isinstance(result, N1mmSpectrumPacket)
    assert result.name == "Thetis & 1 <2>"


def test_whitespace_around_commas_is_allowed() -> None:
    xml = EXAMPLE.replace(b"10,20,65535,0", b"10 , 20,65535 ,0", 1)
    assert parse_n1mm_spectrum(xml) == parse_n1mm_spectrum(EXAMPLE)


REJECTIONS: list[tuple[str, bytes, str]] = [
    ("too_large", b"x" * (MAX_DATAGRAM_BYTES + 1), "too_large"),
    ("malformed", b"<Spectrum><app>", "malformed_xml"),
    ("bad_freq", _rep(b"14000.000", b"abc"), "bad_number"),
    ("non_utf8", b"\xff\xfe<Spectrum/>", "malformed_xml"),
    (
        "laughs",
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lollollol">'
        b'<!ENTITY lol2 "&lol;&lol;">]><Spectrum/>',
        "doctype_forbidden",
    ),
    (
        "external_lowercase",
        b'<?xml version="1.0"?><!doctype foo SYSTEM "file:///etc/passwd"><Spectrum/>',
        "doctype_forbidden",
    ),
    ("wrong_root", EXAMPLE.replace(b"Spectrum", b"Radio"), "wrong_root"),
    ("empty_item", _rep(b"10,20", b"1,,2"), "bad_number"),
    ("trailing_comma", _rep(b"65535,0", b"65535,0,"), "bad_number"),
    ("non_numeric_level", _rep(b"10,20", b"10,ab"), "bad_number"),
    ("level_65536", _rep(b"10,20", b"10,65536"), "level_out_of_range"),
    ("negative_level", _rep(b"10,20", b"10,-1"), "level_out_of_range"),
    ("too_few_count", _rep(b"<DataCount>4<", b"<DataCount>1<"), "count_mismatch"),
    ("short_items", _rep(b"10,20,65535,0", b"10,20,0"), "count_mismatch"),
    ("long_items", _rep(b"10,20,65535,0", b"10,20,30,0,5"), "count_mismatch"),
    ("huge_count", _rep(b"<DataCount>4<", b"<DataCount>65536<"), "too_many_points"),
    ("bad_count", _rep(b"<DataCount>4<", b"<DataCount>x<"), "bad_number"),
    ("low_ge_high", _rep(b"14000.000", b"14055.000"), "bad_range"),
    ("zero_low", _rep(b"14000.000", b"0"), "bad_range"),
    ("nan_freq", _rep(b"14000.000", b"nan"), "bad_number"),
    ("inf_freq", _rep(b"14000.000", b"inf"), "bad_number"),
    ("two_separators", _rep(b"14000.000", b"14,000.5"), "bad_number"),
    ("zero_scale", _rep(b"<ScalingFactor>1<", b"<ScalingFactor>0<"), "bad_number"),
    ("nan_scale", _rep(b"<ScalingFactor>1<", b"<ScalingFactor>NaN<"), "bad_number"),
    ("inf_scale", _rep(b"<ScalingFactor>1<", b"<ScalingFactor>1e999<"), "bad_number"),
    ("overlong_app", _rep(b"Thetis</app>", b"T" * 65 + b"</app>"), "too_long"),
]


@pytest.mark.parametrize("label, xml, reason", REJECTIONS)
def test_rejections(label: str, xml: bytes, reason: str) -> None:
    result = parse_n1mm_spectrum(xml)
    assert isinstance(result, N1mmSpectrumRejected)
    assert result.reason == reason


@pytest.mark.parametrize(
    "tag",
    [
        "app",
        "Name",
        "LowScopeFrequency",
        "HighScopeFrequency",
        "ScalingFactor",
        "DataCount",
        "SpectrumData",
    ],
)
def test_missing_each_required_field(tag: str) -> None:
    result = parse_n1mm_spectrum(_without_tag(EXAMPLE, tag))
    assert isinstance(result, N1mmSpectrumRejected)
    assert result.reason == "missing_field"


def test_single_point_is_rejected() -> None:
    tiny = N1mmSpectrumPacket("Thetis", "T", 14_000_000.0, 14_001_000.0, 1.0, (7,))
    result = parse_n1mm_spectrum(emit_n1mm_spectrum(tiny))
    assert isinstance(result, N1mmSpectrumRejected)
    assert result.reason == "count_mismatch"


def test_parse_never_raises_on_random_bytes() -> None:
    rng = random.Random(4394)
    alphabet = b"<>&'\"!ENTITYdoctype Spectrum0123456789,.\xff\xfe \n"
    for i in range(500):
        if i < 250:
            blob = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
        else:
            blob = bytes(rng.choice(alphabet) for _ in range(rng.randrange(0, 200)))
        assert isinstance(
            parse_n1mm_spectrum(blob), (N1mmSpectrumPacket, N1mmSpectrumRejected)
        )


@pytest.mark.parametrize("size", [475, 1024, 16384])
def test_round_trip(size: int) -> None:
    rng = random.Random(1000 + size)
    packet = N1mmSpectrumPacket(
        app="emit",
        name="rig & <1>",
        low_hz=7_000_000.0,
        high_hz=7_000_000.0 + (size - 1) * 1000.0,
        scaling_factor=0.3125,
        levels=tuple(rng.randrange(161) for _ in range(size)),
    )
    assert parse_n1mm_spectrum(emit_n1mm_spectrum(packet)) == packet
