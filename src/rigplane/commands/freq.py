"""Frequency commands (0x03/0x05/0x25/0x26)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types import Mode, bcd_decode, bcd_encode
from ._frame import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _CMD_BAND_EDGE,
    _CMD_FREQ_GET,
    _CMD_FREQ_SET,
    _CMD_SELECTED_FREQ,
    _CMD_SELECTED_MODE,
    _build_from_map,
    build_civ_frame,
    build_cmd29_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


def get_freq(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'get frequency' CI-V command."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_freq", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_FREQ_GET)


def set_freq(
    freq_hz: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'set frequency' CI-V command.

    Note:
        BCD encoding uses 5 bytes (10 decimal digits), so the maximum
        representable frequency is 9,999,999,999 Hz (~10 GHz).  Frequencies
        outside this range will raise ``ValueError`` from :func:`bcd_encode`.

    Args:
        freq_hz: Frequency in Hz (0 - 9,999,999,999).
        to_addr: Radio CI-V address.
        from_addr: Controller CI-V address.
        receiver: RECEIVER_MAIN (0x00) or RECEIVER_SUB (0x01).

    Returns:
        CI-V frame bytes.
    """
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_freq",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bcd_encode(freq_hz),
            receiver=receiver,
            command29=(receiver != RECEIVER_MAIN),
        )
    bcd = bcd_encode(freq_hz)
    if receiver != RECEIVER_MAIN:
        return build_cmd29_frame(
            to_addr, from_addr, _CMD_FREQ_SET, data=bcd, receiver=receiver
        )
    return build_civ_frame(to_addr, from_addr, _CMD_FREQ_SET, data=bcd)


def parse_frequency_response(frame: CivFrame) -> int:
    """Parse a frequency response frame.

    Args:
        frame: Parsed CivFrame (command 0x02/0x03/0x00 with 5-byte BCD data).

    Returns:
        Frequency in Hz.

    Raises:
        ValueError: If frame is not a frequency response.
    """
    if frame.command not in (_CMD_BAND_EDGE, _CMD_FREQ_GET, 0x00):
        raise ValueError(f"Not a frequency response: command 0x{frame.command:02x}")
    return bcd_decode(frame.data)


# --- Selected / Unselected receiver freq & mode (0x25/0x26) ---


def get_selected_freq(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
) -> bytes:
    """Build a 'get selected receiver frequency' CI-V command (0x25 0x00)."""
    return build_civ_frame(to_addr, from_addr, _CMD_SELECTED_FREQ, data=bytes([0x00]))


def get_unselected_freq(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
) -> bytes:
    """Build a 'get unselected receiver frequency' CI-V command (0x25 0x01)."""
    return build_civ_frame(to_addr, from_addr, _CMD_SELECTED_FREQ, data=bytes([0x01]))


def parse_selected_freq_response(frame: CivFrame) -> tuple[int, int]:
    """Parse a 0x25 selected/unselected frequency response.

    Returns:
        Tuple of (receiver_byte, frequency_hz).
    """
    if frame.command != _CMD_SELECTED_FREQ:
        raise ValueError(f"Not a 0x25 response: command 0x{frame.command:02x}")
    if len(frame.data) < 6:
        raise ValueError(
            f"0x25 response payload too short: expected >=6 bytes, got {len(frame.data)}"
        )
    receiver_byte = frame.data[0]
    freq = bcd_decode(frame.data[1:6])
    return receiver_byte, freq


def get_selected_mode(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
) -> bytes:
    """Build a 'get selected receiver mode' CI-V command (0x26 0x00)."""
    return build_civ_frame(to_addr, from_addr, _CMD_SELECTED_MODE, data=bytes([0x00]))


def get_unselected_mode(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
) -> bytes:
    """Build a 'get unselected receiver mode' CI-V command (0x26 0x01)."""
    return build_civ_frame(to_addr, from_addr, _CMD_SELECTED_MODE, data=bytes([0x01]))


def parse_selected_mode_response(
    frame: CivFrame,
) -> tuple[int, Mode, int | None, int | None]:
    """Parse a 0x26 selected/unselected mode response.

    Returns:
        Tuple of (receiver_byte, mode, data_mode_or_None, filter_or_None).
    """
    if frame.command != _CMD_SELECTED_MODE:
        raise ValueError(f"Not a 0x26 response: command 0x{frame.command:02x}")
    if len(frame.data) < 2:
        raise ValueError(
            f"0x26 response payload too short: expected >=2 bytes, got {len(frame.data)}"
        )
    receiver_byte = frame.data[0]
    mode = Mode(frame.data[1])
    data_mode = frame.data[2] if len(frame.data) >= 3 else None
    filt = frame.data[3] if len(frame.data) >= 4 else None
    return receiver_byte, mode, data_mode, filt


# Backward-compat aliases
get_frequency = get_freq
set_frequency = set_freq
