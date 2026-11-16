"""4 m (70 MHz) band rows as plain profile data, shown in every region.

MOR-4300. Every IC-7300 and IC-7300MK2 receives 70.000-70.500 MHz, so
the 4 m band row is plain profile data with no display gate: the band
button is shown in every band-plan region, and the radio itself decides
whether it transmits on 70 MHz (MOR-4303 — only the European versions
do). No CI-V ``bsr_code`` exists for 70 MHz, so the button tunes
``default_hz`` directly.

Covers, in order: shipped profile data, and the band where a client
enters it — a real ``WebServer`` on real sockets, the serial radio Core
builds from the CLI options over the testkit ``CivSerialFake`` (only
the OS open step is replaced; see ``tests/testkit/test_memory_recall_wire.py``
for the harness), HTTP ``GET /api/v1/capabilities``, ``POST
/api/v1/band-plan/config``, ``GET /api/v1/band-plan/segments`` and
``POST /api/v1/commands`` against the real routing and command queue.

Failing-first: on origin/main no profile carries a 4 m row, so the
default-region capabilities assertion (the 4 m selector entry and
``txBands`` row) fails before anything else.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from rigplane.backends.factory import create_radio
from rigplane.cli import _build_backend_config, _build_parser
from rigplane.core import serial_open
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.profiles import resolve_radio_profile
from rigplane.profiles.rig_loader import discover_rigs
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.serial import CivSerialFake
from support.command_consumer import run_command_consumer
from test_band_plan_vhf import (
    _copy_plans,
    _get_segments_json,
    _ham_only,
    _http_post_json,
    _write_region,
)
from test_web_server import _addr, _http_get
from test_web_split_writes import _request

RIGS_DIR = Path(__file__).resolve().parents[1] / "rigs"

_FOUR_METRE_START = 70_000_000
_FOUR_METRE_END = 70_500_000
_FOUR_METRE_DEFAULT = 70_200_000

# The IC-7300 station (see ``_station`` below).
_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened
_ANSWER_WINDOW_S = 0.2  # compressed CI-V answer window (production: 2.0 s)
_VFO_FREQ_HZ = 14_074_000

# The five IARU-R1 4 m overlay segments (band-plans/iaru-r1.toml).
_FOUR_METRE_R1_SEGMENTS = [
    (70_000_000, 70_090_000, "BCN"),
    (70_090_000, 70_100_000, "BCN"),
    (70_100_000, 70_250_000, "SSB"),
    (70_250_000, 70_294_000, "AM/FM"),
    (70_294_000, 70_500_000, "FM"),
]


def _band_in(profile, name: str):
    for freq_range in profile.freq_ranges:
        for band in freq_range.bands:
            if band.name == name:
                return band
    return None


def _payload_bands(data: dict) -> list[dict]:
    return [band for freq_range in data["freqRanges"] for band in freq_range["bands"]]


_FOUR_METRE_TX_ROW = {
    "name": "4m",
    "start": _FOUR_METRE_START,
    "end": _FOUR_METRE_END,
    "maxPowerW": 50,
}


# ── Shipped profile data ───────────────────────────────────────────


class TestShippedFourMetreRows:
    @pytest.mark.parametrize("model", ["IC-7300", "IC-7300MK2"])
    def test_icom_four_metre_row(self, model: str):
        """Edges, default tuning frequency, rated power and the absent
        ``bsr_code`` (no 70 MHz band code exists, so the band button
        tunes ``default_hz`` directly)."""
        band = _band_in(resolve_radio_profile(model=model), "4m")
        assert band is not None, f"{model}: no 4m band row"
        assert band.start == _FOUR_METRE_START
        assert band.end == _FOUR_METRE_END
        assert band.default == _FOUR_METRE_DEFAULT
        assert band.max_power_w == 50
        assert band.bsr_code is None

    def test_ftx1_four_metre_row_is_unchanged(self):
        """The FTX-1 4 m row predates MOR-4300 and keeps its own CAT
        band code; this PR does not change it."""
        band = _band_in(resolve_radio_profile(model="FTX-1"), "4m")
        assert band is not None
        assert band.start == _FOUR_METRE_START
        assert band.end == _FOUR_METRE_END
        assert band.default == _FOUR_METRE_DEFAULT
        assert band.bsr_code == 0x0B

    def test_no_other_profile_overlaps_four_metres(self):
        """No other shipped profile declares a TX band overlapping
        70.0-70.5 MHz. (IC-705's ``Gen`` band spans it but is ``rx_only``,
        so it grants nothing; ``rx_only`` rows are excluded here.)"""
        overlapping = {
            (model, band.name)
            for model, config in sorted(discover_rigs(RIGS_DIR).items())
            for freq_range in config.to_profile().freq_ranges
            for band in freq_range.bands
            if not band.rx_only
            and band.start < _FOUR_METRE_END
            and band.end > _FOUR_METRE_START
        }
        assert overlapping == {
            ("IC-7300", "4m"),
            ("IC-7300MK2", "4m"),
            ("FTX-1", "4m"),
        }


# ── End to end: a client against the real server ───────────────────


@contextlib.asynccontextmanager
async def _station(
    monkeypatch: pytest.MonkeyPatch, plans: Path
) -> AsyncIterator[tuple[WebServer, Any, CivSerialFake, list[Any], asyncio.Event]]:
    """The memory-recall harness (``test_memory_recall_wire._station``)
    over a band-plans copy: a real IC-7300 built from the CLI options on
    the testkit ``CivSerialFake``, a real ``WebServer`` on a real socket,
    and a queue consumer that executes each submitted command through
    the poller (the role the running poller loop plays in production)."""
    fake = CivSerialFake(model="IC-7300", frequency_hz=_VFO_FREQ_HZ)

    async def opener(*, url: str, baudrate: int, **kw: Any) -> Any:
        return await fake.opener(url=url, baudrate=baudrate, **kw)

    monkeypatch.setattr(serial_open, "_open_with_idle_lines", opener)
    args = _build_parser().parse_args(
        ["--model", "IC-7300", "--serial-port", _DEVICE, "status"]
    )
    radio = create_radio(await _build_backend_config(args))
    radio._civ_get_timeout = _ANSWER_WINDOW_S
    radio._enumerate_serial_ports_fn = lambda: []  # no OS port enumeration
    await asyncio.wait_for(radio.connect(), 30)
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, band_plans_dir=plans))
    # Production startup (``web_startup._start_web_server``) loads the
    # configured band-plan directory before binding; this harness binds
    # the socket itself, so the same startup load happens here.
    web._band_plan.load(plans)  # noqa: SLF001
    poller = RadioPoller(radio, web.command_queue, state_store=web.command_state_store)
    web.command_state_store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=asyncio.get_running_loop().time(),
            max_age=60,
        )
    )
    web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)
    entries: list[Any] = []
    executed = asyncio.Event()

    consumer = asyncio.create_task(
        run_command_consumer(
            web.command_queue,
            poller._execute_queued_entry,
            entries=entries,
            executed=executed,
        )
    )
    try:
        yield web, radio, fake, entries, executed
    finally:
        web._server.close()
        await web._server.wait_closed()
        web._server = None
        tasks = (consumer, *tuple(web._client_tasks))
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await radio.disconnect()


async def _capabilities_over_http(web: WebServer) -> dict:
    host, port = _addr(web)
    status, _, body = await _http_get(host, port, "/api/v1/capabilities")
    assert status == 200, f"GET capabilities: HTTP {status}"
    return json.loads(body)


async def _post_region(web: WebServer, region: str) -> dict:
    host, port = _addr(web)
    status, _, raw = await _http_post_json(
        host, port, "/api/v1/band-plan/config", {"region": region}
    )
    assert status == 200, f"POST region {region}: HTTP {status}"
    reply = json.loads(raw)
    assert reply["region"] == region
    return reply


class TestFourMetreOverRealHttp:
    """MOR-4300 from the client's seat, in the default (US) region: the
    4 m selector entry and ``txBands`` row are plain profile data, so
    they are served in every region; the IARU-R1 overlay segments follow
    the band-plan region (MOR-4303)."""

    @pytest.mark.asyncio
    async def test_default_region_shows_the_band_and_tx_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "US")
        async with _station(monkeypatch, plans) as (web, _r, _f, _e, _x):
            data = await _capabilities_over_http(web)
            band = next((b for b in _payload_bands(data) if b["name"] == "4m"), None)
            assert band == {
                "name": "4m",
                "start": _FOUR_METRE_START,
                "end": _FOUR_METRE_END,
                "default": _FOUR_METRE_DEFAULT,
            }
            assert _FOUR_METRE_TX_ROW in (data["txBands"] or [])

    @pytest.mark.asyncio
    async def test_band_button_default_tunes_the_radio(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The UI's band-button path for a band without ``bsrCode``: the
        frontend's ``onBandSelect`` jumps the tuning accumulator, which
        emits a ``set_freq`` intent — the same command this test submits
        through ``POST /api/v1/commands``. The published ``default``
        (70.200 MHz) must land on the simulated radio."""
        plans = _copy_plans(tmp_path)
        _write_region(plans, "US")
        async with _station(monkeypatch, plans) as (
            web,
            radio,
            fake,
            entries,
            executed,
        ):
            data = await _capabilities_over_http(web)
            band = next((b for b in _payload_bands(data) if b["name"] == "4m"), None)
            assert band is not None and "bsrCode" not in band
            target = band["default"]

            status, result = await _request(
                web, "http", "set_freq", {"freq": target, "receiver": 0}
            )
            await asyncio.wait_for(executed.wait(), 5)

            assert status == 200
            assert result["ok"] is True
            assert len(entries) == 1
            assert fake.frequency_hz == target
            assert await radio.get_freq() == target

    @pytest.mark.asyncio
    async def test_iaru_r1_overlay_has_the_four_metre_segments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "US")
        async with _station(monkeypatch, plans) as (web, _r, _f, _e, _x):
            await _post_region(web, "IARU-R1")
            host, port = _addr(web)
            segments = _ham_only(
                await _get_segments_json(host, port, _FOUR_METRE_START, _FOUR_METRE_END)
            )
            assert [(s["start"], s["end"], s["label"]) for s in segments] == (
                _FOUR_METRE_R1_SEGMENTS
            )
