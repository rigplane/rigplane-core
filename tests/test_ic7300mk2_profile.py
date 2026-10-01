"""Regression coverage for the distinct IC-7300MK2 profile (MOR-3091).

The command expectations below follow the printed pages of the official
IC-7300MK2 CI-V Reference Guide: p.7 (Quick Split), p.9 (MOD settings),
p.4 (VFO selection/swap/equalize), pp.11-12 (scope edges), and p.6 (native
ID command). The guide does not define the bytes returned by 19 00, so
identity metadata stays empty.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rigplane import IcomRadio, create_radio
import rigplane.commands as commands
from rigplane.commands import (
    set_acc1_mod_level,
    set_audio_peak_filter,
    set_data1_mod_input,
    set_data_off_mod_input,
    set_lan_mod_level,
    set_quick_split,
    set_rx_antenna,
    set_usb_mod_level,
    set_vfo,
)
from rigplane.backends.config import LanBackendConfig, SerialBackendConfig
from rigplane.backends.ic7300.serial import Ic7300SerialRadio
from rigplane.exceptions import CommandError
from rigplane.profiles import get_radio_profile, resolve_radio_profile
from rigplane.profiles.rig_loader import RigConfig, discover_rigs, load_rig
from rigplane.runtime.callable_support import supports_callable

REPO_ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = REPO_ROOT / "rigs"


def _profile_config() -> RigConfig:
    return discover_rigs(RIGS_DIR)["IC-7300MK2"]


def test_mk2_profile_is_distinct_and_keeps_unknown_native_identity_unset() -> None:
    config = _profile_config()
    profile = config.to_profile()

    assert config.id == "icom_ic7300mk2"
    assert config.model == "IC-7300MK2"
    assert config.civ_addr == 0xB6
    assert config.receiver_count == 1
    assert config.has_lan is True
    assert config.has_wifi is False
    assert config.expected_identity_ids == ()
    assert profile.expected_identity_ids == ()


def test_explicit_mk2_model_wins_when_its_civ_address_is_overridden() -> None:
    mk2 = get_radio_profile("ic-7300mk2")

    # 0x98 is also the IC-7610's default address. Address compatibility does
    # not change the explicitly selected model/profile.
    assert resolve_radio_profile(model="IC-7300MK2", radio_addr=0x98) is mk2
    assert resolve_radio_profile(profile="icom_ic7300mk2", radio_addr=0x98) is mk2


def test_mk2_factory_construction_uses_profile_for_lan_and_shared_serial() -> None:
    default_lan = create_radio(LanBackendConfig(host="127.0.0.1", model="IC-7300MK2"))
    overridden_lan = create_radio(
        LanBackendConfig(host="127.0.0.1", model="IC-7300MK2", radio_addr=0x98)
    )
    serial = create_radio(SerialBackendConfig(device="/dev/null", model="IC-7300MK2"))

    assert isinstance(default_lan, IcomRadio)
    assert default_lan.model == "IC-7300MK2"
    assert default_lan._radio_addr == 0xB6
    assert default_lan._profile.civ_addr == 0xB6
    assert isinstance(overridden_lan, IcomRadio)
    assert overridden_lan.model == "IC-7300MK2"
    assert overridden_lan._radio_addr == 0x98
    assert overridden_lan._profile.civ_addr == 0xB6
    assert isinstance(serial, Ic7300SerialRadio)
    assert serial.model == "IC-7300MK2"
    assert serial._profile.civ_addr == 0xB6


def test_mk2_keeps_single_receiver_ab_vfo_topology() -> None:
    profile = get_radio_profile("IC-7300MK2")

    assert profile.receiver_count == 1
    assert profile.vfo_scheme == "ab"
    assert profile.swap_ab_code == 0xB0
    assert profile.equal_ab_code == 0xA0
    assert profile.swap_main_sub_code is None
    assert profile.equal_main_sub_code is None
    assert not profile.cmd29_routes
    assert not profile.supports_capability("dual_rx")
    assert not profile.supports_capability("dual_watch")


def test_mk2_command_map_builds_documented_menu_frames() -> None:
    config = _profile_config()
    profile = config.to_profile()
    command_map = config.to_command_map()
    addr = profile.civ_addr

    # These are built through the production command builders and the loaded
    # profile map, including the documented LAN source value 05.
    assert set_usb_mod_level(50, to_addr=addr, cmd_map=command_map) == bytes.fromhex(
        "FE FE B6 E0 1A 05 00 81 00 50 FD"
    )
    assert set_lan_mod_level(50, to_addr=addr, cmd_map=command_map) == bytes.fromhex(
        "FE FE B6 E0 1A 05 00 83 00 50 FD"
    )
    assert set_acc1_mod_level(50, to_addr=addr, cmd_map=command_map) == bytes.fromhex(
        "FE FE B6 E0 1A 05 00 82 00 50 FD"
    )
    assert set_data_off_mod_input(
        5, to_addr=addr, cmd_map=command_map
    ) == bytes.fromhex("FE FE B6 E0 1A 05 00 84 05 FD")
    assert set_data1_mod_input(5, to_addr=addr, cmd_map=command_map) == bytes.fromhex(
        "FE FE B6 E0 1A 05 00 85 05 FD"
    )
    assert set_quick_split(True, to_addr=addr, cmd_map=command_map) == bytes.fromhex(
        "FE FE B6 E0 1A 05 00 33 01 FD"
    )


def test_mk2_apf_values_and_rx_antenna_use_documented_frames() -> None:
    config = _profile_config()
    command_map = config.to_command_map()

    for value in range(4):
        assert set_audio_peak_filter(
            value,
            to_addr=0xB6,
            command29=False,
            cmd_map=command_map,
        ) == bytes.fromhex(f"FE FE B6 E0 16 32 0{value:X} FD")

    assert set_rx_antenna(True, to_addr=0xB6, cmd_map=command_map) == bytes.fromhex(
        "FE FE B6 E0 12 00 01 FD"
    )
    assert set_rx_antenna(False, to_addr=0xB6, cmd_map=command_map) == bytes.fromhex(
        "FE FE B6 E0 12 00 00 FD"
    )


@pytest.mark.parametrize(
    "operation",
    ("get_antenna_2", "set_antenna_2", "get_rx_antenna_ant2", "set_rx_antenna_ant2"),
)
async def test_mk2_refuses_absent_antenna_alias_before_public_io(
    operation, monkeypatch
) -> None:
    radio = create_radio(LanBackendConfig(host="127.0.0.1", model="IC-7300MK2"))
    reader = AsyncMock(return_value=False)
    writer = AsyncMock()
    monkeypatch.setattr(radio, "_get_bool_value", reader)
    monkeypatch.setattr(radio, "_send_fire_and_forget", writer)

    assert not supports_callable(radio._profile, operation)
    args = (True,) if operation.startswith("set_") else ()
    with pytest.raises(CommandError, match="declared absent"):
        await getattr(radio, operation)(*args)
    reader.assert_not_awaited()
    writer.assert_not_awaited()


@pytest.mark.parametrize("operation", ("get_antenna_2", "set_antenna_2"))
def test_mk2_absent_antenna_alias_also_refuses_response_shape(operation) -> None:
    radio = create_radio(LanBackendConfig(host="127.0.0.1", model="IC-7300MK2"))
    with pytest.raises(CommandError, match="declared absent"):
        radio._commands.expect(getattr(commands, operation))


def test_ic7610_second_antenna_shared_builders_remain_available() -> None:
    radio = create_radio(LanBackendConfig(host="127.0.0.1", model="IC-7610"))
    assert supports_callable(radio._profile, "get_antenna_2")
    assert supports_callable(radio._profile, "set_antenna_2")
    assert radio._commands.get_antenna_2(to_addr=0x98) == bytes.fromhex(
        "FE FE 98 E0 12 01 FD"
    )
    assert radio._commands.set_antenna_2(True, to_addr=0x98) == bytes.fromhex(
        "FE FE 98 E0 12 01 01 FD"
    )


def test_mk2_vfo_selectors_and_old_profiles_remain_separate() -> None:
    mk2 = _profile_config()
    command_map = mk2.to_command_map()

    assert set_vfo(0, to_addr=0xB6, cmd_map=command_map) == bytes.fromhex(
        "FE FE B6 E0 07 00 FD"
    )
    assert set_vfo(1, to_addr=0xB6, cmd_map=command_map) == bytes.fromhex(
        "FE FE B6 E0 07 01 FD"
    )

    original = load_rig(RIGS_DIR / "ic7300.toml")
    ic7610 = load_rig(RIGS_DIR / "ic7610.toml")
    original_map = original.to_command_map()
    ic7610_map = ic7610.to_command_map()
    mk2_map = command_map

    assert original.civ_addr == 0x94
    assert original.receiver_count == 1
    assert original.has_lan is False
    assert original_map.get("set_data1_mod_input") == (0x1A, 0x05, 0x00, 0x67)
    assert ic7610_map.get("set_data1_mod_input") == (0x1A, 0x05, 0x00, 0x92)
    assert mk2_map.get("set_data1_mod_input") == (0x1A, 0x05, 0x00, 0x85)
    assert mk2_map.get("set_data1_mod_input") != (0x1A, 0x05, 0x00, 0x67)
    assert mk2_map.get("set_data1_mod_input") != (0x1A, 0x05, 0x00, 0x92)
    # CI-V p.14 supplies the replacement slots; the old slots are now scope
    # edges. Check both directions rather than accepting omitted controls.
    for control, wire in (
        ("nb_depth", (0x1A, 0x05, 0x02, 0x65)),
        ("nb_width", (0x1A, 0x05, 0x02, 0x66)),
        ("vox_delay", (0x1A, 0x05, 0x02, 0x67)),
    ):
        assert mk2_map.get(f"get_{control}") == wire
        assert mk2_map.get(f"set_{control}") == wire


@pytest.mark.parametrize(
    ("group", "expected"),
    [
        (
            "1p6mhz",
            (
                (0x1A, 0x05, 0x01, 0x52),
                (0x1A, 0x05, 0x01, 0x53),
                (0x1A, 0x05, 0x01, 0x54),
                (0x1A, 0x05, 0x01, 0x55),
            ),
        ),
        (
            "2mhz",
            (
                (0x1A, 0x05, 0x01, 0x56),
                (0x1A, 0x05, 0x01, 0x57),
                (0x1A, 0x05, 0x01, 0x58),
                (0x1A, 0x05, 0x01, 0x59),
            ),
        ),
        (
            "6mhz",
            (
                (0x1A, 0x05, 0x01, 0x60),
                (0x1A, 0x05, 0x01, 0x61),
                (0x1A, 0x05, 0x01, 0x62),
                (0x1A, 0x05, 0x01, 0x63),
            ),
        ),
        (
            "8mhz",
            (
                (0x1A, 0x05, 0x01, 0x64),
                (0x1A, 0x05, 0x01, 0x65),
                (0x1A, 0x05, 0x01, 0x66),
                (0x1A, 0x05, 0x01, 0x67),
            ),
        ),
        (
            "11mhz",
            (
                (0x1A, 0x05, 0x01, 0x68),
                (0x1A, 0x05, 0x01, 0x69),
                (0x1A, 0x05, 0x01, 0x70),
                (0x1A, 0x05, 0x01, 0x71),
            ),
        ),
        (
            "15mhz",
            (
                (0x1A, 0x05, 0x01, 0x72),
                (0x1A, 0x05, 0x01, 0x73),
                (0x1A, 0x05, 0x01, 0x74),
                (0x1A, 0x05, 0x01, 0x75),
            ),
        ),
        (
            "20mhz",
            (
                (0x1A, 0x05, 0x01, 0x76),
                (0x1A, 0x05, 0x01, 0x77),
                (0x1A, 0x05, 0x01, 0x78),
                (0x1A, 0x05, 0x01, 0x79),
            ),
        ),
        (
            "22mhz",
            (
                (0x1A, 0x05, 0x01, 0x80),
                (0x1A, 0x05, 0x01, 0x81),
                (0x1A, 0x05, 0x01, 0x82),
                (0x1A, 0x05, 0x01, 0x83),
            ),
        ),
        (
            "26mhz",
            (
                (0x1A, 0x05, 0x01, 0x84),
                (0x1A, 0x05, 0x01, 0x85),
                (0x1A, 0x05, 0x01, 0x86),
                (0x1A, 0x05, 0x01, 0x87),
            ),
        ),
        (
            "30mhz",
            (
                (0x1A, 0x05, 0x01, 0x88),
                (0x1A, 0x05, 0x01, 0x89),
                (0x1A, 0x05, 0x01, 0x90),
                (0x1A, 0x05, 0x01, 0x91),
            ),
        ),
        (
            "45mhz",
            (
                (0x1A, 0x05, 0x01, 0x92),
                (0x1A, 0x05, 0x01, 0x93),
                (0x1A, 0x05, 0x01, 0x94),
                (0x1A, 0x05, 0x01, 0x95),
            ),
        ),
        (
            "60mhz",
            (
                (0x1A, 0x05, 0x01, 0x96),
                (0x1A, 0x05, 0x01, 0x97),
                (0x1A, 0x05, 0x01, 0x98),
                (0x1A, 0x05, 0x01, 0x99),
            ),
        ),
        (
            "74mhz",
            (
                (0x1A, 0x05, 0x02, 0x00),
                (0x1A, 0x05, 0x02, 0x01),
                (0x1A, 0x05, 0x02, 0x02),
                (0x1A, 0x05, 0x02, 0x03),
            ),
        ),
    ],
)
def test_mk2_scope_edge_address_groups_match_reference(
    group: str,
    expected: tuple[
        tuple[int, int, int, int],
        tuple[int, int, int, int],
        tuple[int, int, int, int],
        tuple[int, int, int, int],
    ],
) -> None:
    command_map = _profile_config().to_command_map()

    for edge, expected_wire in enumerate(expected, start=1):
        assert command_map.get(f"get_scope_edge{edge}_{group}") == expected_wire
        assert command_map.get(f"set_scope_edge{edge}_{group}") == expected_wire
