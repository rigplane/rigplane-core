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
from dataclasses import astuple
from pathlib import Path

import pytest
from rigplane.backends.config import SerialBackendConfig
from rigplane.backends.factory import create_radio, supports_serial_model
from rigplane.backends.ic705.serial import XieguSerialRadio
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.commands._codec import filter_index_to_hz

# The pristine builder, not the ``rigplane.commands`` package attribute:
# several unrelated test modules wrap that package process-wide with
# ``bind_default_addr_module(..., to_addr=0x98)`` at import time, so the
# package attribute may be a partial that rejects an explicit to_addr.
from rigplane.commands.freq import set_freq as build_set_freq
from rigplane.profiles import (
    get_radio_profile,
    profile_by_xiegu_model_id,
    resolve_radio_profile,
)
from rigplane.profiles.control_domain import (
    decode_legacy_control,
    encode_legacy_control,
)
from rigplane.profiles.rig_loader import RigConfig, discover_rigs, load_rig
from rigplane.types import bcd_encode
from rigplane.runtime.meter_cal import interpolate_meter
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
    # 0xA0 is the only address answering over LAN in wfview network mode
    # (LIVE G7250 LAN 2026-11-13, MOR-4436: 0x88 and 0x94 silent, 0xA0
    # answers; the vendor wfview G7250.rig uses CIVAddress=160 too). Over
    # USB serial the radio answers ANY address and echoes it (LIVE
    # 2026-11-09, probed 00/A4/88/70/72/E0). No other shipped profile is
    # at 0xA0, so address-only resolution is undisturbed, and discovery
    # still names this radio through 0x1D 0x19, never the address table.
    # See test_address_only_resolution_regression below.
    assert config.civ_addr == 0xA0
    assert config.receiver_count == 1
    # wfview network mode is Icom LAN protocol control (LIVE 2026-11-13,
    # V1.0.1); HDSDR mode stays the UDP IQ stream under [iq_lan].
    assert config.has_lan is True
    # LIVE: the radio answers 115200 and stays silent at 19200.
    assert config.default_baud == 115200
    # LIVE: 0x1D 0x19 answered `72 50`.
    assert config.xiegu_model_id == 0x7250


def test_iq_lan_wire_facts_are_profile_data() -> None:
    iq_lan = get_radio_profile("G7250").iq_lan

    expected = ("xiegu_udp", 50002, 50001, 48000, 192)
    expected += (8000, 40000, 100000, 54000000)
    assert astuple(iq_lan) == expected


def test_registry_rows_resolve_the_model() -> None:
    assert RADIOS["G7250"].civ_addr == 0xA0
    assert RADIOS["G7250"].has_lan is True
    assert RADIOS["G7250"].receivers == 1
    assert get_civ_addr("g7250") == 0xA0
    assert get_civ_addr("G-7250") == 0xA0


def test_address_only_resolution_regression() -> None:
    """The 0xA0 move must not disturb address-only profile resolution.

    g7250.toml loads before ic705.toml and x6200.toml, and the profiles'
    ``_by_civ_addr`` table keeps the FIRST profile per address — with the
    G7250 at 0xA4 it stole the IC-705's address-only resolution (the
    local Codex review's round-1 blocker). 0xA0 is used by no other
    shipped profile, so the pre-G7250 rows are unchanged.
    """
    assert resolve_radio_profile(radio_addr=0xA4).model == "IC-705"
    assert resolve_radio_profile(radio_addr=0x70).model == "X6100"
    # The G7250 itself resolves by name (or id) to its own 0xA0 profile.
    by_name = resolve_radio_profile(model="G7250")
    assert by_name.model == "G7250"
    assert by_name.civ_addr == 0xA0
    assert resolve_radio_profile(radio_addr=0xA0).model == "G7250"
    # 0x88 is the old profile address; no shipped profile claims it now,
    # so an address-only lookup for it refuses instead of naming a radio.
    with pytest.raises(ValueError, match="Cannot resolve a radio profile"):
        resolve_radio_profile(radio_addr=0x88)


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
    # FM width was not measured on the bench; the profile must say so in
    # prose instead of inventing values. RTTY/CW-R/RTTY-R have no width
    # table at all (Compat mode shadows, Reference Table 2-3 / page 7) and
    # DIGI was not observed on the G7250 (read-only audit 2026-11-09).
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
        # MOR-4436 part A: 0x0E scan is unimplemented on the G7250
        # (Reference Table 4-2 / page 13), scan edges FA (Table 1-3 / p3).
        "scan",
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


def test_tx_power_watts_are_vendor_spec_not_wire_facts() -> None:
    """The watts figures are the preliminary vendor spec, never wire facts.

    The read-only audit never keyed the radio and the CI-V TX-band table
    never answered (0x1E no reply, LIVE), so no TX fact here is
    bench-proven; ``[power]``/``max_power_w`` (100 W HF, 90 W 50 MHz —
    preliminary vendor spec) is display data for the power slider only.
    """
    profile = get_radio_profile("G7250")
    assert profile.max_watts == 100
    assert profile.min_watts is None
    assert "get_tx_band_count" in profile.absent_command_names
    declared = {
        band.name: band.max_power_w
        for freq_range in profile.freq_ranges
        for band in freq_range.bands
        if band.max_power_w is not None
    }
    assert declared == {
        "160m": 100,
        "80m": 100,
        "40m": 100,
        "30m": 100,
        "20m": 100,
        "17m": 100,
        "15m": 100,
        "12m": 100,
        "10m": 100,
        "6m": 90,
    }
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


# ---------------------------------------------------------------------------
# MOR-4436 part A — Xiegu G7250 CI-V Reference Rev 1.2 (2026-11-06) data
# ---------------------------------------------------------------------------


def test_attenuator_accepts_only_00_or_20db() -> None:
    """A1: ``[attenuator]`` is [0, 20] — 0x11 takes only 00 OFF or 20 ON.

    Source: Reference Table 1-3 / page 3 ("write only A=00 OFF or 20 ON;
    any other non-zero value is FA, not a toggle"). Same shape as
    rigs/ic7300.toml. The pre-1.0.1 X6200-inherited [0, 1] would FA on a
    write of 01.
    """
    assert _config().att_values == (0, 20)


def test_agc_has_no_mid_or_auto_and_preamp_has_two_stages() -> None:
    """A2: AGC is OFF/FAST/SLOW = 0/1/3; preamp is 0/1/2.

    Source: Reference Table 1-6 / page 6 — 0x16 0x12 is 00 OFF, 01 FAST,
    03 SLOW ([Real]); 02 MID is [Compat] (stored, never drives hardware)
    and native AUTO has no protocol code. 0x16 0x02 is 00 OFF, 01 PRE1,
    02 PRE2 ([Real]). The panel's AUTO is not commandable over
    CI-V in V1.0.1, so no AUTO value is declared.
    """
    config = _config()
    assert config.agc_modes == (0, 1, 3)
    assert config.agc_labels == {"0": "OFF", "1": "FAST", "3": "SLOW"}
    assert config.pre_values == (0, 1, 2)


def test_scan_removed_and_only_real_modes_selectable() -> None:
    """A3: no ``scan`` feature; modes are USB/LSB/CW/AM/FM; no RTTY widths.

    Sources: 0x0E scan is unimplemented (Reference Table 4-2 / page 13)
    and the P1/P2 scan edges answer FA (Table 1-3 / page 3). Table 2-1 /
    page 2: LSB/USB/AM/CW/FM are [Real]; RTTY (04), CW-R (07), RTTY-R (08)
    are [Compat] (stored only, native mode unchanged) — not selectable.
    RTTY/CW-R/RTTY-R widths are mode shadows (Table 2-3 / page 7), so no
    width table is declared for them.
    """
    profile = get_radio_profile("G7250")
    assert not profile.supports_capability("scan")
    assert profile.modes == ("USB", "LSB", "CW", "AM", "FM")
    assert profile.mode_families == {"cw": ("CW",)}
    assert profile.filter_config is not None
    assert "RTTY" not in profile.filter_config
    assert profile.resolve_filter_rule("RTTY") is None


def test_key_speed_cw_pitch_mic_gain_reference_domains() -> None:
    """A4: control domains from Reference Tables 1-4/1-5/2-2 (pages 4-5).

    Key speed native 5-50 WPM (5+floor(N*45/255)); CW pitch 400-1200 Hz in
    10 Hz steps (decode_quantum = 10); mic gain raw 0-255 over native 0-30
    (floor(N*30/255)). The legacy band cannot express the reference floor
    formulas exactly (see the profile comments); these are the closest
    available domains.
    """
    config = _config()
    assert config.controls is not None
    key_speed = config.controls["key_speed"]
    assert (key_speed["raw_min"], key_speed["raw_max"]) == (0, 255)
    assert (key_speed["display_min"], key_speed["display_max"]) == (5, 50)
    assert key_speed["display_unit"] == "WPM"
    assert key_speed["decode_quantum"] == 1
    cw_pitch = config.controls["cw_pitch"]
    assert (cw_pitch["display_min"], cw_pitch["display_max"]) == (400, 1200)
    assert cw_pitch["display_unit"] == "Hz"
    assert cw_pitch["decode_quantum"] == 10
    mic_gain = config.controls["mic_gain"]
    assert (mic_gain["raw_min"], mic_gain["raw_max"]) == (0, 255)
    assert (mic_gain["display_min"], mic_gain["display_max"]) == (0, 30)


def test_meter_calibration_anchors_match_the_reference() -> None:
    """A5: meter tables interpolate to the Reference Table 1-8/2-4 anchors.

    PO raw 213 -> 100 W; SWR raw 120 -> 3.0; S raw 120 -> -75 dBm (-2.0 dB
    rel S9); Vd raw 13 -> 10 V. Id/ALC tables exist but have no reader
    (no get_alc key; get_id_meter declared absent) — asserted below.
    """
    profile = get_radio_profile("G7250")
    cals = profile.meter_calibrations
    assert cals is not None
    assert interpolate_meter(213, cals, "power") == (100.0, True)
    assert interpolate_meter(120, cals, "swr") == (3.0, True)
    assert interpolate_meter(120, cals, "s_meter") == (-2.0, True)
    assert interpolate_meter(13, cals, "vd") == (10.0, True)
    assert interpolate_meter(146, cals, "id") == (15.0, True)
    assert interpolate_meter(60, cals, "alc") == (50.0, True)
    # Calibrations with no reader: ALC has no profile key, Id is absent.
    assert "get_alc" not in profile.command_names
    assert "get_id_meter" in profile.absent_command_names


def test_no_get_vfo_key_and_no_bare_07_poll() -> None:
    """A6: bare 07 is a SET (leave memory mode), never a read.

    Source: Reference Table 1-3 / page 3. Nothing reads VFO selection
    through this key (rigctld works from the state store; selected-VFO
    reads are 25 00 / 26 00), so the key is removed rather than replaced.
    set_vfo stays: 07 00/01/A0/B0 are real sets. No acquisition path polls
    a VFO selector either.
    """
    profile = get_radio_profile("G7250")
    assert "get_vfo" not in profile.command_names
    assert "set_vfo" in profile.command_names
    acquisition = profile.state_acquisition
    assert acquisition is not None
    paths = [str(capability.path) for capability in acquisition.capabilities]
    assert paths, "expected polled/observable acquisition paths"
    assert not [path for path in paths if "vfo" in path.lower()], paths


# ---------------------------------------------------------------------------
# MOR-4436 part B — wfview-mode LAN: address 0xA0, 25 00 frequency SET
# ---------------------------------------------------------------------------


def test_set_freq_is_25_00_and_neighbors_stay_on_05() -> None:
    """B: the G7250 frequency SET is ``25 00``; IC-7300/IC-705 stay on ``05``.

    LIVE G7250 LAN 2026-11-13 (MOR-4436, V1.0.1, wfview network mode):
    writing ``05`` + freq is echoed but NOT applied, while ``25 00`` +
    freq is applied (read back by ``03`` and ``25 00``). The Xiegu G7250
    CI-V Reference Rev 1.2 (2026-11-06), Table 1-1 / page 1 classes
    ``25 V=00/01 F[5]`` as [Real] over serial too (not bench-verified on
    serial), so one command works on both transports. ``get_freq`` stays
    ``03``.
    """
    g7250_map = load_rig(RIGS_DIR / "g7250.toml").to_command_map()
    frame = build_set_freq(7_280_000, 0xA0, cmd_map=g7250_map)
    assert frame == build_civ_frame(
        0xA0, CONTROLLER_ADDR, 0x25, sub=0x00, data=bcd_encode(7_280_000)
    )
    assert frame.hex() == "fefea0e025000000280700fd"

    # The neighbors' SETs are byte-identical to before this change.
    ic7300_map = load_rig(RIGS_DIR / "ic7300.toml").to_command_map()
    assert build_set_freq(7_280_000, 0x94, cmd_map=ic7300_map) == build_civ_frame(
        0x94, CONTROLLER_ADDR, 0x05, data=bcd_encode(7_280_000)
    )
    ic705_map = load_rig(RIGS_DIR / "ic705.toml").to_command_map()
    assert build_set_freq(7_280_000, 0xA4, cmd_map=ic705_map) == build_civ_frame(
        0xA4, CONTROLLER_ADDR, 0x05, data=bcd_encode(7_280_000)
    )


# ---------------------------------------------------------------------------
# MOR-4436 part A gate P1 — key-speed rounding regression: encode -> radio
# formulas -> decode must be the identity for every settable value.
# ---------------------------------------------------------------------------


def _half_up(numerator: int, denominator: int) -> int:
    """Round-to-nearest with exact halves up (the reference's own ``round``)."""
    quotient, remainder = divmod(numerator, denominator)
    return quotient + (1 if 2 * remainder >= denominator else 0)


def radio_roundtrip(control: str, raw: int) -> int:
    """Apply the vendor's documented write/read formulas to wire raw *raw*.

    Xiegu G7250 CI-V Reference Rev 1.2 (2026-11-06): key speed Table 1-4 /
    page 4 (write ``5+floor(N*45/255)``, read ``floor((x-5)*255/45)``) and
    Table 2-2 / page 4; MIC gain, same tables (write ``floor(N*30/255)``,
    read ``floor(x*255/30)``); CW pitch Table 1-5 / page 5 and Table 2-2 /
    page 4 (write ``x=400+10*round(N*80/255)``, read
    ``N=round(255*(x-400)/800)``). Returns the raw level a GET would report
    after the radio stored a SET of *raw* — the quantization the old
    echo-everything fake hid.
    """
    if control == "key_speed":
        stored = 5 + (raw * 45) // 255
        return (stored - 5) * 255 // 45
    if control == "mic_gain":
        stored = (raw * 30) // 255
        return stored * 255 // 30
    if control == "cw_pitch":
        stored = 400 + 10 * _half_up(raw * 80, 255)
        return _half_up(255 * (stored - 400), 800)
    raise ValueError(f"no vendor write/read formula for {control!r}")


def _roundtrip_cases() -> list[tuple[str, int]]:
    """Every user-settable value of the three display-band levels."""
    return (
        [("key_speed", wpm) for wpm in range(5, 51)]
        + [("mic_gain", gain) for gain in range(0, 31)]
        + [("cw_pitch", hz) for hz in range(400, 1201, 10)]
    )


@pytest.mark.parametrize(("control", "display"), _roundtrip_cases())
def test_display_value_survives_encode_radio_formulas_and_decode(
    control: str, display: int
) -> None:
    """Encode -> the radio's own write/read -> decode is the identity.

    The profile declares ``encode_rounding = "ceil"`` because the radio
    floors (key speed, MIC gain) or rounds (CW pitch) on write; ``ceil``
    lands on the raw whose stored value reads back as the request. Under the
    previous ``nearest_half_down`` this fails for 15 of the 46 key-speed
    values (every third WPM from 7: 7, 10, 13, ..., 49 — e.g. 25 WPM encodes
    as raw 113, stored as 24 WPM) and for all 15 odd MIC-gain values
    (1, 3, ..., 29); CW pitch round-trips under either rounding.
    """
    controls = _config().controls
    assert controls is not None
    assert controls[control]["encode_rounding"] == "ceil"
    raw = encode_legacy_control(controls, control, display)
    assert (
        decode_legacy_control(controls, control, radio_roundtrip(control, raw))
        == display
    )
