"""``CivFrameFilter``: the client-side frame gate for a CI-V capture."""

from __future__ import annotations

import asyncio
import json

from rigplane.commands import parse_civ_frame
from rigplane.core.serial_open import open_serial_port

from rigplane_testkit.serial import (
    CivFrameGuard,
    CivSerialFake,
    SerialCivRecorder,
)

READ = bytes.fromhex("fefe94e003fd")  # 03: read operating frequency
SET = bytes.fromhex("fefe94e0050000071400fd")  # 05: set frequency
ID_REQUEST = bytes.fromhex("fefe94e01900fd")


def _only_reads(frame: bytes) -> bool:
    return frame[4] in (0x03, 0x19)


def test_split_and_joined_chunks_forward_only_allowed_frames() -> None:
    flt = CivFrameGuard(_only_reads).new_filter()
    assert flt.feed(READ[:3]) == []  # partial frame held
    assert flt.feed(READ[3:] + SET + ID_REQUEST) == [
        (READ, True),
        (SET, False),
        (ID_REQUEST, True),
    ]


def test_junk_before_a_preamble_is_blocked_and_a_lone_fe_is_kept() -> None:
    flt = CivFrameGuard(_only_reads).new_filter()
    assert flt.feed(b"\xaa\xbb" + READ + b"\xfe") == [
        (b"\xaa\xbb", False),
        (READ, True),
    ]
    assert flt.feed(b"\xfe\x94\xe0\x03\xfd") == [
        (READ, True)
    ]  # FE + FE... completes it


def test_an_endless_unterminated_run_is_blocked_whole() -> None:
    flt = CivFrameGuard(_only_reads).new_filter()
    run = b"\xfe\xfe" + b"\x94" * 200
    assert flt.feed(run) == [(run, False)]
    assert flt.feed(READ) == [(READ, True)]  # a new preamble starts a fresh frame


async def test_refused_frame_never_reaches_the_radio_through_the_bridge(
    tmp_path,
) -> None:
    fake = CivSerialFake(echo=False, frequency_hz=14_074_000)
    out = tmp_path / "session.jsonl"
    rec = SerialCivRecorder(
        "fake-tty",
        out,
        opener=fake.opener,
        client_filter=CivFrameGuard(_only_reads).new_filter,
    )
    await rec.start()
    reader, writer = await open_serial_port(
        f"socket://127.0.0.1:{rec.listen_port}", baudrate=115200
    )
    try:
        writer.write(SET + READ)
        await writer.drain()
        answer = await asyncio.wait_for(reader.readuntil(b"\xfd"), timeout=2.0)
    finally:
        writer.close()
        await rec.stop()
    assert parse_civ_frame(answer).command == 0x03
    assert fake.frequency_hz == 14_074_000  # the SET never reached the radio
    assert rec.blocked_lines == 1
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [bytes.fromhex(r["hex"]) for r in rows if r["dir"] == "blocked"] == [SET]
    assert [bytes.fromhex(r["hex"]) for r in rows if r["dir"] == "c2r"] == [READ]
