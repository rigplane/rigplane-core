"""Wire-level tests: token-renewal answers (live / held / retired) and CI-V standby.

Bare asyncio UDP clients against a real ``MockIcomRadio`` — no production
client involved — reusing the ``_UdpClient`` and packet builders of
``tests/testkit/test_simulator_faults.py``.

Recorded IC-7610 behaviour (MOR-3349 evidence): a client token renewal is a
0x40 control packet with [0x14]=0x01, [0x15]=0x05, the token at 0x1C (LE u32)
and an inner sequence at 0x16..0x17. The radio answers with a 0x40 control
packet, type 0, [0x14]=0x02, [0x15]=0x05, the request's token echoed at 0x1C,
the inner sequence echoed, [0x24..0x25]=07 98, sender/receiver ids swapped,
every other byte in 0x10..0x3f zero except [0x13]=0x30, and the response in
[0x30..0x33]: ``00000000`` for a live or still-held session, ``ffffffff``
for a session that has expired or is otherwise gone (exactly one reply).
A renewal with the held token also extends the hold window.

``civ_standby`` is a simulator scenario, NOT recorded radio behaviour.
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from rigplane_testkit.icom import MockIcomRadio
from testkit.test_simulator_faults import (
    _CLIENT_ID,
    _freq_get,
    _header,
    _is_freq_reply,
    _login,
    _openclose,
    _ping,
    _recv_reply,
    _UdpClient,
)

_HOST = "127.0.0.1"
_PT_DATA = 0x00

_TOKEN_RENEW = 0x05
_TOKEN_REMOVE = 0x01

_RESP_OK = 0x00000000
_RESP_REJECT = 0xFFFFFFFF


def _renewal(token: int, inner_seq: int = 0x0007, seq: int = 0) -> bytes:
    """A 0x40 client token-renewal packet ([0x14]=0x01, [0x15]=0x05)."""
    pkt = _header(0x40, _PT_DATA, seq=seq)
    pkt[0x14] = 0x01
    pkt[0x15] = _TOKEN_RENEW
    struct.pack_into(">H", pkt, 0x16, inner_seq)
    struct.pack_into("<I", pkt, 0x1C, token)
    return bytes(pkt)


def _token_remove() -> bytes:
    """A 0x40 graceful-withdraw packet ([0x14]=0x01, [0x15]=0x01)."""
    pkt = _header(0x40, _PT_DATA)
    pkt[0x14] = 0x01
    pkt[0x15] = _TOKEN_REMOVE
    return bytes(pkt)


def _disconnect() -> bytes:
    """A 0x10 disconnect packet (type 0x05)."""
    return bytes(_header(0x10, 0x05))


async def _login_get_token(client: _UdpClient, server: MockIcomRadio) -> int:
    """Login and return the token the radio issued (LE u32 at 0x1C)."""
    client.send(_login(), server.control_port)
    reply = await client.recv()
    assert len(reply) == 0x60
    return struct.unpack_from("<I", reply, 0x1C)[0]


def _assert_renew_reply(
    reply: bytes,
    server: MockIcomRadio,
    *,
    request_token: int,
    inner_seq: int,
    response: int,
    seq: int = 0,
) -> None:
    """Pin every recorded byte of a 0x40 renewal answer."""
    assert len(reply) == 0x40
    assert struct.unpack_from("<I", reply, 0x00)[0] == 0x40  # length word
    assert struct.unpack_from("<H", reply, 0x04)[0] == 0  # type 0
    assert struct.unpack_from("<H", reply, 0x06)[0] == seq  # header seq echo
    assert struct.unpack_from("<I", reply, 0x08)[0] == server.radio_id
    assert struct.unpack_from("<I", reply, 0x0C)[0] == _CLIENT_ID
    assert reply[0x13] == 0x30
    assert reply[0x14] == 0x02
    assert reply[0x15] == _TOKEN_RENEW
    assert reply[0x16:0x18] == struct.pack(">H", inner_seq)
    assert struct.unpack_from("<I", reply, 0x1C)[0] == request_token
    assert reply[0x24:0x26] == bytes((0x07, 0x98))
    assert struct.unpack_from("<I", reply, 0x30)[0] == response
    pinned = {
        0x13,
        0x14,
        0x15,
        0x16,
        0x17,
        0x1C,
        0x1D,
        0x1E,
        0x1F,
        0x24,
        0x25,
        0x30,
        0x31,
        0x32,
        0x33,
    }
    for offset in range(0x10, 0x40):
        if offset not in pinned:
            assert reply[offset] == 0, f"byte {offset:#04x}"


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
# Renewal reply layout: live vs retired
# ---------------------------------------------------------------------------


async def test_live_renewal_reply_layout(server, client) -> None:
    """A renewal with the live token is answered 00000000, every byte pinned."""
    token = await _login_get_token(client, server)
    client.send(_renewal(token, inner_seq=0x1234, seq=0x2222), server.control_port)
    reply = await client.recv()
    _assert_renew_reply(
        reply,
        server,
        request_token=token,
        inner_seq=0x1234,
        response=_RESP_OK,
        seq=0x2222,
    )


async def test_retired_token_renewal_rejected_exactly_once(server, client) -> None:
    """After a session drop the old token gets one ffffffff reply, then silence."""
    token = await _login_get_token(client, server)
    server.drop_session(at=0.0, notice=False)
    await asyncio.sleep(0.05)
    assert server.token != token  # the drop rotated the token

    client.send(_renewal(token, inner_seq=0x0009), server.control_port)
    reply = await client.recv()
    _assert_renew_reply(
        reply,
        server,
        request_token=token,
        inner_seq=0x0009,
        response=_RESP_REJECT,
    )
    # Exactly one reply per renewal: nothing else follows.
    await client.expect_silence(0.3)


async def test_token_remove_retires_token(server, client) -> None:
    """A graceful withdraw ends the token epoch: the old token is rejected."""
    token = await _login_get_token(client, server)
    client.send(_token_remove(), server.control_port)
    await asyncio.sleep(0.05)

    client.send(_renewal(token), server.control_port)
    reply = await client.recv()
    _assert_renew_reply(
        reply,
        server,
        request_token=token,
        inner_seq=0x0007,
        response=_RESP_REJECT,
    )


async def test_second_login_does_not_retire_token(server, client) -> None:
    """A second login is not a new session: the issued token does not rotate."""
    first_token = await _login_get_token(client, server)
    second_token = await _login_get_token(client, server)
    assert second_token == first_token

    client.send(_renewal(first_token), server.control_port)
    reply = await client.recv()
    _assert_renew_reply(
        reply,
        server,
        request_token=first_token,
        inner_seq=0x0007,
        response=_RESP_OK,
    )


async def _second_client() -> _UdpClient:
    """A second bare UDP client: a different socket, same client id."""
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    return proto


async def test_second_login_keeps_single_owner_session() -> None:
    """A second client's login does not retire the holder's token (MOR-3349).

    The holder's renewal stays 00000000 while the session is held, and the
    second client's conninfo is busy-rejected: the radio rejects the second
    client instead of ending the holder's session.
    """
    sim = MockIcomRadio(single_owner=True, keepalive_hold_s=5.0)
    await sim.start()
    loop = asyncio.get_running_loop()
    first = _UdpClient()
    await loop.create_datagram_endpoint(lambda: first, local_addr=(_HOST, 0))
    second = await _second_client()
    try:
        first.send(_login(), sim.control_port)
        login = await first.recv()
        token = struct.unpack_from("<I", login, 0x1C)[0]
        pkt = _header(0x90, _PT_DATA)
        pkt[0x15] = 0x03
        first.send(bytes(pkt), sim.control_port)
        await first.recv()  # status reply: session claimed
        assert sim.session_held is True

        second.send(_login(), sim.control_port)
        second_login = await second.recv()
        assert struct.unpack_from("<I", second_login, 0x1C)[0] == token

        first.send(_renewal(token), sim.control_port)
        reply = await first.recv()
        _assert_renew_reply(
            reply,
            sim,
            request_token=token,
            inner_seq=0x0007,
            response=_RESP_OK,
        )
        assert sim.session_held is True

        pkt = _header(0x90, _PT_DATA)
        pkt[0x15] = 0x03
        second.send(bytes(pkt), sim.control_port)
        busy = await second.recv()
        assert struct.unpack_from("<I", busy, 0x30)[0] == 0xFFFFFFFF
    finally:
        first.close()
        second.close()
        await sim.stop()


async def test_first_conninfo_accepted_after_second_login(server, client) -> None:
    """In default mode the first client's conninfo survives a second login."""
    first_token = await _login_get_token(client, server)
    await _login_get_token(client, server)  # second login: no new epoch

    pkt = _header(0x90, _PT_DATA)
    pkt[0x15] = 0x03
    struct.pack_into("<I", pkt, 0x1C, first_token)
    client.send(bytes(pkt), server.control_port)
    status = await client.recv()
    assert struct.unpack_from(">H", status, 0x42)[0] == server.civ_port


async def test_session_turnover_retires_previous_token() -> None:
    """Ending one session and granting the next retires the previous token."""
    sim = MockIcomRadio(single_owner=True, keepalive_hold_s=5.0)
    await sim.start()
    loop = asyncio.get_running_loop()
    first = _UdpClient()
    await loop.create_datagram_endpoint(lambda: first, local_addr=(_HOST, 0))
    second = await _second_client()
    try:
        pkt = _header(0x90, _PT_DATA)
        pkt[0x15] = 0x03
        first.send(bytes(pkt), sim.control_port)
        await first.recv()  # status reply: session granted to the first owner
        granted_token = sim.token

        first.send(_disconnect(), sim.control_port)
        await asyncio.sleep(0.05)
        assert sim.session_held is False

        second.send(bytes(pkt), sim.control_port)
        await second.recv()  # status reply: session granted to the second owner
        assert sim.token != granted_token  # the previous token epoch ended

        first.send(_renewal(granted_token), sim.control_port)
        old_reply = await first.recv()
        _assert_renew_reply(
            old_reply,
            sim,
            request_token=granted_token,
            inner_seq=0x0007,
            response=_RESP_REJECT,
        )

        second.send(_renewal(sim.token), sim.control_port)
        new_reply = await second.recv()
        _assert_renew_reply(
            new_reply,
            sim,
            request_token=sim.token,
            inner_seq=0x0007,
            response=_RESP_OK,
        )
    finally:
        first.close()
        second.close()
        await sim.stop()


async def test_hold_expiry_retires_token() -> None:
    """Once the single-owner hold lapses, the token no longer names a session."""
    sim = MockIcomRadio(single_owner=True, keepalive_hold_s=0.2)
    await sim.start()
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    try:
        token = sim.token
        pkt = _header(0x90, _PT_DATA)
        pkt[0x15] = 0x03
        proto.send(bytes(pkt), sim.control_port)
        await proto.recv()  # status reply: session claimed
        assert sim.session_held is True

        await asyncio.sleep(0.35)  # past the hold, no traffic
        assert sim.session_held is False
        assert sim.token != token  # the expired epoch was retired

        proto.send(_renewal(token), sim.control_port)
        reply = await proto.recv()
        _assert_renew_reply(
            reply,
            sim,
            request_token=token,
            inner_seq=0x0007,
            response=_RESP_REJECT,
        )
    finally:
        proto.close()
        await sim.stop()


async def test_held_renewal_extends_hold() -> None:
    """A renewal with the held token restarts the single-owner hold window."""
    sim = MockIcomRadio(single_owner=True, keepalive_hold_s=0.4)
    await sim.start()
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    try:
        pkt = _header(0x90, _PT_DATA)
        pkt[0x15] = 0x03
        proto.send(bytes(pkt), sim.control_port)
        await proto.recv()  # status reply: session claimed

        await asyncio.sleep(0.25)  # inside the hold
        proto.send(_renewal(sim.token), sim.control_port)
        reply = await proto.recv()
        _assert_renew_reply(
            reply,
            sim,
            request_token=sim.token,
            inner_seq=0x0007,
            response=_RESP_OK,
        )

        # Past the original 0.4 s hold, the renewed session is still held.
        await asyncio.sleep(0.25)
        assert sim.session_held is True
    finally:
        proto.close()
        await sim.stop()


# ---------------------------------------------------------------------------
# civ_standby: a simulator scenario, NOT recorded radio behaviour
# ---------------------------------------------------------------------------


async def test_foreign_token_remove_is_noop() -> None:
    """A token-remove from a non-holder retires nothing (single-owner)."""
    sim = MockIcomRadio(single_owner=True, keepalive_hold_s=5.0)
    await sim.start()
    loop = asyncio.get_running_loop()
    first = _UdpClient()
    await loop.create_datagram_endpoint(lambda: first, local_addr=(_HOST, 0))
    second = await _second_client()
    try:
        token = sim.token
        pkt = _header(0x90, _PT_DATA)
        pkt[0x15] = 0x03
        first.send(bytes(pkt), sim.control_port)
        await first.recv()  # status reply: session granted to the first owner
        assert sim.session_held is True

        second.send(_token_remove(), sim.control_port)
        await asyncio.sleep(0.05)
        assert sim.token == token  # no new epoch: the foreign remove was a no-op

        first.send(_renewal(token), sim.control_port)
        reply = await first.recv()
        _assert_renew_reply(
            reply,
            sim,
            request_token=token,
            inner_seq=0x0007,
            response=_RESP_OK,
        )
        assert sim.session_held is True

        first.send(_token_remove(), sim.control_port)
        await asyncio.sleep(0.05)
        assert sim.session_held is False
        assert sim.token != token  # the owner's own remove ends the epoch

        first.send(_renewal(token), sim.control_port)
        gone = await first.recv()
        _assert_renew_reply(
            gone,
            sim,
            request_token=token,
            inner_seq=0x0007,
            response=_RESP_REJECT,
        )
    finally:
        first.close()
        second.close()
        await sim.stop()


async def test_foreign_source_renewal_extends_hold() -> None:
    """A renewal with the held token extends the hold whatever its source."""
    sim = MockIcomRadio(single_owner=True, keepalive_hold_s=0.4)
    await sim.start()
    loop = asyncio.get_running_loop()
    first = _UdpClient()
    await loop.create_datagram_endpoint(lambda: first, local_addr=(_HOST, 0))
    second = await _second_client()
    try:
        pkt = _header(0x90, _PT_DATA)
        pkt[0x15] = 0x03
        first.send(bytes(pkt), sim.control_port)
        await first.recv()  # status reply: session claimed

        await asyncio.sleep(0.25)  # inside the hold
        second.send(_renewal(sim.token), sim.control_port)
        reply = await second.recv()
        _assert_renew_reply(
            reply,
            sim,
            request_token=sim.token,
            inner_seq=0x0007,
            response=_RESP_OK,
        )

        # Past the original 0.4 s hold, the foreign-source renewal kept it held.
        await asyncio.sleep(0.25)
        assert sim.session_held is True
    finally:
        first.close()
        second.close()
        await sim.stop()


async def test_civ_standby_silences_data_only(server, client) -> None:
    """While standby is on, CI-V DATA gets no echo and no reply.

    CI-V-port pings stay answered, and the control port keeps answering
    pings and renewals. Turning standby off restores CI-V answers.
    """
    token = await _login_get_token(client, server)

    server.civ_standby(True)
    try:
        request = _freq_get()
        client.send(request, server.civ_port)
        await client.expect_silence(0.3)  # no echo, no reply

        client.send(_ping(), server.civ_port)
        civ_ping = await client.recv()
        assert len(civ_ping) == 0x15 and civ_ping[0x10] == 0x01

        client.send(_ping(), server.control_port)
        ctrl_ping = await client.recv()
        assert len(ctrl_ping) == 0x15 and ctrl_ping[0x10] == 0x01

        client.send(_renewal(token), server.control_port)
        renew = await client.recv()
        assert len(renew) == 0x40 and renew[0x15] == _TOKEN_RENEW
    finally:
        server.civ_standby(False)

    request = _freq_get()
    client.send(request, server.civ_port)
    assert _is_freq_reply(await _recv_reply(client, request))


async def test_civ_standby_tracks_sequence_while_silent(server, client) -> None:
    """Standby swallows CI-V DATA but still advances sequence tracking.

    The stream is opened, standby is turned on, tracked packets cross it in
    silence, then standby is turned off: the next request is answered and no
    retransmit request was sent.
    """
    before = server.civ_retransmit_requests
    client.send(_openclose(seq=0), server.civ_port)
    await client.recv()  # unsolicited CI-V frame on open

    server.civ_standby(True)
    try:
        for seq in (1, 2, 3):
            request = _freq_get(seq=seq)
            client.send(request, server.civ_port)
            await client.expect_silence(0.3)  # no echo, no reply
    finally:
        server.civ_standby(False)

    request = _freq_get(seq=4)
    client.send(request, server.civ_port)
    assert _is_freq_reply(await _recv_reply(client, request))
    assert server.civ_retransmit_requests == before
