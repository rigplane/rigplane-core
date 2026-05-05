"""Power on/off and powerstat commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._frame import (
    CONTROLLER_ADDR,
    _CMD_POWER_CTRL,
    _build_from_map,
    build_civ_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


def get_powerstat(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V frame to query radio power status (0x18 GET)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_powerstat", to_addr=to_addr, from_addr=from_addr, data=b""
        )
    return build_civ_frame(to_addr, from_addr, _CMD_POWER_CTRL, data=b"")


def power_on(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V frame to power on the radio."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "power_on", to_addr=to_addr, from_addr=from_addr, data=b"\x01"
        )
    return build_civ_frame(to_addr, from_addr, _CMD_POWER_CTRL, data=b"\x01")


def power_off(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V frame to power off the radio."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "power_off", to_addr=to_addr, from_addr=from_addr, data=b"\x00"
        )
    return build_civ_frame(to_addr, from_addr, _CMD_POWER_CTRL, data=b"\x00")


def parse_powerstat(frame: CivFrame) -> bool:
    """Parse power status response (0x18 GET).

    Returns:
        True if powered on, False if powered off.
    """
    if frame.command != _CMD_POWER_CTRL:
        raise ValueError(
            f"Expected power control response (0x18), got 0x{frame.command:02X}"
        )
    if len(frame.data) != 1:
        raise ValueError(f"Expected 1 byte power status, got {len(frame.data)} bytes")
    val = frame.data[0]
    if val not in (0x00, 0x01):
        raise ValueError(
            f"Invalid power status value: 0x{val:02X} (expected 0x00 or 0x01)"
        )
    return val == 0x01
