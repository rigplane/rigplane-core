"""PTT on/off commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._frame import (
    CONTROLLER_ADDR,
    _CMD_PTT,
    _SUB_PTT,
    _build_from_map,
    build_civ_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


def ptt_on(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a PTT-on CI-V command."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "ptt_on", to_addr=to_addr, from_addr=from_addr, data=b"\x01"
        )
    return build_civ_frame(to_addr, from_addr, _CMD_PTT, sub=_SUB_PTT, data=b"\x01")


def ptt_off(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a PTT-off CI-V command."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "ptt_off", to_addr=to_addr, from_addr=from_addr, data=b"\x00"
        )
    return build_civ_frame(to_addr, from_addr, _CMD_PTT, sub=_SUB_PTT, data=b"\x00")
