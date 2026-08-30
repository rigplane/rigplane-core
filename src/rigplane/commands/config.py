"""Configuration commands: mod levels, mod input routing, CI-V options.

Migrated onto the bound command map in MOR-2006 Steps 5..N (module 1,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4): every builder
here requires ``cmd_map``, with no hardcoded fallback left. This module
accounted for 44 of the rows `tests/command_map_parity_divergences.txt`
recorded before this migration -- all now gone, since a divergence needs
two disagreeing implementations and this module has only one left --
including the measured ACC1/MIC-gain collision (MOR-1992) and
``set_data_off_mod_input``, which `runtime/profiles_runtime.py:
apply_profile` writes with no user action.

Every builder calls `_frame.py: _build_from_map` directly rather than the
shared `_builders.py: _build_ctl_mem_get` / `_build_ctl_mem_set` templates
used before migrating: those still carry their own ``cmd_map is None``
fallback for `levels.py` / `system.py`, unmigrated, so routing through them
would leave an explicit ``cmd_map=None`` call silently reaching old,
sometimes-wrong bytes instead of failing loudly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._codec import _level_bcd_encode, bcd_encode_value
from ._frame import (
    CONTROLLER_ADDR,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


# --- Modulation Levels (0x14 0x0B / 0x10 / 0x11 on radios without a menu
# address for them; IC-7300/7610/9700/705 route these through extended
# menu addresses instead -- see rigs/*.toml) ---


@expose_command_key(lambda cmd_map: "get_acc1_mod_level")
@require_cmd_map
def get_acc1_mod_level(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_acc1_mod_level", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_acc1_mod_level")
@require_cmd_map
def set_acc1_mod_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    return _build_from_map(
        cmd_map,
        "set_acc1_mod_level",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
    )


@expose_command_key(lambda cmd_map: "get_usb_mod_level")
@require_cmd_map
def get_usb_mod_level(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_usb_mod_level", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_usb_mod_level")
@require_cmd_map
def set_usb_mod_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    return _build_from_map(
        cmd_map,
        "set_usb_mod_level",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
    )


@expose_command_key(lambda cmd_map: "get_lan_mod_level")
@require_cmd_map
def get_lan_mod_level(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_lan_mod_level", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_lan_mod_level")
@require_cmd_map
def set_lan_mod_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    return _build_from_map(
        cmd_map,
        "set_lan_mod_level",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
    )


# --- Modulation Input Routing (menu addresses vary by radio -- see
# rigs/*.toml; the values here are names only, no longer bytes) ---


@expose_command_key(lambda cmd_map: "get_data_off_mod_input")
@require_cmd_map
def get_data_off_mod_input(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_data_off_mod_input", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_data_off_mod_input")
@require_cmd_map
def set_data_off_mod_input(
    source: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    if not 0 <= source <= 5:
        raise ValueError(f"Data Off mod input must be 0-5, got {source}")
    return _build_from_map(
        cmd_map,
        "set_data_off_mod_input",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(source, byte_count=1),
    )


@expose_command_key(lambda cmd_map: "get_data1_mod_input")
@require_cmd_map
def get_data1_mod_input(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_data1_mod_input", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_data1_mod_input")
@require_cmd_map
def set_data1_mod_input(
    source: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    if not 0 <= source <= 5:
        raise ValueError(f"DATA1 mod input must be 0-5, got {source}")
    return _build_from_map(
        cmd_map,
        "set_data1_mod_input",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(source, byte_count=1),
    )


@expose_command_key(lambda cmd_map: "get_data2_mod_input")
@require_cmd_map
def get_data2_mod_input(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_data2_mod_input", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_data2_mod_input")
@require_cmd_map
def set_data2_mod_input(
    source: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    if not 0 <= source <= 5:
        raise ValueError(f"DATA2 mod input must be 0-5, got {source}")
    return _build_from_map(
        cmd_map,
        "set_data2_mod_input",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(source, byte_count=1),
    )


@expose_command_key(lambda cmd_map: "get_data3_mod_input")
@require_cmd_map
def get_data3_mod_input(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_data3_mod_input", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_data3_mod_input")
@require_cmd_map
def set_data3_mod_input(
    source: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    if not 0 <= source <= 5:
        raise ValueError(f"DATA3 mod input must be 0-5, got {source}")
    return _build_from_map(
        cmd_map,
        "set_data3_mod_input",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(source, byte_count=1),
    )


# --- CI-V Options (menu addresses vary by radio -- see rigs/*.toml) ---


@expose_command_key(lambda cmd_map: "get_civ_transceive")
@require_cmd_map
def get_civ_transceive(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_civ_transceive", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_civ_transceive")
@require_cmd_map
def set_civ_transceive(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    return _build_from_map(
        cmd_map,
        "set_civ_transceive",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(1 if enabled else 0, byte_count=1),
    )


@expose_command_key(lambda cmd_map: "get_civ_output_ant")
@require_cmd_map
def get_civ_output_ant(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_civ_output_ant", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_civ_output_ant")
@require_cmd_map
def set_civ_output_ant(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    return _build_from_map(
        cmd_map,
        "set_civ_output_ant",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(1 if enabled else 0, byte_count=1),
    )
