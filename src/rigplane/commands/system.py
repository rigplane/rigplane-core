"""System commands: transceiver ID, band edge, tuner, XFC, TX freq monitor, RIT/XIT.

Migrated onto the bound command map in MOR-2008 (batch 1,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N): all
twenty builders now require ``cmd_map``, with no hardcoded fallback left.
Fourteen of them (transceiver ID, band edge, tuner/XFC/TX-freq-monitor
status, RIT/XIT) carried zero divergence rows and zero gap rows -- every
declaring profile's tuple already matched the fallback's own bytes exactly
(verified by grep across ``rigs/*.toml`` before deleting it), so
``tests/command_map_parity_divergences.txt`` stays empty of their rows.

The other six -- ``get_system_date``/``set_system_date``,
``get_system_time``/``set_system_time``, ``get_utc_offset``/
``set_utc_offset`` -- are the owner-ruled exception: their ``cmd_map``
branches resolved the bare keys ``"system_date"``/``"system_time"``/
``"utc_offset"``, which no profile TOML has ever declared (every one of the
four declaring profiles spells the direction-prefixed
``get_system_date``/``set_system_date`` etc.), so those branches raised
``KeyError`` for every profile the moment a real ``CommandMap`` reached
them -- ``tests/command_map_parity_uncovered.txt`` recorded this as a
``gap`` row for the bare names on every profile, never a divergence (a gap
means the ``cmd_map`` branch could not even build a frame to compare). The
fix is what this migration makes real for the first time: the builders now
resolve the direction-prefixed keys the TOML files actually declare. On
IC-7610 that changes nothing observable -- ``rigs/ic7610.toml`` declares
``0x01 0x58``/``0x01 0x59``/``0x01 0x62``, byte-identical to the shared
fallback constants below, which is why the fallback path looked correct for
years. On IC-7300, this is an intended behavior change: ``rigs/ic7300.toml``
declares its own family numbers ``0x00 0x94``/``0x00 0x95``/``0x00 0x96``,
and the CommandMap requirement means IC-7300 sends its own correct extended
addresses for the first time, not IC-7610's. Response parsing moves with
the request: ``parse_system_date_response``/``parse_system_time_response``/
``parse_utc_offset_response`` now take the map-derived ``prefix`` the reply
must start with, rather than checking the module constant unconditionally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._codec import _bcd_decode_value, bcd_encode_value
from ._frame import (
    CONTROLLER_ADDR,
    _CMD_CTL_MEM,
    _CTL_MEM_SYSTEM_DATE,
    _CTL_MEM_SYSTEM_TIME,
    _CTL_MEM_UTC_OFFSET,
    _SUB_CTL_MEM,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


@expose_command_key(lambda cmd_map: "get_transceiver_id")
@require_cmd_map
def get_transceiver_id(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read transceiver ID command (0x19 0x00)."""
    return _build_from_map(
        cmd_map, "get_transceiver_id", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "get_band_edge_freq")
@require_cmd_map
def get_band_edge_freq(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read band-edge frequency command (0x02)."""
    return _build_from_map(
        cmd_map, "get_band_edge_freq", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "get_tuner_status")
@require_cmd_map
def get_tuner_status(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read tuner/ATU status command (0x1C 0x01)."""
    return _build_from_map(
        cmd_map, "get_tuner_status", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_tuner_status")
@require_cmd_map
def set_tuner_status(
    value: int, to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a set tuner/ATU status command (0x1C 0x01). 0=off, 1=on, 2=tune."""
    if value not in (0, 1, 2):
        raise ValueError(f"Tuner status must be 0, 1, or 2, got {value}")
    return _build_from_map(
        cmd_map,
        "set_tuner_status",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([value]),
    )


@expose_command_key(lambda cmd_map: "get_xfc_status")
@require_cmd_map
def get_xfc_status(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read XFC status command (0x1C 0x02)."""
    return _build_from_map(
        cmd_map, "get_xfc_status", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_xfc_status")
@require_cmd_map
def set_xfc_status(
    on: bool, to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a set XFC status command (0x1C 0x02)."""
    return _build_from_map(
        cmd_map,
        "set_xfc_status",
        to_addr=to_addr,
        from_addr=from_addr,
        data=b"\x01" if on else b"\x00",
    )


@expose_command_key(lambda cmd_map: "get_tx_freq_monitor")
@require_cmd_map
def get_tx_freq_monitor(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read TX frequency monitor status command (0x1C 0x03)."""
    return _build_from_map(
        cmd_map, "get_tx_freq_monitor", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_tx_freq_monitor")
@require_cmd_map
def set_tx_freq_monitor(
    on: bool, to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a set TX frequency monitor command (0x1C 0x03)."""
    return _build_from_map(
        cmd_map,
        "set_tx_freq_monitor",
        to_addr=to_addr,
        from_addr=from_addr,
        data=b"\x01" if on else b"\x00",
    )


# --- RIT/XIT ---


@expose_command_key(lambda cmd_map: "get_rit_frequency")
@require_cmd_map
def get_rit_frequency(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read RIT frequency offset command (0x21 0x00)."""
    return _build_from_map(
        cmd_map, "get_rit_frequency", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_rit_frequency")
@require_cmd_map
def set_rit_frequency(
    offset_hz: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set RIT frequency offset command (0x21 0x00)."""
    if not -9999 <= offset_hz <= 9999:
        raise ValueError(f"RIT offset must be ±9999 Hz, got {offset_hz}")
    abs_hz = abs(offset_hz)
    d0 = ((abs_hz % 100 // 10) << 4) | (abs_hz % 10)
    d1 = ((abs_hz % 10000 // 1000) << 4) | (abs_hz % 1000 // 100)
    sign = b"\x01" if offset_hz < 0 else b"\x00"
    return _build_from_map(
        cmd_map,
        "set_rit_frequency",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([d0, d1]) + sign,
    )


def parse_rit_frequency_response(data: bytes) -> int:
    """Parse RIT frequency response data (2-byte BCD + sign byte)."""
    if len(data) < 3:
        return 0
    d0, d1, sign = data[0], data[1], data[2]
    hz = (d1 >> 4) * 1000 + (d1 & 0x0F) * 100 + (d0 >> 4) * 10 + (d0 & 0x0F)
    return -hz if sign else hz


@expose_command_key(lambda cmd_map: "get_rit_status")
@require_cmd_map
def get_rit_status(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_rit_status", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_rit_status")
@require_cmd_map
def set_rit_status(
    on: bool, to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map,
        "set_rit_status",
        to_addr=to_addr,
        from_addr=from_addr,
        data=b"\x01" if on else b"\x00",
    )


@expose_command_key(lambda cmd_map: "get_rit_tx_status")
@require_cmd_map
def get_rit_tx_status(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_rit_tx_status", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_rit_tx_status")
@require_cmd_map
def set_rit_tx_status(
    on: bool, to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map,
        "set_rit_tx_status",
        to_addr=to_addr,
        from_addr=from_addr,
        data=b"\x01" if on else b"\x00",
    )


# --- Date / Time / UTC Offset ---
#
# The six builders below resolve the direction-prefixed keys
# (`get_system_date`/`set_system_date` etc.) per the owner ruling in this
# module's docstring, not the bare `"system_date"`/`"system_time"`/
# `"utc_offset"` names their pre-migration `cmd_map` branches used to look
# up -- those bare names are still, and will remain, absent from every
# profile (`tests/test_undeclared_command_policy.py` pins `"system_date"`
# as one of the names that stays universally undeclared).


@expose_command_key(lambda cmd_map: "get_system_date")
@require_cmd_map
def get_system_date(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_system_date", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_system_date")
@require_cmd_map
def set_system_date(
    year: int,
    month: int,
    day: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    if not 2000 <= year <= 2099:
        raise ValueError(f"Year must be 2000-2099, got {year}")
    if not 1 <= month <= 12:
        raise ValueError(f"Month must be 1-12, got {month}")
    if not 1 <= day <= 31:
        raise ValueError(f"Day must be 1-31, got {day}")
    bcd = (
        bcd_encode_value(year, byte_count=2)
        + bcd_encode_value(month, byte_count=1)
        + bcd_encode_value(day, byte_count=1)
    )
    return _build_from_map(
        cmd_map, "set_system_date", to_addr=to_addr, from_addr=from_addr, data=bcd
    )


def parse_system_date_response(
    frame: CivFrame,
    *,
    prefix: bytes = _CTL_MEM_SYSTEM_DATE,
) -> tuple[int, int, int]:
    """Parse a system-date reply.

    ``prefix`` is the map-derived extended-address bytes the caller's
    request used (`runtime/radio.py: CoreRadio._expect_shape`); it defaults
    to the shared IC-7610-shaped constant only so tests built before this
    migration, which never passed one, keep working unchanged.
    """
    if frame.command != _CMD_CTL_MEM or frame.sub != _SUB_CTL_MEM:
        raise ValueError(f"Not a system date response: 0x{frame.command:02x}")
    data = frame.data
    if not data.startswith(prefix):
        raise ValueError(f"System date prefix mismatch: {data.hex()}")
    data = data[len(prefix) :]
    if len(data) < 4:
        raise ValueError(f"System date payload too short: {len(data)} bytes")
    year = _bcd_decode_value(data[0:2])
    month = _bcd_decode_value(data[2:3])
    day = _bcd_decode_value(data[3:4])
    return (year, month, day)


@expose_command_key(lambda cmd_map: "get_system_time")
@require_cmd_map
def get_system_time(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_system_time", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_system_time")
@require_cmd_map
def set_system_time(
    hour: int,
    minute: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    if not 0 <= hour <= 23:
        raise ValueError(f"Hour must be 0-23, got {hour}")
    if not 0 <= minute <= 59:
        raise ValueError(f"Minute must be 0-59, got {minute}")
    bcd = bcd_encode_value(hour, byte_count=1) + bcd_encode_value(minute, byte_count=1)
    return _build_from_map(
        cmd_map, "set_system_time", to_addr=to_addr, from_addr=from_addr, data=bcd
    )


def parse_system_time_response(
    frame: CivFrame,
    *,
    prefix: bytes = _CTL_MEM_SYSTEM_TIME,
) -> tuple[int, int]:
    """Parse a system-time reply. See ``parse_system_date_response`` for ``prefix``."""
    if frame.command != _CMD_CTL_MEM or frame.sub != _SUB_CTL_MEM:
        raise ValueError(f"Not a system time response: 0x{frame.command:02x}")
    data = frame.data
    if not data.startswith(prefix):
        raise ValueError(f"System time prefix mismatch: {data.hex()}")
    data = data[len(prefix) :]
    if len(data) < 2:
        raise ValueError(f"System time payload too short: {len(data)} bytes")
    return (_bcd_decode_value(data[0:1]), _bcd_decode_value(data[1:2]))


@expose_command_key(lambda cmd_map: "get_utc_offset")
@require_cmd_map
def get_utc_offset(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_from_map(
        cmd_map, "get_utc_offset", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_utc_offset")
@require_cmd_map
def set_utc_offset(
    hours: int,
    minutes: int,
    is_negative: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    if not 0 <= hours <= 14:
        raise ValueError(f"UTC offset hours must be 0-14, got {hours}")
    if minutes not in (0, 15, 30, 45):
        raise ValueError(f"UTC offset minutes must be 0/15/30/45, got {minutes}")
    payload = (
        bcd_encode_value(hours, byte_count=1)
        + bcd_encode_value(minutes, byte_count=1)
        + (b"\x01" if is_negative else b"\x00")
    )
    return _build_from_map(
        cmd_map, "set_utc_offset", to_addr=to_addr, from_addr=from_addr, data=payload
    )


def parse_utc_offset_response(
    frame: CivFrame,
    *,
    prefix: bytes = _CTL_MEM_UTC_OFFSET,
) -> tuple[int, int, bool]:
    """Parse a UTC-offset reply. See ``parse_system_date_response`` for ``prefix``."""
    if frame.command != _CMD_CTL_MEM or frame.sub != _SUB_CTL_MEM:
        raise ValueError(f"Not a UTC offset response: 0x{frame.command:02x}")
    data = frame.data
    if not data.startswith(prefix):
        raise ValueError(f"UTC offset prefix mismatch: {data.hex()}")
    data = data[len(prefix) :]
    if len(data) < 3:
        raise ValueError(f"UTC offset payload too short: {len(data)} bytes")
    return (_bcd_decode_value(data[0:1]), _bcd_decode_value(data[1:2]), data[2] != 0x00)
