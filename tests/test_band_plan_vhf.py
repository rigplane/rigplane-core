"""Region-aware VHF/UHF band plans (MOR-3631).

Covers:
- Loader reads the region ham files for every region (US, IARU-R1,
  IARU-R2, IARU-R3) and returns VHF/UHF segments for 6 m, 2 m, 70 cm,
  23 cm spans, including calling frequencies as point data.
- Each ham file cites its source in [meta] (loader test asserts presence).
- Unset or unknown region is explicit: logged and visible via the config
  route (requested vs effective + reason), never a silent identical
  rendering of Region 2 (MOR-1861).
- Integration across the real HTTP seam: _serve_band_plan_segments and
  _serve_band_plan_config.

Failing-first: on old code the highest ham frequency is 29.7 MHz
(no VHF segments), there is no iaru-r3.toml, _sanitize_freq clamps to
500 MHz (23 cm unrepresentable), and unknown regions fall back to US
silently with no requested/fallback fields.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rigplane.web.band_plan import BandPlanRegistry

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

    @pytest.mark.parametrize("region", ALL_REGIONS)
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
# Integration across the HTTP seam
# ---------------------------------------------------------------------------


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def get_extra_info(self, name: str, default=None):
        if name == "peername":
            return ("127.0.0.1", 55555)
        return default

    @property
    def response_body(self) -> dict:
        body = self.buffer.split(b"\r\n\r\n", 1)[1]
        return json.loads(body)


def _server_with_plans(plans_dir: Path):
    from rigplane.web.server import WebConfig, WebServer

    srv = WebServer(MagicMock(), WebConfig(host="127.0.0.1", port=0))
    reg = BandPlanRegistry()
    reg.load(plans_dir)
    srv._band_plan = reg  # noqa: SLF001
    return srv


@pytest.mark.asyncio
async def test_segments_api_returns_two_m(tmp_path: Path) -> None:
    plans = _copy_plans(tmp_path)
    _write_region(plans, "US")
    srv = _server_with_plans(plans)
    writer = _FakeWriter()
    await srv._serve_band_plan_segments(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        {"start": ["144000000"], "end": ["148000000"]},
    )
    body = writer.response_body
    assert any(s["layer"] == "ham" for s in body.get("segments", [])), (
        "segments API returned no ham on 2 m"
    )


@pytest.mark.asyncio
async def test_segments_api_returns_calling(tmp_path: Path) -> None:
    plans = _copy_plans(tmp_path)
    _write_region(plans, "US")
    srv = _server_with_plans(plans)
    writer = _FakeWriter()
    await srv._serve_band_plan_segments(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        {"start": ["146520000"], "end": ["146520000"]},
    )
    body = writer.response_body
    assert any(
        "146.520" in (s.get("notes") or "")
        for s in body.get("segments", [])
        if s["layer"] == "ham"
    ), "segments API returned no 146.520 calling note"


@pytest.mark.asyncio
async def test_config_api_reports_fallback(tmp_path: Path) -> None:
    plans = _copy_plans(tmp_path)
    _write_region(plans, "XX-NOWHERE")
    srv = _server_with_plans(plans)
    writer = _FakeWriter()
    await srv._serve_band_plan_config(writer)  # noqa: SLF001
    body = writer.response_body
    assert body["region"] == "US"
    assert body["requestedRegion"] == "XX-NOWHERE"
    assert body["fallbackReason"]
    assert "XX-NOWHERE" in body["fallbackReason"]


@pytest.mark.asyncio
async def test_config_api_explicit_has_no_fallback(tmp_path: Path) -> None:
    plans = _copy_plans(tmp_path)
    _write_region(plans, "IARU-R1")
    srv = _server_with_plans(plans)
    writer = _FakeWriter()
    await srv._serve_band_plan_config(writer)  # noqa: SLF001
    body = writer.response_body
    assert body["region"] == "IARU-R1"
    assert body["requestedRegion"] == "IARU-R1"
    assert body["fallbackReason"] is None
