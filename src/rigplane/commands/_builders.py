"""Shared builder templates used by multiple leaf modules.

Imports from ``_frame`` and ``_codec`` only -- never from leaf modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ._codec import _bcd_byte, _bcd_decode_value, _level_bcd_encode, bcd_encode_value
from ._frame import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _CMD_CTL_MEM,
    _CMD_LEVEL,
    _CMD_METER,
    _CMD_PREAMP,
    _SUB_CTL_MEM,
    _build_from_map,
    build_civ_frame,
    build_cmd29_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


def _build_level_get(
    sub: int,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    command29: bool = False,
    cmd_map: CommandMap | None = None,
    cmd_name: str | None = None,
) -> bytes:
    if cmd_map is not None and cmd_name is not None:
        return _build_from_map(
            cmd_map,
            cmd_name,
            to_addr=to_addr,
            from_addr=from_addr,
            receiver=receiver,
            command29=command29,
        )
    if command29:
        return build_cmd29_frame(
            to_addr,
            from_addr,
            _CMD_LEVEL,
            sub=sub,
            receiver=receiver,
        )
    return build_civ_frame(to_addr, from_addr, _CMD_LEVEL, sub=sub)


def _build_level_set(
    sub: int,
    value: int,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    command29: bool = False,
    encoder: Callable[[int], bytes] = _level_bcd_encode,
    cmd_map: CommandMap | None = None,
    cmd_name: str | None = None,
) -> bytes:
    payload = encoder(value)
    if cmd_map is not None and cmd_name is not None:
        return _build_from_map(
            cmd_map,
            cmd_name,
            to_addr=to_addr,
            from_addr=from_addr,
            data=payload,
            receiver=receiver,
            command29=command29,
        )
    if command29:
        return build_cmd29_frame(
            to_addr,
            from_addr,
            _CMD_LEVEL,
            sub=sub,
            data=payload,
            receiver=receiver,
        )
    return build_civ_frame(to_addr, from_addr, _CMD_LEVEL, sub=sub, data=payload)


def _build_ctl_mem_get(
    prefix: bytes,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    cmd_name: str | None = None,
) -> bytes:
    if cmd_map is not None and cmd_name is not None:
        # When using cmd_map, wire bytes already include the full command structure
        # including any data prefix, so don't pass prefix as data
        return _build_from_map(
            cmd_map, cmd_name, to_addr=to_addr, from_addr=from_addr, data=None
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_CTL_MEM,
        sub=_SUB_CTL_MEM,
        data=prefix,
    )


def _build_ctl_mem_set(
    prefix: bytes,
    value: int,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    byte_count: int,
    cmd_map: CommandMap | None = None,
    cmd_name: str | None = None,
) -> bytes:
    data = prefix + bcd_encode_value(value, byte_count=byte_count)
    if cmd_map is not None and cmd_name is not None:
        return _build_from_map(
            cmd_map, cmd_name, to_addr=to_addr, from_addr=from_addr, data=data
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_CTL_MEM,
        sub=_SUB_CTL_MEM,
        data=data,
    )


def _build_meter_bool_get(
    sub: int,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    command29: bool = False,
    cmd_map: CommandMap | None = None,
    cmd_name: str | None = None,
) -> bytes:
    if cmd_map is not None and cmd_name is not None:
        return _build_from_map(
            cmd_map,
            cmd_name,
            to_addr=to_addr,
            from_addr=from_addr,
            receiver=receiver,
            command29=command29,
        )
    if command29:
        return build_cmd29_frame(
            to_addr,
            from_addr,
            _CMD_METER,
            sub=sub,
            receiver=receiver,
        )
    return build_civ_frame(to_addr, from_addr, _CMD_METER, sub=sub)


def _build_function_get(
    sub: int,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    command29: bool = False,
    cmd_map: CommandMap | None = None,
    cmd_name: str | None = None,
) -> bytes:
    if cmd_map is not None and cmd_name is not None:
        return _build_from_map(
            cmd_map,
            cmd_name,
            to_addr=to_addr,
            from_addr=from_addr,
            receiver=receiver,
            command29=command29,
        )
    if command29:
        return build_cmd29_frame(
            to_addr,
            from_addr,
            _CMD_PREAMP,
            sub=sub,
            receiver=receiver,
        )
    return build_civ_frame(to_addr, from_addr, _CMD_PREAMP, sub=sub)


def _build_function_bool_set(
    sub: int,
    on: bool,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    command29: bool = False,
    cmd_map: CommandMap | None = None,
    cmd_name: str | None = None,
) -> bytes:
    payload = b"\x01" if on else b"\x00"
    if cmd_map is not None and cmd_name is not None:
        return _build_from_map(
            cmd_map,
            cmd_name,
            to_addr=to_addr,
            from_addr=from_addr,
            data=payload,
            receiver=receiver,
            command29=command29,
        )
    if command29:
        return build_cmd29_frame(
            to_addr,
            from_addr,
            _CMD_PREAMP,
            sub=sub,
            data=payload,
            receiver=receiver,
        )
    return build_civ_frame(to_addr, from_addr, _CMD_PREAMP, sub=sub, data=payload)


def _build_function_value_set(
    sub: int,
    value: int,
    *,
    minimum: int,
    maximum: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    command29: bool = False,
    cmd_map: CommandMap | None = None,
    cmd_name: str | None = None,
) -> bytes:
    if not minimum <= value <= maximum:
        raise ValueError(f"Value must be {minimum}-{maximum}, got {value}")
    payload = bytes([_bcd_byte(value)])
    if cmd_map is not None and cmd_name is not None:
        return _build_from_map(
            cmd_map,
            cmd_name,
            to_addr=to_addr,
            from_addr=from_addr,
            data=payload,
            receiver=receiver,
            command29=command29,
        )
    if command29:
        return build_cmd29_frame(
            to_addr,
            from_addr,
            _CMD_PREAMP,
            sub=sub,
            data=payload,
            receiver=receiver,
        )
    return build_civ_frame(to_addr, from_addr, _CMD_PREAMP, sub=sub, data=payload)


def _build_ctl_mem_single_bcd_get(
    sub: int,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    command29: bool = False,
    cmd_map: CommandMap | None = None,
    cmd_name: str | None = None,
) -> bytes:
    if cmd_map is not None and cmd_name is not None:
        return _build_from_map(
            cmd_map,
            cmd_name,
            to_addr=to_addr,
            from_addr=from_addr,
            receiver=receiver,
            command29=command29,
        )
    if command29:
        return build_cmd29_frame(
            to_addr,
            from_addr,
            _CMD_CTL_MEM,
            sub=sub,
            receiver=receiver,
        )
    return build_civ_frame(to_addr, from_addr, _CMD_CTL_MEM, sub=sub)


def _build_ctl_mem_single_bcd_set(
    sub: int,
    value: int,
    *,
    minimum: int,
    maximum: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    command29: bool = False,
    cmd_map: CommandMap | None = None,
    cmd_name: str | None = None,
) -> bytes:
    if not minimum <= value <= maximum:
        raise ValueError(f"Value must be {minimum}-{maximum}, got {value}")
    payload = bytes([_bcd_byte(value)])
    if cmd_map is not None and cmd_name is not None:
        return _build_from_map(
            cmd_map,
            cmd_name,
            to_addr=to_addr,
            from_addr=from_addr,
            data=payload,
            receiver=receiver,
            command29=command29,
        )
    if command29:
        return build_cmd29_frame(
            to_addr,
            from_addr,
            _CMD_CTL_MEM,
            sub=sub,
            data=payload,
            receiver=receiver,
        )
    return build_civ_frame(to_addr, from_addr, _CMD_CTL_MEM, sub=sub, data=payload)


def parse_level_response(
    frame: CivFrame,
    *,
    command: int = _CMD_LEVEL,
    sub: int | None = None,
    prefix: bytes = b"",
    bcd_bytes: int = 2,
) -> int:
    """Parse a BCD-encoded level/config response."""
    if frame.command != command:
        raise ValueError(f"Not a level response: command 0x{frame.command:02x}")
    if sub is not None and frame.sub != sub:
        got = 0 if frame.sub is None else frame.sub
        raise ValueError(
            f"Not a level response: sub-command 0x{got:02x} != 0x{sub:02x}"
        )
    data = frame.data
    if prefix:
        if not data.startswith(prefix):
            raise ValueError(
                f"Level response prefix mismatch: expected {prefix.hex()}, got {data.hex()}"
            )
        data = data[len(prefix) :]
    if len(data) < bcd_bytes:
        raise ValueError(
            f"Level response payload too short: expected at least {bcd_bytes} bytes, got {len(data)}"
        )
    return _bcd_decode_value(data[:bcd_bytes])


def parse_bool_response(
    frame: CivFrame,
    *,
    command: int,
    sub: int | None = None,
    prefix: bytes = b"",
) -> bool:
    """Parse a boolean CI-V response payload."""
    if frame.command != command:
        raise ValueError(f"Not a boolean response: command 0x{frame.command:02x}")
    if sub is not None and frame.sub != sub:
        got = 0 if frame.sub is None else frame.sub
        raise ValueError(
            f"Not a boolean response: sub-command 0x{got:02x} != 0x{sub:02x}"
        )
    data = frame.data
    if prefix:
        if not data.startswith(prefix):
            raise ValueError(
                f"Boolean response prefix mismatch: expected {prefix.hex()}, got {data.hex()}"
            )
        data = data[len(prefix) :]
    if not data:
        raise ValueError("Boolean response has no payload byte")
    return data[0] != 0x00
