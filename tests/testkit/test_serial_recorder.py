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
