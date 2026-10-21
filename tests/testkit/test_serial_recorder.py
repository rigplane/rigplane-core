"""``SerialCivRecorder`` — a serial CI-V bridge recorder behind a TCP listener.

Mirrors the LAN recorder's tests (``test_recorder.py``) and the CAT fake's
``socket://`` proof (``test_cat_fake.py``): the production
``open_serial_port`` client connects to the recorder's TCP listener with
``device="socket://127.0.0.1:<port>"``, ``CivSerialFake`` sits behind the
bridge through the ``opener`` seam, and every chunk lands in the JSONL.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from rigplane.commands import parse_civ_frame
from rigplane.core.serial_open import open_serial_port

from rigplane_testkit.serial import CivSerialFake, SerialCivRecorder, civ_frames

_ID_REQUEST = bytes.fromhex("fefe94e01900fd")  # 19 00 identity read, IC-7300 addr


async def test_bridge_round_trip_records_both_directions(tmp_path) -> None:
    fake = CivSerialFake(echo=False)
    out = tmp_path / "session.jsonl"
    rec = SerialCivRecorder("fake-tty", out, baudrate=115200, opener=fake.opener)
    await rec.start()
    reader, writer = await open_serial_port(
        f"socket://127.0.0.1:{rec.listen_port}", baudrate=115200
    )
    try:
        writer.write(_ID_REQUEST)
        await writer.drain()
        answer = await asyncio.wait_for(reader.readuntil(b"\xfd"), timeout=2.0)
    finally:
        writer.close()
        await rec.stop()

    parsed = parse_civ_frame(answer)
    assert (parsed.command, parsed.sub) == (0x19, 0x00)
    assert parsed.data == fake.model_id

    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["dir"] for r in rows].count("c2r") >= 1
    assert [r["dir"] for r in rows].count("r2c") >= 1
    assert all(r["port"] == "serial" for r in rows)
    assert b"".join(bytes.fromhex(r["hex"]) for r in rows if r["dir"] == "c2r") == (
        _ID_REQUEST
    )
    assert (
        b"".join(bytes.fromhex(r["hex"]) for r in rows if r["dir"] == "r2c") == answer
    )
    ts = [r["t"] for r in rows]
    assert ts == sorted(ts)  # monotonic non-decreasing in file order
    assert rec.records_written == len(rows)


def test_civ_frames_reassembles_split_and_joined_chunks(tmp_path) -> None:
    out = tmp_path / "hand.jsonl"
    rows = [
        # Junk before the first FE FE of c2r: discarded.
        {"t": 0.1, "dir": "c2r", "port": "serial", "hex": "e0"},
        # One frame split over two c2r chunks, interleaved with r2c traffic.
        {"t": 0.2, "dir": "c2r", "port": "serial", "hex": "fefe94e0"},
        {"t": 0.3, "dir": "r2c", "port": "serial", "hex": "fefee09419005cfd"},
        {"t": 0.4, "dir": "c2r", "port": "serial", "hex": "1900fd"},
        # Two complete frames in one r2c chunk.
        {"t": 0.5, "dir": "r2c", "port": "serial", "hex": "fefee094fbfdfefee094fafd"},
    ]
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert list(civ_frames(out)) == [
        (0.3, "r2c", bytes.fromhex("fefee09419005cfd")),
        (0.4, "c2r", bytes.fromhex("fefe94e01900fd")),
        (0.5, "r2c", bytes.fromhex("fefee094fbfd")),
        (0.5, "r2c", bytes.fromhex("fefee094fafd")),
    ]


def test_civ_frames_reassembles_preamble_split_across_chunks(tmp_path) -> None:
    out = tmp_path / "preamble-split.jsonl"
    rows = [
        # Split between the two FE bytes: the lone FE must survive until
        # the next chunk completes the preamble.
        {"t": 0.1, "dir": "c2r", "port": "serial", "hex": "fe"},
        {"t": 0.2, "dir": "c2r", "port": "serial", "hex": "fe94e01900fd"},
        # Split between FE FE and the address byte.
        {"t": 0.3, "dir": "r2c", "port": "serial", "hex": "fefe"},
        {"t": 0.4, "dir": "r2c", "port": "serial", "hex": "e09419005cfd"},
    ]
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert list(civ_frames(out)) == [
        (0.2, "c2r", bytes.fromhex("fefe94e01900fd")),
        (0.4, "r2c", bytes.fromhex("fefee09419005cfd")),
    ]


async def test_failed_device_open_keeps_existing_recording(tmp_path) -> None:
    fake = CivSerialFake(echo=False)
    out = tmp_path / "session.jsonl"
    out.write_text('{"t": 0.5, "dir": "c2r", "port": "serial", "hex": "fe9494"}\n')
    fake.usb_vanish()
    await asyncio.sleep(0.05)  # let the scheduled vanish fire before start
    rec = SerialCivRecorder("fake-tty", out, opener=fake.opener)
    with pytest.raises(OSError):
        await rec.start()
    assert "fe9494" in out.read_text()  # the old recording was not truncated


async def test_serve_forever_returns_when_the_device_goes_away(tmp_path) -> None:
    fake = CivSerialFake(echo=False)
    rec = SerialCivRecorder("fake-tty", tmp_path / "session.jsonl", opener=fake.opener)
    await rec.start()
    fake.usb_vanish()
    await asyncio.wait_for(rec.serve_forever(), timeout=2.0)
    assert rec.listen_port == 0  # serve_forever stopped the bridge


async def test_second_client_is_rejected(tmp_path) -> None:
    fake = CivSerialFake(echo=False)
    rec = SerialCivRecorder("fake-tty", tmp_path / "session.jsonl", opener=fake.opener)
    await rec.start()
    first_reader, first_writer = await open_serial_port(
        f"socket://127.0.0.1:{rec.listen_port}", baudrate=115200
    )
    try:
        # A round trip proves the bridge registered this client before the
        # second connections arrive.
        first_writer.write(_ID_REQUEST)
        await first_writer.drain()
        await asyncio.wait_for(first_reader.readuntil(b"\xfd"), timeout=2.0)
        for _ in range(2):
            reader, writer = await asyncio.open_connection("127.0.0.1", rec.listen_port)
            assert await asyncio.wait_for(reader.read(), timeout=2.0) == b""
            writer.close()
        assert rec.rejected_connections == 2
    finally:
        first_writer.close()
        await rec.stop()


_FREQ_READ = bytes.fromhex("fefe94e003fd")  # 03: read operating frequency
_FREQ_SET = bytes.fromhex("fefe94e0050000071400fd")  # 05: set frequency (a write)


async def test_c2r_guard_blocks_frames_and_records_them_apart(tmp_path) -> None:
    """A guarded bridge forwards only the frames the guard accepts.

    A refused frame never reaches the device (the fake's state stays put),
    is recorded as ``c2r_blocked`` and counted, and the allowed frame
    around it still gets its answer.
    """
    fake = CivSerialFake(echo=False, frequency_hz=14_074_000)
    out = tmp_path / "session.jsonl"
    rec = SerialCivRecorder(
        "fake-tty",
        out,
        opener=fake.opener,
        c2r_guard=lambda frame: frame[4] == 0x03,
    )
    await rec.start()
    reader, writer = await open_serial_port(
        f"socket://127.0.0.1:{rec.listen_port}", baudrate=115200
    )
    try:
        # One chunk holding a refused SET, then an allowed read.
        writer.write(_FREQ_SET + _FREQ_READ)
        await writer.drain()
        answer = await asyncio.wait_for(reader.readuntil(b"\xfd"), timeout=2.0)
    finally:
        writer.close()
        await rec.stop()

    assert parse_civ_frame(answer).command == 0x03
    assert fake.frequency_hz == 14_074_000  # the SET never reached the radio
    assert rec.blocked_frames == 1
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    blocked = [r for r in rows if r["dir"] == "c2r_blocked"]
    assert [bytes.fromhex(r["hex"]) for r in blocked] == [_FREQ_SET]
    forwarded = b"".join(bytes.fromhex(r["hex"]) for r in rows if r["dir"] == "c2r")
    assert forwarded == _FREQ_READ


async def test_c2r_guard_reassembles_a_frame_split_over_chunks(tmp_path) -> None:
    fake = CivSerialFake(echo=False)
    out = tmp_path / "session.jsonl"
    rec = SerialCivRecorder(
        "fake-tty", out, opener=fake.opener, c2r_guard=lambda frame: True
    )
    await rec.start()
    reader, writer = await open_serial_port(
        f"socket://127.0.0.1:{rec.listen_port}", baudrate=115200
    )
    try:
        writer.write(_ID_REQUEST[:3])
        await writer.drain()
        await asyncio.sleep(0.05)
        writer.write(_ID_REQUEST[3:])
        await writer.drain()
        answer = await asyncio.wait_for(reader.readuntil(b"\xfd"), timeout=2.0)
    finally:
        writer.close()
        await rec.stop()
    assert parse_civ_frame(answer).data == fake.model_id
    assert rec.blocked_frames == 0
