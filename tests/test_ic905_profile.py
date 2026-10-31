"""Conformance coverage for the IC-905 profile (MOR-3984).

Source of truth: the official IC-905 CI-V Reference Guide, edition
``IC-905_ENG_CI-V_2`` (A7711-9EX-2, May 2024). Page numbers below are the
guide's printed numbers: command table pp.3-15, command formats pp.16-29.
The 144/430/1200/2400/5600 MHz bands only: the 10 GHz band (CX-10G, 6-byte
frequencies, MOR-3983) and the DD/ATV modes are out of scope. No IC-905
hardware exists in the bench, so nothing here is a hardware claim.
"""

import tomllib
from pathlib import Path

import pytest

from rigplane import IcomRadio, create_radio
from rigplane.backends.config import LanBackendConfig, SerialBackendConfig
from rigplane.backends.factory import supports_serial_model
from rigplane.backends.ic705.serial import Ic705SerialRadio
from rigplane.commands import (
    set_data1_mod_input,
    set_data_off_mod_input,
    set_lan_mod_level,
    set_quick_split,
    set_usb_mod_level,
    set_vfo,
)
from rigplane.profiles import get_radio_profile, resolve_radio_profile
from rigplane.profiles.rig_loader import RigConfig, discover_rigs
from rigplane.runtime.radios import (
    CIV_PROFILE_MAP,
    RADIOS,
    SERIAL_RADIO_MAP,
    get_civ_addr,
    identify_radio,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = REPO_ROOT / "rigs"
ADDR = 0xAC


def _config() -> RigConfig:
    return discover_rigs(RIGS_DIR)["IC-905"]


def _raw() -> dict:
    return tomllib.loads((RIGS_DIR / "ic905.toml").read_text())


def test_identity_is_ic905_at_ac_and_native_id_reply_stays_unset() -> None:
    config = _config()

    assert config.id == "icom_ic905"
    assert config.civ_addr == 0xAC  # guide p.2: transceiver default address AC
    assert config.receiver_count == 1
    assert config.has_lan is True
    assert config.has_wifi is False
    # The guide lists 19 00 (p.5) but not the reply bytes: no token invented.
    assert config.expected_identity_ids == ()


def test_registry_rows_agree_on_the_address_and_profile() -> None:
    assert RADIOS["IC-905"].civ_addr == 0xAC
    assert RADIOS["IC-905"].receivers == 1
    assert get_civ_addr("ic905") == 0xAC
    assert SERIAL_RADIO_MAP[0xAC] == ("IC-905", b"")
    assert CIV_PROFILE_MAP[0xAC] == "icom_ic905"
    assert identify_radio(0xAC, b"") == "IC-905"


def test_factory_builds_lan_and_serial_radios_from_the_profile() -> None:
    assert supports_serial_model("IC-905")
    lan = create_radio(LanBackendConfig(host="127.0.0.1", model="IC-905"))
    serial = create_radio(SerialBackendConfig(device="/dev/null", model="IC-905"))

    assert isinstance(lan, IcomRadio)
    assert lan.model == "IC-905"
    assert lan._radio_addr == 0xAC
    assert isinstance(serial, Ic705SerialRadio)
    assert serial.model == "IC-905"
    assert serial._profile.civ_addr == 0xAC
    assert resolve_radio_profile(model="IC-905", radio_addr=0x98) is get_radio_profile(
        "IC-905"
    )


def test_single_receiver_ab_vfo_topology_without_dual_watch() -> None:
    profile = get_radio_profile("IC-905")

    assert profile.receiver_count == 1
    assert profile.vfo_scheme == "ab"
    assert profile.swap_ab_code == 0xB0  # p.3: 07 B0 exchanges VFO A and B
    assert profile.equal_ab_code == 0xA0  # p.3: 07 A0 equalizes
    assert not profile.cmd29_routes
    assert not profile.supports_capability("dual_rx")
    assert not profile.supports_capability("dual_watch")
    # p.15: 27 12 and 27 13 are fixed (Main, Single).
    command_map = _config().to_command_map()
    assert set_vfo(1, to_addr=ADDR, cmd_map=command_map) == bytes.fromhex(
        "FE FE AC E0 07 01 FD"
    )


def test_bands_are_the_five_documented_bands_with_per_band_power() -> None:
    profile = get_radio_profile("IC-905")
    bands = {b.name: b for r in profile.freq_ranges for b in r.bands}

    # BSR frequency band codes, p.19: 01 144, 02 430, 03 1200, 04 2400, 05 5600
    # (06 = 10G is out of scope). Ranges are the guide's, USA version.
    assert {n: (b.start, b.end, b.bsr_code) for n, b in bands.items()} == {
        "2m": (144_000_000, 148_000_000, 0x01),
        "70cm": (430_000_000, 450_000_000, 0x02),
        "23cm": (1_240_000_000, 1_300_000_000, 0x03),
        "13cm": (2_300_000_000, 2_450_000_000, 0x04),
        "5cm": (5_650_000_000, 5_925_000_000, 0x05),
    }
    # Basic manual, Specifications: 10 W on 144/430/1200 MHz, 2 W on 2400/5600.
    assert {n: b.max_power_w for n, b in bands.items()} == {
        "2m": 10,
        "70cm": 10,
        "23cm": 10,
        "13cm": 2,
        "5cm": 2,
    }
    assert all(b.default_mode == "FM" for b in bands.values())
    assert not any(b.rx_only for b in bands.values())


def test_capabilities_follow_the_guide() -> None:
    profile = get_radio_profile("IC-905")

    for feature in ("scope", "repeater_shift", "split", "rit", "xfc", "bsr", "scan"):
        assert profile.supports_capability(feature), feature
    # No ATU (no 1C 01 row, p.14), no antenna select (no 12 row), no APF, no
    # DIGI-SEL/IP+/drive gain. Memory channels need a shared-reader change
    # (one channel block, GHz digits): 1A 00 is deliberately undeclared.
    for feature in ("tuner", "antenna", "rx_antenna", "apf", "digisel", "ip_plus"):
        assert not profile.supports_capability(feature), feature
    assert not profile.supports_capability("memory_read")


def test_modes_exclude_dd_atv_and_wfm() -> None:
    raw = _raw()

    # p.16 operating mode codes: LSB USB AM CW RTTY FM CW-R RTTY-R DV DD ATV;
    # DD (22) and ATV (23) are out of scope, there is no WFM.
    assert raw["modes"]["list"] == [
        "USB",
        "LSB",
        "CW",
        "CW-R",
        "AM",
        "FM",
        "RTTY",
        "RTTY-R",
        "DV",
    ]
    assert raw["data_mode"]["inputs"] == [
        {"value": 0, "name": "MIC"},
        {"value": 1, "name": "USB"},
        {"value": 2, "name": "MIC,USB"},
        {"value": 3, "name": "LAN"},
    ]  # p.8: 1A 05 01 26 / 01 27 (0=MIC 1=USB 2=MIC,USB 3=LAN)


def test_front_end_and_level_domains() -> None:
    raw = _raw()

    assert raw["attenuator"]["values"] == [0, 10]  # p.3: 11 00 / 11 10 (10 dB)
    assert raw["preamp"]["values"] == [0, 1]  # p.4: 16 02 00/01
    assert raw["agc"]["modes"] == [1, 2, 3]  # p.4: 16 12 01-03
    assert raw["power"]["max_watts"] == 10
    assert raw["tone_squelch_types"] == {
        "0": "off",
        "1": "tone",
        "2": "tsql",
        "3": "dtcs",
        "6": "dtcs_t",
        "7": "tone_t_dtcs_r",
        "8": "dtcs_t_tsql_r",
        "9": "tone_t_tsql_r",
    }  # p.4: 16 5D
    assert raw["rit"]["range_hz"] == 9999  # Basic manual: RIT +-9.999 kHz
    assert raw["cw"]["dash_ratio_min"] == 28  # p.11: 1A 05 02 36 28-45
    assert raw["cw"]["dash_ratio_max"] == 45


def test_scope_declares_the_guide_spans_reference_and_475_points() -> None:
    raw = _raw()
    profile = get_radio_profile("IC-905")

    assert raw["spectrum"] == {"seq_max": 11, "amp_max": 160, "data_len_max": 475}
    # p.28 span table, 27 15 (Hz): 2.5 kHz ... 25 MHz.
    assert profile.scope_span_presets_hz == (
        2500,
        5000,
        10000,
        25000,
        50000,
        100000,
        250000,
        500000,
        1000000,
        2500000,
        5000000,
        10000000,
        25000000,
    )
    assert profile.scope_ref_min_db == -20.0  # p.28: 27 19 -20.0 ~ +20.0 dB
    assert profile.scope_ref_max_db == 20.0


@pytest.mark.parametrize(
    ("key", "wire"),
    [
        # p.3
        ("get_band_edge_freq", (0x02,)),
        ("set_memory_mode", (0x08,)),
        ("set_memory_group", (0x08, 0xA0)),
        ("memory_write", (0x09,)),
        ("memory_to_vfo", (0x0A,)),
        ("get_freq_offset", (0x0C,)),
        ("set_freq_offset", (0x0D,)),
        ("get_split", (0x0F,)),
        ("get_repeater_shift", (0x0F,)),
        ("get_tuning_step", (0x10,)),
        ("get_attenuator", (0x11,)),
        ("get_speech", (0x13,)),
        ("get_af_level", (0x14, 0x01)),
        ("get_nr_level", (0x14, 0x06)),
        ("get_pbt_inner", (0x14, 0x07)),
        # p.4
        ("get_pbt_outer", (0x14, 0x08)),
        ("get_cw_pitch", (0x14, 0x09)),
        ("get_rf_power", (0x14, 0x0A)),
        ("get_notch_filter", (0x14, 0x0D)),
        ("get_nb_level", (0x14, 0x12)),
        ("get_monitor_gain", (0x14, 0x15)),
        ("get_vox_gain", (0x14, 0x16)),
        ("get_anti_vox_gain", (0x14, 0x17)),
        ("get_s_meter_sql_status", (0x15, 0x01)),
        ("get_s_meter", (0x15, 0x02)),
        ("get_overflow_status", (0x15, 0x07)),
        ("get_power_meter", (0x15, 0x11)),
        ("get_vd_meter", (0x15, 0x15)),
        ("get_id_meter", (0x15, 0x16)),
        ("get_preamp", (0x16, 0x02)),
        ("get_agc", (0x16, 0x12)),
        ("get_nb", (0x16, 0x22)),
        ("get_twin_peak_filter", (0x16, 0x4F)),
        ("get_dial_lock", (0x16, 0x50)),
        ("get_filter_shape", (0x16, 0x56)),
        ("get_manual_notch_width", (0x16, 0x57)),
        ("get_ssb_tx_bandwidth", (0x16, 0x58)),
        ("get_tone_squelch_type", (0x16, 0x5D)),
        # p.5
        ("power_on", (0x18,)),
        ("get_transceiver_id", (0x19, 0x00)),
        ("get_bsr", (0x1A, 0x01)),
        ("get_filter_width", (0x1A, 0x03)),
        ("get_agc_time_constant", (0x1A, 0x04)),
        # p.14
        ("get_data_mode", (0x1A, 0x06)),
        ("get_tone_freq", (0x1B, 0x00)),
        ("get_tsql_freq", (0x1B, 0x01)),
        ("get_dtcs", (0x1B, 0x02)),
        ("get_csql", (0x1B, 0x07)),
        ("get_transceiver_status", (0x1C, 0x00)),
        ("get_xfc_status", (0x1C, 0x02)),
        ("get_tx_band_count", (0x1E, 0x00)),
        ("get_tx_band_edge", (0x1E, 0x01)),
        ("get_rit_frequency", (0x21, 0x00)),
        ("get_rit_status", (0x21, 0x01)),
        ("get_rit_tx_status", (0x21, 0x02)),
        # p.15
        ("get_selected_freq", (0x25, 0x00)),
        ("get_unselected_freq", (0x25, 0x01)),
        ("get_selected_mode", (0x26, 0x00)),
        ("get_unselected_mode", (0x26, 0x01)),
        ("get_scope_wave", (0x27, 0x00)),
        ("scope_on", (0x27, 0x10)),
        ("scope_data_output", (0x27, 0x11)),
        ("get_scope_main_sub", (0x27, 0x12)),
        ("get_scope_single_dual", (0x27, 0x13)),
        ("get_scope_mode", (0x27, 0x14)),
        ("get_scope_span", (0x27, 0x15)),
        ("get_scope_edge", (0x27, 0x16)),
        ("get_scope_hold", (0x27, 0x17)),
        ("get_scope_ref", (0x27, 0x19)),
        ("get_scope_speed", (0x27, 0x1A)),
        ("get_scope_during_tx", (0x27, 0x1B)),
        ("get_scope_center_type", (0x27, 0x1C)),
        ("get_scope_vbw", (0x27, 0x1D)),
        ("get_scope_fixed_edge", (0x27, 0x1E)),
        # 1A 05 menu rows, pp.6-14
        ("get_quick_split", (0x1A, 0x05, 0x00, 0x46)),  # p.6
        ("get_ref_adjust", (0x1A, 0x05, 0x00, 0x93)),  # p.7
        ("get_usb_mod_level", (0x1A, 0x05, 0x01, 0x24)),  # p.8
        ("get_lan_mod_level", (0x1A, 0x05, 0x01, 0x25)),  # p.8
        ("get_data_off_mod_input", (0x1A, 0x05, 0x01, 0x26)),  # p.8
        ("get_data1_mod_input", (0x1A, 0x05, 0x01, 0x27)),  # p.8
        ("get_civ_transceive", (0x1A, 0x05, 0x01, 0x42)),  # p.8
        ("get_system_date", (0x1A, 0x05, 0x01, 0x76)),  # p.9
        ("get_system_time", (0x1A, 0x05, 0x01, 0x77)),  # p.9
        ("get_utc_offset", (0x1A, 0x05, 0x01, 0x81)),  # p.9
        ("get_dash_ratio", (0x1A, 0x05, 0x02, 0x36)),  # p.11
        ("get_nb_depth", (0x1A, 0x05, 0x03, 0x37)),  # p.14
        ("get_nb_width", (0x1A, 0x05, 0x03, 0x38)),  # p.14
        ("get_vox_delay", (0x1A, 0x05, 0x03, 0x39)),  # p.14
    ],
)
def test_command_map_matches_the_guide_table(key: str, wire: tuple[int, ...]) -> None:
    command_map = _config().to_command_map()

    assert command_map.get(key) == wire
    setter = key.replace("get_", "set_", 1)
    # 0C reads and 0D sends the duplex offset: the one get/set pair that differs.
    if key != "get_freq_offset" and key.startswith("get_") and command_map.has(setter):
        assert command_map.get(setter) == wire


@pytest.mark.parametrize(
    "key",
    [
        "get_tuner_status",  # no 1C 01 row
        "get_dual_watch",  # 07 table is 00/01/A0/B0 only
        "get_main_sub_band",
        "get_antenna",  # no 12 row
        "get_rx_antenna",
        "get_digisel",
        "get_apf_type_level",
        "get_audio_peak_filter",
        "get_drive_gain",
        "get_ip_plus",
        "get_main_sub_tracking",
        "get_scope_rbw",  # 27 table ends at 1E
        "get_data2_mod_input",
        "get_acc1_mod_level",
        "get_civ_output_ant",
        "get_quick_dual_watch",
    ],
)
def test_rows_the_guide_does_not_have_are_declared_absent(key: str) -> None:
    assert key in _config().to_profile().absent_command_names
    assert not _config().to_command_map().has(key)


def test_memory_contents_are_undeclared_until_the_shared_reader_handles_it() -> None:
    command_map = _config().to_command_map()

    assert not command_map.has("get_memory_contents")
    assert "get_memory_contents" not in _config().to_profile().absent_command_names
    assert "memory" not in _raw()


def test_menu_frames_built_through_the_production_builders() -> None:
    command_map = _config().to_command_map()

    assert set_usb_mod_level(50, to_addr=ADDR, cmd_map=command_map) == bytes.fromhex(
        "FE FE AC E0 1A 05 01 24 00 50 FD"
    )
    assert set_lan_mod_level(50, to_addr=ADDR, cmd_map=command_map) == bytes.fromhex(
        "FE FE AC E0 1A 05 01 25 00 50 FD"
    )
    assert set_data_off_mod_input(
        3, to_addr=ADDR, cmd_map=command_map
    ) == bytes.fromhex("FE FE AC E0 1A 05 01 26 03 FD")
    assert set_data1_mod_input(3, to_addr=ADDR, cmd_map=command_map) == bytes.fromhex(
        "FE FE AC E0 1A 05 01 27 03 FD"
    )
    assert set_quick_split(True, to_addr=ADDR, cmd_map=command_map) == bytes.fromhex(
        "FE FE AC E0 1A 05 00 46 01 FD"
    )


def test_meter_scales_follow_the_guide_rows() -> None:
    meters = _raw()["meters"]

    def points(name: str) -> list[tuple[int, float]]:
        return [(p["raw"], p["actual"]) for p in meters[name]["calibration"]]

    assert points("s_meter") == [(0, -54.0), (120, 0.0), (241, 60.0)]  # p.4: 15 02
    assert points("swr") == [(0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0)]  # 15 12
    assert points("alc") == [(0, 0.0), (120, 100.0)]  # 15 13
    assert points("comp") == [(0, 0.0), (130, 15.0), (210, 25.5)]  # 15 14
    # 15 15 Vd differs from the IC-705: 0000=0 V, 0040=5 V, 0241=30 V.
    assert points("vd") == [(0, 0.0), (40, 5.0), (241, 30.0)]
    assert points("id") == [(0, 0.0), (121, 2.0), (241, 4.0)]  # 15 16
    # 15 11 Po is a percentage of the current band's rating: no watts scale.
    assert "power" not in meters


def test_tx_policy_is_declared_and_claims_no_measurement() -> None:
    raw = _raw()

    assert raw["tx_policy"]["refused_during_tx"] == []
    assert raw["tx_policy"]["tx_state_map"] == {"0": "rx"}


def test_lan_scope_is_fully_declared_for_the_prospect_use_case() -> None:
    """LB6B wants the spectrum over LAN: the LAN radio must carry the whole
    guide 0x27 table (p.15) plus the scope declarations, as data.

    The radio's own network control (Basic manual: Network Control, UDP ports
    50001-50003, user ID/password) is not verifiable without hardware; the
    manual still calls the RS-BA1 remote software "compatible in the near
    future".
    """
    lan = create_radio(LanBackendConfig(host="127.0.0.1", model="IC-905"))
    command_map = lan._profile.command_map

    assert lan._profile.supports_capability("scope")
    assert lan._profile.has_lan is True
    # p.15: 27 00, 10-17, 19-1E (18 and 1F do not exist; 12/13 are fixed).
    sub_commands = {0x00, *range(0x10, 0x18), *range(0x19, 0x1F)}
    wires = {
        wire[1]
        for name in command_map
        if (wire := command_map.get(name))[0] == 0x27 and len(wire) == 2
    }
    assert wires == sub_commands
    assert lan._profile.scope_span_presets_hz[0] == 2500
    assert lan._profile.scope_ref_min_db == -20.0
