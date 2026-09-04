"""Repeater tone/TSQL commands (0x1B family, 0x16 0x42/0x43).

Migrated onto the bound command map in MOR-2008 (batch 2,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N): all
eight builders now require ``cmd_map``, with no hardcoded fallback left.
Zero divergence rows -- every declaring profile's tuple already matched the
fallback's own bytes exactly (verified by grep across ``rigs/*.toml`` before
deleting each fallback).

``get_repeater_tone``/``set_repeater_tone``/``get_repeater_tsql``/
``set_repeater_tsql`` route through `_builders.py`'s shared
``_build_function_get``/``_build_function_bool_set`` templates, which stay:
`dsp.py` (not migrated in this batch) still calls them with ``cmd_map=None``,
so their fallback branches are not dead yet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._builders import _build_function_bool_set, _build_function_get
from ._codec import _bcd_decode_value, bcd_encode_value
from ._frame import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _CMD_TONE,
    _SUB_REPEATER_TONE,
    _SUB_REPEATER_TSQL,
    _SUB_TONE_FREQ,
    _SUB_TSQL_FREQ,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


_TONE_MIN_CENTIHZ = 6700
_TONE_MAX_CENTIHZ = 25410


def _validate_tone_freq_centihz(freq_centihz: int) -> int:
    """Validate the exact backend-neutral CTCSS unit before wire conversion."""
    if type(freq_centihz) is not int:
        raise TypeError("Tone frequency must be an int in centiHz")
    if not _TONE_MIN_CENTIHZ <= freq_centihz <= _TONE_MAX_CENTIHZ:
        raise ValueError(
            f"Tone frequency must be 6700-25410 centiHz, got {freq_centihz}"
        )
    if freq_centihz % 10:
        raise ValueError(
            f"Tone frequency must be divisible by 10 centiHz, got {freq_centihz}"
        )
    return freq_centihz


def _require_ctcss_domain(
    ctcss_tones_centihz: tuple[int, ...],
) -> tuple[int, ...]:
    """Require the resolved profile domain; never infer a legal-value set."""
    if not ctcss_tones_centihz:
        raise ValueError("CTCSS tone domain must not be empty")
    return ctcss_tones_centihz


def _require_profile_tone(
    freq_centihz: int,
    ctcss_tones_centihz: tuple[int, ...],
) -> int:
    freq_centihz = _validate_tone_freq_centihz(freq_centihz)
    domain = _require_ctcss_domain(ctcss_tones_centihz)
    if freq_centihz not in domain:
        raise ValueError(
            f"Tone frequency {freq_centihz} is not declared by the radio profile"
        )
    return freq_centihz


def _encode_tone_freq(freq_centihz: int) -> bytes:
    """Encode an exact centiHz tone frequency to 3-byte CI-V BCD.

    Three bytes hold six packed BCD digits, read as a decimal integer of
    tenths of a Hz: ``[0][0][100Hz digit][10Hz digit][1Hz digit]
    [0.1Hz digit]`` (IC-705 CI-V Reference Guide 2020 p.21, "Repeater
    tone/tone squelch frequency settings", command 1B 00/1B 01 -- the same
    layout as the IC-7300 Advanced Manual, IC-9700 and IC-7610 CI-V
    references). MOR-2091 fixed a prior layout mismatch here; see
    ``tests/test_tone_tsql.py``'s ``_BCD_TABLE`` for the full manual and
    hardware-capture sourcing.
    """
    return bcd_encode_value(
        _validate_tone_freq_centihz(freq_centihz) // 10,
        byte_count=3,
    )


def _decode_tone_freq(data: bytes) -> int:
    """Decode 3-byte CI-V BCD to exact centiHz."""
    if len(data) < 3:
        raise ValueError(f"Expected 3 bytes for tone freq, got {len(data)}")
    return _validate_tone_freq_centihz(_bcd_decode_value(data[:3]) * 10)


@expose_command_key(lambda cmd_map: "get_repeater_tone")
@require_cmd_map
def get_repeater_tone(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to get repeater tone status (0x16 0x42)."""
    return _build_function_get(
        _SUB_REPEATER_TONE,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="get_repeater_tone",
    )


@expose_command_key(lambda cmd_map: "set_repeater_tone")
@require_cmd_map
def set_repeater_tone(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to set repeater tone (0x16 0x42)."""
    return _build_function_bool_set(
        _SUB_REPEATER_TONE,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="set_repeater_tone",
    )


@expose_command_key(lambda cmd_map: "get_repeater_tsql")
@require_cmd_map
def get_repeater_tsql(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to get repeater TSQL status (0x16 0x43)."""
    return _build_function_get(
        _SUB_REPEATER_TSQL,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="get_repeater_tsql",
    )


@expose_command_key(lambda cmd_map: "set_repeater_tsql")
@require_cmd_map
def set_repeater_tsql(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to set repeater TSQL (0x16 0x43)."""
    return _build_function_bool_set(
        _SUB_REPEATER_TSQL,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="set_repeater_tsql",
    )


@expose_command_key(lambda cmd_map: "get_tone_freq")
@require_cmd_map
def get_tone_freq(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
    ctcss_tones_centihz: tuple[int, ...],
) -> bytes:
    """Build CI-V command to get tone frequency (0x1B 0x00)."""
    _require_ctcss_domain(ctcss_tones_centihz)
    return _build_from_map(
        cmd_map,
        "get_tone_freq",
        to_addr=to_addr,
        from_addr=from_addr,
        command29=command29,
        receiver=receiver,
    )


@expose_command_key(lambda cmd_map: "set_tone_freq")
@require_cmd_map
def set_tone_freq(
    freq_centihz: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
    ctcss_tones_centihz: tuple[int, ...],
) -> bytes:
    """Build CI-V command to set tone frequency (0x1B 0x00)."""
    return _build_from_map(
        cmd_map,
        "set_tone_freq",
        to_addr=to_addr,
        from_addr=from_addr,
        command29=command29,
        receiver=receiver,
        data=_encode_tone_freq(
            _require_profile_tone(freq_centihz, ctcss_tones_centihz)
        ),
    )


@expose_command_key(lambda cmd_map: "get_tsql_freq")
@require_cmd_map
def get_tsql_freq(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
    ctcss_tones_centihz: tuple[int, ...],
) -> bytes:
    """Build CI-V command to get TSQL frequency (0x1B 0x01)."""
    _require_ctcss_domain(ctcss_tones_centihz)
    return _build_from_map(
        cmd_map,
        "get_tsql_freq",
        to_addr=to_addr,
        from_addr=from_addr,
        command29=command29,
        receiver=receiver,
    )


@expose_command_key(lambda cmd_map: "set_tsql_freq")
@require_cmd_map
def set_tsql_freq(
    freq_centihz: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
    ctcss_tones_centihz: tuple[int, ...],
) -> bytes:
    """Build CI-V command to set TSQL frequency (0x1B 0x01)."""
    return _build_from_map(
        cmd_map,
        "set_tsql_freq",
        to_addr=to_addr,
        from_addr=from_addr,
        command29=command29,
        receiver=receiver,
        data=_encode_tone_freq(
            _require_profile_tone(freq_centihz, ctcss_tones_centihz)
        ),
    )


def parse_tone_freq_response(
    frame: CivFrame,
    *,
    ctcss_tones_centihz: tuple[int, ...],
) -> tuple[int | None, int]:
    """Parse tone frequency response (0x1B 0x00)."""
    if frame.command != _CMD_TONE or frame.sub != _SUB_TONE_FREQ:
        raise ValueError(
            f"Not a tone freq response: 0x{frame.command:02x} sub=0x{frame.sub!r}"
        )
    if len(frame.data) < 3:
        raise ValueError(f"Expected 3 bytes for tone freq, got {len(frame.data)}")
    return (
        frame.receiver,
        _require_profile_tone(_decode_tone_freq(frame.data), ctcss_tones_centihz),
    )


def parse_tsql_freq_response(
    frame: CivFrame,
    *,
    ctcss_tones_centihz: tuple[int, ...],
) -> tuple[int | None, int]:
    """Parse TSQL frequency response (0x1B 0x01)."""
    if frame.command != _CMD_TONE or frame.sub != _SUB_TSQL_FREQ:
        raise ValueError(
            f"Not a TSQL freq response: 0x{frame.command:02x} sub=0x{frame.sub!r}"
        )
    if len(frame.data) < 3:
        raise ValueError(f"Expected 3 bytes for TSQL freq, got {len(frame.data)}")
    return (
        frame.receiver,
        _require_profile_tone(_decode_tone_freq(frame.data), ctcss_tones_centihz),
    )
