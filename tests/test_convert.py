"""Unit tests for the pure Hamlib dump_caps → draft TOML converter (MOR-203)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from rigplane.backends.hamlib_models import HamlibCaps, parse_hamlib_dump_caps
from rigplane.cli._convert import (
    CrossCheckReport,
    build_draft_toml,
    caps_to_capabilities,
    cross_check,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "hamlib_dump_caps_ts480.txt"


def _ts480_caps() -> HamlibCaps:
    return parse_hamlib_dump_caps(_FIXTURE.read_text())


# ---------------------------------------------------------------------------
# build_draft_toml
# ---------------------------------------------------------------------------


def test_build_draft_toml_parses_and_has_required_sections() -> None:
    caps = _ts480_caps()
    # model_id is not set by parse_hamlib_dump_caps; inject it like load_hamlib_caps.
    import dataclasses

    caps = dataclasses.replace(caps, model_id=2028)

    text = build_draft_toml(caps, model="TS-480", profile_id="ts480")

    # (#2) Generated draft is valid TOML.
    data = tomllib.loads(text)

    # Required sections present (rig_loader._REQUIRED_SECTIONS).
    for section in ("radio", "capabilities", "modes", "filters", "vfo"):
        assert section in data, f"missing section {section!r}"

    # Required radio fields (rig_loader._REQUIRED_RADIO_FIELDS).
    radio = data["radio"]
    for f in ("id", "model", "receiver_count", "has_lan", "has_wifi"):
        assert f in radio, f"missing [radio].{f}"

    assert radio["model"] == "TS-480"
    assert radio["id"] == "ts_480"
    assert radio["hamlib_model_id"] == 2028

    # Features mapped from tokens.
    features = set(data["capabilities"]["features"])
    assert {
        "nb",
        "nr",
        "rf_gain",
        "af_level",
        "attenuator",
        "preamp",
        "squelch",
        "meters",
    } <= features

    # Modes normalized: CWR -> CW-R, original token gone.
    modes = data["modes"]["list"]
    assert "CW-R" in modes
    assert "CWR" not in modes
    assert "RTTY-R" in modes
    assert "RTTYR" not in modes

    # Review banner + TODO markers present in raw text.
    assert "# REVIEW:" in text
    assert "TODO(human)" in text

    # hamlib_model_id line present when model_id set.
    assert "hamlib_model_id = 2028" in text


def test_build_draft_toml_omits_hamlib_model_id_when_none() -> None:
    caps = _ts480_caps()  # model_id is None from parse.
    text = build_draft_toml(caps, model="TS-480", profile_id="ts480")
    assert "hamlib_model_id" not in text
    tomllib.loads(text)


# ---------------------------------------------------------------------------
# caps_to_capabilities / cross_check
# ---------------------------------------------------------------------------


def test_caps_to_capabilities_maps_tokens() -> None:
    caps = HamlibCaps(
        get_levels=frozenset({"RF", "AF"}),
        get_funcs=frozenset({"NB"}),
    )
    assert caps_to_capabilities(caps) == frozenset({"rf_gain", "af_level", "nb"})


def test_caps_to_capabilities_maps_ptt_to_tx() -> None:
    caps = HamlibCaps(ptt_type="RIG")
    assert caps_to_capabilities(caps) == frozenset({"tx"})


def test_cross_check_buckets() -> None:
    caps = HamlibCaps(
        get_levels=frozenset({"RF", "AF"}),
        get_funcs=frozenset({"NB"}),
    )
    profile_caps = frozenset({"rf_gain", "agc", "rit"})
    report = cross_check(caps, profile_caps, profile_id="ts480")

    assert isinstance(report, CrossCheckReport)
    assert report.profile_id == "ts480"

    assert report.agreed == ("rf_gain",)
    # Declared by profile, no Hamlib token.
    assert "agc" in report.rigplane_only
    assert "rit" in report.rigplane_only
    # Token present, profile omits.
    assert "af_level" in report.hamlib_only
    assert "nb" in report.hamlib_only

    # All tuple fields sorted.
    assert list(report.rigplane_only) == sorted(report.rigplane_only)
    assert list(report.hamlib_only) == sorted(report.hamlib_only)
    assert list(report.agreed) == sorted(report.agreed)

    # Mode buckets empty in v1.
    assert report.mode_only_profile == ()
    assert report.mode_only_hamlib == ()


def test_cross_check_report_to_dict_and_table() -> None:
    caps = HamlibCaps(
        get_levels=frozenset({"RF", "AF"}),
        get_funcs=frozenset({"NB"}),
    )
    report = cross_check(caps, frozenset({"rf_gain", "agc"}), profile_id="ts480")

    d = report.to_dict()
    for key in (
        "agreed",
        "rigplane_only",
        "hamlib_only",
        "mode_only_profile",
        "mode_only_hamlib",
        "profile_id",
    ):
        assert key in d
    # List values (not tuples) for the bucket fields.
    for key in (
        "agreed",
        "rigplane_only",
        "hamlib_only",
        "mode_only_profile",
        "mode_only_hamlib",
    ):
        assert isinstance(d[key], list)

    table = report.human_table()
    assert isinstance(table, str)
    assert table
    assert "agreed" in table
    assert "rigplane_only" in table
    assert "hamlib_only" in table


# ---------------------------------------------------------------------------
# Degraded caps
# ---------------------------------------------------------------------------


def test_build_draft_toml_degraded_caps() -> None:
    caps = HamlibCaps(degraded_reason="x")
    text = build_draft_toml(caps, model="Mystery Rig", profile_id="mystery")

    data = tomllib.loads(text)
    for section in ("radio", "capabilities", "modes", "filters", "vfo"):
        assert section in data

    # Empty features, mode fallback to ["USB"].
    assert data["capabilities"]["features"] == []
    assert data["modes"]["list"] == ["USB"]


def test_cross_check_degraded_caps() -> None:
    caps = HamlibCaps(degraded_reason="x")
    report = cross_check(caps, frozenset({"rf_gain"}))
    assert report.rigplane_only == ("rf_gain",)
    assert report.agreed == ()
    assert report.hamlib_only == ()
