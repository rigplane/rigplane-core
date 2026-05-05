"""Antenna selection / RX-ANT commands (0x12)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._frame import (
    CONTROLLER_ADDR,
    _CMD_ANTENNA,
    _SUB_ANT1,
    _SUB_ANT2,
    _build_from_map,
    build_civ_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


def get_antenna_1(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build ANT1 select/read command (0x12 0x00) WITHOUT data byte."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_antenna",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_SUB_ANT1]),
        )
    return build_civ_frame(to_addr, from_addr, _CMD_ANTENNA, sub=_SUB_ANT1)


def set_antenna_1(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build ANT1 select command (0x12 0x00 <00|01>)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_antenna",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_SUB_ANT1]) + (b"\x01" if enabled else b"\x00"),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_ANTENNA,
        sub=_SUB_ANT1,
        data=b"\x01" if enabled else b"\x00",
    )


def get_antenna_2(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build ANT2 select/read command (0x12 0x01) WITHOUT data byte."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_antenna",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_SUB_ANT2]),
        )
    return build_civ_frame(to_addr, from_addr, _CMD_ANTENNA, sub=_SUB_ANT2)


def set_antenna_2(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build ANT2 select command (0x12 0x01 <00|01>)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_antenna",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_SUB_ANT2]) + (b"\x01" if enabled else b"\x00"),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_ANTENNA,
        sub=_SUB_ANT2,
        data=b"\x01" if enabled else b"\x00",
    )


def get_rx_antenna_ant1(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build read RX ANT state for ANT1 (0x12 0x00). Warning: also selects ANT1."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_antenna",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_SUB_ANT1]),
        )
    return build_civ_frame(to_addr, from_addr, _CMD_ANTENNA, sub=_SUB_ANT1)


def set_rx_antenna_ant1(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build set RX ANT state for ANT1 (0x12 0x00 <00|01>). Warning: also selects ANT1."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_antenna",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_SUB_ANT1]) + (b"\x01" if enabled else b"\x00"),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_ANTENNA,
        sub=_SUB_ANT1,
        data=b"\x01" if enabled else b"\x00",
    )


def get_rx_antenna_ant2(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build read RX ANT state for ANT2 (0x12 0x01). Warning: also selects ANT2."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_rx_antenna_ant2",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_SUB_ANT2]),
        )
    return build_civ_frame(to_addr, from_addr, _CMD_ANTENNA, sub=_SUB_ANT2)


def set_rx_antenna_ant2(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build set RX ANT state for ANT2 (0x12 0x01 <00|01>). Warning: also selects ANT2."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_rx_antenna_ant2",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_SUB_ANT2]) + (b"\x01" if enabled else b"\x00"),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_ANTENNA,
        sub=_SUB_ANT2,
        data=b"\x01" if enabled else b"\x00",
    )


# TOML canonical aliases
get_antenna = get_antenna_1
set_antenna = set_antenna_1
get_rx_antenna = get_rx_antenna_ant1
set_rx_antenna = set_rx_antenna_ant1
