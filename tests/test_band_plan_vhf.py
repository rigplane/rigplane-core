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

from rigplane.web.band_plan import BandPlanRegistry
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
        assert any(s.get("label") == "SSB" for s in below), (
            "R1 70 cm: 432.350 MHz stays SSB below the beacon band"
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
# Integration across the real HTTP seam (pre-review finding 8a)
# ---------------------------------------------------------------------------
#
# Production server startup (which loads band-plans/ from the repo),
# real routing over real sockets, region selection persisted via the
# config route. One sequential test mutates the repo _config.toml so
# parallel xdist workers cannot race on it; the original bytes are
# restored in ``finally``.


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
async def test_region_selection_over_real_http() -> None:
    """Select regions via POST, read segments/config via GET (finding 8a).

    One sequential pass US -> IARU-R1 -> IARU-R3 -> US, then an unknown
    region across a restart. Failing-first: restoring the pre-review rows
    (R1 beacons at 432.800-433.000, R3 144.300-144.500 SAT) turns the
    per-region assertions red; bypassing startup/routing cannot persist
    the selection to disk.
    """
    original = REPO_CONFIG.read_bytes()
    REPO_CONFIG.write_text(_US_CONFIG)
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0))
    try:
        await srv.start()
        host, port = _addr(srv)

        # US baseline through the real seam.
        segs = _ham_only(await _get_segments_json(host, port, 146_400_000, 147_000_000))
        assert any("146.520" in (s.get("notes") or "") for s in segs), (
            "US startup: no 146.520 calling note on 2 m"
        )
        assert (
            _ham_only(await _get_segments_json(host, port, 432_450_000, 432_450_000))
            == []
        ), "US startup: 432.450 MHz must be a plan gap, not R1 beacons"

        # Region switch via the production config route.
        status, _, raw = await _http_post_json(
            host, port, "/api/v1/band-plan/config", {"region": "IARU-R1"}
        )
        assert status == 200, f"POST region IARU-R1: HTTP {status}"
        assert json.loads(raw)["region"] == "IARU-R1"
        assert 'region = "IARU-R1"' in REPO_CONFIG.read_text(), (
            "region selection was not persisted to _config.toml"
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
    finally:
        REPO_CONFIG.write_bytes(original)
        await srv.stop()

    # Unknown region hand-written to disk: production startup falls back
    # to US data visibly instead of rendering R2 silently (MOR-1861).
    REPO_CONFIG.write_text(
        '# Band plan configuration\n\n[settings]\nregion = "XX-NOWHERE"\n\n'
        "[layers]\nham = true\nbroadcast = true\nutility = true\n"
    )
    srv2 = WebServer(None, WebConfig(host="127.0.0.1", port=0))
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
        REPO_CONFIG.write_bytes(original)
        await srv2.stop()
