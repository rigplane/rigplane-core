"""Observed CI-V wave playback from the opt-in IC-7610 LAN simulator.

All tests run on the virtual clock: the simulator's hand-advanced clock
pins wave pacing, real loopback delivery is awaited with ``await_until``,
and silence is asserted by advancing the clock and checking the client's
queue — never by waiting out a real timeout.
"""

import asyncio
import json
import struct
from pathlib import Path

import pytest

from rigplane.testing.timing import VirtualClock, await_until
from rigplane_testkit.icom import MockIcomRadio
from testkit.ic7610_fakes import _answer_table
from testkit.test_simulator_faults import (
    _HOST,
    _UdpClient,
    _header,
    _is_echo_of,
    _login,
    _openclose,
)

pytestmark = pytest.mark.timeout(30)

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/ic7610/civ_pairs.jsonl"
_ACK = bytes.fromhex("fefee098fbfd")
_NAK = bytes.fromhex("fefee098fafd")
_WAVE_PREFIX = bytes.fromhex("fefee0982700")


async def _client() -> _UdpClient:
    client = _UdpClient()
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(lambda: client, local_addr=(_HOST, 0))
    return client


async def _open(client: _UdpClient, sim: MockIcomRadio, seq: int = 1) -> None:
    client.send(_openclose(seq=seq), sim.civ_port)
    marker = await client.recv()
    assert marker[0x15:] == bytes.fromhex("fefe009800fd")


def _sent_waves(sim: MockIcomRadio) -> int:
    """Waves the simulator has sent so far (its per-receiver replay counters)."""
    return sum(sim._scope_template_index.values())


async def _waves(client: _UdpClient, at_least: int = 0) -> list[bytes]:
    """Collect radio-origin wave frames currently reaching the client.

    Every wave this returns was already sent before the call — the tests
    move simulator time with ``VirtualClock.advance`` and pump the stream
    by hand — so only real loopback delivery is outstanding, and
    ``await_until`` parks on real time for exactly that. ``at_least`` is
    the exact number of still-undrained waves (never a guess); with no
    count the queue is settled briefly and drained as-is.
    """
    if at_least:
        assert await await_until(
            lambda: client.queue.qsize() >= at_least,
            timeout=5.0,
            description=f"{at_least} loopback wave datagrams",
        )
    else:
        for _ in range(3):
            await asyncio.sleep(0)
    frames = []
    while not client.queue.empty():
        packet = client.queue.get_nowait()
        if packet[0x15:].startswith(_WAVE_PREFIX):
            frames.append(packet[0x15:])
    return frames


async def _assert_silence(
    clock: VirtualClock, client: _UdpClient, seconds: float
) -> None:
    """Advance the clock and assert nothing arrived (no real waiting)."""
    clock.advance(seconds)
    for _ in range(3):
        await asyncio.sleep(0)
    assert client.queue.empty(), "expected silence on the wire"


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
    assert _is_echo_of(await client.recv(), request)  # recorded LAN echo precedes reply
    reply = await client.recv()
    return reply[0x15:]


async def test_default_with_table_preserves_recorded_fb_and_no_waves(
    virtual_clock: VirtualClock,
) -> None:
    sim = MockIcomRadio(recorded_answers=_answer_table("lan"))
    await sim.start()
    client = await _client()
    try:
        for seq, payload in enumerate((b"\x10\x01", b"\x11\x01"), 1):
            assert await _wire_scope(client, sim, payload, seq) == _ACK
        assert sim.pump_scope_stream() == 0
        await _assert_silence(virtual_clock, client, 0.16)  # default stays silent
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


async def test_panel_set_starts_with_output_off(
    virtual_clock: VirtualClock,
) -> None:
    # The recorded scope window is the dual-receiver IC-7610's: SUB waves
    # only load for a dual-receiver personality (MOR-3907).
    sim = MockIcomRadio(scope_stream=True, clock=virtual_clock, dual_receiver=True)
    await sim.start()
    client = await _client()
    try:
        await _open(client, sim)
        assert sim._dispatch_civ(0x27, b"\x11", 0xE0) == bytes.fromhex(
            "fefee098271100fd"
        )
        _set(sim, b"\x10\x01")  # panel on cannot enable wave output
        virtual_clock.advance(1.0)
        assert sim.pump_scope_stream() == 0
        assert await _waves(client) == []
        _set(sim, b"\x11\x01")
        virtual_clock.advance(0.13)
        assert sim.pump_scope_stream() == 2
        assert len(await _waves(client, at_least=2)) == 2
    finally:
        client.close()
        await sim.stop()


async def test_recorded_bytes_and_distinct_receiver_cadences(
    virtual_clock: VirtualClock,
) -> None:
    # The recorded scope window is the dual-receiver IC-7610's: SUB waves
    # only load for a dual-receiver personality (MOR-3907).
    sim = MockIcomRadio(scope_stream=True, clock=virtual_clock, dual_receiver=True)
    await sim.start()
    client = await _client()
    try:
        await _open(client, sim)
        _set(sim, b"\x11\x01")
        observed: list[bytes] = []
        for step in range(120):  # 1.2 simulated seconds in 10 ms steps
            virtual_clock.advance(0.01)
            sim.pump_scope_stream()
            if step % 10 == 9:
                outstanding = _sent_waves(sim) - len(observed)
                observed.extend(await _waves(client, at_least=outstanding))
        outstanding = _sent_waves(sim) - len(observed)
        observed.extend(await _waves(client, at_least=outstanding))
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


async def test_delayed_consumer_gets_no_catchup_burst(
    virtual_clock: VirtualClock,
) -> None:
    # The recorded scope window is the dual-receiver IC-7610's: SUB waves
    # only load for a dual-receiver personality (MOR-3907).
    sim = MockIcomRadio(scope_stream=True, clock=virtual_clock, dual_receiver=True)
    await sim.start()
    client = await _client()
    try:
        await _open(client, sim)
        _set(sim, b"\x11\x01")
        virtual_clock.advance(60.0)
        assert sim.pump_scope_stream() == 2
        assert len(await _waves(client, at_least=2)) == 2
        assert sim.pump_scope_stream() == 0
        assert await _waves(client) == []
        virtual_clock.advance(0.035)
        assert sim.pump_scope_stream() == 1
        assert len(await _waves(client, at_least=1)) == 1
    finally:
        client.close()
        await sim.stop()


@pytest.mark.parametrize("fault", ["stall", "standby"])
async def test_fault_silence_resumes_at_cadence_without_backlog(
    virtual_clock: VirtualClock, fault: str
) -> None:
    # The recorded scope window is the dual-receiver IC-7610's: SUB waves
    # only load for a dual-receiver personality (MOR-3907).
    sim = MockIcomRadio(scope_stream=True, clock=virtual_clock, dual_receiver=True)
    await sim.start()
    client = await _client()
    try:
        await _open(client, sim)
        _set(sim, b"\x11\x01")
        if fault == "stall":
            sim.stall_for(30.0)
        else:
            sim.civ_standby(True)
        virtual_clock.advance(60.0)
        assert sim.pump_scope_stream() == 0
        assert await _waves(client) == []
        if fault == "stall":
            sim.stall_for(0.0)
        else:
            sim.civ_standby(False)
        assert sim.pump_scope_stream() == 0
        assert await _waves(client) == []
        virtual_clock.advance(0.035)
        assert sim.pump_scope_stream() == 1
        waves = await _waves(client, at_least=1)
        assert len(waves) == 1 and waves[0][6] == 1  # SUB fast; MAIN still due
    finally:
        client.close()
        await sim.stop()


async def test_same_owner_reopen_continues_new_owner_starts_silent(
    virtual_clock: VirtualClock,
) -> None:
    sim = MockIcomRadio(scope_stream=True, clock=virtual_clock)
    await sim.start()
    first = await _client()
    second = await _client()
    try:
        await _open(first, sim)
        _set(sim, b"\x11\x01")
        virtual_clock.advance(0.13)
        sim.pump_scope_stream()
        assert await _waves(first, at_least=1)
        await _open(first, sim, seq=2)  # production watchdog resend
        virtual_clock.advance(0.13)
        sim.pump_scope_stream()
        assert await _waves(first, at_least=1)
        await _open(second, sim)
        virtual_clock.advance(1.0)
        assert sim.pump_scope_stream() == 0
        assert await _waves(first) == []
        assert await _waves(second) == []
        _set(sim, b"\x11\x01")
        virtual_clock.advance(0.13)
        sim.pump_scope_stream()
        assert await _waves(second, at_least=1)
        assert await _waves(first) == []
    finally:
        first.close()
        second.close()
        await sim.stop()


async def test_preopen_and_foreign_scope_writes_cannot_enable_owner_output(
    virtual_clock: VirtualClock,
) -> None:
    sim = MockIcomRadio(scope_stream=True, clock=virtual_clock)
    await sim.start()
    owner = await _client()
    foreign = await _client()
    try:
        assert await _wire_scope(owner, sim, b"\x11\x01", 1) == _NAK
        await _open(owner, sim, seq=2)
        assert await _wire_scope(foreign, sim, b"\x11\x01", 1) == _NAK
        virtual_clock.advance(1.0)
        assert sim.pump_scope_stream() == 0
        assert await _waves(owner) == []
        assert await _waves(foreign) == []
        assert await _wire_scope(owner, sim, b"\x11\x01", 3) == _ACK
        virtual_clock.advance(0.13)
        sim.pump_scope_stream()
        assert await _waves(owner, at_least=1)
        assert await _waves(foreign) == []
    finally:
        owner.close()
        foreign.close()
        await sim.stop()


@pytest.mark.parametrize("end", ["close", "token_remove", "disconnect", "stop"])
async def test_session_end_stops_wave_output(
    virtual_clock: VirtualClock, end: str
) -> None:
    sim = MockIcomRadio(scope_stream=True, clock=virtual_clock)
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
        virtual_clock.advance(0.13)
        sim.pump_scope_stream()
        assert await _waves(civ, at_least=1)
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
        if end != "stop":
            # The session end is a datagram the simulator handles only when
            # it is delivered; wait for the simulator-side effect instead
            # of a fixed sleep.
            assert await await_until(
                lambda: not sim._scope_owner_open,
                timeout=5.0,
                description="the session end to stop scope output",
            )
        virtual_clock.advance(1.0)
        assert sim.pump_scope_stream() == 0
        assert await _waves(civ) == []
        if end != "stop":
            # The old UDP socket is still reachable, but its CI-V open
            # epoch has ended. A scope SET cannot silently revive playback.
            next_seq = 3 if end == "close" else 2
            assert await _wire_scope(civ, sim, b"\x11\x01", next_seq) == _NAK
            virtual_clock.advance(0.13)
            assert sim.pump_scope_stream() == 0
            assert await _waves(civ) == []
    finally:
        civ.close()
        control.close()
        if not stopped:
            await sim.stop()


async def test_live_loop_stops_after_server_stop(
    virtual_clock: VirtualClock,
) -> None:
    sim = MockIcomRadio(scope_stream=True, clock=virtual_clock)
    await sim.start()
    client = await _client()
    try:
        await _open(client, sim)
        _set(sim, b"\x11\x01")
        sent_before = _sent_waves(sim)
        await virtual_clock.elapse(0.3)
        # No manual pump; start() runs the loop, which the elapse drives.
        assert await _waves(client, at_least=_sent_waves(sim) - sent_before)
        await sim.stop()
        await _waves(client)  # drain any packets sent before stop
        await virtual_clock.elapse(0.16)
        assert await _waves(client) == []
    finally:
        client.close()
        await sim.stop()


async def test_unrecorded_speed_form_remains_nak() -> None:
    sim = MockIcomRadio(scope_stream=True)
    assert sim._dispatch_civ(0x27, b"\x1a\x01\x00", 0xE0) == _NAK
    assert sim._dispatch_civ(0x27, b"\x1a\x00\x00", 0xE0) == _ACK
