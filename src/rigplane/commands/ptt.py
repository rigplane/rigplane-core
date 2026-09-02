"""PTT on/off commands.

Migrated onto the bound command map in MOR-2007 Steps 5..N (module 4,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4): both builders
now require ``cmd_map``, with no hardcoded fallback left. This module
accounted for 2 of the rows `tests/command_map_parity_divergences.txt`
recorded before the plan's Step 2 (MOR-2002 step 2b-ptt): ``rigs/x6100.toml``
already held the 3-byte ``[0x1C, 0x00, <payload>]`` tuple the Q7 tuple
contract requires, and the ``cmd_map`` branch doubled the payload byte on
top of it. That divergence was fixed before this migration (commits
9957ee49/713172a6); the wire bytes below are unchanged by this commit --
only the ``cmd_map`` requirement is new. Every CI-V profile in `rigs/`
now declares the identical ``[0x1C, 0x00, 0x01]``/``[0x1C, 0x00, 0x00]``
tuple, matching the fallback's own bytes exactly (verified by grep across
`rigs/*.toml` before deleting it), so `tests/command_map_parity_divergences.txt`
is empty of ptt.py rows and stays that way.

``_CMD_PTT`` (0x1C) still survives in `commands/_frame.py`, past
`commands/system.py`'s own migration (MOR-2008 batch 1), which deleted
that module's transceiver-status-family fallback branches (0x1C is the
whole "transceiver status" CI-V command family, not just PTT):
`tests/test_radio.py` imports ``_CMD_PTT`` directly (alongside
``_SUB_PTT``, 0x00) to build synthetic CivFrames for unrelated managed-TX/
ACK-tracking tests, unconnected to either module's fallback -- the
migration contract deletes a constant only when every reader, module
fallback or test, is gone, and this one still has one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._frame import (
    CONTROLLER_ADDR,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


@expose_command_key(lambda cmd_map: "ptt_on")
@require_cmd_map
def ptt_on(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a PTT-on CI-V command.

    The payload byte (0x01) is constant, so under the tuple contract
    (Q7, `docs/plans/2026-08-29-profile-driven-command-bytes.md` §8.1)
    every profile's ``ptt_on`` tuple already carries it; this builder
    passes no ``data`` of its own.
    """
    return _build_from_map(cmd_map, "ptt_on", to_addr=to_addr, from_addr=from_addr)


@expose_command_key(lambda cmd_map: "ptt_off")
@require_cmd_map
def ptt_off(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a PTT-off CI-V command. See ``ptt_on``."""
    return _build_from_map(cmd_map, "ptt_off", to_addr=to_addr, from_addr=from_addr)
