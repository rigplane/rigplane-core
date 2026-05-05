"""Repeater tone/TSQL commands (0x1B family, 0x16 0x42/0x43)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._builders import _build_function_bool_set, _build_function_get
from ._codec import _bcd_byte, _bcd_decode_value
from ._frame import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _CMD_TONE,
    _SUB_REPEATER_TONE,
    _SUB_REPEATER_TSQL,
    _SUB_TONE_FREQ,
    _SUB_TSQL_FREQ,
    _build_from_map,
    build_cmd29_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


def _encode_tone_freq(freq_hz: float) -> bytes:
    """Encode tone frequency (Hz) to 3-byte BCD."""
    if not 67.0 <= freq_hz <= 254.1:
        raise ValueError(f"Tone frequency must be 67.0-254.1 Hz, got {freq_hz}")
    total_tenths = round(freq_hz * 10)
    integer_hz = total_tenths // 10
    hundreds = integer_hz // 100
    tens_units = integer_hz % 100
    tenths_digit = total_tenths % 10
    return bytes([_bcd_byte(hundreds), _bcd_byte(tens_units), _bcd_byte(tenths_digit)])


def _decode_tone_freq(data: bytes) -> float:
    """Decode 3-byte BCD to tone frequency (Hz)."""
    if len(data) < 3:
        raise ValueError(f"Expected 3 bytes for tone freq, got {len(data)}")
    hundreds = _bcd_decode_value(data[0:1])
    tens_units = _bcd_decode_value(data[1:2])
    tenths_digit = _bcd_decode_value(data[2:3])
    return float(hundreds * 100 + tens_units) + tenths_digit / 10.0


def get_repeater_tone(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to get repeater tone status (0x16 0x42)."""
    return _build_function_get(
        _SUB_REPEATER_TONE,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_repeater_tone",
    )


def set_repeater_tone(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to set repeater tone (0x16 0x42)."""
    return _build_function_bool_set(
        _SUB_REPEATER_TONE,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_repeater_tone",
    )


def get_repeater_tsql(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to get repeater TSQL status (0x16 0x43)."""
    return _build_function_get(
        _SUB_REPEATER_TSQL,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_repeater_tsql",
    )


def set_repeater_tsql(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to set repeater TSQL (0x16 0x43)."""
    return _build_function_bool_set(
        _SUB_REPEATER_TSQL,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_repeater_tsql",
    )


def get_tone_freq(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to get tone frequency (0x1B 0x00)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_tone_freq",
            to_addr=to_addr,
            from_addr=from_addr,
            command29=True,
            receiver=receiver,
        )
    return build_cmd29_frame(
        to_addr, from_addr, _CMD_TONE, sub=_SUB_TONE_FREQ, receiver=receiver
    )


def set_tone_freq(
    freq_hz: float,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to set tone frequency (0x1B 0x00)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_tone_freq",
            to_addr=to_addr,
            from_addr=from_addr,
            command29=True,
            receiver=receiver,
            data=_encode_tone_freq(freq_hz),
        )
    return build_cmd29_frame(
        to_addr,
        from_addr,
        _CMD_TONE,
        sub=_SUB_TONE_FREQ,
        data=_encode_tone_freq(freq_hz),
        receiver=receiver,
    )


def get_tsql_freq(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to get TSQL frequency (0x1B 0x01)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_tsql_freq",
            to_addr=to_addr,
            from_addr=from_addr,
            command29=True,
            receiver=receiver,
        )
    return build_cmd29_frame(
        to_addr, from_addr, _CMD_TONE, sub=_SUB_TSQL_FREQ, receiver=receiver
    )


def set_tsql_freq(
    freq_hz: float,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to set TSQL frequency (0x1B 0x01)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_tsql_freq",
            to_addr=to_addr,
            from_addr=from_addr,
            command29=True,
            receiver=receiver,
            data=_encode_tone_freq(freq_hz),
        )
    return build_cmd29_frame(
        to_addr,
        from_addr,
        _CMD_TONE,
        sub=_SUB_TSQL_FREQ,
        data=_encode_tone_freq(freq_hz),
        receiver=receiver,
    )


def parse_tone_freq_response(frame: CivFrame) -> tuple[int | None, float]:
    """Parse tone frequency response (0x1B 0x00)."""
    if frame.command != _CMD_TONE or frame.sub != _SUB_TONE_FREQ:
        raise ValueError(
            f"Not a tone freq response: 0x{frame.command:02x} sub=0x{frame.sub!r}"
        )
    if len(frame.data) < 3:
        raise ValueError(f"Expected 3 bytes for tone freq, got {len(frame.data)}")
    return (frame.receiver, _decode_tone_freq(frame.data))


def parse_tsql_freq_response(frame: CivFrame) -> tuple[int | None, float]:
    """Parse TSQL frequency response (0x1B 0x01)."""
    if frame.command != _CMD_TONE or frame.sub != _SUB_TSQL_FREQ:
        raise ValueError(
            f"Not a TSQL freq response: 0x{frame.command:02x} sub=0x{frame.sub!r}"
        )
    if len(frame.data) < 3:
        raise ValueError(f"Expected 3 bytes for TSQL freq, got {len(frame.data)}")
    return (frame.receiver, _decode_tone_freq(frame.data))
