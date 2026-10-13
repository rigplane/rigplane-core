"""Opt-in IC-7610 scope wave streaming in the LAN simulator (MOR-3310 slice I).

With ``scope_stream=True`` ``MockIcomRadio`` answers the recorded scope
controls natively and, while wave output is on, streams each receiver's
recorded ``27 00`` template frame on the CI-V stream at its own speed.
Gaps come from ``tests/fixtures/ic7610/scope_cadence.json``; templates are
the first recorded ``27 00`` frame per receiver in the ``scope`` window of
``tests/fixtures/ic7610/civ_pairs.jsonl``. Only request forms recorded in
the probe/scope/rx_write_restore windows are answered natively
(``27 10 01`` / ``27 11 01`` send no reply frame — the wave stream is their
answer); every other 0x27 form keeps today's path with no state change.
Reply shapes go through the simulator's in-process ``_dispatch_civ`` and
every wave frame through the real CI-V stream to a raw UDP client, so both
are pinned. Pacing reads the simulator's injected clock; the cadence tests
take over pacing explicitly (cancelling the background loop like the gap
tests do) so no wall time is involved — except the live-loop test, which
runs the real loop on a short real-clock window.
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path

from rigplane_testkit.icom import MockIcomRadio

from testkit.test_simulator_faults import _CLIENT_ID, _login, _openclose, _UdpClient

_HOST = "127.0.0.1"
_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ic7610"

_FROM_CONTROLLER = 0xE0
_ACK = bytes.fromhex("fefee098fbfd")
_NAK = bytes.fromhex("fefee098fafd")

_SCOPE_WINDOWS = ("probe", "scope", "rx_write_restore")
_SCOPE_SUBS = (0x10, 0x11, 0x12, 0x13, 0x1A)

# The two recorded forms with no reply frame: echo only, then the stream.
_ECHO_ONLY_FORMS = (b"\x10\x01", b"\x11\x01")


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


def _check_wave_packet(pkt: bytes) -> bytes:
    """Assert one CI-V stream packet is a wave frame; return the frame."""
    assert len(pkt) == 0x15 + 711, f"non-wave packet ({len(pkt)} bytes)"
    frame = pkt[0x15:]
    assert frame[4] == 0x27 and frame[5] == 0x00, "non-wave CI-V frame"
    assert len(frame) == 711
    return frame


async def _drain_waves(client: _UdpClient, timeout: float = 0.2) -> list[bytes]:
    """Collect CI-V stream packets until quiet; every one must be a wave frame."""
    frames: list[bytes] = []
    while True:
        try:
            pkt = await client.recv(timeout=timeout)
        except TimeoutError:
            return frames
        frames.append(_check_wave_packet(pkt))


async def _collect_waves(client: _UdpClient, duration: float) -> list[bytes]:
    """Collect wave frames for a bounded window (the stream never goes quiet).

    For the live loop, where waiting for silence would never return.
    """
    frames: list[bytes] = []
    deadline = asyncio.get_running_loop().time() + duration
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return frames
        try:
            pkt = await client.recv(timeout=min(0.2, remaining))
        except TimeoutError:
            return frames
        frames.append(_check_wave_packet(pkt))


def _set(sim: MockIcomRadio, payload: bytes) -> None:
    """A scope SET through the in-process dispatcher (no UDP bookkeeping)."""
    assert sim._dispatch_civ(0x27, payload, _FROM_CONTROLLER) == _ACK


def _output_on(sim: MockIcomRadio) -> None:
    """Enable wave output: the recorded ``27 11 01`` SET sends no reply."""
    assert sim._dispatch_civ(0x27, b"\x11\x01", _FROM_CONTROLLER) is None
    assert sim._scope_wave_output == 0x01


async def _run_stream(
    sim: MockIcomRadio, clock: _ManualClock, seconds: float, step: float = 0.01
) -> None:
    """Advance the manual clock in steps, pumping after each step.

    The step stays below the fastest recorded gap (SUB fast, p10 0.0322 s)
    so at most one frame per receiver comes due per pump — the simulator
    never owes catch-up — and each step breathes the event loop so the
    pumped datagrams reach the client queue.
    """
    for _ in range(int(round(seconds / step))):
        clock.advance(step)
        sim.pump_scope_stream()
        await asyncio.sleep(0.001)


async def _run_stream_drained(
    sim: MockIcomRadio,
    clock: _ManualClock,
    client: _UdpClient,
    seconds: float,
    chunk: float = 1.0,
) -> list[bytes]:
    """Run the stream in chunks, draining the client between them.

    The client must keep reading while the simulator pumps: hundreds of
    unread datagrams overflow the socket buffers and the kernel drops the
    rest, which reads as missing frames.
    """
    frames: list[bytes] = []
    for _ in range(int(round(seconds / chunk))):
        await _run_stream(sim, clock, chunk)
        frames += await _drain_waves(client)
    return frames


def _token_remove(token: int) -> bytes:
    """Graceful logout: a 0x40 control packet carrying the live token."""
    pkt = bytearray(0x40)
    struct.pack_into("<I", pkt, 0x00, 0x40)
    struct.pack_into("<H", pkt, 0x04, 0x00)
    struct.pack_into("<I", pkt, 0x08, _CLIENT_ID)
    pkt[0x15] = 0x01  # token-remove: the session owner withdraws
    struct.pack_into("<I", pkt, 0x1C, token)
    return bytes(pkt)


def _disconnect() -> bytes:
    """A header-only control-port disconnect from the logged-in client.

    The sender id matches the shared login builder, so the packet matches
    the login identity the simulator remembers.
    """
    pkt = bytearray(0x10)
    struct.pack_into("<I", pkt, 0x00, 0x10)
    struct.pack_into("<H", pkt, 0x04, 0x05)  # disconnect
    struct.pack_into("<I", pkt, 0x08, _CLIENT_ID)
    return bytes(pkt)


def _civ_close(seq: int) -> bytes:
    """OpenClose(close): the tracked-packet shape of the open builder."""
    pkt = bytearray(_openclose(seq=seq))
    pkt[0x15] = 0x00  # graceful data-port close
    return bytes(pkt)


# ---------------------------------------------------------------------------
# (a) reply shapes
# ---------------------------------------------------------------------------


def test_recorded_scope_requests_answered_in_recorded_shape() -> None:
    """Every recorded ``27 10/11/12/13/1A`` request gets its recorded reply.

    Reads match the recorded reply byte-for-byte; recorded SETs are ACKed;
    the ``27 10 01`` / ``27 11 01`` SETs send no reply frame — the echo
    already went out on the wire path and the wave stream is their answer
    (the ``scope`` window pairs no reply for them). Each pair runs on a
    fresh simulator so the recorded arrival state holds.
    """
    covered: set[tuple[int, int]] = set()
    for request, recorded in _recorded_scope_requests():
        sim = MockIcomRadio(scope_stream=True, recorded_answers=None)
        reply = sim._dispatch_civ(request[4], request[5:-1], _FROM_CONTROLLER)
        if recorded is None:
            assert reply is None, request.hex()
            assert request[5:-1] in _ECHO_ONLY_FORMS, request.hex()
        else:
            assert reply == recorded, request.hex()
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
    }, "reads and SETs of 12/13/1A plus the echo-only 10/11 SETs"


def test_scope_read_values_equal_simulator_state() -> None:
    """``27 12/13/1A`` reads report the simulator's own state values."""
    sim = MockIcomRadio(scope_stream=True, recorded_answers=None)
    reply = sim._dispatch_civ(0x27, b"\x12", _FROM_CONTROLLER)
    assert reply is not None and reply[6:7] == bytes((sim._scope_receiver,))
    reply = sim._dispatch_civ(0x27, b"\x13", _FROM_CONTROLLER)
    assert reply is not None and reply[6:7] == bytes((sim._scope_dual,))
    reply = sim._dispatch_civ(0x27, b"\x1a\x00", _FROM_CONTROLLER)
    assert reply is not None
    assert reply[6:7] == b"\x00"
    assert reply[7:8] == bytes((sim._scope_speed[0x00],))
    # The recorded arrival state: panel on, output off, dual, MAIN, MAIN mid.
    assert (sim._scope_panel, sim._scope_wave_output) == (0x01, 0x00)
    assert (sim._scope_receiver, sim._scope_dual) == (0x00, 0x01)
    assert sim._scope_speed == {0x00: 0x01, 0x01: 0x00}


def test_scope_set_then_read_roundtrip() -> None:
    """Recorded SETs land in state: a read behind each SET reports the value."""
    sim = MockIcomRadio(scope_stream=True, recorded_answers=None)
    _set(sim, b"\x12\x01")
    assert sim._dispatch_civ(0x27, b"\x12", _FROM_CONTROLLER) == bytes.fromhex(
        "fefee098271201fd"
    )
    _set(sim, b"\x13\x00")
    assert sim._dispatch_civ(0x27, b"\x13", _FROM_CONTROLLER) == bytes.fromhex(
        "fefee098271300fd"
    )
    _set(sim, b"\x1a\x00\x00")
    assert sim._dispatch_civ(0x27, b"\x1a\x00", _FROM_CONTROLLER) == bytes.fromhex(
        "fefee098271a0000fd"
    )


def test_unrecorded_scope_forms_keep_todays_path() -> None:
    """Forms absent from the recording NAK with no state change.

    The bare ``27 10`` / ``27 11`` reads, the ``27 1A 01`` read and the
    ``00`` values of the 10/11 SETs never appear in the probe, scope or
    rx_write_restore windows, so they fall through to the recorded table
    (empty here) and the native NAK — and, as SETs, change nothing. The
    ``27 11 00`` case pins this while output is on: output stays on.
    """
    sim = MockIcomRadio(scope_stream=True, recorded_answers=None)
    _output_on(sim)
    for payload in (
        b"\x10",
        b"\x11",
        b"\x10\x00",
        b"\x11\x00",
        b"\x1a\x01",
        b"\x1a\x01\x00",
        b"\x1a\x00\x02",
    ):
        assert sim._dispatch_civ(0x27, payload, _FROM_CONTROLLER) == _NAK, payload.hex()
    assert (sim._scope_panel, sim._scope_wave_output) == (0x01, 0x01)
    assert (sim._scope_receiver, sim._scope_dual) == (0x00, 0x01)
    assert sim._scope_speed == {0x00: 0x01, 0x01: 0x00}
    # The recorded bare ``27 1A`` NAKs through today's path too, as recorded.
    assert sim._dispatch_civ(0x27, b"\x1a", _FROM_CONTROLLER) == _NAK


def test_panel_set_leaves_output_and_pacing_alone() -> None:
    """The panel SET never touches the wave-output flag or the schedule."""
    sim = MockIcomRadio(scope_stream=True, recorded_answers=None)
    _output_on(sim)
    due_before = dict(sim._scope_next_due)
    assert sim._dispatch_civ(0x27, b"\x10\x01", _FROM_CONTROLLER) is None
    assert sim._scope_panel == 0x01
    assert sim._scope_wave_output == 0x01
    assert sim._scope_next_due == due_before
    assert sim._dispatch_civ(0x27, b"\x10\x00", _FROM_CONTROLLER) == _NAK
    assert sim._scope_wave_output == 0x01


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
        _output_on(sim)
        frames = await _run_stream_drained(sim, clock, client, 12)
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


async def test_clock_jump_sends_at_most_one_frame_per_receiver() -> None:
    """A 60 s clock jump emits one frame per streaming receiver, then waits.

    The radio does not catch up missed sweeps: the next due time is one gap
    out from now, so an immediate second pump sends nothing.
    """
    clock = _ManualClock()
    sim = await _stream_sim(clock)
    client = await _open_client(sim)
    try:
        _output_on(sim)
        clock.advance(60)
        assert sim.pump_scope_stream() <= 2
        frames = await _drain_waves(client)
        assert len(frames) <= 2, len(frames)
        assert sum(1 for frame in frames if frame[6] == 0x00) <= 1
        assert sum(1 for frame in frames if frame[6] == 0x01) <= 1
        assert sim.pump_scope_stream() == 0
    finally:
        client.close()
        await sim.stop()


async def test_frequent_sets_to_main_do_not_stall_sub() -> None:
    """A SET retimes only the receiver it changes: SUB keeps its cadence."""
    clock = _ManualClock()
    sim = await _stream_sim(clock)
    client = await _open_client(sim)
    try:
        _output_on(sim)
        frames = []
        for step in range(400):  # 4 simulated seconds in 10 ms steps
            clock.advance(0.01)
            if step % 2 == 0:  # a MAIN SET every 20 ms
                _set(sim, b"\x1a\x00\x00")
            sim.pump_scope_stream()
            if step % 25 == 24:  # keep reading: an unread burst overflows
                frames += await _drain_waves(client)  # the socket buffers
        frames += await _drain_waves(client)
        sub = [frame for frame in frames if frame[6] == 0x01]
        main = [frame for frame in frames if frame[6] == 0x00]
        assert abs(len(sub) - round(4 / 0.0344)) <= 3, len(sub)
        assert main == [], "each MAIN SET restarts only MAIN's own schedule"
    finally:
        client.close()
        await sim.stop()


def test_slow_speed_streams_nothing() -> None:
    """Slow maps to no stream in the gap table (unrecorded on the wire).

    No recorded SET carries speed slow, so the value is injected directly:
    this pins only the fixture-derived gap table, not radio behaviour.
    """
    sim = MockIcomRadio(scope_stream=True, recorded_answers=None)
    sim._scope_wave_output = 0x01
    sim._scope_speed[0x00] = 0x02
    assert sim._scope_streaming_receivers() == [0x01]


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
        _output_on(sim)
        frames = await _run_stream_drained(sim, clock, client, 3)
        assert frames, "SUB streams in single mode"
        assert all(frame[6] == 0x01 for frame in frames)
        assert abs(len(frames) - round(3 / 0.0344)) <= 1, len(frames)
        _set(sim, b"\x12\x00")
        frames = await _run_stream_drained(sim, clock, client, 3)
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
    """MAIN fast streams at the fast gap; back to mid streams at the mid gap."""
    clock = _ManualClock()
    sim = await _stream_sim(clock)
    client = await _open_client(sim)
    try:
        _output_on(sim)
        _set(sim, b"\x1a\x00\x00")
        assert sim._dispatch_civ(0x27, b"\x1a\x00", _FROM_CONTROLLER) == bytes.fromhex(
            "fefee098271a0000fd"
        )
        frames = await _run_stream_drained(sim, clock, client, 12)
        main = [frame for frame in frames if frame[6] == 0x00]
        assert abs(len(main) - round(12 / 0.0344)) <= 1, len(main)
        _set(sim, b"\x1a\x00\x01")
        assert sim._dispatch_civ(0x27, b"\x1a\x00", _FROM_CONTROLLER) == bytes.fromhex(
            "fefee098271a0001fd"
        )
        frames = await _run_stream_drained(sim, clock, client, 12)
        main = [frame for frame in frames if frame[6] == 0x00]
        sub = [frame for frame in frames if frame[6] == 0x01]
        assert abs(len(main) - 100) <= 1, len(main)
        assert abs(len(sub) - round(12 / 0.0344)) <= 1, len(sub)
    finally:
        client.close()
        await sim.stop()


# ---------------------------------------------------------------------------
# (e) session ends stop the stream; the default simulator ignores scope
# ---------------------------------------------------------------------------


async def test_session_end_stops_stream_and_clears_output() -> None:
    """Output on runs the frames; the end of the session stops them."""
    clock = _ManualClock()
    sim = await _stream_sim(clock)
    client = await _open_client(sim)
    try:
        _output_on(sim)
        await _run_stream(sim, clock, 1)
        assert await _drain_waves(client), "the stream runs while output is on"
        sent = sim.scope_frames_sent
        assert sent > 0
        sim.free_session("test")
        assert sim._scope_wave_output == 0x00
        await _run_stream(sim, clock, 12)
        assert await _drain_waves(client) == []
        assert sim.scope_frames_sent == sent, "nothing sent after the session end"
    finally:
        client.close()
        await sim.stop()


async def test_logout_via_token_remove_stops_scope_stream() -> None:
    """Default mode: the owner's token-remove ends the wave stream."""
    clock = _ManualClock()
    sim = await _stream_sim(clock)
    client = await _open_client(sim)
    try:
        _output_on(sim)
        await _run_stream(sim, clock, 1)
        assert await _drain_waves(client), "the stream runs while output is on"
        sent = sim.scope_frames_sent
        client.send(_token_remove(sim.token), sim.control_port)
        await asyncio.sleep(0.1)
        assert sim._scope_wave_output == 0x00
        await _run_stream(sim, clock, 12)
        assert await _drain_waves(client) == []
        assert sim.scope_frames_sent == sent, "nothing sent after token-remove"
    finally:
        client.close()
        await sim.stop()


async def test_logout_via_disconnect_stops_scope_stream() -> None:
    """Default mode: the client's control-port disconnect ends the stream."""
    clock = _ManualClock()
    sim = await _stream_sim(clock)
    client = await _open_client(sim)
    try:
        _output_on(sim)
        await _run_stream(sim, clock, 1)
        assert await _drain_waves(client), "the stream runs while output is on"
        sent = sim.scope_frames_sent
        client.send(_login(), sim.control_port)
        await client.recv()  # the login response, before the wave drain
        client.send(_disconnect(), sim.control_port)
        await asyncio.sleep(0.1)
        assert sim._scope_wave_output == 0x00
        await _run_stream(sim, clock, 12)
        assert await _drain_waves(client) == []
        assert sim.scope_frames_sent == sent, "nothing sent after disconnect"
    finally:
        client.close()
        await sim.stop()


async def test_civ_close_stops_scope_stream() -> None:
    """Default mode: the stream owner's OpenClose(close) ends the stream."""
    clock = _ManualClock()
    sim = await _stream_sim(clock)
    client = await _open_client(sim)
    try:
        _output_on(sim)
        await _run_stream(sim, clock, 1)
        assert await _drain_waves(client), "the stream runs while output is on"
        sent = sim.scope_frames_sent
        client.send(_civ_close(seq=1), sim.civ_port)
        await asyncio.sleep(0.1)
        assert sim._scope_wave_output == 0x00
        await _run_stream(sim, clock, 12)
        assert await _drain_waves(client) == []
        assert sim.scope_frames_sent == sent, "nothing sent after CI-V close"
    finally:
        client.close()
        await sim.stop()


async def test_second_client_gets_no_frames_without_enabling_output() -> None:
    """A new opener starts silent: output enabled for one client leaks none."""
    clock = _ManualClock()
    sim = await _stream_sim(clock)
    first = await _open_client(sim)
    try:
        _output_on(sim)
        await _run_stream(sim, clock, 1)
        assert await _drain_waves(first), "the stream runs while output is on"
        second = await _open_client(sim)
        try:
            assert sim._scope_wave_output == 0x00
            await _run_stream_drained(sim, clock, first, 3)
            await second.expect_silence(0.3)
            assert await _drain_waves(first) == []
        finally:
            second.close()
    finally:
        first.close()
        await sim.stop()


async def test_live_loop_streams_and_stop_ends_it() -> None:
    """The background loop emits template frames on its own; stop() ends them."""
    sim = MockIcomRadio(scope_stream=True, recorded_answers=None)
    await sim.start()
    assert sim._scope_task is not None, "start() runs the stream loop"
    client = await _open_client(sim)
    stopped = False
    try:
        templates = _recorded_templates()
        _output_on(sim)
        frames = await _collect_waves(client, 0.6)
        main = [frame for frame in frames if frame[6] == 0x00]
        sub = [frame for frame in frames if frame[6] == 0x01]
        assert len(main) + len(sub) == len(frames) and frames
        assert len(sub) >= 5 and len(main) >= 1, (len(main), len(sub))
        assert all(frame == templates[0x00] for frame in main)
        assert all(frame == templates[0x01] for frame in sub)
        loop_task = sim._scope_task
        sent = sim.scope_frames_sent
        assert sent > 0
        await sim.stop()
        stopped = True
        assert loop_task is not None and loop_task.done(), "stop() ends the loop"
        await _drain_waves(client)  # stragglers sent before stop()
        await asyncio.sleep(0.3)
        assert await _drain_waves(client) == [], "no frame arrives after stop()"
        assert sim.scope_frames_sent == sent
    finally:
        client.close()
        if not stopped:
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


async def test_default_simulator_with_recorded_table_answers_from_table() -> None:
    """Default mode with the LAN table: 0x27 comes from the table, no stream."""
    from testkit.ic7610_fakes import _answer_table

    sim = MockIcomRadio(recorded_answers=_answer_table("lan"))
    await sim.start()
    client = await _open_client(sim)
    try:
        assert sim._scope_task is None, "no stream loop without the keyword"
        assert sim._dispatch_civ(0x27, b"\x13", _FROM_CONTROLLER) == bytes.fromhex(
            "fefee098271301fd"
        )
        assert sim._dispatch_civ(0x27, b"\x12\x01", _FROM_CONTROLLER) == _ACK
        assert sim._dispatch_civ(0x27, b"\x1a\x00", _FROM_CONTROLLER) == bytes.fromhex(
            "fefee098271a0001fd"
        )
        assert sim.pump_scope_stream() == 0
        await asyncio.sleep(0.15)
        assert await _drain_waves(client) == []
    finally:
        client.close()
        await sim.stop()
