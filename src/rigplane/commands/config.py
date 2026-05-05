"""Configuration commands: mod levels, mod input routing, CI-V options."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._builders import _build_ctl_mem_get, _build_ctl_mem_set
from ._codec import _level_bcd_encode
from ._frame import (
    CONTROLLER_ADDR,
    _CMD_LEVEL,
    _CTL_MEM_CIV_OUTPUT_ANT,
    _CTL_MEM_CIV_TRANSCEIVE,
    _CTL_MEM_DATA1_MOD_INPUT,
    _CTL_MEM_DATA2_MOD_INPUT,
    _CTL_MEM_DATA3_MOD_INPUT,
    _CTL_MEM_DATA_OFF_MOD_INPUT,
    _SUB_ACC1_MOD_LEVEL,
    _SUB_LAN_MOD_LEVEL,
    _SUB_USB_MOD_LEVEL,
    _build_from_map,
    build_civ_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


# --- Modulation Levels (0x14 0x0B / 0x10 / 0x11) ---


def get_acc1_mod_level(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_acc1_mod_level", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_LEVEL, sub=_SUB_ACC1_MOD_LEVEL)


def set_acc1_mod_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_acc1_mod_level",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_level_bcd_encode(level),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_LEVEL,
        sub=_SUB_ACC1_MOD_LEVEL,
        data=_level_bcd_encode(level),
    )


def get_usb_mod_level(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_usb_mod_level", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_LEVEL, sub=_SUB_USB_MOD_LEVEL)


def set_usb_mod_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_usb_mod_level",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_level_bcd_encode(level),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_LEVEL,
        sub=_SUB_USB_MOD_LEVEL,
        data=_level_bcd_encode(level),
    )


def get_lan_mod_level(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_lan_mod_level", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_LEVEL, sub=_SUB_LAN_MOD_LEVEL)


def set_lan_mod_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_lan_mod_level",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_level_bcd_encode(level),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_LEVEL,
        sub=_SUB_LAN_MOD_LEVEL,
        data=_level_bcd_encode(level),
    )


# --- Modulation Input Routing (0x1A 0x05 0x00 0x91-0x94) ---


def get_data_off_mod_input(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    return _build_ctl_mem_get(
        _CTL_MEM_DATA_OFF_MOD_INPUT,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_data_off_mod_input",
    )


def set_data_off_mod_input(
    source: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if not 0 <= source <= 5:
        raise ValueError(f"Data Off mod input must be 0-5, got {source}")
    return _build_ctl_mem_set(
        _CTL_MEM_DATA_OFF_MOD_INPUT,
        source,
        to_addr=to_addr,
        from_addr=from_addr,
        byte_count=1,
        cmd_map=cmd_map,
        cmd_name="set_data_off_mod_input",
    )


def get_data1_mod_input(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    return _build_ctl_mem_get(
        _CTL_MEM_DATA1_MOD_INPUT,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_data1_mod_input",
    )


def set_data1_mod_input(
    source: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if not 0 <= source <= 4:
        raise ValueError(f"DATA1 mod input must be 0-4, got {source}")
    return _build_ctl_mem_set(
        _CTL_MEM_DATA1_MOD_INPUT,
        source,
        to_addr=to_addr,
        from_addr=from_addr,
        byte_count=1,
        cmd_map=cmd_map,
        cmd_name="set_data1_mod_input",
    )


def get_data2_mod_input(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    return _build_ctl_mem_get(
        _CTL_MEM_DATA2_MOD_INPUT,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_data2_mod_input",
    )


def set_data2_mod_input(
    source: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if not 0 <= source <= 4:
        raise ValueError(f"DATA2 mod input must be 0-4, got {source}")
    return _build_ctl_mem_set(
        _CTL_MEM_DATA2_MOD_INPUT,
        source,
        to_addr=to_addr,
        from_addr=from_addr,
        byte_count=1,
        cmd_map=cmd_map,
        cmd_name="set_data2_mod_input",
    )


def get_data3_mod_input(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    return _build_ctl_mem_get(
        _CTL_MEM_DATA3_MOD_INPUT,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_data3_mod_input",
    )


def set_data3_mod_input(
    source: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if not 0 <= source <= 4:
        raise ValueError(f"DATA3 mod input must be 0-4, got {source}")
    return _build_ctl_mem_set(
        _CTL_MEM_DATA3_MOD_INPUT,
        source,
        to_addr=to_addr,
        from_addr=from_addr,
        byte_count=1,
        cmd_map=cmd_map,
        cmd_name="set_data3_mod_input",
    )


# --- CI-V Options (0x1A 0x05 0x01 0x29 / 0x30) ---


def get_civ_transceive(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    return _build_ctl_mem_get(
        _CTL_MEM_CIV_TRANSCEIVE,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_civ_transceive",
    )


def set_civ_transceive(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    return _build_ctl_mem_set(
        _CTL_MEM_CIV_TRANSCEIVE,
        1 if enabled else 0,
        to_addr=to_addr,
        from_addr=from_addr,
        byte_count=1,
        cmd_map=cmd_map,
        cmd_name="set_civ_transceive",
    )


def get_civ_output_ant(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    return _build_ctl_mem_get(
        _CTL_MEM_CIV_OUTPUT_ANT,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_civ_output_ant",
    )


def set_civ_output_ant(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    return _build_ctl_mem_set(
        _CTL_MEM_CIV_OUTPUT_ANT,
        1 if enabled else 0,
        to_addr=to_addr,
        from_addr=from_addr,
        byte_count=1,
        cmd_map=cmd_map,
        cmd_name="set_civ_output_ant",
    )
