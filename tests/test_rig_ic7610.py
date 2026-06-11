"""IC-7610 TOML profile tests — verify TOML produces correct RadioProfile.

All profile data comes from TOML; there are no hardcoded constants to
compare against.  Tests verify expected values directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.rig_loader import load_rig

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"
IC7610_PATH = RIGS_DIR / "ic7610.toml"


@pytest.fixture()
def rig():
    return load_rig(IC7610_PATH)


@pytest.fixture()
def profile(rig):
    return rig.to_profile()


@pytest.fixture()
def cmdmap(rig):
    return rig.to_command_map()


# ── Profile parity ──────────────────────────────────────────────


class TestProfileParity:
    """ic7610.toml profile must match hardcoded RadioProfile exactly."""

    def test_loads_without_error(self, rig):
        assert rig is not None

    def test_profile_id(self, profile):
        assert profile.id == "icom_ic7610"

    def test_model(self, profile):
        assert profile.model == "IC-7610"

    def test_civ_addr(self, profile):
        assert profile.civ_addr == 0x98

    def test_receiver_count(self, profile):
        assert profile.receiver_count == 2

    def test_capabilities_exact(self, profile):
        expected = frozenset(
            {
                # Receiver
                "audio",
                "dual_rx",
                "dual_watch",
                "lan_dual_rx_audio_routing",
                "af_level",
                "rf_gain",
                "squelch",
                # RF front end
                "attenuator",
                "preamp",
                "digisel",
                "ip_plus",
                # Antenna
                "antenna",
                "rx_antenna",
                # DSP / Noise
                "nb",
                "nr",
                "notch",
                "apf",
                "twin_peak",
                # Filter
                "pbt",
                "filter_width",
                "filter_shape",
                # TX
                "tx",
                "split",
                "vox",
                "compressor",
                "monitor",
                "drive_gain",
                "ssb_tx_bw",
                # CW
                "cw",
                "break_in",
                # RIT / XIT
                "rit",
                "xit",
                # Tuner
                "tuner",
                # Metering / Scope
                "meters",
                "scope",
                # Tone: the IC-7610 (HF/6m) has no FM-repeater CTCSS tone
                # feature, so repeater_tone / tsql are intentionally absent
                # (MOR-661).
                # Data / System
                "data_mode",
                # MOR-678: USB/LAN/DATA MOD-input routing source-select guard.
                "mod_input_routing",
                "power_control",
                "dial_lock",
                "scan",
                "bsr",
                "main_sub_tracking",
                # AGC
                "agc",
                "tuning_step",
                "band_edge",
                "xfc",
                # System
                "system_settings",
            }
        )
        assert profile.capabilities == expected

    def test_capabilities_count(self, profile):
        # MOR-661: dropped repeater_tone + tsql (48 → 46).
        # MOR-678: added mod_input_routing (46 → 47).
        assert len(profile.capabilities) == 47

    def test_cmd29_routes_exact(self, profile):
        expected = frozenset(
            {
                (0x11, None),
                (0x12, None),
                (0x14, 0x01),
                (0x14, 0x02),
                (0x14, 0x03),
                (0x14, 0x05),
                (0x14, 0x06),
                (0x14, 0x07),
                (0x14, 0x08),
                (0x14, 0x0D),
                (0x14, 0x12),
                (0x14, 0x13),
                (0x15, 0x01),
                (0x15, 0x02),
                (0x15, 0x05),
                (0x16, 0x02),
                (0x16, 0x12),
                (0x16, 0x22),
                (0x16, 0x32),
                (0x16, 0x40),
                (0x16, 0x41),
                (0x16, 0x42),
                (0x16, 0x43),
                (0x16, 0x48),
                (0x16, 0x4E),
                (0x16, 0x4F),
                (0x16, 0x53),
                (0x16, 0x56),
                (0x16, 0x65),
                (0x1A, 0x03),
                (0x1A, 0x04),
                (0x1A, 0x09),
                (0x1B, 0x00),
                (0x1B, 0x01),
            }
        )
        assert profile.cmd29_routes == expected

    def test_cmd29_routes_count(self, profile):
        assert len(profile.cmd29_routes) == 34

    def test_vfo_main_code(self, profile):
        assert profile.vfo_main_code == 0xD0

    def test_vfo_sub_code(self, profile):
        assert profile.vfo_sub_code == 0xD1

    def test_vfo_swap_code(self, profile):
        assert profile.vfo_swap_code == 0xB0

    def test_freq_ranges_count(self, profile):
        assert len(profile.freq_ranges) == 2

    def test_freq_range_hf(self, profile):
        hf = profile.freq_ranges[0]
        assert hf.start == 30_000
        assert hf.end == 60_000_000
        assert hf.label == "HF"

    def test_freq_range_6m(self, profile):
        sixm = profile.freq_ranges[1]
        assert sixm.start == 50_000_000
        assert sixm.end == 54_000_000
        assert sixm.label == "6m"

    def test_hf_bands_count(self, profile):
        hf = profile.freq_ranges[0]
        assert len(hf.bands) == 10

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
            "PSK",
            "PSK-R",
        )

    def test_filters(self, profile):
        assert profile.filters == ("FIL1", "FIL2", "FIL3")

    def test_spectrum_matches_wfview_rig(self, rig):
        assert rig.spectrum == {
            "seq_max": 15,
            "amp_max": 200,
            "data_len_max": 689,
        }

    def test_keyboard_config(self, profile):
        assert profile.keyboard is not None
        assert profile.keyboard.leader_key == "g"
        assert profile.keyboard.leader_timeout_ms == 1000
        assert profile.keyboard.alt_hints is True
        assert any(
            binding.action == "toggle_help" for binding in profile.keyboard.bindings
        )


# ── CommandMap parity ───────────────────────────────────────────


class TestCommandMapParity:
    """ic7610.toml commands must have correct wire bytes."""

    def test_get_freq(self, cmdmap):
        assert cmdmap.get("get_freq") == (0x03,)

    def test_set_freq(self, cmdmap):
        assert cmdmap.get("set_freq") == (0x05,)

    def test_get_af_level(self, cmdmap):
        assert cmdmap.get("get_af_level") == (0x14, 0x01)

    def test_get_s_meter(self, cmdmap):
        assert cmdmap.get("get_s_meter") == (0x15, 0x02)

    def test_get_power_meter(self, cmdmap):
        assert cmdmap.get("get_power_meter") == (0x15, 0x11)

    def test_get_swr(self, cmdmap):
        assert cmdmap.get("get_swr") == (0x15, 0x12)

    def test_ptt_on(self, cmdmap):
        assert cmdmap.get("ptt_on") == (0x1C, 0x00)

    def test_scope_on(self, cmdmap):
        assert cmdmap.get("scope_on") == (0x27, 0x10)

    def test_get_split(self, cmdmap):
        assert cmdmap.get("get_split") == (0x0F,)

    def test_get_scope_wave(self, cmdmap):
        assert cmdmap.get("get_scope_wave") == (0x27, 0x00)
        assert cmdmap.get("set_scope_wave") == (0x27, 0x00)

    def test_main_sub_prefix(self, cmdmap):
        assert cmdmap.get("get_main_sub_prefix") == (0x29,)
        assert cmdmap.get("set_main_sub_prefix") == (0x29,)

    def test_get_civ_output_ant_wfview_1c04(self, cmdmap):
        assert cmdmap.get("get_civ_output_ant") == (0x1C, 0x04)
        assert cmdmap.get("set_civ_output_ant") == (0x1C, 0x04)

    def test_send_cw(self, cmdmap):
        assert cmdmap.get("send_cw") == (0x17,)

    def test_command_count_minimum(self, cmdmap):
        assert len(cmdmap) >= 95


# ── cmd29 route detail checks ──────────────────────────────────


class TestCmd29Detail:
    """Verify specific cmd29 route entries."""

    def test_att_cmd_only(self, profile):
        assert (0x11, None) in profile.cmd29_routes

    def test_af_gain(self, profile):
        assert (0x14, 0x01) in profile.cmd29_routes

    def test_rf_gain(self, profile):
        assert (0x14, 0x02) in profile.cmd29_routes

    def test_preamp(self, profile):
        assert (0x16, 0x02) in profile.cmd29_routes

    def test_ip_plus(self, profile):
        assert (0x16, 0x65) in profile.cmd29_routes

    def test_agc_time_constant(self, profile):
        assert (0x1A, 0x04) in profile.cmd29_routes

    def test_af_mute(self, profile):
        assert (0x1A, 0x09) in profile.cmd29_routes


# ── Meter calibration tables ─────────────────────────────────────


class TestMeterCalibrations:
    """Verify meter calibration tables parsed from ic7610.toml."""

    def test_all_meter_keys_present(self, rig):
        mc = rig.meter_calibrations
        assert mc is not None
        assert set(mc.keys()) == {"s_meter", "power", "swr", "alc"}

    def test_s_meter_calibration_count(self, rig):
        assert len(rig.meter_calibrations["s_meter"]) == 9

    def test_power_calibration_count(self, rig):
        assert len(rig.meter_calibrations["power"]) == 3

    def test_swr_calibration_count(self, rig):
        assert len(rig.meter_calibrations["swr"]) == 5

    def test_alc_calibration_count(self, rig):
        assert len(rig.meter_calibrations["alc"]) == 2

    def test_power_redline(self, rig):
        assert rig.meter_redlines["power"] == 212

    def test_swr_redline(self, rig):
        assert rig.meter_redlines["swr"] == 120

    def test_alc_redline(self, rig):
        assert rig.meter_redlines["alc"] == 120

    def test_s_meter_redline(self, rig):
        assert rig.meter_redlines["s_meter"] == 130

    def test_power_endpoints(self, rig):
        pts = rig.meter_calibrations["power"]
        assert pts[0]["raw"] == 0 and pts[0]["actual"] == 0.0
        assert pts[-1]["raw"] == 212 and pts[-1]["actual"] == 100.0

    def test_swr_endpoints(self, rig):
        pts = rig.meter_calibrations["swr"]
        assert pts[0]["raw"] == 0 and pts[0]["actual"] == 1.0
        # 5th point added in P3-01 (issue #1173) — wfview IC-7610.rig.
        assert pts[-1]["raw"] == 255 and pts[-1]["actual"] == 6.0

    def test_alc_endpoints(self, rig):
        pts = rig.meter_calibrations["alc"]
        assert pts[0]["raw"] == 0 and pts[0]["actual"] == 0.0
        assert pts[-1]["raw"] == 120 and pts[-1]["actual"] == 100.0


class TestControlRanges:
    """[controls.*] raw/display ranges (MOR-490)."""

    def test_nr_level_wire_range_is_full_bcd(self, rig):
        # The NR-level CI-V wire value is 0-255 BCD on the IC-7610, like
        # every other rig.  raw_max must be 255, not the front-panel 0-15.
        nr = rig.controls["nr_level"]
        assert nr["raw_min"] == 0
        assert nr["raw_max"] == 255

    def test_nr_level_display_range_matches_front_panel(self, rig):
        # The front panel shows NR as 0-15; record that display mapping so
        # the web slider can convert wire 0-255 <-> display 0-15.
        nr = rig.controls["nr_level"]
        assert nr["display_min"] == 0
        assert nr["display_max"] == 15

    def test_nb_depth_wire_range_is_zero_based(self, rig):
        # The NB-depth CI-V wire value is 0-9 on the IC-7610; the front
        # panel shows it 1-based (1-10).  raw range stays 0-9.
        nb = rig.controls["nb_depth"]
        assert nb["raw_min"] == 0
        assert nb["raw_max"] == 9

    def test_nb_depth_display_range_matches_front_panel(self, rig):
        # The front panel shows NB depth as 1-10; record that display
        # mapping so the web slider can convert wire 0-9 <-> display 1-10.
        nb = rig.controls["nb_depth"]
        assert nb["display_min"] == 1
        assert nb["display_max"] == 10
