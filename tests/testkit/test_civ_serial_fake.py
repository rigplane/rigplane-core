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

from rigplane_testkit.icom.replay import RecordedAnswerTable
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


# ---------------------------------------------------------------------------
# recorded_answers: a RecordedAnswerTable mounted on the fake
# ---------------------------------------------------------------------------

# Hand-built rows (radio 0x98, controller 0xE0, no echo rows -> echo fact
# False). The table keys on the frame body, so its rows answer requests to
# any radio address; the fake still answers only its own address.
_TABLE_ROWS = [
    {
        "window": "w",
        "transport": "usb",
        "t": 0.1,
        "dir": "c2r",
        "frame": "fefe98e003fd",
    },
    {
        "window": "w",
        "transport": "usb",
        "t": 0.101,
        "dir": "r2c",
        "frame": "fefee098030060281400fd",  # a recorded 03 read: 14 282 600 Hz
    },
    {
        "window": "w",
        "transport": "usb",
        "t": 0.2,
        "dir": "c2r",
        "frame": "fefe98e0290003fd",
    },
    {
        "window": "w",
        "transport": "usb",
        "t": 0.201,
        "dir": "r2c",
        "frame": "fefee098fafd",  # the radio NAKs the cmd29-wrapped 03 read
    },
    {
        "window": "w",
        "transport": "usb",
        "t": 0.3,
        "dir": "c2r",
        "frame": "fefe98e029001502fd",
    },
    {
        "window": "w",
        "transport": "usb",
        "t": 0.301,
        "dir": "r2c",
        "frame": "fefee098290015020000fd",  # the wrapped S-meter read, answered wrapped
    },
    {
        "window": "w",
        "transport": "usb",
        "t": 0.4,
        "dir": "c2r",
        "frame": "fefe98e0140afd",
    },
    {
        "window": "w",
        "transport": "usb",
        "t": 0.401,
        "dir": "r2c",
        "frame": "fefee098140a0013fd",  # a level read the fake has no native handler for
    },
]


def _recorded_table() -> RecordedAnswerTable:
    return RecordedAnswerTable.from_civ_pairs(_TABLE_ROWS, "usb")


def _to_fake(fake: CivSerialFake, body_hex: str) -> bytes:
    """A request frame to the fake's own address carrying ``body_hex``."""
    return bytes.fromhex(f"fefe{fake.civ_addr:02x}e0{body_hex}fd")


async def test_native_frequency_read_keeps_precedence_over_the_table() -> None:
    fake = CivSerialFake(recorded_answers=_recorded_table())
    (reply,) = await _roundtrip(fake, _to_fake(fake, "03"), replies=1)
    # The native state (default 14 074 000 Hz), not the recorded 14 282 600.
    assert parse_civ_frame(reply).data == b"\x00\x40\x07\x14\x00"


async def test_cmd29_wrapped_frequency_read_is_naked_from_the_table() -> None:
    fake = CivSerialFake(recorded_answers=_recorded_table())
    (reply,) = await _roundtrip(fake, _to_fake(fake, "290003"), replies=1)
    assert parse_civ_frame(reply).command == 0xFA  # NAK, never the native 03


async def test_cmd29_request_absent_from_the_table_is_naked() -> None:
    fake = CivSerialFake(recorded_answers=_recorded_table())
    (reply,) = await _roundtrip(fake, _to_fake(fake, "29001c00"), replies=1)
    # A wrapped PTT read: unwrapping into the native 1C 00 handler is forbidden.
    assert parse_civ_frame(reply).command == 0xFA


async def test_cmd29_wrapped_read_answered_wrapped_from_the_table() -> None:
    fake = CivSerialFake(recorded_answers=_recorded_table())
    (reply,) = await _roundtrip(fake, _to_fake(fake, "29001502"), replies=1)
    parsed = parse_civ_frame(reply)
    assert parsed.receiver == 0x00  # still wrapped: 29 00 15 02 ...
    assert parsed.command == 0x15
    assert parsed.sub == 0x02
    assert parsed.data == b"\x00\x00"


async def test_unwrapped_unknown_request_answered_from_the_table() -> None:
    fake = CivSerialFake(recorded_answers=_recorded_table())
    (reply,) = await _roundtrip(fake, _to_fake(fake, "140a"), replies=1)
    parsed = parse_civ_frame(reply)
    assert (parsed.command, parsed.sub) == (0x14, 0x0A)
    assert parsed.data == b"\x00\x13"


async def test_unwrapped_unknown_request_missing_from_the_table_naks() -> None:
    fake = CivSerialFake(recorded_answers=_recorded_table())
    (reply,) = await _roundtrip(fake, _to_fake(fake, "140b"), replies=1)
    assert parse_civ_frame(reply).command == 0xFA


async def test_echo_default_comes_from_the_table() -> None:
    fake = CivSerialFake(recorded_answers=_recorded_table())
    assert fake.echo is False  # the hand-built rows carry no echo row
    (reply,) = await _roundtrip(fake, _to_fake(fake, "03"), replies=1)
    assert parse_civ_frame(reply).command == 0x03  # the only frame back

    echoing = CivSerialFake(recorded_answers=_recorded_table(), echo=True)
    echo, reply2 = await _roundtrip(echoing, _to_fake(echoing, "03"), replies=2)
    assert echo == _to_fake(echoing, "03")  # an explicit echo= still wins
    assert parse_civ_frame(reply2).command == 0x03


async def test_without_a_table_the_fake_is_unchanged() -> None:
    fake = CivSerialFake()
    assert fake.echo is True
    request = build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x03)
    echo, reply = await _roundtrip(fake, request)
    assert echo == request
    assert parse_civ_frame(reply).data == b"\x00\x40\x07\x14\x00"
