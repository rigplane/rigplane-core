"""IC-705 TOML profile tests — pin what ``rigs/ic705.toml`` must (not) declare."""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.meter_cal import interpolate_meter
from rigplane.rig_loader import load_rig

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"
IC705_PATH = RIGS_DIR / "ic705.toml"


@pytest.fixture()
def rig():
    return load_rig(IC705_PATH)


@pytest.fixture()
def cmdmap(rig):
    return rig.to_command_map()


class TestNoStrayCivOutputKeys:
    """``get_civ_output``/``set_civ_output`` (bare spelling) are dead keys.

    No builder in ``src/rigplane/commands/`` resolves this spelling; the
    only reachable builder for 0x1C 0x04 is ``get_civ_output_ant`` /
    ``set_civ_output_ant`` (``src/rigplane/commands/config.py``). Per the
    IC-705 CI-V Reference Guide (A7560-8EX-1, Jul.2020) p.8, IC-705 has no
    ANT-output CI-V setting at all, so this pins the bare keys' absence
    rather than asserting anything about the ``_ant`` spelling.
    """

    def test_bare_civ_output_keys_absent(self, cmdmap) -> None:
        assert not cmdmap.has("get_civ_output")
        assert not cmdmap.has("set_civ_output")

    def test_neighboring_key_still_present(self, cmdmap) -> None:
        """Discrimination guard: the map is loaded and non-empty."""
        assert cmdmap.has("get_xfc_status")
        assert not cmdmap.has("get_dual_watch")


# ── Meter scales from the IC-705 CI-V Reference Guide (MOR-2916) ────────
#
# Each anchor is a printed row of the guide's command table, p.4
# (A7560-8EX-1, Jul. 2020; the same rows in A7560-8EX-6, Jan. 2023):
# 15 02 S-meter, 15 11 Po, 15 13 ALC, 15 14 COMP, 15 15 Vd, 15 16 Id.
# The S-meter row is stated in the IC-7300 profile's convention (dB
# relative to S9, S0 = -54). The guide prints Po in percent; the table
# states it in watts of the IC-705's 10 W rating ([power].max_watts).

_GUIDE_METER_ANCHORS = [
    ("s_meter", 0, -54.0),
    ("s_meter", 120, 0.0),
    ("s_meter", 241, 60.0),
    ("power", 0, 0.0),
    ("power", 143, 5.0),
    ("power", 213, 10.0),
    ("alc", 0, 0.0),
    ("alc", 120, 100.0),
    ("comp", 0, 0.0),
    ("comp", 130, 15.0),
    ("comp", 210, 25.5),
    ("vd", 0, 0.0),
    ("vd", 75, 5.0),
    ("vd", 241, 16.0),
    ("id", 0, 0.0),
    ("id", 121, 2.0),
    ("id", 241, 4.0),
]


class TestMeterScalesFromTheCivGuide:
    """Without a table, ``interpolate_meter`` returns the raw byte flagged
    uncalibrated (``runtime/meter_cal.py``)."""

    @pytest.mark.parametrize(
        "meter_key", ["s_meter", "power", "alc", "comp", "vd", "id", "swr"]
    )
    def test_declares_a_table_for_every_meter(self, rig, meter_key) -> None:
        assert rig.meter_calibrations is not None
        assert len(rig.meter_calibrations.get(meter_key, [])) >= 2

    @pytest.mark.parametrize(("meter_key", "raw", "expected"), _GUIDE_METER_ANCHORS)
    def test_guide_anchor_round_trip(self, rig, meter_key, raw, expected) -> None:
        actual, calibrated = interpolate_meter(raw, rig.meter_calibrations, meter_key)
        assert calibrated is True
        assert actual == pytest.approx(expected)

    def test_vd_and_id_are_not_the_ic7300_scales(self, rig) -> None:
        """The IC-7300 anchors Vd 10 V at raw 13 and Id 10 A at raw 97;
        the IC-705 guide anchors Vd 5 V at raw 75 and Id 2 A at raw 121."""
        vd, _ = interpolate_meter(13, rig.meter_calibrations, "vd")
        amps, _ = interpolate_meter(97, rig.meter_calibrations, "id")
        assert vd == pytest.approx(13 * 5.0 / 75)
        assert amps == pytest.approx(97 * 2.0 / 121)

    def test_power_stops_at_the_guides_100_percent(self, rig) -> None:
        """No anchor above raw 213 is printed, so nothing reads above 10 W."""
        actual, calibrated = interpolate_meter(255, rig.meter_calibrations, "power")
        assert calibrated is True
        assert actual == pytest.approx(10.0)

    def test_s_meter_redline_is_s9(self, rig) -> None:
        assert rig.meter_redlines is not None
        assert rig.meter_redlines["s_meter"] == 120


# ── Band-stacking-register codes (MOR-2916) ─────────────────────────────
#
# The IC-705 CI-V Reference Guide lists the band codes for 1A 01 on p.18
# (A7560-8EX-1): 01..09 = 1.9..28 MHz, 10 = 50, 11 = WFM, 12 = Air,
# 13 = 144, 14 = 430, 15 = GENE ("other than above"). The file writes each
# code as its number (6m = 0x0A = 10). A band button with a code sends it
# (web/radio_poller.py: RadioPoller._execute, SetBand); when the recall
# fails, the fallback tunes the default of the FIRST band carrying the
# same code, so a shared code sends one band's button to another band.


def _bands(rig):
    return {
        band.name: band for rng in rig.to_profile().freq_ranges for band in rng.bands
    }


class TestBandStackCodes:
    def test_no_band_sends_a_code_the_radio_does_not_have(self, rig) -> None:
        codes = [b.bsr_code for b in _bands(rig).values() if b.bsr_code is not None]
        assert all(1 <= code <= 15 for code in codes), codes

    def test_no_two_bands_share_a_code(self, rig) -> None:
        codes = [b.bsr_code for b in _bands(rig).values() if b.bsr_code is not None]
        assert len(codes) == len(set(codes)), codes

    def test_bands_without_a_register_of_their_own_jump_straight_to_frequency(
        self, rig
    ) -> None:
        """2200m, 630m and 60m have no code of their own in the guide's
        table: they are "other than above", GENE."""
        bands = _bands(rig)
        assert {name: bands[name].bsr_code for name in ("2200m", "630m", "60m")} == {
            "2200m": None,
            "630m": None,
            "60m": None,
        }

    def test_codes_follow_the_guide_table(self, rig) -> None:
        assert {
            name: band.bsr_code
            for name, band in _bands(rig).items()
            if band.bsr_code is not None
        } == {
            "160m": 1,
            "80m": 2,
            "40m": 3,
            "30m": 4,
            "20m": 5,
            "17m": 6,
            "15m": 7,
            "12m": 8,
            "10m": 9,
            "6m": 10,
            "WFM": 11,
            "Air": 12,
            "2m": 13,
            "70cm": 14,
            "Gen": 15,
        }
