"""Profile-driven tuning envelope (MOR-3782).

The web UI and command guards derive min/max tunable frequency from each
radio's ``freq_ranges`` profile data — not from per-model constants.
"""

from __future__ import annotations

from pathlib import Path

from rigplane.profiles import RadioProfile, get_radio_profile, reload_profiles
from rigplane.profiles.rig_loader import load_rig

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"


def tuning_envelope_hz(profile: RadioProfile) -> tuple[int, int]:
    """Mirror ``deriveBand``'s tuneMinHz/tuneMaxHz envelope in the frontend adapter."""
    if not profile.freq_ranges:
        raise AssertionError("profile has no freq_ranges")
    starts = [r.start for r in profile.freq_ranges]
    ends = [r.end for r in profile.freq_ranges]
    return min(starts), max(ends)


def frequency_in_profile_rx_ranges(profile: RadioProfile, freq_hz: int) -> bool:
    """Mirror ``RadioPoller`` VFO frequency guard against profile receive ranges."""
    return any(r.start <= freq_hz <= r.end for r in profile.freq_ranges)


class TestProfileTuningEnvelope:
    """Limits come from loaded profile data per radio model."""

    @classmethod
    def setup_class(cls) -> None:
        reload_profiles()

    def test_ic7300_eu_4m_band_within_tuning_envelope(self) -> None:
        profile = get_radio_profile("IC-7300")
        tune_min, tune_max = tuning_envelope_hz(profile)
        assert tune_min == 30_000
        assert tune_max >= 70_500_000, (
            f"IC-7300 tuneMaxHz {tune_max} must cover EU 4 m (70.0–70.5 MHz)"
        )
        assert frequency_in_profile_rx_ranges(profile, 70_200_000)

    def test_ic7300_standard_variant_keeps_60_mhz_envelope(self) -> None:
        rig = load_rig(RIGS_DIR / "ic7300.toml")
        profile = rig.to_profile(freq_range_variant="standard")
        _, tune_max = tuning_envelope_hz(profile)
        assert tune_max == 60_000_000
        assert not frequency_in_profile_rx_ranges(profile, 70_200_000)

    def test_ic705_tune_max_from_profile_not_ic7300_cap(self) -> None:
        profile = get_radio_profile("IC-705")
        _, tune_max = tuning_envelope_hz(profile)
        assert tune_max == 470_000_000

    def test_ic7610_tune_max_from_profile_hf_segment(self) -> None:
        profile = get_radio_profile("IC-7610")
        _, tune_max = tuning_envelope_hz(profile)
        assert tune_max == 60_000_000
