"""Opt-in IC-7610 scope wave streaming in the LAN simulator (MOR-3310 slice I).

With ``scope_stream=True`` ``MockIcomRadio`` answers the scope controls
(``27 10/11/12/13/1A``) natively from the recorded arrival state and, while
wave output is on, streams each receiver's recorded ``27 00`` template
frame on the CI-V stream at its own speed. Gaps come from
``tests/fixtures/ic7610/scope_cadence.json``; templates are the first
recorded ``27 00`` frame per receiver in the ``scope`` window of
``tests/fixtures/ic7610/civ_pairs.jsonl``. Reply shapes go through the
simulator's in-process ``_dispatch_civ`` and every wave frame through the
real CI-V stream to a raw UDP client, so both are pinned. Pacing reads the
simulator's injected clock; the cadence tests take over pacing explicitly
(cancelling the background loop like the gap tests do) so no wall time is
involved.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rigplane_testkit.icom import MockIcomRadio

from testkit.test_simulator_faults import _UdpClient, _openclose

_HOST = "127.0.0.1"
_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ic7610"

_FROM_CONTROLLER = 0xE0
_ACK = bytes.fromhex("fefee098fbfd")
_NAK = bytes.fromhex("fefee098fafd")

_SCOPE_WINDOWS = ("probe", "scope", "rx_write_restore")
_SCOPE_SUBS = (0x10, 0x11, 0x12, 0x13, 0x1A)


def _civ_pairs_rows() -> list[dict]:
    """Every row of the recorded CI-V pairs, in file order."""
    return [
        json.loads(line)
        for line in (_FIXTURES / "civ_pairs.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _is_wave_frame(frame: bytes) -> bool:
    """True for a ``27 00`` wave sweep frame (the stream, not a reply)."""
    return len(frame) > 6 and frame[4] == 0x27 and frame[5] == 0x00


def _recorded_scope_requests() -> list[tuple[bytes, bytes | None]]:
    """Every recorded ``27 10/11/12/13/1A`` request with its closing reply.

    The reply is the last radio frame before the next client frame that is
    neither the LAN echo of the request nor a ``27 00`` wave frame — or None
    when the window pairs none: the ``27 10 01`` / ``27 11 01`` SETs, whose
    answer is the wave stream itself.
    """
    out: list[tuple[bytes, bytes | None]] = []
    pending: bytes | None = None
    replies: list[bytes] = []
    for row in _civ_pairs_rows():
        if row.get("window") not in _SCOPE_WINDOWS:
            continue
        frame = bytes.fromhex(row["frame"])
        if row["dir"] == "c2r":
            if pending is not None and pending[4] == 0x27 and pending[5] in _SCOPE_SUBS:
                out.append((pending, replies[-1] if replies else None))
            pending, replies = frame, []
        elif pending is not None and frame != pending and not _is_wave_frame(frame):
            replies.append(frame)
    if pending is not None and pending[4] == 0x27 and pending[5] in _SCOPE_SUBS:
        out.append((pending, replies[-1] if replies else None))
    assert out, "no recorded 27 10/11/12/13/1A requests"
    return out


def _recorded_templates() -> dict[int, bytes]:
    """First recorded ``27 00`` frame per receiver in the scope window."""
    out: dict[int, bytes] = {}
    for row in _civ_pairs_rows():
        if row.get("window") != "scope" or row["dir"] != "r2c":
            continue
        frame = bytes.fromhex(row["frame"])
        if (
            len(frame) == 711
            and frame[4] == 0x27
            and frame[5] == 0x00
            and frame[6] not in out
        ):
            out[frame[6]] = frame
    assert set(out) == {0x00, 0x01}, "one template per receiver (MAIN/SUB)"
    assert out[0x00] != out[0x01], "the receivers have distinct templates"
    return out


class _ManualClock:
    """A hand-advanced clock driving the simulator's scope pacing."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def _stream_sim(clock: _ManualClock) -> MockIcomRadio:
    """A started scope-streaming simulator with pacing taken over by the test.

    The background loop is cancelled (like the gap tests do); the test
    drives ``pump_scope_stream`` after each clock step. The caller must
    ``await sim.stop()``.
    """
    sim = MockIcomRadio(scope_stream=True, recorded_answers=None, clock=clock)
    await sim.start()
    loop_task = sim._scope_task
    sim._scope_task = None
    if loop_task is not None:
        loop_task.cancel()
        try:
            await loop_task
        except (asyncio.CancelledError, Exception):
            pass
    return sim


async def _open_client(sim: MockIcomRadio) -> _UdpClient:
    """A raw UDP client owning the simulator's CI-V stream."""
    client = _UdpClient()
    await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: client, local_addr=(_HOST, 0)
    )
    client.send(_openclose(seq=0), sim.civ_port)
    await client.recv()  # the simulator's unsolicited open marker
    return client


async def _drain_waves(client: _UdpClient, timeout: float = 0.2) -> list[bytes]:
    """Collect CI-V stream packets until quiet; every one must be a wave frame."""
    frames: list[bytes] = []
    while True:
        try:
            pkt = await client.recv(timeout=timeout)
        except TimeoutError:
            return frames
        assert len(pkt) == 0x15 + 711, f"non-wave packet ({len(pkt)} bytes)"
        frame = pkt[0x15:]
        assert frame[4] == 0x27 and frame[5] == 0x00, "non-wave CI-V frame"
        assert len(frame) == 711
        frames.append(frame)


def _set(sim: MockIcomRadio, payload: bytes) -> None:
    """A scope SET through the in-process dispatcher (no UDP bookkeeping)."""
    assert sim._dispatch_civ(0x27, payload, _FROM_CONTROLLER) == _ACK


async def _run_stream(
    sim: MockIcomRadio, clock: _ManualClock, seconds: float, step: float = 0.05
) -> None:
    """Advance the manual clock in steps, pumping after each step.

    Each step breathes the event loop so the pumped datagrams reach the
    client queue instead of piling up in the UDP buffers as one burst.
    """
    for _ in range(int(round(seconds / step))):
        clock.advance(step)
        sim.pump_scope_stream()
        await asyncio.sleep(0.001)


# ---------------------------------------------------------------------------
# (a) reply shapes
# ---------------------------------------------------------------------------


def test_recorded_scope_requests_answered_in_recorded_shape() -> None:
    """Every recorded ``27 10/11/12/13/1A`` request gets its recorded reply.

    Reads match the recorded reply byte-for-byte; the ``27 10 01`` /
    ``27 11 01`` SETs (paired with no reply — their answer is the wave
    stream) are ACKed like every other recorded SET. Each pair runs on a
    fresh simulator so the recorded arrival state holds.
    """
    covered: set[tuple[int, int]] = set()
    for request, recorded in _recorded_scope_requests():
        sim = MockIcomRadio(scope_stream=True, recorded_answers=None)
        reply = sim._dispatch_civ(request[4], request[5:-1], _FROM_CONTROLLER)
        assert reply == (recorded if recorded is not None else _ACK), request.hex()
        covered.add((request[5], len(request) - 6))
    assert covered == {
        (0x10, 2),
        (0x11, 2),
        (0x12, 1),
        (0x12, 2),
        (0x13, 1),
        (0x13, 2),
        (0x1A, 1),  # bare 27 1A NAKs (no receiver byte), as recorded
        (0x1A, 2),
        (0x1A, 3),
    }, "reads and SETs of 12/13/1A plus the 10/11 SETs"


def test_scope_read_values_equal_simulator_state() -> None:
    """``27 12/13/1A`` reads report the simulator's own state values."""
    sim = MockIcomRadio(scope_stream=True, recorded_answers=None)
    reply = sim._dispatch_civ(0x27, b"\x12", _FROM_CONTROLLER)
    assert reply is not None and reply[6:7] == bytes((sim._scope_receiver,))
    reply = sim._dispatch_civ(0x27, b"\x13", _FROM_CONTROLLER)
    assert reply is not None and reply[6:7] == bytes((sim._scope_dual,))
    for receiver in (0x00, 0x01):
        reply = sim._dispatch_civ(0x27, bytes((0x1A, receiver)), _FROM_CONTROLLER)
        assert reply is not None
        assert reply[6:7] == bytes((receiver,))
        assert reply[7:8] == bytes((sim._scope_speed[receiver],))
    # The recorded arrival state: panel on, output off, dual, MAIN, MAIN mid.
    assert (sim._scope_panel, sim._scope_wave_output) == (0x01, 0x00)
    assert (sim._scope_receiver, sim._scope_dual) == (0x00, 0x01)
    assert sim._scope_speed == {0x00: 0x01, 0x01: 0x00}


def test_scope_set_then_read_roundtrip() -> None:
    """SETs land in state: a read behind each SET reports the new value."""
    sim = MockIcomRadio(scope_stream=True, recorded_answers=None)
    _set(sim, b"\x12\x01")
    assert sim._dispatch_civ(0x27, b"\x12", _FROM_CONTROLLER) == bytes.fromhex(
        "fefee098271201fd"
    )
    _set(sim, b"\x13\x00")
    assert sim._dispatch_civ(0x27, b"\x13", _FROM_CONTROLLER) == bytes.fromhex(
        "fefee098271300fd"
    )
    _set(sim, b"\x1a\x01\x02")
    assert sim._dispatch_civ(0x27, b"\x1a\x01", _FROM_CONTROLLER) == bytes.fromhex(
        "fefee098271a0102fd"
    )


# ---------------------------------------------------------------------------
# (b) cadence on a manual clock
# ---------------------------------------------------------------------------


async def test_dual_cadence_matches_recorded_gaps() -> None:
    """Output on, dual: MAIN sends ~100 and SUB ~round(12/0.0344) frames in 12 s.

    Every frame is byte-identical to its receiver's recorded template.
    """
    clock = _ManualClock()
    sim = await _stream_sim(clock)
    client = await _open_client(sim)
    try:
        templates = _recorded_templates()
        _set(sim, b"\x11\x01")
        await _run_stream(sim, clock, 12)
        frames = await _drain_waves(client)
        main = [frame for frame in frames if frame[6] == 0x00]
        sub = [frame for frame in frames if frame[6] == 0x01]
        assert len(main) + len(sub) == len(frames)
        assert abs(len(main) - 100) <= 1, len(main)
        assert abs(len(sub) - round(12 / 0.0344)) <= 1, len(sub)
        assert all(frame == templates[0x00] for frame in main)
        assert all(frame == templates[0x01] for frame in sub)
    finally:
        client.close()
        await sim.stop()


# ---------------------------------------------------------------------------
# (c) single streams only the selected scope receiver
# ---------------------------------------------------------------------------


async def test_single_streams_only_selected_receiver() -> None:
    """Single + SUB: only SUB frames; reselect MAIN: only MAIN frames."""
    clock = _ManualClock()
    sim = await _stream_sim(clock)
    client = await _open_client(sim)
    try:
        _set(sim, b"\x13\x00")
        _set(sim, b"\x12\x01")
        _set(sim, b"\x11\x01")
        await _run_stream(sim, clock, 3)
        frames = await _drain_waves(client)
        assert frames, "SUB streams in single mode"
        assert all(frame[6] == 0x01 for frame in frames)
        assert abs(len(frames) - round(3 / 0.0344)) <= 1, len(frames)
        _set(sim, b"\x12\x00")
        await _run_stream(sim, clock, 3)
        frames = await _drain_waves(client)
        assert frames, "MAIN streams once selected"
        assert all(frame[6] == 0x00 for frame in frames)
        assert abs(len(frames) - round(3 / 0.12)) <= 1, len(frames)
    finally:
        client.close()
        await sim.stop()


# ---------------------------------------------------------------------------
# (d) speed change
# ---------------------------------------------------------------------------


async def test_speed_change_retimes_receiver() -> None:
    """MAIN fast streams at the fast gap; MAIN slow streams nothing."""
    clock = _ManualClock()
    sim = await _stream_sim(clock)
    client = await _open_client(sim)
    try:
        _set(sim, b"\x11\x01")
        _set(sim, b"\x1a\x00\x00")
        assert sim._dispatch_civ(0x27, b"\x1a\x00", _FROM_CONTROLLER) == bytes.fromhex(
            "fefee098271a0000fd"
        )
        await _run_stream(sim, clock, 12)
        frames = await _drain_waves(client)
        main = [frame for frame in frames if frame[6] == 0x00]
        assert abs(len(main) - round(12 / 0.0344)) <= 1, len(main)
        _set(sim, b"\x1a\x00\x02")
        assert sim._dispatch_civ(0x27, b"\x1a\x00", _FROM_CONTROLLER) == bytes.fromhex(
            "fefee098271a0002fd"
        )
        await _run_stream(sim, clock, 12)
        frames = await _drain_waves(client)
        assert frames, "SUB keeps streaming while MAIN is slow"
        assert all(frame[6] == 0x01 for frame in frames)
    finally:
        client.close()
        await sim.stop()


# ---------------------------------------------------------------------------
# (e) output off and the default simulator
# ---------------------------------------------------------------------------


async def test_output_off_and_session_end_stop_stream() -> None:
    """Output off stops the frames; so does the end of the session."""
    clock = _ManualClock()
    sim = await _stream_sim(clock)
    client = await _open_client(sim)
    try:
        _set(sim, b"\x11\x01")
        await _run_stream(sim, clock, 1)
        assert await _drain_waves(client), "the stream runs while output is on"
        sent = sim.scope_frames_sent
        assert sent > 0
        _set(sim, b"\x11\x00")
        await _run_stream(sim, clock, 12)
        assert await _drain_waves(client) == []
        assert sim.scope_frames_sent == sent, "nothing sent while output is off"
        _set(sim, b"\x11\x01")
        sim.free_session("test")
        assert sim._dispatch_civ(0x27, b"\x11", _FROM_CONTROLLER) == bytes.fromhex(
            "fefee098271100fd"
        )
        await _run_stream(sim, clock, 12)
        assert await _drain_waves(client) == []
    finally:
        client.close()
        await sim.stop()


async def test_default_simulator_ignores_scope() -> None:
    """Without the keyword 0x27 keeps today's path: native NAK, no frames."""
    sim = MockIcomRadio()
    await sim.start()
    try:
        assert sim._scope_task is None, "no stream loop without the keyword"
        for payload in (
            b"\x10",
            b"\x10\x01",
            b"\x11",
            b"\x11\x01",
            b"\x12",
            b"\x12\x01",
            b"\x13",
            b"\x1a\x00",
        ):
            assert sim._dispatch_civ(0x27, payload, _FROM_CONTROLLER) == _NAK
        assert sim.pump_scope_stream() == 0
    finally:
        await sim.stop()
