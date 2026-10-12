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


def _renewal(token: int, inner_seq: int = 0x0007) -> bytes:
    """A 0x40 client token-renewal packet ([0x14]=0x01, [0x15]=0x05)."""
    pkt = _header(0x40, _PT_DATA)
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
) -> None:
    """Pin every recorded byte of a 0x40 renewal answer."""
    assert len(reply) == 0x40
    assert struct.unpack_from("<H", reply, 0x04)[0] == 0  # type 0
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
    client.send(_renewal(token, inner_seq=0x1234), server.control_port)
    reply = await client.recv()
    _assert_renew_reply(
        reply,
        server,
        request_token=token,
        inner_seq=0x1234,
        response=_RESP_OK,
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


async def test_newer_login_retires_old_token(server, client) -> None:
    """A newer login replaces the token: old rejected, new accepted."""
    old_token = await _login_get_token(client, server)
    new_token = await _login_get_token(client, server)
    assert new_token != old_token

    client.send(_renewal(old_token), server.control_port)
    old_reply = await client.recv()
    _assert_renew_reply(
        old_reply,
        server,
        request_token=old_token,
        inner_seq=0x0007,
        response=_RESP_REJECT,
    )

    client.send(_renewal(new_token), server.control_port)
    new_reply = await client.recv()
    _assert_renew_reply(
        new_reply,
        server,
        request_token=new_token,
        inner_seq=0x0007,
        response=_RESP_OK,
    )


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
