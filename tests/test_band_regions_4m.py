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

Covers, in order: loader parsing/validation, shipped profile data, the
capabilities payload display gate (``freqRanges`` bands), and the
simulated IC-7300 over the profile fake (``tests/testkit/ic7300_fakes.py:
ic7300_profile_fake`` through the documented ``SerialCivLink`` opener seam,
the lightest harness that really drives the simulated radio — no OS port,
no subprocess).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rigplane.profiles import resolve_radio_profile
from rigplane.profiles.rig_loader import RigLoadError, discover_rigs, load_rig
from rigplane.web.band_plan import _REGION_HAM_FILE

from test_band_plan_vhf import _copy_plans, _write_region
from test_rig_loader import _MINIMAL_TOML, _write_toml
from test_web_capability_guards import (
    _FakeWriter,
    _make_radio,
    _parse_json_body,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = REPO_ROOT / "rigs"

_FOUR_METRE_START = 70_000_000
_FOUR_METRE_END = 70_500_000

# These suites also drive ``RadioPoller._execute`` directly to exercise
# dispatch bodies; the RF premise is the conftest fixture's (see its
# docstring — the seat's own matrix lives in test_radio_poller_tx_interlock.py).
pytestmark = pytest.mark.usefixtures("observed_rx_dispatch_premise")


def _band_in(profile, name: str):
    for freq_range in profile.freq_ranges:
        for band in freq_range.bands:
            if band.name == name:
                return band
    return None


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
        assert band.default == 70_200_000
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


# ── Capabilities payload: the band-selector display gate ───────────


async def _capabilities(model: str, region: str, plans_root: Path) -> dict:
    from rigplane.web.server import WebConfig, WebServer

    plans_root.mkdir(parents=True)
    plans = _copy_plans(plans_root)
    _write_region(plans, region)
    srv = WebServer(
        _make_radio(model),
        WebConfig(host="127.0.0.1", port=0, band_plans_dir=plans),
    )
    # The registry loads at server start(); these tests call
    # _serve_capabilities directly, so load it the same way here.
    srv._band_plan.load(plans)  # noqa: SLF001
    writer = _FakeWriter()
    await srv._serve_capabilities(writer)  # noqa: SLF001
    return _parse_json_body(writer)


def _payload_bands(data: dict) -> list[dict]:
    return [band for freq_range in data["freqRanges"] for band in freq_range["bands"]]


_FOUR_METRE_TX_ROW = {
    "name": "4m",
    "start": _FOUR_METRE_START,
    "end": _FOUR_METRE_END,
    "maxPowerW": 50,
}


class TestCapabilitiesDisplayGate:
    @pytest.mark.asyncio
    async def test_ic7300_iaru_r1_shows_the_band_button(self, tmp_path: Path):
        data = await _capabilities("IC-7300", "IARU-R1", tmp_path / "r1")
        band = next((b for b in _payload_bands(data) if b["name"] == "4m"), None)
        assert band == {
            "name": "4m",
            "start": _FOUR_METRE_START,
            "end": _FOUR_METRE_END,
            "default": 70_200_000,
        }
        # MOR-4303: the TX row is not region-gated — published in every
        # region, like any other band.
        assert _FOUR_METRE_TX_ROW in (data["txBands"] or [])

    @pytest.mark.asyncio
    async def test_ic7300_us_hides_the_button_not_the_tx_row(self, tmp_path: Path):
        """MOR-4303: ``regions`` is display-only — the band-selector
        entry disappears outside IARU-R1 while ``txBands`` keeps the
        4 m row; the radio itself decides whether it transmits there."""
        data = await _capabilities("IC-7300", "US", tmp_path / "us")
        assert not any(b["name"] == "4m" for b in _payload_bands(data))
        assert _FOUR_METRE_TX_ROW in (data["txBands"] or [])
        # Still tunable for RX: the VHF RX receive range covers 70.2 MHz.
        assert any(r["start"] <= 70_200_000 <= r["end"] for r in data["freqRanges"])
        # 70.6 MHz is inside the receive range but covered by no band row.
        assert not any(
            b["start"] <= 70_600_000 <= b["end"] for b in _payload_bands(data)
        )

    @pytest.mark.asyncio
    async def test_ftx1_button_follows_region_tx_row_does_not(self, tmp_path: Path):
        r1 = await _capabilities("FTX-1", "IARU-R1", tmp_path / "r1")
        band = next((b for b in _payload_bands(r1) if b["name"] == "4m"), None)
        assert band is not None and band["bsrCode"] == 0x0B
        assert any(b["name"] == "4m" for b in r1["txBands"])

        us = await _capabilities("FTX-1", "US", tmp_path / "us")
        assert not any(b["name"] == "4m" for b in _payload_bands(us))
        assert any(b["name"] == "4m" for b in (us["txBands"] or []))
        # The tune/receive range itself is not region-gated.
        assert any(
            r["start"] == _FOUR_METRE_START and r["end"] == _FOUR_METRE_END
            for r in us["freqRanges"]
        )


# ── Simulator: the simulated IC-7300 over the profile fake ─────────


@pytest.fixture
def simulated_ic7300():
    from rigplane.backends.ic7300 import Ic7300SerialRadio
    from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink

    from testkit.ic7300_fakes import ic7300_profile_fake

    fake = ic7300_profile_fake()
    link = SerialCivLink(
        device="civ-serial-fake",
        open_serial_connection=fake.open_serial_connection,
    )
    radio = Ic7300SerialRadio(
        device="civ-serial-fake",
        civ_link=link,
        _enumerate_serial_ports_fn=lambda: [],
    )
    return fake, radio


class TestSimulatedIc7300FourMetre:
    @pytest.mark.asyncio
    async def test_band_button_path_lands_on_70200(self, simulated_ic7300):
        """The UI's band-button path for a band without ``bsrCode`` is an
        absolute frequency write (``set_freq`` intent → the poller's
        ``SetFreq`` arm); it must end with the simulated radio on the
        band's default, 70.200 MHz."""
        from rigplane.runtime._poller_types import CommandQueue, SetFreq
        from rigplane.web.radio_poller import RadioPoller

        fake, radio = simulated_ic7300
        poller = RadioPoller(radio, CommandQueue())
        try:
            await asyncio.wait_for(radio.connect(), 20)
            await poller._execute(SetFreq(70_200_000, receiver=0))  # noqa: SLF001
            assert fake.frequency_hz == 70_200_000
        finally:
            await radio.disconnect()

    @pytest.mark.asyncio
    async def test_above_receive_coverage_is_refused(self, simulated_ic7300):
        """75 MHz is outside every declared receive range: the existing
        receive-range check refuses it before anything reaches the wire."""
        from rigplane.core.state_pipeline_contracts import (
            FieldPath,
            Observation,
            SourceMetadata,
        )
        from rigplane.core.state_store import StateStore
        from rigplane.exceptions import CommandError
        from rigplane.runtime._poller_types import CommandQueue, SetFreq, SetVfoFreq
        from rigplane.web.radio_poller import RadioPoller

        fake, radio = simulated_ic7300
        store = StateStore()
        store.begin_provider_generation()
        # A fixed poller clock (never a real-clock read, MOR-4075): the
        # observation below is stamped on the same 0.0 base and stays FRESH
        # for the identity check the refusal path runs past.
        store.apply(
            Observation(
                path=FieldPath.active_slot("0"),
                value="A",
                source=SourceMetadata(
                    source="command_response", provider="vfo_binding"
                ),
                timestamp_monotonic=0.0,
                max_age=5,
                provider_generation=store.provider_generation,
            )
        )
        poller = RadioPoller(
            radio, CommandQueue(), state_store=store, clock=lambda: 0.0
        )
        try:
            await asyncio.wait_for(radio.connect(), 20)
            await poller._execute(SetFreq(70_200_000, receiver=0))  # noqa: SLF001
            assert fake.frequency_hz == 70_200_000
            seen_frames = len(fake.commands)

            with pytest.raises(CommandError, match="outside profile receive ranges"):
                await poller._execute(  # noqa: SLF001
                    SetVfoFreq(
                        freq=75_000_000,
                        receiver=0,
                        slot="A",
                        expected_active_slot="A",
                        provider_generation=store.provider_generation,
                    )
                )
            assert fake.frequency_hz == 70_200_000
            assert len(fake.commands) == seen_frames
        finally:
            await radio.disconnect()

    @pytest.mark.asyncio
    async def test_70600_tunable_outside_the_4m_band_row(self, simulated_ic7300):
        """70.6 MHz lies inside the VHF RX receive range (tunable) but
        outside the 4 m band row: no band row of the profile covers it."""
        from rigplane.runtime._poller_types import CommandQueue, SetFreq
        from rigplane.web.radio_poller import RadioPoller

        fake, radio = simulated_ic7300
        poller = RadioPoller(radio, CommandQueue())
        try:
            await asyncio.wait_for(radio.connect(), 20)
            await poller._execute(SetFreq(70_600_000, receiver=0))  # noqa: SLF001
            assert fake.frequency_hz == 70_600_000

            profile = resolve_radio_profile(model="IC-7300")
            bands = [
                band for freq_range in profile.freq_ranges for band in freq_range.bands
            ]
            assert not any(band.start <= 70_600_000 <= band.end for band in bands)
        finally:
            await radio.disconnect()
