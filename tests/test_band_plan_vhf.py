"""Region-aware VHF/UHF band plans (MOR-3631).

Covers:
- Loader reads the region ham files for every region (US, IARU-R1,
  IARU-R2, IARU-R3) and returns VHF/UHF segments for 6 m, 2 m, 70 cm,
  23 cm spans, with calling frequencies recorded in range notes.
- Each ham file cites its source in [meta] (loader test asserts presence).
- Unset or unknown region is explicit: logged and visible via the config
  route (requested vs effective + reason), never a silent identical
  rendering of Region 2 (MOR-1861).
- Integration across the real HTTP seam: production server startup, real
  routing, region selection persisted via the config route, then
  segment/config responses asserting the corrected region data.

Failing-first: on old code the highest ham frequency is 29.7 MHz
(no VHF segments), there is no iaru-r3.toml, _sanitize_freq clamps to
500 MHz (23 cm unrepresentable), and unknown regions fall back to US
silently with no requested/fallback fields.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from test_web_server import _addr, _http_get, _read_http_response

from rigplane.web.band_plan import _MAX_STR_LEN, _REGION_HAM_FILE, BandPlanRegistry
from rigplane.web.server import WebConfig, WebServer

REPO_ROOT = Path(__file__).resolve().parent.parent
BAND_PLANS_SRC = REPO_ROOT / "band-plans"

# Region 2 (ARRL/US) calling frequencies, Hz.
R2_CALLINGS = (
    52_525_000,  # 6 m FM calling
    144_200_000,  # 2 m SSB calling
    146_520_000,  # 2 m FM calling
    432_100_000,  # 70 cm SSB calling
    446_000_000,  # 70 cm FM calling
)

# Region 1 calling frequencies, Hz.
R1_CALLINGS = (
    144_300_000,  # 2 m SSB calling
    145_500_000,  # 2 m FM calling
    433_500_000,  # 70 cm FM calling
)

ALL_REGIONS = ("US", "IARU-R1", "IARU-R2", "IARU-R3")


def _copy_plans(tmp_path: Path) -> Path:
    """Copy the real band-plans/*.toml into tmp_path (no repo mutation)."""
    dst = tmp_path / "band-plans"
    dst.mkdir()
    for src in BAND_PLANS_SRC.glob("*.toml"):
        shutil.copy(src, dst / src.name)
    return dst


def _write_region(plans_dir: Path, region: str | None) -> None:
    """Write _config.toml with the given region (None = no region key)."""
    if region is None:
        plans_dir.joinpath("_config.toml").write_text(
            "# Band plan configuration\n\n[layers]\nham = true\n"
        )
    else:
        plans_dir.joinpath("_config.toml").write_text(
            "# Band plan configuration\n\n"
            "[settings]\n"
            f'region = "{region}"\n'
            "\n[layers]\nham = true\n"
        )


def _load(plans_dir: Path) -> BandPlanRegistry:
    reg = BandPlanRegistry()
    reg.load(plans_dir)
    return reg


def _ham_segments(reg: BandPlanRegistry, start: int, end: int):
    return [s for s in reg.get_segments(start, end) if s["layer"] == "ham"]


# ---------------------------------------------------------------------------
# Loader + data: every region serves VHF/UHF
# ---------------------------------------------------------------------------


class TestVhfDataPerRegion:
    @pytest.mark.parametrize("region", ALL_REGIONS)
    def test_two_m_span_has_ham_segments(self, tmp_path: Path, region: str) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, region)
        reg = _load(plans)
        segs = _ham_segments(reg, 144_000_000, 148_000_000)
        assert segs, f"region {region}: no ham segments on 2 m"

    @pytest.mark.parametrize("region", ALL_REGIONS)
    def test_seventy_cm_span_has_ham_segments(
        self, tmp_path: Path, region: str
    ) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, region)
        reg = _load(plans)
        segs = _ham_segments(reg, 430_000_000, 450_000_000)
        assert segs, f"region {region}: no ham segments on 70 cm"

    @pytest.mark.parametrize("region", ALL_REGIONS)
    def test_six_m_span_has_ham_segments(self, tmp_path: Path, region: str) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, region)
        reg = _load(plans)
        segs = _ham_segments(reg, 50_000_000, 54_000_000)
        assert segs, f"region {region}: no ham segments on 6 m"

    @pytest.mark.parametrize("region", ALL_REGIONS)
    def test_twentythree_cm_span_has_ham_segments(
        self, tmp_path: Path, region: str
    ) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, region)
        reg = _load(plans)
        segs = _ham_segments(reg, 1_240_000_000, 1_300_000_000)
        assert segs, f"region {region}: no ham segments on 23 cm"

    @pytest.mark.parametrize("region", ("US", "IARU-R2"))
    def test_region2_callings_present(self, tmp_path: Path, region: str) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, region)
        reg = _load(plans)
        for calling in R2_CALLINGS:
            segs = _ham_segments(reg, calling, calling)
            assert segs, f"region {region}: no segment at {calling}"
            assert any(
                f"{calling / 1_000_000:.3f}" in (s.get("notes") or "") for s in segs
            ), f"region {region}: missing calling {calling} in notes"

    def test_region1_callings_present(self, tmp_path: Path) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "IARU-R1")
        reg = _load(plans)
        for calling in R1_CALLINGS:
            segs = _ham_segments(reg, calling, calling)
            assert segs, f"IARU-R1: no segment at {calling}"
            assert any(
                f"{calling / 1_000_000:.3f}" in (s.get("notes") or "") for s in segs
            ), f"IARU-R1: missing calling {calling} in notes"

    @pytest.mark.parametrize("region", ("US", "IARU-R2", "IARU-R1"))
    def test_repeater_notes_carry_shift(self, tmp_path: Path, region: str) -> None:
        """Repeater sub-bands record their standard shift in notes."""
        plans = _copy_plans(tmp_path)
        _write_region(plans, region)
        reg = _load(plans)
        segs = _ham_segments(reg, 144_000_000, 148_000_000)
        assert any(
            "shift" in (s.get("notes") or "").lower()
            or "khz" in (s.get("notes") or "").lower()
            for s in segs
        ), f"region {region}: no repeater shift in 2 m notes"

    def test_r3_two_m_has_no_repeater_splits(self, tmp_path: Path) -> None:
        """IARU-R3 defines no regional 2 m repeater splits (R3-004)."""
        plans = _copy_plans(tmp_path)
        _write_region(plans, "IARU-R3")
        reg = _load(plans)
        segs = _ham_segments(reg, 144_000_000, 148_000_000)
        assert segs, "IARU-R3: no ham segments on 2 m"
        assert all(s.get("label") != "RPT" for s in segs), (
            "IARU-R3: unexpected repeater segment on 2 m"
        )
        assert all("shift" not in (s.get("notes") or "").lower() for s in segs), (
            "IARU-R3: unexpected repeater shift in 2 m notes"
        )

    @pytest.mark.parametrize("region", ALL_REGIONS)
    def test_ham_layer_cites_source(self, tmp_path: Path, region: str) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, region)
        reg = _load(plans)
        layers = {layer["layer"]: layer for layer in reg.get_layers()}
        assert "ham" in layers, f"region {region}: no ham layer"
        assert layers["ham"].get("source"), f"region {region}: ham [meta] has no source"
        assert layers["ham"].get("updated"), (
            f"region {region}: ham [meta] has no updated date"
        )


# ---------------------------------------------------------------------------
# Region fallback is explicit (MOR-1861)
# ---------------------------------------------------------------------------


class TestRegionFallbackExplicit:
    def test_unknown_region_defaults_visibly(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "XX-NOWHERE")
        with caplog.at_level("WARNING", logger="rigplane.web.band_plan"):
            reg = _load(plans)
        # Effective behaviour falls back to US data ...
        assert reg.region == "US"
        # ... but the fallback is visible, not a silent identical rendering.
        assert reg.requested_region == "XX-NOWHERE"
        assert reg.region_fallback_reason
        assert "XX-NOWHERE" in reg.region_fallback_reason
        assert any("XX-NOWHERE" in rec.message for rec in caplog.records), (
            "unknown region produced no warning log"
        )

    def test_unset_region_defaults_visibly(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, None)
        with caplog.at_level("WARNING", logger="rigplane.web.band_plan"):
            reg = _load(plans)
        assert reg.region == "US"
        assert reg.requested_region is None
        assert reg.region_fallback_reason
        assert any("region" in rec.message.lower() for rec in caplog.records), (
            "unset region produced no warning log"
        )

    def test_known_region_has_no_fallback(self, tmp_path: Path) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "IARU-R1")
        reg = _load(plans)
        assert reg.region == "IARU-R1"
        assert reg.requested_region == "IARU-R1"
        assert reg.region_fallback_reason is None

    def test_r3_loads_own_file(self, tmp_path: Path) -> None:
        """IARU-R3 no longer falls back to the ARRL file."""
        plans = _copy_plans(tmp_path)
        _write_region(plans, "IARU-R3")
        reg = _load(plans)
        layers = {layer["layer"]: layer for layer in reg.get_layers()}
        assert layers["ham"].get("file") == "iaru-r3.toml"


# ---------------------------------------------------------------------------
# Corrected sub-band boundaries (pre-review findings 1-3)
# ---------------------------------------------------------------------------


def _covering(reg: BandPlanRegistry, freq_hz: int) -> list[dict]:
    """Ham segments containing the exact frequency."""
    return _ham_segments(reg, freq_hz, freq_hz)


class TestCorrectedBoundaries:
    """Pin representative frequencies AND their neighbouring boundaries.

    Failing-first: each case reverts to the pre-review row (swapped 6 m
    in/out roles, +600 kHz 146 MHz outputs, 23 cm beacons at the wrong
    range, R1 SSB/beacon swaps, R3 US-derived rows) and turns red.
    """

    def test_us_six_m_repeater_pair_roles(self, tmp_path: Path) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "US")
        reg = _load(plans)
        inputs = _covering(reg, 51_300_000)
        assert any(
            s["start"] == 51_120_000
            and s["end"] == 51_480_000
            and "input" in (s.get("notes") or "").lower()
            and "51.620-51.980" in (s.get("notes") or "")
            for s in inputs
        ), "US 6 m: 51.300 MHz must sit in the 51.120-51.480 inputs row"
        outputs = _covering(reg, 51_800_000)
        assert any(
            s["start"] == 51_620_000
            and s["end"] == 51_980_000
            and "output" in (s.get("notes") or "").lower()
            and "51.120-51.480" in (s.get("notes") or "")
            for s in outputs
        ), "US 6 m: 51.800 MHz must sit in the 51.620-51.980 outputs row"

    def test_us_two_m_repeater_offsets(self, tmp_path: Path) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "US")
        reg = _load(plans)
        low_out = _covering(reg, 146_800_000)
        assert any(
            s["start"] == 146_610_000
            and s["end"] == 146_970_000
            and "-600" in (s.get("notes") or "")
            and "146.010-146.370" in (s.get("notes") or "")
            for s in low_out
        ), "US 2 m: 146.800 MHz outputs carry -600 kHz, inputs below"
        high_out = _covering(reg, 147_200_000)
        assert any(
            s["start"] == 147_000_000
            and s["end"] == 147_390_000
            and "+600" in (s.get("notes") or "")
            and "147.600-147.990" in (s.get("notes") or "")
            for s in high_out
        ), "US 2 m: 147.200 MHz outputs carry +600 kHz, inputs above"

    def test_us_23cm_beacon_range(self, tmp_path: Path) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "US")
        reg = _load(plans)
        beacons = _covering(reg, 1296_300_000)
        assert any(
            s["start"] == 1_296_200_000
            and s["end"] == 1_296_400_000
            and s.get("label") == "BCN"
            for s in beacons
        ), "US 23 cm: 1296.300 MHz must sit in 1296.200-1296.400 beacons"
        weak_below = _covering(reg, 1296_100_000)
        assert any("1296.100" in (s.get("notes") or "") for s in weak_below), (
            "US 23 cm: 1296.100 MHz keeps the weak-signal calling row"
        )
        narrow_above = _covering(reg, 1296_500_000)
        assert narrow_above and all(s.get("label") != "BCN" for s in narrow_above), (
            "US 23 cm: 1296.500 MHz is general narrowband, not beacons"
        )

    def test_r1_beacon_exclusive_rows(self, tmp_path: Path) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "IARU-R1")
        reg = _load(plans)
        at_70cm = _covering(reg, 432_450_000)
        assert any(
            s["start"] == 432_400_000
            and s["end"] == 432_490_000
            and s.get("label") == "BCN"
            for s in at_70cm
        ), "R1 70 cm: 432.450 MHz must sit in 432.400-432.490 beacons"
        assert all(s.get("label") != "SSB" for s in at_70cm), (
            "R1 70 cm: 432.450 MHz must not be labelled SSB"
        )
        below = _covering(reg, 432_350_000)
        assert {s.get("label") for s in below} == {"ALL"}, (
            "R1 70 cm: 432.350 MHz is the all-mode row below the beacon band"
        )
        above = _covering(reg, 432_492_000)
        assert any(s.get("label") == "DIGI" for s in above), (
            "R1 70 cm: 432.492 MHz is experimental MGM above the beacons"
        )
        at_6m = _covering(reg, 50_450_000)
        assert any(s.get("label") == "BCN" for s in at_6m), (
            "R1 6 m: 50.450 MHz must sit in 50.400-50.500 beacons"
        )

    def test_r1_repeater_rows_not_beacons(self, tmp_path: Path) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "IARU-R1")
        reg = _load(plans)
        at_432_9 = _covering(reg, 432_900_000)
        assert any(s.get("label") == "RPT" for s in at_432_9), (
            "R1 70 cm: 432.900 MHz is a repeater input, not BCN"
        )
        assert all(s.get("label") != "BCN" for s in at_432_9)
        at_1297_1 = _covering(reg, 1_297_100_000)
        assert any(
            s["start"] == 1_296_994_000
            and s["end"] == 1_297_481_000
            and s.get("label") == "RPT"
            for s in at_1297_1
        ), "R1 23 cm: 1297.100 MHz must sit in repeater outputs"
        simplex = _covering(reg, 1_297_500_000)
        assert any(
            s.get("label") == "FM" and "1297.500" in (s.get("notes") or "")
            for s in simplex
        ), "R1 23 cm: 1297.500 MHz is FM simplex calling"

    def test_r3_regional_assignments(self, tmp_path: Path) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "IARU-R3")
        reg = _load(plans)
        at_144_4 = _covering(reg, 144_400_000)
        assert at_144_4, "R3 2 m: 144.400 MHz must be covered"
        assert all(s.get("label") != "SAT" for s in at_144_4), (
            "R3 2 m: 144.400 MHz is all-modes, not satellite"
        )
        at_145_9 = _covering(reg, 145_900_000)
        assert any(s.get("label") == "SAT" for s in at_145_9), (
            "R3 2 m: 145.900 MHz must sit in 145.800-146.000 satellite"
        )
        assert not _covering(reg, 445_000_000), (
            "R3 70 cm: 445.000 MHz must fall outside (440-450 MHz deleted)"
        )
        at_40m = _covering(reg, 7_150_000)
        assert at_40m, "R3 40 m: 7.150 MHz must be covered"
        assert not _covering(reg, 7_250_000), (
            "R3 40 m: 7.250 MHz must fall outside (band ends 7.200 MHz)"
        )
        at_60m = _covering(reg, 5_352_000)
        assert any(s.get("label") == "DIGI" for s in at_60m), (
            "R3 60 m: 5352 kHz must sit in 5351.5-5354 narrowband"
        )


# ---------------------------------------------------------------------------
# IARU-R1 VHF/UHF rows against the VHF Handbook v10.03 (round-2 finding 1)
# ---------------------------------------------------------------------------
#
# Every point is one the handbook's tables (printed pages 35-36, 38-39, 41-43,
# 45-46) assign to a role. Points sit 1 kHz or more inside each row so the
# inclusive row edges never make a boundary ambiguous; the same table
# therefore pins both sides of every corrected boundary. ``None`` = a gap the
# handbook leaves unassigned (no ham segment may claim it).

# (frequency Hz, expected label | None)
_R1_HANDBOOK_ROWS: tuple[tuple[int, str | None], ...] = (
    # 6 m (page 35)
    (50_000_000, "BCN"),  # Synchronised Beacon Project reservation
    (50_029_000, "BCN"),
    (50_031_000, "CW"),  # was labelled beacons up to 50.080
    (50_050_000, "CW"),  # CW future international centre of activity
    (50_090_000, "CW"),
    (50_099_000, "CW"),
    (50_101_000, "SSB"),
    (50_250_000, "SSB"),
    (50_350_000, "DIGI"),
    (50_450_000, "BCN"),
    (50_600_000, "ALL"),
    (50_800_000, "RPT"),  # repeater outputs 50.710-50.890
    (50_950_000, "ALL"),
    (51_300_000, "RPT"),  # repeater inputs 51.210-51.390
    (51_510_000, "ALL"),  # FM calling sits inside an all-modes row
    (51_999_000, "ALL"),  # top of the 50.5-52.0 all-modes sections
    (52_001_000, "ALL"),  # 52-54 MHz, 500 kHz all-modes blocks (footnote f)
    (53_000_000, "ALL"),
    (53_999_000, "ALL"),
    (54_001_000, None),  # above the 6 m band
    # 2 m (pages 38-39)
    (144_010_000, "SAT"),
    (144_030_000, "CW"),
    (144_099_000, "CW"),
    (144_101_000, "CW/MGM"),
    (144_125_000, "CW/MGM"),  # was labelled SSB
    (144_149_000, "CW/MGM"),
    (144_151_000, "SSB"),
    (144_399_000, "SSB"),
    (144_450_000, "BCN"),
    (144_492_000, "MGM"),
    (144_495_000, None),
    (144_600_000, "ALL"),
    (144_800_000, "DIGI"),
    (144_960_000, "DIGI"),
    (144_970_000, None),  # between the digital block and repeater inputs
    (144_987_500, "RPT"),  # was labelled digital
    (145_100_000, "RPT"),
    (145_193_000, "RPT"),
    (145_200_000, "SPC"),  # space communication, not a repeater slot
    (145_207_000, "FM"),
    (145_560_000, "FM"),
    (145_570_000, None),
    (145_587_500, "RPT"),  # was labelled simplex
    (145_790_000, "RPT"),
    (145_800_000, "SPC"),  # space communication, not a repeater slot
    (145_807_000, "SAT"),
    (145_999_000, "SAT"),
    # 70 cm (pages 41-43): no analogue ATV since 2021
    (430_100_000, "RPT"),
    (430_370_000, "RPT"),
    (430_380_000, "ALL"),  # inside the 430.000-432.000 all-mode block
    (430_395_000, "ALL"),
    (430_410_000, "DIGI"),
    (430_010_000, "ALL"),
    (430_030_000, "RPT"),
    (430_570_000, "DIGI"),
    (430_590_000, "ALL"),
    (430_610_000, "DIGI"),
    (431_020_000, "MULTI"),
    (431_035_000, "ALL"),
    (431_060_000, "RPT"),
    (430_500_000, "DIGI"),
    (430_700_000, "DIGI"),
    (430_950_000, "MULTI"),
    (431_100_000, "RPT"),
    (432_100_000, "ALL"),
    (432_350_000, "ALL"),
    (432_450_000, "BCN"),
    (432_492_000, "DIGI"),
    (432_550_000, "ALL"),
    (432_700_000, "RPT"),
    (432_900_000, "RPT"),
    (433_100_000, "RPT"),
    (433_450_000, "FM"),
    (433_500_000, "FM"),
    (433_700_000, "ALL"),
    (434_100_000, "ALL"),  # was ATV
    (434_700_000, "RPT"),
    (435_500_000, "SAT"),
    (437_000_000, "SAT"),
    (438_100_000, "DIGI"),  # was ATV
    (438_300_000, "DIGI"),
    (438_600_000, "MULTI"),
    (439_000_000, "RPT"),
    (437_990_000, "SAT"),
    (438_010_000, "ALL"),  # 438.000-440.000 is one all-mode block
    (438_030_000, "DIGI"),
    (438_170_000, "DIGI"),
    (438_180_000, "ALL"),
    (438_190_000, "ALL"),
    (438_210_000, "DIGI"),
    (438_520_000, "DIGI"),
    (438_530_000, "ALL"),
    (438_540_000, "ALL"),
    (438_560_000, "MULTI"),
    (438_620_000, "MULTI"),
    (438_630_000, "ALL"),
    (438_640_000, "ALL"),
    (438_660_000, "RPT"),
    (439_580_000, "RPT"),
    (439_700_000, "ALL"),
    (439_790_000, "ALL"),
    (439_810_000, "DIGI"),
    (439_970_000, "DIGI"),
    (439_990_000, "ALL"),
    (439_900_000, "DIGI"),
    # 23 cm (pages 45-46)
    (1_240_100_000, "ALL"),
    (1_240_600_000, "BCN"),
    (1_240_800_000, "FM"),
    (1_241_500_000, "ALL"),
    (1_242_100_000, "RPT"),
    (1_242_690_000, "RPT"),
    (1_242_710_000, "ALL"),  # between channels RS28 and RS29 of the all-mode block
    (1_242_730_000, "DIGI"),
    (1_243_000_000, "DIGI"),
    (1_250_000_000, "ATV"),
    (1_259_000_000, "RPT"),
    (1_265_000_000, "SAT"),
    (1_270_010_000, "ALL"),  # the 1270.000-1272.000 block starts here
    (1_270_030_000, "RPT"),
    (1_270_500_000, "RPT"),
    (1_270_690_000, "RPT"),
    (1_270_710_000, "ALL"),
    (1_270_730_000, "DIGI"),
    (1_271_000_000, "DIGI"),
    (1_280_000_000, "ATV"),
    (1_291_200_000, "RPT"),
    (1_291_487_000, None),
    (1_292_500_000, "ALL"),  # was labelled a repeater input
    (1_293_100_000, "ALL"),
    (1_293_200_000, "RPT"),  # designated inputs R20-R68 start here
    (1_294_300_000, "RPT"),
    (1_294_400_000, "ALL"),
    (1_296_010_000, "CW/MGM"),
    (1_296_300_000, "SSB"),
    (1_296_900_000, "BCN"),
    (1_297_100_000, "RPT"),
    (1_297_600_000, "FM"),
    (1_298_500_000, "ALL"),
    (1_299_300_000, "DD"),
    (1_299_800_000, "ALL"),
)


# Handbook mode ranges whose usage rows are only a subset of the range
# (pages 42 and 45): the whole range is "all modes".
_R1_ALL_MODE_BLOCKS: tuple[tuple[int, int], ...] = (
    (430_000_000, 431_987_500),  # sub-regional (national bandplanning), no limit
    (438_000_000, 440_000_000),  # all modes, no limit
    (1_241_000_000, 1_243_250_000),  # all modes, 20 kHz
    (1_270_000_000, 1_272_000_000),  # all modes, 20 kHz
)


class TestIaruR1HandbookRows:
    @pytest.fixture(scope="class")
    def r1(self, tmp_path_factory: pytest.TempPathFactory) -> BandPlanRegistry:
        plans = _copy_plans(tmp_path_factory.mktemp("r1"))
        _write_region(plans, "IARU-R1")
        return _load(plans)

    @pytest.mark.parametrize(
        ("freq_hz", "label"),
        _R1_HANDBOOK_ROWS,
        ids=[f"{f / 1e6:.4f}MHz-{label}" for f, label in _R1_HANDBOOK_ROWS],
    )
    def test_role_at_frequency(
        self, r1: BandPlanRegistry, freq_hz: int, label: str | None
    ) -> None:
        labels = {s["label"] for s in _covering(r1, freq_hz)}
        assert labels == ({label} if label else set()), (
            f"R1 {freq_hz / 1e6:.4f} MHz: handbook says {label}, plan says {labels}"
        )

    @pytest.mark.parametrize(
        ("lo", "hi"),
        _R1_ALL_MODE_BLOCKS,
        ids=[f"{lo / 1e6:.4f}-{hi / 1e6:.4f}MHz" for lo, hi in _R1_ALL_MODE_BLOCKS],
    )
    def test_all_mode_block_has_no_hole_between_usage_rows(
        self, r1: BandPlanRegistry, lo: int, hi: int
    ) -> None:
        """Usage channels sit inside a handbook mode range; the range stays."""
        assert _holes(r1, lo, hi) == [], (
            f"R1 plan leaves part of the {lo / 1e6:.4f}-{hi / 1e6:.4f} MHz "
            "all-mode block unassigned"
        )

    def test_no_analogue_atv_on_70cm(self, r1: BandPlanRegistry) -> None:
        """Analogue ATV left 430-440 MHz on 2021-01-01 (handbook page 43)."""
        assert all(
            s["label"] != "ATV" for s in _ham_segments(r1, 430_000_000, 440_000_000)
        )

    @pytest.mark.parametrize(
        ("freq_hz", "needle"),
        (
            (50_050_000, "50.050"),  # CW centre of activity named on the CW row
            (50_090_000, "50.090"),
            (50_110_000, "DX calling"),
            (144_125_000, "EME"),  # EME belongs to the CW/MGM row
            (432_700_000, "UK repeater outputs"),  # UK reverses the 2 MHz pairs
            (433_100_000, "UK repeater outputs"),
            (434_700_000, "UK repeater inputs"),
            (1_293_200_000, "R20 (1293.150)"),
        ),
    )
    def test_row_notes_carry_the_handbook_usage(
        self, r1: BandPlanRegistry, freq_hz: int, needle: str
    ) -> None:
        segs = _covering(r1, freq_hz)
        assert any(needle.lower() in (s.get("notes") or "").lower() for s in segs), (
            f"R1 {freq_hz / 1e6:.4f} MHz: notes lack {needle!r}"
        )

    def test_beacon_reservation_is_not_a_cw_or_eme_row(
        self, r1: BandPlanRegistry
    ) -> None:
        """50.000-50.030 is the Synchronised Beacon Project, not EME."""
        for freq in (50_000_000, 50_029_000):
            segs = _covering(r1, freq)
            assert {s["label"] for s in segs} == {"BCN"}
            assert all("EME" not in (s.get("notes") or "") for s in segs)


class TestIaruR1UpperSixMetres:
    """52-54 MHz is an all-modes section of the R1 6 m plan (handbook p. 35-36)."""

    @pytest.fixture(scope="class")
    def r1(self, tmp_path_factory: pytest.TempPathFactory) -> BandPlanRegistry:
        plans = _copy_plans(tmp_path_factory.mktemp("r1_6m"))
        _write_region(plans, "IARU-R1")
        return _load(plans)

    def test_row_spans_52_to_54_mhz_with_its_qualifications(
        self, r1: BandPlanRegistry
    ) -> None:
        for freq in (52_001_000, 53_000_000, 53_999_000):
            covering = _covering(r1, freq)
            assert [(s["start"], s["end"], s["label"]) for s in covering] == [
                (52_000_000, 54_000_000, "ALL")
            ], f"R1 6 m: {freq / 1e6:.3f} MHz must sit in the 52-54 MHz all-modes row"
        notes = _covering(r1, 53_000_000)[0]["notes"]
        assert "500 kHz" in notes and "allocated" in notes, (
            "the row must say it applies only where 52-54 MHz is allocated"
        )

    def test_boundaries_do_not_leak_past_either_edge(
        self, r1: BandPlanRegistry
    ) -> None:
        below = _covering(r1, 51_999_000)
        assert [(s["start"], s["end"]) for s in below] == [(51_400_000, 52_000_000)]
        assert _covering(r1, 54_001_000) == []

    def test_band_header_spans_50_to_54_mhz(self) -> None:
        import tomllib

        raw = tomllib.loads((BAND_PLANS_SRC / "iaru-r1.toml").read_text("utf-8"))
        band = next(b for b in raw["band"] if b["name"] == "6m")
        assert (band["start"], band["end"]) == (50_000_000, 54_000_000)


# ---------------------------------------------------------------------------
# ARRL (US) VHF/UHF rows against https://www.arrl.org/band-plan (round-3 finding)
# ---------------------------------------------------------------------------
#
# Every (start, end) below is a row of the ARRL page's 6 m, 2 m, 70 cm and
# 23 cm tables, fetched 2026-10-29. The plan must assign every one of them
# without a hole (432.450 MHz, for example, is "Mixed-mode and weak-signal
# work", not unassigned). Frequencies the page itself leaves unassigned (the
# channel guard gaps between repeater rows) are NOT listed and no test
# expects them to stay empty.

_US_SOURCE_ROWS: tuple[tuple[int, int], ...] = (
    # 6 m
    (50_000_000, 50_100_000),
    (50_100_000, 50_300_000),
    (50_300_000, 50_600_000),
    (50_600_000, 50_800_000),
    (50_800_000, 51_000_000),
    (51_000_000, 51_100_000),
    (51_120_000, 51_480_000),
    (51_500_000, 51_600_000),
    (51_620_000, 51_980_000),
    (52_000_000, 52_480_000),
    (52_500_000, 52_980_000),
    (53_000_000, 53_480_000),
    (53_500_000, 53_980_000),
    # 2 m
    (144_000_000, 144_275_000),
    (144_275_000, 144_300_000),
    (144_300_000, 144_500_000),
    (144_500_000, 144_600_000),
    (144_600_000, 144_900_000),
    (144_900_000, 145_100_000),
    (145_100_000, 145_200_000),
    (145_200_000, 145_500_000),
    (145_500_000, 145_800_000),
    (145_800_000, 146_000_000),
    (146_010_000, 146_370_000),
    (146_400_000, 146_580_000),
    (146_610_000, 146_970_000),
    (147_000_000, 147_390_000),
    (147_420_000, 147_570_000),
    (147_600_000, 147_990_000),
    # 70 cm
    (420_000_000, 426_000_000),
    (426_000_000, 432_000_000),
    (432_000_000, 432_070_000),
    (432_070_000, 432_100_000),
    (432_100_000, 432_300_000),
    (432_300_000, 432_400_000),
    (432_400_000, 433_000_000),
    (433_000_000, 435_000_000),
    (435_000_000, 438_000_000),
    (438_000_000, 444_000_000),
    (442_000_000, 445_000_000),
    (445_000_000, 447_000_000),
    (447_000_000, 450_000_000),
    # 23 cm
    (1_240_000_000, 1_246_000_000),
    (1_246_000_000, 1_248_000_000),
    (1_248_000_000, 1_252_000_000),
    (1_252_000_000, 1_258_000_000),
    (1_258_000_000, 1_260_000_000),
    (1_260_000_000, 1_270_000_000),
    (1_270_000_000, 1_276_000_000),
    (1_276_000_000, 1_282_000_000),
    (1_282_000_000, 1_288_000_000),
    (1_288_000_000, 1_294_000_000),
    (1_294_000_000, 1_295_000_000),
    (1_295_000_000, 1_295_800_000),
    (1_295_800_000, 1_296_080_000),
    (1_296_080_000, 1_296_200_000),
    (1_296_200_000, 1_296_400_000),
    (1_296_400_000, 1_297_000_000),
    (1_297_000_000, 1_300_000_000),
)

# (frequency Hz, text the covering rows' notes must mention): the roles the
# page assigns to rows that used to be missing, and two that were mislabelled.
_US_ROLES: tuple[tuple[int, str], ...] = (
    (50_050_000, "beacons"),
    (50_650_000, "nonvoice"),
    (50_620_000, "packet"),
    (50_900_000, "remote control"),
    (51_050_000, "pacific dx window"),
    (51_550_000, "simplex"),
    (144_550_000, "linear translator inputs"),
    (145_050_000, "weak signal and fm simplex"),
    (145_150_000, "linear translator outputs"),
    (145_650_000, "experimental"),
    (422_000_000, "421.250"),
    (430_000_000, "427.250"),
    (432_200_000, "mixed-mode"),
    (432_450_000, "mixed-mode and weak-signal"),
    (440_000_000, "439.250"),
    (1_247_000_000, "point-to-point"),
    (1_250_000_000, "digital"),
    (1_255_000_000, "atv channel 2"),
    (1_279_000_000, "atv channel 3"),
    (1_289_000_000, "broadband experimental"),
    (1_295_400_000, "narrow band image"),
    (1_298_000_000, "digital"),
)


def _holes(reg: BandPlanRegistry, lo: int, hi: int) -> list[tuple[int, int]]:
    """Sub-ranges of [lo, hi] that no ham segment covers."""
    cursor = lo
    holes: list[tuple[int, int]] = []
    for seg in sorted(_ham_segments(reg, lo, hi), key=lambda s: (s["start"], s["end"])):
        if seg["start"] > cursor:
            holes.append((cursor, seg["start"]))
        cursor = max(cursor, seg["end"])
    if cursor < hi:
        holes.append((cursor, hi))
    return holes


class TestArrlSourceRowsAreAssigned:
    @pytest.fixture(scope="class")
    def us(self, tmp_path_factory: pytest.TempPathFactory) -> BandPlanRegistry:
        plans = _copy_plans(tmp_path_factory.mktemp("us_rows"))
        _write_region(plans, "US")
        return _load(plans)

    @pytest.mark.parametrize(
        ("lo", "hi"),
        _US_SOURCE_ROWS,
        ids=[f"{lo / 1e6:.3f}-{hi / 1e6:.3f}MHz" for lo, hi in _US_SOURCE_ROWS],
    )
    def test_source_row_has_no_hole(
        self, us: BandPlanRegistry, lo: int, hi: int
    ) -> None:
        assert _holes(us, lo, hi) == [], (
            f"US plan leaves part of the ARRL row {lo / 1e6:.3f}-{hi / 1e6:.3f} "
            "MHz unassigned"
        )

    @pytest.mark.parametrize(
        ("freq_hz", "needle"),
        _US_ROLES,
        ids=[f"{f / 1e6:.4f}MHz-{n}" for f, n in _US_ROLES],
    )
    def test_role_at_frequency(
        self, us: BandPlanRegistry, freq_hz: int, needle: str
    ) -> None:
        covering = _covering(us, freq_hz)
        assert any(needle in (s.get("notes") or "").lower() for s in covering), (
            f"US {freq_hz / 1e6:.4f} MHz: the ARRL page says '{needle}'; "
            f"plan has {[s.get('notes') for s in covering]}"
        )

    def test_432_450_is_mixed_mode_not_a_gap(self, us: BandPlanRegistry) -> None:
        covering = _covering(us, 432_450_000)
        assert [(s["start"], s["end"]) for s in covering] == [
            (432_400_000, 433_000_000)
        ]
        # Both sides of the 432.400 beacon boundary.
        assert {s["label"] for s in _covering(us, 432_399_000)} == {"BCN"}
        assert {s["label"] for s in _covering(us, 432_401_000)} == {"ALL"}


# (frequency Hz, labels that must cover it, notes fragments that must appear,
# notes fragments that must NOT appear): ARRL rows that overlap another row.
# Each ``-1 kHz`` / ``+1 kHz`` pair brackets a row edge on both sides.
_US_OVERLAPS: tuple[tuple[int, set[str], tuple[str, ...], tuple[str, ...]], ...] = (
    # 70 cm: ATV runs 438.000-444.000, the local-option repeater row starts 442.
    (437_999_000, {"SAT"}, (), ("atv",)),
    (438_001_000, {"ATV"}, ("439.250",), ("repeater inputs and outputs",)),
    (441_999_000, {"ATV"}, ("439.250",), ("repeater inputs and outputs",)),
    (442_001_000, {"ATV", "RPT"}, ("439.250", "repeater inputs and outputs"), ()),
    (443_000_000, {"ATV", "RPT"}, ("439.250", "repeater inputs and outputs"), ()),
    (443_999_000, {"ATV", "RPT"}, ("439.250", "repeater inputs and outputs"), ()),
    (444_001_000, {"RPT"}, ("repeater inputs and outputs",), ("439.250",)),
    # 23 cm: the 1240-1260 FM ATV regional option spans the channel rows.
    (1_239_999_000, set(), (), ()),
    (1_240_001_000, {"ATV"}, ("fm atv",), ()),
    (1_246_001_000, {"ATV", "LINK"}, ("fm atv", "point-to-point"), ()),
    (1_249_000_000, {"ATV", "DIGI"}, ("fm atv",), ()),
    (1_259_999_000, {"ATV", "LINK"}, ("fm atv", "point-to-point"), ()),
    (1_260_001_000, {"SAT"}, ("satellite",), ("fm atv",)),
    # 23 cm: the 1270-1274 inputs are paired with 1290-1294 (regional option).
    (1_269_999_000, {"SAT"}, ("satellite",), ("1290.000-1294.000",)),
    (1_270_001_000, {"RPT"}, ("1282.000-1288.000", "1290.000-1294.000"), ()),
    (1_271_000_000, {"RPT"}, ("1282.000-1288.000", "1290.000-1294.000"), ()),
    (1_273_999_000, {"RPT"}, ("1282.000-1288.000", "1290.000-1294.000"), ()),
    (1_274_001_000, {"RPT"}, ("1282.000-1288.000",), ("1290.000-1294.000",)),
    # 23 cm: experimental/simplex ATV runs 1288.000-1294.000 over the
    # regional repeater outputs 1290.000-1294.000.
    (1_287_999_000, {"RPT"}, ("1270.000-1276.000",), ("experimental",)),
    (1_288_001_000, {"EXP"}, ("broadband experimental",), ("regional option",)),
    (1_289_999_000, {"EXP"}, ("broadband experimental",), ("regional option",)),
    (1_290_001_000, {"EXP", "RPT"}, ("broadband experimental", "regional option"), ()),
    (1_292_000_000, {"EXP", "RPT"}, ("broadband experimental", "regional option"), ()),
    (1_293_999_000, {"EXP", "RPT"}, ("broadband experimental", "regional option"), ()),
    (1_294_001_000, {"FM"}, ("fm simplex",), ("broadband experimental",)),
)


class TestArrlOverlappingOptions:
    """Where the ARRL page lists two rows for one frequency, both are served."""

    @pytest.fixture(scope="class")
    def us(self, tmp_path_factory: pytest.TempPathFactory) -> BandPlanRegistry:
        plans = _copy_plans(tmp_path_factory.mktemp("us_overlaps"))
        _write_region(plans, "US")
        return _load(plans)

    @pytest.mark.parametrize(
        ("freq_hz", "labels", "present", "absent"),
        _US_OVERLAPS,
        ids=[f"{f / 1e6:.4f}MHz" for f, *_ in _US_OVERLAPS],
    )
    def test_overlapping_rows_at_frequency(
        self,
        us: BandPlanRegistry,
        freq_hz: int,
        labels: set[str],
        present: tuple[str, ...],
        absent: tuple[str, ...],
    ) -> None:
        covering = _covering(us, freq_hz)
        assert {s["label"] for s in covering} == labels
        notes = [(s.get("notes") or "").lower() for s in covering]
        for needle in present:
            assert any(needle in n for n in notes), (
                f"{freq_hz / 1e6:.4f} MHz: no row mentions {needle!r}: {notes}"
            )
        for needle in absent:
            assert not any(needle in n for n in notes), (
                f"{freq_hz / 1e6:.4f} MHz: a row still mentions {needle!r}: {notes}"
            )

    @pytest.mark.parametrize(
        ("start", "end"),
        (
            (438_000_000, 444_000_000),  # ATV repeater input row
            (1_240_000_000, 1_260_000_000),  # FM ATV regional option
            (1_270_000_000, 1_274_000_000),  # inputs paired with 1290-1294
            (1_288_000_000, 1_294_000_000),  # broadband experimental, simplex ATV
        ),
    )
    def test_source_row_is_one_complete_row(
        self, us: BandPlanRegistry, start: int, end: int
    ) -> None:
        assert any(
            s["start"] == start and s["end"] == end
            for s in _ham_segments(us, start, end)
        ), f"the ARRL row {start / 1e6:.3f}-{end / 1e6:.3f} MHz is cut short or missing"


@pytest.mark.parametrize("ham_file", sorted(set(_REGION_HAM_FILE.values())))
def test_ham_notes_are_not_truncated_by_the_loader(
    tmp_path: Path, ham_file: str
) -> None:
    """The loader cuts notes at ``_MAX_STR_LEN``; a longer one would lose data."""
    import tomllib

    raw = tomllib.loads((BAND_PLANS_SRC / ham_file).read_text(encoding="utf-8"))
    too_long = [
        (band["name"], seg["start"], len(seg["notes"]))
        for band in raw.get("band", [])
        for seg in band.get("segment", [])
        if len(seg.get("notes", "")) > _MAX_STR_LEN
    ]
    assert not too_long, f"{ham_file}: notes cut at {_MAX_STR_LEN} chars: {too_long}"


# ---------------------------------------------------------------------------
# Integration across the real HTTP seam (pre-review finding 8a)
# ---------------------------------------------------------------------------
#
# Production server startup (which loads the band-plan directory the server
# was configured with), real routing over real sockets, region selection
# persisted via the config route. Everything runs against an isolated copy
# of band-plans/ injected through ``WebConfig.band_plans_dir``: startup, the
# config POST and the reload all use that one directory, so no test touches
# the shared checkout (and parallel xdist workers cannot race on it).

REPO_CONFIG = REPO_ROOT / "band-plans" / "_config.toml"

_US_CONFIG = (
    "# Band plan configuration\n"
    "\n"
    "[settings]\n"
    'region = "US"\n'
    "\n"
    "[layers]\n"
    "ham = true\n"
    "broadcast = true\n"
    "utility = true\n"
)


async def _http_post_json(
    host: str, port: int, path: str, payload: dict
) -> tuple[int, dict[str, str], bytes]:
    """Minimal POST with a JSON body over a real socket (mirrors _http_get)."""
    body = json.dumps(payload).encode()
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        return await asyncio.wait_for(_read_http_response(reader), timeout=5.0)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _get_segments_json(host: str, port: int, start: int, end: int) -> dict:
    status, _, body = await _http_get(
        host, port, f"/api/v1/band-plan/segments?start={start}&end={end}"
    )
    assert status == 200, f"GET segments {start}-{end}: HTTP {status}"
    return json.loads(body)


def _ham_only(payload: dict) -> list[dict]:
    return [s for s in payload.get("segments", []) if s["layer"] == "ham"]


@pytest.mark.asyncio
async def test_region_selection_over_real_http(tmp_path: Path) -> None:
    """Select regions via POST, read segments/config via GET (finding 8a).

    One sequential pass US -> IARU-R1 -> IARU-R3 -> US, a restart that must
    keep the persisted selection, then an unknown region across another
    restart. All of it against an isolated copy of band-plans/ that the
    server startup and the config route both use. Failing-first: restoring
    the pre-review rows (R1 beacons at 432.800-433.000, R3 144.300-144.500
    SAT) turns the per-region assertions red; bypassing startup/routing
    cannot persist the selection to disk.
    """
    repo_config_before = REPO_CONFIG.read_bytes()
    plans = _copy_plans(tmp_path)
    config_file = plans / "_config.toml"
    config_file.write_text(_US_CONFIG)
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0, band_plans_dir=plans))
    try:
        await srv.start()
        host, port = _addr(srv)

        # US baseline through the real seam.
        segs = _ham_only(await _get_segments_json(host, port, 146_400_000, 147_000_000))
        assert any("146.520" in (s.get("notes") or "") for s in segs), (
            "US startup: no 146.520 calling note on 2 m"
        )
        us_432 = _ham_only(
            await _get_segments_json(host, port, 432_450_000, 432_450_000)
        )
        assert [(s["start"], s["end"], s["label"]) for s in us_432] == [
            (432_400_000, 433_000_000, "ALL")
        ], "US startup: 432.450 MHz is mixed-mode/weak-signal work, not R1 beacons"

        # Region switch via the production config route.
        status, _, raw = await _http_post_json(
            host, port, "/api/v1/band-plan/config", {"region": "IARU-R1"}
        )
        assert status == 200, f"POST region IARU-R1: HTTP {status}"
        assert json.loads(raw)["region"] == "IARU-R1"
        assert 'region = "IARU-R1"' in config_file.read_text(), (
            "region selection was not persisted to the injected _config.toml"
        )

        # Config route: requested vs effective, no fallback for a known region.
        status, _, raw = await _http_get(host, port, "/api/v1/band-plan/config")
        assert status == 200
        cfg = json.loads(raw)
        assert cfg["region"] == "IARU-R1"
        assert cfg["requestedRegion"] == "IARU-R1"
        assert cfg["fallbackReason"] is None

        # Same viewport, different plan: R1 2 m ends at 146 MHz.
        assert (
            _ham_only(await _get_segments_json(host, port, 146_400_000, 147_000_000))
            == []
        ), "IARU-R1 still serves US 2 m segments above 146 MHz"
        # Corrected R1 rows through the seam.
        beacons = _ham_only(
            await _get_segments_json(host, port, 432_450_000, 432_450_000)
        )
        assert len(beacons) == 1 and beacons[0]["label"] == "BCN", (
            "IARU-R1: 432.450 MHz must be the beacon-exclusive row"
        )
        outputs = _ham_only(
            await _get_segments_json(host, port, 1_297_100_000, 1_297_100_000)
        )
        assert any(s.get("label") == "RPT" for s in outputs), (
            "IARU-R1: 1297.100 MHz must sit in repeater outputs"
        )
        for freq, expected in (
            (51_999_000, (51_400_000, 52_000_000)),
            (52_001_000, (52_000_000, 54_000_000)),
            (53_999_000, (52_000_000, 54_000_000)),
        ):
            six_m = _ham_only(await _get_segments_json(host, port, freq, freq))
            assert [(s["start"], s["end"]) for s in six_m] == [expected], (
                f"IARU-R1: {freq / 1e6:.3f} MHz must sit in the handbook 6 m rows"
            )
        assert (
            _ham_only(await _get_segments_json(host, port, 54_001_000, 54_001_000))
            == []
        ), "IARU-R1: 54.001 MHz is above the 6 m band"
        gap = _ham_only(
            await _get_segments_json(host, port, 1_292_500_000, 1_292_500_000)
        )
        assert [s["label"] for s in gap] == ["ALL"], (
            "IARU-R1: 1292.500 MHz is below the designated 23 cm repeater inputs"
        )

        # IARU-R3 serves the regional plan, not US-derived rows.
        status, _, raw = await _http_post_json(
            host, port, "/api/v1/band-plan/config", {"region": "IARU-R3"}
        )
        assert status == 200
        assert json.loads(raw)["region"] == "IARU-R3"
        r3_mid = _ham_only(
            await _get_segments_json(host, port, 144_400_000, 144_400_000)
        )
        assert r3_mid and all(s.get("label") != "SAT" for s in r3_mid), (
            "IARU-R3: 144.400 MHz is all-modes, not satellite"
        )
        r3_sat = _ham_only(
            await _get_segments_json(host, port, 145_900_000, 145_900_000)
        )
        assert any(s.get("label") == "SAT" for s in r3_sat), (
            "IARU-R3: 145.900 MHz must sit in 145.800-146.000 satellite"
        )
        assert (
            _ham_only(await _get_segments_json(host, port, 445_000_000, 445_000_000))
            == []
        ), "IARU-R3: 445.000 MHz must fall outside (440-450 MHz deleted)"

        # Back to US: the American plan returns on the same viewport.
        status, _, _ = await _http_post_json(
            host, port, "/api/v1/band-plan/config", {"region": "US"}
        )
        assert status == 200
        segs = _ham_only(await _get_segments_json(host, port, 146_400_000, 147_000_000))
        assert any("146.520" in (s.get("notes") or "") for s in segs), (
            "US restore: 146.520 calling note did not come back"
        )

        # Leave R1 selected on disk for the restart below.
        status, _, _ = await _http_post_json(
            host, port, "/api/v1/band-plan/config", {"region": "IARU-R1"}
        )
        assert status == 200
    finally:
        await srv.stop()

    # Restart: production startup reads the persisted selection back.
    srv_restart = WebServer(
        None, WebConfig(host="127.0.0.1", port=0, band_plans_dir=plans)
    )
    try:
        await srv_restart.start()
        host, port = _addr(srv_restart)
        status, _, raw = await _http_get(host, port, "/api/v1/band-plan/config")
        assert status == 200
        cfg = json.loads(raw)
        assert cfg["region"] == "IARU-R1" and cfg["fallbackReason"] is None, (
            "restart lost the persisted region selection"
        )
        assert _ham_only(
            await _get_segments_json(host, port, 432_450_000, 432_450_000)
        ), "restart: R1 plan data not served after reload"
    finally:
        await srv_restart.stop()

    # Unknown region hand-written to disk: production startup falls back
    # to US data visibly instead of rendering R2 silently (MOR-1861).
    config_file.write_text(
        '# Band plan configuration\n\n[settings]\nregion = "XX-NOWHERE"\n\n'
        "[layers]\nham = true\nbroadcast = true\nutility = true\n"
    )
    srv2 = WebServer(None, WebConfig(host="127.0.0.1", port=0, band_plans_dir=plans))
    try:
        await srv2.start()
        host, port = _addr(srv2)
        status, _, raw = await _http_get(host, port, "/api/v1/band-plan/config")
        assert status == 200
        cfg = json.loads(raw)
        assert cfg["region"] == "US"
        assert cfg["requestedRegion"] == "XX-NOWHERE"
        assert cfg["fallbackReason"] and "XX-NOWHERE" in cfg["fallbackReason"]
        segs = _ham_only(await _get_segments_json(host, port, 146_520_000, 146_520_000))
        assert any("146.520" in (s.get("notes") or "") for s in segs), (
            "fallback: effective US data not served"
        )
    finally:
        await srv2.stop()

    # Isolation: nothing above wrote to the shared checkout.
    assert REPO_CONFIG.read_bytes() == repo_config_before, (
        "the HTTP test modified the repository band-plans/_config.toml"
    )
