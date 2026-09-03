"""Mode commands (0x04/0x06), data mode, filter shape/width, SSB BW, AGC time constant.

Migrated onto the bound command map in MOR-2008 (batch 2,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N): all
fourteen builders now require ``cmd_map``, with no hardcoded fallback left.
Zero divergence rows -- every declaring profile's tuple already matched the
fallback's own bytes exactly (verified by grep across ``rigs/*.toml`` before
deleting each fallback).

Six of the fourteen route through `_builders.py` shared templates
(``_build_function_get``/``_build_function_value_set``/
``_build_function_bool_set``), which stay -- `dsp.py` (not migrated in this
batch) still calls them with ``cmd_map=None``, so their fallback branches
are not dead yet. ``_build_ctl_mem_single_bcd_get``/
``_build_ctl_mem_single_bcd_set`` are gone: this module was their only
caller, so their own ``cmd_map is None`` fallback branches would otherwise
become dead code nobody reads -- ``get_filter_width``/
``get_agc_time_constant``/``set_agc_time_constant`` now call
`_frame.py: _build_from_map` directly instead, the same de-delegation
`config.py`'s/`levels.py`'s own migrations already did for their
single-caller templates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types import FilterShape, Mode, SsbTxBandwidth
from ._builders import (
    _build_function_bool_set,
    _build_function_get,
    _build_function_value_set,
)
from ._codec import _bcd_byte, bcd_encode_value
from ._frame import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _CMD_CTL_MEM,
    _CMD_MODE_GET,
    _SUB_DATA_MODE,
    _SUB_FILTER_SHAPE,
    _SUB_MAIN_SUB_TRACKING,
    _SUB_SSB_TX_BANDWIDTH,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


@expose_command_key(lambda cmd_map: "get_mode")
@require_cmd_map
def get_mode(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a 'get mode' CI-V command."""
    return _build_from_map(cmd_map, "get_mode", to_addr=to_addr, from_addr=from_addr)


@expose_command_key(lambda cmd_map: "set_mode")
@require_cmd_map
def set_mode(
    mode: Mode,
    filter_width: int | None = None,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap,
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
    return _build_from_map(
        cmd_map,
        "set_mode",
        to_addr=to_addr,
        from_addr=from_addr,
        data=data,
        command29=receiver != RECEIVER_MAIN,
        receiver=receiver,
    )


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


@expose_command_key(lambda cmd_map: "get_data_mode")
@require_cmd_map
def get_data_mode(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a 'get DATA mode' CI-V command (0x1A 0x06)."""
    return _build_from_map(
        cmd_map, "get_data_mode", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_data_mode")
@require_cmd_map
def set_data_mode(
    on: int | bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a 'set DATA mode' CI-V command (0x1A 0x06 <0x00-0x03>).

    Args:
        on: False/0 to disable, True/1 to enable DATA1, or an explicit DATA mode 0-3.
    """
    mode_value = int(on) if isinstance(on, bool) else int(on)
    if not 0 <= mode_value <= 3:
        raise ValueError(f"DATA mode must be 0-3, got {mode_value}")

    return _build_from_map(
        cmd_map,
        "set_data_mode",
        to_addr=to_addr,
        from_addr=from_addr,
        data=(
            None
            if cmd_map._has_value_variants("set_data_mode")
            else bytes([mode_value])
        ),
        receiver=receiver,
        command29=(receiver != RECEIVER_MAIN),
        value=mode_value,
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


@expose_command_key(lambda cmd_map: "get_filter_shape")
@require_cmd_map
def get_filter_shape(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read DSP IF filter shape command."""
    return _build_function_get(
        _SUB_FILTER_SHAPE,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="get_filter_shape",
    )


@expose_command_key(lambda cmd_map: "set_filter_shape")
@require_cmd_map
def set_filter_shape(
    shape: FilterShape | int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set DSP IF filter shape command.

    Encodes the raw single-BCD-byte filter-shape value. Which values are
    legal is a per-profile ``[filter_shape] values`` domain, not a universal
    enum -- this builder only enforces the wire-format's single-BCD-byte
    range and leaves domain validation to the profile-aware caller
    (MOR-1534, mirrors MOR-1522's ``set_agc`` fix).
    """
    return _build_function_value_set(
        _SUB_FILTER_SHAPE,
        int(shape),
        minimum=0,
        maximum=99,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="set_filter_shape",
    )


@expose_command_key(lambda cmd_map: "get_filter_width")
@require_cmd_map
def get_filter_width(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a 'get DSP IF filter width' CI-V command (0x1A 0x03, cmd29)."""
    return _build_from_map(
        cmd_map,
        "get_filter_width",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
    )


@expose_command_key(lambda cmd_map: "set_filter_width")
@require_cmd_map
def set_filter_width(
    filter_index: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a 'set DSP IF filter width' CI-V command (0x1A 0x03, cmd29).

    Args:
        filter_index: Filter width index encoded by the active radio profile.
        receiver: RECEIVER_MAIN (0x00) or RECEIVER_SUB (0x01).
    """
    if filter_index < 0:
        raise ValueError(f"Filter index must be non-negative, got {filter_index}")
    payload = bcd_encode_value(filter_index, byte_count=2)
    return _build_from_map(
        cmd_map,
        "set_filter_width",
        to_addr=to_addr,
        from_addr=from_addr,
        data=payload,
        receiver=receiver,
        command29=True,
    )


# --- SSB TX bandwidth ---


@expose_command_key(lambda cmd_map: "get_ssb_tx_bandwidth")
@require_cmd_map
def get_ssb_tx_bandwidth(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read SSB TX bandwidth preset command."""
    return _build_function_get(
        _SUB_SSB_TX_BANDWIDTH,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_ssb_tx_bandwidth",
    )


@expose_command_key(lambda cmd_map: "set_ssb_tx_bandwidth")
@require_cmd_map
def set_ssb_tx_bandwidth(
    bandwidth: SsbTxBandwidth | int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set SSB TX bandwidth preset command.

    Encodes the raw single-BCD-byte bandwidth value. Which values are legal
    is a per-profile ``[ssb_tx_bw] values`` domain, not a universal enum --
    this builder only enforces the wire-format's single-BCD-byte range and
    leaves domain validation to the profile-aware caller (MOR-1534, mirrors
    MOR-1522's ``set_agc`` fix).
    """
    return _build_function_value_set(
        _SUB_SSB_TX_BANDWIDTH,
        int(bandwidth),
        minimum=0,
        maximum=99,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_ssb_tx_bandwidth",
    )


# --- Main/Sub tracking ---


@expose_command_key(lambda cmd_map: "get_main_sub_tracking")
@require_cmd_map
def get_main_sub_tracking(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read Main/Sub Tracking status command (0x16 0x5E)."""
    return _build_function_get(
        _SUB_MAIN_SUB_TRACKING,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_main_sub_tracking",
    )


@expose_command_key(lambda cmd_map: "set_main_sub_tracking")
@require_cmd_map
def set_main_sub_tracking(
    on: bool, to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a set Main/Sub Tracking status command (0x16 0x5E)."""
    return _build_function_bool_set(
        _SUB_MAIN_SUB_TRACKING,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_main_sub_tracking",
    )


# --- AGC time constant ---


@expose_command_key(lambda cmd_map: "get_agc_time_constant")
@require_cmd_map
def get_agc_time_constant(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read AGC time constant command."""
    return _build_from_map(
        cmd_map,
        "get_agc_time_constant",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_agc_time_constant")
@require_cmd_map
def set_agc_time_constant(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set AGC time constant command."""
    if not 0 <= value <= 13:
        raise ValueError(f"Value must be 0-13, got {value}")
    payload = bytes([_bcd_byte(value)])
    return _build_from_map(
        cmd_map,
        "set_agc_time_constant",
        to_addr=to_addr,
        from_addr=from_addr,
        data=payload,
        receiver=receiver,
        command29=command29,
    )
