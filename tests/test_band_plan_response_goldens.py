"""Band-plan HTTP response goldens, replayed through the real handlers (MOR-3631).

``frontend/src/components/spectrum/__tests__/fixtures/band-plan-responses.json``
holds the bodies the production web server returns for the band-plan routes:
``GET /api/v1/band-plan/layers``, ``GET`` and ``POST /api/v1/band-plan/config``
and ``GET /api/v1/band-plan/segments``. The frontend suites
``BandPlanOverlay.component.test.ts`` and ``SpectrumPanel.component.test.ts``
feed the same file to the real overlay / toolbar / panel as the fetch backend,
so they never rely on hand-written replies.

This module is the named real-handler replay: it builds the file again from
the real ``WebServer`` (production startup, real routing, real sockets, an
isolated copy of ``band-plans/``) and requires it to equal the committed one.
A change to the band-plan data or to those routes fails here until the file is
regenerated and the diff reviewed (run this module with the environment
variable ``UPDATE_BAND_PLAN_GOLDENS=1``).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from test_band_plan_vhf import _http_post_json
from test_web_server import _addr, _http_get

from rigplane.web.server import WebConfig, WebServer

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_PATH = (
    REPO_ROOT
    / "frontend"
    / "src"
    / "components"
    / "spectrum"
    / "__tests__"
    / "fixtures"
    / "band-plan-responses.json"
)

# Fetch windows the overlay requests for the viewports its tests use
# (span +/- 50 % margin): 145.800-146.200 MHz and 5.380-5.390 MHz.
_WINDOWS: dict[str, tuple[int, int]] = {
    "US": (145_600_000, 146_400_000),
    "IARU-R1": (145_600_000, 146_400_000),
    "IARU-R3": (5_375_000, 5_395_000),
}
_UNLOADED_WINDOW = (13_825_000, 14_525_000)

_PROVENANCE = (
    "Real-handler replay: tests/test_band_plan_response_goldens.py builds this "
    "file from the production WebServer; regenerate by running that module "
    "with UPDATE_BAND_PLAN_GOLDENS=1."
)


async def _get_json(host: str, port: int, path: str) -> Any:
    status, _, body = await _http_get(host, port, path)
    assert status == 200, f"GET {path}: HTTP {status}"
    return json.loads(body)


def _segments_path(start: int, end: int) -> str:
    return f"/api/v1/band-plan/segments?start={start}&end={end}"


async def build_goldens(plans_dir: Path, empty_dir: Path) -> dict[str, Any]:
    """Drive the real server over HTTP and collect the response bodies."""
    golden: dict[str, Any] = {
        "_provenance": _PROVENANCE,
        "config": {},
        "configPost": {},
        "segments": {},
    }
    plans_dir.joinpath("_config.toml").write_text(
        "# Band plan configuration\n\n[settings]\n"
        'region = "US"\n\n'
        "[layers]\nham = true\nbroadcast = true\nutility = true\n"
    )
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0, band_plans_dir=plans_dir))
    try:
        await srv.start()
        host, port = _addr(srv)
        golden["layers"] = await _get_json(host, port, "/api/v1/band-plan/layers")
        for region, (start, end) in _WINDOWS.items():
            status, _, raw = await _http_post_json(
                host, port, "/api/v1/band-plan/config", {"region": region}
            )
            assert status == 200, f"POST region {region}: HTTP {status}"
            golden["configPost"][region] = json.loads(raw)
            golden["config"][region] = await _get_json(
                host, port, "/api/v1/band-plan/config"
            )
            golden["segments"][f"{region}|{start}-{end}"] = await _get_json(
                host, port, _segments_path(start, end)
            )
    finally:
        await srv.stop()

    # An install without band-plan data: the plans directory holds no plans.
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0, band_plans_dir=empty_dir))
    try:
        await srv.start()
        host, port = _addr(srv)
        start, end = _UNLOADED_WINDOW
        golden["segments"][f"unloaded|{start}-{end}"] = await _get_json(
            host, port, _segments_path(start, end)
        )
    finally:
        await srv.stop()
    return golden


@pytest.mark.asyncio
async def test_committed_goldens_match_the_real_handlers(tmp_path: Path) -> None:
    plans = tmp_path / "band-plans"
    plans.mkdir()
    for src in (REPO_ROOT / "band-plans").glob("*.toml"):
        shutil.copy(src, plans / src.name)
    empty = tmp_path / "no-plans"
    empty.mkdir()

    produced = await build_goldens(plans, empty)

    if os.environ.get("UPDATE_BAND_PLAN_GOLDENS") == "1":
        GOLDEN_PATH.write_text(
            json.dumps(produced, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    committed = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert produced == committed, (
        "band-plan response goldens are stale; regenerate them (see the module "
        "docstring) and review the diff"
    )


def test_goldens_pin_the_scenarios_the_frontend_relies_on() -> None:
    """The frontend tests need these shapes; a regeneration must keep them."""
    committed = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    segs = committed["segments"]
    us = segs["US|145600000-146400000"]
    r1 = segs["IARU-R1|145600000-146400000"]
    # Same window, different plan per region.
    assert us["plansLoaded"] is True and r1["plansLoaded"] is True
    assert {s["notes"] for s in us["segments"]} != {s["notes"] for s in r1["segments"]}
    # An authoritative empty answer: plans loaded, nothing in the window.
    assert segs["IARU-R3|5375000-5395000"] == {"segments": [], "plansLoaded": True}
    # No plan data on the install: empty and explicitly not authoritative.
    assert segs["unloaded|13825000-14525000"] == {
        "segments": [],
        "plansLoaded": False,
    }
    assert committed["config"]["IARU-R1"]["region"] == "IARU-R1"
    assert len(committed["config"]["US"]["availableRegions"]) > 1
