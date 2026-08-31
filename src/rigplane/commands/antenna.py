"""Antenna selection / RX-ANT commands (0x12).

Migrated onto the bound command map in MOR-2008 (batch 2,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N): all
eight builders now require ``cmd_map``, with no hardcoded fallback left.
Zero divergence rows -- every declaring profile's tuple already matched the
fallback's own bytes exactly (verified by grep across ``rigs/*.toml`` before
deleting each fallback).

Not a matcher-backed-getter candidate for `tests/test_response_shape_from_
profile.py`'s keystone table, despite all four getters routing through
``CoreRadio._get_bool_value``: the ANT1/ANT2 selector (0x00/0x01) is not a
profile-declared sub-command at all -- ``rigs/*.toml``'s own ``get_antenna``
key is a bare ``[0x12]`` tuple with no sub, and the selector is appended by
the *caller* as data (``_build_from_map(cmd_map, "get_antenna", ...,
data=bytes([_SUB_ANT1]))``). ``_frame.py: _COMMANDS_WITH_SUB`` still splits
a 0x12 reply's byte[1] into ``frame.sub``, so the wire-level fact
``runtime/radio.py``'s hardcoded ``command=0x12, sub=0x00``/``0x01`` checks
is a CI-V protocol invariant (which physical antenna a value belongs to),
not something any profile could declare differently -- deriving it via
``self._expect_shape(...)`` would incorrectly read ``sub=None`` off the
map's own bare tuple. Left hardcoded, unchanged by this migration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._frame import (
    CONTROLLER_ADDR,
    _SUB_ANT1,
    _SUB_ANT2,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


@expose_command_key(lambda cmd_map: "get_antenna")
@require_cmd_map
def get_antenna_1(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build ANT1 select/read command (0x12 0x00) WITHOUT data byte."""
    return _build_from_map(
        cmd_map,
        "get_antenna",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([_SUB_ANT1]),
    )


@expose_command_key(lambda cmd_map: "set_antenna")
@require_cmd_map
def set_antenna_1(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build ANT1 select command (0x12 0x00 <00|01>)."""
    return _build_from_map(
        cmd_map,
        "set_antenna",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([_SUB_ANT1]) + (b"\x01" if enabled else b"\x00"),
    )


@expose_command_key(lambda cmd_map: "get_antenna")
@require_cmd_map
def get_antenna_2(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build ANT2 select/read command (0x12 0x01) WITHOUT data byte."""
    return _build_from_map(
        cmd_map,
        "get_antenna",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([_SUB_ANT2]),
    )


@expose_command_key(lambda cmd_map: "set_antenna")
@require_cmd_map
def set_antenna_2(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build ANT2 select command (0x12 0x01 <00|01>)."""
    return _build_from_map(
        cmd_map,
        "set_antenna",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([_SUB_ANT2]) + (b"\x01" if enabled else b"\x00"),
    )


@expose_command_key(lambda cmd_map: "get_antenna")
@require_cmd_map
def get_rx_antenna_ant1(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build read RX ANT state for ANT1 (0x12 0x00). Warning: also selects ANT1."""
    return _build_from_map(
        cmd_map,
        "get_antenna",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([_SUB_ANT1]),
    )


@expose_command_key(lambda cmd_map: "set_antenna")
@require_cmd_map
def set_rx_antenna_ant1(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build set RX ANT state for ANT1 (0x12 0x00 <00|01>). Warning: also selects ANT1."""
    return _build_from_map(
        cmd_map,
        "set_antenna",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([_SUB_ANT1]) + (b"\x01" if enabled else b"\x00"),
    )


@expose_command_key(lambda cmd_map: "get_rx_antenna_ant2")
@require_cmd_map
def get_rx_antenna_ant2(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build read RX ANT state for ANT2 (0x12 0x01). Warning: also selects ANT2."""
    return _build_from_map(
        cmd_map,
        "get_rx_antenna_ant2",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([_SUB_ANT2]),
    )


@expose_command_key(lambda cmd_map: "set_rx_antenna_ant2")
@require_cmd_map
def set_rx_antenna_ant2(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build set RX ANT state for ANT2 (0x12 0x01 <00|01>). Warning: also selects ANT2."""
    return _build_from_map(
        cmd_map,
        "set_rx_antenna_ant2",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([_SUB_ANT2]) + (b"\x01" if enabled else b"\x00"),
    )


# TOML canonical aliases
get_antenna = get_antenna_1
set_antenna = set_antenna_1
get_rx_antenna = get_rx_antenna_ant1
set_rx_antenna = set_rx_antenna_ant1
