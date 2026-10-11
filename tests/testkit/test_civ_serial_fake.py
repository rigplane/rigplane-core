"""Unit tests for ``CivSerialFake`` on raw bytes — no production link involved.

The production-link characterization lives in
``tests/testkit/test_serial_fake_through_link.py``.
"""

from __future__ import annotations

import asyncio
import errno
import time

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.profiles import resolve_radio_profile

from rigplane_testkit.serial import CivSerialFake


def _split_frames(data: bytes) -> list[bytes]:
    """Deliberately dumb FE FE ... FD splitter for assertions."""
    frames: list[bytes] = []
    buf = data
    while True:
        start = buf.find(b"\xfe\xfe")
        if start < 0:
            return frames
        end = buf.find(b"\xfd", start + 2)
        if end < 0:
            return frames
        frames.append(buf[start : end + 1])
        buf = buf[end + 1 :]


async def _read_frames(reader: asyncio.StreamReader, count: int) -> list[bytes]:
    """Read until *count* frames have arrived (or the 1 s guard fires)."""
    data = bytearray()
    deadline = time.monotonic() + 1.0
    while True:
        chunk = await asyncio.wait_for(reader.read(512), timeout=1.0)
        data += chunk
        frames = _split_frames(bytes(data))
        if len(frames) >= count or time.monotonic() > deadline:
            return frames


async def _roundtrip(
    fake: CivSerialFake, request: bytes, *, replies: int = 2
) -> list[bytes]:
    """Write one frame through a fresh pair and read *replies* frames back."""
    reader, writer = await fake.open_serial_connection()
    writer.write(request)
    return await _read_frames(reader, replies)


async def test_identity_answer_is_profile_derived() -> None:
    profile = resolve_radio_profile(model="IC-7300")
    fake = CivSerialFake()
    assert fake.civ_addr == profile.civ_addr
    assert fake.model_id == bytes.fromhex(profile.expected_identity_ids[0])

    request = build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x19, sub=0x00)
    echo, reply = await _roundtrip(fake, request)
    assert echo == request  # USB CI-V echo returns the inbound frame verbatim
    parsed = parse_civ_frame(reply)
    assert parsed.from_addr == fake.civ_addr
    assert parsed.to_addr == CONTROLLER_ADDR
    assert (parsed.command, parsed.sub) == (0x19, 0x00)
    assert parsed.data == fake.model_id


async def test_profile_without_expected_ids_must_not_guess_a_model_id() -> None:
    profile = resolve_radio_profile(model="IC-705")
    assert not profile.expected_identity_ids  # guard: the premise of this test
    with pytest.raises(ValueError, match="expected_ids"):
        CivSerialFake(model="IC-705")
    # An explicit manual-cited override still mounts that personality:
    # tests/civ_fake.py's identity table — the IC-705 answers 19 00 with
    # b"\x01\x05".
    explicit = CivSerialFake(model="IC-705", model_id=b"\x01\x05")
    assert explicit.model_id == b"\x01\x05"
    _, reply = await _roundtrip(
        explicit, build_civ_frame(explicit.civ_addr, CONTROLLER_ADDR, 0x19, sub=0x00)
    )
    assert parse_civ_frame(reply).data == b"\x01\x05"


async def test_frequency_get_set_roundtrip() -> None:
    fake = CivSerialFake(frequency_hz=14_074_000)
    _, get_reply = await _roundtrip(
        fake, build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x03)
    )
    assert parse_civ_frame(get_reply).data == b"\x00\x40\x07\x14\x00"

    _, set_reply = await _roundtrip(
        fake,
        build_civ_frame(
            fake.civ_addr, CONTROLLER_ADDR, 0x05, data=b"\x00\x50\x13\x07\x00"
        ),
    )
    assert parse_civ_frame(set_reply).command == 0xFB  # ACK
    assert fake.frequency_hz == 7_135_000

    _, get_reply = await _roundtrip(
        fake, build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x03)
    )
    assert parse_civ_frame(get_reply).data == b"\x00\x50\x13\x07\x00"


async def test_mode_and_filter_roundtrip() -> None:
    fake = CivSerialFake(mode=0x01, filter_width=1)
    _, reply = await _roundtrip(
        fake, build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x04)
    )
    assert parse_civ_frame(reply).data == bytes([0x01, 1])

    _, ack = await _roundtrip(
        fake,
        build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x06, data=b"\x00\x02"),
    )
    assert parse_civ_frame(ack).command == 0xFB
    assert fake.mode == 0x00  # LSB
    assert fake.filter_width == 2


async def test_ptt_get_and_set() -> None:
    fake = CivSerialFake()
    _, reply = await _roundtrip(
        fake, build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x1C, sub=0x00)
    )
    assert parse_civ_frame(reply).data == b"\x00"
    assert fake.ptt is False

    _, ack = await _roundtrip(
        fake,
        build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x1C, sub=0x00, data=b"\x01"),
    )
    assert parse_civ_frame(ack).command == 0xFB
    assert fake.ptt is True


async def test_s_meter_read() -> None:
    fake = CivSerialFake(s_meter=120)
    _, reply = await _roundtrip(
        fake, build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x15, sub=0x02)
    )
    assert parse_civ_frame(reply).data == b"\x01\x20"  # 2-byte BCD for 120


async def test_unknown_opcode_naks_but_broadcast_stays_silent() -> None:
    fake = CivSerialFake()
    _, reply = await _roundtrip(
        fake, build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x07, data=b"\xd2")
    )
    parsed = parse_civ_frame(reply)
    assert parsed.command == 0xFA  # NAK
    assert parsed.from_addr == fake.civ_addr

    frames = await _roundtrip(
        fake, build_civ_frame(0x00, CONTROLLER_ADDR, 0x07, data=b"\xd2"), replies=1
    )
    assert len(frames) == 1  # echo only — an unknown broadcast gets silence


async def test_frame_for_another_address_is_not_answered() -> None:
    fake = CivSerialFake()
    frames = await _roundtrip(
        fake, build_civ_frame(0xA4, CONTROLLER_ADDR, 0x19, sub=0x00), replies=1
    )
    assert len(frames) == 1  # echo only
    assert fake.commands[-1].to_addr == 0xA4  # still recorded


async def test_echo_can_be_disabled() -> None:
    fake = CivSerialFake(echo=False)
    (reply,) = await _roundtrip(
        fake, build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x03), replies=1
    )
    assert parse_civ_frame(reply).command == 0x03


async def test_delay_identity_answer() -> None:
    fake = CivSerialFake()
    fake.delay_identity_answer(0.15)
    reader, writer = await fake.open_serial_connection()
    started = time.monotonic()
    writer.write(build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x19, sub=0x00))
    frames = await _read_frames(reader, 2)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.15
    assert parse_civ_frame(frames[1]).data == fake.model_id


async def test_silence_keeps_the_link_open_but_answers_nothing() -> None:
    fake = CivSerialFake()
    reader, writer = await fake.open_serial_connection()
    fake.silence(0.2)
    writer.write(build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x03))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(reader.read(512), timeout=0.1)

    await asyncio.sleep(0.25)  # let the silence window lapse
    writer.write(build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x03))
    frames = await _read_frames(reader, 2)
    assert parse_civ_frame(frames[1]).command == 0x03


async def test_answer_identity_after_reconnect_only() -> None:
    fake = CivSerialFake()
    fake.answer_identity_after_reconnect_only()
    request = build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x19, sub=0x00)

    reader, writer = await fake.open_serial_connection()  # open #1
    writer.write(request)
    frames = await _read_frames(reader, 1)
    assert len(frames) == 1  # echo only — identity is dropped on the first open

    reader2, writer2 = await fake.open_serial_connection()  # the "reconnect"
    writer2.write(request)
    echo, reply = await _read_frames(reader2, 2)
    assert echo == request
    assert parse_civ_frame(reply).data == fake.model_id


async def test_wrong_model_id_defaults_to_another_profile_id() -> None:
    fake = CivSerialFake()
    fake.wrong_model_id()
    ic7610 = resolve_radio_profile(model="IC-7610")
    assert fake.model_id == bytes.fromhex(ic7610.expected_identity_ids[0])
    assert fake.model_id != bytes.fromhex(
        resolve_radio_profile(model="IC-7300").expected_identity_ids[0]
    )
    _, reply = await _roundtrip(
        fake, build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x19, sub=0x00)
    )
    assert parse_civ_frame(reply).data == fake.model_id


async def test_usb_vanish_and_reappear() -> None:
    fake = CivSerialFake()
    reader, writer = await fake.open_serial_connection()
    fake.usb_vanish(at=0.02)
    fake.usb_reappear(after=0.3)

    with pytest.raises(OSError) as read_error:
        await asyncio.wait_for(reader.read(512), timeout=1.0)
    assert read_error.value.errno == errno.ENODEV
    with pytest.raises(OSError):
        writer.write(build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x03))
    with pytest.raises(OSError) as open_error:
        await fake.open_serial_connection()
    assert open_error.value.errno == errno.ENODEV

    await asyncio.sleep(0.35)  # let the device reappear
    reader2, writer2 = await fake.open_serial_connection()
    writer2.write(build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x19, sub=0x00))
    echo, reply = await _read_frames(reader2, 2)
    assert parse_civ_frame(reply).data == fake.model_id
