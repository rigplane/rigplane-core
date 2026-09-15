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
    decode_legacy_control,
    encode_control_domain,
    encode_legacy_control,
    quantize_control_domain,
    snap_control_domain,
)
from rigplane.rig_loader import RigLoadError, load_rig
from rigplane.rig_loader import _parse_control_spec

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


def test_snaps_identity_domains_to_the_nearest_legal_point() -> None:
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
    assert snap_control_domain(domain, "0") == "0"
    assert snap_control_domain(domain, "0.4") == "0"
    assert snap_control_domain(domain, "0.5") == "1"


def test_snaps_linear_domains_with_nonzero_origins_and_ties_up() -> None:
    domain = variant(
        raw_min=-4,
        raw_origin=-4,
        display_min="-1",
        display_origin="-1",
    )
    assert snap_control_domain(domain, "-1") == "-1"
    assert snap_control_domain(domain, "-0.75") == "-0.5"
    assert snap_control_domain(domain, "0.8") == "1"


@pytest.mark.parametrize("display", ["-1.1", "1.1"])
def test_snapping_out_of_range_raises_naming_the_display_range(
    display: str,
) -> None:
    with pytest.raises(ValueError, match=r"-1-1\b"):
        snap_control_domain(LINEAR, display)


@pytest.mark.parametrize("display", ["01", "0.50", " 0", "0 ", "0\n", "-0"])
def test_snapping_fails_closed_for_non_canonical_display(display: str) -> None:
    assert snap_control_domain(LINEAR, display) is None


@pytest.mark.parametrize(
    "domain",
    [
        variant(mapping="future"),
        variant(mapping="lookup", lookup=[{"raw": 0, "display": "0", "extra": 1}]),
        "not-a-domain",
    ],
    ids=["unknown-mapping", "malformed-lookup", "not-a-mapping"],
)
def test_snapping_fails_closed_for_invalid_domains(domain: object) -> None:
    assert snap_control_domain(domain, "0") is None


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


def test_ftx1_notch_snaps_to_the_nearest_legal_hz() -> None:
    notch = _ftx1_domains()["manual_notch_freq"]
    assert snap_control_domain(notch, "1500") == "1500"
    assert snap_control_domain(notch, "1505") == "1510"


def test_ftx1_identity_domains_snap_ties_up() -> None:
    cw_pitch = _ftx1_domains()["cw_pitch"]
    assert snap_control_domain(cw_pitch, "304") == "300"
    assert snap_control_domain(cw_pitch, "305") == "310"


@pytest.mark.parametrize("display", ["5", "3300", "-10"])
def test_ftx1_notch_snap_out_of_range_raises_naming_the_range(
    display: str,
) -> None:
    notch = _ftx1_domains()["manual_notch_freq"]
    with pytest.raises(ValueError, match="10-3200"):
        snap_control_domain(notch, display)


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


# -- Legacy rational decode (MOR-2473 read path, MOR-2481 decode half) ---------

# Golden vectors captured from commands/levels.py before its decode helpers
# were deleted: every entry below is the literal output of the old
# ``_cw_pitch_from_level`` / ``_key_speed_from_level`` for that raw level.
_ICOM_CW_PITCH_HZ = (
    300,
    300,
    305,
    305,
    310,
    310,
    315,
    315,
    320,
    320,
    325,
    325,
    330,
    330,
    335,
    335,
    340,
    340,
    340,
    345,
    345,
    350,
    350,
    355,
    355,
    360,
    360,
    365,
    365,
    370,
    370,
    375,
    375,
    380,
    380,
    380,
    385,
    385,
    390,
    390,
    395,
    395,
    400,
    400,
    405,
    405,
    410,
    410,
    415,
    415,
    420,
    420,
    420,
    425,
    425,
    430,
    430,
    435,
    435,
    440,
    440,
    445,
    445,
    450,
    450,
    455,
    455,
    460,
    460,
    460,
    465,
    465,
    470,
    470,
    475,
    475,
    480,
    480,
    485,
    485,
    490,
    490,
    495,
    495,
    500,
    500,
    500,
    505,
    505,
    510,
    510,
    515,
    515,
    520,
    520,
    525,
    525,
    530,
    530,
    535,
    535,
    540,
    540,
    540,
    545,
    545,
    550,
    550,
    555,
    555,
    560,
    560,
    565,
    565,
    570,
    570,
    575,
    575,
    580,
    580,
    580,
    585,
    585,
    590,
    590,
    595,
    595,
    600,
    600,
    605,
    605,
    610,
    610,
    615,
    615,
    620,
    620,
    620,
    625,
    625,
    630,
    630,
    635,
    635,
    640,
    640,
    645,
    645,
    650,
    650,
    655,
    655,
    660,
    660,
    660,
    665,
    665,
    670,
    670,
    675,
    675,
    680,
    680,
    685,
    685,
    690,
    690,
    695,
    695,
    700,
    700,
    700,
    705,
    705,
    710,
    710,
    715,
    715,
    720,
    720,
    725,
    725,
    730,
    730,
    735,
    735,
    740,
    740,
    740,
    745,
    745,
    750,
    750,
    755,
    755,
    760,
    760,
    765,
    765,
    770,
    770,
    775,
    775,
    780,
    780,
    780,
    785,
    785,
    790,
    790,
    795,
    795,
    800,
    800,
    805,
    805,
    810,
    810,
    815,
    815,
    820,
    820,
    820,
    825,
    825,
    830,
    830,
    835,
    835,
    840,
    840,
    845,
    845,
    850,
    850,
    855,
    855,
    860,
    860,
    860,
    865,
    865,
    870,
    870,
    875,
    875,
    880,
    880,
    885,
    885,
    890,
    890,
    895,
    895,
    900,
    900,
)

_ICOM_KEY_SPEED_WPM = (
    6,
    6,
    6,
    6,
    7,
    7,
    7,
    7,
    7,
    7,
    8,
    8,
    8,
    8,
    8,
    8,
    9,
    9,
    9,
    9,
    9,
    9,
    10,
    10,
    10,
    10,
    10,
    10,
    11,
    11,
    11,
    11,
    11,
    11,
    12,
    12,
    12,
    12,
    12,
    12,
    13,
    13,
    13,
    13,
    13,
    13,
    14,
    14,
    14,
    14,
    14,
    14,
    15,
    15,
    15,
    15,
    15,
    15,
    16,
    16,
    16,
    16,
    16,
    16,
    17,
    17,
    17,
    17,
    17,
    17,
    18,
    18,
    18,
    18,
    18,
    18,
    19,
    19,
    19,
    19,
    19,
    19,
    20,
    20,
    20,
    20,
    20,
    20,
    20,
    21,
    21,
    21,
    21,
    21,
    21,
    22,
    22,
    22,
    22,
    22,
    22,
    23,
    23,
    23,
    23,
    23,
    23,
    24,
    24,
    24,
    24,
    24,
    24,
    25,
    25,
    25,
    25,
    25,
    25,
    26,
    26,
    26,
    26,
    26,
    26,
    27,
    27,
    27,
    27,
    27,
    27,
    28,
    28,
    28,
    28,
    28,
    28,
    29,
    29,
    29,
    29,
    29,
    29,
    30,
    30,
    30,
    30,
    30,
    30,
    31,
    31,
    31,
    31,
    31,
    31,
    32,
    32,
    32,
    32,
    32,
    32,
    33,
    33,
    33,
    33,
    33,
    33,
    34,
    34,
    34,
    34,
    34,
    34,
    34,
    35,
    35,
    35,
    35,
    35,
    35,
    36,
    36,
    36,
    36,
    36,
    36,
    37,
    37,
    37,
    37,
    37,
    37,
    38,
    38,
    38,
    38,
    38,
    38,
    39,
    39,
    39,
    39,
    39,
    39,
    40,
    40,
    40,
    40,
    40,
    40,
    41,
    41,
    41,
    41,
    41,
    41,
    42,
    42,
    42,
    42,
    42,
    42,
    43,
    43,
    43,
    43,
    43,
    43,
    44,
    44,
    44,
    44,
    44,
    44,
    45,
    45,
    45,
    45,
    45,
    45,
    46,
    46,
    46,
    46,
    46,
    46,
    47,
    47,
    47,
    47,
    47,
    47,
    48,
    48,
    48,
    48,
)


@pytest.mark.parametrize("rig", ["ic7300", "ic7610", "ic9700", "ic705"])
def test_ci_v_cw_pitch_decode_matches_the_deleted_formula_vectors(rig: str) -> None:
    controls = _rig_domains(rig)
    for raw in range(256):
        assert (
            decode_legacy_control(controls, "cw_pitch", raw) == (_ICOM_CW_PITCH_HZ[raw])
        ), f"{rig} cw_pitch raw {raw}"


@pytest.mark.parametrize("rig", ["ic7300", "ic7610", "ic9700", "ic705", "x6200"])
def test_ci_v_key_speed_decode_matches_the_deleted_formula_vectors(rig: str) -> None:
    # The X6200 profile declares the same 6-48 WPM band the shared CI-V
    # key-speed decode used on origin/main, so its vectors are identical.
    controls = _rig_domains(rig)
    for raw in range(256):
        assert (
            decode_legacy_control(controls, "key_speed", raw)
            == (_ICOM_KEY_SPEED_WPM[raw])
        ), f"{rig} key_speed raw {raw}"


def test_x6200_cw_pitch_decode_uses_the_declared_400_1200_domain() -> None:
    # rigs/x6200.toml [controls.cw_pitch] cites the Radioddity X6200 CI-V
    # V1.0.6 PDF page 6: "0=400Hz, 255=1200Hz" for CW sidetone.
    controls = _rig_domains("x6200")
    assert decode_legacy_control(controls, "cw_pitch", 0) == 400
    assert decode_legacy_control(controls, "cw_pitch", 255) == 1200


# Golden vectors regenerated from the X6100 profile's documented domain, not
# captured from any shared CI-V formula: every entry below is
# decode_legacy_control over the declared 400-1200 Hz band with its 10 Hz
# decode_quantum (Radioddity Extended manual for Xiegu X6100 v1.1.8 section
# 10.3.4) and the declared 5-50 WPM band (section 10.3.2). They pin the
# declared bands: any change to either moves these rows.
_X6100_CW_PITCH_HZ = (
    400,
    400,
    410,
    410,
    410,
    420,
    420,
    420,
    430,
    430,
    430,
    430,
    440,
    440,
    440,
    450,
    450,
    450,
    460,
    460,
    460,
    470,
    470,
    470,
    480,
    480,
    480,
    480,
    490,
    490,
    490,
    500,
    500,
    500,
    510,
    510,
    510,
    520,
    520,
    520,
    530,
    530,
    530,
    530,
    540,
    540,
    540,
    550,
    550,
    550,
    560,
    560,
    560,
    570,
    570,
    570,
    580,
    580,
    580,
    590,
    590,
    590,
    590,
    600,
    600,
    600,
    610,
    610,
    610,
    620,
    620,
    620,
    630,
    630,
    630,
    640,
    640,
    640,
    640,
    650,
    650,
    650,
    660,
    660,
    660,
    670,
    670,
    670,
    680,
    680,
    680,
    690,
    690,
    690,
    690,
    700,
    700,
    700,
    710,
    710,
    710,
    720,
    720,
    720,
    730,
    730,
    730,
    740,
    740,
    740,
    750,
    750,
    750,
    750,
    760,
    760,
    760,
    770,
    770,
    770,
    780,
    780,
    780,
    790,
    790,
    790,
    800,
    800,
    800,
    800,
    810,
    810,
    810,
    820,
    820,
    820,
    830,
    830,
    830,
    840,
    840,
    840,
    850,
    850,
    850,
    850,
    860,
    860,
    860,
    870,
    870,
    870,
    880,
    880,
    880,
    890,
    890,
    890,
    900,
    900,
    900,
    910,
    910,
    910,
    910,
    920,
    920,
    920,
    930,
    930,
    930,
    940,
    940,
    940,
    950,
    950,
    950,
    960,
    960,
    960,
    960,
    970,
    970,
    970,
    980,
    980,
    980,
    990,
    990,
    990,
    1000,
    1000,
    1000,
    1010,
    1010,
    1010,
    1010,
    1020,
    1020,
    1020,
    1030,
    1030,
    1030,
    1040,
    1040,
    1040,
    1050,
    1050,
    1050,
    1060,
    1060,
    1060,
    1070,
    1070,
    1070,
    1070,
    1080,
    1080,
    1080,
    1090,
    1090,
    1090,
    1100,
    1100,
    1100,
    1110,
    1110,
    1110,
    1120,
    1120,
    1120,
    1120,
    1130,
    1130,
    1130,
    1140,
    1140,
    1140,
    1150,
    1150,
    1150,
    1160,
    1160,
    1160,
    1170,
    1170,
    1170,
    1170,
    1180,
    1180,
    1180,
    1190,
    1190,
    1190,
    1200,
    1200,
)

_X6100_KEY_SPEED_WPM = (
    5,
    5,
    5,
    6,
    6,
    6,
    6,
    6,
    6,
    7,
    7,
    7,
    7,
    7,
    7,
    8,
    8,
    8,
    8,
    8,
    9,
    9,
    9,
    9,
    9,
    9,
    10,
    10,
    10,
    10,
    10,
    10,
    11,
    11,
    11,
    11,
    11,
    12,
    12,
    12,
    12,
    12,
    12,
    13,
    13,
    13,
    13,
    13,
    13,
    14,
    14,
    14,
    14,
    14,
    15,
    15,
    15,
    15,
    15,
    15,
    16,
    16,
    16,
    16,
    16,
    16,
    17,
    17,
    17,
    17,
    17,
    18,
    18,
    18,
    18,
    18,
    18,
    19,
    19,
    19,
    19,
    19,
    19,
    20,
    20,
    20,
    20,
    20,
    21,
    21,
    21,
    21,
    21,
    21,
    22,
    22,
    22,
    22,
    22,
    22,
    23,
    23,
    23,
    23,
    23,
    24,
    24,
    24,
    24,
    24,
    24,
    25,
    25,
    25,
    25,
    25,
    25,
    26,
    26,
    26,
    26,
    26,
    27,
    27,
    27,
    27,
    27,
    27,
    28,
    28,
    28,
    28,
    28,
    28,
    29,
    29,
    29,
    29,
    29,
    30,
    30,
    30,
    30,
    30,
    30,
    31,
    31,
    31,
    31,
    31,
    31,
    32,
    32,
    32,
    32,
    32,
    33,
    33,
    33,
    33,
    33,
    33,
    34,
    34,
    34,
    34,
    34,
    34,
    35,
    35,
    35,
    35,
    35,
    36,
    36,
    36,
    36,
    36,
    36,
    37,
    37,
    37,
    37,
    37,
    37,
    38,
    38,
    38,
    38,
    38,
    39,
    39,
    39,
    39,
    39,
    39,
    40,
    40,
    40,
    40,
    40,
    40,
    41,
    41,
    41,
    41,
    41,
    42,
    42,
    42,
    42,
    42,
    42,
    43,
    43,
    43,
    43,
    43,
    43,
    44,
    44,
    44,
    44,
    44,
    45,
    45,
    45,
    45,
    45,
    45,
    46,
    46,
    46,
    46,
    46,
    46,
    47,
    47,
    47,
    47,
    47,
    48,
    48,
    48,
    48,
    48,
    48,
    49,
    49,
    49,
    49,
    49,
    49,
    50,
    50,
    50,
)


def test_x6100_cw_pitch_decode_matches_the_declared_400_1200_domain() -> None:
    controls = _rig_domains("x6100")
    for raw in range(256):
        assert (
            decode_legacy_control(controls, "cw_pitch", raw) == _X6100_CW_PITCH_HZ[raw]
        ), f"x6100 cw_pitch raw {raw}"


def test_x6100_key_speed_decode_matches_the_declared_5_50_domain() -> None:
    controls = _rig_domains("x6100")
    for raw in range(256):
        assert (
            decode_legacy_control(controls, "key_speed", raw)
            == _X6100_KEY_SPEED_WPM[raw]
        ), f"x6100 key_speed raw {raw}"


def test_legacy_decode_fails_closed_when_the_control_is_missing() -> None:
    with pytest.raises(ValueError, match="cw_pitch"):
        decode_legacy_control({}, "cw_pitch", 128)
    with pytest.raises(ValueError, match="key_speed"):
        decode_legacy_control(None, "key_speed", 128)


def test_legacy_decode_fails_closed_for_malformed_domains_and_raw() -> None:
    missing_quantum = {
        "raw_min": 0,
        "raw_max": 255,
        "display_min": 300,
        "display_max": 900,
    }
    with pytest.raises(ValueError, match="cw_pitch"):
        decode_legacy_control({"cw_pitch": missing_quantum}, "cw_pitch", 128)
    with pytest.raises(ValueError, match="cw_pitch"):
        decode_legacy_control({"cw_pitch": 7}, "cw_pitch", 128)
    controls = _rig_domains("ic7300")
    with pytest.raises(ValueError, match="cw_pitch"):
        decode_legacy_control(controls, "cw_pitch", 256)
    with pytest.raises(ValueError, match="cw_pitch"):
        decode_legacy_control(controls, "cw_pitch", -1)


def test_loader_rejects_a_legacy_domain_with_an_exact_half_tie() -> None:
    # raw 1 over raw 0..2, display span 1, quantum 1 gives Fraction(1, 2) --
    # an exact tie the loader must reject rather than round silently.
    tie = {
        "raw_min": 0,
        "raw_max": 2,
        "display_min": 0,
        "display_max": 1,
        "display_unit": "Hz",
        "decode_quantum": 1,
    }
    with pytest.raises(RigLoadError, match="tie"):
        _parse_control_spec("rigs/synthetic.toml", "cw_pitch", tie)


# -- Legacy rational encode (MOR-2473 write path, MOR-2481 encode half) --------


def test_legacy_encode_applies_the_declared_rounding_rule() -> None:
    ceil_controls = {
        "cw_pitch": {
            "raw_min": 0,
            "raw_max": 255,
            "display_min": 300,
            "display_max": 900,
            "encode_rounding": "ceil",
        }
    }
    # ceil((display - 300) * 255 / 600): 300 -> 0, 301 -> 1 (255/600 rounds
    # up from 0.425), 305 -> 3 (2.125), 600 -> 128 (an exact half, up under
    # ceil), 900 -> 255.
    assert encode_legacy_control(ceil_controls, "cw_pitch", 300) == 0
    assert encode_legacy_control(ceil_controls, "cw_pitch", 301) == 1
    assert encode_legacy_control(ceil_controls, "cw_pitch", 305) == 3
    assert encode_legacy_control(ceil_controls, "cw_pitch", 600) == 128
    assert encode_legacy_control(ceil_controls, "cw_pitch", 900) == 255

    controls = {
        "key_speed": {
            "raw_min": 0,
            "raw_max": 255,
            "display_min": 6,
            "display_max": 48,
            "encode_rounding": "nearest_half_down",
        }
    }
    # nearest((wpm - 6) * 255 / 42) with exact halves down: 6 -> 0, 7 -> 6
    # (6.071...), 48 -> 255, and the three exact ties in range -- 13 WPM
    # (85/2), 27 WPM (255/2), 41 WPM (425/2) -- all round down.
    assert encode_legacy_control(controls, "key_speed", 6) == 0
    assert encode_legacy_control(controls, "key_speed", 7) == 6
    assert encode_legacy_control(controls, "key_speed", 48) == 255
    assert encode_legacy_control(controls, "key_speed", 13) == 42
    assert encode_legacy_control(controls, "key_speed", 27) == 127
    assert encode_legacy_control(controls, "key_speed", 41) == 212


def test_legacy_encode_fails_closed_when_the_control_is_missing_or_rounding_is_absent() -> (
    None
):
    no_rounding = {
        "raw_min": 0,
        "raw_max": 255,
        "display_min": 300,
        "display_max": 900,
        "decode_quantum": 5,
    }
    with pytest.raises(ValueError, match="cw_pitch"):
        encode_legacy_control({"cw_pitch": no_rounding}, "cw_pitch", 600)
    with pytest.raises(ValueError, match="key_speed"):
        encode_legacy_control(None, "key_speed", 30)
    with pytest.raises(ValueError, match="cw_pitch"):
        encode_legacy_control({}, "cw_pitch", 600)
    with pytest.raises(ValueError, match="cw_pitch"):
        encode_legacy_control({"cw_pitch": 7}, "cw_pitch", 600)
    undeclared_mode = dict(no_rounding, encode_rounding="floor")
    with pytest.raises(ValueError, match="cw_pitch"):
        encode_legacy_control({"cw_pitch": undeclared_mode}, "cw_pitch", 600)


# Encode golden vectors captured from commands/levels.py before its
# per-control encode helpers were deleted: every entry below is the literal
# output of the old Hz/WPM -> raw-level conversion for that display value.
_CW_PITCH_HZ_TO_LEVEL = {
    300: 0,
    301: 1,
    302: 1,
    303: 2,
    304: 2,
    305: 3,
    306: 3,
    307: 3,
    308: 4,
    309: 4,
    310: 5,
    311: 5,
    312: 6,
    313: 6,
    314: 6,
    315: 7,
    316: 7,
    317: 8,
    318: 8,
    319: 9,
    320: 9,
    321: 9,
    322: 10,
    323: 10,
    324: 11,
    325: 11,
    326: 12,
    327: 12,
    328: 12,
    329: 13,
    330: 13,
    331: 14,
    332: 14,
    333: 15,
    334: 15,
    335: 15,
    336: 16,
    337: 16,
    338: 17,
    339: 17,
    340: 17,
    341: 18,
    342: 18,
    343: 19,
    344: 19,
    345: 20,
    346: 20,
    347: 20,
    348: 21,
    349: 21,
    350: 22,
    351: 22,
    352: 23,
    353: 23,
    354: 23,
    355: 24,
    356: 24,
    357: 25,
    358: 25,
    359: 26,
    360: 26,
    361: 26,
    362: 27,
    363: 27,
    364: 28,
    365: 28,
    366: 29,
    367: 29,
    368: 29,
    369: 30,
    370: 30,
    371: 31,
    372: 31,
    373: 32,
    374: 32,
    375: 32,
    376: 33,
    377: 33,
    378: 34,
    379: 34,
    380: 34,
    381: 35,
    382: 35,
    383: 36,
    384: 36,
    385: 37,
    386: 37,
    387: 37,
    388: 38,
    389: 38,
    390: 39,
    391: 39,
    392: 40,
    393: 40,
    394: 40,
    395: 41,
    396: 41,
    397: 42,
    398: 42,
    399: 43,
    400: 43,
    401: 43,
    402: 44,
    403: 44,
    404: 45,
    405: 45,
    406: 46,
    407: 46,
    408: 46,
    409: 47,
    410: 47,
    411: 48,
    412: 48,
    413: 49,
    414: 49,
    415: 49,
    416: 50,
    417: 50,
    418: 51,
    419: 51,
    420: 51,
    421: 52,
    422: 52,
    423: 53,
    424: 53,
    425: 54,
    426: 54,
    427: 54,
    428: 55,
    429: 55,
    430: 56,
    431: 56,
    432: 57,
    433: 57,
    434: 57,
    435: 58,
    436: 58,
    437: 59,
    438: 59,
    439: 60,
    440: 60,
    441: 60,
    442: 61,
    443: 61,
    444: 62,
    445: 62,
    446: 63,
    447: 63,
    448: 63,
    449: 64,
    450: 64,
    451: 65,
    452: 65,
    453: 66,
    454: 66,
    455: 66,
    456: 67,
    457: 67,
    458: 68,
    459: 68,
    460: 68,
    461: 69,
    462: 69,
    463: 70,
    464: 70,
    465: 71,
    466: 71,
    467: 71,
    468: 72,
    469: 72,
    470: 73,
    471: 73,
    472: 74,
    473: 74,
    474: 74,
    475: 75,
    476: 75,
    477: 76,
    478: 76,
    479: 77,
    480: 77,
    481: 77,
    482: 78,
    483: 78,
    484: 79,
    485: 79,
    486: 80,
    487: 80,
    488: 80,
    489: 81,
    490: 81,
    491: 82,
    492: 82,
    493: 83,
    494: 83,
    495: 83,
    496: 84,
    497: 84,
    498: 85,
    499: 85,
    500: 85,
    501: 86,
    502: 86,
    503: 87,
    504: 87,
    505: 88,
    506: 88,
    507: 88,
    508: 89,
    509: 89,
    510: 90,
    511: 90,
    512: 91,
    513: 91,
    514: 91,
    515: 92,
    516: 92,
    517: 93,
    518: 93,
    519: 94,
    520: 94,
    521: 94,
    522: 95,
    523: 95,
    524: 96,
    525: 96,
    526: 97,
    527: 97,
    528: 97,
    529: 98,
    530: 98,
    531: 99,
    532: 99,
    533: 100,
    534: 100,
    535: 100,
    536: 101,
    537: 101,
    538: 102,
    539: 102,
    540: 102,
    541: 103,
    542: 103,
    543: 104,
    544: 104,
    545: 105,
    546: 105,
    547: 105,
    548: 106,
    549: 106,
    550: 107,
    551: 107,
    552: 108,
    553: 108,
    554: 108,
    555: 109,
    556: 109,
    557: 110,
    558: 110,
    559: 111,
    560: 111,
    561: 111,
    562: 112,
    563: 112,
    564: 113,
    565: 113,
    566: 114,
    567: 114,
    568: 114,
    569: 115,
    570: 115,
    571: 116,
    572: 116,
    573: 117,
    574: 117,
    575: 117,
    576: 118,
    577: 118,
    578: 119,
    579: 119,
    580: 119,
    581: 120,
    582: 120,
    583: 121,
    584: 121,
    585: 122,
    586: 122,
    587: 122,
    588: 123,
    589: 123,
    590: 124,
    591: 124,
    592: 125,
    593: 125,
    594: 125,
    595: 126,
    596: 126,
    597: 127,
    598: 127,
    599: 128,
    600: 128,
    601: 128,
    602: 129,
    603: 129,
    604: 130,
    605: 130,
    606: 131,
    607: 131,
    608: 131,
    609: 132,
    610: 132,
    611: 133,
    612: 133,
    613: 134,
    614: 134,
    615: 134,
    616: 135,
    617: 135,
    618: 136,
    619: 136,
    620: 136,
    621: 137,
    622: 137,
    623: 138,
    624: 138,
    625: 139,
    626: 139,
    627: 139,
    628: 140,
    629: 140,
    630: 141,
    631: 141,
    632: 142,
    633: 142,
    634: 142,
    635: 143,
    636: 143,
    637: 144,
    638: 144,
    639: 145,
    640: 145,
    641: 145,
    642: 146,
    643: 146,
    644: 147,
    645: 147,
    646: 148,
    647: 148,
    648: 148,
    649: 149,
    650: 149,
    651: 150,
    652: 150,
    653: 151,
    654: 151,
    655: 151,
    656: 152,
    657: 152,
    658: 153,
    659: 153,
    660: 153,
    661: 154,
    662: 154,
    663: 155,
    664: 155,
    665: 156,
    666: 156,
    667: 156,
    668: 157,
    669: 157,
    670: 158,
    671: 158,
    672: 159,
    673: 159,
    674: 159,
    675: 160,
    676: 160,
    677: 161,
    678: 161,
    679: 162,
    680: 162,
    681: 162,
    682: 163,
    683: 163,
    684: 164,
    685: 164,
    686: 165,
    687: 165,
    688: 165,
    689: 166,
    690: 166,
    691: 167,
    692: 167,
    693: 168,
    694: 168,
    695: 168,
    696: 169,
    697: 169,
    698: 170,
    699: 170,
    700: 170,
    701: 171,
    702: 171,
    703: 172,
    704: 172,
    705: 173,
    706: 173,
    707: 173,
    708: 174,
    709: 174,
    710: 175,
    711: 175,
    712: 176,
    713: 176,
    714: 176,
    715: 177,
    716: 177,
    717: 178,
    718: 178,
    719: 179,
    720: 179,
    721: 179,
    722: 180,
    723: 180,
    724: 181,
    725: 181,
    726: 182,
    727: 182,
    728: 182,
    729: 183,
    730: 183,
    731: 184,
    732: 184,
    733: 185,
    734: 185,
    735: 185,
    736: 186,
    737: 186,
    738: 187,
    739: 187,
    740: 187,
    741: 188,
    742: 188,
    743: 189,
    744: 189,
    745: 190,
    746: 190,
    747: 190,
    748: 191,
    749: 191,
    750: 192,
    751: 192,
    752: 193,
    753: 193,
    754: 193,
    755: 194,
    756: 194,
    757: 195,
    758: 195,
    759: 196,
    760: 196,
    761: 196,
    762: 197,
    763: 197,
    764: 198,
    765: 198,
    766: 199,
    767: 199,
    768: 199,
    769: 200,
    770: 200,
    771: 201,
    772: 201,
    773: 202,
    774: 202,
    775: 202,
    776: 203,
    777: 203,
    778: 204,
    779: 204,
    780: 204,
    781: 205,
    782: 205,
    783: 206,
    784: 206,
    785: 207,
    786: 207,
    787: 207,
    788: 208,
    789: 208,
    790: 209,
    791: 209,
    792: 210,
    793: 210,
    794: 210,
    795: 211,
    796: 211,
    797: 212,
    798: 212,
    799: 213,
    800: 213,
    801: 213,
    802: 214,
    803: 214,
    804: 215,
    805: 215,
    806: 216,
    807: 216,
    808: 216,
    809: 217,
    810: 217,
    811: 218,
    812: 218,
    813: 219,
    814: 219,
    815: 219,
    816: 220,
    817: 220,
    818: 221,
    819: 221,
    820: 221,
    821: 222,
    822: 222,
    823: 223,
    824: 223,
    825: 224,
    826: 224,
    827: 224,
    828: 225,
    829: 225,
    830: 226,
    831: 226,
    832: 227,
    833: 227,
    834: 227,
    835: 228,
    836: 228,
    837: 229,
    838: 229,
    839: 230,
    840: 230,
    841: 230,
    842: 231,
    843: 231,
    844: 232,
    845: 232,
    846: 233,
    847: 233,
    848: 233,
    849: 234,
    850: 234,
    851: 235,
    852: 235,
    853: 236,
    854: 236,
    855: 236,
    856: 237,
    857: 237,
    858: 238,
    859: 238,
    860: 238,
    861: 239,
    862: 239,
    863: 240,
    864: 240,
    865: 241,
    866: 241,
    867: 241,
    868: 242,
    869: 242,
    870: 243,
    871: 243,
    872: 244,
    873: 244,
    874: 244,
    875: 245,
    876: 245,
    877: 246,
    878: 246,
    879: 247,
    880: 247,
    881: 247,
    882: 248,
    883: 248,
    884: 249,
    885: 249,
    886: 250,
    887: 250,
    888: 250,
    889: 251,
    890: 251,
    891: 252,
    892: 252,
    893: 253,
    894: 253,
    895: 253,
    896: 254,
    897: 254,
    898: 255,
    899: 255,
    900: 255,
}

_KEY_SPEED_WPM_TO_LEVEL = {
    6: 0,
    7: 6,
    8: 12,
    9: 18,
    10: 24,
    11: 30,
    12: 36,
    13: 42,
    14: 49,
    15: 55,
    16: 61,
    17: 67,
    18: 73,
    19: 79,
    20: 85,
    21: 91,
    22: 97,
    23: 103,
    24: 109,
    25: 115,
    26: 121,
    27: 127,
    28: 134,
    29: 140,
    30: 146,
    31: 152,
    32: 158,
    33: 164,
    34: 170,
    35: 176,
    36: 182,
    37: 188,
    38: 194,
    39: 200,
    40: 206,
    41: 212,
    42: 219,
    43: 225,
    44: 231,
    45: 237,
    46: 243,
    47: 249,
    48: 255,
}


@pytest.mark.parametrize("rig", ["ic7300", "ic7610", "ic9700", "ic705"])
def test_ci_v_cw_pitch_encode_matches_the_deleted_formula_vectors(rig: str) -> None:
    controls = _rig_domains(rig)
    for hz, level in _CW_PITCH_HZ_TO_LEVEL.items():
        assert encode_legacy_control(controls, "cw_pitch", hz) == level, (
            f"{rig} cw_pitch {hz} Hz"
        )


@pytest.mark.parametrize("rig", ["ic7300", "ic7610", "ic9700", "ic705"])
def test_ci_v_key_speed_encode_matches_the_deleted_formula_vectors(rig: str) -> None:
    controls = _rig_domains(rig)
    for wpm, level in _KEY_SPEED_WPM_TO_LEVEL.items():
        assert encode_legacy_control(controls, "key_speed", wpm) == level, (
            f"{rig} key_speed {wpm} WPM"
        )


def test_x6200_key_speed_encode_matches_the_icom_vectors() -> None:
    # rigs/x6200.toml [controls.key_speed] declares the same 6-48 WPM band
    # and the same nearest_half_down rule as the Icom profiles, so the
    # captured Icom encode vectors apply unchanged.
    controls = _rig_domains("x6200")
    for wpm, level in _KEY_SPEED_WPM_TO_LEVEL.items():
        assert encode_legacy_control(controls, "key_speed", wpm) == level, (
            f"x6200 key_speed {wpm} WPM"
        )


def test_x6200_cw_pitch_encode_uses_the_declared_400_1200_domain() -> None:
    # rigs/x6200.toml [controls.cw_pitch] cites the Radioddity X6200 CI-V
    # V1.0.6 PDF page 6: "0=400Hz, 255=1200Hz" for CW sidetone.
    controls = _rig_domains("x6200")
    assert encode_legacy_control(controls, "cw_pitch", 400) == 0
    assert encode_legacy_control(controls, "cw_pitch", 1200) == 255
    # 900 Hz sits at (900 - 400) * 255 / 800 = 159.375, which ceil sends to
    # level 160.
    assert encode_legacy_control(controls, "cw_pitch", 900) == 160
    # The profile declares decode_quantum 1 (no Hz step documented), so the
    # documented-10-Hz-step round-trip claim does not apply: exactly 131 of
    # the 801 integers in 400-1200 round-trip exactly (400 and 1200 among
    # them) and every other integer lands within 3 Hz.
    round_trips = [
        h
        for h in range(400, 1201)
        if decode_legacy_control(
            controls, "cw_pitch", encode_legacy_control(controls, "cw_pitch", h)
        )
        == h
    ]
    assert len(round_trips) == 131
    assert round_trips[0] == 400 and round_trips[-1] == 1200
    for h in range(400, 1201):
        decoded = decode_legacy_control(
            controls, "cw_pitch", encode_legacy_control(controls, "cw_pitch", h)
        )
        assert abs(decoded - h) <= 3, f"x6200 cw_pitch {h} Hz round-trips to {decoded}"


def test_x6100_cw_pitch_encode_uses_the_declared_400_1200_domain() -> None:
    controls = _rig_domains("x6100")
    assert encode_legacy_control(controls, "cw_pitch", 400) == 0
    assert encode_legacy_control(controls, "cw_pitch", 1200) == 255
    # Radioddity Extended manual for Xiegu X6100 v1.1.8 section 10.3.4
    # documents a 10 Hz step, and the profile declares decode_quantum 10,
    # so every documented 10 Hz value round-trips exactly.
    for h in range(400, 1201, 10):
        assert (
            decode_legacy_control(
                controls, "cw_pitch", encode_legacy_control(controls, "cw_pitch", h)
            )
            == h
        ), f"x6100 cw_pitch {h} Hz"


def test_x6100_key_speed_encode_uses_the_declared_5_50_domain() -> None:
    controls = _rig_domains("x6100")
    assert encode_legacy_control(controls, "key_speed", 5) == 0
    assert encode_legacy_control(controls, "key_speed", 50) == 255
    for w in range(5, 51):
        assert (
            decode_legacy_control(
                controls, "key_speed", encode_legacy_control(controls, "key_speed", w)
            )
            == w
        ), f"x6100 key_speed {w} WPM"


def test_legacy_encode_rejects_out_of_band_and_non_integer_display() -> None:
    controls = {
        "cw_pitch": {
            "raw_min": 0,
            "raw_max": 255,
            "display_min": 300,
            "display_max": 900,
            "encode_rounding": "ceil",
        }
    }
    with pytest.raises(ValueError, match="300-900"):
        encode_legacy_control(controls, "cw_pitch", 299)
    with pytest.raises(ValueError, match="300-900"):
        encode_legacy_control(controls, "cw_pitch", 901)
    with pytest.raises(ValueError, match="cw_pitch"):
        encode_legacy_control(controls, "cw_pitch", 600.5)
    with pytest.raises(ValueError, match="cw_pitch"):
        encode_legacy_control(controls, "cw_pitch", True)
