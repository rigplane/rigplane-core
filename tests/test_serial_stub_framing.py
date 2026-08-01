"""Deterministic unit tests for serial-ready framing and receive stability."""

from __future__ import annotations

import pytest

from serial_stub import (
    DeterministicSerialCivLink,
    SerialFrameCodec,
    SerialFrameOverflowError,
    SerialFrameTimeoutError,
)


def test_serial_frame_codec_parses_single_frame() -> None:
    codec = SerialFrameCodec(max_frame_len=64, frame_timeout_s=0.05)
    frame = b"\xfe\xfe\x98\xe0\x03\xfd"
    assert codec.feed(frame) == [frame]


def test_serial_frame_codec_ignores_noise_and_parses_frame() -> None:
    codec = SerialFrameCodec(max_frame_len=64, frame_timeout_s=0.05)
    mixed = b"\x00\x01garbage\xfe\xfe\x98\xe0\x03\xfd\x99"
    assert codec.feed(mixed) == [b"\xfe\xfe\x98\xe0\x03\xfd"]


def test_serial_frame_codec_collects_partial_chunks() -> None:
    codec = SerialFrameCodec(max_frame_len=64, frame_timeout_s=0.05)
    assert codec.feed(b"\xfe\xfe\x98") == []
    assert codec.feed(b"\xe0\x03") == []
    assert codec.feed(b"\xfd") == [b"\xfe\xfe\x98\xe0\x03\xfd"]


def test_serial_frame_codec_multiple_frames_in_one_chunk() -> None:
    codec = SerialFrameCodec(max_frame_len=64, frame_timeout_s=0.05)
    chunk = b"\xfe\xfe\x98\xe0\x03\xfd\xfe\xfe\x98\xe0\x04\xfd"
    frames = codec.feed(chunk)
    assert frames == [b"\xfe\xfe\x98\xe0\x03\xfd", b"\xfe\xfe\x98\xe0\x04\xfd"]


def test_serial_frame_codec_overflow_raises() -> None:
    codec = SerialFrameCodec(max_frame_len=6, frame_timeout_s=0.05)
    codec.feed(b"\xfe\xfe\x98\xe0")
    with pytest.raises(SerialFrameOverflowError):
        codec.feed(b"\x03\x99\x98")


@pytest.mark.asyncio
async def test_serial_link_receive_timeout_without_frame_returns_none() -> None:
    link = DeterministicSerialCivLink(
        SerialFrameCodec(max_frame_len=64, frame_timeout_s=0.01)
    )
    assert await link.receive(timeout=0.005) is None


@pytest.mark.asyncio
async def test_serial_link_receive_timeout_for_partial_frame_raises() -> None:
    link = DeterministicSerialCivLink(
        SerialFrameCodec(max_frame_len=64, frame_timeout_s=0.001)
    )
    link.push_incoming_chunk(b"\xfe\xfe\x98")
    with pytest.raises(SerialFrameTimeoutError):
        await link.receive(timeout=0.01)


@pytest.mark.asyncio
async def test_serial_link_send_encodes_and_receive_decodes() -> None:
    link = DeterministicSerialCivLink(
        SerialFrameCodec(max_frame_len=64, frame_timeout_s=0.05)
    )
    await link.send(b"\x98\xe0\x03")
    assert link.sent_frames == [b"\xfe\xfe\x98\xe0\x03\xfd"]

    link.push_incoming_chunk(b"\xfe\xfe\x98\xe0\x03\xfd")
    assert await link.receive(timeout=0.01) == b"\xfe\xfe\x98\xe0\x03\xfd"
