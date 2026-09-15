"""DSP commands: ATT, preamp, NB, NR, IP+, AGC, notch, compressor, VOX, break-in, etc.

Migrated onto the bound command map in MOR-2008 (batch 3,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N): all
36 builders now require ``cmd_map``, with no hardcoded fallback left. Zero
divergence rows -- every declaring profile's tuple already matched the
fallback's own bytes exactly (verified by grep across ``rigs/*.toml`` before
deleting each fallback; ``tests/command_map_parity_divergences.txt`` was
already empty and stays empty).

22 of the 36 route through `_builders.py`'s shared
``_build_function_get``/``_build_function_bool_set``/``_build_function_value_set``
templates, which stayed alone through batch 2 specifically because this
module was still calling them with ``cmd_map=None`` (see batch 2's own
`mode.py` docstring) -- this batch is what makes their fallback branches
finally dead, and `_builders.py` drops them in the same commit.

**Constant census** (resolved by AST import analysis against `_frame.py`,
not by name occurrence -- several test files below define their own
same-named local literals rather than importing, which a text grep
conflates with a real reader): ``_SUB_NB``, ``_SUB_NR``, ``_SUB_IP_PLUS``,
``_SUB_AF_MUTE`` and ``_SUB_DIGISEL_STATUS`` have no importer left
anywhere in `src/` or `tests/` once this module's own fallback branches
were gone -- the last two were missed in this batch's first pass (found by
review, MOR-2008 batch 3 round 2) because `tests/mock_server.py`,
`tests/test_dsp_levels_part2.py` and `tests/integration/
test_dsp_levels_integration.py` each hold an unrelated local literal of
the same name, not an ``from .._frame import`` of it. All five are
deleted from `_frame.py`. ``_CMD_ATT`` and ``_SUB_PREAMP_STATUS`` stay:
each has exactly one real importer, `tests/test_single_rx_plain_routing.py`
(confirmed by AST, not by the four candidate files the first pass named).
``_CMD_PREAMP`` stays for a different, stronger reason: `_frame.py` reads
its own definition directly, in the cmd29 decode path
(``if real_command == _CMD_PREAMP``), and `commands/__init__.py`
re-exports it -- `tests/test_single_rx_plain_routing.py` also imports it,
but that import is not why it survives. ``_CMD_CTL_MEM`` stays for the
same reason batch 2 kept it: `mode.py: parse_data_mode_response`,
`system.py` and `memory.py` all still read it directly. This module drops
its own import of all of them except the ``_SUB_*`` values still passed as
the leading, now-unread ``sub`` argument to the three shared templates
above (see `_builders.py`'s own docstring for why that parameter survives
unread rather than being swept out in this batch).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types import AgcMode, AudioPeakFilter, BreakInMode
from ._builders import (
    _build_function_bool_set,
    _build_function_get,
    _build_function_value_set,
)
from ._codec import _bcd_byte
from ._frame import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _SUB_AGC,
    _SUB_AUDIO_PEAK_FILTER,
    _SUB_AUTO_NOTCH,
    _SUB_BREAK_IN,
    _SUB_COMPRESSOR,
    _SUB_DIAL_LOCK,
    _SUB_MANUAL_NOTCH,
    _SUB_MANUAL_NOTCH_WIDTH,
    _SUB_MONITOR,
    _SUB_TWIN_PEAK_FILTER,
    _SUB_VOX,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


@expose_command_key(lambda cmd_map: "get_attenuator")
@require_cmd_map
def get_attenuator(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to read attenuator level (Command29-aware)."""
    return _build_from_map(
        cmd_map,
        "get_attenuator",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_attenuator")
@require_cmd_map
def set_attenuator_level(
    db: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Set attenuator level in dB (IC-7610 supports 0..45 in 3 dB steps)."""
    return _build_from_map(
        cmd_map,
        "set_attenuator",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([_bcd_byte(db)]),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_preamp")
@require_cmd_map
def get_preamp(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to read preamp status (Command29-aware)."""
    return _build_from_map(
        cmd_map,
        "get_preamp",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_preamp")
@require_cmd_map
def set_preamp(
    level: int = 1,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Set preamp level (0=off, 1=PREAMP1, 2=PREAMP2)."""
    return _build_from_map(
        cmd_map,
        "set_preamp",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([_bcd_byte(level)]),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_digisel")
@require_cmd_map
def get_digisel(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to read DIGI-SEL status (Command29-aware)."""
    return _build_from_map(
        cmd_map,
        "get_digisel",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_digisel")
@require_cmd_map
def set_digisel(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Set DIGI-SEL status (Command29-aware)."""
    return _build_from_map(
        cmd_map,
        "set_digisel",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([_bcd_byte(1 if on else 0)]),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_nb")
@require_cmd_map
def get_nb(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to read NB status."""
    return _build_from_map(
        cmd_map,
        "get_nb",
        to_addr=to_addr,
        from_addr=from_addr,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_nb")
@require_cmd_map
def set_nb(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Set Noise Blanker on/off."""
    return _build_from_map(
        cmd_map,
        "set_nb",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([0x01 if on else 0x00]),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_nr")
@require_cmd_map
def get_nr(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to read NR status."""
    return _build_from_map(
        cmd_map,
        "get_nr",
        to_addr=to_addr,
        from_addr=from_addr,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_nr")
@require_cmd_map
def set_nr(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Set Noise Reduction on/off."""
    return _build_from_map(
        cmd_map,
        "set_nr",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([0x01 if on else 0x00]),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_ip_plus")
@require_cmd_map
def get_ip_plus(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to read IP+ status."""
    return _build_from_map(
        cmd_map,
        "get_ip_plus",
        to_addr=to_addr,
        from_addr=from_addr,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_ip_plus")
@require_cmd_map
def set_ip_plus(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Set IP+ on/off."""
    return _build_from_map(
        cmd_map,
        "set_ip_plus",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([0x01 if on else 0x00]),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_af_mute")
@require_cmd_map
def get_af_mute(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read AF Mute command."""
    return _build_from_map(
        cmd_map,
        "get_af_mute",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_af_mute")
@require_cmd_map
def set_af_mute(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set AF Mute command."""
    return _build_from_map(
        cmd_map,
        "set_af_mute",
        to_addr=to_addr,
        from_addr=from_addr,
        data=b"\x01" if on else b"\x00",
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_agc")
@require_cmd_map
def get_agc(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read AGC mode command."""
    return _build_function_get(
        _SUB_AGC,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="get_agc",
    )


@expose_command_key(lambda cmd_map: "set_agc")
@require_cmd_map
def set_agc(
    mode: AgcMode | int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set AGC mode command.

    Encodes the raw single-BCD-byte AGC mode value. Which mode values are
    legal for a given radio (IC-7300's FAST/MID/SLOW vs. the X6200's
    OFF/FAST/SLOW/AUTO) is a per-profile domain, not a universal one — this
    builder only enforces the wire-format's single-BCD-byte range and
    leaves domain validation to the profile-aware caller (MOR-1522).
    """
    return _build_function_value_set(
        _SUB_AGC,
        int(mode),
        minimum=0,
        maximum=99,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="set_agc",
    )


@expose_command_key(lambda cmd_map: "get_audio_peak_filter")
@require_cmd_map
def get_audio_peak_filter(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read audio peak filter mode command."""
    return _build_function_get(
        _SUB_AUDIO_PEAK_FILTER,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="get_audio_peak_filter",
    )


@expose_command_key(lambda cmd_map: "set_audio_peak_filter")
@require_cmd_map
def set_audio_peak_filter(
    mode: AudioPeakFilter | int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set audio peak filter mode command."""
    return _build_function_value_set(
        _SUB_AUDIO_PEAK_FILTER,
        int(AudioPeakFilter(mode)),
        minimum=int(AudioPeakFilter.OFF),
        maximum=int(AudioPeakFilter.NAR),
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="set_audio_peak_filter",
    )


@expose_command_key(lambda cmd_map: "get_auto_notch")
@require_cmd_map
def get_auto_notch(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read auto-notch status command."""
    return _build_function_get(
        _SUB_AUTO_NOTCH,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="get_auto_notch",
    )


@expose_command_key(lambda cmd_map: "set_auto_notch")
@require_cmd_map
def set_auto_notch(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set auto-notch status command."""
    return _build_function_bool_set(
        _SUB_AUTO_NOTCH,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="set_auto_notch",
    )


@expose_command_key(lambda cmd_map: "get_compressor")
@require_cmd_map
def get_compressor(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_function_get(
        _SUB_COMPRESSOR,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_compressor",
    )


@expose_command_key(lambda cmd_map: "set_compressor")
@require_cmd_map
def set_compressor(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    return _build_function_bool_set(
        _SUB_COMPRESSOR,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_compressor",
    )


@expose_command_key(lambda cmd_map: "get_monitor")
@require_cmd_map
def get_monitor(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_function_get(
        _SUB_MONITOR,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_monitor",
    )


@expose_command_key(lambda cmd_map: "set_monitor")
@require_cmd_map
def set_monitor(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    return _build_function_bool_set(
        _SUB_MONITOR,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_monitor",
    )


@expose_command_key(lambda cmd_map: "get_vox")
@require_cmd_map
def get_vox(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_function_get(
        _SUB_VOX,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_vox",
    )


@expose_command_key(lambda cmd_map: "set_vox")
@require_cmd_map
def set_vox(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    return _build_function_bool_set(
        _SUB_VOX,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_vox",
    )


@expose_command_key(lambda cmd_map: "get_break_in")
@require_cmd_map
def get_break_in(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_function_get(
        _SUB_BREAK_IN,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_break_in",
    )


@expose_command_key(lambda cmd_map: "set_break_in")
@require_cmd_map
def set_break_in(
    mode: BreakInMode | int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set break-in mode command.

    Encodes the raw single-BCD-byte break-in value. Which values are legal
    for a given radio (the documented OFF/SEMI/FULL domain on IC-705/
    IC-7300/IC-9700/IC-7610 vs. the X6100/X6200, which have no confirmed
    domain at all) is a per-profile question, not a universal one — this
    builder only enforces the wire-format's single-BCD-byte range and
    leaves domain validation to the profile-aware caller (MOR-1534, mirrors
    MOR-1522's ``set_agc`` fix).
    """
    return _build_function_value_set(
        _SUB_BREAK_IN,
        int(mode),
        minimum=0,
        maximum=99,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_break_in",
    )


@expose_command_key(lambda cmd_map: "get_manual_notch")
@require_cmd_map
def get_manual_notch(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    return _build_function_get(
        _SUB_MANUAL_NOTCH,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="get_manual_notch",
    )


@expose_command_key(lambda cmd_map: "set_manual_notch")
@require_cmd_map
def set_manual_notch(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    return _build_function_bool_set(
        _SUB_MANUAL_NOTCH,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="set_manual_notch",
    )


@expose_command_key(lambda cmd_map: "get_manual_notch_width")
@require_cmd_map
def get_manual_notch_width(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a 'get manual notch width' CI-V command (0x16 0x57)."""
    return _build_function_get(
        _SUB_MANUAL_NOTCH_WIDTH,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="get_manual_notch_width",
    )


@expose_command_key(lambda cmd_map: "set_manual_notch_width")
@require_cmd_map
def set_manual_notch_width(
    width: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a 'set manual notch width' CI-V command (0x16 0x57).

    Encodes the raw single-BCD-byte notch-width value. Which values are
    legal is a per-profile ``[notch] width_values`` domain, not a fixed
    0/1/2 (WIDE/MID/NAR) enum on every radio — this builder only enforces
    the wire-format's single-BCD-byte range and leaves domain validation
    to the profile-aware caller (MOR-1542, mirrors set_break_in/
    set_filter_shape/set_ssb_tx_bandwidth's MOR-1534 fix; CoreRadio keeps
    the domain-legality seat).
    """
    return _build_function_value_set(
        _SUB_MANUAL_NOTCH_WIDTH,
        width,
        minimum=0,
        maximum=99,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="set_manual_notch_width",
    )


@expose_command_key(lambda cmd_map: "get_twin_peak_filter")
@require_cmd_map
def get_twin_peak_filter(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    return _build_function_get(
        _SUB_TWIN_PEAK_FILTER,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="get_twin_peak_filter",
    )


@expose_command_key(lambda cmd_map: "set_twin_peak_filter")
@require_cmd_map
def set_twin_peak_filter(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    return _build_function_bool_set(
        _SUB_TWIN_PEAK_FILTER,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
        cmd_map=cmd_map,
        cmd_name="set_twin_peak_filter",
    )


@expose_command_key(lambda cmd_map: "get_dial_lock")
@require_cmd_map
def get_dial_lock(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    return _build_function_get(
        _SUB_DIAL_LOCK,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_dial_lock",
    )


@expose_command_key(lambda cmd_map: "set_dial_lock")
@require_cmd_map
def set_dial_lock(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    return _build_function_bool_set(
        _SUB_DIAL_LOCK,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_dial_lock",
    )
