"""Standalone scope-v2 decoder; accepts one complete binary message."""

import math
import struct
from typing import Literal, TypedDict


class ReferenceFrame(TypedDict):
    source: Literal["radio", "audio_fft", "iq_fft"]
    source_id: str
    receiver: int | None
    view_start_hz: float
    view_end_hz: float
    coverage_start_hz: float
    coverage_end_hz: float
    width: int
    pixels: list[int] | None
    out_of_range: bool
    epoch: int
    seq: int
    sent_at_ms: int | None


_HEADER = struct.Struct("<BBBBHHIIqdddd")
_KINDS: tuple[Literal["radio", "audio_fft", "iq_fft"], ...] = (
    "radio",
    "audio_fft",
    "iq_fft",
)


def decode(data: bytes) -> ReferenceFrame:
    if len(data) < _HEADER.size:
        raise ValueError("short header")
    (
        msg,
        kind,
        flags,
        length,
        receiver,
        width,
        epoch,
        seq,
        stamp,
        start,
        end,
        low,
        high,
    ) = _HEADER.unpack_from(data)
    if msg != 2 or kind >= len(_KINDS) or flags & ~3:
        raise ValueError("invalid message type, source or flags")
    offset = _HEADER.size + length
    if len(data) != offset + (width if flags & 2 else 0):
        raise ValueError("invalid message length")
    identity = data[_HEADER.size : offset]
    if not identity or any(c < 33 or c > 126 for c in identity):
        raise ValueError("invalid source ID")
    if (
        not all(math.isfinite(v) for v in (start, end, low, high))
        or not 0 <= start <= low <= high <= end
        or start == end
    ):
        raise ValueError("invalid frequency geometry")
    if width == 0 or not -1 <= stamp <= 2**53 - 1:
        raise ValueError("invalid width or timestamp")
    return ReferenceFrame(
        source=_KINDS[kind],
        source_id=identity.decode("ascii"),
        receiver=None if receiver == 65535 else receiver,
        view_start_hz=start,
        view_end_hz=end,
        coverage_start_hz=low,
        coverage_end_hz=high,
        width=width,
        pixels=list(data[offset:]) if flags & 2 else None,
        out_of_range=bool(flags & 1),
        epoch=epoch,
        seq=seq,
        sent_at_ms=None if stamp == -1 else stamp,
    )
