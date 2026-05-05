"""Mode commands (0x04/0x06), data mode, filter shape/width, SSB BW, AGC time constant."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types import FilterShape, Mode, SsbTxBandwidth
from ._builders import (
    _build_ctl_mem_single_bcd_get,
    _build_ctl_mem_single_bcd_set,
    _build_function_get,
    _build_function_value_set,
)
from ._codec import bcd_encode_value
from ._frame import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _CMD_CTL_MEM,
    _CMD_MODE_GET,
    _CMD_MODE_SET,
    _SUB_AGC_TIME_CONSTANT,
    _SUB_DATA_MODE,
    _SUB_FILTER_SHAPE,
    _SUB_FILTER_WIDTH,
    _SUB_MAIN_SUB_TRACKING,
    _SUB_SSB_TX_BANDWIDTH,
    _build_from_map,
    build_civ_frame,
    build_cmd29_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


def get_mode(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'get mode' CI-V command."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_mode", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_MODE_GET)


def set_mode(
    mode: Mode,
    filter_width: int | None = None,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'set mode' CI-V command.

    Args:
        mode: Operating mode.
        filter_width: Optional filter number (1-3).
        to_addr: Radio CI-V address.
        from_addr: Controller CI-V address.
        receiver: RECEIVER_MAIN (0x00) or RECEIVER_SUB (0x01).

    Returns:
        CI-V frame bytes.
    """
    data = bytes([mode])
    if filter_width is not None:
        data += bytes([filter_width])
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_mode",
            to_addr=to_addr,
            from_addr=from_addr,
            data=data,
            command29=receiver != RECEIVER_MAIN,
            receiver=receiver,
        )
    if receiver != RECEIVER_MAIN:
        return build_cmd29_frame(
            to_addr, from_addr, _CMD_MODE_SET, data=data, receiver=receiver
        )
    return build_civ_frame(to_addr, from_addr, _CMD_MODE_SET, data=data)


def parse_mode_response(frame: CivFrame) -> tuple[Mode, int | None]:
    """Parse a mode response frame.

    Returns:
        Tuple of (mode, filter_width or None).
    """
    if frame.command not in (_CMD_MODE_GET, 0x01):
        raise ValueError(f"Not a mode response: command 0x{frame.command:02x}")
    if len(frame.data) < 1:
        raise ValueError(
            "Mode response payload too short: expected at least 1 byte, "
            f"got {len(frame.data)}"
        )
    mode = Mode(frame.data[0])
    filt = frame.data[1] if len(frame.data) > 1 else None
    return mode, filt


# --- DATA mode commands (CI-V 0x1A 0x06) ---


def get_data_mode(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'get DATA mode' CI-V command (0x1A 0x06)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_data_mode", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_CTL_MEM, sub=_SUB_DATA_MODE)


def set_data_mode(
    on: int | bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'set DATA mode' CI-V command (0x1A 0x06 <0x00-0x03>).

    Args:
        on: False/0 to disable, True/1 to enable DATA1, or an explicit DATA mode 0-3.
    """
    mode_value = int(on) if isinstance(on, bool) else int(on)
    if not 0 <= mode_value <= 3:
        raise ValueError(f"DATA mode must be 0-3, got {mode_value}")

    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_data_mode",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([mode_value]),
            receiver=receiver,
            command29=(receiver != RECEIVER_MAIN),
        )
    if receiver != RECEIVER_MAIN:
        return build_cmd29_frame(
            to_addr,
            from_addr,
            _CMD_CTL_MEM,
            sub=_SUB_DATA_MODE,
            data=bytes([mode_value]),
            receiver=receiver,
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_CTL_MEM,
        sub=_SUB_DATA_MODE,
        data=bytes([mode_value]),
    )


def parse_data_mode_response(frame: CivFrame) -> bool:
    """Parse a DATA mode response frame.

    Returns:
        True if DATA mode is active (data[0] != 0x00), False otherwise.
    """
    if frame.command != _CMD_CTL_MEM or frame.sub != _SUB_DATA_MODE:
        raise ValueError(
            f"Not a DATA mode response: cmd=0x{frame.command:02x} sub=0x{frame.sub if frame.sub is not None else 0:02x}"
        )
    if not frame.data:
        raise ValueError("DATA mode response has no data byte")
    return frame.data[0] != 0x00


# --- Filter shape / width ---


def get_filter_shape(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read DSP IF filter shape command."""
    return _build_function_get(
        _SUB_FILTER_SHAPE,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_filter_shape",
    )


def set_filter_shape(
    shape: FilterShape | int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set DSP IF filter shape command."""
    return _build_function_value_set(
        _SUB_FILTER_SHAPE,
        int(FilterShape(shape)),
        minimum=int(FilterShape.SHARP),
        maximum=int(FilterShape.SOFT),
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_filter_shape",
    )


def get_filter_width(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'get DSP IF filter width' CI-V command (0x1A 0x03, cmd29)."""
    return _build_ctl_mem_single_bcd_get(
        _SUB_FILTER_WIDTH,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_filter_width",
    )


def set_filter_width(
    filter_index: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'set DSP IF filter width' CI-V command (0x1A 0x03, cmd29).

    Args:
        filter_index: Filter width index encoded by the active radio profile.
        receiver: RECEIVER_MAIN (0x00) or RECEIVER_SUB (0x01).
    """
    if filter_index < 0:
        raise ValueError(f"Filter index must be non-negative, got {filter_index}")
    payload = bcd_encode_value(filter_index, byte_count=2)
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_filter_width",
            to_addr=to_addr,
            from_addr=from_addr,
            data=payload,
            receiver=receiver,
            command29=True,
        )
    return build_cmd29_frame(
        to_addr,
        from_addr,
        _CMD_CTL_MEM,
        sub=_SUB_FILTER_WIDTH,
        data=payload,
        receiver=receiver,
    )


# --- SSB TX bandwidth ---


def get_ssb_tx_bandwidth(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read SSB TX bandwidth preset command."""
    return _build_function_get(
        _SUB_SSB_TX_BANDWIDTH,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_ssb_tx_bandwidth",
    )


def set_ssb_tx_bandwidth(
    bandwidth: SsbTxBandwidth | int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set SSB TX bandwidth preset command."""
    return _build_function_value_set(
        _SUB_SSB_TX_BANDWIDTH,
        int(SsbTxBandwidth(bandwidth)),
        minimum=int(SsbTxBandwidth.WIDE),
        maximum=int(SsbTxBandwidth.NAR),
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_ssb_tx_bandwidth",
    )


# --- Main/Sub tracking ---


def get_main_sub_tracking(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read Main/Sub Tracking status command (0x16 0x5E)."""
    return _build_function_get(
        _SUB_MAIN_SUB_TRACKING,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_main_sub_tracking",
    )


def set_main_sub_tracking(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set Main/Sub Tracking status command (0x16 0x5E)."""
    from ._builders import _build_function_bool_set

    return _build_function_bool_set(
        _SUB_MAIN_SUB_TRACKING,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_main_sub_tracking",
    )


# --- AGC time constant ---


def get_agc_time_constant(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read AGC time constant command."""
    return _build_ctl_mem_single_bcd_get(
        _SUB_AGC_TIME_CONSTANT,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_agc_time_constant",
    )


def set_agc_time_constant(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set AGC time constant command."""
    return _build_ctl_mem_single_bcd_set(
        _SUB_AGC_TIME_CONSTANT,
        value,
        minimum=0,
        maximum=13,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_agc_time_constant",
    )
