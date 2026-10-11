"""Wire-level tests: real MockIcomRadio + a plain UDP client.

These tests start the actual simulator on ephemeral loopback ports and talk
to it with a bare asyncio datagram client — no production code involved —
so drop/duplicate/corrupt/truncate/reorder are observed on the wire,
exactly as a real client would see them.
"""

import asyncio
import struct

import pytest

from rigplane_testkit.icom import FaultPlan, FaultRule, MockIcomRadio

_HOST = "127.0.0.1"
_CLIENT_ID = 0x11223344

# Packet type codes (mirrors of the wire constants the simulator speaks).
_PT_DATA = 0x00
_PT_ARE_YOU_THERE = 0x03
_PT_I_AM_HERE = 0x04


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


def _freq_get(seq: int = 1) -> bytes:
    """CI-V 'get frequency' (0x03) wrapped in a DATA header for the civ port."""
    frame = b"\xfe\xfe\x98\xe0\x03\xfd"
    pkt = _header(0x15 + len(frame), _PT_DATA, seq=seq)
    pkt[0x10] = 0xC1
    struct.pack_into("<H", pkt, 0x11, len(frame))
    pkt[0x15:] = frame
    return bytes(pkt)


def _is_freq_reply(pkt: bytes) -> bool:
    return len(pkt) > 0x1A and pkt[0x15:0x17] == b"\xfe\xfe" and pkt[0x19] == 0x03


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
    client.send(_freq_get(), server.civ_port)
    reply = await client.recv()
    assert _is_freq_reply(reply)


# ---------------------------------------------------------------------------
# Fault plan on the wire (radio → client unless noted)
# ---------------------------------------------------------------------------


async def test_drop_bounded_to_next_packet(server, client):
    """'Lose the next civ reply': the first answer vanishes, the second lands."""
    server.fault_plan = FaultPlan(
        civ_to_client=FaultRule(drop_probability=1.0, max_packets=1)
    )
    client.send(_freq_get(), server.civ_port)
    await client.expect_silence(0.3)
    client.send(_freq_get(), server.civ_port)
    assert _is_freq_reply(await client.recv())
    assert server.fault_plan.civ_to_client.dropped == 1


async def test_duplicate_sends_two_identical_copies(server, client):
    server.fault_plan = FaultPlan(
        civ_to_client=FaultRule(duplicate_probability=1.0, duplicate_delay_s=0.05)
    )
    client.send(_freq_get(), server.civ_port)
    first = await client.recv()
    second = await client.recv()
    assert _is_freq_reply(first)
    assert first == second


async def test_corrupt_flips_bytes_on_the_wire(server, client):
    # Flip every byte of the reply (corrupt_bytes above the packet length):
    # the length header no longer matches the datagram size.
    server.fault_plan = FaultPlan(
        civ_to_client=FaultRule(corrupt_probability=1.0, corrupt_bytes=64)
    )
    client.send(_freq_get(), server.civ_port)
    reply = await client.recv()
    assert struct.unpack_from("<I", reply, 0)[0] != len(reply)


async def test_truncate_cuts_reply_on_the_wire(server, client):
    server.fault_plan = FaultPlan(
        civ_to_client=FaultRule(truncate_probability=1.0, truncate_to=6)
    )
    client.send(_freq_get(), server.civ_port)
    reply = await client.recv()
    assert len(reply) == 6


async def test_reorder_permutes_replies(server, client):
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
    client.send(_freq_get(), server.civ_port)
    assert _is_freq_reply(await client.recv(timeout=1.0))
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
