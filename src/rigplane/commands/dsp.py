"""DSP commands: ATT, preamp, NB, NR, IP+, AGC, notch, compressor, VOX, break-in, etc."""

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
    _CMD_ATT,
    _CMD_CTL_MEM,
    _CMD_PREAMP,
    _SUB_AF_MUTE,
    _SUB_AGC,
    _SUB_AUDIO_PEAK_FILTER,
    _SUB_AUTO_NOTCH,
    _SUB_BREAK_IN,
    _SUB_COMPRESSOR,
    _SUB_DIAL_LOCK,
    _SUB_DIGISEL_STATUS,
    _SUB_IP_PLUS,
    _SUB_MANUAL_NOTCH,
    _SUB_MANUAL_NOTCH_WIDTH,
    _SUB_MONITOR,
    _SUB_NB,
    _SUB_NR,
    _SUB_PREAMP_STATUS,
    _SUB_TWIN_PEAK_FILTER,
    _SUB_VOX,
    _build_from_map,
    build_civ_frame,
    build_cmd29_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


def get_attenuator(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to read attenuator level (Command29-aware)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_attenuator",
            to_addr=to_addr,
            from_addr=from_addr,
            receiver=receiver,
            command29=True,
        )
    return build_cmd29_frame(to_addr, from_addr, _CMD_ATT, receiver=receiver)


def set_attenuator_level(
    db: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Set attenuator level in dB (IC-7610 supports 0..45 in 3 dB steps)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_attenuator",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_bcd_byte(db)]),
            receiver=receiver,
            command29=True,
        )
    return build_cmd29_frame(
        to_addr, from_addr, _CMD_ATT, data=bytes([_bcd_byte(db)]), receiver=receiver
    )


def set_attenuator(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Compatibility wrapper for attenuator toggle (False->0dB, True->18dB)."""
    return set_attenuator_level(
        18 if on else 0,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        cmd_map=cmd_map,
    )


def get_preamp(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to read preamp status (Command29-aware)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_preamp",
            to_addr=to_addr,
            from_addr=from_addr,
            receiver=receiver,
            command29=True,
        )
    return build_cmd29_frame(
        to_addr, from_addr, _CMD_PREAMP, sub=_SUB_PREAMP_STATUS, receiver=receiver
    )


def set_preamp(
    level: int = 1,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Set preamp level (0=off, 1=PREAMP1, 2=PREAMP2)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_preamp",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_bcd_byte(level)]),
            receiver=receiver,
            command29=True,
        )
    return build_cmd29_frame(
        to_addr,
        from_addr,
        _CMD_PREAMP,
        sub=_SUB_PREAMP_STATUS,
        data=bytes([_bcd_byte(level)]),
        receiver=receiver,
    )


def get_digisel(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to read DIGI-SEL status (Command29-aware)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_digisel",
            to_addr=to_addr,
            from_addr=from_addr,
            receiver=receiver,
            command29=True,
        )
    return build_cmd29_frame(
        to_addr, from_addr, _CMD_PREAMP, sub=_SUB_DIGISEL_STATUS, receiver=receiver
    )


def set_digisel(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Set DIGI-SEL status (Command29-aware)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_digisel",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_bcd_byte(1 if on else 0)]),
            receiver=receiver,
            command29=True,
        )
    return build_cmd29_frame(
        to_addr,
        from_addr,
        _CMD_PREAMP,
        sub=_SUB_DIGISEL_STATUS,
        data=bytes([_bcd_byte(1 if on else 0)]),
        receiver=receiver,
    )


def get_nb(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build CI-V command to read NB status."""
    if cmd_map is not None:
        return _build_from_map(cmd_map, "get_nb", to_addr=to_addr, from_addr=from_addr)
    return build_civ_frame(to_addr, from_addr, _CMD_PREAMP, sub=_SUB_NB)


def set_nb(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Set Noise Blanker on/off."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_nb",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([0x01 if on else 0x00]),
            receiver=receiver,
            command29=(receiver != RECEIVER_MAIN),
        )
    data = bytes([0x01 if on else 0x00])
    if receiver != RECEIVER_MAIN:
        return build_cmd29_frame(
            to_addr, from_addr, _CMD_PREAMP, sub=_SUB_NB, data=data, receiver=receiver
        )
    return build_civ_frame(to_addr, from_addr, _CMD_PREAMP, sub=_SUB_NB, data=data)


def get_nr(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build CI-V command to read NR status."""
    if cmd_map is not None:
        return _build_from_map(cmd_map, "get_nr", to_addr=to_addr, from_addr=from_addr)
    return build_civ_frame(to_addr, from_addr, _CMD_PREAMP, sub=_SUB_NR)


def set_nr(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Set Noise Reduction on/off."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_nr",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([0x01 if on else 0x00]),
            receiver=receiver,
            command29=(receiver != RECEIVER_MAIN),
        )
    data = bytes([0x01 if on else 0x00])
    if receiver != RECEIVER_MAIN:
        return build_cmd29_frame(
            to_addr, from_addr, _CMD_PREAMP, sub=_SUB_NR, data=data, receiver=receiver
        )
    return build_civ_frame(to_addr, from_addr, _CMD_PREAMP, sub=_SUB_NR, data=data)


def get_ip_plus(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Build CI-V command to read IP+ status."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_ip_plus", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_PREAMP, sub=_SUB_IP_PLUS)


def set_ip_plus(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Set IP+ on/off."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_ip_plus",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([0x01 if on else 0x00]),
            receiver=receiver,
            command29=(receiver != RECEIVER_MAIN),
        )
    data = bytes([0x01 if on else 0x00])
    if receiver != RECEIVER_MAIN:
        return build_cmd29_frame(
            to_addr,
            from_addr,
            _CMD_PREAMP,
            sub=_SUB_IP_PLUS,
            data=data,
            receiver=receiver,
        )
    return build_civ_frame(to_addr, from_addr, _CMD_PREAMP, sub=_SUB_IP_PLUS, data=data)


def get_af_mute(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read AF Mute command."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_af_mute",
            to_addr=to_addr,
            from_addr=from_addr,
            receiver=receiver,
            command29=True,
        )
    return build_cmd29_frame(
        to_addr, from_addr, _CMD_CTL_MEM, sub=_SUB_AF_MUTE, receiver=receiver
    )


def set_af_mute(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set AF Mute command."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_af_mute",
            to_addr=to_addr,
            from_addr=from_addr,
            data=b"\x01" if on else b"\x00",
            receiver=receiver,
            command29=True,
        )
    return build_cmd29_frame(
        to_addr,
        from_addr,
        _CMD_CTL_MEM,
        sub=_SUB_AF_MUTE,
        data=b"\x01" if on else b"\x00",
        receiver=receiver,
    )


def get_agc(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read AGC mode command."""
    return _build_function_get(
        _SUB_AGC,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=receiver != RECEIVER_MAIN,
        cmd_map=cmd_map,
        cmd_name="get_agc",
    )


def set_agc(
    mode: AgcMode | int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set AGC mode command."""
    return _build_function_value_set(
        _SUB_AGC,
        int(AgcMode(mode)),
        minimum=int(AgcMode.FAST),
        maximum=int(AgcMode.SLOW),
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=receiver != RECEIVER_MAIN,
        cmd_map=cmd_map,
        cmd_name="set_agc",
    )


def get_audio_peak_filter(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read audio peak filter mode command."""
    return _build_function_get(
        _SUB_AUDIO_PEAK_FILTER,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_audio_peak_filter",
    )


def set_audio_peak_filter(
    mode: AudioPeakFilter | int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
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
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_audio_peak_filter",
    )


def get_auto_notch(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a read auto-notch status command."""
    return _build_function_get(
        _SUB_AUTO_NOTCH,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_auto_notch",
    )


def set_auto_notch(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a set auto-notch status command."""
    return _build_function_bool_set(
        _SUB_AUTO_NOTCH,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_auto_notch",
    )


def get_compressor(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    return _build_function_get(
        _SUB_COMPRESSOR,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_compressor",
    )


def set_compressor(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    return _build_function_bool_set(
        _SUB_COMPRESSOR,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_compressor",
    )


def get_monitor(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    return _build_function_get(
        _SUB_MONITOR,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_monitor",
    )


def set_monitor(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    return _build_function_bool_set(
        _SUB_MONITOR,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_monitor",
    )


def get_vox(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    return _build_function_get(
        _SUB_VOX,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_vox",
    )


def set_vox(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    return _build_function_bool_set(
        _SUB_VOX,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_vox",
    )


def get_break_in(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    return _build_function_get(
        _SUB_BREAK_IN,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_break_in",
    )


def set_break_in(
    mode: BreakInMode | int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    return _build_function_value_set(
        _SUB_BREAK_IN,
        int(BreakInMode(mode)),
        minimum=int(BreakInMode.OFF),
        maximum=int(BreakInMode.FULL),
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_break_in",
    )


def get_manual_notch(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    return _build_function_get(
        _SUB_MANUAL_NOTCH,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_manual_notch",
    )


def set_manual_notch(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    return _build_function_bool_set(
        _SUB_MANUAL_NOTCH,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_manual_notch",
    )


def get_manual_notch_width(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'get manual notch width' CI-V command (0x16 0x57)."""
    return _build_function_get(
        _SUB_MANUAL_NOTCH_WIDTH,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_manual_notch_width",
    )


def set_manual_notch_width(
    width: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'set manual notch width' CI-V command (0x16 0x57). 0=WIDE, 1=MID, 2=NAR."""
    return _build_function_value_set(
        _SUB_MANUAL_NOTCH_WIDTH,
        width,
        minimum=0,
        maximum=2,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_manual_notch_width",
    )


def get_twin_peak_filter(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    return _build_function_get(
        _SUB_TWIN_PEAK_FILTER,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="get_twin_peak_filter",
    )


def set_twin_peak_filter(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int = RECEIVER_MAIN,
    cmd_map: CommandMap | None = None,
) -> bytes:
    return _build_function_bool_set(
        _SUB_TWIN_PEAK_FILTER,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        receiver=receiver,
        command29=True,
        cmd_map=cmd_map,
        cmd_name="set_twin_peak_filter",
    )


def get_dial_lock(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    return _build_function_get(
        _SUB_DIAL_LOCK,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="get_dial_lock",
    )


def set_dial_lock(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    return _build_function_bool_set(
        _SUB_DIAL_LOCK,
        on,
        to_addr=to_addr,
        from_addr=from_addr,
        cmd_map=cmd_map,
        cmd_name="set_dial_lock",
    )
