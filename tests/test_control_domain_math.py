"""Python port of the TypeScript control-domain exact-math vectors (MOR-2472).

Every case below mirrors a case in
``frontend/src/lib/radio/__tests__/control-domain.test.ts`` with the same
inputs and expected outputs, followed by real FTX-1 profile domains. The
Python module must stay semantically identical to the TypeScript mirror;
a divergence here is a contract break.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from rigplane.profiles.control_domain import (
    control_display_band,
    decode_control_domain,
    encode_control_domain,
    quantize_control_domain,
)
from rigplane.rig_loader import load_rig

LINEAR: dict[str, Any] = {
    "raw_min": -4,
    "raw_max": 4,
    "raw_step": 2,
    "raw_origin": 0,
    "display_min": "-1",
    "display_max": "1",
    "display_step": "0.5",
    "display_origin": "0",
    "display_unit": "dB",
    "mapping": "linear",
    "quantization": "nearest_ties_down",
    "restoration": "exact",
}


def variant(**overrides: object) -> dict[str, Any]:
    """Spread-equivalent of the TypeScript ``{ ...linear, ... }`` cases."""
    domain = dict(LINEAR)
    domain.update(overrides)
    return domain


# -- Ported TypeScript cases -------------------------------------------------


def test_round_trips_a_valid_identity_domain() -> None:
    domain = variant(
        raw_min=-1,
        raw_max=1,
        raw_step=1,
        raw_origin=0,
        display_min="-1",
        display_max="1",
        display_step="1",
        display_origin="0",
        mapping="identity",
    )
    assert decode_control_domain(domain, -1) == "-1"
    assert encode_control_domain(domain, "1") == 1


def test_maps_linear_lattice_points_with_negative_values_and_nonzero_raw_origins() -> (
    None
):
    assert decode_control_domain(LINEAR, -4) == "-1"
    assert decode_control_domain(LINEAR, 0) == "0"
    assert encode_control_domain(LINEAR, "0.5") == 2
    assert encode_control_domain(LINEAR, "-0.75") == -4


@pytest.mark.parametrize(
    ("quantization", "expected"),
    [
        ("nearest_ties_down", "-1"),
        ("nearest_ties_up", "-0.5"),
        ("floor", "-1"),
        ("ceil", "-0.5"),
        ("reject", None),
    ],
)
def test_uses_quantization_at_negative_half_step_boundaries(
    quantization: str, expected: str | None
) -> None:
    assert quantize_control_domain(variant(quantization=quantization), "-0.75") == (
        expected
    )


@pytest.mark.parametrize(
    "quantization",
    ["nearest_ties_down", "nearest_ties_up", "floor", "ceil", "reject"],
)
def test_keeps_supported_quantization_valid(quantization: str) -> None:
    domain = variant(quantization=quantization)
    assert decode_control_domain(domain, 0) == "0"
    assert quantize_control_domain(domain, "0") == "0"
    assert encode_control_domain(domain, "0") == 0


def test_keeps_tiny_steps_and_huge_coefficients_exact() -> None:
    huge = "1" + "0" * 300
    domain = variant(
        raw_min=0,
        raw_max=2,
        raw_step=1,
        raw_origin=0,
        display_min=huge,
        display_origin=huge,
        display_step="0.00000000000000000001",
        display_max=f"{huge}.00000000000000000002",
    )
    assert decode_control_domain(domain, 1) == f"{huge}.00000000000000000001"
    assert encode_control_domain(domain, f"{huge}.00000000000000000002") == 2


def test_canonicalizes_mixed_scale_arithmetic_without_signed_or_fractional_zeroes() -> (
    None
):
    domain = variant(
        raw_min=-2,
        raw_max=0,
        raw_step=1,
        raw_origin=0,
        display_min="0",
        display_max="0.1",
        display_step="0.05",
        display_origin="0.1",
    )
    assert decode_control_domain(domain, 0) == "0.1"
    assert quantize_control_domain(domain, "0.1") == "0.1"


def test_uses_centered_anchors_independently_of_nonzero_lattice_origins() -> None:
    domain = variant(mapping="centered", raw_center=0, display_center="0")
    for raw, display in (
        (-4, "-1"),
        (-2, "-0.5"),
        (0, "0"),
        (2, "0.5"),
        (4, "1"),
    ):
        assert decode_control_domain(domain, raw) == display
        assert encode_control_domain(domain, display) == raw


def test_inverts_descending_lookup_values_and_rejects_ambiguous_entries() -> None:
    lookup = [
        {"raw": -4, "display": "1"},
        {"raw": -2, "display": "0.5"},
        {"raw": 0, "display": "0"},
        {"raw": 2, "display": "-0.5"},
        {"raw": 4, "display": "-1"},
    ]
    domain = variant(mapping="lookup", lookup=lookup)
    assert decode_control_domain(domain, 2) == "-0.5"
    assert encode_control_domain(domain, "-0.5") == 2
    ambiguous = variant(
        mapping="lookup", lookup=[*lookup, {"raw": 4, "display": "-0.5"}]
    )
    assert encode_control_domain(ambiguous, "-0.5") is None


def test_never_claims_reversible_encoding_for_unavailable_restoration() -> None:
    assert encode_control_domain(variant(restoration="unavailable"), "0") is None
    assert decode_control_domain(variant(restoration="unavailable"), 0) == "0"


def test_rejects_values_outside_the_axis_before_quantization_or_encoding() -> None:
    assert quantize_control_domain(LINEAR, "1.1") is None
    assert quantize_control_domain(LINEAR, "-1.1") is None
    assert encode_control_domain(LINEAR, "1.1") is None


@pytest.mark.parametrize(
    "lookup",
    [
        None,
        {},
        [{"raw": 0, "display": "0", "extra": True}],
        [{"raw": -4, "display": "-1"}, {"raw": -4, "display": "0"}],
    ],
    ids=["missing", "empty-object", "extra-key", "duplicate-raw"],
)
def test_fails_closed_for_malformed_lookup(lookup: object) -> None:
    domain = variant(mapping="lookup", lookup=lookup)
    assert decode_control_domain(domain, 0) is None
    assert quantize_control_domain(domain, "0") is None
    assert encode_control_domain(domain, "0") is None


def test_rejects_a_centered_domain_whose_center_offsets_do_not_align() -> None:
    domain = variant(mapping="centered", raw_center=0, display_center="0.5")
    assert decode_control_domain(domain, -4) is None
    assert decode_control_domain(domain, 4) is None
    assert quantize_control_domain(domain, "0") is None
    assert encode_control_domain(domain, "0") is None


@pytest.mark.parametrize(
    "quantization",
    [None, 0, {}, [], "", "nearest"],
    ids=["none", "zero", "object", "array", "empty-string", "unknown-word"],
)
def test_fails_closed_for_invalid_quantization(quantization: object) -> None:
    domain = variant(quantization=quantization)
    assert decode_control_domain(domain, 0) is None
    assert quantize_control_domain(domain, "0") is None
    assert encode_control_domain(domain, "0") is None


def test_fails_closed_for_off_lattice_raw_malformed_decimals_bad_mapping_and_overflow() -> (
    None
):
    assert decode_control_domain(LINEAR, -3) is None
    assert quantize_control_domain(LINEAR, "01") is None
    assert decode_control_domain(variant(mapping="future"), 0) is None
    # Number.MAX_SAFE_INTEGER + 1 in the TypeScript vector.
    assert encode_control_domain(variant(raw_max=2**53), "0") is None


# -- Canonical-decimal strictness (TypeScript parity) --------------------------


def _strict_linear_domain() -> dict[str, Any]:
    """Wide-axis linear domain that accepts ``0`` and ``-1.5`` as canonical."""
    return variant(
        raw_step=1,
        display_min="-2",
        display_max="2",
    )


@pytest.mark.parametrize(
    "display",
    ["0\n", "-0\n", "10\n", "1.5\n", " 0", "0 "],
)
def test_rejects_uncanonical_surrounding_whitespace_in_display_decimals(
    display: str,
) -> None:
    domain = _strict_linear_domain()
    assert quantize_control_domain(domain, display) is None
    assert encode_control_domain(domain, display) is None
    stepped = variant(display_step="1\n")
    assert decode_control_domain(stepped, 0) is None
    assert quantize_control_domain(stepped, "0") is None
    assert encode_control_domain(stepped, "0") is None


@pytest.mark.parametrize(
    ("display", "raw"),
    [("0", 0), ("-1.5", -3)],
)
def test_keeps_canonical_decimals_accepted(display: str, raw: int) -> None:
    domain = _strict_linear_domain()
    assert quantize_control_domain(domain, display) == display
    assert encode_control_domain(domain, display) == raw


# -- Real FTX-1 profile domains -----------------------------------------------

_PROFILE_PATH = Path(__file__).resolve().parents[1] / "rigs" / "ftx1.toml"


def _ftx1_domains() -> dict[str, dict[str, Any]]:
    profile = load_rig(_PROFILE_PATH).to_profile()
    assert profile.controls is not None
    return {name: dict(spec) for name, spec in profile.controls.items()}


def test_ftx1_linear_manual_notch_decodes_and_encodes() -> None:
    notch = _ftx1_domains()["manual_notch_freq"]
    assert decode_control_domain(notch, 1) == "10"
    assert decode_control_domain(notch, 320) == "3200"
    assert encode_control_domain(notch, "10") == 1
    assert encode_control_domain(notch, "3200") == 320


def test_ftx1_identity_domains_decode_and_encode() -> None:
    domains = _ftx1_domains()
    if_shift = domains["if_shift"]
    cw_pitch = domains["cw_pitch"]
    assert decode_control_domain(if_shift, 0) == "0"
    assert decode_control_domain(if_shift, -1200) == "-1200"
    assert encode_control_domain(if_shift, "1200") == 1200
    assert decode_control_domain(cw_pitch, 300) == "300"
    assert decode_control_domain(cw_pitch, 550) == "550"
    assert encode_control_domain(cw_pitch, "1050") == 1050


@pytest.mark.parametrize(
    ("quantization", "expected"),
    [
        ("nearest_ties_down", "10"),
        ("nearest_ties_up", "20"),
        ("floor", "10"),
        ("ceil", "20"),
        ("reject", None),
    ],
)
def test_ftx1_notch_half_step_ties(quantization: str, expected: str | None) -> None:
    notch = _ftx1_domains()["manual_notch_freq"] | {"quantization": quantization}
    assert quantize_control_domain(notch, "15") == expected


def test_ftx1_rejects_off_lattice_and_out_of_axis() -> None:
    notch = _ftx1_domains()["manual_notch_freq"]
    assert quantize_control_domain(notch, "15") is None
    assert quantize_control_domain(notch, "5") is None
    assert quantize_control_domain(notch, "3210") is None
    assert encode_control_domain(notch, "15") is None
    assert decode_control_domain(notch, 0) is None
    assert decode_control_domain(notch, 321) is None


# -- Declared control bands (MOR-2476) -----------------------------------------


def _rig_domains(rig: str) -> dict[str, dict[str, Any]]:
    """Published ``[controls.*]`` tables of a shipped rig profile."""
    profile = load_rig(_PROFILE_PATH.parent / f"{rig}.toml").to_profile()
    assert profile.controls is not None
    return {name: dict(spec) for name, spec in profile.controls.items()}


def test_band_normalized_domain_yields_display_band_and_declared_step() -> None:
    # FTX-1 cw_pitch: identity 300..1050 on a 10 Hz display lattice.
    assert control_display_band(_ftx1_domains(), "cw_pitch") == (
        Decimal("300"),
        Decimal("1050"),
        Decimal("10"),
    )


def test_band_normalized_linear_domain_yields_display_axis() -> None:
    assert control_display_band({"pbt": dict(LINEAR)}, "pbt") == (
        Decimal("-1"),
        Decimal("1"),
        Decimal("0.5"),
    )


def test_band_legacy_display_pair_yields_display_band_without_step() -> None:
    # IC-7610 cw_pitch shape: raw 0-255 with a legacy display 300-900 band.
    legacy = {
        "raw_min": 0,
        "raw_max": 255,
        "display_min": 300,
        "display_max": 900,
        "display_unit": "Hz",
    }
    assert control_display_band({"cw_pitch": legacy}, "cw_pitch") == (
        Decimal(300),
        Decimal(900),
        None,
    )


def test_band_band_only_range_pair_yields_that_band() -> None:
    # FTX-1 nb/nr shape: only range_min/range_max declared.
    assert control_display_band({"nb": {"range_min": 0, "range_max": 10}}, "nb") == (
        Decimal(0),
        Decimal(10),
        None,
    )


def test_band_raw_only_pair_yields_the_raw_band() -> None:
    # Icom/Xiegu level-control shape: only raw_min/raw_max declared.
    assert control_display_band(
        {"nr_level": {"raw_min": 0, "raw_max": 255}}, "nr_level"
    ) == (Decimal(0), Decimal(255), None)


def test_band_is_none_for_absent_encoded_or_shapeless_controls() -> None:
    encoded = {
        "mapping": "encoded",
        "choices": [{"raw": 0, "label": "Default"}, {"raw": 1, "display": "1"}],
    }
    assert control_display_band({}, "cw_pitch") is None
    assert control_display_band(None, "cw_pitch") is None
    assert control_display_band({"cw_pitch": 7}, "cw_pitch") is None
    assert control_display_band({"cw_pitch": {}}, "cw_pitch") is None
    assert control_display_band({"sql_type": encoded}, "sql_type") is None
    # A half-declared pair declares no band at all.
    assert control_display_band({"c": {"display_min": 300}}, "c") is None


def test_band_fails_closed_for_malformed_declarations() -> None:
    # Inverted normalized display strings are refused, not reordered.
    inverted = dict(LINEAR) | {"display_min": "900", "display_max": "300"}
    assert control_display_band({"c": inverted}, "c") is None
    # Inverted legacy display pair likewise.
    legacy = {"raw_min": 0, "raw_max": 255, "display_min": 900, "display_max": 300}
    assert control_display_band({"c": legacy}, "c") is None
    # Non-canonical display strings are rejected, not coerced.
    noncanonical = dict(LINEAR) | {"display_step": "0.50"}
    assert control_display_band({"c": noncanonical}, "c") is None
    missing_step = {k: v for k, v in LINEAR.items() if k != "display_step"}
    assert control_display_band({"c": missing_step}, "c") is None
    assert control_display_band({"c": {"range_min": 10, "range_max": 0}}, "c") is None
    assert control_display_band({"c": {"raw_min": 255, "raw_max": 0}}, "c") is None


def test_band_profile_pins_ic7610_and_x6200_cw_pitch() -> None:
    # The legacy display pair is the profile-declared band: 300-900 on the
    # IC-7610 but 400-1200 on the X6200 -- the profile decides, not a code
    # constant.
    assert control_display_band(_rig_domains("ic7610"), "cw_pitch") == (
        Decimal(300),
        Decimal(900),
        None,
    )
    assert control_display_band(_rig_domains("x6200"), "cw_pitch") == (
        Decimal(400),
        Decimal(1200),
        None,
    )
