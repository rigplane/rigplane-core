"""Speech announcement command (0x13)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._frame import (
    CONTROLLER_ADDR,
    _CMD_SPEECH,
    _build_from_map,
    build_civ_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


def get_speech(
    what: int = 0,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a speech announcement CI-V command (0x13).

    Fire-and-forget.  Triggers the IC-7610 voice synthesizer.

    Args:
        what: 0 = all (S-meter, frequency, mode),
              1 = frequency + S-meter,
              2 = mode.
    """
    if cmd_map is not None:
        speech_key = "set_speech" if cmd_map.has("set_speech") else "get_speech"
        return _build_from_map(
            cmd_map,
            speech_key,
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([what]),
        )
    if what not in (0, 1, 2):
        raise ValueError(f"speech 'what' must be 0, 1, or 2, got {what}")
    return build_civ_frame(to_addr, from_addr, _CMD_SPEECH, data=bytes([what]))


# Backward-compat alias
speech = get_speech
