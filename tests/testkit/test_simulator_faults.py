"""Wire-level tests: real MockIcomRadio + a plain UDP client.

These tests start the actual simulator on ephemeral loopback ports and talk
to it with a bare asyncio datagram client — no production code involved —
so drop/duplicate/corrupt/truncate/reorder and the reboot/keepalive-silence
events are observed on the wire, exactly as a real client would see them.
"""

import asyncio
import struct
import time

import pytest

from rigplane_testkit.icom import FaultPlan, FaultRule, MockIcomRadio

_HOST = "127.0.0.1"
_CLIENT_ID = 0x11223344

# Packet type codes (mirrors of the wire constants the simulator speaks).
_PT_DATA = 0x00
_PT_ARE_YOU_THERE = 0x03
_PT_I_AM_HERE = 0x04
_PT_PING = 0x07

_ERR_PREV_SESSION_ACTIVE = 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Plain UDP client
# ---------------------------------------------------------------------------


class _UdpClient(asyncio.DatagramProtocol):
    """Bare datagram client: everything received lands in ``queue``."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.queue.put_nowait(data)

    def send(self, data: bytes, port: int) -> None:
        assert self.transport is not None
        self.transport.sendto(data, (_HOST, port))

    async def recv(self, timeout: float = 2.0) -> bytes:
        return await asyncio.wait_for(self.queue.get(), timeout)

    async def expect_silence(self, duration: float = 0.3) -> None:
        try:
            data = await asyncio.wait_for(self.queue.get(), duration)
        except TimeoutError:
            return
        raise AssertionError(f"expected silence, received {len(data)} bytes")

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None


# ---------------------------------------------------------------------------
# Minimal wire packet builders (client side of the Icom LAN protocol)
# ---------------------------------------------------------------------------


def _header(size: int, ptype: int, seq: int = 0) -> bytearray:
    pkt = bytearray(size)
    struct.pack_into("<I", pkt, 0x00, size)
    struct.pack_into("<H", pkt, 0x04, ptype)
    struct.pack_into("<H", pkt, 0x06, seq)
    struct.pack_into("<I", pkt, 0x08, _CLIENT_ID)
    return pkt


def _ayt() -> bytes:
    return bytes(_header(0x10, _PT_ARE_YOU_THERE))


def _ping() -> bytes:
    pkt = _header(0x15, _PT_PING, seq=7)
    pkt[0x10] = 0x00  # request flag
    pkt[0x11:0x15] = b"\x01\x02\x03\x04"  # timestamp to echo
    return bytes(pkt)


def _login() -> bytes:
    pkt = _header(0x80, _PT_DATA)
    struct.pack_into("<H", pkt, 0x1A, 0x0001)  # token request
    return bytes(pkt)


def _conninfo(token: int = 0) -> bytes:
    pkt = _header(0x90, _PT_DATA)
    pkt[0x15] = 0x03  # requesttype: client conninfo
    struct.pack_into("<I", pkt, 0x1C, token)
    return bytes(pkt)


def _renew() -> bytes:
    pkt = _header(0x40, _PT_DATA)
    pkt[0x15] = 0x05  # requesttype: token renew
    return bytes(pkt)


def _freq_get(seq: int = 1) -> bytes:
    """CI-V 'get frequency' (0x03) wrapped in a DATA header for the civ port."""
    frame = b"\xfe\xfe\x98\xe0\x03\xfd"
    pkt = _header(0x15 + len(frame), _PT_DATA, seq=seq)
    pkt[0x10] = 0xC1
    struct.pack_into("<H", pkt, 0x11, len(frame))
    pkt[0x15:] = frame
    return bytes(pkt)


def _openclose() -> bytes:
    """OpenClose(open) on the CI-V port — starts the client's data stream."""
    pkt = _header(0x16, _PT_DATA)
    pkt[0x15] = 0x04  # open_stream
    return bytes(pkt)


def _is_freq_reply(pkt: bytes) -> bool:
    return len(pkt) > 0x1A and pkt[0x15:0x17] == b"\xfe\xfe" and pkt[0x19] == 0x03


async def _recv_reply(
    client: _UdpClient, request: bytes, timeout: float = 2.0
) -> bytes:
    """Read the radio's reply to ``request``, skipping its LAN CI-V echo.

    ``MockIcomRadio`` echoes every CI-V request packet back byte-identical
    immediately before the reply, like the recorded IC-7610 on the LAN CI-V
    port; raw-UDP tests that want "the reply" skip that echo here.
    """
    while True:
        pkt = await client.recv(timeout)
        if pkt != request:
            return pkt


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
# Baseline
# ---------------------------------------------------------------------------


async def test_no_plan_behaves_as_before(server, client):
    request = _freq_get()
    client.send(request, server.civ_port)
    reply = await _recv_reply(client, request)
    assert _is_freq_reply(reply)


async def test_echo_precedes_every_reply_by_default(server, client):
    """The LAN CI-V echo: the request packet comes back byte-identical
    first, the reply second (recorded IC-7610 behaviour; the conformance
    suite replays all recorded requests against this)."""
    request = _freq_get()
    client.send(request, server.civ_port)
    assert await client.recv() == request
    assert _is_freq_reply(await client.recv())

    server.lan_civ_echo = False
    client.send(request, server.civ_port)
    assert _is_freq_reply(await client.recv())


# ---------------------------------------------------------------------------
# Fault plan on the wire (radio → client unless noted)
# ---------------------------------------------------------------------------


async def test_drop_bounded_to_next_packet(server, client):
    """'Lose the next civ reply': the first answer vanishes, the second lands."""
    server.lan_civ_echo = False  # drop the reply itself, not its echo
    server.fault_plan = FaultPlan(
        civ_to_client=FaultRule(drop_probability=1.0, max_packets=1)
    )
    client.send(_freq_get(), server.civ_port)
    await client.expect_silence(0.3)
    client.send(_freq_get(), server.civ_port)
    assert _is_freq_reply(await client.recv())
    assert server.fault_plan.civ_to_client.dropped == 1


async def test_duplicate_sends_two_identical_copies(server, client):
    server.lan_civ_echo = False  # duplicate the reply itself, not its echo
    server.fault_plan = FaultPlan(
        civ_to_client=FaultRule(duplicate_probability=1.0, duplicate_delay_s=0.05)
    )
    request = _freq_get()
    client.send(request, server.civ_port)
    first = await _recv_reply(client, request)
    second = await client.recv()
    assert _is_freq_reply(first)
    assert first == second


async def test_corrupt_flips_bytes_on_the_wire(server, client):
    # Flip every byte of the reply (corrupt_bytes above the packet length):
    # the length header no longer matches the datagram size.
    server.lan_civ_echo = False  # corrupt the reply itself, not its echo
    server.fault_plan = FaultPlan(
        civ_to_client=FaultRule(corrupt_probability=1.0, corrupt_bytes=64)
    )
    client.send(_freq_get(), server.civ_port)
    reply = await client.recv()
    assert struct.unpack_from("<I", reply, 0)[0] != len(reply)


async def test_truncate_cuts_reply_on_the_wire(server, client):
    server.lan_civ_echo = False  # truncate the reply itself, not its echo
    server.fault_plan = FaultPlan(
        civ_to_client=FaultRule(truncate_probability=1.0, truncate_to=6)
    )
    client.send(_freq_get(), server.civ_port)
    reply = await client.recv()
    assert len(reply) == 6


async def test_reorder_permutes_replies(server, client):
    server.lan_civ_echo = False  # permute three replies, not echo+reply pairs
    server.fault_plan = FaultPlan(civ_to_client=FaultRule(reorder_window=3))
    for seq in (1, 2, 3):
        client.send(_freq_get(seq=seq), server.civ_port)
    replies = [await client.recv() for _ in range(3)]
    seqs = [struct.unpack_from("<H", r, 0x06)[0] for r in replies]
    assert sorted(seqs) == [1, 2, 3]  # nothing lost
    # and permuted: the per-stream RNG (seed 0xC0FFEE, civ:to_client) shuffles
    # [1, 2, 3] to [3, 2, 1]
    assert seqs != [1, 2, 3]


@pytest.mark.parametrize("direction", ["to_client", "from_client"])
async def test_reorder_flush_releases_held_burst_on_idle(server, client, direction):
    """A single request can never fill a reorder_window=4 rule: the reply
    only arrives because MockIcomRadio._arm_reorder_flush fires the idle
    flush after FaultRule.reorder_flush_s seconds."""
    rule = FaultRule(reorder_window=4, reorder_flush_s=0.05)
    server.fault_plan = FaultPlan(**{f"civ_{direction}": rule})
    request = _freq_get()
    client.send(request, server.civ_port)
    assert _is_freq_reply(await _recv_reply(client, request, timeout=1.0))
    assert rule.held == 0


async def test_inbound_drop_silences_the_server(server, client):
    """client → radio direction: dropped requests get no answer at all."""
    server.fault_plan = FaultPlan(control_from_client=FaultRule(drop_probability=1.0))
    client.send(_ayt(), server.control_port)
    await client.expect_silence(0.3)
    # Clearing the plan restores normal behaviour.
    server.fault_plan = None
    client.send(_ayt(), server.control_port)
    reply = await client.recv()
    assert struct.unpack_from("<H", reply, 4)[0] == _PT_I_AM_HERE


# ---------------------------------------------------------------------------
# Scheduled events
# ---------------------------------------------------------------------------


async def test_reboot_drops_session_then_busy_then_recovers(server, client):
    # Phase 1 — clean login and conninfo before the reboot.
    client.send(_login(), server.control_port)
    login1 = await client.recv()
    assert len(login1) == 0x60
    assert struct.unpack_from("<I", login1, 0x30)[0] == 0  # error OK
    token1 = struct.unpack_from("<I", login1, 0x1C)[0]
    client.send(_conninfo(), server.control_port)
    status = await client.recv()
    assert struct.unpack_from(">H", status, 0x42)[0] == server.civ_port

    # Reboot: down [+0.2, +1.6], busy [+1.6, +2.8] — every probe below keeps
    # a ≥0.5 s margin from the window edge it exercises.
    server.reboot(at=0.2, down_for=1.4, busy_for=1.2)
    t0 = time.monotonic()

    # Phase 2 — powered off: discovery goes unanswered. Probe at +0.75; the
    # 0.3 s silence observation ends at +1.05, still ≥0.5 s inside the window.
    await asyncio.sleep(0.75)
    client.send(_ayt(), server.control_port)
    await client.expect_silence(0.3)

    # Phase 3 — busy window: conninfo rejected previous-session-active.
    await asyncio.sleep(max(0.0, 2.2 - (time.monotonic() - t0)))
    client.send(_conninfo(), server.control_port)
    busy = await client.recv()
    assert struct.unpack_from("<I", busy, 0x30)[0] == _ERR_PREV_SESSION_ACTIVE
    assert struct.unpack_from(">H", busy, 0x42)[0] == 0

    # Phase 4 — recovered: fresh conninfo and a login with a rotated token.
    await asyncio.sleep(max(0.0, 3.3 - (time.monotonic() - t0)))
    client.send(_conninfo(), server.control_port)
    status2 = await client.recv()
    assert struct.unpack_from("<I", status2, 0x30)[0] == 0
    assert struct.unpack_from(">H", status2, 0x42)[0] == server.civ_port
    client.send(_login(), server.control_port)
    login2 = await client.recv()
    token2 = struct.unpack_from("<I", login2, 0x1C)[0]
    assert token2 != token1
    assert server.reboot_count == 1


async def test_keepalive_silence_stops_ping_replies_but_keeps_receiving(
    server,
    client,
):
    # Baseline: pings are answered (ptype 0x07, reply flag 0x01).
    client.send(_ping(), server.control_port)
    reply = await client.recv()
    assert struct.unpack_from("<H", reply, 4)[0] == _PT_PING
    assert reply[0x10] == 0x01

    # Silence window [+0.2, +1.8]: pings received but never answered. Probes
    # below keep a ≥0.5 s margin from both window edges.
    server.keepalive_silence(at=0.2, for_seconds=1.6)
    t0 = time.monotonic()
    await asyncio.sleep(0.7)

    # Still receiving: discovery is answered inside the silence window.
    client.send(_ayt(), server.control_port)
    assert struct.unpack_from("<H", await client.recv(), 4)[0] == _PT_I_AM_HERE

    # But ping replies are suppressed (observation ends ~+1.0, margin 0.8 s).
    client.send(_ping(), server.control_port)
    await client.expect_silence(0.3)

    # After the window, pings are answered again (+2.3, 0.5 s past the edge).
    await asyncio.sleep(max(0.0, 2.3 - (time.monotonic() - t0)))
    client.send(_ping(), server.control_port)
    reply = await client.recv()
    assert struct.unpack_from("<H", reply, 4)[0] == _PT_PING
    assert reply[0x10] == 0x01


async def test_reboot_fences_stale_client_until_fresh_open(server, client):
    """After a reboot the radio shuts out the pre-reboot client: its CI-V data
    and pings get silence until it completes a fresh OpenClose(open)."""
    # Log in, open the data port, and confirm the client is served.
    client.send(_login(), server.control_port)
    await client.recv()
    client.send(_conninfo(), server.control_port)
    await client.recv()
    client.send(_openclose(), server.civ_port)
    await client.recv()  # unsolicited CI-V frame on open
    request = _freq_get()
    client.send(request, server.civ_port)
    assert _is_freq_reply(await _recv_reply(client, request))

    # Reboot: down [+0.2, +0.8], busy [+0.8, +1.6].
    server.reboot(at=0.2, down_for=0.6, busy_for=0.8)
    t0 = time.monotonic()

    # The radio is back (probe at +1.3: 0.5 s past the down window), but the
    # stale client is fenced — CI-V data and pings are ignored.
    await asyncio.sleep(max(0.0, 1.3 - (time.monotonic() - t0)))
    client.send(_ping(), server.civ_port)
    client.send(_freq_get(), server.civ_port)
    await client.expect_silence(0.3)

    # A fresh login from a new client succeeds and is served (probe at +2.1:
    # 0.5 s past the busy window ending at +1.6).
    fresh = _UdpClient()
    await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: fresh, local_addr=(_HOST, 0)
    )
    try:
        await asyncio.sleep(max(0.0, 2.1 - (time.monotonic() - t0)))
        fresh.send(_login(), server.control_port)
        login = await fresh.recv()
        assert struct.unpack_from("<I", login, 0x30)[0] == 0
        fresh.send(_conninfo(), server.control_port)
        status = await fresh.recv()
        assert struct.unpack_from(">H", status, 0x42)[0] == server.civ_port
        fresh.send(_openclose(), server.civ_port)
        await fresh.recv()
        fresh_request = _freq_get()
        fresh.send(fresh_request, server.civ_port)
        assert _is_freq_reply(await _recv_reply(fresh, fresh_request))
        # ...while the stale client is still fenced.
        client.send(_freq_get(), server.civ_port)
        await client.expect_silence(0.3)
    finally:
        fresh.close()


async def test_reboot_ignores_control_packets_carrying_the_old_token(server, client):
    """After a reboot a conninfo carrying the pre-reboot token gets no answer,
    while the same conninfo carrying the token from a fresh login is served."""
    client.send(_login(), server.control_port)
    old_token = struct.unpack_from("<I", await client.recv(), 0x1C)[0]

    # Reboot: down [+0.1, +0.4], no busy window.
    server.reboot(at=0.1, down_for=0.3, busy_for=0.0)
    await asyncio.sleep(0.9)

    client.send(_conninfo(old_token), server.control_port)
    await client.expect_silence(0.3)

    client.send(_login(), server.control_port)
    new_token = struct.unpack_from("<I", await client.recv(), 0x1C)[0]
    assert new_token != old_token
    client.send(_conninfo(new_token), server.control_port)
    status = await client.recv()
    assert struct.unpack_from(">H", status, 0x42)[0] == server.civ_port


async def test_token_renew_reply_swaps_endpoint_ids(server, client):
    """The 0x40/0x05 renew answer carries the radio id as sender and the
    client id as receiver (pinned by the reply bytes below)."""
    client.send(_renew(), server.control_port)
    reply = await client.recv()
    assert len(reply) == 0x40
    assert reply[0x15] == 0x05
    assert struct.unpack_from("<I", reply, 0x08)[0] == server.radio_id
    assert struct.unpack_from("<I", reply, 0x0C)[0] == _CLIENT_ID
