"""Shared builder templates used by multiple leaf modules.

Imports from ``_frame`` and ``_codec`` only -- never from leaf modules.

``_build_function_get``/``_build_function_bool_set``/``_build_function_value_set``
lost their hardcoded fallback branches in MOR-2008 batch 3
(`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N),
when `dsp.py` -- their last caller still invoking them with ``cmd_map=None``
-- migrated onto the required-``cmd_map`` contract (`mode.py`/`tone.py`'s own
batch 2 migration left them alone for exactly this reason: see cc632498's
`mode.py` docstring). ``cmd_map``/``cmd_name`` are required now, matching
every builder that calls through them.

Each still takes a leading ``sub`` parameter its body no longer reads --
`_build_from_map` resolves the real command/sub pair from the profile map,
not from this argument. Left in place rather than removed, since removing
it would require touching every call site in `mode.py`/`tone.py` too (not
just `dsp.py`, this batch's scope); reported to the PR reviewer as a found,
not-fixed nit rather than swept in silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._codec import _bcd_byte, _bcd_decode_value
from ._frame import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _CMD_LEVEL,
    _build_from_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


def _build_function_get(
    sub: int,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    command29: bool = False,
    cmd_map: CommandMap,
    cmd_name: str,
) -> bytes:
    return _build_from_map(
        cmd_map,
        cmd_name,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


def _build_function_bool_set(
    sub: int,
    on: bool,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    command29: bool = False,
    cmd_map: CommandMap,
    cmd_name: str,
) -> bytes:
    payload = b"\x01" if on else b"\x00"
    return _build_from_map(
        cmd_map,
        cmd_name,
        to_addr=to_addr,
        from_addr=from_addr,
        data=payload,
        receiver=receiver,
        command29=command29,
    )


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
    cmd_map: CommandMap,
    cmd_name: str,
) -> bytes:
    if not minimum <= value <= maximum:
        raise ValueError(f"Value must be {minimum}-{maximum}, got {value}")
    payload = bytes([_bcd_byte(value)])
    return _build_from_map(
        cmd_map,
        cmd_name,
        to_addr=to_addr,
        from_addr=from_addr,
        data=payload,
        receiver=receiver,
        command29=command29,
    )


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
