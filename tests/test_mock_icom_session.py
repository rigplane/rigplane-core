"""Simulator-fidelity tests for the IC-7610 session lifecycle in MockIcomRadio.

These tests validate the SIMULATOR ITSELF — not any production lifecycle code
(which does not exist yet). They drive the mock's control / CI-V UDP ports with
hand-built protocol packets and assert the modelled network CI-V session
lifecycle:

  * held single-owner session -> foreign conninfo gets civ_port=0 + 0xFFFFFFFF;
  * token-remove (token magic 0x01) -> session freed immediately;
  * OpenClose(close) -> session freed immediately;
  * keepalive timeout (accelerated) -> session auto-released after the hold;
  * keepalive refresh on ping / renew keeps the session alive;
  * civ_port-unavailable knob -> civ_port=0 with error=0 (not-ready, not busy);
  * fault-injection knobs (drop_rate, stall_for, unsolicited stream).

The packets only need the sizes / offsets the simulator parses
(``len`` @0x00, ``type`` @0x04, ``sender_id`` @0x08, magic byte @0x15), so they
are hand-built rather than going through the production handshake.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncGenerator

import pytest

from mock_server import (
    _ERR_OK,
    _ERR_PREV_SESSION_ACTIVE,
    _OPENCLOSE_CLOSE,
    _OPENCLOSE_OPEN,
    _PT_DATA,
    _PT_DISCONNECT,
    _PT_PING,
    _TOKEN_REMOVE,
    _TOKEN_RENEW,
    MockIcomRadio,
)
from rigplane.testing.timing import VirtualClock, await_until

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Raw UDP client driving the simulator's control / CI-V ports
# ---------------------------------------------------------------------------


class _RawClient:
    """Minimal UDP client that speaks just enough protocol for the simulator.

    Owner identity on the wire is ``(remote_addr, sender_id)``; each client
    instance uses a distinct ``sender_id`` so the simulator treats them as
    different owners.
    """

    def __init__(self, sender_id: int) -> None:
        self.sender_id = sender_id
        self._transport: asyncio.DatagramTransport | None = None
        self._proto: _ClientProto | None = None

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        transport, proto = await loop.create_datagram_endpoint(
            lambda: _ClientProto(),
            local_addr=("127.0.0.1", 0),
        )
        self._transport = transport
        self._proto = proto

    async def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _send(self, pkt: bytes, port: int) -> None:
        assert self._transport is not None
        self._transport.sendto(pkt, ("127.0.0.1", port))

    async def _recv(self, timeout: float = 1.0) -> bytes:
        assert self._proto is not None
        return await asyncio.wait_for(self._proto.queue.get(), timeout)

    # -- header helper --------------------------------------------------

    def _header(self, length: int, ptype: int) -> bytearray:
        pkt = bytearray(length)
        struct.pack_into("<I", pkt, 0x00, length)
        struct.pack_into("<H", pkt, 0x04, ptype)
        struct.pack_into("<I", pkt, 0x08, self.sender_id)
        return pkt

    # -- control-port primitives ---------------------------------------

    async def conninfo(self, ctrl_port: int) -> tuple[int, int]:
        """Send a 0x90 conninfo (requesttype 0x03) and parse the status reply.

        Returns ``(civ_port, error)`` from the status response
        (civ_port BE@0x42, error LE@0x30).
        """
        pkt = self._header(0x90, _PT_DATA)
        pkt[0x15] = 0x03
        self._send(bytes(pkt), ctrl_port)
        reply = await self._recv()
        error = struct.unpack_from("<I", reply, 0x30)[0]
        civ_port = struct.unpack_from(">H", reply, 0x42)[0]
        return civ_port, error

    def token(self, ctrl_port: int, magic: int) -> None:
        """Send a 0x40 token packet with the given magic at pkt[0x15]."""
        pkt = self._header(0x40, _PT_DATA)
        pkt[0x14] = 0x01
        pkt[0x15] = magic
        self._send(bytes(pkt), ctrl_port)

    def ping(self, ctrl_port: int) -> None:
        """Send a 0x15 control ping request (data[0x10]=0x00)."""
        pkt = self._header(0x15, _PT_PING)
        pkt[0x10] = 0x00
        self._send(bytes(pkt), ctrl_port)

    def disconnect(self, ctrl_port: int) -> None:
        self._send(bytes(self._header(0x10, _PT_DISCONNECT)), ctrl_port)

    # -- CI-V-port primitives ------------------------------------------

    def openclose(self, civ_port: int, magic: int) -> None:
        """Send a 0x16 OpenClose packet with magic at pkt[0x15]."""
        pkt = self._header(0x16, _PT_DATA)
        pkt[0x15] = magic
        self._send(bytes(pkt), civ_port)


class _ClientProto(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.queue.put_nowait(data)


@pytest.fixture
async def sim() -> AsyncGenerator[MockIcomRadio]:
    """Single-owner simulator with a fast keepalive hold for the tests."""
    server = MockIcomRadio(single_owner=True, keepalive_hold_s=0.3)
    await server.start()
    yield server
    await server.stop()


async def _client(sender_id: int) -> _RawClient:
    c = _RawClient(sender_id)
    await c.open()
    return c


async def _claim(sim: MockIcomRadio, client: _RawClient) -> tuple[int, int]:
    """Drive a conninfo and return ``(civ_port, error)``; claims on success."""
    return await client.conninfo(sim.control_port)


# ---------------------------------------------------------------------------
# 1. Held single-owner session -> busy reject (0xFFFFFFFF)
# ---------------------------------------------------------------------------


async def test_first_conninfo_claims_and_returns_civ_port(sim: MockIcomRadio) -> None:
    c = await _client(0x10001)
    try:
        civ_port, error = await _claim(sim, c)
        assert error == _ERR_OK
        assert civ_port == sim.civ_port
        assert civ_port != 0
        assert sim.session_held is True
        assert sim.owner is not None and sim.owner[1] == 0x10001
    finally:
        await c.close()


async def test_held_session_rejects_foreign_owner_with_0xffffffff(
    sim: MockIcomRadio,
) -> None:
    owner = await _client(0x10001)
    intruder = await _client(0x10002)
    try:
        civ_port, error = await _claim(sim, owner)
        assert error == _ERR_OK and civ_port != 0
        assert sim.session_held is True

        # A DIFFERENT owner asking for data ports while held -> busy reject.
        civ_port2, error2 = await _claim(sim, intruder)
        assert civ_port2 == 0
        assert error2 == _ERR_PREV_SESSION_ACTIVE
        assert sim.busy_rejects == 1
        # Ownership is unchanged by the rejected attempt.
        assert sim.owner is not None and sim.owner[1] == 0x10001
    finally:
        await owner.close()
        await intruder.close()


async def test_same_owner_reconnect_is_not_rejected(sim: MockIcomRadio) -> None:
    c = await _client(0x10001)
    try:
        await _claim(sim, c)
        # Same owner repeating conninfo refreshes, never busy-rejects.
        civ_port, error = await _claim(sim, c)
        assert error == _ERR_OK and civ_port == sim.civ_port
        assert sim.busy_rejects == 0
    finally:
        await c.close()


# ---------------------------------------------------------------------------
# 2. Graceful release: token-remove / OpenClose(close) free immediately
# ---------------------------------------------------------------------------


async def test_token_remove_frees_session_immediately(
    sim: MockIcomRadio, virtual_clock: VirtualClock
) -> None:
    owner = await _client(0x10001)
    newcomer = await _client(0x10002)
    try:
        await _claim(sim, owner)
        assert sim.session_held is True

        # Graceful withdraw: the release is a real UDP delivery, so wait for
        # the simulator to observe it instead of sleeping a wall interval.
        owner.token(sim.control_port, _TOKEN_REMOVE)
        await await_until(
            lambda: not sim.session_held,
            timeout=5.0,
            description="session released after token-remove",
        )
        assert sim.session_held is False
        assert sim.last_release_reason == "token_remove"

        # A subsequent connect (even a different owner) succeeds, no cooldown.
        civ_port, error = await _claim(sim, newcomer)
        assert error == _ERR_OK and civ_port == sim.civ_port
        assert sim.busy_rejects == 0
    finally:
        await owner.close()
        await newcomer.close()


async def test_openclose_close_frees_session_immediately(
    sim: MockIcomRadio, virtual_clock: VirtualClock
) -> None:
    owner = await _client(0x10001)
    try:
        await _claim(sim, owner)
        # Open the data stream, then close it gracefully. Both are real UDP
        # deliveries, so wait for the simulator to observe each one.
        owner.openclose(sim.civ_port, _OPENCLOSE_OPEN)
        await await_until(
            lambda: sim.openclose_open_count >= 1,
            timeout=5.0,
            description="openclose open processed",
        )
        assert sim.session_held is True

        owner.openclose(sim.civ_port, _OPENCLOSE_CLOSE)
        await await_until(
            lambda: not sim.session_held,
            timeout=5.0,
            description="session released after openclose close",
        )
        assert sim.session_held is False
        assert sim.last_release_reason == "openclose_close"
    finally:
        await owner.close()


async def test_disconnect_packet_frees_session(
    sim: MockIcomRadio, virtual_clock: VirtualClock
) -> None:
    owner = await _client(0x10001)
    try:
        await _claim(sim, owner)
        assert sim.session_held is True
        owner.disconnect(sim.control_port)
        await await_until(
            lambda: not sim.session_held,
            timeout=5.0,
            description="session released after disconnect",
        )
        assert sim.session_held is False
        assert sim.last_release_reason == "disconnect"
    finally:
        await owner.close()


# ---------------------------------------------------------------------------
# 3. Keepalive timeout: silent drop holds, then auto-releases
# ---------------------------------------------------------------------------


async def test_keepalive_timeout_auto_releases_after_hold(
    sim: MockIcomRadio, virtual_clock: VirtualClock
) -> None:
    # keepalive_hold_s is 0.3 in the fixture. The auto-release is a loop
    # timer (call_later), so elapsing the virtual clock fires it exactly.
    owner = await _client(0x10001)
    try:
        await _claim(sim, owner)
        assert sim.session_held is True

        # No graceful close: while inside the hold the session is still held.
        await virtual_clock.elapse(0.1)
        assert sim.session_held is True

        # After the hold elapses with no traffic, the session auto-releases.
        await virtual_clock.elapse(0.35)
        assert sim.session_held is False
        assert sim.last_release_reason == "keepalive_timeout"
    finally:
        await owner.close()


async def test_busy_reject_while_inside_keepalive_hold(
    sim: MockIcomRadio, virtual_clock: VirtualClock
) -> None:
    """A fast relaunch inside the keepalive window still hits the held session."""
    owner = await _client(0x10001)
    newcomer = await _client(0x10002)
    try:
        await _claim(sim, owner)
        await owner.close()  # socket goes silent, NO token-remove

        # Still inside the hold -> the radio holds -> new owner is rejected.
        await virtual_clock.elapse(0.05)
        civ_port, error = await _claim(sim, newcomer)
        assert civ_port == 0 and error == _ERR_PREV_SESSION_ACTIVE

        # After the hold expires, the new owner can connect.
        await virtual_clock.elapse(0.35)
        civ_port2, error2 = await _claim(sim, newcomer)
        assert error2 == _ERR_OK and civ_port2 == sim.civ_port
    finally:
        await newcomer.close()


async def test_ping_refreshes_keepalive(virtual_clock: VirtualClock) -> None:
    # Deterministic: the hold (60 s) dwarfs the test time, so the release
    # timer cannot fire mid-test; each ping must re-arm its deadline. The
    # elapse moves virtual time forward so the re-armed deadline (now + hold)
    # is strictly later than the previous one; the ping itself is a real UDP
    # delivery, awaited with await_until.
    sim = MockIcomRadio(single_owner=True, keepalive_hold_s=60.0)
    await sim.start()
    owner = await _client(0x10001)
    try:
        await _claim(sim, owner)
        assert sim.session_held is True
        assert sim._keepalive_timer is not None
        for _ in range(6):
            await virtual_clock.elapse(0.05)
            assert sim._keepalive_timer is not None
            before = sim._keepalive_timer.when()
            owner.ping(sim.control_port)
            await await_until(
                lambda: (
                    sim._keepalive_timer is not None
                    and sim._keepalive_timer.when() > before
                ),
                timeout=5.0,
                description="keepalive deadline to move after ping",
            )
            assert sim.session_held is True
            assert sim.released_count == 0
        assert sim.session_held is True
        assert sim.released_count == 0
    finally:
        await owner.close()
        await sim.stop()


async def test_token_renew_refreshes_keepalive(virtual_clock: VirtualClock) -> None:
    # Deterministic: the hold (60 s) dwarfs the test time, so the release
    # timer cannot fire mid-test; each renewal must re-arm its deadline. The
    # elapse moves virtual time forward so the re-armed deadline (now + hold)
    # is strictly later than the previous one; the renewal itself is a real
    # UDP delivery, awaited with await_until.
    sim = MockIcomRadio(single_owner=True, keepalive_hold_s=60.0)
    await sim.start()
    owner = await _client(0x10001)
    try:
        await _claim(sim, owner)
        assert sim.session_held is True
        assert sim._keepalive_timer is not None
        for _ in range(3):
            await virtual_clock.elapse(0.05)
            assert sim._keepalive_timer is not None
            before = sim._keepalive_timer.when()
            owner.token(sim.control_port, _TOKEN_RENEW)
            await await_until(
                lambda: (
                    sim._keepalive_timer is not None
                    and sim._keepalive_timer.when() > before
                ),
                timeout=5.0,
                description="keepalive deadline to move after token renew",
            )
            assert sim.session_held is True
            assert sim.released_count == 0
            assert sim._keepalive_timer is not None
        assert sim.session_held is True
        assert sim.released_count == 0
    finally:
        await owner.close()
        await sim.stop()


# ---------------------------------------------------------------------------
# 4. civ_port-unavailable knob (cooldown driver) — distinct from busy reject
# ---------------------------------------------------------------------------


async def test_force_civ_unavailable_returns_not_ready_then_recovers(
    sim: MockIcomRadio, virtual_clock: VirtualClock
) -> None:
    owner = await _client(0x10001)
    try:
        sim.force_civ_unavailable_for(0.2)
        # Not-ready: civ_port=0 but error=0 (NOT a busy reject), no claim.
        civ_port, error = await _claim(sim, owner)
        assert civ_port == 0 and error == _ERR_OK
        assert sim.session_held is False
        assert sim.busy_rejects == 0

        # End the not-ready window deterministically instead of sleeping out
        # the wall-clock knob; the recovery path below is what this pins.
        sim.clear_civ_unavailable()
        # Now available -> claims and returns the real port.
        civ_port2, error2 = await _claim(sim, owner)
        assert error2 == _ERR_OK and civ_port2 == sim.civ_port
        assert sim.session_held is True
    finally:
        await owner.close()


# ---------------------------------------------------------------------------
# 5. Fault-injection knobs
# ---------------------------------------------------------------------------


async def test_unsolicited_stream_emits_frames(virtual_clock: VirtualClock) -> None:
    sim = MockIcomRadio(
        single_owner=True,
        keepalive_hold_s=5.0,
        unsolicited_civ=True,
        unsolicited_interval_s=0.02,
    )
    await sim.start()
    c = await _client(0x10001)
    try:
        await _claim(sim, c)
        # Open the data stream so the radio knows where to send unsolicited frames.
        start_freq = sim._frequency
        c.openclose(sim.civ_port, _OPENCLOSE_OPEN)
        # The emission cadence is a loop timer, so elapsing the virtual clock
        # drives the stream deterministically (delivery of the open settles
        # first, then the cadence timers fire).
        await virtual_clock.elapse(0.2)
        assert sim.unsolicited_sent > 0
        # Autonomous state evolution: frequency drifted.
        assert sim._frequency > start_freq
    finally:
        await c.close()
        await sim.stop()


async def test_stall_for_suppresses_unsolicited_stream(
    virtual_clock: VirtualClock,
) -> None:
    sim = MockIcomRadio(
        single_owner=True,
        keepalive_hold_s=5.0,
        unsolicited_civ=True,
        unsolicited_interval_s=0.02,
    )
    await sim.start()
    c = await _client(0x10001)
    try:
        await _claim(sim, c)
        c.openclose(sim.civ_port, _OPENCLOSE_OPEN)
        await virtual_clock.elapse(0.1)
        assert sim.unsolicited_sent > 0

        # Stall: no frames should be emitted for the window. The stall knob
        # is a wall-clock timestamp, so while it is armed the virtual cadence
        # timers run but emit nothing — a deterministic absence.
        sim.stall_for(0.3)
        before = sim.unsolicited_sent
        await virtual_clock.elapse(0.15)
        assert sim.unsolicited_sent == before  # watchdog would trip here

        # After the stall window, the stream resumes. End the wall-clock
        # window deterministically (a zero stall expires at once) and drive
        # the cadence timers again.
        sim.stall_for(0)
        await virtual_clock.elapse(0.25)
        assert sim.unsolicited_sent > before
    finally:
        await c.close()
        await sim.stop()


async def test_drop_rate_suppresses_unsolicited_frames(
    virtual_clock: VirtualClock,
) -> None:
    sim = MockIcomRadio(
        single_owner=True,
        keepalive_hold_s=5.0,
        unsolicited_civ=True,
        unsolicited_interval_s=0.02,
        drop_rate=1.0,
    )
    await sim.start()
    c = await _client(0x10001)
    try:
        await _claim(sim, c)
        c.openclose(sim.civ_port, _OPENCLOSE_OPEN)
        await virtual_clock.elapse(0.15)
        # Every unsolicited frame is dropped -> none counted as sent.
        assert sim.unsolicited_sent == 0
    finally:
        await c.close()
        await sim.stop()


# ---------------------------------------------------------------------------
# 6. Backwards-compat: single_owner=False keeps legacy always-allow behaviour
# ---------------------------------------------------------------------------


async def test_legacy_mode_never_rejects() -> None:
    sim = MockIcomRadio()  # single_owner defaults to False
    await sim.start()
    a = await _client(0x10001)
    b = await _client(0x10002)
    try:
        civ_a, err_a = await _claim(sim, a)
        civ_b, err_b = await _claim(sim, b)
        assert err_a == _ERR_OK and civ_a == sim.civ_port
        assert err_b == _ERR_OK and civ_b == sim.civ_port  # no busy reject
        assert sim.busy_rejects == 0
        assert sim.session_held is False  # no session tracking in legacy mode
    finally:
        await a.close()
        await b.close()
        await sim.stop()


# ---------------------------------------------------------------------------
# 7. Slow near-real keepalive timing test (R3 / D7)
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_near_real_keepalive_hold_releases(virtual_clock: VirtualClock) -> None:
    """The realistic-hold case: a 1.5 s hold on the virtual clock.

    Uses a ~1.5 s hold (well under a real 45-60 s radio window but long enough
    to exercise the exact hold/release timer path with a non-accelerated
    value). The keepalive expiry is a loop timer, so elapsing the virtual
    clock fires it deterministically with no wall waiting.
    """
    hold = 1.5
    sim = MockIcomRadio(single_owner=True, keepalive_hold_s=hold)
    await sim.start()
    owner = await _client(0x10001)
    newcomer = await _client(0x10002)
    try:
        await _claim(sim, owner)
        await owner.close()  # silent drop, no graceful close

        # Inside the hold: still held, foreign owner rejected.
        await virtual_clock.elapse(hold * 0.5)
        civ_port, error = await _claim(sim, newcomer)
        assert civ_port == 0 and error == _ERR_PREV_SESSION_ACTIVE

        # After the full hold: auto-released, foreign owner connects.
        await virtual_clock.elapse(hold)
        assert sim.session_held is False
        civ_port2, error2 = await _claim(sim, newcomer)
        assert error2 == _ERR_OK and civ_port2 == sim.civ_port
    finally:
        await newcomer.close()
        await sim.stop()
