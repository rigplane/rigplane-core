"""All 0x14-family level get/set commands + parse_level_response.

Migrated onto the bound command map in MOR-2006 Steps 5..N (module 2,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4): every builder
here requires ``cmd_map``, with no hardcoded fallback left. This module
accounted for 16 of the rows `tests/command_map_parity_divergences.txt`
recorded before this migration -- all now gone, since a divergence needs
two disagreeing implementations and this module has only one left --
including the other half of the measured ACC1/MIC-gain collision
(MOR-1992): this module's ``set_mic_gain`` fallback built the exact same
eight bytes on the IC-7300 as `config.py: set_acc1_mod_level`'s fallback
did, for two unrelated controls.

Every builder calls `_frame.py: _build_from_map` directly rather than the
shared `_builders.py` templates used before migrating
(``_build_level_get`` / ``_build_level_set`` / ``_build_ctl_mem_set``, all
deleted here -- this module was their only caller, so their own
``cmd_map is None`` fallback branches would otherwise become dead code
nobody reads; ``_build_ctl_mem_get`` was kept at the time, since
`system.py`'s own date/time/UTC-offset getters still routed through its
fallback -- it was deleted later, when `system.py` migrated (MOR-2008
batch 1) removed that last caller too). Routing this module's builders
through a template that retains a fallback would leave an explicit
``cmd_map=None`` call one layer away from silently reaching old,
sometimes-wrong bytes instead of failing loudly through `_frame.py:
require_cmd_map` -- the same reasoning `config.py`'s own module docstring
records for the first half of this migration.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ._codec import _level_bcd_encode, bcd_encode_value
from ._frame import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


def _cw_pitch_from_level(level: int) -> int:
    return int(round((((600.0 / 255.0) * level) + 300) / 5.0) * 5.0)


def _cw_pitch_to_level(pitch_hz: int) -> int:
    if not 300 <= pitch_hz <= 900:
        raise ValueError(f"CW pitch must be 300-900 Hz, got {pitch_hz}")
    return math.ceil((pitch_hz - 300) * (255.0 / 600.0))


def _key_speed_from_level(level: int) -> int:
    return round((level / 6.071) + 6)


def _key_speed_to_level(wpm: int) -> int:
    if not 6 <= wpm <= 48:
        raise ValueError(f"Key speed must be 6-48 WPM, got {wpm}")
    return round((wpm - 6) * 6.071)


@expose_command_key(lambda cmd_map: "get_rf_power")
@require_cmd_map
def get_rf_power(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a 'get RF power' CI-V command."""
    return _build_from_map(
        cmd_map, "get_rf_power", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_rf_power")
@require_cmd_map
def set_rf_power(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a 'set RF power' CI-V command.

    Args:
        level: Power level 0-255 (radio maps to actual watts).
    """
    return _build_from_map(
        cmd_map,
        "set_rf_power",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
    )


@expose_command_key(lambda cmd_map: "get_rf_gain")
@require_cmd_map
def get_rf_gain(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a 'read RF gain' CI-V command (0x14 0x02)."""
    return _build_from_map(
        cmd_map,
        "get_rf_gain",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_rf_gain")
@require_cmd_map
def set_rf_gain(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a 'set RF gain' CI-V command."""
    return _build_from_map(
        cmd_map,
        "set_rf_gain",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_af_level")
@require_cmd_map
def get_af_level(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a 'read AF output level' CI-V command (0x14 0x01)."""
    return _build_from_map(
        cmd_map,
        "get_af_level",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_af_level")
@require_cmd_map
def set_af_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a 'set AF output level' CI-V command."""
    return _build_from_map(
        cmd_map,
        "set_af_level",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_squelch")
@require_cmd_map
def get_squelch(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a 'get squelch level' CI-V command (0x14 0x03)."""
    return _build_from_map(
        cmd_map,
        "get_squelch",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_squelch")
@require_cmd_map
def set_squelch(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a 'set squelch level' CI-V command."""
    return _build_from_map(
        cmd_map,
        "set_squelch",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_apf_type_level")
@require_cmd_map
def get_apf_type_level(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read APF Type Level command."""
    return _build_from_map(
        cmd_map,
        "get_apf_type_level",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_apf_type_level")
@require_cmd_map
def set_apf_type_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set APF Type Level command."""
    return _build_from_map(
        cmd_map,
        "set_apf_type_level",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_nr_level")
@require_cmd_map
def get_nr_level(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read NR Level command."""
    return _build_from_map(
        cmd_map,
        "get_nr_level",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_nr_level")
@require_cmd_map
def set_nr_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set NR Level command."""
    return _build_from_map(
        cmd_map,
        "set_nr_level",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_pbt_inner")
@require_cmd_map
def get_pbt_inner(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read PBT Inner command."""
    return _build_from_map(
        cmd_map,
        "get_pbt_inner",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_pbt_inner")
@require_cmd_map
def set_pbt_inner(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set PBT Inner command."""
    return _build_from_map(
        cmd_map,
        "set_pbt_inner",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_pbt_outer")
@require_cmd_map
def get_pbt_outer(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read PBT Outer command."""
    return _build_from_map(
        cmd_map,
        "get_pbt_outer",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_pbt_outer")
@require_cmd_map
def set_pbt_outer(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set PBT Outer command."""
    return _build_from_map(
        cmd_map,
        "set_pbt_outer",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_cw_pitch")
@require_cmd_map
def get_cw_pitch(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read CW Pitch command."""
    return _build_from_map(
        cmd_map, "get_cw_pitch", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_cw_pitch")
@require_cmd_map
def set_cw_pitch(
    pitch_hz: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set CW Pitch command."""
    return _build_from_map(
        cmd_map,
        "set_cw_pitch",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(_cw_pitch_to_level(pitch_hz)),
    )


@expose_command_key(lambda cmd_map: "get_mic_gain")
@require_cmd_map
def get_mic_gain(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read Mic Gain command."""
    return _build_from_map(
        cmd_map, "get_mic_gain", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_mic_gain")
@require_cmd_map
def set_mic_gain(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set Mic Gain command."""
    return _build_from_map(
        cmd_map,
        "set_mic_gain",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
    )


@expose_command_key(lambda cmd_map: "get_key_speed")
@require_cmd_map
def get_key_speed(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read Key Speed command."""
    return _build_from_map(
        cmd_map, "get_key_speed", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_key_speed")
@require_cmd_map
def set_key_speed(
    wpm: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set Key Speed command."""
    return _build_from_map(
        cmd_map,
        "set_key_speed",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(_key_speed_to_level(wpm)),
    )


@expose_command_key(lambda cmd_map: "get_notch_filter")
@require_cmd_map
def get_notch_filter(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read Notch Filter level command."""
    return _build_from_map(
        cmd_map,
        "get_notch_filter",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_notch_filter")
@require_cmd_map
def set_notch_filter(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set Notch Filter level command."""
    return _build_from_map(
        cmd_map,
        "set_notch_filter",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_compressor_level")
@require_cmd_map
def get_compressor_level(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read Compressor Level command."""
    return _build_from_map(
        cmd_map, "get_compressor_level", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_compressor_level")
@require_cmd_map
def set_compressor_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set Compressor Level command."""
    return _build_from_map(
        cmd_map,
        "set_compressor_level",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
    )


@expose_command_key(lambda cmd_map: "get_break_in_delay")
@require_cmd_map
def get_break_in_delay(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read Break-In Delay command."""
    return _build_from_map(
        cmd_map, "get_break_in_delay", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_break_in_delay")
@require_cmd_map
def set_break_in_delay(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set Break-In Delay command."""
    return _build_from_map(
        cmd_map,
        "set_break_in_delay",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
    )


@expose_command_key(lambda cmd_map: "get_nb_level")
@require_cmd_map
def get_nb_level(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read NB Level command."""
    return _build_from_map(
        cmd_map,
        "get_nb_level",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_nb_level")
@require_cmd_map
def set_nb_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set NB Level command."""
    return _build_from_map(
        cmd_map,
        "set_nb_level",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_digisel_shift")
@require_cmd_map
def get_digisel_shift(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a read DIGI-SEL Shift command."""
    return _build_from_map(
        cmd_map,
        "get_digisel_shift",
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "set_digisel_shift")
@require_cmd_map
def set_digisel_shift(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    *,
    command29: bool = True,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set DIGI-SEL Shift command."""
    return _build_from_map(
        cmd_map,
        "set_digisel_shift",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
        receiver=receiver,
        command29=command29,
    )


@expose_command_key(lambda cmd_map: "get_drive_gain")
@require_cmd_map
def get_drive_gain(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read Drive Gain command."""
    return _build_from_map(
        cmd_map, "get_drive_gain", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_drive_gain")
@require_cmd_map
def set_drive_gain(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set Drive Gain command."""
    return _build_from_map(
        cmd_map,
        "set_drive_gain",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
    )


@expose_command_key(lambda cmd_map: "get_monitor_gain")
@require_cmd_map
def get_monitor_gain(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read Monitor Gain command."""
    return _build_from_map(
        cmd_map, "get_monitor_gain", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_monitor_gain")
@require_cmd_map
def set_monitor_gain(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set Monitor Gain command."""
    return _build_from_map(
        cmd_map,
        "set_monitor_gain",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
    )


@expose_command_key(lambda cmd_map: "get_vox_gain")
@require_cmd_map
def get_vox_gain(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read Vox Gain command."""
    return _build_from_map(
        cmd_map, "get_vox_gain", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_vox_gain")
@require_cmd_map
def set_vox_gain(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set Vox Gain command."""
    return _build_from_map(
        cmd_map,
        "set_vox_gain",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
    )


@expose_command_key(lambda cmd_map: "get_anti_vox_gain")
@require_cmd_map
def get_anti_vox_gain(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read Anti-Vox Gain command."""
    return _build_from_map(
        cmd_map, "get_anti_vox_gain", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_anti_vox_gain")
@require_cmd_map
def set_anti_vox_gain(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set Anti-Vox Gain command."""
    return _build_from_map(
        cmd_map,
        "set_anti_vox_gain",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_level_bcd_encode(level),
    )


# --- CTL_MEM-based levels (menu addresses vary by radio -- see rigs/*.toml) ---


@expose_command_key(lambda cmd_map: "get_ref_adjust")
@require_cmd_map
def get_ref_adjust(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read REF Adjust command."""
    return _build_from_map(
        cmd_map, "get_ref_adjust", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_ref_adjust")
@require_cmd_map
def set_ref_adjust(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set REF Adjust command."""
    if not 0 <= value <= 511:
        raise ValueError(f"REF Adjust must be 0-511, got {value}")
    return _build_from_map(
        cmd_map,
        "set_ref_adjust",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(value, byte_count=2),
    )


@expose_command_key(lambda cmd_map: "get_dash_ratio")
@require_cmd_map
def get_dash_ratio(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read Dash Ratio command."""
    return _build_from_map(
        cmd_map, "get_dash_ratio", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_dash_ratio")
@require_cmd_map
def set_dash_ratio(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set Dash Ratio command."""
    if not 28 <= value <= 45:
        raise ValueError(f"Dash Ratio must be 28-45, got {value}")
    return _build_from_map(
        cmd_map,
        "set_dash_ratio",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(value, byte_count=1),
    )


@expose_command_key(lambda cmd_map: "get_nb_depth")
@require_cmd_map
def get_nb_depth(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read NB Depth command."""
    return _build_from_map(
        cmd_map, "get_nb_depth", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_nb_depth")
@require_cmd_map
def set_nb_depth(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set NB Depth command."""
    if not 0 <= value <= 9:
        raise ValueError(f"NB Depth must be 0-9, got {value}")
    return _build_from_map(
        cmd_map,
        "set_nb_depth",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(value, byte_count=1),
    )


@expose_command_key(lambda cmd_map: "get_nb_width")
@require_cmd_map
def get_nb_width(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read NB Width command."""
    return _build_from_map(
        cmd_map, "get_nb_width", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_nb_width")
@require_cmd_map
def set_nb_width(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set NB Width command."""
    if not 0 <= value <= 255:
        raise ValueError(f"NB Width must be 0-255, got {value}")
    return _build_from_map(
        cmd_map,
        "set_nb_width",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(value, byte_count=2),
    )


@expose_command_key(lambda cmd_map: "get_vox_delay")
@require_cmd_map
def get_vox_delay(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a read VOX Delay command (0x1A 0x05 0x02 0x92)."""
    return _build_from_map(
        cmd_map, "get_vox_delay", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_vox_delay")
@require_cmd_map
def set_vox_delay(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build a set VOX Delay command (0x1A 0x05 0x02 0x92)."""
    if not 0 <= value <= 20:
        raise ValueError(f"VOX Delay must be 0-20, got {value}")
    return _build_from_map(
        cmd_map,
        "set_vox_delay",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(value, byte_count=1),
    )


__all__ = [
    # Canonical level builders (alphabetised).
    "get_af_level",
    "get_anti_vox_gain",
    "get_apf_type_level",
    "get_break_in_delay",
    "get_compressor_level",
    "get_cw_pitch",
    "get_dash_ratio",
    "get_digisel_shift",
    "get_drive_gain",
    "get_key_speed",
    "get_mic_gain",
    "get_monitor_gain",
    "get_nb_depth",
    "get_nb_level",
    "get_nb_width",
    "get_notch_filter",
    "get_nr_level",
    "get_pbt_inner",
    "get_pbt_outer",
    "get_ref_adjust",
    "get_rf_gain",
    "get_rf_power",
    "get_squelch",
    "get_vox_delay",
    "get_vox_gain",
    "set_af_level",
    "set_anti_vox_gain",
    "set_apf_type_level",
    "set_break_in_delay",
    "set_compressor_level",
    "set_cw_pitch",
    "set_dash_ratio",
    "set_digisel_shift",
    "set_drive_gain",
    "set_key_speed",
    "set_mic_gain",
    "set_monitor_gain",
    "set_nb_depth",
    "set_nb_level",
    "set_nb_width",
    "set_notch_filter",
    "set_nr_level",
    "set_pbt_inner",
    "set_pbt_outer",
    "set_ref_adjust",
    "set_rf_gain",
    "set_rf_power",
    "set_squelch",
    "set_vox_delay",
    "set_vox_gain",
]
