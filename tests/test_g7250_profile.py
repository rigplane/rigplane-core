"""Conformance coverage for the Xiegu G7250 profile (MOR-4352).

Source of truth: the live bench audit of 2026-11-09 — a read-only CI-V
probe session against a real G7250 on its USB CH340 serial port at
115200 baud (no answer at 19200). Every claim below marked LIVE quotes
that audit; claims marked *inherited* come from the X6200 profile
(``rigs/x6200.toml``) and were NOT probed on the G7250. Nothing here is
a transmit claim: the audit never keyed the radio.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from rigplane.backends.config import SerialBackendConfig
from rigplane.backends.factory import create_radio, supports_serial_model
from rigplane.backends.ic705.serial import XieguSerialRadio
from rigplane.commands._codec import filter_index_to_hz
from rigplane.profiles import (
    get_radio_profile,
    profile_by_xiegu_model_id,
    resolve_radio_profile,
)
from rigplane.profiles.rig_loader import RigConfig, discover_rigs
from rigplane.runtime.radios import RADIOS, get_civ_addr

REPO_ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = REPO_ROOT / "rigs"

# The nine (mode, FIL slot, wire byte, owner-read Hz) rows the audit
# captured: the owner read the radio display while the byte was logged.
# LIVE G7250 2026-11-09 (MOR-4352).
_MEASURED_FILTERS = [
    ("LSB", 1, 0x33, 2900),
    ("LSB", 2, 0x28, 2400),
    ("LSB", 3, 0x22, 1800),
    ("CW", 1, 0x09, 500),
    ("CW", 2, 0x05, 300),
    ("CW", 3, 0x02, 150),
    ("AM", 1, 0x44, 9000),
    ("AM", 2, 0x29, 6000),
    ("AM", 3, 0x14, 3000),
]

# NAKed live on the bench: the opcode was sent and the radio answered
# 0xFA (LIVE G7250 2026-11-09, MOR-4352). These keys must load as
# formally absent, never present.
_LIVE_NAKED_ABSENT = {
    "get_band_spectrum_display",
    "get_tuner_status",
    "set_tuner_status",
    "get_data_mode",
    "set_data_mode",
    "get_memory_contents",
    "set_memory_contents",
    "get_break_in",
    "set_break_in",
    "get_manual_notch",
    "set_manual_notch",
    "get_repeater_tone",
    "set_repeater_tone",
    "get_repeater_tsql",
    "set_repeater_tsql",
    "get_twin_peak_filter",
    "set_twin_peak_filter",
    "get_comp_meter",
    "get_id_meter",
}

# Answered 0x02, not 00/01 (LIVE G7250 2026-11-09, MOR-4352): the meaning
# of 0x02 on this radio is unconfirmed, so these keys must load as
# formally absent with that source, never present.
_LIVE_02_ABSENT = {
    "get_auto_notch",
    "set_auto_notch",
    "get_compressor",
    "set_compressor",
    "get_dial_lock",
    "set_dial_lock",
}


def _config() -> RigConfig:
    return discover_rigs(RIGS_DIR)["G7250"]


def _raw() -> dict:
    return tomllib.loads((RIGS_DIR / "g7250.toml").read_text())


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_identity_is_g7250_with_the_live_baud_and_model_id() -> None:
    config = _config()

    assert config.id == "xiegu_g7250"
    assert config.model == "G7250"
    # The radio answers ANY CI-V address and echoes the address it was
    # asked as its own (LIVE 2026-11-09, probed 00/A4/88/70/72/E0; 0x88
    # answered the full read set). 0x88, not the family default 0xA4:
    # this file loads before ic705.toml and the profiles' address-only
    # table would otherwise resolve 0xA4 to the G7250 (the IC-705's
    # address). See test_address_only_resolution_regression below.
    assert config.civ_addr == 0x88
    assert config.receiver_count == 1
    # The G7250 "LAN" port is a proprietary IQ stream, not CI-V control.
    assert config.has_lan is False
    # LIVE: the radio answers 115200 and stays silent at 19200.
    assert config.default_baud == 115200
    # LIVE: 0x1D 0x19 answered `72 50`.
    assert config.xiegu_model_id == 0x7250


def test_registry_rows_resolve_the_model() -> None:
    assert RADIOS["G7250"].civ_addr == 0x88
    assert RADIOS["G7250"].has_lan is False
    assert RADIOS["G7250"].receivers == 1
    assert get_civ_addr("g7250") == 0x88
    assert get_civ_addr("G-7250") == 0x88


def test_address_only_resolution_regression() -> None:
    """The 0x88 move must not disturb address-only profile resolution.

    g7250.toml loads before ic705.toml and x6200.toml, and the profiles'
    ``_by_civ_addr`` table keeps the FIRST profile per address — with the
    G7250 at 0xA4 it stole the IC-705's address-only resolution (the
    local Codex review's round-1 blocker). 0x88 is used by no other
    shipped profile, so the pre-G7250 rows are unchanged.
    """
    assert resolve_radio_profile(radio_addr=0xA4).model == "IC-705"
    assert resolve_radio_profile(radio_addr=0x70).model == "X6100"
    # The G7250 itself resolves by name (or id) to its own 0x88 profile.
    by_name = resolve_radio_profile(model="G7250")
    assert by_name.model == "G7250"
    assert by_name.civ_addr == 0x88
    assert resolve_radio_profile(radio_addr=0x88).model == "G7250"


def test_factory_routes_the_shared_xiegu_serial_class() -> None:
    assert supports_serial_model("G7250")
    radio = create_radio(SerialBackendConfig(device="/dev/null", model="G7250"))
    assert isinstance(radio, XieguSerialRadio)


# ---------------------------------------------------------------------------
# Data-driven Xiegu model-id discovery
# ---------------------------------------------------------------------------


def test_model_id_bytes_resolve_back_to_the_profile() -> None:
    assert profile_by_xiegu_model_id(b"\x72\x50").id == "xiegu_g7250"
    assert profile_by_xiegu_model_id(b"\x62\x00").id == "xiegu_x6200"
    # An unlisted Xiegu must not fall back to a known profile.
    assert profile_by_xiegu_model_id(b"\x51\x00") is None


def test_x6200_declares_its_model_id_too() -> None:
    x6200 = tomllib.loads((RIGS_DIR / "x6200.toml").read_text())
    # Reply bytes of the X6200 fixture: `62 00` (tests/test_discovery.py).
    assert x6200["radio"]["xiegu_model_id"] == 0x6200


# ---------------------------------------------------------------------------
# Filter width — LIVE BCD index against the owner-read display
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("mode", "fil", "wire_byte", "expected_hz"), _MEASURED_FILTERS)
def test_measured_filter_bytes_decode_to_the_owner_read_hz(
    mode: str, fil: int, wire_byte: int, expected_hz: int
) -> None:
    profile = get_radio_profile("G7250")
    rule = profile.resolve_filter_rule(mode)
    assert rule is not None, f"no filter rule for {mode}"
    assert len(rule.defaults) == 3

    # segmented_bcd_index: the wire byte is packed BCD (0x33 -> 33), the
    # decoded index then maps through the IC-7300-style segments.
    bcd_index = (wire_byte >> 4) * 10 + (wire_byte & 0x0F)
    assert filter_index_to_hz(bcd_index, segments=rule.segments) == expected_hz
    # The measured slot default is the measured Hz for that FIL slot.
    assert rule.defaults[fil - 1] == expected_hz


def test_filter_encoding_is_the_ic7300_bcd_index_not_the_x6200_formula() -> None:
    profile = get_radio_profile("G7250")
    # LIVE: 0x33 read 2900 Hz on the display. The X6200 raw-byte formula
    # would read 0x33 as (0x33-10)*100 = 4100 Hz — wrong for the G7250.
    assert profile.filter_width_encoding == "segmented_bcd_index"


def test_unmeasured_filter_families_are_marked_not_measured_in_the_toml() -> None:
    # RTTY/FM widths were not measured on the bench; the profile must say
    # so in prose instead of inventing values. DIGI has no width table at
    # all — not observed on the G7250 (read-only audit 2026-11-09).
    text = (RIGS_DIR / "g7250.toml").read_text()
    assert "not measured" in text
    assert _raw()["filters"]["encoding"] == "segmented_bcd_index"


# ---------------------------------------------------------------------------
# Live-confirmed absent commands
# ---------------------------------------------------------------------------


def test_live_naked_reads_are_formally_absent() -> None:
    profile = get_radio_profile("G7250")
    missing = _LIVE_NAKED_ABSENT - profile.absent_command_names
    assert not missing, f"NAKed-on-the-bench keys not declared absent: {missing}"
    # And each carries the live-audit source text.
    for name in _LIVE_NAKED_ABSENT:
        assert "LIVE G7250" in profile.absent_command_sources[name], name


def test_0x02_reads_are_formally_absent() -> None:
    """Toggles whose reads answered 0x02 (not 00/01) declare no state.

    The round-1 review blocker: ``CoreRadio.get_*`` compares to 0x01 while
    ingress uses ``bool(data[0])``, so a 0x02 reply shows contradictory
    state. NB (16 22) and NR (16 40) read 00 — off — and stay; the
    auto-notch/compressor/dial-lock reads answered 0x02 with an
    unconfirmed meaning and must be formally absent.
    """
    profile = get_radio_profile("G7250")
    missing = _LIVE_02_ABSENT - profile.absent_command_names
    assert not missing, f"0x02-read keys not declared absent: {missing}"
    for name in _LIVE_02_ABSENT:
        assert profile.absent_command_sources[name] == (
            "LIVE G7250 2026-11-09: read 0x02, meaning unconfirmed"
        ), name
    # Their capabilities are gone with them.
    for cap in ("notch", "compressor", "dial_lock"):
        assert not profile.supports_capability(cap), cap


def test_no_rit_xit_pbt_vox_tone_or_data_mode_capability() -> None:
    profile = get_radio_profile("G7250")
    # LIVE: 0x21 RIT/XIT reads timed out; 0x16 0x46 VOX answered 0x02
    # with an unconfirmed meaning; tone/memory/data-mode NAKed. PBT reads
    # (0x14 0x07/0x08) answer but the setter was never exercised. The
    # auto-notch/compressor/dial-lock reads (0x16 0x41/0x44/0x50) answered
    # 0x02 — same unconfirmed-meaning posture as VOX.
    for cap in (
        "rit",
        "xit",
        "pbt",
        "vox",
        "notch",
        "compressor",
        "dial_lock",
        "tuner",
        "break_in",
        "data_mode",
        "scope",
    ):
        assert not profile.supports_capability(cap), cap
    # The audit-proved posture is kept for these.
    for cap in (
        "audio",
        "af_level",
        "rf_gain",
        "squelch",
        "attenuator",
        "preamp",
        "nb",
        "nr",
        "tx",
        "split",
        "cw",
        "meters",
        "agc",
        "power_control",
    ):
        assert profile.supports_capability(cap), cap


# ---------------------------------------------------------------------------
# RX range — LIVE band-edge read
# ---------------------------------------------------------------------------


def test_rx_range_is_the_live_band_edge_reply() -> None:
    config = _config()
    hf = next(r for r in config.freq_ranges if r["label"] == "HF")
    # LIVE: `02` band edge answered RX 0.5 .. 54 MHz.
    assert hf["start_hz"] == 500_000
    assert hf["end_hz"] == 54_000_000
    # No WFM/Air ranges: the live reply bounds the receiver at 54 MHz.
    labels = {r["label"] for r in config.freq_ranges}
    assert "WFM" not in labels
    assert "Air" not in labels


def test_no_tx_power_or_band_edge_facts_are_invented() -> None:
    profile = get_radio_profile("G7250")
    assert profile.max_watts is None
    assert profile.min_watts is None
    assert "get_tx_band_count" in profile.absent_command_names
    # TX policy posture inherited from the X6200, nothing measured.
    assert profile.tx_policy.refused_during_tx == frozenset()
    assert dict(profile.tx_policy.tx_state_map) == {"0": "rx"}


# ---------------------------------------------------------------------------
# RF power range — the LIVE 14 0A envelope (MOR-4352 follow-up)
# ---------------------------------------------------------------------------


def test_rf_power_control_declares_the_live_raw_range() -> None:
    """``[controls.rf_power]`` is raw 12..255, both ends read on the bench.

    The Pro TX smoke tool reads this band, requires ``raw_max == 255`` and
    drives the radio to ``raw_min / 255`` over the HTTP command path before
    it keys; without the table it refuses the G7250 with "unsupported RF
    profile range". Sources: LIVE G7250 2026-11-10 — at the owner's lowest
    possible setting (display 5 W, the menu goes no lower) ``14 0A``
    answered ``00 12``; LIVE G7250 2026-11-09 — at the maximum setting
    ``14 0A`` answered ``02 55``.
    """
    profile = get_radio_profile("G7250")
    assert profile.controls is not None
    entry = profile.controls["rf_power"]
    assert entry["raw_min"] == 12
    assert entry["raw_max"] == 255
