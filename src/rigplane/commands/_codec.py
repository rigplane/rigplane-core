"""BCD and level encode/decode helpers.

No sibling imports -- only depends on the ``types`` module.
"""

from __future__ import annotations

from typing import Any, Sequence


def _level_bcd_encode(value: int) -> bytes:
    """Encode a 0-255 level as 2-byte BCD (0x00 0x00 to 0x02 0x55).

    Each byte holds two BCD digits: high nibble = tens, low nibble = units.
    Byte 0 = hundreds and tens, Byte 1 = tens and units... actually:
    Byte 0 = (d2 << 4 | d1), Byte 1 = (d0 << 4 | 0)? No --
    Looking at wfview: bcdHexToUChar reads 2 bytes as hundreds, tens, units.
    128 -> 0x01 0x28: byte0=0x01 (0,1), byte1=0x28 (2,8). So:
    byte0 high=0, low=1 -> 0*10+1=01, byte1 high=2, low=8 -> 28. Total=128.
    """
    if not 0 <= value <= 255:
        raise ValueError(f"Level must be 0-255, got {value}")
    d = f"{value:04d}"  # e.g. "0128"
    b0 = (int(d[0]) << 4) | int(d[1])
    b1 = (int(d[2]) << 4) | int(d[3])
    return bytes([b0, b1])


def _level_bcd_decode(data: bytes) -> int:
    """Decode 2-byte BCD level to 0-255 int."""
    if len(data) < 2:
        raise ValueError(
            f"Level payload too short: expected at least 2 bytes, got {len(data)}"
        )
    return _bcd_decode_value(data[:2])


def _bcd_decode_value(data: bytes) -> int:
    """Decode packed BCD bytes into an integer."""
    value = 0
    for index, byte in enumerate(data):
        high = (byte >> 4) & 0x0F
        low = byte & 0x0F
        if high > 9 or low > 9:
            raise ValueError(f"Invalid BCD digit in byte {index}: 0x{byte:02x}")
        value = (value * 100) + (high * 10) + low
    return value


def bcd_encode_value(value: int, *, byte_count: int) -> bytes:
    """Encode an integer as packed BCD using a fixed byte width."""
    if value < 0:
        raise ValueError(f"BCD value must be non-negative, got {value}")
    digits = byte_count * 2
    maximum = (10**digits) - 1
    if value > maximum:
        raise ValueError(f"BCD value must fit in {byte_count} byte(s), got {value}")
    text = f"{value:0{digits}d}"
    return bytes(
        (int(text[index]) << 4) | int(text[index + 1])
        for index in range(0, len(text), 2)
    )


def _bcd_byte(value: int) -> int:
    """Encode 0-99 integer into one BCD byte."""
    if not 0 <= value <= 99:
        raise ValueError(f"BCD byte value must be 0-99, got {value}")
    return ((value // 10) << 4) | (value % 10)


def _segment_value(segment: Any, key: str) -> int:
    if isinstance(segment, dict):
        return int(segment[key])
    return int(getattr(segment, key))


def filter_hz_to_index(hz: int, *, segments: Sequence[Any]) -> int:
    """Convert a filter width in Hz to a CI-V index using profile segments."""
    for segment in segments:
        hz_min = _segment_value(segment, "hz_min")
        hz_max = _segment_value(segment, "hz_max")
        step_hz = _segment_value(segment, "step_hz")
        index_min = _segment_value(segment, "index_min")
        if hz_min <= hz <= hz_max:
            delta = hz - hz_min
            if delta % step_hz != 0:
                raise ValueError(
                    f"Filter width {hz} is not aligned to {step_hz} Hz steps"
                )
            return index_min + (delta // step_hz)
    raise ValueError(f"Filter width {hz} is outside the configured segments")


def filter_index_to_hz(index: int, *, segments: Sequence[Any]) -> int:
    """Convert a CI-V filter-width index to Hz using profile segments."""
    ordered = sorted(segments, key=lambda segment: _segment_value(segment, "index_min"))
    for offset, segment in enumerate(ordered):
        hz_min = _segment_value(segment, "hz_min")
        hz_max = _segment_value(segment, "hz_max")
        step_hz = _segment_value(segment, "step_hz")
        index_min = _segment_value(segment, "index_min")
        step_count = ((hz_max - hz_min) // step_hz) + 1
        next_index = (
            _segment_value(ordered[offset + 1], "index_min")
            if offset + 1 < len(ordered)
            else index_min + step_count
        )
        if index_min <= index < next_index:
            return hz_min + ((index - index_min) * step_hz)
    raise ValueError(f"Filter width index {index} is outside the configured segments")


def table_index_to_hz(index: int, *, table: Sequence[int]) -> int:
    """Convert a table-based filter-width index to Hz.

    Used by rigs like FTX-1 where a 2-digit code maps directly to a
    position in a mode-dependent lookup table.
    """
    if not (0 <= index < len(table)):
        raise ValueError(
            f"Filter width index {index} is outside the table (0-{len(table) - 1})"
        )
    return table[index]


def hz_to_table_index(hz: int, *, table: Sequence[int]) -> int:
    """Convert Hz to the closest table-based filter-width index.

    Returns the index whose table entry is closest to *hz*.
    """
    if not table:
        raise ValueError("Filter width table is empty")
    best_idx = 0
    best_diff = abs(table[0] - hz)
    for idx, entry in enumerate(table):
        diff = abs(entry - hz)
        if diff < best_diff:
            best_diff = diff
            best_idx = idx
    return best_idx
