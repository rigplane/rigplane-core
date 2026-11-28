"""Wire-level tests: the simulator's recorded CI-V sequence-gap behaviour.

A bare asyncio datagram client replays the client side of each window of
``tests/fixtures/ic7610/retransmit_gaps.jsonl`` (recorded real IC-7610, LAN
CI-V port, 2026-10-12) against a real ``MockIcomRadio`` and pins the wire
facts: a packet ahead of the expected tracked number opens a gap, later
packets are held, and while any number is missing the radio sends the
retransmit request twice per round — the single form (0x10-byte
type-0x01, header seq = the missing number) when exactly one number is
missing, otherwise one ranges packet (header seq 0, then little-endian u16
``[first, last]`` pairs, one per contiguous missing run). A tracked packet
held while a gap is open draws a round only when the previous round is at
least 75 ms old; the 0.1 s loop is the fallback for a silent client. A tracked packet carrying a missing number (any
tracked kind, idle included) fills it; once nothing is missing the held
packets are processed in sequence order, so a CI-V request held through
the stall is answered after the fill and no CI-V answer goes out while
the gap is open. Pings are answered throughout.

Tracked kinds are idle (0x10 type-0), OpenClose (0x16) and CI-V DATA —
never pings, discovery or retransmit requests. The OpenClose consumes its
header sequence number like any tracked packet (the production client
sends it through ``IcomTransport.send_tracked``). Tracking belongs to the
radio session: a later OpenClose(open) never restarts it, so a number at
or below the last processed one is ignored (recorded real IC-7610,
MOR-3361). Tracking restarts only on a new session (an accepted login, a
conninfo claiming a new session) or after ``drop_session`` / reboot.
Round pacing mirrors the recording: a tracked packet held while a gap is
open draws a round only when the previous round is at least 75 ms old
(recorded rounds are never closer than 77 ms; row 143 @1.005 draws no
round 3 ms after one) — the 0.1 s loop only fills a silent client's gap.

Every client-side sequence number, request kind and range asserted below
comes from the fixture file (loaded by :func:`_fixture_rows`), never from
a hardcoded copy of it.
"""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import AsyncGenerator, Coroutine
from pathlib import Path
from typing import Any, TypeVar

import pytest

from rigplane.radio import IcomRadio
from rigplane.testing.timing import VirtualClock, await_until

from rigplane_testkit.icom import FaultPlan, FaultRule, MockIcomRadio

from _perf_helpers import fast_connect
from testkit.test_simulator_faults import (
    _UdpClient,
    _conninfo,
    _freq_get,
    _header,
    _is_echo_of,
    _is_freq_reply,
    _login,
    _openclose,
    _ping,
)

_HOST = "127.0.0.1"

_PT_DATA = 0x00
_PT_RETRANSMIT = 0x01
_PT_PING = 0x07

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "ic7610"
    / "retransmit_gaps.jsonl"
)

# A stuck virtual-clock test would wait forever: its own loop time only moves
# on ``elapse``, so this real-time watchdog is the bound (the pairing
# ``src/rigplane/testing/timing.py`` mandates).
pytestmark = pytest.mark.timeout(30)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Fixture access (M1: the file is the source of every asserted number)
# ---------------------------------------------------------------------------


def _fixture_rows(window: str) -> list[dict]:
    """All rows of one fixture window, in file order."""
    rows = []
    with open(_FIXTURE_PATH, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row["window"] == window:
                rows.append(row)
    assert rows, f"fixture window {window!r} missing in {_FIXTURE_PATH.name}"
    return rows


def _client_side(rows: list[dict]) -> list[tuple[int, str]]:
    """Client (c2r) ``(seq, kind)`` script of a window, repeats expanded."""
    out = []
    for row in rows:
        if row["dir"] == "c2r":
            out.extend([(row["seq"], row["kind"])] * row.get("repeat", 1))
    return out


def _radio_requests(rows: list[dict]) -> list[tuple[str, int | None, object]]:
    """Radio (r2c) retransmit ``(kind, seq, ranges)`` rows of a window."""
    return [
        (row["kind"], row.get("seq"), row.get("ranges"))
        for row in rows
        if row["dir"] == "r2c" and row["kind"].startswith("retransmit")
    ]


# ---------------------------------------------------------------------------
# Wire builders / parsers (client side)
# ---------------------------------------------------------------------------


def _idle(seq: int) -> bytes:
    """A tracked idle keepalive: 0x10-byte type-0 control packet with a seq."""
    return bytes(_header(0x10, _PT_DATA, seq=seq))


def _parse_retransmit(pkt: bytes) -> tuple | None:
    """Classify a radio retransmit request, or return None for other packets.

    Returns ``("single", seq)`` for the 0x10-byte form and
    ``("ranges", [(first, last), ...])`` for the longer form.
    """
    if len(pkt) < 0x10:
        return None
    if struct.unpack_from("<H", pkt, 4)[0] != _PT_RETRANSMIT:
        return None
    if len(pkt) == 0x10:
        return ("single", struct.unpack_from("<H", pkt, 6)[0])
    pairs = []
    off = 0x10
    while off + 4 <= len(pkt):
        first, last = struct.unpack_from("<HH", pkt, off)
        pairs.append((first, last))
        off += 4
    return ("ranges", pairs)


def _drain_nowait(client: _UdpClient) -> list[bytes]:
    """Take every datagram loopback has delivered so far, without waiting."""
    out: list[bytes] = []
    while not client.queue.empty():
        try:
            out.append(client.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


async def _collect(
    client: _UdpClient, clock: VirtualClock, duration: float
) -> list[bytes]:
    """Gather every packet sent within ``duration`` virtual seconds.

    Advances the clock (firing the simulator's 0.1 s fallback loop on the
    way), then takes everything loopback delivered. The advance settles the
    loop first, so packets sent before it are processed at the old reading
    and only timer-driven rounds land in the window.
    """
    await clock.elapse(duration)
    for _ in range(3):
        await asyncio.sleep(0)
    return _drain_nowait(client)


async def _next(client: _UdpClient) -> bytes:
    """The next datagram loopback delivers (a real event, generously bounded)."""

    def _pop() -> bytes | None:
        try:
            return client.queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    return await await_until(_pop, timeout=10.0, description="the next datagram")


async def _take(client: _UdpClient, n: int, description: str) -> list[bytes]:
    """Take exactly ``n`` datagrams loopback delivers (a real event, bounded)."""
    await await_until(
        lambda: client.queue.qsize() >= n, timeout=10.0, description=description
    )
    return [client.queue.get_nowait() for _ in range(n)]


def _is_ping_reply(pkt: bytes) -> bool:
    """Whether ``pkt`` is a ping reply (0x15, ping type, reply flag set)."""
    return (
        len(pkt) == 0x15
        and struct.unpack_from("<H", pkt, 4)[0] == _PT_PING
        and pkt[0x10] == 0x01
    )


def _inject(sim: MockIcomRadio, client: _UdpClient, pkt: bytes) -> None:
    """Feed one client packet through the simulator's synchronous entry point.

    ``MockIcomRadio._on_packet`` handles the datagram inline, so it is
    processed at the current virtual-clock reading — unlike a real ``send``,
    whose loopback delivery would land at whatever virtual time the next
    ``elapse`` settles. The simulator's radio-to-client answers still travel
    the real loopback socket and are read from ``client``.
    """
    transport = client.transport
    assert transport is not None
    proto = sim._civ_proto
    assert proto is not None
    sim._on_packet(pkt, transport.get_extra_info("sockname"), "civ", proto)


async def _drive(
    clock: VirtualClock,
    step: str,
    coro: Coroutine[Any, Any, T],
    budget_s: float,
    granule_s: float = 0.05,
) -> T:
    """Run ``coro`` while advancing ``clock`` until it finishes.

    Awaiting a production-client coroutine directly would freeze on a reply
    the client's own virtual timeouts guard: nothing else moves the clock.
    Each ``elapse`` fires every timer on the way and settles loop callbacks,
    so real localhost I/O interleaves with timer progress (the shape of the
    suite's G7250 journey helper). Only an exhausted budget raises.
    """
    task = asyncio.create_task(coro)
    spent = 0.0
    try:
        while not task.done():
            if spent >= budget_s:
                break
            step_s = min(granule_s, budget_s - spent)
            await clock.elapse(step_s)
            spent += step_s
        if task.done():
            return task.result()
    except BaseException:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        raise
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    raise AssertionError(f"{step} did not finish within {budget_s:g} virtual s")


async def _open_stream(
    client: _UdpClient, server: MockIcomRadio, open_seq: int
) -> None:
    """Open the CI-V stream with a tracked OpenClose and swallow its marker.

    The open consumes ``open_seq``: the next tracked packet must carry
    ``open_seq + 1``, the way ``IcomTransport.send_tracked`` numbers a
    real client's stream.
    """
    client.send(_openclose(seq=open_seq), server.civ_port)
    marker = await client.recv()
    assert _parse_retransmit(marker) is None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def server(
    virtual_clock: VirtualClock,
) -> AsyncGenerator[MockIcomRadio, None]:
    s = MockIcomRadio(clock=virtual_clock)
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
async def client() -> AsyncGenerator[_UdpClient, None]:
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    yield proto
    proto.close()


# ---------------------------------------------------------------------------
# gap_lost_buffered: single missing number, resent, delayed answer
# ---------------------------------------------------------------------------


async def test_lost_tracked_packet_gets_single_requests_then_fill(
    server: MockIcomRadio, client: _UdpClient, virtual_clock: VirtualClock
) -> None:
    """Packet 133 never arrives: at the next tracked packet (134) the radio
    asks for 133 in single form, twice; the resent 133 is answered."""
    rows = _fixture_rows("gap_lost_buffered")
    script = _client_side(rows)
    # The file's client script: 130/131/132 answered, 134 opens the gap,
    # 133 refills it (the file's doubled resend is a capture of the
    # client's own retry and is replayed once here).
    assert script[:5] == [
        (130, "civ_data"),
        (131, "civ_data"),
        (132, "civ_data"),
        (134, "idle"),
        (133, "civ_data"),
    ]
    (req_kind, req_seq, _), *_ = _radio_requests(rows)
    assert (req_kind, req_seq) == ("retransmit_single", 133)

    await _open_stream(client, server, 129)
    for seq in (130, 131, 132):
        req = _freq_get(seq=seq)
        client.send(req, server.civ_port)
        assert _is_echo_of(await client.recv(), req)  # LAN echo
        assert _is_freq_reply(await client.recv())
    assert server.civ_gap_open is False
    assert server.civ_retransmit_requests == 0

    client.send(_idle(134), server.civ_port)
    pair = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("single", req_seq) for p in pair)
    assert server.civ_gap_open is True
    assert server.civ_retransmit_requests == 2

    # The resent 133 fills the gap: echo + reply, and the held idle 134
    # (an idle) stays silent.
    resend = _freq_get(seq=133)
    client.send(resend, server.civ_port)
    assert _is_echo_of(await client.recv(), resend)
    assert _is_freq_reply(await client.recv())
    assert server.civ_gap_open is False

    # Quiesce (a pre-fill round may still be in flight), then the request
    # loop must go silent and the counter must stop.
    await _collect(client, virtual_clock, 0.15)
    sent = server.civ_retransmit_requests
    assert await _collect(client, virtual_clock, 0.3) == []
    assert server.civ_retransmit_requests == sent


# ---------------------------------------------------------------------------
# gap_never_filled: ranges request, twice per round, stall, pings live
# ---------------------------------------------------------------------------


async def test_unfilled_gap_repeats_ranges_twice_per_round(
    server: MockIcomRadio, client: _UdpClient, virtual_clock: VirtualClock
) -> None:
    """Skipped 154-156: the radio repeats ``ranges [[154, 156]]`` twice per
    0.1 s round, answers no CI-V data, and still answers pings."""
    rows = _fixture_rows("gap_never_filled")
    script = _client_side(rows)
    # The file opens the window with an idle 153 and the gap-opening
    # CI-V DATA 157; every later client row is an idle.
    assert script[0] == (153, "idle")
    assert script[1] == (157, "civ_data")
    assert all(kind == "idle" for _, kind in script[2:])
    first_req = _radio_requests(rows)[0]
    assert first_req[0] == "retransmit_ranges"
    expected_ranges = [tuple(pair) for pair in first_req[2]]
    assert expected_ranges == [(154, 156)]
    assert all(
        (kind, seq) == ("retransmit_ranges", 0)
        and [tuple(pair) for pair in ranges] == expected_ranges
        for kind, seq, ranges in _radio_requests(rows)
    )

    await _open_stream(client, server, 152)
    client.send(_idle(153), server.civ_port)
    held = _freq_get(seq=157)
    client.send(held, server.civ_port)
    first = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("ranges", expected_ranges) for p in first)

    # A 0.55 s window holds the immediate pair plus the paced fallback
    # rounds of two (one 0.1 s loop tick per round): every packet is the
    # same ranges request, never the held echo.
    window = await _collect(client, virtual_clock, 0.55)
    assert 6 <= len(window) <= 12
    assert len(window) % 2 == 0
    assert all(_parse_retransmit(p) == ("ranges", expected_ranges) for p in window)
    assert all(p != held for p in window)
    assert server.civ_gap_open is True

    # Pings are answered while tracked-packet processing is stalled.
    await _collect(client, virtual_clock, 0.2)  # drain in-flight requests
    client.send(_ping(), server.civ_port)

    def _ping_reply() -> bytes | None:
        for pkt in _drain_nowait(client):
            if _is_ping_reply(pkt):
                return pkt
            # Only pre-ping fallback requests can still be in flight.
            assert _parse_retransmit(pkt) is not None
        return None

    assert (
        await await_until(
            _ping_reply, timeout=10.0, description="the ping reply while stalled"
        )
    ) is not None


# ---------------------------------------------------------------------------
# gap_filled_by_idle: fills release the held CI-V request in order
# ---------------------------------------------------------------------------


async def test_idle_fills_release_held_request_in_order(
    server: MockIcomRadio, client: _UdpClient, virtual_clock: VirtualClock
) -> None:
    """Skipped 131-133, then idle fills for 131/133 switch the request to
    single 132, and the idle carrying 132 releases the CI-V request held
    through the stall (answered after the fill)."""
    rows = _fixture_rows("gap_filled_by_idle")
    script = _client_side(rows)
    # The window's first client packet opens the gap; the last processed
    # number before it (130) is replayed as a preamble after the open.
    assert script[0] == (134, "idle")
    fill_1 = script.index((131, "idle"))
    assert script[fill_1 + 1] == (133, "idle")
    stall_idles = [seq for seq, kind in script[1:fill_1] if kind == "idle"]
    assert stall_idles == list(range(135, 143))
    held_seq = next(seq for seq, kind in script if kind == "civ_data")
    assert held_seq == 158
    req_rows = _radio_requests(rows)
    assert req_rows[0][2] == [[131, 133]]
    assert req_rows[0][0] == "retransmit_ranges"
    narrowed = next(r for r in req_rows if r[0] == "retransmit_single")
    assert narrowed[:2] == ("retransmit_single", 132)

    await _open_stream(client, server, 129)
    req130 = _freq_get(seq=130)
    client.send(req130, server.civ_port)
    assert _is_echo_of(await client.recv(), req130)
    assert _is_freq_reply(await client.recv())

    client.send(_idle(134), server.civ_port)
    pair = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("ranges", [(131, 133)]) for p in pair)

    # A CI-V request sent during the stall is held: no echo, no reply —
    # only the ranges request keeps arriving. The stall-time idles arrive
    # first (like the fixture's 135..178), so the later drain can walk the
    # whole held chain in order.
    for seq in range(135, 158):
        client.send(_idle(seq), server.civ_port)
    held = _freq_get(seq=held_seq)
    client.send(held, server.civ_port)
    window = await _collect(client, virtual_clock, 0.25)
    assert window
    assert all(_parse_retransmit(p) == ("ranges", [(131, 133)]) for p in window)
    assert server.civ_gap_open is True

    # Idle fills for 131 and 133: the request narrows to single 132.
    client.send(_idle(131), server.civ_port)
    client.send(_idle(133), server.civ_port)
    soon = await _collect(client, virtual_clock, 0.2)
    assert 2 <= len(soon) <= 8
    assert all(_parse_retransmit(p) == ("single", 132) for p in soon)

    # The idle carrying 132 closes the gap: the held 134 (idle, silent) and
    # 158 (CI-V DATA: echo + reply) are processed in sequence order.
    client.send(_idle(132), server.civ_port)
    got_echo = got_reply = False
    while not (got_echo and got_reply):
        pkt = await _next(client)
        if _is_echo_of(pkt, held):
            got_echo = True
        elif _is_freq_reply(pkt):
            got_reply = True
        else:
            assert _parse_retransmit(pkt) is not None  # pre-fill straggler
    assert server.civ_gap_open is False

    await _collect(client, virtual_clock, 0.15)
    sent = server.civ_retransmit_requests
    assert await _collect(client, virtual_clock, 0.3) == []
    assert server.civ_retransmit_requests == sent


# ---------------------------------------------------------------------------
# single_gap_filled_by_idle: one missing number via idle
# ---------------------------------------------------------------------------


async def test_single_gap_fill_resumes(server, client, virtual_clock) -> None:
    """Skipped 199 draws single requests; the idle carrying 199 closes the
    gap and later CI-V requests are answered normally."""
    rows = _fixture_rows("single_gap_filled_by_idle")
    script = _client_side(rows)
    assert script[0] == (198, "idle")
    assert script[1] == (200, "idle")
    fill_at = script.index((199, "idle"))
    assert fill_at > 1
    (req_kind, req_seq, _), *_ = _radio_requests(rows)
    assert (req_kind, req_seq) == ("retransmit_single", 199)

    await _open_stream(client, server, 197)
    client.send(_idle(198), server.civ_port)
    client.send(_idle(200), server.civ_port)
    pair = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("single", req_seq) for p in pair)
    assert server.civ_gap_open is True

    client.send(_idle(199), server.civ_port)
    await _collect(client, virtual_clock, 0.15)  # drain pre-fill stragglers
    assert server.civ_gap_open is False
    assert await _collect(client, virtual_clock, 0.3) == []

    req = _freq_get(seq=201)
    client.send(req, server.civ_port)
    assert _is_echo_of(await client.recv(), req)
    assert _is_freq_reply(await client.recv())


# ---------------------------------------------------------------------------
# MOR-3361: tracking survives OpenClose(open) within one radio session
# ---------------------------------------------------------------------------


async def test_reopened_stream_ignores_stale_numbers_until_passing_last(
    server: MockIcomRadio, client: _UdpClient, virtual_clock: VirtualClock
) -> None:
    """A re-sent OpenClose never restarts sequence tracking (MOR-3361).

    A raw client opens (consuming 0), sends five tracked packets, then
    re-sends OpenClose and restarts its numbering at 0. Every renumbered
    packet at or below the last processed number is ignored — no echo, no
    reply, no retransmit request — and answers resume only once the new
    numbering passes it.
    """
    await _open_stream(client, server, 0)
    last = 5
    for seq in range(1, last + 1):
        req = _freq_get(seq=seq)
        client.send(req, server.civ_port)
        assert _is_echo_of(await client.recv(), req)
        assert _is_freq_reply(await client.recv())
    assert server.civ_gap_open is False

    # The re-sent OpenClose keeps the stream alive (its marker may still
    # arrive) but must not touch the tracker's numbering.
    client.send(_openclose(), server.civ_port)
    await _collect(client, virtual_clock, 0.2)

    before = server.civ_retransmit_requests
    for seq in range(last + 1):
        client.send(_freq_get(seq=seq), server.civ_port)
    assert await _collect(client, virtual_clock, 0.35) == []
    assert server.civ_gap_open is False
    assert server.civ_retransmit_requests == before

    req = _freq_get(seq=last + 1)
    client.send(req, server.civ_port)
    assert _is_echo_of(await client.recv(), req)
    assert _is_freq_reply(await client.recv())
    assert server.civ_gap_open is False


# ---------------------------------------------------------------------------
# B1: a tracked OpenClose consumes its header sequence number
# ---------------------------------------------------------------------------


async def test_tracked_openclose_consumes_its_number(
    server: MockIcomRadio, client: _UdpClient, virtual_clock: VirtualClock
) -> None:
    """Data 1-3 answered, tracked OpenClose 4, data 5: no false gap.

    The production client sends OpenClose through ``send_tracked``, so the
    open at 4 is the expected number and data 5 follows it in order —
    ``civ_gap_open`` stays False and no request goes out. Before the fix
    the open never reached the tracker: 5 opened ``single 4`` and was
    held forever.
    """
    await _open_stream(client, server, 0)
    for seq in (1, 2, 3):
        req = _freq_get(seq=seq)
        client.send(req, server.civ_port)
        assert _is_echo_of(await client.recv(), req)
        assert _is_freq_reply(await client.recv())

    client.send(_openclose(seq=4), server.civ_port)
    assert _parse_retransmit(await client.recv()) is None  # stream marker
    assert server.civ_gap_open is False

    req = _freq_get(seq=5)
    client.send(req, server.civ_port)
    assert _is_echo_of(await client.recv(), req)
    assert _is_freq_reply(await client.recv())
    assert server.civ_gap_open is False

    await _collect(client, virtual_clock, 0.15)
    assert server.civ_retransmit_requests == 0
    assert await _collect(client, virtual_clock, 0.3) == []


async def test_production_client_nudge_opens_no_gap(
    virtual_clock: VirtualClock,
) -> None:
    """The CI-V watchdog's OpenClose nudge takes a tracked number and the
    simulator consumes it: ``civ_gap_open`` is False afterwards and no
    retransmit request goes out."""
    sim = MockIcomRadio(clock=virtual_clock)
    await sim.start()
    try:
        radio = IcomRadio(
            host="127.0.0.1",
            port=sim.control_port,
            username="testuser",
            password="testpass",
            model="IC-7610",
        )
        with fast_connect():
            await _drive(virtual_clock, "connect", radio.connect(), 60.0)
        assert radio.connected
        assert (
            await _drive(virtual_clock, "get_freq", radio.get_freq(), 30.0)
            == 14_074_000
        )
        assert sim.civ_gap_open is False

        sent = sim.civ_retransmit_requests
        await _drive(
            virtual_clock,
            "nudge",
            radio._send_open_close(open_stream=True),
            30.0,
        )
        # More tracked traffic after the nudge: pre-fix this data packet
        # opened a gap for the nudge's ignored number and was held.
        assert (
            await _drive(virtual_clock, "get_freq", radio.get_freq(), 30.0)
            == 14_074_000
        )
        await virtual_clock.elapse(0.4)  # let any timer-driven round arrive
        assert sim.civ_gap_open is False
        assert sim.civ_retransmit_requests == sent
        await _drive(virtual_clock, "disconnect", radio.disconnect(), 30.0)
    finally:
        await sim.stop()


# ---------------------------------------------------------------------------
# B2: the last missing number arriving ahead still releases the stall
# ---------------------------------------------------------------------------


async def test_out_of_order_fills_release_held_request(
    server: MockIcomRadio, client: _UdpClient, virtual_clock: VirtualClock
) -> None:
    """Gap 134-136 filled as 135, 134, 136: the held CI-V request is
    answered in order and no new request goes out afterwards.

    The same shape as the reported 131-133 / 132-131-133 probe, shifted
    past a 131-133 preamble so the gap opens identically before and
    after the OpenClose-numbering fix.
    """
    await _open_stream(client, server, 130)
    for seq in (131, 132, 133):
        req = _freq_get(seq=seq)
        client.send(req, server.civ_port)
        assert _is_echo_of(await client.recv(), req)
        assert _is_freq_reply(await client.recv())
    opener = _idle(137)
    client.send(opener, server.civ_port)
    pair = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("ranges", [(134, 136)]) for p in pair)

    held = _freq_get(seq=138)
    client.send(held, server.civ_port)
    await _collect(client, virtual_clock, 0.15)  # the round answering the held packet
    assert server.civ_gap_open is True

    # Out-of-order fills: 135 arrives ahead, 134 in order, 136 last —
    # and still ahead of the expected number.
    client.send(_idle(135), server.civ_port)
    client.send(_idle(134), server.civ_port)
    client.send(_idle(136), server.civ_port)
    got_echo = got_reply = False
    while not (got_echo and got_reply):
        pkt = await _next(client)
        if _is_echo_of(pkt, held):
            got_echo = True
        elif _is_freq_reply(pkt):
            got_reply = True
        else:
            assert _parse_retransmit(pkt) is not None  # pre-fill straggler
    assert server.civ_gap_open is False

    # After the fill the request loop goes silent: no re-marked gap for
    # numbers already received.
    await _collect(client, virtual_clock, 0.15)
    sent = server.civ_retransmit_requests
    assert await _collect(client, virtual_clock, 0.3) == []
    assert server.civ_retransmit_requests == sent


# ---------------------------------------------------------------------------
# B3: every tracker-restart site serves restarted numbering from 0
# ---------------------------------------------------------------------------


async def test_reboot_clears_gap_tracking(
    server: MockIcomRadio, client: _UdpClient, virtual_clock: VirtualClock
) -> None:
    """After a reboot a client numbering from 0 is served.

    No login or new claim happens between the gap and the reboot, so the
    cleared gap below is the reboot's own clear. Without it the old
    numbering lingers and the restarted stream is ignored.
    """
    await _open_stream(client, server, 0)
    for seq in (1, 2, 3):
        req = _freq_get(seq=seq)
        client.send(req, server.civ_port)
        assert _is_echo_of(await client.recv(), req)
        assert _is_freq_reply(await client.recv())
    client.send(_idle(5), server.civ_port)
    pair = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("single", 4) for p in pair)
    assert server.civ_gap_open is True

    server.reboot(at=0, down_for=0, busy_for=0)
    await virtual_clock.elapse(0.1)
    assert server.civ_gap_open is False
    await _collect(client, virtual_clock, 0.2)  # drain pre-reboot requests in flight

    # Fresh login after the reboot: numbering from 0 is served.
    client.send(_login(), server.control_port)
    await client.recv()
    client.send(_conninfo(), server.control_port)
    await client.recv()
    await _open_stream(client, server, 0)
    req = _freq_get(seq=1)
    client.send(req, server.civ_port)
    assert _is_echo_of(await client.recv(), req)
    assert _is_freq_reply(await client.recv())
    assert server.civ_gap_open is False


async def test_drop_session_clears_gap_tracking(
    server: MockIcomRadio, client: _UdpClient, virtual_clock: VirtualClock
) -> None:
    """After ``drop_session`` a client numbering from 0 is served.

    No login or new claim happens between the gap and the drop, so the
    cleared gap below is the drop's own clear. Without it the old
    numbering lingers and the restarted stream is ignored.
    """
    await _open_stream(client, server, 0)
    for seq in (1, 2, 3):
        req = _freq_get(seq=seq)
        client.send(req, server.civ_port)
        assert _is_echo_of(await client.recv(), req)
        assert _is_freq_reply(await client.recv())
    client.send(_idle(5), server.civ_port)
    pair = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("single", 4) for p in pair)
    assert server.civ_gap_open is True

    server.drop_session(at=0, notice=False)
    await virtual_clock.elapse(0.1)
    assert server.civ_gap_open is False
    await _collect(client, virtual_clock, 0.2)  # drain pre-drop requests in flight

    # Fresh login after the drop: numbering from 0 is served.
    client.send(_login(), server.control_port)
    await client.recv()
    client.send(_conninfo(), server.control_port)
    await client.recv()
    await _open_stream(client, server, 0)
    req = _freq_get(seq=1)
    client.send(req, server.civ_port)
    assert _is_echo_of(await client.recv(), req)
    assert _is_freq_reply(await client.recv())
    assert server.civ_gap_open is False


async def test_new_claim_clears_gap_tracking(
    virtual_clock: VirtualClock,
) -> None:
    """After a conninfo claims a new session, numbering from 0 is served.

    No login happens anywhere here, so the cleared tracking below is the
    new claim's own clear. Without it the previous session's numbering
    lingers and the restarted stream is ignored.
    """
    sim = MockIcomRadio(single_owner=True, clock=virtual_clock)
    await sim.start()
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    try:
        # A first session claims, opens (consuming 0) and numbers 1-3.
        proto.send(_conninfo(), sim.control_port)
        await proto.recv()
        await _open_stream(proto, sim, 0)
        for seq in (1, 2, 3):
            req = _freq_get(seq=seq)
            proto.send(req, sim.civ_port)
            assert _is_echo_of(await proto.recv(), req)
            assert _is_freq_reply(await proto.recv())

        # The session is released (close, no tracking clear) and a new
        # conninfo claims a new session: past numbering must not apply.
        close = bytearray(_openclose())
        close[0x15] = 0x00
        proto.send(bytes(close), sim.civ_port)
        # The close and the conninfo go to different sockets, so delivery
        # does not order them: a close handled after the new claim would
        # open a gap in the new session's numbering. Send the conninfo
        # only once the close has released the session.
        released = await asyncio.wait_for(sim.wait_session_released(), 5.0)
        # A client end (never a hold expiry).
        assert released in MockIcomRadio.CLIENT_END_REASONS, released
        proto.send(_conninfo(), sim.control_port)
        await proto.recv()

        # Numbering from 0 is served on the new session.
        await _open_stream(proto, sim, 0)
        req = _freq_get(seq=1)
        proto.send(req, sim.civ_port)
        assert _is_echo_of(await proto.recv(), req)
        assert _is_freq_reply(await proto.recv())
        assert sim.civ_gap_open is False
    finally:
        proto.close()
        await sim.stop()


# ---------------------------------------------------------------------------
# B1: paced rounds match the recorded rate, per window
# ---------------------------------------------------------------------------


def _close(seq: int) -> bytes:
    """Tracked OpenClose(close): same header as the open, close magic."""
    pkt = bytearray(_openclose(seq=seq))
    pkt[0x15] = 0x00
    return bytes(pkt)


async def _paced_sim(virtual_clock: VirtualClock) -> MockIcomRadio:
    """A started simulator whose rounds answer only to ``virtual_clock``.

    Stops the silent-client repeat loop, so every round below is
    packet-triggered and stamped with ``virtual_clock``. The fallback itself
    stays pinned by
    ``test_unfilled_gap_repeats_ranges_twice_per_round``. The caller
    must ``await sim.stop()``.
    """
    sim = MockIcomRadio(clock=virtual_clock)
    await sim.start()
    loop_task = sim._civ_gap_task
    sim._civ_gap_task = None
    if loop_task is not None:
        loop_task.cancel()
        try:
            await loop_task
        except (asyncio.CancelledError, Exception):
            pass
    return sim


async def _replay_rounds(
    window: str, virtual_clock: VirtualClock
) -> tuple[int, list[float]]:
    """Drive one window's recorded c2r timeline against a fresh simulator.

    Returns the recorded round count (one per r2c retransmit row) and the
    simulator-clock time of each observed round. Client packets are fed
    through the simulator's synchronous entry point at their recorded
    offsets on the shared virtual clock, so round spacing is measured on
    the simulator's clock, never wall time. Every radio-to-client request
    still travels the real loopback socket and is classified from the wire.
    """
    rows = _fixture_rows(window)
    script = [
        (row["t"], row["seq"], row["kind"])
        for row in rows
        if row["dir"] == "c2r"
        for _ in range(row.get("repeat", 1))
    ]
    recorded = sum(
        1
        for row in rows
        if row["dir"] == "r2c" and row["kind"].startswith("retransmit")
    )
    sim = await _paced_sim(virtual_clock)
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    wire: list[bytes] = []
    try:
        pre = window == "gap_filled_by_idle"
        await _open_stream(proto, sim, 129 if pre else script[0][1] - 1)
        if pre:  # the window's last processed number before the gap opens
            req = _freq_get(seq=130)
            proto.send(req, sim.civ_port)
            assert _is_echo_of(await proto.recv(), req)
            assert _is_freq_reply(await proto.recv())
        base = script[0][0]
        prev_offset = 0.0
        starts: list[float] = []
        for stamp, seq, kind in script:
            # The simulator processes this packet inline at the step's
            # virtual time, so an observed round is stamped here.
            offset = stamp - base
            virtual_clock.advance(offset - prev_offset)
            prev_offset = offset
            before = sim.civ_retransmit_requests
            _inject(sim, proto, _idle(seq) if kind == "idle" else _freq_get(seq=seq))
            delta = sim.civ_retransmit_requests - before
            assert delta % 2 == 0, (window, delta)
            starts.extend([virtual_clock()] * (delta // 2))

        # The requests above travelled the real loopback socket: gather them
        # from the wire and cross-check the counter.
        def _rounds_done() -> bool:
            wire.extend(_drain_nowait(proto))
            return (
                sum(1 for pkt in wire if _parse_retransmit(pkt) is not None)
                == sim.civ_retransmit_requests
            )

        await await_until(
            _rounds_done,
            timeout=10.0,
            description=f"{window} retransmit requests on the wire",
        )
    finally:
        proto.close()
        await sim.stop()
    retransmits = sum(1 for pkt in wire if _parse_retransmit(pkt) is not None)
    assert len(starts) * 2 == retransmits, (window, retransmits)
    return recorded, starts


async def test_round_rate_matches_recording(
    virtual_clock: VirtualClock,
) -> None:
    """Each window's client timeline draws about its recorded rounds.

    Per window the observed round count is within ±20 % of the recorded
    one and no two rounds are closer than 70 ms — both measured on the
    simulator's shared virtual clock, so a loaded runner cannot skew
    them. The tail pins row 143 @1.005 of gap_filled_by_idle: fills 131/133
    narrow the request to single 132: a packet 3 ms after that round
    draws no round, one 53 ms after it is still silent, while the next
    one past the pacing window draws a round.
    """
    for window in (
        "gap_lost_buffered",
        "gap_never_filled",
        "single_gap_filled_by_idle",
        "gap_filled_by_idle",
    ):
        recorded, starts = await _replay_rounds(window, virtual_clock)
        assert abs(len(starts) - recorded) * 5 <= recorded, (window, len(starts))
        if len(starts) >= 2:
            gaps = [after - before for before, after in zip(starts, starts[1:])]
            assert min(gaps) >= 0.070, (window, min(gaps))

    sim = await _paced_sim(virtual_clock)
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    try:
        await _open_stream(proto, sim, 129)
        req = _freq_get(seq=130)
        proto.send(req, sim.civ_port)
        assert _is_echo_of(await proto.recv(), req)
        assert _is_freq_reply(await proto.recv())
        _inject(sim, proto, _idle(134))
        pair = await _take(proto, 2, "the ranges round for 131-133")
        assert all(_parse_retransmit(p) == ("ranges", [(131, 133)]) for p in pair)
        virtual_clock.advance(0.09)
        _inject(sim, proto, _idle(131))  # in order: fills, no round
        _inject(sim, proto, _idle(133))  # narrows to single 132: round
        pair = await _take(proto, 2, "the narrowed single round for 132")
        assert all(_parse_retransmit(p) == ("single", 132) for p in pair)
        sent = sim.civ_retransmit_requests
        virtual_clock.advance(0.003)
        _inject(sim, proto, _idle(135))  # ~3 ms after the round: silence
        assert await _collect(proto, virtual_clock, 0.0) == []
        assert sim.civ_retransmit_requests == sent
        virtual_clock.advance(0.05)  # ~53 ms after the round: still silent
        _inject(sim, proto, _idle(136))
        assert await _collect(proto, virtual_clock, 0.0) == []
        assert sim.civ_retransmit_requests == sent
        virtual_clock.advance(0.1)
        _inject(sim, proto, _idle(137))
        pair = await _take(proto, 2, "the round past the pacing window")
        assert all(_parse_retransmit(p) == ("single", 132) for p in pair)
    finally:
        proto.close()
        await sim.stop()


async def test_tracked_openclose_close_consumes_its_number(
    server: MockIcomRadio, client: _UdpClient, virtual_clock: VirtualClock
) -> None:
    """Data 1-3 answered, tracked OpenClose(close) 4, data 5: answered.

    The close consumes its header number like any tracked packet, so 5
    arrives in order: no gap, no request. Without the gate 5 opens
    single 4 and is held forever.
    """
    await _open_stream(client, server, 0)
    for seq in (1, 2, 3):
        req = _freq_get(seq=seq)
        client.send(req, server.civ_port)
        assert _is_echo_of(await client.recv(), req)
        assert _is_freq_reply(await client.recv())
    client.send(_close(4), server.civ_port)
    req = _freq_get(seq=5)
    client.send(req, server.civ_port)
    assert _is_echo_of(await client.recv(), req)
    assert _is_freq_reply(await client.recv())
    assert server.civ_gap_open is False
    await _collect(client, virtual_clock, 0.15)
    assert server.civ_retransmit_requests == 0
    assert await _collect(client, virtual_clock, 0.3) == []


async def test_new_gap_never_remarks_held_numbers(
    server: MockIcomRadio, client: _UdpClient, virtual_clock: VirtualClock
) -> None:
    """Idle 141 after fills 131-133 requests only (135,139): 140 is held.

    Open 129, data 130, idle 134 opens ranges [[131,133]], idle 140 is
    held; once 131-133 fill in order, 141 opens a new gap that must skip
    the still-held 140. Before the fix the radio asked for (135,140).
    """
    await _open_stream(client, server, 129)
    req = _freq_get(seq=130)
    client.send(req, server.civ_port)
    assert _is_echo_of(await client.recv(), req)
    assert _is_freq_reply(await client.recv())
    client.send(_idle(134), server.civ_port)
    pair = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("ranges", [(131, 133)]) for p in pair)
    await virtual_clock.elapse(0.09)
    client.send(_idle(140), server.civ_port)
    pair = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("ranges", [(131, 133)]) for p in pair)
    for seq in (131, 132, 133):
        client.send(_idle(seq), server.civ_port)
    await virtual_clock.elapse(0.09)
    client.send(_idle(141), server.civ_port)
    pair = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("ranges", [(135, 139)]) for p in pair)
    assert server.civ_gap_open is True


# ---------------------------------------------------------------------------
# End to end: a dropped CI-V DATA packet is resent from the client buffer
# ---------------------------------------------------------------------------


async def test_dropped_civ_data_is_resent_from_buffer_and_read_completes() -> None:
    """With one client→radio CI-V packet lost, the simulator's retransmit
    request makes the production client resend from its buffer: the read
    completes (only the exact missing number unblocks the held packets)
    and the gap closes."""
    sim = MockIcomRadio()
    await sim.start()
    try:
        radio = IcomRadio(
            host="127.0.0.1",
            port=sim.control_port,
            username="testuser",
            password="testpass",
            model="IC-7610",
        )
        with fast_connect():
            await radio.connect()
        assert radio.connected
        plan = FaultPlan(civ_from_client=FaultRule(drop_probability=1.0, max_packets=1))
        sim.fault_plan = plan
        assert await asyncio.wait_for(radio.get_freq(), timeout=10.0) == 14_074_000
        assert plan.civ_from_client.dropped == 1
        assert sim.civ_retransmit_requests >= 2
        assert sim.civ_gap_open is False
        await radio.disconnect()
    finally:
        await sim.stop()
