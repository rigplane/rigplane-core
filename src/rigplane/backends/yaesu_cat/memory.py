"""FTX-1 stored memory channel as a read model (MOR-3628).

MR answers in the IF layout (CAT OM 2508-C p.19); MT carries the 12-character
tag. MR has no tone frequency, DCS code or per-channel offset, so those stay
unknown here. An unprogrammed channel answers ``?;`` (the caller maps that to
an empty entry).
"""

from __future__ import annotations

from typing import Any

from ...core.types import MemoryEntry, RepeaterShiftDirection, ToneSquelchType
from .parser import is_data_mode

# MR's own P8 codes (not the CT command's).
_TONE_BY_CODE = {
    "0": ToneSquelchType.OFF,
    "1": ToneSquelchType.TSQL,  # CTCSS encode and decode
    "2": ToneSquelchType.TONE,  # CTCSS encode
    "3": ToneSquelchType.DTCS,
    "4": ToneSquelchType.PR_FREQ,
    "5": ToneSquelchType.REV_TONE,
}
_SHIFT_BY_CODE = {
    "0": RepeaterShiftDirection.SIMPLEX,
    "1": RepeaterShiftDirection.PLUS,
    "2": RepeaterShiftDirection.MINUS,
}


def memory_entry_from_cat(
    channel: int, mr: dict[str, Any], tag: str, mode_name: str
) -> MemoryEntry:
    """Build the entry for ``channel`` from a parsed MR answer and its MT tag.

    Raises ``ValueError`` when MR answered another channel or an unknown
    tone code.
    """
    if mr["chan"] != f"{channel:05d}":
        raise ValueError(f"MR answered channel {mr['chan']!r} for {channel}")
    tone = _TONE_BY_CODE.get(mr["tone"])
    if tone is None:
        raise ValueError(f"unknown MR tone code {mr['tone']!r}")
    return MemoryEntry(
        group=0,
        channel=channel,
        frequency_hz=mr["freq"],
        mode=mode_name,
        data_mode=is_data_mode(mode_name),
        duplex=_SHIFT_BY_CODE.get(mr["shift"]),  # 3 = ARS has no stored direction
        tone_mode=tone,
        name=tag.rstrip(),
    )
