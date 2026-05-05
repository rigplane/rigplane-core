"""VFO, scan, dual watch, split, tuning step commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._codec import _bcd_byte
from ._frame import (
    CONTROLLER_ADDR,
    _CMD_CTL_MEM,
    _CMD_SCAN,
    _CMD_SPLIT,
    _CMD_TUNING_STEP,
    _CMD_VFO_EQUAL,
    _CMD_VFO_SELECT,
    _CTL_MEM_QUICK_DUAL_WATCH,
    _CTL_MEM_QUICK_SPLIT,
    _SUB_CTL_MEM,
    _VFO_DUAL_WATCH_OFF,
    _VFO_DUAL_WATCH_ON,
    _VFO_DUAL_WATCH_QUERY,
    _build_from_map,
    build_civ_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


def get_vfo(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'get VFO' CI-V command (0x07 read back current VFO)."""
    if cmd_map is not None:
        return _build_from_map(cmd_map, "get_vfo", to_addr=to_addr, from_addr=from_addr)
    return build_civ_frame(to_addr, from_addr, _CMD_VFO_SELECT)


def get_main_sub_band(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a 'get main/sub band' CI-V command (0x07 0xD2)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_main_sub_band",
            to_addr=to_addr,
            from_addr=from_addr,
            data=b"\xd2",
        )
    return build_civ_frame(to_addr, from_addr, _CMD_VFO_SELECT, data=b"\xd2")


def set_vfo(
    vfo: str = "A",
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Select VFO.

    Args:
        vfo: "A", "B", "MAIN", or "SUB".
    """
    codes = {"A": 0x00, "B": 0x01, "MAIN": 0xD0, "SUB": 0xD1}
    code = codes.get(vfo.upper(), 0x00)
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "set_vfo", to_addr=to_addr, from_addr=from_addr, data=bytes([code])
        )
    return build_civ_frame(to_addr, from_addr, _CMD_VFO_SELECT, data=bytes([code]))


def vfo_a_equals_b(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Copy VFO A to VFO B (A=B)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "set_vfo", to_addr=to_addr, from_addr=from_addr, data=b"\xa0"
        )
    return build_civ_frame(to_addr, from_addr, _CMD_VFO_EQUAL, data=b"\xa0")


def vfo_swap(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Swap VFO A and B."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "set_vfo", to_addr=to_addr, from_addr=from_addr, data=b"\xb0"
        )
    return build_civ_frame(to_addr, from_addr, _CMD_VFO_EQUAL, data=b"\xb0")


def set_split(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Enable or disable split mode."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_split",
            to_addr=to_addr,
            from_addr=from_addr,
            data=b"\x01" if on else b"\x00",
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_SPLIT, data=b"\x01" if on else b"\x00"
    )


def get_split(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to read split state (0x0F)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_split", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_SPLIT)


def get_tuning_step(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to get tuning step (0x10)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_tuning_step", to_addr=to_addr, from_addr=from_addr
        )
    return build_civ_frame(to_addr, from_addr, _CMD_TUNING_STEP)


def set_tuning_step(
    step: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to set tuning step (0x10)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_tuning_step",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_bcd_byte(step)]),
        )
    if not 0 <= step <= 8:
        raise ValueError(f"Tuning step must be 0-8, got {step}")
    return build_civ_frame(
        to_addr, from_addr, _CMD_TUNING_STEP, data=bytes([_bcd_byte(step)])
    )


def scan_start(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to start scanning (0x0E 0x01)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "scan_start", to_addr=to_addr, from_addr=from_addr, data=b"\x01"
        )
    return build_civ_frame(to_addr, from_addr, _CMD_SCAN, data=b"\x01")


def scan_stop(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to stop scanning (0x0E 0x00)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "scan_stop", to_addr=to_addr, from_addr=from_addr, data=b"\x00"
        )
    return build_civ_frame(to_addr, from_addr, _CMD_SCAN, data=b"\x00")


# Valid scan type sub-bytes for 0x0E command.
VALID_SCAN_TYPES = frozenset({0x01, 0x02, 0x03, 0x12, 0x22, 0x23})
# Valid ΔF span sub-bytes: 0xA1=±5kHz .. 0xA7=±1MHz.
VALID_DF_SPANS = frozenset(range(0xA1, 0xA8))
# Valid scan resume sub-bytes: 0xD0=OFF, 0xD1=5s, 0xD2=10s, 0xD3=15s.
VALID_SCAN_RESUME = frozenset(range(0xD0, 0xD4))


def scan_start_type(
    scan_type: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to start scan with specific type (0x0E sub).

    Valid scan_type values:
      0x01=programmed, 0x02=programmed P2, 0x03=ΔF,
      0x12=fine programmed, 0x22=memory, 0x23=select memory.
    """
    if scan_type not in VALID_SCAN_TYPES:
        raise ValueError(
            f"scan_type must be one of {sorted(hex(x) for x in VALID_SCAN_TYPES)}, got {hex(scan_type)}"
        )
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "scan_start_type",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([scan_type]),
        )
    return build_civ_frame(to_addr, from_addr, _CMD_SCAN, data=bytes([scan_type]))


def scan_set_df_span(
    df_span: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to set ΔF scan span (0x0E 0xA1-0xA7).

    0xA1=±5kHz, 0xA2=±10kHz, 0xA3=±20kHz, 0xA4=±50kHz,
    0xA5=±100kHz, 0xA6=±500kHz, 0xA7=±1MHz.
    """
    if df_span not in VALID_DF_SPANS:
        raise ValueError(f"df_span must be 0xA1-0xA7, got {hex(df_span)}")
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "scan_set_df_span",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([df_span]),
        )
    return build_civ_frame(to_addr, from_addr, _CMD_SCAN, data=bytes([df_span]))


def scan_set_resume(
    resume_mode: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to set scan resume mode (0x0E 0xD0-0xD3).

    0xD0=OFF, 0xD1=5sec, 0xD2=10sec, 0xD3=15sec.
    """
    if resume_mode not in VALID_SCAN_RESUME:
        raise ValueError(f"resume_mode must be 0xD0-0xD3, got {hex(resume_mode)}")
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "scan_set_resume",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([resume_mode]),
        )
    return build_civ_frame(to_addr, from_addr, _CMD_SCAN, data=bytes([resume_mode]))


def set_dual_watch_off(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to turn off dual watch (0x07 0xC0)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_dual_watch",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_VFO_DUAL_WATCH_OFF]),
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_VFO_SELECT, data=bytes([_VFO_DUAL_WATCH_OFF])
    )


def set_dual_watch_on(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to turn on dual watch (0x07 0xC1)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_dual_watch",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_VFO_DUAL_WATCH_ON]),
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_VFO_SELECT, data=bytes([_VFO_DUAL_WATCH_ON])
    )


def get_dual_watch(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to query dual watch status (0x07 0xC2)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_dual_watch",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_VFO_DUAL_WATCH_QUERY]),
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_VFO_SELECT, data=bytes([_VFO_DUAL_WATCH_QUERY])
    )


def set_dual_watch(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command to enable or disable dual watch."""
    return (
        set_dual_watch_on(to_addr=to_addr, from_addr=from_addr, cmd_map=cmd_map)
        if on
        else set_dual_watch_off(to_addr=to_addr, from_addr=from_addr, cmd_map=cmd_map)
    )


def quick_dual_watch(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command for one-shot dual watch trigger (0x1A 0x05 0x00 0x32)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "quick_dual_watch",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_CTL_MEM_QUICK_DUAL_WATCH,
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_CTL_MEM,
        sub=_SUB_CTL_MEM,
        data=_CTL_MEM_QUICK_DUAL_WATCH,
    )


def quick_split(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build CI-V command for one-shot split trigger (0x1A 0x05 0x00 0x33)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "quick_split",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_CTL_MEM_QUICK_SPLIT,
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_CTL_MEM, sub=_SUB_CTL_MEM, data=_CTL_MEM_QUICK_SPLIT
    )


# --- TOML canonical get_/set_ aliases ---


def get_quick_split(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Alias for quick_split -- trigger quick split (0x1A 0x05 0x00 0x33)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_quick_split",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_CTL_MEM_QUICK_SPLIT,
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_CTL_MEM, sub=_SUB_CTL_MEM, data=_CTL_MEM_QUICK_SPLIT
    )


def set_quick_split(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Alias for quick_split -- trigger quick split (0x1A 0x05 0x00 0x33)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_quick_split",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_CTL_MEM_QUICK_SPLIT,
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_CTL_MEM, sub=_SUB_CTL_MEM, data=_CTL_MEM_QUICK_SPLIT
    )


def get_quick_dual_watch(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Alias for quick_dual_watch -- trigger quick dual watch (0x1A 0x05 0x00 0x32)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_quick_dual_watch",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_CTL_MEM_QUICK_DUAL_WATCH,
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_CTL_MEM,
        sub=_SUB_CTL_MEM,
        data=_CTL_MEM_QUICK_DUAL_WATCH,
    )


def set_quick_dual_watch(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Alias for quick_dual_watch -- trigger quick dual watch (0x1A 0x05 0x00 0x32)."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "set_quick_dual_watch",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_CTL_MEM_QUICK_DUAL_WATCH,
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_CTL_MEM,
        sub=_SUB_CTL_MEM,
        data=_CTL_MEM_QUICK_DUAL_WATCH,
    )


# Backward-compat aliases
select_vfo = set_vfo
start_scan = scan_start
stop_scan = scan_stop
