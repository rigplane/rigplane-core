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
transport's state use the shared ``await_until`` helper
(``src/rigplane/testing/timing.py``); the cases where a timer must actually
expire (the ready-handshake grace wait, the discovery retries) expire it
with ``clock.elapse()`` on the shared ``virtual_clock`` fixture instead of
sleeping for real.
"""

import asyncio
import struct
from unittest.mock import patch

import pytest

from rigplane.audio.lan_stream import build_audio_packet
from rigplane.core.exceptions import DiscoveryTimeout
from rigplane.core.transport import IcomTransport
from rigplane.testing.timing import VirtualClock, await_until
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
    """Fake loop for full connect(): records the endpoint transport.

    ``time`` is the real running loop's own ``time`` attribute captured
    before the ``get_running_loop`` patch, so deadline math in
    ``_discover`` reads the real (possibly virtual) clock — and so does
    the virtual-clock branch of the shared ``await_until`` helper, which
    keys off that attribute. Under the ``virtual_clock`` fixture it is the
    clock itself; otherwise it is the loop's bound method.
    """

    def __init__(
        self,
        sockname: tuple[str, int],
        real_loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.sockname = sockname
        self.time = real_loop.time
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
    real_loop = asyncio.get_running_loop()
    loop = _FakeLoop(("192.168.2.194", 50002), real_loop)

    with patch("rigplane.transport.asyncio.get_running_loop", return_value=loop):
        task = asyncio.create_task(t.connect("192.168.2.1", 50002))
        await await_until(
            lambda: loop.endpoint is not None and loop.endpoint.sent,
            description="fake endpoint with an Are-You-There send",
        )
        assert loop.endpoint is not None
        # Control port sends Are-You-There first.
        assert _ptype_of(loop.endpoint.sent[0]) == PacketType.ARE_YOU_THERE
        # Sub-stream never sends 0x04: it pings us instead.
        t._handle_packet(radio._build_ping())
        await await_until(
            lambda: t.remote_id == RADIO_ID,
            description="remote_id learned from the ping",
        )
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
    await await_until(
        lambda: _sent_ptypes(sent, PacketType.ARE_YOU_THERE),
        description="first Are-You-There send",
    )
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
    real_loop = asyncio.get_running_loop()
    loop = _FakeLoop(("192.168.2.194", 50003), real_loop)

    with patch("rigplane.transport.asyncio.get_running_loop", return_value=loop):
        task = asyncio.create_task(t.connect("192.168.2.1", 50003))
        await await_until(
            lambda: loop.endpoint is not None and loop.endpoint.sent,
            description="fake endpoint with an Are-You-There send",
        )
    # The fake loop only fakes endpoint creation; the patch ends
    # here while the connect task keeps running. Its time()
    # delegates to the real loop captured above, so deadline math
    # and clock.elapse() below stay on the same (virtual) clock.
    assert loop.endpoint is not None
    t._handle_packet(radio._build_ping())
    await await_until(
        lambda: t.remote_id == RADIO_ID,
        description="remote_id learned from the ping",
    )
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
    await await_until(
        lambda: bool(sent),
        description="first Are-You-There send",
    )
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


async def test_audio_flood_then_ping_completes_discovery(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
) -> None:
    """An RX audio flood must not burn the discovery attempts (MOR-4436).

    The audio sub-stream streams data-size packets right after our
    Are-You-There and only pings ~100 ms later: each attempt lasts its full
    DISCOVERY_TIMEOUT, the flood is drained, and the ping still ends
    discovery with the ping sender as remote_id after a single
    Are-You-There. On the old code the flood burned one attempt per
    datagram, so discovery raised before the ping could arrive.
    """
    monkeypatch.setattr("rigplane.core.transport.DISCOVERY_RETRIES", 3)
    monkeypatch.setattr("rigplane.core.transport.DISCOVERY_TIMEOUT", 0.5)
    t = IcomTransport()
    sent: list[bytes] = []
    t._raw_send = sent.append
    radio = _radio(RADIO_ID)

    def _rx_audio(inner_seq: int) -> bytes:
        # Production RX audio layout with no connection ID established yet
        # (sentid 0): the data path learns nothing from it, so remote_id
        # stays 0 until the ping arrives (the nonzero-sender case is pinned
        # by ``test_audio_data_before_ping_still_completes_discovery``).
        return build_audio_packet(
            bytes(160), sender_id=0, receiver_id=0, send_seq=inner_seq
        )

    async def _flood_then_ping() -> None:
        for inner in range(5):  # more than DISCOVERY_RETRIES
            t._handle_packet(_rx_audio(inner))
        await asyncio.sleep(0.1)  # the radio's ping lands ~100 ms later
        t._handle_packet(radio._build_ping())

    task = asyncio.create_task(t._discover())
    responder = asyncio.create_task(_flood_then_ping())
    try:
        await virtual_clock.elapse(0.5)
        await asyncio.wait_for(task, timeout=10.0)
    finally:
        await responder

    assert t.remote_id == RADIO_ID
    assert t._discovery_ping_sender == RADIO_ID
    assert t._discover_ping_event is None
    assert t._packet_queue.qsize() == 0
    assert len(_sent_ptypes(sent, PacketType.ARE_YOU_THERE)) == 1


async def test_audio_flood_without_ping_still_times_out(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
) -> None:
    """A data flood with no ping/0x04 still raises _DiscoveryTimeout."""
    monkeypatch.setattr("rigplane.core.transport.DISCOVERY_RETRIES", 3)
    monkeypatch.setattr("rigplane.core.transport.DISCOVERY_TIMEOUT", 0.05)
    t = IcomTransport()
    sent: list[bytes] = []
    t._raw_send = sent.append

    task = asyncio.create_task(t._discover())
    await await_until(
        lambda: _sent_ptypes(sent, PacketType.ARE_YOU_THERE),
        description="first Are-You-There send",
    )
    for inner in range(8):  # more than DISCOVERY_RETRIES, still no answer
        t._handle_packet(
            build_audio_packet(bytes(160), sender_id=0, receiver_id=0, send_seq=inner)
        )
    await virtual_clock.elapse(3 * 0.05 + 0.1)
    with pytest.raises(DiscoveryTimeout):
        await task

    assert t.remote_id == 0
    assert len(_sent_ptypes(sent, PacketType.ARE_YOU_THERE)) == 3


async def test_audio_data_before_ping_still_completes_discovery(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
) -> None:
    """RX DATA from the radio id must not block the ping signal (MOR-4436).

    On the live audio sub-stream the radio's first datagrams are RX audio
    DATA carrying the radio id, so the shared data path learns remote_id
    before the radio's ping REQUEST arrives. The ping handler still signals
    the waiter whenever a discovery is active and no sender was recorded
    yet, so discovery completes via the ping. Against the old
    ``remote_id == 0`` guard the ping is dropped and ``_discover`` raises
    ``DiscoveryTimeout`` after the full retries.
    """
    monkeypatch.setattr("rigplane.core.transport.DISCOVERY_RETRIES", 3)
    monkeypatch.setattr("rigplane.core.transport.DISCOVERY_TIMEOUT", 0.5)
    t = IcomTransport()
    sent: list[bytes] = []
    t._raw_send = sent.append
    radio = _radio(RADIO_ID)

    def _rx_audio(inner_seq: int) -> bytes:
        # Live layout: RX audio DATA already stamped with the radio id.
        return build_audio_packet(
            bytes(160), sender_id=RADIO_ID, receiver_id=0, send_seq=inner_seq
        )

    async def _data_then_ping() -> None:
        for inner in range(3):
            t._handle_packet(_rx_audio(inner))
        assert t.remote_id == RADIO_ID  # learned by the data path first
        await asyncio.sleep(0.1)  # the radio's ping lands ~100 ms later
        t._handle_packet(radio._build_ping())

    task = asyncio.create_task(t._discover())
    responder = asyncio.create_task(_data_then_ping())
    try:
        await virtual_clock.elapse(0.5)
        await asyncio.wait_for(task, timeout=10.0)
    finally:
        await responder

    assert t.remote_id == RADIO_ID
    assert t._discovery_ping_sender == RADIO_ID
    assert t._discover_ping_event is None
    assert t._packet_queue.qsize() == 0
    assert len(_sent_ptypes(sent, PacketType.ARE_YOU_THERE)) == 1


async def test_cancelled_discovery_retires_waiters_and_rediscovers() -> None:
    """Cancelling discovery retires both waiters; a retry answers at once.

    The queue getter and the ping waiter are cancelled and awaited even
    when the cancel lands while parked in ``asyncio.wait``: no task is
    left pending, no getter stays queued on ``_packet_queue``, and a fresh
    ``_discover`` on the same transport completes on its first response
    instead of losing it to the abandoned getter.
    """
    t = IcomTransport()
    radio = _radio(RADIO_ID)
    sent: list[bytes] = []
    t._raw_send = sent.append

    before = set(asyncio.all_tasks())
    task = asyncio.create_task(t._discover())
    await await_until(
        lambda: _sent_ptypes(sent, PacketType.ARE_YOU_THERE),
        description="first Are-You-There send",
    )
    # The send and the waiter setup run with no await between them, so an
    # observed send means both waiters exist and discovery is parked in
    # asyncio.wait: cancel exactly there.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    current = asyncio.current_task()
    leftovers = {
        waiter
        for waiter in asyncio.all_tasks()
        if waiter not in before and waiter is not current and not waiter.done()
    }
    assert not leftovers
    assert t._discover_ping_event is None
    # No abandoned queue getter may be left behind: an item put now stays
    # queued instead of waking a leaked waiter.
    t._packet_queue.put_nowait(b"probe")
    assert t._packet_queue.qsize() == 1
    assert t._packet_queue.get_nowait() == b"probe"

    # A fresh discovery on the same transport receives its first response
    # normally: one more Are-You-There, then the I-Am-Here completes it.
    ayts_before = len(_sent_ptypes(sent, PacketType.ARE_YOU_THERE))
    retry = asyncio.create_task(t._discover())
    await await_until(
        lambda: len(_sent_ptypes(sent, PacketType.ARE_YOU_THERE)) > ayts_before,
        description="retry Are-You-There send",
    )
    t._handle_packet(radio._build_control(ptype=PacketType.I_AM_HERE, seq=0))
    await asyncio.wait_for(retry, timeout=5.0)

    assert t.remote_id == RADIO_ID
    assert t._discover_ping_event is None
    assert len(_sent_ptypes(sent, PacketType.ARE_YOU_THERE)) == ayts_before + 1


async def test_cancel_during_discovery_cleanup_stops_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel landing in the waiter-retirement await still stops connect().

    I-Am-Here arrives, the wait returns, and discovery retires the ping
    waiter via gather: cancelling exactly there must propagate out of
    ``connect()`` instead of letting discovery return and send
    Are-You-Ready.
    """
    t = IcomTransport()
    radio = _radio(RADIO_ID)
    real_loop = asyncio.get_running_loop()
    loop = _FakeLoop(("192.168.2.194", 50004), real_loop)

    retire_started = False
    release = asyncio.Event()
    real_gather = asyncio.gather

    async def _spy_gather(*args, **kwargs):
        nonlocal retire_started
        if kwargs.get("return_exceptions"):
            # Hold the retirement await open so the cancel below lands
            # exactly in it instead of racing discovery's return.
            retire_started = True
            await release.wait()
        return await real_gather(*args, **kwargs)

    monkeypatch.setattr(asyncio, "gather", _spy_gather)
    with patch("rigplane.transport.asyncio.get_running_loop", return_value=loop):
        task = asyncio.create_task(t.connect("192.168.2.1", 50004))
        await await_until(
            lambda: loop.endpoint is not None and loop.endpoint.sent,
            description="fake endpoint with an Are-You-There send",
        )
        assert loop.endpoint is not None
        # I-Am-Here ends the wait; discovery then retires the ping waiter
        # via gather — cancel exactly while it awaits that retirement.
        t._handle_packet(radio._build_control(ptype=PacketType.I_AM_HERE, seq=0))
        await await_until(lambda: retire_started, description="waiter retirement")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert loop.endpoint is not None
    assert t._discover_ping_event is None
    assert not _sent_ptypes(loop.endpoint.sent, PacketType.ARE_YOU_READY)
