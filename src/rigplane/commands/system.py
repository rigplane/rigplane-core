"""System commands: transceiver ID, band edge, tuner, XFC, TX freq monitor, RIT/XIT."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._codec import _bcd_decode_value, bcd_encode_value
from ._frame import (
    CONTROLLER_ADDR,
    _CMD_BAND_EDGE,
    _CMD_CTL_MEM,
    _CMD_PTT,
    _CMD_RIT,
    _CMD_TRANSCEIVER_ID,
    _CTL_MEM_SYSTEM_DATE,
    _CTL_MEM_SYSTEM_TIME,
    _CTL_MEM_UTC_OFFSET,
    _SUB_CTL_MEM,
    _SUB_RIT_FREQ,
    _SUB_RIT_STATUS,
    _SUB_RIT_TX_STATUS,
    _SUB_TRANSCEIVER_ID,
    _SUB_TUNER_STATUS,
    _SUB_TX_FREQ_MONITOR,
    _SUB_XFC_STATUS,
    _build_from_map,
    build_civ_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


def get_transceiver_id(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read transceiver ID command (0x19 0x00)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_transceiver_id", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_TRANSCEIVER_ID, sub=_SUB_TRANSCEIVER_ID
    )


def get_band_edge_freq(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read band-edge frequency command (0x02)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_band_edge_freq", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_BAND_EDGE)


def get_tuner_status(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read tuner/ATU status command (0x1C 0x01)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_tuner_status", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_PTT, sub=_SUB_TUNER_STATUS)


def set_tuner_status(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set tuner/ATU status command (0x1C 0x01). 0=off, 1=on, 2=tune."""
    if value not in (0, 1, 2):
        raise ValueError(f"Tuner status must be 0, 1, or 2, got {value}")
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_tuner_status",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([value]),
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_PTT, sub=_SUB_TUNER_STATUS, data=bytes([value])
    )


def get_xfc_status(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read XFC status command (0x1C 0x02)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_xfc_status", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_PTT, sub=_SUB_XFC_STATUS)


def set_xfc_status(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set XFC status command (0x1C 0x02)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_xfc_status",
            to_addr=to_addr,
            from_addr=from_addr,
            data=b"\x01" if on else b"\x00",
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_PTT,
        sub=_SUB_XFC_STATUS,
        data=b"\x01" if on else b"\x00",
    )


def get_tx_freq_monitor(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read TX frequency monitor status command (0x1C 0x03)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_tx_freq_monitor", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_PTT, sub=_SUB_TX_FREQ_MONITOR)


def set_tx_freq_monitor(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set TX frequency monitor command (0x1C 0x03)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_tx_freq_monitor",
            to_addr=to_addr,
            from_addr=from_addr,
            data=b"\x01" if on else b"\x00",
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_PTT,
        sub=_SUB_TX_FREQ_MONITOR,
        data=b"\x01" if on else b"\x00",
    )


# --- RIT/XIT ---


def get_rit_frequency(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build a read RIT frequency offset command (0x21 0x00)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_rit_frequency", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_RIT, sub=_SUB_RIT_FREQ)


def set_rit_frequency(
    offset_hz: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set RIT frequency offset command (0x21 0x00)."""
    if not -9999 <= offset_hz <= 9999:
        raise ValueError(f"RIT offset must be \u00b19999 Hz, got {offset_hz}")
    abs_hz = abs(offset_hz)
    d0 = ((abs_hz % 100 // 10) << 4) | (abs_hz % 10)
    d1 = ((abs_hz % 10000 // 1000) << 4) | (abs_hz % 1000 // 100)
    sign = b"\x01" if offset_hz < 0 else b"\x00"
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_rit_frequency",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([d0, d1]) + sign,
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_RIT, sub=_SUB_RIT_FREQ, data=bytes([d0, d1]) + sign
    )


def parse_rit_frequency_response(data: bytes) -> int:
    """Parse RIT frequency response data (2-byte BCD + sign byte)."""
    if len(data) < 3:
        return 0
    d0, d1, sign = data[0], data[1], data[2]
    hz = (d1 >> 4) * 1000 + (d1 & 0x0F) * 100 + (d0 >> 4) * 10 + (d0 & 0x0F)
    return -hz if sign else hz


def get_rit_status(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_rit_status", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_RIT, sub=_SUB_RIT_STATUS)


def set_rit_status(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_rit_status",
            to_addr=to_addr,
            from_addr=from_addr,
            data=b"\x01" if on else b"\x00",
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_RIT,
        sub=_SUB_RIT_STATUS,
        data=b"\x01" if on else b"\x00",
    )


def get_rit_tx_status(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_rit_tx_status", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_RIT, sub=_SUB_RIT_TX_STATUS)


def set_rit_tx_status(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_rit_tx_status",
            to_addr=to_addr,
            from_addr=from_addr,
            data=b"\x01" if on else b"\x00",
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_RIT,
        sub=_SUB_RIT_TX_STATUS,
        data=b"\x01" if on else b"\x00",
    )


# --- Date / Time / UTC Offset ---


def get_system_date(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    from ._builders import _build_ctl_mem_get

    return _build_ctl_mem_get(
        _CTL_MEM_SYSTEM_DATE,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="system_date",
    )


def set_system_date(
    year: int,
    month: int,
    day: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
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
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "system_date", to_addr=to_addr, from_addr=from_addr, data=bcd
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_CTL_MEM,
        sub=_SUB_CTL_MEM,
        data=_CTL_MEM_SYSTEM_DATE + bcd,
    )


def parse_system_date_response(frame: CivFrame) -> tuple[int, int, int]:
    if frame.command != _CMD_CTL_MEM or frame.sub != _SUB_CTL_MEM:
        raise ValueError(f"Not a system date response: 0x{frame.command:02x}")
    data = frame.data
    if not data.startswith(_CTL_MEM_SYSTEM_DATE):
        raise ValueError(f"System date prefix mismatch: {data.hex()}")
    data = data[len(_CTL_MEM_SYSTEM_DATE) :]
    if len(data) < 4:
        raise ValueError(f"System date payload too short: {len(data)} bytes")
    year = _bcd_decode_value(data[0:2])
    month = _bcd_decode_value(data[2:3])
    day = _bcd_decode_value(data[3:4])
    return (year, month, day)


def get_system_time(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    from ._builders import _build_ctl_mem_get

    return _build_ctl_mem_get(
        _CTL_MEM_SYSTEM_TIME,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="system_time",
    )


def set_system_time(
    hour: int,
    minute: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if not 0 <= hour <= 23:
        raise ValueError(f"Hour must be 0-23, got {hour}")
    if not 0 <= minute <= 59:
        raise ValueError(f"Minute must be 0-59, got {minute}")
    bcd = bcd_encode_value(hour, byte_count=1) + bcd_encode_value(minute, byte_count=1)
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "system_time", to_addr=to_addr, from_addr=from_addr, data=bcd
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_CTL_MEM,
        sub=_SUB_CTL_MEM,
        data=_CTL_MEM_SYSTEM_TIME + bcd,
    )


def parse_system_time_response(frame: CivFrame) -> tuple[int, int]:
    if frame.command != _CMD_CTL_MEM or frame.sub != _SUB_CTL_MEM:
        raise ValueError(f"Not a system time response: 0x{frame.command:02x}")
    data = frame.data
    if not data.startswith(_CTL_MEM_SYSTEM_TIME):
        raise ValueError(f"System time prefix mismatch: {data.hex()}")
    data = data[len(_CTL_MEM_SYSTEM_TIME) :]
    if len(data) < 2:
        raise ValueError(f"System time payload too short: {len(data)} bytes")
    return (_bcd_decode_value(data[0:1]), _bcd_decode_value(data[1:2]))


def get_utc_offset(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    from ._builders import _build_ctl_mem_get

    return _build_ctl_mem_get(
        _CTL_MEM_UTC_OFFSET,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="utc_offset",
    )


def set_utc_offset(
    hours: int,
    minutes: int,
    is_negative: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
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
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "utc_offset", to_addr=to_addr, from_addr=from_addr, data=payload
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_CTL_MEM,
        sub=_SUB_CTL_MEM,
        data=_CTL_MEM_UTC_OFFSET + payload,
    )


def parse_utc_offset_response(frame: CivFrame) -> tuple[int, int, bool]:
    if frame.command != _CMD_CTL_MEM or frame.sub != _SUB_CTL_MEM:
        raise ValueError(f"Not a UTC offset response: 0x{frame.command:02x}")
    data = frame.data
    if not data.startswith(_CTL_MEM_UTC_OFFSET):
        raise ValueError(f"UTC offset prefix mismatch: {data.hex()}")
    data = data[len(_CTL_MEM_UTC_OFFSET) :]
    if len(data) < 3:
        raise ValueError(f"UTC offset payload too short: {len(data)} bytes")
    return (_bcd_decode_value(data[0:1]), _bcd_decode_value(data[1:2]), data[2] != 0x00)
