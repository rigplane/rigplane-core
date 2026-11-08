"""4 m (70 MHz) band rows as profile data, shown per band-plan region.

MOR-4300. A band row may declare ``regions`` — the band-plan region ids
(``band-plans/_config.toml`` ``[settings]`` region) its band-selector
entry is shown in. Empty (every existing row) applies in every region;
the 4 m rows declare ``["IARU-R1"]`` because only European/UK versions
transmit 70.000-70.500 MHz and the market variant is not reported by
CI-V, so the user's band-plan region decides what is displayed. The
``regions`` tag is a display gate, not a TX gate (MOR-4303): it never
affects ``txBands`` — the 4 m row is published there in every region,
like any other band, and the radio itself decides whether it transmits
on 70 MHz.

Covers, in order: loader parsing/validation, shipped profile data, and
the region flow end to end where a client enters — a real ``WebServer``
on real sockets, the serial radio Core builds from the CLI options over
the testkit ``CivSerialFake`` (only the OS open step is replaced; see
``tests/testkit/test_memory_recall_wire.py`` for the harness), HTTP
``GET /api/v1/capabilities``, ``POST /api/v1/band-plan/config``, ``GET
/api/v1/band-plan/segments`` and ``POST /api/v1/commands`` against the
real routing and command queue.

Failing-first: on origin/main no profile carries a 4 m row, so the US
``txBands`` assertion (the 4 m row with ``maxPowerW`` 50) fails before
anything else; the IARU-R1 selector-entry and segment assertions fail
there for the same reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
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
from rigplane.profiles.rig_loader import RigLoadError, discover_rigs, load_rig
from rigplane.web.band_plan import _REGION_HAM_FILE
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.serial import CivSerialFake
from test_band_plan_vhf import (
    _copy_plans,
    _get_segments_json,
    _ham_only,
    _http_post_json,
    _write_region,
)
from test_rig_loader import _MINIMAL_TOML, _write_toml
from test_web_server import _addr, _http_get
from test_web_split_writes import _request

REPO_ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = REPO_ROOT / "rigs"

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


# ── Loader: optional per-band ``regions`` ───────────────────────────


class TestRegionsParsing:
    @staticmethod
    def _rig_with_regions(tmp_path: Path, regions: str) -> Path:
        return _write_toml(
            tmp_path,
            _MINIMAL_TOML.replace(
                'label = "HF"\nstart_hz = 30000\nend_hz = 60000000',
                'label = "HF"\nstart_hz = 30000\nend_hz = 60000000\n\n'
                "[[freq_ranges.ranges.bands]]\n"
                'name = "160m"\n'
                "start_hz = 1800000\n"
                "end_hz = 2000000\n"
                "default_hz = 1825000\n"
                f"{regions}",
            ),
        )

    def test_omitted_regions_default_empty(self, tmp_path: Path):
        profile = load_rig(_write_toml(tmp_path, _MINIMAL_TOML)).to_profile()
        assert all(band.regions == () for band in profile.freq_ranges[0].bands)

    def test_regions_parse_into_bandinfo(self, tmp_path: Path):
        p = self._rig_with_regions(tmp_path, 'regions = ["IARU-R1"]')
        profile = load_rig(p).to_profile()
        band = profile.freq_ranges[0].bands[0]
        assert band.regions == ("IARU-R1",)

    @pytest.mark.parametrize(
        "regions",
        [
            'regions = "IARU-R1"',  # not a list
            "regions = []",  # empty list
            'regions = ["IARU-R1", 1]',  # non-string item
        ],
        ids=["non-list", "empty-list", "non-string-item"],
    )
    def test_regions_rejects_bad_values(self, tmp_path: Path, regions: str):
        p = self._rig_with_regions(tmp_path, regions)
        with pytest.raises(
            RigLoadError,
            match=r"bands\[0\]\.regions must be a non-empty list of non-empty "
            r"strings \(band '160m'\)",
        ):
            load_rig(p)


# ── Shipped profile data ───────────────────────────────────────────


class TestShippedFourMetreRows:
    @pytest.mark.parametrize("model", ["IC-7300", "IC-7300MK2", "FTX-1"])
    def test_four_metre_row_is_iaru_r1_only(self, model: str):
        profile = resolve_radio_profile(model=model)
        band = _band_in(profile, "4m")
        assert band is not None, f"{model}: no 4m band row"
        assert band.start == _FOUR_METRE_START
        assert band.end == _FOUR_METRE_END
        assert band.default == _FOUR_METRE_DEFAULT
        assert band.regions == ("IARU-R1",)

    @pytest.mark.parametrize("model", ["IC-7300", "IC-7300MK2"])
    def test_icom_four_metre_rated_power(self, model: str):
        band = _band_in(resolve_radio_profile(model=model), "4m")
        assert band is not None
        assert band.max_power_w == 50

    def test_every_shipped_regions_value_is_a_known_region(self):
        """A typo'd region id would silently hide the band's selector
        entry in every region; every declared value must be a key of
        the band-plan registry's region table."""
        for filename, config in sorted(discover_rigs(RIGS_DIR).items()):
            profile = config.to_profile()
            for freq_range in profile.freq_ranges:
                for band in freq_range.bands:
                    for region in band.regions:
                        assert region in _REGION_HAM_FILE, (filename, band.name)

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
) -> AsyncIterator[
    tuple[WebServer, Any, CivSerialFake, list[Any], asyncio.Event]
]:
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

    async def consume() -> None:
        while True:
            await web.command_queue.wait(2)
            entry = web.command_queue.take_entry()
            assert entry is not None
            entries.append(entry)
            await poller._execute_queued_entry(entry)
            executed.set()

    consumer = asyncio.create_task(consume())
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


class TestFourMetreRegionFlowOverRealHttp:
    """MOR-4300 from the client's seat: the band-selector entry follows
    the band-plan region through the real routes, while ``txBands`` and
    the tune range do not (MOR-4303)."""

    @pytest.mark.asyncio
    async def test_us_hides_the_selector_row_not_the_tx_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "US")
        async with _station(monkeypatch, plans) as (web, _r, _f, _e, _x):
            data = await _capabilities_over_http(web)
            assert not any(b["name"] == "4m" for b in _payload_bands(data))
            assert _FOUR_METRE_TX_ROW in (data["txBands"] or [])
            # Still tunable for RX: the VHF RX receive range covers
            # 70.2 MHz, and no band row covers 70.6 MHz (US).
            assert any(
                r["start"] <= _FOUR_METRE_DEFAULT <= r["end"]
                for r in data["freqRanges"]
            )
            assert not any(
                b["start"] <= 70_600_000 <= b["end"] for b in _payload_bands(data)
            )

    @pytest.mark.asyncio
    async def test_iaru_r1_shows_the_selector_entry_and_segments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "US")
        async with _station(monkeypatch, plans) as (web, _r, _f, _e, _x):
            await _post_region(web, "IARU-R1")

            data = await _capabilities_over_http(web)
            band = next(
                (b for b in _payload_bands(data) if b["name"] == "4m"), None
            )
            assert band == {
                "name": "4m",
                "start": _FOUR_METRE_START,
                "end": _FOUR_METRE_END,
                "default": _FOUR_METRE_DEFAULT,
            }
            assert _FOUR_METRE_TX_ROW in (data["txBands"] or [])

            host, port = _addr(web)
            segments = _ham_only(
                await _get_segments_json(
                    host, port, _FOUR_METRE_START, _FOUR_METRE_END
                )
            )
            assert [(s["start"], s["end"], s["label"]) for s in segments] == (
                _FOUR_METRE_R1_SEGMENTS
            )

            # Back to US: the band-selector entry is gone again; the TX
            # row stays (MOR-4303).
            await _post_region(web, "US")
            data = await _capabilities_over_http(web)
            assert not any(b["name"] == "4m" for b in _payload_bands(data))
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
        async with _station(monkeypatch, plans) as (web, radio, fake, entries, executed):
            await _post_region(web, "IARU-R1")
            data = await _capabilities_over_http(web)
            band = next(
                (b for b in _payload_bands(data) if b["name"] == "4m"), None
            )
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


class TestFtx1DisplayGateOverRealHttp:
    """The FTX-1 4 m row has a ``bsrCode``; its selector entry follows
    the region the same way, over a radio-less server started through
    production ``start()`` (the ``test_band_plan_vhf`` pattern)."""

    @pytest.mark.asyncio
    async def test_selector_entry_follows_region_tx_row_does_not(
        self, tmp_path: Path
    ) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "US")
        srv = WebServer(
            None,
            WebConfig(
                host="127.0.0.1",
                port=0,
                band_plans_dir=plans,
                radio_model="FTX-1",
            ),
        )
        try:
            await srv.start()
            host, port = _addr(srv)

            async def caps() -> dict:
                status, _, body = await _http_get(host, port, "/api/v1/capabilities")
                assert status == 200
                return json.loads(body)

            us = await caps()
            assert not any(b["name"] == "4m" for b in _payload_bands(us))
            assert any(b["name"] == "4m" for b in (us["txBands"] or []))
            # The tune/receive range itself is not region-gated.
            assert any(
                r["start"] == _FOUR_METRE_START and r["end"] == _FOUR_METRE_END
                for r in us["freqRanges"]
            )

            status, _, raw = await _http_post_json(
                host, port, "/api/v1/band-plan/config", {"region": "IARU-R1"}
            )
            assert status == 200

            r1 = await caps()
            band = next((b for b in _payload_bands(r1) if b["name"] == "4m"), None)
            assert band is not None and band["bsrCode"] == 0x0B
            assert any(b["name"] == "4m" for b in (r1["txBands"] or []))
        finally:
            await srv.stop()


# ── Mounted-UI fixture: region-dependent capabilities documents ────

_4M_REGION_FIXTURE = (
    REPO_ROOT
    / "frontend"
    / "src"
    / "components-v2"
    / "controls"
    / "__tests__"
    / "fixtures"
    / "ic7300-4m-region-capabilities.json"
)

# ``providerGeneration`` (the radio session's generation counter) is the
# only volatile field in the served body — everything else is derived
# from the profile and the band-plan region — so the fixture pins it to
# 0 before comparing. No timestamps are served.
_4M_FIXTURE_GENERATION = 0


def _pin_volatile(doc: dict) -> dict:
    doc["providerGeneration"] = _4M_FIXTURE_GENERATION
    return doc


class TestFourMetreRegionCapabilitiesFixture:
    """The mounted BandSelector suite
    (``frontend/src/components-v2/controls/__tests__/BandSelector.isolated.test.ts``)
    proves the region-dependent 4 m button against these two bodies. This
    test rebuilds them from the real IC-7300 handler end to end and
    requires the committed fixture to equal what the server serves
    (regenerate with ``UPDATE_4M_REGION_GOLDENS=1`` and review the diff)."""

    @pytest.mark.asyncio
    async def test_committed_fixture_matches_the_real_handler(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plans = _copy_plans(tmp_path)
        _write_region(plans, "US")
        async with _station(monkeypatch, plans) as (web, _r, _f, _e, _x):
            us = _pin_volatile(await _capabilities_over_http(web))
            await _post_region(web, "IARU-R1")
            r1 = _pin_volatile(await _capabilities_over_http(web))

        produced = {"US": us, "IARU-R1": r1}
        if os.environ.get("UPDATE_4M_REGION_GOLDENS") == "1":
            _4M_REGION_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
            _4M_REGION_FIXTURE.write_text(
                '{"US": '
                + json.dumps(produced["US"], sort_keys=True)
                + ',\n"IARU-R1": '
                + json.dumps(produced["IARU-R1"], sort_keys=True)
                + "}\n",
                encoding="utf-8",
            )
        committed = json.loads(_4M_REGION_FIXTURE.read_text(encoding="utf-8"))
        assert produced == committed, (
            "the 4 m region capabilities fixture is stale; regenerate it "
            "(run this module with UPDATE_4M_REGION_GOLDENS=1) and review "
            "the diff"
        )

    def test_fixture_pins_the_scenarios_the_frontend_relies_on(self) -> None:
        committed = json.loads(_4M_REGION_FIXTURE.read_text(encoding="utf-8"))
        us, r1 = committed["US"], committed["IARU-R1"]
        # US: no 4 m selector entry, the other bands stay.
        assert not any(b["name"] == "4m" for b in _payload_bands(us))
        assert any(b["name"] == "6m" for b in _payload_bands(us))
        # IARU-R1: the 4 m entry with its published default, no bsrCode.
        band = next(b for b in _payload_bands(r1) if b["name"] == "4m")
        assert band["default"] == _FOUR_METRE_DEFAULT
        assert "bsrCode" not in band
        # The TX row is unconditional in both regions (MOR-4303).
        assert _FOUR_METRE_TX_ROW in (us["txBands"] or [])
        assert _FOUR_METRE_TX_ROW in (r1["txBands"] or [])
