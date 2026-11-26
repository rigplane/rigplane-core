"""TX band edges are declared profile data on every TX-path radio (MOR-4840).

The deciding TX-availability input is the profile's ``[[freq_ranges.ranges.bands]]``
table: ``WebServer._serialize_tx_bands`` (``src/rigplane/web/server.py``) publishes
every non-``rx_only`` band row as capabilities ``txBands``, and the shared frontend
refusal path ``getFrequencyPermit`` (``frontend/src/lib/utils/tx-permit.ts``) denies
TX outside those rows (``outside-configured-ranges``) and fails closed when the
table is unconfigured. No radio-specific code is involved on either side.

This pins, in order: every shipped profile with a TX path declares at least one TX
band row (so ``txBands`` is never an unconfigured null for a radio that can key);
the IC-7300's exact twelve-row TX table (the Icom amateur-band limits, 4 m
included per the Advanced Manual quote in ``rigs/ic7300.toml``); and the two-radio
4 m difference behind the GTR7 customer row — 70.200 MHz is inside the IC-7300's
declared 4 m band (permitted by data; the radio itself decides per version,
MOR-4303) while the IC-7610 declares no 4 m row at all, and the nearest
out-of-band gaps (65.000 and 69.500 MHz, tunable via VHF RX on the IC-7300) sit in
no TX band on either radio, so the existing path denies them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.profiles import resolve_radio_profile
from rigplane.profiles.rig_loader import discover_rigs

RIGS_DIR = Path(__file__).resolve().parents[1] / "rigs"


def _profile_tx_bands(profile) -> list[tuple[str, int, int]]:
    """Non-``rx_only`` band rows: exactly what ``txBands`` publishes."""
    return [
        (band.name, band.start, band.end)
        for freq_range in profile.freq_ranges
        for band in freq_range.bands
        if not band.rx_only
    ]


def _tx_bands(model: str) -> list[tuple[str, int, int]]:
    return _profile_tx_bands(resolve_radio_profile(model=model))


def _is_in_bands(freq_hz: int, bands: list[tuple[str, int, int]]) -> str | None:
    for name, start, end in bands:
        if start <= freq_hz <= end:
            return name
    return None


# ── Every TX-path profile declares its TX band edges ────────────────────


class TestEveryTxPathProfileDeclaresTxBandEdges:
    @pytest.mark.parametrize(
        "model",
        sorted(discover_rigs(RIGS_DIR).keys()),
    )
    def test_shipped_profile_declares_at_least_one_tx_band(self, model: str) -> None:
        """A radio with a TX path must publish a non-empty ``txBands`` table.

        If a future receive-only profile lands with no TX band rows, this row
        names it: document the reason in the profile and extend this test with
        the explicit exemption (a ``tx_band_edges_unknown_reason``-style note)
        instead of weakening the assertion.
        """
        config = discover_rigs(RIGS_DIR)[model]
        assert _profile_tx_bands(config.to_profile()), (
            f"{model}: no non-rx_only band rows, so capabilities txBands "
            "would be null (ranges-unconfigured) and every TX permit unknown. "
            "Declare the TX bands or document why this profile has none."
        )

    def test_rx_only_rows_never_grant_transmit(self) -> None:
        """The IC-705's general-coverage row spans 4 m but grants nothing."""
        profile = resolve_radio_profile(model="IC-705")
        overlapping = [
            band.name
            for freq_range in profile.freq_ranges
            for band in freq_range.bands
            if band.start < 70_500_000 and band.end > 70_000_000
        ]
        assert overlapping == ["Gen"]
        assert "Gen" not in [name for name, _s, _e in _tx_bands("IC-705")]


# ── IC-7300 exact TX table ───────────────────────────────────────────────


_IC7300_TX_BANDS = [
    ("160m", 1_800_000, 2_000_000),
    ("80m", 3_500_000, 4_000_000),
    ("60m", 5_330_000, 5_410_000),
    ("40m", 7_000_000, 7_300_000),
    ("30m", 10_100_000, 10_150_000),
    ("20m", 14_000_000, 14_350_000),
    ("17m", 18_068_000, 18_168_000),
    ("15m", 21_000_000, 21_450_000),
    ("12m", 24_890_000, 24_990_000),
    ("10m", 28_000_000, 29_700_000),
    ("6m", 50_000_000, 54_000_000),
    ("4m", 70_000_000, 70_500_000),
]


class TestIc7300TxBandTable:
    def test_exact_twelve_row_table(self) -> None:
        assert _tx_bands("IC-7300") == _IC7300_TX_BANDS

    def test_four_metre_row_defaults_to_70_200_mhz(self) -> None:
        profile = resolve_radio_profile(model="IC-7300")
        row = next(
            band
            for freq_range in profile.freq_ranges
            for band in freq_range.bands
            if band.name == "4m"
        )
        assert (row.default, row.max_power_w) == (70_200_000, 50)


# ── Two-radio 4 m / out-of-band membership (the refusal path's input) ────


class TestTwoRadioFourMetreDifference:
    """Band membership is the refusal path's whole input: ``getFrequencyPermit``
    allows exactly the frequencies covered here and denies the rest."""

    def test_70_200_mhz_is_a_7300_tx_band_only(self) -> None:
        assert _is_in_bands(70_200_000, _tx_bands("IC-7300")) == "4m"
        assert _is_in_bands(70_200_000, _tx_bands("IC-7610")) is None

    @pytest.mark.parametrize("model", ["IC-7300", "IC-7610"])
    @pytest.mark.parametrize("freq_hz", [65_000_000, 69_500_000])
    def test_nearest_out_of_band_gaps_are_in_no_tx_band(
        self, model: str, freq_hz: int
    ) -> None:
        """65 / 69.5 MHz tune (VHF RX on the IC-7300) but key nowhere."""
        assert _is_in_bands(freq_hz, _tx_bands(model)) is None

    @pytest.mark.parametrize("model", ["IC-7300", "IC-7610"])
    def test_20m_sanity_row_on_both_radios(self, model: str) -> None:
        assert _is_in_bands(14_074_000, _tx_bands(model)) == "20m"
