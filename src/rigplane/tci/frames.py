"""TCI binary stream frames: header, sample types, conversion (stdlib only).

Wire layout (spec 3.4, ``<16I`` = 64 bytes): eight header fields, then
``count``, then ``reserv[7]``. A reader that only knows the 2.0 layout
(``<8I32x``) sees the same first eight fields; the trailing 32 bytes
are ``count`` plus ``reserv[7]``.

``length`` counts samples with all channels counted; ``count`` is the
per-stream packet counter, reset when switching RX/TX.
"""

from __future__ import annotations

import array
import math
import struct
from dataclasses import dataclass
from enum import IntEnum

__all__ = [
    "HEADER_SIZE",
    "BYTES_PER_SAMPLE",
    "PacketCounter",
    "SampleType",
    "StreamHeader",
    "StreamType",
    "convert_samples",
    "pack_header",
    "unpack_header",
]

#: Header size in bytes: sixteen little-endian uint32.
HEADER_SIZE = 64

_HEADER_STRUCT = struct.Struct("<16I")


class StreamType(IntEnum):
    """Binary frame stream type (spec 3.4)."""

    RX_AUDIO = 1
    TX_AUDIO = 2
    TX_CHRONO = 3
    LINE_OUT = 4


class SampleType(IntEnum):
    """Audio sample encoding (spec 3.4; default is float32)."""

    INT16 = 0
    INT24 = 1
    INT32 = 2
    FLOAT32 = 3


#: Encoded bytes per sample, by sample type.
BYTES_PER_SAMPLE = {
    SampleType.INT16: 2,
    SampleType.INT24: 3,
    SampleType.INT32: 4,
    SampleType.FLOAT32: 4,
}

_INT_RANGES = {
    SampleType.INT16: (-32768, 32767),
    SampleType.INT24: (-8388608, 8388607),
    SampleType.INT32: (-2147483648, 2147483647),
}


def _int_to_float(value: int, sample_type: SampleType) -> float:
    minimum, maximum = _INT_RANGES[sample_type]
    if value >= 0:
        return value / maximum
    return value / -minimum


def _float_to_int(value: float, sample_type: SampleType) -> int:
    minimum, maximum = _INT_RANGES[sample_type]
    if math.isnan(value):
        return 0
    if value >= 1.0:
        return maximum
    if value <= -1.0:
        return minimum
    # Round half away from zero onto the asymmetric two's-complement
    # rails (positive full scale is maximum, negative is minimum).
    if value >= 0.0:
        return min(maximum, math.floor(value * maximum + 0.5))
    return max(minimum, math.floor(value * -minimum + 0.5))


def _decode_int24(data: bytes) -> list[int]:
    return [
        int.from_bytes(data[i : i + 3], "little", signed=True)
        for i in range(0, len(data), 3)
    ]


def _encode_int24(values: list[int]) -> bytes:
    return b"".join(v.to_bytes(3, "little", signed=True) for v in values)


@dataclass(frozen=True)
class StreamHeader:
    """A parsed 64-byte TCI stream header."""

    receiver: int
    sample_rate: int
    sample_type: SampleType
    codec: int
    crc: int
    length: int
    stream_type: StreamType
    channels: int
    count: int
    reserv: tuple[int, ...] = (0, 0, 0, 0, 0, 0, 0)


def pack_header(header: StreamHeader) -> bytes:
    """Serialize a header to its 64 wire bytes."""
    if len(header.reserv) != 7:
        raise ValueError(f"reserv must hold 7 words, got {len(header.reserv)}")
    return _HEADER_STRUCT.pack(
        header.receiver,
        header.sample_rate,
        int(header.sample_type),
        header.codec,
        header.crc,
        header.length,
        int(header.stream_type),
        header.channels,
        header.count,
        *header.reserv,
    )


def unpack_header(data: bytes) -> StreamHeader:
    """Parse the first 64 bytes; needs at least 64 (``struct.error``).

    Unknown ``type``/``format`` values raise ``ValueError``: the leaf
    only speaks the four stream types and four sample types of spec 3.4.
    """
    if len(data) < HEADER_SIZE:
        raise struct.error(f"header needs {HEADER_SIZE} bytes, got {len(data)}")
    words = _HEADER_STRUCT.unpack_from(bytes(data[:HEADER_SIZE]))
    (
        receiver,
        sample_rate,
        sample_format,
        codec,
        crc,
        length,
        stream_type,
        channels,
        count,
        *reserv,
    ) = words
    return StreamHeader(
        receiver=receiver,
        sample_rate=sample_rate,
        sample_type=SampleType(sample_format),
        codec=codec,
        crc=crc,
        length=length,
        stream_type=StreamType(stream_type),
        channels=channels,
        count=count,
        reserv=tuple(reserv),
    )


def convert_samples(
    data: bytes,
    from_type: SampleType | int,
    to_type: SampleType | int,
) -> bytes:
    """Convert PCM samples between int16/int24/int32/float32.

    Every conversion runs through float64 intermediates: integers map
    their rails onto [-1, 1] exactly (positive full scale to the type
    maximum, negative to the minimum), and floats round half away from
    zero back onto the target rails. Float input clamps to [-1, 1] with
    NaN becoming silence and infinities the rails; float-to-float
    passes bytes through, so a NaN payload never crashes the converter.
    """
    source = SampleType(from_type)
    target = SampleType(to_type)
    if source is target:
        return bytes(data)
    width = BYTES_PER_SAMPLE[source]
    if len(data) % width != 0:
        raise ValueError(f"{len(data)} bytes is not a whole {source.name} frame")
    floats: list[float]
    if source is SampleType.INT16:
        values = array.array("h", data)
        if struct.pack("=h", 1) != struct.pack("<h", 1):
            values.byteswap()
        floats = [_int_to_float(v, source) for v in values]
    elif source is SampleType.INT24:
        floats = [_int_to_float(v, source) for v in _decode_int24(data)]
    elif source is SampleType.INT32:
        values32 = array.array("i", data)
        if struct.pack("=i", 1) != struct.pack("<i", 1):
            values32.byteswap()
        floats = [_int_to_float(v, source) for v in values32]
    else:
        raw = array.array("f", data)
        if struct.pack("=f", 1.0) != struct.pack("<f", 1.0):
            raw.byteswap()
        floats = list(raw)
        if target is SampleType.FLOAT32:
            return bytes(data)
    if target is SampleType.FLOAT32:
        out = array.array("f", floats)
        if struct.pack("=f", 1.0) != struct.pack("<f", 1.0):
            out.byteswap()
        return out.tobytes()
    if target is SampleType.INT24:
        return _encode_int24([_float_to_int(v, target) for v in floats])
    code = "h" if target is SampleType.INT16 else "i"
    out_int = array.array(code, [_float_to_int(v, target) for v in floats])
    if (code == "h" and struct.pack("=h", 1) != struct.pack("<h", 1)) or (
        code == "i" and struct.pack("=i", 1) != struct.pack("<i", 1)
    ):
        out_int.byteswap()
    return out_int.tobytes()


class PacketCounter:
    """Per-stream ``count``: increments modulo 2**32, resets to zero."""

    def __init__(self, start: int = 0) -> None:
        self._next = start % (1 << 32)

    def next(self) -> int:
        """Return the current count, then advance with wrap."""
        value = self._next
        self._next = (value + 1) % (1 << 32)
        return value

    def reset(self) -> None:
        """Reset to zero (called when switching RX/TX)."""
        self._next = 0
