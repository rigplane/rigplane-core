"""Stream frame tests for ``rigplane.tci.frames`` (MOR-3821 L1, spec 3.4).

64-byte header, ``StreamType``/``SampleType``, sample conversion with
stdlib only, per-stream ``count``. Byte-exact vectors are built with
raw ``struct`` from the wire layout so the test does not reuse the
implementation it checks.
"""

import math
import struct

import pytest

from rigplane.tci.frames import (
    HEADER_SIZE,
    PacketCounter,
    SampleType,
    StreamHeader,
    StreamType,
    convert_samples,
    pack_header,
    unpack_header,
)

_WIRE = struct.Struct("<16I")


def _rx_header_bytes(**over: int) -> bytes:
    fields = {
        "receiver": 0,
        "sample_rate": 48000,
        "format": SampleType.FLOAT32,
        "codec": 0,
        "crc": 0,
        "length": 256,
        "type": StreamType.RX_AUDIO,
        "channels": 1,
        "count": 7,
    }
    fields.update(over)
    return _WIRE.pack(
        fields["receiver"],
        fields["sample_rate"],
        int(fields["format"]),
        fields["codec"],
        fields["crc"],
        fields["length"],
        int(fields["type"]),
        fields["channels"],
        fields["count"],
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )


def test_header_size_is_64() -> None:
    assert HEADER_SIZE == 64
    header = StreamHeader(
        receiver=0,
        sample_rate=48000,
        sample_type=SampleType.FLOAT32,
        codec=0,
        crc=0,
        length=256,
        stream_type=StreamType.RX_AUDIO,
        channels=1,
        count=0,
    )
    assert len(pack_header(header)) == 64


def test_rx_vector_byte_exact() -> None:
    raw = _rx_header_bytes()
    header = unpack_header(raw)
    assert header == StreamHeader(
        receiver=0,
        sample_rate=48000,
        sample_type=SampleType.FLOAT32,
        codec=0,
        crc=0,
        length=256,
        stream_type=StreamType.RX_AUDIO,
        channels=1,
        count=7,
        reserv=(0, 0, 0, 0, 0, 0, 0),
    )
    assert pack_header(header) == raw


def test_tx_and_chrono_vectors_byte_exact() -> None:
    tx = unpack_header(_rx_header_bytes(type=StreamType.TX_AUDIO, count=3))
    assert tx.stream_type is StreamType.TX_AUDIO
    assert tx.count == 3
    chrono = unpack_header(_rx_header_bytes(type=StreamType.TX_CHRONO, count=41))
    assert chrono.stream_type is StreamType.TX_CHRONO
    assert chrono.count == 41
    assert pack_header(chrono) == _rx_header_bytes(type=StreamType.TX_CHRONO, count=41)


def test_header_matches_documented_eesdr_prefix() -> None:
    # eesdr-tci packs the header as <8I32x with the same first eight
    # fields in the same order (design 8.1); the trailing 32 bytes carry
    # count plus reserv[7].
    prefix = struct.pack(
        "<8I",
        0,
        48000,
        SampleType.INT16,
        0,
        0,
        128,
        StreamType.RX_AUDIO,
        1,
    )
    raw = prefix + struct.pack("<8I", 9, 0, 0, 0, 0, 0, 0, 0)
    header = unpack_header(raw)
    assert (
        header.receiver,
        header.sample_rate,
        header.sample_type,
        header.length,
        header.stream_type,
        header.channels,
        header.count,
    ) == (0, 48000, SampleType.INT16, 128, StreamType.RX_AUDIO, 1, 9)


def test_unpack_short_buffer_raises() -> None:
    with pytest.raises(struct.error):
        unpack_header(b"\x00" * 63)


def test_unpack_uses_first_64_bytes() -> None:
    raw = _rx_header_bytes() + b"\x01\x02\x03"
    assert unpack_header(raw).count == 7


def test_unpack_unknown_type_or_format_raises() -> None:
    with pytest.raises(ValueError):
        unpack_header(_rx_header_bytes(type=9))
    with pytest.raises(ValueError):
        unpack_header(_rx_header_bytes(format=9))


def test_pack_rejects_bad_reserv() -> None:
    header = StreamHeader(
        receiver=0,
        sample_rate=48000,
        sample_type=SampleType.FLOAT32,
        codec=0,
        crc=0,
        length=1,
        stream_type=StreamType.RX_AUDIO,
        channels=1,
        count=0,
        reserv=(0, 0),
    )
    with pytest.raises(ValueError):
        pack_header(header)


def test_convert_identity_all_types() -> None:
    assert convert_samples(b"\x01\x02\x03\x04", SampleType.INT16, SampleType.INT16) == (
        b"\x01\x02\x03\x04"
    )
    assert convert_samples(b"\x01\x02\x03", SampleType.INT24, SampleType.INT24) == (
        b"\x01\x02\x03"
    )
    assert (
        convert_samples(b"\x01\x02\x03\x04", SampleType.INT32, SampleType.INT32)
        == b"\x01\x02\x03\x04"
    )
    assert (
        convert_samples(b"\x01\x02\x03\x04", SampleType.FLOAT32, SampleType.FLOAT32)
        == b"\x01\x02\x03\x04"
    )


def test_convert_rejects_ragged_input() -> None:
    with pytest.raises(ValueError):
        convert_samples(b"\x01\x02\x03", SampleType.INT16, SampleType.FLOAT32)


def test_int16_to_float32_exact() -> None:
    data = struct.pack("<3h", 0, 32767, -32768)
    out = convert_samples(data, SampleType.INT16, SampleType.FLOAT32)
    assert struct.unpack("<3f", out) == pytest.approx((0.0, 1.0, -1.0))


def test_float32_to_int16_clamps_and_rounds() -> None:
    data = struct.pack("<5f", 0.0, 1.0, -1.0, 1.5, -1.5)
    out = convert_samples(data, SampleType.FLOAT32, SampleType.INT16)
    assert struct.unpack("<5h", out) == (0, 32767, -32768, 32767, -32768)


def test_negative_ties_round_half_away_from_zero() -> None:
    """Exact ties round away from zero on each asymmetric rail.

    Tie points must be exact on their own rail: the negative rail is
    32768 (a power of two, so -0.5 LSB is exactly 2**-16), the
    positive rail is 32767 (so its smallest exact tie is 0.5).
    """
    data = struct.pack("<3f", 0.5, -0.5 / 32768, -0.5)
    out = convert_samples(data, SampleType.FLOAT32, SampleType.INT16)
    assert struct.unpack("<3h", out) == (16384, -1, -16384)


def test_int24_sign_and_widening() -> None:
    data = b"\xff\xff\x7f" + b"\x00\x00\x80"  # max, min
    as32 = convert_samples(data, SampleType.INT24, SampleType.INT32)
    assert struct.unpack("<2i", as32) == (2147483647, -2147483648)
    back = convert_samples(as32, SampleType.INT32, SampleType.INT24)
    assert back == data


def test_float_nan_and_inf_never_crash() -> None:
    # NaN becomes silence, infinities clamp to the rails.
    data = struct.pack("<3f", math.nan, math.inf, -math.inf)
    out = convert_samples(data, SampleType.FLOAT32, SampleType.INT16)
    assert struct.unpack("<3h", out) == (0, 32767, -32768)
    # float-to-float passes bytes through untouched (NaN payload kept).
    assert convert_samples(data, SampleType.FLOAT32, SampleType.FLOAT32) == data


def test_counter_wraps_and_resets() -> None:
    counter = PacketCounter(start=0xFFFFFFFE)
    assert [counter.next() for _ in range(4)] == [0xFFFFFFFE, 0xFFFFFFFF, 0, 1]
    counter.reset()
    assert counter.next() == 0


def test_round_trip_with_eesdr_tci_packet() -> None:
    """Pack with ``eesdr_tci.tci.TciDataPacket`` and unpack with the leaf.

    Runs on the mini where the ``eesdr-tci`` dev dependency resolves.
    Field names are the real 0.1.0 API (``rx``, ``data_format``,
    ``data_type``); the attribute access fails closed if it changes.
    ``eesdr-tci`` 0.1.0 predates the 2.1 ``count`` header field, so its
    wire image carries a zeroed tail and the leaf reads ``count == 0``.
    """
    import importlib

    pytest.importorskip("eesdr_tci", reason="mini-only dev dependency")
    tci_mod = importlib.import_module("eesdr_tci.tci")
    packet_cls = tci_mod.TciDataPacket
    payload = struct.pack("<4h", 0, 1000, -1000, 32767)
    packet = packet_cls(
        rx=0,
        sample_rate=48000,
        data_format=int(SampleType.INT16),
        codec=0,
        crc=0,
        length=4,
        data_type=int(StreamType.RX_AUDIO),
        channels=1,
        data=payload,
    )
    raw = bytes(packet.to_bytes())
    assert len(raw) == HEADER_SIZE + len(payload)
    header = unpack_header(raw[:HEADER_SIZE])
    assert (
        header.receiver,
        header.sample_rate,
        header.sample_type,
        header.length,
        header.stream_type,
        header.channels,
        header.count,
    ) == (0, 48000, SampleType.INT16, 4, StreamType.RX_AUDIO, 1, 0)
    assert raw[HEADER_SIZE:] == payload
