"""IC-7300 TOML tests — verify ic7300.toml loads correctly and overrides are accurate.

TDD: these tests were written FIRST, then the TOML was created to pass them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.commands._codec import filter_hz_to_index, filter_index_to_hz
from rigplane.meter_cal import interpolate_meter
from rigplane.rig_loader import load_rig

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"
IC7300_PATH = RIGS_DIR / "ic7300.toml"


@pytest.fixture()
def rig():
    return load_rig(IC7300_PATH)


@pytest.fixture()
def profile(rig):
    return rig.to_profile()


@pytest.fixture()
def cmdmap(rig):
    return rig.to_command_map()


# ── Profile basics ─────────────────────────────────────────────


class TestProfileBasics:
    """ic7300.toml profile must have correct metadata."""

    def test_loads_without_error(self, rig):
        assert rig is not None

    def test_profile_id(self, profile):
        assert profile.id == "icom_ic7300"

    def test_model(self, profile):
        assert profile.model == "IC-7300"

    def test_civ_addr(self, profile):
        assert profile.civ_addr == 0x94

    def test_receiver_count(self, profile):
        assert profile.receiver_count == 1

    def test_no_cmd29_routes(self, profile):
        assert len(profile.cmd29_routes) == 0

    def test_modes(self, profile):
        assert profile.modes == (
            "USB",
            "LSB",
            "CW",
            "CW-R",
            "AM",
            "FM",
            "RTTY",
            "RTTY-R",
        )

    def test_filters(self, profile):
        assert profile.filters == ("FIL1", "FIL2", "FIL3")

    def test_filter_encoding_is_segmented(self, profile):
        assert profile.filter_width_encoding == "segmented_bcd_index"


# ── Filter width segments ─────────────────────────────────────


class TestFilterWidthSegments:
    """IC-7300 filter width uses index table (CI-V Reference p.19)."""

    def test_ssb_index_0_is_50hz(self, profile):
        rule = profile.resolve_filter_rule("USB")
        assert filter_index_to_hz(0, segments=rule.segments) == 50

    def test_ssb_index_9_is_500hz(self, profile):
        rule = profile.resolve_filter_rule("USB")
        assert filter_index_to_hz(9, segments=rule.segments) == 500

    def test_ssb_index_10_is_600hz(self, profile):
        rule = profile.resolve_filter_rule("USB")
        assert filter_index_to_hz(10, segments=rule.segments) == 600

    def test_ssb_index_40_is_3600hz(self, profile):
        rule = profile.resolve_filter_rule("USB")
        assert filter_index_to_hz(40, segments=rule.segments) == 3600

    def test_ssb_roundtrip(self, profile):
        rule = profile.resolve_filter_rule("LSB")
        assert filter_hz_to_index(1500, segments=rule.segments) == 19
        assert filter_index_to_hz(19, segments=rule.segments) == 1500

    def test_cw_same_as_ssb(self, profile):
        rule = profile.resolve_filter_rule("CW")
        assert filter_index_to_hz(40, segments=rule.segments) == 3600

    def test_cw_r_falls_back_to_cw(self, profile):
        rule = profile.resolve_filter_rule("CW-R")
        assert rule is not None
        assert filter_index_to_hz(0, segments=rule.segments) == 50

    def test_rtty_index_31_is_2700hz(self, profile):
        rule = profile.resolve_filter_rule("RTTY")
        assert filter_index_to_hz(31, segments=rule.segments) == 2700

    def test_rtty_max_is_2700_not_3600(self, profile):
        rule = profile.resolve_filter_rule("RTTY")
        with pytest.raises(ValueError):
            filter_index_to_hz(32, segments=rule.segments)

    def test_am_index_0_is_200hz(self, profile):
        rule = profile.resolve_filter_rule("AM")
        assert filter_index_to_hz(0, segments=rule.segments) == 200

    def test_am_index_49_is_10000hz(self, profile):
        rule = profile.resolve_filter_rule("AM")
        assert filter_index_to_hz(49, segments=rule.segments) == 10000

    def test_fm_is_fixed(self, profile):
        rule = profile.resolve_filter_rule("FM")
        assert rule is not None
        assert rule.fixed is True


# ── VFO scheme ─────────────────────────────────────────────────


class TestVFOScheme:
    """IC-7300 uses A/B VFO scheme, not main/sub."""

    def test_vfo_main_code_is_a(self, profile):
        # In ab scheme, "main" maps to VFO-A
        assert profile.vfo_main_code == 0x00

    def test_vfo_sub_code_is_b(self, profile):
        # In ab scheme, "sub" maps to VFO-B
        assert profile.vfo_sub_code == 0x01

    def test_vfo_swap_code(self, profile):
        assert profile.vfo_swap_code == 0xB0


# ── Capabilities ───────────────────────────────────────────────


class TestCapabilities:
    """IC-7300 capabilities: no dual_rx, digisel; includes ip_plus per wfview."""

    def test_has_audio(self, profile):
        assert "audio" in profile.capabilities

    def test_has_scope(self, profile):
        assert "scope" in profile.capabilities

    def test_has_meters(self, profile):
        assert "meters" in profile.capabilities

    def test_has_tx(self, profile):
        assert "tx" in profile.capabilities

    def test_has_nb(self, profile):
        assert "nb" in profile.capabilities

    def test_has_nr(self, profile):
        assert "nr" in profile.capabilities

    def test_has_attenuator(self, profile):
        assert "attenuator" in profile.capabilities

    def test_has_preamp(self, profile):
        assert "preamp" in profile.capabilities

    def test_no_dual_rx(self, profile):
        assert "dual_rx" not in profile.capabilities

    def test_no_digisel(self, profile):
        assert "digisel" not in profile.capabilities

    def test_has_ip_plus(self, profile):
        assert "ip_plus" in profile.capabilities

    def test_capabilities_count(self, profile):
        assert len(profile.capabilities) == 41


# ── Command overrides ──────────────────────────────────────────


class TestCommandOverrides:
    """IC-7300-specific wire bytes (base [commands] + merged [commands.overrides])."""

    def test_get_acc1_mod_level(self, cmdmap):
        assert cmdmap.get("get_acc1_mod_level") == (0x1A, 0x05, 0x00, 0x64)

    def test_get_usb_mod_level(self, cmdmap):
        assert cmdmap.get("get_usb_mod_level") == (0x1A, 0x05, 0x00, 0x65)

    def test_get_data_off_mod_input(self, cmdmap):
        assert cmdmap.get("get_data_off_mod_input") == (0x1A, 0x05, 0x00, 0x66)

    def test_get_data1_mod_input(self, cmdmap):
        assert cmdmap.get("get_data1_mod_input") == (0x1A, 0x05, 0x00, 0x67)

    def test_get_civ_transceive(self, cmdmap):
        assert cmdmap.get("get_civ_transceive") == (0x1A, 0x05, 0x00, 0x71)

    def test_get_system_date(self, cmdmap):
        assert cmdmap.get("get_system_date") == (0x1A, 0x05, 0x00, 0x94)

    def test_get_system_time(self, cmdmap):
        assert cmdmap.get("get_system_time") == (0x1A, 0x05, 0x00, 0x95)

    def test_get_utc_offset(self, cmdmap):
        assert cmdmap.get("get_utc_offset") == (0x1A, 0x05, 0x00, 0x96)

    def test_get_quick_split(self, cmdmap):
        assert cmdmap.get("get_quick_split") == (0x1A, 0x05, 0x00, 0x30)

    def test_get_nb_depth(self, cmdmap):
        assert cmdmap.get("get_nb_depth") == (0x1A, 0x05, 0x01, 0x89)

    def test_get_nb_width(self, cmdmap):
        assert cmdmap.get("get_nb_width") == (0x1A, 0x05, 0x01, 0x90)

    def test_get_s_meter_sql_status(self, cmdmap):
        assert cmdmap.get("get_s_meter_sql_status") == (0x15, 0x01)

    def test_get_s_meter_sql_status_04(self, cmdmap):
        assert cmdmap.get("get_s_meter_sql_status_04") == (0x15, 0x04)

    def test_get_split_opcode(self, cmdmap):
        assert cmdmap.get("get_split") == (0x0F,)

    def test_get_ip_plus(self, cmdmap):
        assert cmdmap.get("get_ip_plus") == (0x16, 0x65)

    def test_set_speech_not_get_speech(self, cmdmap):
        assert cmdmap.get("set_speech") == (0x13,)
        assert not cmdmap.has("get_speech")

    def test_get_scope_wave(self, cmdmap):
        assert cmdmap.get("get_scope_wave") == (0x27, 0x00)

    def test_get_speech_cmd_map_uses_set_speech(self, cmdmap):
        """Profile exposes set_speech; get_speech() must resolve the same opcode."""
        from rigplane.commands import get_speech

        with_map = get_speech(2, to_addr=0x94, cmd_map=cmdmap)
        bare = get_speech(2, to_addr=0x94)
        assert with_map == bare

    def test_scope_edge3_6mhz_is_0x20_not_sequential(self, cmdmap):
        """wfview uses 0x18, 0x19, 0x20 for 6 MHz edges (skip 0x1A-0x1F)."""
        assert cmdmap.get("get_scope_edge3_6mhz") == (0x1A, 0x05, 0x01, 0x20)

    def test_get_civ_output_ant(self, cmdmap):
        assert cmdmap.get("get_civ_output_ant") == (0x1A, 0x05, 0x00, 0x61)

    def test_agc_time_constant(self, cmdmap):
        assert cmdmap.get("get_agc_time_constant") == (0x1A, 0x04)


# ── Shared commands (same wire bytes as IC-7610) ───────────────


class TestSharedCommands:
    """Commands not overridden must match IC-7610 wire bytes."""

    def test_get_freq(self, cmdmap):
        assert cmdmap.get("get_freq") == (0x03,)

    def test_get_s_meter(self, cmdmap):
        assert cmdmap.get("get_s_meter") == (0x15, 0x02)

    def test_get_mode(self, cmdmap):
        assert cmdmap.get("get_mode") == (0x04,)

    def test_ptt_on(self, cmdmap):
        assert cmdmap.get("ptt_on") == (0x1C, 0x00)

    def test_scope_on(self, cmdmap):
        assert cmdmap.get("scope_on") == (0x27, 0x10)


# ── Removed commands (not available on IC-7300) ────────────────


class TestRemovedCommands:
    """Commands that should NOT be in the IC-7300 command map."""

    def test_no_digisel(self, cmdmap):
        assert not cmdmap.has("get_digisel")
        assert not cmdmap.has("set_digisel")

    def test_no_digisel_shift(self, cmdmap):
        assert not cmdmap.has("get_digisel_shift")
        assert not cmdmap.has("set_digisel_shift")

    def test_no_drive_gain(self, cmdmap):
        assert not cmdmap.has("get_drive_gain")
        assert not cmdmap.has("set_drive_gain")

    def test_no_scope_main_sub(self, cmdmap):
        assert not cmdmap.has("get_scope_main_sub")


# ── Spectrum params ────────────────────────────────────────────


class TestSpectrumParams:
    """IC-7300 spectrum parameters (same as IC-7610 for these values)."""

    def test_seq_max(self, rig):
        assert rig.spectrum["seq_max"] == 11

    def test_amp_max(self, rig):
        assert rig.spectrum["amp_max"] == 160

    def test_data_len_max(self, rig):
        assert rig.spectrum["data_len_max"] == 475


# ── S-meter calibration (MOR-1451) ─────────────────────────────
#
# Before this table existed, the frontend fell back to a hardcoded IC-7610
# curve (S9 at raw 130) for every radio lacking its own — including the
# IC-7300, whose real S9 anchor is raw 120. Live evidence: main.sMeter raw
# 53 rendered as "S9+40" regardless of the actual signal. These pin the
# TOML data itself; the frontend conformance case (raw 53 -> S4, not
# S9+40) lives in `frontend/src/components-v2/meters/__tests__/
# LinearSMeter.test.ts` and `meter-utils.test.ts`.


class TestSMeterCalibration:
    """IC-7300 CI-V S-meter scale: 0=S0, 120=S9, 241=S9+60 — distinct from
    the IC-7610 curve (S9 at raw 130)."""

    def test_has_s_meter_calibration_block(self, rig):
        assert rig.meter_calibrations is not None
        assert "s_meter" in rig.meter_calibrations

    def test_anchor_count(self, rig):
        assert len(rig.meter_calibrations["s_meter"]) == 3

    def test_redline_raw_is_s9(self, rig):
        assert rig.meter_redlines["s_meter"] == 120

    @pytest.mark.parametrize(
        "raw,expected_actual",
        [(0, -54.0), (120, 0.0), (241, 60.0)],
    )
    def test_anchor_round_trip(self, rig, raw, expected_actual):
        """Interpolating at a documented anchor returns that anchor's dB-rel-S9."""
        actual, calibrated = interpolate_meter(raw, rig.meter_calibrations, "s_meter")
        assert calibrated is True
        assert actual == pytest.approx(expected_actual)

    def test_live_evidence_raw_53_publishes_minus_30_dbm_rel_s9(self, rig):
        """Backend-side pin for the exact MOR-1451 live-evidence value.

        `runtime/_civ_rx.py`'s `_calibrated_meter_value` calls exactly this
        function (`interpolate_meter`) over `profile.meter_calibrations`
        before ``ServerState.main.sMeter`` is ever published — the raw wire
        byte never reaches the frontend for a radio with a calibration
        table (see `test_civ_rx_coverage.py`'s pre-existing "raw 111 -> -8"
        pin for a worked example on a different profile). With this
        profile's table, raw 53 -> -30 dB-rel-S9, which the frontend then
        renders as S4 (`LinearSMeter.test.ts` / `meter-utils.test.ts`) —
        not the reported "S9+40".
        """
        actual, calibrated = interpolate_meter(53, rig.meter_calibrations, "s_meter")
        assert calibrated is True
        assert actual == pytest.approx(-30.15)
        # `_calibrated_meter_value` (runtime/_civ_rx.py) rounds s_meter to an
        # int before publishing — this IS the value ServerState.main.sMeter
        # (and therefore LinearSMeter's `value` prop) actually carries.
        assert int(round(actual)) == -30

    def test_live_evidence_raw_53_is_not_the_ic7610_curve(self, rig):
        """Raw 53 must NOT interpolate to the IC-7610 curve's answer (~S3,
        actual -36 dB-rel-S9 at its raw-52 anchor) — the two rigs do not
        share an S-meter scale."""
        actual, calibrated = interpolate_meter(53, rig.meter_calibrations, "s_meter")
        assert calibrated is True
        assert actual != pytest.approx(-36.0)


# ── PA/TX telemetry meter calibration (MOR-1527) ────────────────
#
# Before this table existed, power/ALC/COMP/Vd/Id had no
# [[meters.<key>.calibration]] block on this profile, so
# `_calibrated_meter_value` published the raw CI-V byte flagged
# uncalibrated for every one of them — mirrors the pre-MOR-1451 s_meter
# gap above. Live evidence (the finding that opened this ticket): the Vd
# tile read raw "158" with no unit, bench PSU actually reading ~13.8 V.
#
# These tables are hamlib-sourced (rigs/icom/ic7300.c; full citation and
# per-meter provenance honesty notes live as a comment in rigs/ic7300.toml
# itself, right above the tables these tests read). The Vd anchor set is
# additionally live-corroborated: interpolating at raw 158 reproduces the
# operator's own bench reading. Table-driven over the TOML data itself —
# every case reads through `rig.meter_calibrations`, so a profile edit is
# what the parametrization exercises, not a hand-duplicated literal.

_PA_METER_ANCHORS = [
    # (meter_key, raw, expected_actual) — each is a real anchor point from
    # rigs/ic7300.toml's [[meters.<key>.calibration]] tables.
    ("power", 0, 0.0),
    ("power", 21, 5.0),
    ("power", 143, 50.0),
    ("power", 213, 100.0),
    ("power", 255, 120.0),
    ("alc", 0, 0.0),
    ("alc", 120, 100.0),
    ("comp", 0, 0.0),
    ("comp", 130, 15.0),
    ("comp", 241, 30.0),
    ("vd", 0, 0.0),
    ("vd", 13, 10.0),
    ("vd", 241, 16.0),
    ("id", 0, 0.0),
    ("id", 97, 10.0),
    ("id", 146, 15.0),
    ("id", 241, 25.0),
]


class TestPaMeterCalibration:
    """IC-7300 power/ALC/COMP/Vd/Id calibration tables (MOR-1527)."""

    @pytest.mark.parametrize("meter_key", ["power", "alc", "comp", "vd", "id"])
    def test_declares_calibration_table(self, rig, meter_key):
        assert rig.meter_calibrations is not None
        assert meter_key in rig.meter_calibrations
        assert len(rig.meter_calibrations[meter_key]) >= 2

    @pytest.mark.parametrize("meter_key,raw,expected_actual", _PA_METER_ANCHORS)
    def test_anchor_round_trip(self, rig, meter_key, raw, expected_actual):
        """Interpolating at a documented hamlib anchor returns that
        anchor's engineering value. ``_PA_METER_ANCHORS`` hand-transcribes
        each anchor from ``rigs/ic7300.toml`` (by design, as a regression
        pin -- not read dynamically from the TOML), but the interpolation
        itself runs through the real ``rig.meter_calibrations`` parsed from
        that same file, so a profile edit that drifts from these literals
        is what this parametrization catches."""
        actual, calibrated = interpolate_meter(raw, rig.meter_calibrations, meter_key)
        assert calibrated is True
        assert actual == pytest.approx(expected_actual)

    def test_live_evidence_vd_raw_158_publishes_approximately_13_8_volts(self, rig):
        """MOR-1527's owner-reported live finding: the Vd tile showed raw
        158 while the bench PSU actually read ~13.8 V. This is the
        red-first pin for this ticket: before ``rigs/ic7300.toml`` declared
        ``[meters.vd]``, ``interpolate_meter`` returned ``(158.0, False)``
        here — no table, so ``calibrated`` was False and ``actual`` was the
        untouched raw byte, 158.0. This assertion only passes once the
        profile's ``[meters.vd]`` table exists (verified failing on `main`
        prior to this PR's TOML change; see the PR body for the captured
        red-first run).
        """
        actual, calibrated = interpolate_meter(158, rig.meter_calibrations, "vd")
        assert calibrated is True
        assert actual == pytest.approx(13.8, abs=0.05)

    def test_alc_actual_is_percent_not_normalized_fraction(self, rig):
        """The TOML stores ALC ``actual`` in the repo's 0-100 percent
        convention (matching ``rigs/ic7610.toml``'s own ``[meters.alc]``
        table), not hamlib's native 0.0-1.0 fraction — ``_civ_rx.py``'s
        ``_calibrated_meter_value`` divides by 100 after this lookup, so a
        100.0-scaled table here is what makes that division land in the
        frontend's expected 0-1 domain."""
        actual, calibrated = interpolate_meter(120, rig.meter_calibrations, "alc")
        assert calibrated is True
        assert actual == pytest.approx(100.0)
