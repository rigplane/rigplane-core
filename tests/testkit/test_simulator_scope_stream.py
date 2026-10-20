"""Observed CI-V wave playback from the opt-in IC-7610 LAN simulator."""

import asyncio
import json
import struct
from pathlib import Path

import pytest

from rigplane_testkit.icom import MockIcomRadio
from testkit.ic7610_fakes import _answer_table
from testkit.test_simulator_faults import _HOST, _UdpClient, _header, _login, _openclose

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/ic7610/civ_pairs.jsonl"
_ACK = bytes.fromhex("fefee098fbfd")
_NAK = bytes.fromhex("fefee098fafd")
_WAVE_PREFIX = bytes.fromhex("fefee0982700")


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def _client() -> _UdpClient:
    client = _UdpClient()
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(lambda: client, local_addr=(_HOST, 0))
    return client


async def _open(client: _UdpClient, sim: MockIcomRadio, seq: int = 1) -> None:
    client.send(_openclose(seq=seq), sim.civ_port)
    marker = await client.recv()
    assert marker[0x15:] == bytes.fromhex("fefe009800fd")


async def _waves(client: _UdpClient) -> list[bytes]:
    # Allow the loopback datagrams to reach the client, then inspect only
    # radio-origin wave frames; handshake markers and control replies differ.
    await asyncio.sleep(0.01)
    frames = []
    while not client.queue.empty():
        packet = client.queue.get_nowait()
        if packet[0x15:].startswith(_WAVE_PREFIX):
            frames.append(packet[0x15:])
    return frames


def _recorded_waves() -> dict[int, list[bytes]]:
    frames: dict[int, list[bytes]] = {0: [], 1: []}
    for line in _FIXTURE.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["window"] == "scope" and row["dir"] == "r2c":
            frame = bytes.fromhex(row["frame"])
            if frame.startswith(_WAVE_PREFIX):
                frames[frame[6]].append(frame)
    assert len(frames[0]) == 1 and len(frames[1]) == 4
    return frames


def _set(sim: MockIcomRadio, payload: bytes) -> None:
    assert sim._dispatch_civ(0x27, payload, 0xE0) == _ACK


def _packet(payload: bytes, seq: int) -> bytes:
    frame = bytes.fromhex("fefe98e027") + payload + b"\xfd"
    packet = _header(0x15 + len(frame), 0x00, seq=seq)
    packet[0x10] = 0xC1
    struct.pack_into("<H", packet, 0x11, len(frame))
    packet[0x15:] = frame
    return bytes(packet)


async def _wire_scope(
    client: _UdpClient, sim: MockIcomRadio, payload: bytes, seq: int
) -> bytes:
    request = _packet(payload, seq)
    client.send(request, sim.civ_port)
    assert await client.recv() == request  # recorded LAN echo precedes reply
    reply = await client.recv()
    return reply[0x15:]


async def test_default_with_table_preserves_recorded_fb_and_no_waves() -> None:
    sim = MockIcomRadio(recorded_answers=_answer_table("lan"))
    await sim.start()
    client = await _client()
    try:
        for seq, payload in enumerate((b"\x10\x01", b"\x11\x01"), 1):
            assert await _wire_scope(client, sim, payload, seq) == _ACK
        assert sim.pump_scope_stream() == 0
        await client.expect_silence(0.16)  # default stays silent while output is ON
        assert await _wire_scope(client, sim, b"\x11\x00", 3) == _ACK
        # A form outside the native 10-13 slice still uses the recorded table.
        assert await _wire_scope(client, sim, b"\x1a\x00", 4) == bytes.fromhex(
            "fefee098271a0001fd"
        )
        assert sim.pump_scope_stream() == 0
        assert await _waves(client) == []
    finally:
        client.close()
        await sim.stop()


async def test_panel_set_starts_with_output_off() -> None:
    clock = _Clock()
    sim = MockIcomRadio(scope_stream=True, clock=clock)
    await sim.start()
    client = await _client()
    try:
        await _open(client, sim)
        assert sim._dispatch_civ(0x27, b"\x11", 0xE0) == bytes.fromhex(
            "fefee098271100fd"
        )
        _set(sim, b"\x10\x01")  # panel on cannot enable wave output
        clock.advance(1.0)
        assert sim.pump_scope_stream() == 0
        assert await _waves(client) == []
        _set(sim, b"\x11\x01")
        clock.advance(0.13)
        assert sim.pump_scope_stream() == 2
        assert len(await _waves(client)) == 2
    finally:
        client.close()
        await sim.stop()


async def test_recorded_bytes_and_distinct_receiver_cadences() -> None:
    clock = _Clock()
    sim = MockIcomRadio(scope_stream=True, clock=clock)
    await sim.start()
    client = await _client()
    try:
        await _open(client, sim)
        _set(sim, b"\x11\x01")
        observed = []
        for step in range(120):  # 1.2 simulated seconds in 10 ms steps
            clock.advance(0.01)
            sim.pump_scope_stream()
            if step % 10 == 9:
                observed.extend(await _waves(client))
        observed.extend(await _waves(client))
        reference = _recorded_waves()
        main = [frame for frame in observed if frame[6] == 0]
        sub = [frame for frame in observed if frame[6] == 1]
        assert abs(len(main) - 10) <= 1
        assert abs(len(sub) - round(1.2 / 0.0344)) <= 1
        assert all(frame == reference[0][0] for frame in main)
        assert sub[:4] == reference[1]
        assert all(frame == reference[1][i % 4] for i, frame in enumerate(sub))
    finally:
        client.close()
        await sim.stop()


async def test_delayed_consumer_gets_no_catchup_burst() -> None:
    clock = _Clock()
    sim = MockIcomRadio(scope_stream=True, clock=clock)
    await sim.start()
    client = await _client()
    try:
        await _open(client, sim)
        _set(sim, b"\x11\x01")
        clock.advance(60.0)
        assert sim.pump_scope_stream() == 2
        assert len(await _waves(client)) == 2
        assert sim.pump_scope_stream() == 0
        assert await _waves(client) == []
        clock.advance(0.035)
        assert sim.pump_scope_stream() == 1
        assert len(await _waves(client)) == 1
    finally:
        client.close()
        await sim.stop()


@pytest.mark.parametrize("fault", ["stall", "standby"])
async def test_fault_silence_resumes_at_cadence_without_backlog(fault: str) -> None:
    clock = _Clock()
    sim = MockIcomRadio(scope_stream=True, clock=clock)
    await sim.start()
    client = await _client()
    try:
        await _open(client, sim)
        _set(sim, b"\x11\x01")
        if fault == "stall":
            sim.stall_for(30.0)
        else:
            sim.civ_standby(True)
        clock.advance(60.0)
        assert sim.pump_scope_stream() == 0
        assert await _waves(client) == []
        if fault == "stall":
            sim.stall_for(0.0)
        else:
            sim.civ_standby(False)
        assert sim.pump_scope_stream() == 0
        assert await _waves(client) == []
        clock.advance(0.035)
        assert sim.pump_scope_stream() == 1
        waves = await _waves(client)
        assert len(waves) == 1 and waves[0][6] == 1  # SUB fast; MAIN still due
    finally:
        client.close()
        await sim.stop()


async def test_same_owner_reopen_continues_new_owner_starts_silent() -> None:
    clock = _Clock()
    sim = MockIcomRadio(scope_stream=True, clock=clock)
    await sim.start()
    first = await _client()
    second = await _client()
    try:
        await _open(first, sim)
        _set(sim, b"\x11\x01")
        clock.advance(0.13)
        sim.pump_scope_stream()
        assert await _waves(first)
        await _open(first, sim, seq=2)  # production watchdog resend
        clock.advance(0.13)
        sim.pump_scope_stream()
        assert await _waves(first)
        await _open(second, sim)
        clock.advance(1.0)
        assert sim.pump_scope_stream() == 0
        assert await _waves(first) == []
        assert await _waves(second) == []
        _set(sim, b"\x11\x01")
        clock.advance(0.13)
        sim.pump_scope_stream()
        assert await _waves(second)
        assert await _waves(first) == []
    finally:
        first.close()
        second.close()
        await sim.stop()


async def test_preopen_and_foreign_scope_writes_cannot_enable_owner_output() -> None:
    clock = _Clock()
    sim = MockIcomRadio(scope_stream=True, clock=clock)
    await sim.start()
    owner = await _client()
    foreign = await _client()
    try:
        assert await _wire_scope(owner, sim, b"\x11\x01", 1) == _NAK
        await _open(owner, sim, seq=2)
        assert await _wire_scope(foreign, sim, b"\x11\x01", 1) == _NAK
        clock.advance(1.0)
        assert sim.pump_scope_stream() == 0
        assert await _waves(owner) == []
        assert await _waves(foreign) == []
        assert await _wire_scope(owner, sim, b"\x11\x01", 3) == _ACK
        clock.advance(0.13)
        sim.pump_scope_stream()
        assert await _waves(owner)
        assert await _waves(foreign) == []
    finally:
        owner.close()
        foreign.close()
        await sim.stop()


@pytest.mark.parametrize("end", ["close", "token_remove", "disconnect", "stop"])
async def test_session_end_stops_wave_output(end: str) -> None:
    clock = _Clock()
    sim = MockIcomRadio(scope_stream=True, clock=clock)
    await sim.start()
    civ = await _client()
    control = await _client()
    stopped = False
    try:
        if end == "disconnect":
            control.send(_login(), sim.control_port)
            await control.recv()
        await _open(civ, sim)
        _set(sim, b"\x11\x01")
        clock.advance(0.13)
        sim.pump_scope_stream()
        assert await _waves(civ)
        if end == "close":
            packet = bytearray(_openclose(seq=2))
            packet[0x15] = 0x00
            civ.send(bytes(packet), sim.civ_port)
        elif end == "token_remove":
            packet = _header(0x40, 0x00)
            packet[0x15] = 0x01
            struct.pack_into("<I", packet, 0x1C, sim.token)
            control.send(bytes(packet), sim.control_port)
        elif end == "disconnect":
            control.send(bytes(_header(0x10, 0x05)), sim.control_port)
        else:
            await sim.stop()
            stopped = True
        await asyncio.sleep(0.01)
        clock.advance(1.0)
        assert sim.pump_scope_stream() == 0
        assert await _waves(civ) == []
        if end != "stop":
            # The old UDP socket is still reachable, but its CI-V open
            # epoch has ended. A scope SET cannot silently revive playback.
            next_seq = 3 if end == "close" else 2
            assert await _wire_scope(civ, sim, b"\x11\x01", next_seq) == _NAK
            clock.advance(0.13)
            assert sim.pump_scope_stream() == 0
            assert await _waves(civ) == []
    finally:
        civ.close()
        control.close()
        if not stopped:
            await sim.stop()


async def test_live_loop_stops_after_server_stop() -> None:
    sim = MockIcomRadio(scope_stream=True)
    await sim.start()
    client = await _client()
    try:
        await _open(client, sim)
        _set(sim, b"\x11\x01")
        await asyncio.sleep(0.3)
        assert await _waves(client)  # no manual pump; start() runs the loop
        await sim.stop()
        await _waves(client)  # drain any packets sent before stop
        await asyncio.sleep(0.16)
        assert await _waves(client) == []
    finally:
        client.close()
        await sim.stop()


async def test_unrecorded_speed_form_remains_nak() -> None:
    sim = MockIcomRadio(scope_stream=True)
    assert sim._dispatch_civ(0x27, b"\x1a\x01\x00", 0xE0) == _NAK
    assert sim._dispatch_civ(0x27, b"\x1a\x00\x00", 0xE0) == _ACK
