"""Wire-level tests: the simulator's recorded CI-V sequence-gap behaviour.

A bare asyncio datagram client replays the client side of each window of
``tests/fixtures/ic7610/retransmit_gaps.jsonl`` (recorded real IC-7610, LAN
CI-V port, 2026-10-12) against a real ``MockIcomRadio`` and pins the wire
facts: a packet ahead of the expected tracked number opens a gap, later
packets are held, and while any number is missing the radio sends the
retransmit request twice every 0.1 s — the single form (0x10-byte
type-0x01, header seq = the missing number) when exactly one number is
missing, otherwise one ranges packet (header seq 0, then little-endian u16
``[first, last]`` pairs, one per contiguous missing run). A tracked packet
carrying a missing number (any tracked kind, idle included) fills it; once
nothing is missing the held packets are processed in sequence order, so a
CI-V request held through the stall is answered after the fill and no CI-V
answer goes out while the gap is open. Pings are answered throughout.

Tracked kinds are idle (0x10 type-0), OpenClose (0x16) and CI-V DATA —
never pings, discovery or retransmit requests. Tracking starts at the
first tracked packet after OpenClose(open) and belongs to the radio
session: a later OpenClose(open) never restarts it, so a number at or
below the last processed one is ignored (recorded real IC-7610,
MOR-3361). Tracking restarts only on a new session (an accepted login
or conninfo claiming a new session) or after ``drop_session`` / reboot.
"""

from __future__ import annotations

import asyncio
import struct
import time

import pytest

from rigplane.radio import IcomRadio

from rigplane_testkit.icom import FaultPlan, FaultRule, MockIcomRadio

from _perf_helpers import fast_connect
from testkit.test_simulator_faults import (
    _UdpClient,
    _freq_get,
    _header,
    _is_freq_reply,
    _openclose,
    _ping,
)

_HOST = "127.0.0.1"

_PT_DATA = 0x00
_PT_RETRANSMIT = 0x01
_PT_PING = 0x07


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


async def _collect(client: _UdpClient, duration: float) -> list[bytes]:
    """Gather every packet arriving within ``duration`` seconds."""
    out: list[bytes] = []
    end = time.monotonic() + duration
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return out
        try:
            out.append(await client.recv(remaining))
        except TimeoutError:
            return out


async def _open_stream(client: _UdpClient, server: MockIcomRadio) -> None:
    """Open the CI-V stream and swallow the stream-active marker."""
    client.send(_openclose(), server.civ_port)
    await client.recv()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def server():
    s = MockIcomRadio()
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
async def client():
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    yield proto
    proto.close()


# ---------------------------------------------------------------------------
# gap_lost_buffered: single missing number, resent, delayed answer
# ---------------------------------------------------------------------------


async def test_lost_tracked_packet_gets_single_requests_then_fill(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """Packet 133 never arrives: at the next tracked packet (134) the radio
    asks for 133 in single form, twice; the resent 133 is answered."""
    await _open_stream(client, server)
    for seq in (130, 131, 132):
        req = _freq_get(seq=seq)
        client.send(req, server.civ_port)
        assert await client.recv() == req  # LAN echo
        assert _is_freq_reply(await client.recv())
    assert server.civ_gap_open is False
    assert server.civ_retransmit_requests == 0

    client.send(_idle(134), server.civ_port)
    pair = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("single", 133) for p in pair)
    assert server.civ_gap_open is True
    assert server.civ_retransmit_requests == 2

    # The resent 133 fills the gap: echo + reply, and the held idle 134
    # (an idle) stays silent.
    resend = _freq_get(seq=133)
    client.send(resend, server.civ_port)
    assert await client.recv() == resend
    assert _is_freq_reply(await client.recv())
    assert server.civ_gap_open is False

    # Quiesce (a pre-fill round may still be in flight), then the request
    # loop must go silent and the counter must stop.
    await _collect(client, 0.15)
    sent = server.civ_retransmit_requests
    assert await _collect(client, 0.3) == []
    assert server.civ_retransmit_requests == sent


# ---------------------------------------------------------------------------
# gap_never_filled: ranges request, twice per round, stall, pings live
# ---------------------------------------------------------------------------


async def test_unfilled_gap_repeats_ranges_twice_per_round(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """Skipped 154-156: the radio repeats ``ranges [[154, 156]]`` twice per
    0.1 s round, answers no CI-V data, and still answers pings."""
    await _open_stream(client, server)
    client.send(_idle(153), server.civ_port)
    held = _freq_get(seq=157)
    client.send(held, server.civ_port)
    first = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("ranges", [(154, 156)]) for p in first)

    # A 0.55 s window holds the immediate pair plus ~5 rounds of two: every
    # packet is the same ranges request, never the held request's echo.
    window = await _collect(client, 0.55)
    assert 10 <= len(window) <= 14
    assert len(window) % 2 == 0
    assert all(_parse_retransmit(p) == ("ranges", [(154, 156)]) for p in window)
    assert all(p != held for p in window)
    assert server.civ_gap_open is True

    # Pings are answered while tracked-packet processing is stalled.
    await _collect(client, 0.2)  # drain in-flight requests
    client.send(_ping(), server.civ_port)
    answered = False
    for _ in range(20):
        pkt = await client.recv(timeout=2.0)
        if (
            len(pkt) == 0x15
            and struct.unpack_from("<H", pkt, 4)[0] == _PT_PING
            and pkt[0x10] == 0x01
        ):
            answered = True
            break
        assert _parse_retransmit(pkt) is not None  # only requests otherwise
    assert answered


# ---------------------------------------------------------------------------
# gap_filled_by_idle: fills release the held CI-V request in order
# ---------------------------------------------------------------------------


async def test_idle_fills_release_held_request_in_order(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """Skipped 131-133, then idle fills for 131/133 switch the request to
    single 132, and the idle carrying 132 releases the CI-V request held
    through the stall (answered after the fill)."""
    await _open_stream(client, server)
    req130 = _freq_get(seq=130)
    client.send(req130, server.civ_port)
    assert await client.recv() == req130
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
    held = _freq_get(seq=158)
    client.send(held, server.civ_port)
    window = await _collect(client, 0.25)
    assert window
    assert all(_parse_retransmit(p) == ("ranges", [(131, 133)]) for p in window)
    assert server.civ_gap_open is True

    # Idle fills for 131 and 133: the request narrows to single 132.
    client.send(_idle(131), server.civ_port)
    client.send(_idle(133), server.civ_port)
    soon = await _collect(client, 0.3)
    assert 2 <= len(soon) <= 8
    assert all(_parse_retransmit(p) == ("single", 132) for p in soon)

    # The idle carrying 132 closes the gap: the held 134 (idle, silent) and
    # 158 (CI-V DATA: echo + reply) are processed in sequence order.
    client.send(_idle(132), server.civ_port)
    got_echo = got_reply = False
    deadline = time.monotonic() + 2.0
    while not (got_echo and got_reply):
        pkt = await client.recv(timeout=max(0.1, deadline - time.monotonic()))
        if pkt == held:
            got_echo = True
        elif _is_freq_reply(pkt):
            got_reply = True
        else:
            assert _parse_retransmit(pkt) is not None  # pre-fill straggler
    assert server.civ_gap_open is False

    await _collect(client, 0.15)
    sent = server.civ_retransmit_requests
    assert await _collect(client, 0.3) == []
    assert server.civ_retransmit_requests == sent


# ---------------------------------------------------------------------------
# single_gap_filled_by_idle: one missing number via idle
# ---------------------------------------------------------------------------


async def test_single_gap_fill_resumes(server, client) -> None:
    """Skipped 199 draws single requests; the idle carrying 199 closes the
    gap and later CI-V requests are answered normally."""
    await _open_stream(client, server)
    client.send(_idle(198), server.civ_port)
    client.send(_idle(200), server.civ_port)
    pair = [await client.recv() for _ in range(2)]
    assert all(_parse_retransmit(p) == ("single", 199) for p in pair)
    assert server.civ_gap_open is True

    client.send(_idle(199), server.civ_port)
    await _collect(client, 0.15)  # drain pre-fill stragglers
    assert server.civ_gap_open is False
    assert await _collect(client, 0.3) == []

    req = _freq_get(seq=201)
    client.send(req, server.civ_port)
    assert await client.recv() == req
    assert _is_freq_reply(await client.recv())


# ---------------------------------------------------------------------------
# MOR-3361: tracking survives OpenClose(open) within one radio session
# ---------------------------------------------------------------------------


async def test_reopened_stream_ignores_stale_numbers_until_passing_last(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """A re-sent OpenClose never restarts sequence tracking (MOR-3361).

    A raw client opens, sends five tracked packets, then re-sends
    OpenClose and restarts its numbering at 0. Every renumbered packet
    at or below the last processed number is ignored — no echo, no
    reply, no retransmit request — and answers resume only once the new
    numbering passes it.
    """
    await _open_stream(client, server)
    last = 5
    for seq in range(last):
        req = _freq_get(seq=seq)
        client.send(req, server.civ_port)
        assert await client.recv() == req
        assert _is_freq_reply(await client.recv())
    assert server.civ_gap_open is False

    # The re-sent OpenClose keeps the stream alive (its marker may still
    # arrive) but must not touch the tracker's numbering.
    client.send(_openclose(), server.civ_port)
    await _collect(client, 0.2)

    before = server.civ_retransmit_requests
    for seq in range(last):
        client.send(_freq_get(seq=seq), server.civ_port)
    assert await _collect(client, 0.35) == []
    assert server.civ_gap_open is False
    assert server.civ_retransmit_requests == before

    req = _freq_get(seq=last)
    client.send(req, server.civ_port)
    assert await client.recv() == req
    assert _is_freq_reply(await client.recv())
    assert server.civ_gap_open is False


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
