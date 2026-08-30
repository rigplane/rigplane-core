"""PTT on/off commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._frame import (
    CONTROLLER_ADDR,
    _CMD_PTT,
    _SUB_PTT,
    _build_from_map,
    build_civ_frame,
    expose_command_key,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


@expose_command_key(lambda cmd_map: "ptt_on")
def ptt_on(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a PTT-on CI-V command.

    The payload byte (0x01) is constant, so under the tuple contract
    (Q7, `docs/plans/2026-08-29-profile-driven-command-bytes.md` §8.1)
    every profile's ``ptt_on`` tuple already carries it; the map branch
    passes no ``data`` of its own. Exposes its command-map key as a plain
    literal (MOR-2003 Step 3, §3.1) for `commands/bound.py:
    BoundCommands.expect`.
    """
    if cmd_map is not None:
        return _build_from_map(cmd_map, "ptt_on", to_addr=to_addr, from_addr=from_addr)
    return build_civ_frame(to_addr, from_addr, _CMD_PTT, sub=_SUB_PTT, data=b"\x01")


@expose_command_key(lambda cmd_map: "ptt_off")
def ptt_off(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a PTT-off CI-V command.

    The payload byte (0x00) is constant, so under the tuple contract
    (Q7, `docs/plans/2026-08-29-profile-driven-command-bytes.md` §8.1)
    every profile's ``ptt_off`` tuple already carries it; the map branch
    passes no ``data`` of its own. Exposes its command-map key as a plain
    literal (MOR-2003 Step 3, §3.1) for `commands/bound.py:
    BoundCommands.expect`.
    """
    if cmd_map is not None:
        return _build_from_map(cmd_map, "ptt_off", to_addr=to_addr, from_addr=from_addr)
    return build_civ_frame(to_addr, from_addr, _CMD_PTT, sub=_SUB_PTT, data=b"\x00")
