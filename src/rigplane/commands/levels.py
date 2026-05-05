"""All 0x14-family level get/set commands + parse_level_response."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ._builders import (
    _build_level_get,
    _build_level_set,
    _build_ctl_mem_get,
    _build_ctl_mem_set,
)
from ._codec import _level_bcd_encode
from ._frame import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    _CMD_LEVEL,
    _CTL_MEM_DASH_RATIO,
    _CTL_MEM_NB_DEPTH,
    _CTL_MEM_NB_WIDTH,
    _CTL_MEM_REF_ADJUST,
    _CTL_MEM_VOX_DELAY,
    _SUB_AF_LEVEL,
    _SUB_ANTI_VOX_GAIN,
    _SUB_APF_TYPE_LEVEL,
    _SUB_BREAK_IN_DELAY,
    _SUB_COMPRESSOR_LEVEL,
    _SUB_CW_PITCH,
    _SUB_DIGISEL_SHIFT,
    _SUB_DRIVE_GAIN,
    _SUB_KEY_SPEED,
    _SUB_MIC_GAIN,
    _SUB_MONITOR_GAIN,
    _SUB_NB_LEVEL,
    _SUB_NOTCH_FILTER,
    _SUB_NR_LEVEL,
    _SUB_PBT_INNER,
    _SUB_PBT_OUTER,
    _SUB_RF_GAIN,
    _SUB_RF_POWER,
    _SUB_SQL,
    _SUB_VOX_GAIN,
    _build_from_map,
    build_civ_frame,
    build_cmd29_frame,
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


def get_rf_power(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'get RF power' CI-V command."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_rf_power", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_LEVEL, sub=_SUB_RF_POWER)


def set_rf_power(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'set RF power' CI-V command.

    Args:
        level: Power level 0-255 (radio maps to actual watts).
    """
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_rf_power",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_level_bcd_encode(level),
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_LEVEL, sub=_SUB_RF_POWER, data=_level_bcd_encode(level)
    )


def get_rf_gain(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'read RF gain' CI-V command (0x14 0x02).

    For SUB receiver, the frame is wrapped in cmd29 (0x29 0x01) — same routing
    as ``set_rf_gain``.
    """
    return _build_level_get(
        _SUB_RF_GAIN,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=(receiver != RECEIVER_MAIN),
        cmd_map=cmd_map,
        cmd_name="get_rf_gain",
    )


def set_rf_gain(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'set RF gain' CI-V command."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_rf_gain",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_level_bcd_encode(level),
            receiver=receiver,
            command29=(receiver != RECEIVER_MAIN),
        )
    bcd = _level_bcd_encode(level)
    if receiver != RECEIVER_MAIN:
        return build_cmd29_frame(
            to_addr,
            from_addr,
            _CMD_LEVEL,
            sub=_SUB_RF_GAIN,
            data=bcd,
            receiver=receiver,
        )
    return build_civ_frame(to_addr, from_addr, _CMD_LEVEL, sub=_SUB_RF_GAIN, data=bcd)


def get_af_level(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'read AF output level' CI-V command (0x14 0x01).

    For SUB receiver, the frame is wrapped in cmd29 (0x29 0x01) — same routing
    as ``set_af_level``.
    """
    return _build_level_get(
        _SUB_AF_LEVEL,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=(receiver != RECEIVER_MAIN),
        cmd_map=cmd_map,
        cmd_name="get_af_level",
    )


def set_af_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'set AF output level' CI-V command."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_af_level",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_level_bcd_encode(level),
            receiver=receiver,
            command29=(receiver != RECEIVER_MAIN),
        )
    bcd = _level_bcd_encode(level)
    if receiver != RECEIVER_MAIN:
        return build_cmd29_frame(
            to_addr,
            from_addr,
            _CMD_LEVEL,
            sub=_SUB_AF_LEVEL,
            data=bcd,
            receiver=receiver,
        )
    return build_civ_frame(to_addr, from_addr, _CMD_LEVEL, sub=_SUB_AF_LEVEL, data=bcd)


def get_squelch(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'get squelch level' CI-V command (0x14 0x03)."""
    return _build_level_get(
        _SUB_SQL,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=(receiver != RECEIVER_MAIN),
        cmd_map=cmd_map,
        cmd_name="get_squelch",
    )


def set_squelch(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'set squelch level' CI-V command."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_squelch",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_level_bcd_encode(level),
            receiver=receiver,
            command29=(receiver != RECEIVER_MAIN),
        )
    bcd = _level_bcd_encode(level)
    if receiver != RECEIVER_MAIN:
        return build_cmd29_frame(
            to_addr, from_addr, _CMD_LEVEL, sub=_SUB_SQL, data=bcd, receiver=receiver
        )
    return build_civ_frame(to_addr, from_addr, _CMD_LEVEL, sub=_SUB_SQL, data=bcd)


def get_apf_type_level(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read APF Type Level command."""
    return _build_level_get(
        _SUB_APF_TYPE_LEVEL,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_apf_type_level",
    )


def set_apf_type_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set APF Type Level command."""
    return _build_level_set(
        _SUB_APF_TYPE_LEVEL,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_apf_type_level",
    )


def get_nr_level(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read NR Level command."""
    return _build_level_get(
        _SUB_NR_LEVEL,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_nr_level",
    )


def set_nr_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set NR Level command."""
    return _build_level_set(
        _SUB_NR_LEVEL,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_nr_level",
    )


def get_pbt_inner(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read PBT Inner command."""
    return _build_level_get(
        _SUB_PBT_INNER,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_pbt_inner",
    )


def set_pbt_inner(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set PBT Inner command."""
    return _build_level_set(
        _SUB_PBT_INNER,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_pbt_inner",
    )


def get_pbt_outer(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read PBT Outer command."""
    return _build_level_get(
        _SUB_PBT_OUTER,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_pbt_outer",
    )


def set_pbt_outer(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set PBT Outer command."""
    return _build_level_set(
        _SUB_PBT_OUTER,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_pbt_outer",
    )


def get_cw_pitch(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read CW Pitch command."""
    return _build_level_get(
        _SUB_CW_PITCH,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_cw_pitch",
    )


def set_cw_pitch(
    pitch_hz: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set CW Pitch command."""
    return _build_level_set(
        _SUB_CW_PITCH,
        _cw_pitch_to_level(pitch_hz),
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_cw_pitch",
    )


def get_mic_gain(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read Mic Gain command."""
    return _build_level_get(
        _SUB_MIC_GAIN,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_mic_gain",
    )


def set_mic_gain(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set Mic Gain command."""
    return _build_level_set(
        _SUB_MIC_GAIN,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_mic_gain",
    )


def get_key_speed(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read Key Speed command."""
    return _build_level_get(
        _SUB_KEY_SPEED,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_key_speed",
    )


def set_key_speed(
    wpm: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set Key Speed command."""
    return _build_level_set(
        _SUB_KEY_SPEED,
        _key_speed_to_level(wpm),
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_key_speed",
    )


def get_notch_filter(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read Notch Filter level command."""
    return _build_level_get(
        _SUB_NOTCH_FILTER,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=(receiver != RECEIVER_MAIN),
        cmd_map=cmd_map,
        cmd_name="get_notch_filter",
    )


def set_notch_filter(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set Notch Filter level command."""
    return _build_level_set(
        _SUB_NOTCH_FILTER,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=(receiver != RECEIVER_MAIN),
        cmd_map=cmd_map,
        cmd_name="set_notch_filter",
    )


def get_compressor_level(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read Compressor Level command."""
    return _build_level_get(
        _SUB_COMPRESSOR_LEVEL,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_compressor_level",
    )


def set_compressor_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set Compressor Level command."""
    return _build_level_set(
        _SUB_COMPRESSOR_LEVEL,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_compressor_level",
    )


def get_break_in_delay(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read Break-In Delay command."""
    return _build_level_get(
        _SUB_BREAK_IN_DELAY,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_break_in_delay",
    )


def set_break_in_delay(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set Break-In Delay command."""
    return _build_level_set(
        _SUB_BREAK_IN_DELAY,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_break_in_delay",
    )


def get_nb_level(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read NB Level command."""
    return _build_level_get(
        _SUB_NB_LEVEL,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_nb_level",
    )


def set_nb_level(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set NB Level command."""
    return _build_level_set(
        _SUB_NB_LEVEL,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_nb_level",
    )


def get_digisel_shift(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read DIGI-SEL Shift command."""
    return _build_level_get(
        _SUB_DIGISEL_SHIFT,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_digisel_shift",
    )


def set_digisel_shift(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set DIGI-SEL Shift command."""
    return _build_level_set(
        _SUB_DIGISEL_SHIFT,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_digisel_shift",
    )


def get_drive_gain(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read Drive Gain command."""
    return _build_level_get(
        _SUB_DRIVE_GAIN,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_drive_gain",
    )


def set_drive_gain(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set Drive Gain command."""
    return _build_level_set(
        _SUB_DRIVE_GAIN,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_drive_gain",
    )


def get_monitor_gain(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read Monitor Gain command."""
    return _build_level_get(
        _SUB_MONITOR_GAIN,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_monitor_gain",
    )


def set_monitor_gain(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set Monitor Gain command."""
    return _build_level_set(
        _SUB_MONITOR_GAIN,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_monitor_gain",
    )


def get_vox_gain(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read Vox Gain command."""
    return _build_level_get(
        _SUB_VOX_GAIN,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_vox_gain",
    )


def set_vox_gain(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set Vox Gain command."""
    return _build_level_set(
        _SUB_VOX_GAIN,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_vox_gain",
    )


def get_anti_vox_gain(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read Anti-Vox Gain command."""
    return _build_level_get(
        _SUB_ANTI_VOX_GAIN,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_anti_vox_gain",
    )


def set_anti_vox_gain(
    level: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set Anti-Vox Gain command."""
    return _build_level_set(
        _SUB_ANTI_VOX_GAIN,
        level,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_anti_vox_gain",
    )


# --- CTL_MEM-based levels ---


def get_ref_adjust(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build a read REF Adjust command."""
    return _build_ctl_mem_get(
        _CTL_MEM_REF_ADJUST,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_ref_adjust",
    )


def set_ref_adjust(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set REF Adjust command."""
    if not 0 <= value <= 511:
        raise ValueError(f"REF Adjust must be 0-511, got {value}")
    return _build_ctl_mem_set(
        _CTL_MEM_REF_ADJUST,
        value,
        to_addr=to_addr,
        from_addr=from_addr,
        byte_count=2,
        cmd_map=cmd_map,
        cmd_name="set_ref_adjust",
    )


def get_dash_ratio(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build a read Dash Ratio command."""
    return _build_ctl_mem_get(
        _CTL_MEM_DASH_RATIO,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_dash_ratio",
    )


def set_dash_ratio(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set Dash Ratio command."""
    if not 28 <= value <= 45:
        raise ValueError(f"Dash Ratio must be 28-45, got {value}")
    return _build_ctl_mem_set(
        _CTL_MEM_DASH_RATIO,
        value,
        to_addr=to_addr,
        from_addr=from_addr,
        byte_count=1,
        cmd_map=cmd_map,
        cmd_name="set_dash_ratio",
    )


def get_nb_depth(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build a read NB Depth command."""
    return _build_ctl_mem_get(
        _CTL_MEM_NB_DEPTH,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_nb_depth",
    )


def set_nb_depth(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set NB Depth command."""
    if not 0 <= value <= 9:
        raise ValueError(f"NB Depth must be 0-9, got {value}")
    return _build_ctl_mem_set(
        _CTL_MEM_NB_DEPTH,
        value,
        to_addr=to_addr,
        from_addr=from_addr,
        byte_count=1,
        cmd_map=cmd_map,
        cmd_name="set_nb_depth",
    )


def get_nb_width(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build a read NB Width command."""
    return _build_ctl_mem_get(
        _CTL_MEM_NB_WIDTH,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_nb_width",
    )


def set_nb_width(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set NB Width command."""
    if not 0 <= value <= 255:
        raise ValueError(f"NB Width must be 0-255, got {value}")
    return _build_ctl_mem_set(
        _CTL_MEM_NB_WIDTH,
        value,
        to_addr=to_addr,
        from_addr=from_addr,
        byte_count=2,
        cmd_map=cmd_map,
        cmd_name="set_nb_width",
    )


def get_vox_delay(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build a read VOX Delay command (0x1A 0x05 0x02 0x92)."""
    return _build_ctl_mem_get(
        _CTL_MEM_VOX_DELAY,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_vox_delay",
    )


def set_vox_delay(
    value: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set VOX Delay command (0x1A 0x05 0x02 0x92)."""
    if not 0 <= value <= 20:
        raise ValueError(f"VOX Delay must be 0-20, got {value}")
    return _build_ctl_mem_set(
        _CTL_MEM_VOX_DELAY,
        value,
        to_addr=to_addr,
        from_addr=from_addr,
        byte_count=1,
        cmd_map=cmd_map,
        cmd_name="set_vox_delay",
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
