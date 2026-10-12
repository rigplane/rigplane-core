"""Simulator slice G: an unclean end leaves CI-V stream state (MOR-3418).

After a client dies without logout the radio keeps the dead client's
frontier and gap list; the next opener inherits both (packets at/behind the
frontier are ignored; the first packet past it draws requests naming every
missing number). A clean logout and reboot clear it; unclaimed it expires
(IC-7610, between ~126 s and 150 s). Whether a claimed state expires, and
per-IP vs global, are unmeasured and unmodelled. Raw-UDP pins plus one
production ``IcomRadio`` run (client code unchanged).
"""

from __future__ import annotations

import asyncio
import struct
import time

from rigplane.radio import IcomRadio

from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect
from testkit.test_simulator_civ_gap import (
    _collect,
    _open_stream,
    _parse_retransmit,
)
from testkit.test_simulator_faults import (
    _UdpClient,
    _conninfo,
    _freq_get,
    _header,
    _is_freq_reply,
    _login,
    _openclose,
)

_HOST = "127.0.0.1"

_PT_DATA = 0x00
_PT_DISCONNECT = 0x05


def _idle(seq: int) -> bytes:
    """A tracked idle keepalive: 0x10-byte type-0 control packet with a seq."""
    return bytes(_header(0x10, _PT_DATA, seq=seq))


def _close(seq: int) -> bytes:
    """Tracked OpenClose(close): same header as the open, close magic."""
    pkt = bytearray(_openclose(seq=seq))
    pkt[0x15] = 0x00
    return bytes(pkt)


def _token_remove(token: int) -> bytes:
    """Owner token-remove (0x40, requesttype=0x01) carrying ``token``."""
    pkt = _header(0x40, _PT_DATA)
    pkt[0x15] = 0x01
    struct.pack_into("<I", pkt, 0x1C, token)
    return bytes(pkt)


def _disconnect() -> bytes:
    """Control-port disconnect packet."""
    return bytes(_header(0x10, _PT_DISCONNECT))


def _login_token(reply: bytes) -> int:
    """The session token a 0x60-byte login response carries at 0x1C."""
    assert len(reply) == 0x60
    return struct.unpack_from("<I", reply, 0x1C)[0]


def _status_civ_port(reply: bytes) -> int:
    """The CI-V port a 0x50-byte status reply advertises at 0x42 (0 = refuse)."""
    assert len(reply) == 0x50
    return struct.unpack_from(">H", reply, 0x42)[0]


async def _make_client() -> _UdpClient:
    """A bound raw UDP client on loopback."""
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    return proto


async def _login_and_claim(client: _UdpClient, sim: MockIcomRadio) -> None:
    """Login plus conninfo; asserts the session was claimed for this client."""
    client.send(_login(), sim.control_port)
    await client.recv()
    client.send(_conninfo(), sim.control_port)
    assert _status_civ_port(await client.recv()) == sim.civ_port
    assert sim.session_held


async def _die_with_gap(
    sim: MockIcomRadio, *, open_seq: int, last_in_order: int, jump: int
) -> None:
    """Claim, open, process to ``last_in_order``, gap with ``jump``, die.

    The socket just closes (no logout), so the hold expires on its own.
    Returns after the gap-opening round, proving the gap predates death.
    """
    client = await _make_client()
    try:
        await _login_and_claim(client, sim)
        await _open_stream(client, sim, open_seq)
        for seq in range(open_seq + 1, last_in_order + 1):
            req = _freq_get(seq=seq)
            client.send(req, sim.civ_port)
            assert await client.recv() == req
            assert _is_freq_reply(await client.recv())
        client.send(_idle(jump), sim.civ_port)
        pair = [await client.recv() for _ in range(2)]
        missing = [(last_in_order + 1, jump - 1)]
        assert all(_parse_retransmit(p) == ("ranges", missing) for p in pair)
        assert sim.civ_gap_open is True
    finally:
        client.close()  # unclean death: no token-remove, no close, no ping


async def _wait_hold_released(sim: MockIcomRadio, budget_s: float = 5.0) -> None:
    """Wait (bounded) until the simulator holds no session."""
    deadline = time.monotonic() + budget_s
    while sim.session_held:
        assert time.monotonic() < deadline, "held session never expired"
        await asyncio.sleep(0.01)


async def _fresh_stream(sim: MockIcomRadio, *, open_seq: int) -> _UdpClient:
    """A new client that logs in, claims the free session and opens CI-V."""
    client = await _make_client()
    await _login_and_claim(client, sim)
    await _open_stream(client, sim, open_seq)
    return client


async def _assert_starts_clean(sim: MockIcomRadio) -> None:
    """A fresh opener is answered at once and draws no requests.

    Logs out cleanly at once (well within the hold) so no orphan lingers
    for whatever runs next on this simulator.
    """
    fresh = await _fresh_stream(sim, open_seq=20)
    try:
        req = _freq_get(seq=21)
        fresh.send(req, sim.civ_port)
        await _recv_data_reply(fresh, req)
        fresh.send(_close(22), sim.civ_port)
        await _wait_hold_released(sim)
        assert sim.civ_gap_open is False
        assert await _collect(fresh, 0.3) == []
    finally:
        fresh.close()


async def _recv_data_reply(
    client: _UdpClient, request: bytes, budget_s: float = 2.0
) -> None:
    """Assert ``request``'s echo and a CI-V reply arrive, skipping requests."""
    deadline = time.monotonic() + budget_s
    got_echo = got_reply = False
    while not (got_echo and got_reply):
        pkt = await client.recv(timeout=max(0.1, deadline - time.monotonic()))
        if pkt == request:
            got_echo = True
        elif _is_freq_reply(pkt):
            got_reply = True
        else:
            assert _parse_retransmit(pkt) is not None, "stray packet on the wire"
    assert time.monotonic() < deadline


def _request_runs(packets: list[bytes]) -> list[list[tuple[int, int]]]:
    """The missing runs of every retransmit request in ``packets``."""
    runs = []
    for pkt in packets:
        parsed = _parse_retransmit(pkt)
        assert parsed is not None, "non-request packet where only requests belong"
        assert parsed[0] == "ranges"
        runs.append(parsed[1])
    return runs


def _orphan_sim(**kwargs) -> MockIcomRadio:
    """A single-owner simulator with orphan state on, unless told otherwise."""
    kwargs.setdefault("single_owner", True)
    kwargs.setdefault("keepalive_hold_s", 0.25)
    kwargs.setdefault("civ_state_survives_unclean_end", True)
    return MockIcomRadio(**kwargs)


async def test_inherited_gap_ignores_behind_and_requests_missing() -> None:
    """Dead frontier 105 with gap 106-109 seeds the next opener (case a).

    Packets at/below 105 draw no echo, reply or triggered round; the jump
    to 110 draws requests naming exactly 106-109; after the fills the held
    110 and later traffic are answered in order and the loop goes silent.
    """
    sim = _orphan_sim()
    await sim.start()
    try:
        await _die_with_gap(sim, open_seq=100, last_in_order=105, jump=110)
        await _wait_hold_released(sim)
        fresh = await _fresh_stream(sim, open_seq=50)
        try:
            # The loop already asks for the inherited gap, naming only it.
            inherited = await _collect(fresh, 0.25)
            assert len(inherited) >= 2
            assert all(r == [(106, 109)] for r in _request_runs(inherited))

            # Behind the frontier: sync on a just-sent round, then silence
            # for well under the loop period (a triggered round is instant).
            for _ in range(2):
                assert _parse_retransmit(await fresh.recv()) is not None
            sent = sim.civ_retransmit_requests
            for seq in (51, 52):
                fresh.send(_freq_get(seq=seq), sim.civ_port)
            await fresh.expect_silence(0.06)
            assert sim.civ_retransmit_requests == sent

            jumped = _freq_get(seq=110)
            fresh.send(jumped, sim.civ_port)
            after_jump = await _collect(fresh, 0.3)
            assert len(after_jump) >= 2
            assert all(r == [(106, 109)] for r in _request_runs(after_jump))
            assert jumped not in after_jump

            await _collect(fresh, 0.2)  # drain pre-fill rounds in flight
            for seq in (106, 107, 108, 109):
                fresh.send(_freq_get(seq=seq), sim.civ_port)
            for seq in (106, 107, 108, 109, 110):
                await _recv_data_reply(fresh, _freq_get(seq=seq))
            assert sim.civ_gap_open is False

            req = _freq_get(seq=111)
            fresh.send(req, sim.civ_port)
            await _recv_data_reply(fresh, req)
            quiet = sim.civ_retransmit_requests
            assert await _collect(fresh, 0.35) == []
            assert sim.civ_retransmit_requests == quiet
        finally:
            fresh.close()
    finally:
        await sim.stop()


async def test_clean_logout_clears_orphan() -> None:
    """A clean logout clears a pending orphan: the next opener is clean (b).

    Per clean end (owner close, token-remove, disconnect): A dies with a
    gap and the hold expires; B claims and logs out cleanly; C is answered
    at once. The close form needs B to own the stream, so B opens first
    (consuming the orphan) and its owner-close ends the session.
    """
    sim = _orphan_sim(keepalive_hold_s=0.3)
    await sim.start()
    try:
        for logout in ("close", "token_remove", "disconnect"):
            await _die_with_gap(sim, open_seq=100, last_in_order=105, jump=110)
            await _wait_hold_released(sim)
            bro = await _make_client()
            try:
                bro.send(_login(), sim.control_port)
                token = _login_token(await bro.recv())
                bro.send(_conninfo(), sim.control_port)
                assert _status_civ_port(await bro.recv()) == sim.civ_port
                if logout == "close":
                    await _open_stream(bro, sim, 40)
                    bro.send(_close(41), sim.civ_port)
                elif logout == "token_remove":
                    bro.send(_token_remove(token), sim.control_port)
                else:
                    bro.send(_disconnect(), sim.control_port)
                await _wait_hold_released(sim)
            finally:
                bro.close()

            await _assert_starts_clean(sim)
    finally:
        await sim.stop()


async def test_reboot_clears_orphan() -> None:
    """A reboot clears the orphan: a fresh opener is answered at once (c)."""
    sim = _orphan_sim()
    await sim.start()
    try:
        await _die_with_gap(sim, open_seq=100, last_in_order=105, jump=110)
        await _wait_hold_released(sim)
        sim.reboot(at=0, down_for=0.2, busy_for=0)
        await asyncio.sleep(0.35)  # outlast the powered-off window
        await _assert_starts_clean(sim)
    finally:
        await sim.stop()


async def test_unclaimed_expiry_starts_clean() -> None:
    """Past the unclaimed expiry a fresh opener is answered at once (d)."""
    sim = _orphan_sim(orphan_civ_state_expiry_s=0.4)
    await sim.start()
    try:
        await _die_with_gap(sim, open_seq=100, last_in_order=105, jump=110)
        await _wait_hold_released(sim)
        await asyncio.sleep(0.5)  # outlast the 0.4 s orphan expiry
        await _assert_starts_clean(sim)
    finally:
        await sim.stop()


async def test_default_off_starts_clean() -> None:
    """With the knob off, an unclean end leaves nothing behind (e)."""
    sim = _orphan_sim(civ_state_survives_unclean_end=False)
    await sim.start()
    try:
        await _die_with_gap(sim, open_seq=100, last_in_order=105, jump=110)
        await _wait_hold_released(sim)
        await _assert_starts_clean(sim)
    finally:
        await sim.stop()


async def test_production_client_identity_unanswered_behind_frontier() -> None:
    """Production client after an unclean death: connect returns (f).

    The dead raw client leaves (5001, {5001-5009}); the fresh client's
    numbering starts near zero, so its identity read (CI-V 0x19 0x00)
    arrives behind the frontier and goes unanswered. Pinned behaviour:
    ``connect()`` returns but the radio is not connected (NO_RESPONSE);
    the session is held and the open seeded a tracker; the radio asked for
    exactly 5001-5009 (see ``civ_retransmit_request_log``); the client
    filled them with idles, reaching frontier 5010, while its own reads
    stay behind it, unanswered.
    """
    from rigplane.core.radio_protocol import RadioIdentityStatus

    sim = _orphan_sim(keepalive_hold_s=0.3)
    await sim.start()
    try:
        await _die_with_gap(sim, open_seq=4980, last_in_order=5000, jump=5010)
        await _wait_hold_released(sim)
        # The production client renews slowly: lengthen the hold so the
        # fresh session stays held through the asserts.
        sim.keepalive_hold_s = 30.0
        radio = IcomRadio(
            host="127.0.0.1",
            port=sim.control_port,
            username="testuser",
            password="testpass",
            model="IC-7610",
        )
        with fast_connect():
            await radio.connect()  # returns: the identity gate gets no answer
        try:
            assert radio.connected is False
            identity = radio.connection_identity
            assert identity is not None
            assert identity.status == RadioIdentityStatus.NO_RESPONSE
            assert sim.session_held  # the fresh login claimed a new session
            assert sim.openclose_open_count >= 1  # its open seeded a tracker
            # Only an inherited tracker the client filled with idles can
            # sit at 5010: the fresh numbering alone cannot reach that far.
            assert any(
                runs == ((5001, 5009),) for _, runs in sim.civ_retransmit_request_log
            )
            frontiers = [t.next_expected or 0 for t in sim._civ_gap.values()]
            assert max(frontiers, default=0) >= 5010
        finally:
            await asyncio.wait_for(radio.disconnect(), 10.0)
    finally:
        await sim.stop()


async def test_inherited_one_fill_per_round() -> None:
    """Opt-in: an inherited tracker accepts one fill per 0.1 s round.

    The burst 107-109 accepts only the first: requests still name 108-109
    and neither is echoed. Resent on later rounds, both land, and 106 plus
    the drain release everything.
    """
    sim = _orphan_sim(civ_inherited_fills_per_round=1)
    await sim.start()
    try:
        await _die_with_gap(sim, open_seq=100, last_in_order=105, jump=110)
        await _wait_hold_released(sim)
        fresh = await _fresh_stream(sim, open_seq=50)
        try:
            await _collect(fresh, 0.25)  # drain loop rounds in flight
            jumped = _freq_get(seq=110)
            fresh.send(jumped, sim.civ_port)
            await _collect(fresh, 0.15)  # the jump's round in flight
            # Sync on a just-sent round so the fills land in one window.
            for _ in range(2):
                assert _parse_retransmit(await fresh.recv()) is not None
            for seq in (107, 108, 109):
                fresh.send(_freq_get(seq=seq), sim.civ_port)
            window = await _collect(fresh, 0.35)
            assert len(window) >= 2
            assert any(r == [(106, 106), (108, 109)] for r in _request_runs(window))
            assert all(r != [(106, 106)] for r in _request_runs(window)), (
                "only one fill per round may be accepted"
            )
            assert _freq_get(seq=108) not in window
            assert _freq_get(seq=109) not in window

            await asyncio.sleep(0.15)
            fresh.send(_freq_get(seq=108), sim.civ_port)
            await asyncio.sleep(0.15)
            fresh.send(_freq_get(seq=109), sim.civ_port)
            await asyncio.sleep(0.15)
            await _collect(fresh, 0.15)  # drain pre-fill rounds in flight
            # Only 106 is (re)sent: it advances the frontier and the drain
            # releases the already-held 107-110 in order.
            fresh.send(_freq_get(seq=106), sim.civ_port)
            for seq in (106, 107, 108, 109, 110):
                await _recv_data_reply(fresh, _freq_get(seq=seq))
            assert sim.civ_gap_open is False
        finally:
            fresh.close()
    finally:
        await sim.stop()


async def test_inherited_drop_unanswered_range() -> None:
    """Opt-in: an inherited range with no fills is dropped after N rounds.

    With ``civ_inherited_drop_unanswered_rounds=3`` the never-filled 106-109
    stops being requested after ~0.3 s then in-order traffic resumes cleanly.
    """
    sim = _orphan_sim(civ_inherited_drop_unanswered_rounds=3)
    await sim.start()
    try:
        await _die_with_gap(sim, open_seq=100, last_in_order=105, jump=110)
        await _wait_hold_released(sim)
        fresh = await _fresh_stream(sim, open_seq=50)
        try:
            first = await _collect(fresh, 0.15)
            assert len(first) >= 2
            assert all(r == [(106, 109)] for r in _request_runs(first))
            await asyncio.sleep(0.4)  # three 0.1 s rounds with no fills
            await _collect(fresh, 0.15)  # drain pre-drop rounds in flight
            quiet = sim.civ_retransmit_requests
            assert await _collect(fresh, 0.35) == []
            assert sim.civ_retransmit_requests == quiet
            assert sim.civ_gap_open is False

            # In-order traffic resumes the stream: 106-109 advance the
            # frontier (held packets are not inherited, so 110 is sent
            # anew) and later traffic is answered at once.
            for seq in (106, 107, 108, 109, 110):
                fresh.send(_freq_get(seq=seq), sim.civ_port)
                await _recv_data_reply(fresh, _freq_get(seq=seq))
            req = _freq_get(seq=111)
            fresh.send(req, sim.civ_port)
            await _recv_data_reply(fresh, req)
        finally:
            fresh.close()
    finally:
        await sim.stop()
