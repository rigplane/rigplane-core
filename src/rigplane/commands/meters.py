"""All 0x15-family meter read commands.

Migrated onto the bound command map in MOR-2008 (batch 2,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N): all
ten builders now require ``cmd_map``, with no hardcoded fallback left. Zero
divergence rows -- every declaring profile's tuple already matched the
fallback's own bytes exactly (verified by grep across ``rigs/*.toml`` before
deleting each fallback).

``_build_meter_bool_get`` is gone from `_builders.py`: this module was its
only caller, so its own ``cmd_map is None`` fallback branch would otherwise
become dead code nobody reads -- ``get_s_meter_sql_status``/
``get_overflow_status``/``get_various_squelch`` now call `_frame.py:
_build_from_map` directly instead, the same de-delegation `config.py`'s/
`levels.py`'s own migrations already did for their single-caller templates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._codec import _level_bcd_decode
from ._frame import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _CMD_METER,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


@expose_command_key(lambda cmd_map: "get_s_meter")
@require_cmd_map
def get_s_meter(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a 'read S-meter' CI-V command."""
    return _build_from_map(cmd_map, "get_s_meter", to_addr=to_addr, from_addr=from_addr)


@expose_command_key(lambda cmd_map: "get_swr")
@require_cmd_map
def get_swr(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a 'read SWR meter' CI-V command."""
    return _build_from_map(cmd_map, "get_swr", to_addr=to_addr, from_addr=from_addr)


@expose_command_key(lambda cmd_map: "get_alc")
@require_cmd_map
def get_alc(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a 'read ALC meter' CI-V command."""
    return _build_from_map(cmd_map, "get_alc", to_addr=to_addr, from_addr=from_addr)


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


@expose_command_key(lambda cmd_map: "get_s_meter_sql_status")
@require_cmd_map
def get_s_meter_sql_status(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read S-meter squelch status command."""
    return _build_from_map(
        cmd_map,
        "get_s_meter_sql_status",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_overflow_status")
@require_cmd_map
def get_overflow_status(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read overflow status command."""
    return _build_from_map(
        cmd_map, "get_overflow_status", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "get_various_squelch")
@require_cmd_map
def get_various_squelch(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read various-squelch status command (0x15 0x05, Command29)."""
    return _build_from_map(
        cmd_map,
        "get_various_squelch",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_power_meter")
@require_cmd_map
def get_power_meter(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read RF power meter command (0x15 0x11)."""
    return _build_from_map(
        cmd_map, "get_power_meter", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "get_comp_meter")
@require_cmd_map
def get_comp_meter(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read compressor meter command (0x15 0x14)."""
    return _build_from_map(
        cmd_map, "get_comp_meter", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "get_vd_meter")
@require_cmd_map
def get_vd_meter(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read Vd (supply voltage) meter command (0x15 0x15)."""
    return _build_from_map(
        cmd_map, "get_vd_meter", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "get_id_meter")
@require_cmd_map
def get_id_meter(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read Id (drain current) meter command (0x15 0x16)."""
    return _build_from_map(
        cmd_map, "get_id_meter", to_addr=to_addr, from_addr=from_addr
    )
