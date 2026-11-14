"""Discovery via sub-stream ping requests (MOR-4436).

Some LAN sub-streams (e.g. a Xiegu G7250 in wfview network mode on the
CI-V/audio ports) never answer Are-You-There with I-Am-Here (0x04): they
start PINGING the client instead. These tests pin the shared fallback in
``IcomTransport._discover``: a ping REQUEST arriving while still
undiscovered ends the are-you-there phase with the ping's sender as
remote_id, and the ready handshake then runs unchanged.

All inbound packets below are built with the PRODUCTION builders
(``IcomTransport._build_control`` / ``_build_ping`` / the ping reply path
in ``_handle_ping_packet``) — never hand-typed byte layouts.

Timing (MOR-4075): no test here uses the real clock. Waits for the fake
transport's state are bounded ``sleep(0)`` yield polls; the cases where a
timer must actually expire (the ready-handshake grace wait, the discovery
retries) expire it with ``clock.elapse()`` on the shared ``virtual_clock``
fixture instead of sleeping for real.
"""

import asyncio
import struct
from unittest.mock import patch

import pytest

from rigplane.core.exceptions import DiscoveryTimeout
from rigplane.core.transport import IcomTransport
from rigplane.testing.timing import VirtualClock
from rigplane.transport import PING_SIZE
from rigplane.types import PacketType

pytestmark = pytest.mark.timeout(30)

RADIO_ID = 0x0EF167A4
OTHER_ID = 0x0AABBCCDD


def _radio(sender_id: int) -> IcomTransport:
    """A scratch transport acting as the radio's packet builder."""
    radio = IcomTransport()
    radio.my_id = sender_id
    return radio


def _production_ping_reply(sender_id: int) -> bytes:
    """A ping REPLY packet produced by the production reply path."""
    asker = IcomTransport()
    asker.my_id = 0x11111111
    radio = IcomTransport()
    radio.my_id = sender_id
    captured: list[bytes] = []
    radio._raw_send = captured.append
    radio._handle_packet(asker._build_ping())
    assert len(captured) == 1
    assert captured[0][0x10] == 0x01  # reply flag
    return captured[0]


async def _wait_for(pred, attempts: int = 10000) -> None:
    """Yield until ``pred`` holds; bounded, no clock and no real sleep."""
    for _ in range(attempts):
        if pred():
            return
        await asyncio.sleep(0)
    raise AssertionError("timed out waiting for condition")


def _ptype_of(pkt: bytes) -> int:
    return struct.unpack_from("<H", pkt, 4)[0]


class _FakeDatagramTransport:
    def __init__(
        self,
        sockname: tuple[str, int],
        peername: tuple[str, int] | None = None,
    ) -> None:
        self._sockname = sockname
        self._peername = peername
        self.sent: list[bytes] = []

    def get_extra_info(self, name: str):
        if name == "sockname":
            return self._sockname
        if name == "peername":
            return self._peername
        return None

    def sendto(self, data: bytes, addr=None) -> None:
        self.sent.append(bytes(data))


class _FakeLoop:
    """Fake loop for full connect(): records the endpoint transport."""

    def __init__(self, sockname: tuple[str, int]) -> None:
        self.sockname = sockname
        self.endpoint: _FakeDatagramTransport | None = None

    async def create_datagram_endpoint(
        self,
        protocol_factory,
        *,
        remote_addr=None,
        local_addr=None,
        sock=None,
    ):
        transport = _FakeDatagramTransport(self.sockname, remote_addr)
        self.endpoint = transport
        protocol = protocol_factory()
        protocol.connection_made(transport)
        return transport, protocol


def _sent_ptypes(sent: list[bytes], ptype: int) -> list[bytes]:
    return [p for p in sent if len(p) >= 6 and _ptype_of(p) == ptype]


async def test_ping_only_discovery_completes_connect() -> None:
    """AYT answered with pings only: discovery learns remote_id from ping."""
    t = IcomTransport()
    radio = _radio(RADIO_ID)
    loop = _FakeLoop(("192.168.2.194", 50002))

    with patch("rigplane.transport.asyncio.get_running_loop", return_value=loop):
        task = asyncio.create_task(t.connect("192.168.2.1", 50002))
        await _wait_for(lambda: loop.endpoint is not None and loop.endpoint.sent)
        assert loop.endpoint is not None
        # Control port sends Are-You-There first.
        assert _ptype_of(loop.endpoint.sent[0]) == PacketType.ARE_YOU_THERE
        # Sub-stream never sends 0x04: it pings us instead.
        t._handle_packet(radio._build_ping())
        await _wait_for(lambda: t.remote_id == RADIO_ID)
        # Ready handshake echo arrives on the CI-V stream normally.
        t._handle_packet(radio._build_control(ptype=PacketType.ARE_YOU_READY, seq=0))
        await asyncio.wait_for(task, timeout=5.0)

    assert t.remote_id == RADIO_ID
    assert t._discovery_ping_sender == RADIO_ID
    # The ping was still answered as before (reply flag 0x01, seq echoed).
    replies = [
        p
        for p in _sent_ptypes(loop.endpoint.sent, PacketType.PING)
        if len(p) == PING_SIZE and p[0x10] == 0x01
    ]
    assert len(replies) == 1


async def test_i_am_here_still_wins_without_ping_path() -> None:
    """A normal 0x04 reply behaves exactly as before; ping path unused."""
    t = IcomTransport()
    radio = _radio(RADIO_ID)
    sent: list[bytes] = []
    t._raw_send = sent.append

    task = asyncio.create_task(t._discover())
    await _wait_for(lambda: _sent_ptypes(sent, PacketType.ARE_YOU_THERE))
    t._handle_packet(radio._build_control(ptype=PacketType.I_AM_HERE, seq=0))
    await asyncio.wait_for(task, timeout=5.0)

    assert t.remote_id == RADIO_ID
    assert t._discovery_ping_sender is None
    assert t._discover_ping_event is None


async def test_audio_stream_without_ready_reply_proceeds(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
) -> None:
    """Ping-discovered stream with no 0x06 takes the 'proceeding' path."""
    monkeypatch.setattr("rigplane.core.transport.DISCOVERY_TIMEOUT", 0.05)
    t = IcomTransport()
    radio = _radio(RADIO_ID)
    loop = _FakeLoop(("192.168.2.194", 50003))

    with patch("rigplane.transport.asyncio.get_running_loop", return_value=loop):
        task = asyncio.create_task(t.connect("192.168.2.1", 50003))
        await _wait_for(lambda: loop.endpoint is not None and loop.endpoint.sent)
    # The fake loop only fakes endpoint creation; packet handling and
    # clock.elapse() below need the real running loop back (elapse reads
    # loop.time, which the fake does not provide).
    assert loop.endpoint is not None
    t._handle_packet(radio._build_ping())
    await _wait_for(lambda: t.remote_id == RADIO_ID)
    # No ARE_YOU_READY echo ever arrives (audio-stream case): expire the
    # handshake grace wait on the virtual clock, not in real time.
    await virtual_clock.elapse(0.5)
    await asyncio.wait_for(task, timeout=10.0)

    assert t.remote_id == RADIO_ID


async def test_late_ping_does_not_change_remote_id() -> None:
    """A ping arriving after discovery finished changes nothing."""
    t = IcomTransport()
    sent: list[bytes] = []
    t._raw_send = sent.append

    # Discovered normally via 0x04 from OTHER_ID ...
    other = _radio(OTHER_ID)
    task = asyncio.create_task(t._discover())
    await _wait_for(lambda: bool(sent))
    t._handle_packet(other._build_control(ptype=PacketType.I_AM_HERE, seq=0))
    await asyncio.wait_for(task, timeout=5.0)
    assert t.remote_id == OTHER_ID

    # ... then a ping from RADIO_ID arrives late: still answered, ignored.
    n_sent = len(sent)
    t._handle_packet(_radio(RADIO_ID)._build_ping())
    assert t.remote_id == OTHER_ID
    assert t._discovery_ping_sender is None
    assert len(sent) == n_sent + 1
    assert sent[-1][0x10] == 0x01  # a ping reply


async def test_ping_with_no_active_discovery_is_answered_and_ignored() -> None:
    """Ping on an undiscovered transport with no _discover: reply only."""
    t = IcomTransport()
    sent: list[bytes] = []
    t._raw_send = sent.append

    t._handle_packet(_radio(RADIO_ID)._build_ping())

    assert t.remote_id == 0
    assert t._discovery_ping_sender is None
    assert len(sent) == 1
    assert sent[0][0x10] == 0x01


async def test_no_answer_still_times_out(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
) -> None:
    """Silence still raises _DiscoveryTimeout after the retries."""
    monkeypatch.setattr("rigplane.core.transport.DISCOVERY_RETRIES", 3)
    monkeypatch.setattr("rigplane.core.transport.DISCOVERY_TIMEOUT", 0.05)
    t = IcomTransport()
    sent: list[bytes] = []
    t._raw_send = sent.append

    task = asyncio.create_task(t._discover())
    # Expire all three attempts on the virtual clock: no real waiting.
    await virtual_clock.elapse(3 * 0.05 + 0.1)
    with pytest.raises(DiscoveryTimeout):
        await task

    assert t.remote_id == 0
    assert len(_sent_ptypes(sent, PacketType.ARE_YOU_THERE)) == 3


async def test_ping_replies_do_not_trigger_discovery(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
) -> None:
    """Ping REPLIES (flag 0x01) are not requests: still _DiscoveryTimeout."""
    monkeypatch.setattr("rigplane.core.transport.DISCOVERY_RETRIES", 3)
    monkeypatch.setattr("rigplane.core.transport.DISCOVERY_TIMEOUT", 0.05)
    t = IcomTransport()
    sent: list[bytes] = []
    t._raw_send = sent.append

    async def _reply_only_radio() -> None:
        for _ in range(6):
            await asyncio.sleep(0.02)
            t._handle_packet(_production_ping_reply(RADIO_ID))

    task = asyncio.create_task(t._discover())
    responder = asyncio.create_task(_reply_only_radio())
    try:
        # Pace the replies and expire the attempts on the virtual clock, so
        # the replies still land mid-discovery without any real sleeping.
        await virtual_clock.elapse(0.5)
        with pytest.raises(DiscoveryTimeout):
            await task
    finally:
        await responder

    assert t.remote_id == 0
    assert t._discovery_ping_sender is None
