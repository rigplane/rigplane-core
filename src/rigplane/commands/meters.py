"""All 0x15-family meter read commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._builders import _build_meter_bool_get
from ._codec import _level_bcd_decode
from ._frame import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _CMD_METER,
    _SUB_ALC_METER,
    _SUB_COMP_METER,
    _SUB_ID_METER,
    _SUB_POWER_METER,
    _SUB_S_METER,
    _SUB_S_METER_SQL_STATUS,
    _SUB_SWR_METER,
    _SUB_VARIOUS_SQUELCH,
    _SUB_VD_METER,
    _build_from_map,
    build_civ_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


def get_s_meter(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'read S-meter' CI-V command."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_s_meter", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_METER, sub=_SUB_S_METER)


def get_swr(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'read SWR meter' CI-V command."""
    if cmd_map is not None:
        return _build_from_map(cmd_map, "get_swr", to_addr=to_addr, from_addr=from_addr)
    return build_civ_frame(to_addr, from_addr, _CMD_METER, sub=_SUB_SWR_METER)


def get_alc(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'read ALC meter' CI-V command."""
    if cmd_map is not None:
        return _build_from_map(cmd_map, "get_alc", to_addr=to_addr, from_addr=from_addr)
    return build_civ_frame(to_addr, from_addr, _CMD_METER, sub=_SUB_ALC_METER)


def parse_meter_response(frame: CivFrame) -> int:
    """Parse a meter response frame.

    Returns:
        Meter value 0-255.
    """
    if frame.command != _CMD_METER:
        raise ValueError(f"Not a meter response: command 0x{frame.command:02x}")
    if len(frame.data) < 2:
        raise ValueError(
            "Meter response payload too short: expected at least 2 bytes, "
            f"got {len(frame.data)}"
        )
    return _level_bcd_decode(frame.data)


def get_s_meter_sql_status(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read S-meter squelch status command."""
    return _build_meter_bool_get(
        _SUB_S_METER_SQL_STATUS,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_s_meter_sql_status",
    )


def get_overflow_status(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read overflow status command."""
    from ._frame import _SUB_OVERFLOW_STATUS

    return _build_meter_bool_get(
        _SUB_OVERFLOW_STATUS,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_overflow_status",
    )


def get_various_squelch(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read various-squelch status command (0x15 0x05, Command29)."""
    return _build_meter_bool_get(
        _SUB_VARIOUS_SQUELCH,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_various_squelch",
    )


def get_power_meter(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read RF power meter command (0x15 0x11)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_power_meter", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_METER, sub=_SUB_POWER_METER)


def get_comp_meter(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read compressor meter command (0x15 0x14)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_comp_meter", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_METER, sub=_SUB_COMP_METER)


def get_vd_meter(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read Vd (supply voltage) meter command (0x15 0x15)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_vd_meter", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_METER, sub=_SUB_VD_METER)


def get_id_meter(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read Id (drain current) meter command (0x15 0x16)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_id_meter", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_METER, sub=_SUB_ID_METER)
