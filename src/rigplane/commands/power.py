"""Power on/off and powerstat commands.

Migrated onto the bound command map in MOR-2008 (batch 1,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N): all
three builders now require ``cmd_map``, with no hardcoded fallback left.
Zero divergence rows, zero gap rows -- every profile that declares
``get_powerstat``/``power_on``/``power_off`` already carries the identical
``[0x18]`` tuple the fallback built (verified by grep across
``rigs/*.toml`` before deleting it; IC-7300/IC-9700 record ``get_powerstat``
absent instead, per D2 MOR-2014, unaffected by this migration), so
``tests/command_map_parity_divergences.txt`` stays empty of this module's
rows. ``parse_powerstat`` is unchanged: its response check reads
``_CMD_POWER_CTRL`` directly, which does not vary by profile.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._frame import (
    CONTROLLER_ADDR,
    _CMD_POWER_CTRL,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


@expose_command_key(lambda cmd_map: "get_powerstat")
@require_cmd_map
def get_powerstat(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V frame to query radio power status (0x18 GET)."""
    return _build_from_map(
        cmd_map, "get_powerstat", to_addr=to_addr, from_addr=from_addr, data=b""
    )


@expose_command_key(lambda cmd_map: "power_on")
@require_cmd_map
def power_on(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V frame to power on the radio."""
    return _build_from_map(
        cmd_map, "power_on", to_addr=to_addr, from_addr=from_addr, data=b"\x01"
    )


@expose_command_key(lambda cmd_map: "power_off")
@require_cmd_map
def power_off(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V frame to power off the radio."""
    return _build_from_map(
        cmd_map, "power_off", to_addr=to_addr, from_addr=from_addr, data=b"\x00"
    )


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
