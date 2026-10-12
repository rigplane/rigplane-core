"""Wire-level unit tests for the simulator's opt-in stateful PTT hook.

MOR-3251 part 1. Bare asyncio UDP clients against a real ``MockIcomRadio``
— no production client involved — pinning the hook's contract:

* default off is byte-for-byte unchanged: ``1C 00`` GET answers OFF and
  every ``1C 00`` SET NAKs (the MOR-1217 note the dispatcher carries);
* ``stateful_ptt=True``: SET ``1C 00 01`` / ``1C 00 00`` answers FB and
  flips the simulated state, GET answers the current state, and any other
  ``1C 00`` payload still NAKs;
* the LAN CI-V echo precedes the FB reply, as it does for every request;
* ``ptt_on_session_loss``: ``"release"`` unkeys at every session end,
  ``"hold"`` keeps PTT on until a client unkeys.
"""

from __future__ import annotations

import asyncio
import struct
import time
from collections.abc import AsyncGenerator

import pytest

from rigplane_testkit.icom import MockIcomRadio

from testkit.test_simulator_faults import (
    _UdpClient,
    _conninfo,
    _header,
    _login,
    _openclose,
    _recv_reply,
)

_HOST = "127.0.0.1"
_PT_DATA = 0x00

# CI-V frame tails after the "to 0x98 / from 0xE0" addressing prefix.
_PTT_GET_FRAME = b"\xfe\xfe\x98\xe0\x1c\x00\xfd"
_PTT_ON_FRAME = b"\xfe\xfe\x98\xe0\x1c\x00\x01\xfd"
_PTT_OFF_FRAME = b"\xfe\xfe\x98\xe0\x1c\x00\x00\xfd"
_PTT_BAD_FRAME = b"\xfe\xfe\x98\xe0\x1c\x00\x02\xfd"

# Reply frames the radio builds (radio 0x98 -> controller 0xE0).
_FB_FRAME = b"\xfe\xfe\xe0\x98\xfb\xfd"
_FA_FRAME = b"\xfe\xfe\xe0\x98\xfa\xfd"


def _civ_packet(frame: bytes, seq: int) -> bytes:
    """Wrap one CI-V frame in a DATA header for the CI-V port."""
    pkt = _header(0x15 + len(frame), _PT_DATA, seq=seq)
    pkt[0x10] = 0xC1
    struct.pack_into("<H", pkt, 0x11, len(frame))
    pkt[0x15:] = frame
    return bytes(pkt)


def _is_ptt_get_reply(pkt: bytes, state_byte: int) -> bool:
    civ = pkt[0x15:]
    return civ == b"\xfe\xfe\xe0\x98\x1c\x00" + bytes([state_byte]) + b"\xfd"


class _Seq:
    """Client-side tracked-packet numbering (the open is the first)."""

    def __init__(self) -> None:
        self.next = 0

    def take(self) -> int:
        value = self.next
        self.next += 1
        return value


async def _open_stream(client: _UdpClient, server: MockIcomRadio) -> _Seq:
    """Login, claim the session, and open the CI-V stream."""
    client.send(_login(), server.control_port)
    await client.recv()
    client.send(_conninfo(), server.control_port)
    await client.recv()
    seq = _Seq()
    client.send(_openclose(seq=seq.take()), server.civ_port)
    await client.recv()  # the stream marker the open triggers
    return seq


async def _key(
    client: _UdpClient, server: MockIcomRadio, seq: _Seq, *, on: bool
) -> bytes:
    """Send one PTT SET and return the reply with the LAN echo skipped."""
    request = _civ_packet(_PTT_ON_FRAME if on else _PTT_OFF_FRAME, seq.take())
    client.send(request, server.civ_port)
    return await _recv_reply(client, request)


async def _ptt_get(client: _UdpClient, server: MockIcomRadio, seq: _Seq) -> bytes:
    request = _civ_packet(_PTT_GET_FRAME, seq.take())
    client.send(request, server.civ_port)
    return await _recv_reply(client, request)


@pytest.fixture
async def client() -> AsyncGenerator[_UdpClient]:
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    yield proto
    proto.close()


async def _server(**kwargs: object) -> MockIcomRadio:
    server = MockIcomRadio(**kwargs)  # type: ignore[arg-type]
    await server.start()
    return server


# ---------------------------------------------------------------------------
# Default off — byte-for-byte the pre-hook behaviour
# ---------------------------------------------------------------------------


async def test_default_off_get_answers_off_and_set_naks(client: _UdpClient) -> None:
    server = await _server()
    try:
        seq = await _open_stream(client, server)

        reply = await _ptt_get(client, server, seq)
        assert _is_ptt_get_reply(reply, 0x00)
        assert server.ptt_on is False

        reply = await _key(client, server, seq, on=True)
        assert reply[0x15:] == _FA_FRAME
        assert server.ptt_on is False
        assert server.ptt_transitions == []
        assert server.ptt_set_writes == []
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# stateful_ptt=True — SET/GET round trip
# ---------------------------------------------------------------------------


async def test_stateful_set_and_get_round_trip(client: _UdpClient) -> None:
    server = await _server(stateful_ptt=True)
    try:
        seq = await _open_stream(client, server)

        reply = await _ptt_get(client, server, seq)
        assert _is_ptt_get_reply(reply, 0x00)

        reply = await _key(client, server, seq, on=True)
        assert reply[0x15:] == _FB_FRAME
        assert server.ptt_on is True
        reply = await _ptt_get(client, server, seq)
        assert _is_ptt_get_reply(reply, 0x01)

        reply = await _key(client, server, seq, on=False)
        assert reply[0x15:] == _FB_FRAME
        assert server.ptt_on is False
        reply = await _ptt_get(client, server, seq)
        assert _is_ptt_get_reply(reply, 0x00)

        assert [state for _, state in server.ptt_transitions] == [True, False]
        times = [t for t, _ in server.ptt_transitions]
        assert times == sorted(times)
        assert [on for _, on in server.ptt_set_writes] == [True, False]
    finally:
        await server.stop()


async def test_stateful_other_ptt_payload_still_naks(client: _UdpClient) -> None:
    server = await _server(stateful_ptt=True)
    try:
        seq = await _open_stream(client, server)
        request = _civ_packet(_PTT_BAD_FRAME, seq.take())
        client.send(request, server.civ_port)
        reply = await _recv_reply(client, request)
        assert reply[0x15:] == _FA_FRAME
        assert server.ptt_on is False
    finally:
        await server.stop()


async def test_stateful_set_echo_precedes_the_fb(client: _UdpClient) -> None:
    server = await _server(stateful_ptt=True)
    try:
        seq = await _open_stream(client, server)
        request = _civ_packet(_PTT_ON_FRAME, seq.take())
        client.send(request, server.civ_port)
        echo = await client.recv()
        assert echo == request  # recorded IC-7610 LAN behaviour: echo first
        reply = await client.recv()
        assert reply[0x15:] == _FB_FRAME
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# ptt_on_session_loss — "release" unkeys when the session ends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("loss_event", "release_reason"),
    [
        pytest.param(
            lambda server: server.drop_session(at=0.05, notice=False),
            "drop_session",
            id="drop-without-notice",
        ),
        pytest.param(
            lambda server: server.drop_session(at=0.05, notice=True),
            "drop_session",
            id="drop-with-notice",
        ),
        pytest.param(
            lambda server: server.free_session("manual"),
            "manual",
            id="free-session",
        ),
        pytest.param(
            lambda server: server.reboot(at=0.05, down_for=0.2, busy_for=0.1),
            "reboot",
            id="reboot",
        ),
    ],
)
async def test_release_unkeys_at_session_end(
    client: _UdpClient, loss_event, release_reason: str
) -> None:
    server = await _server(
        single_owner=True,
        keepalive_hold_s=5.0,
        stateful_ptt=True,
        ptt_on_session_loss="release",
    )
    try:
        seq = await _open_stream(client, server)
        assert server.session_held
        await _key(client, server, seq, on=True)
        assert server.ptt_on is True

        loss_event(server)
        deadline = time.monotonic() + 1.0
        while server.session_held and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert not server.session_held
        assert server.last_release_reason == release_reason
        assert server.ptt_on is False
        assert [state for _, state in server.ptt_transitions] == [True, False]
        # The session-end unkey is not a CI-V write: no client frame did it.
        assert [on for _, on in server.ptt_set_writes] == [True]
    finally:
        await server.stop()


async def test_release_unkeys_at_keepalive_timeout(client: _UdpClient) -> None:
    server = await _server(
        single_owner=True,
        keepalive_hold_s=0.25,
        stateful_ptt=True,
        ptt_on_session_loss="release",
    )
    try:
        seq = await _open_stream(client, server)
        await _key(client, server, seq, on=True)
        assert server.ptt_on is True

        # Stop pinging: the radio's keepalive timer ends the session.
        deadline = time.monotonic() + 2.0
        while server.session_held and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert server.last_release_reason == "keepalive_timeout"
        assert server.ptt_on is False
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# ptt_on_session_loss — "hold" keeps PTT on until a client unkeys
# ---------------------------------------------------------------------------


async def test_hold_keeps_ptt_across_the_drop_until_a_client_unkeys(
    client: _UdpClient,
) -> None:
    server = await _server(
        single_owner=True,
        keepalive_hold_s=5.0,
        stateful_ptt=True,
        ptt_on_session_loss="hold",
    )
    try:
        seq = await _open_stream(client, server)
        await _key(client, server, seq, on=True)
        assert server.ptt_on is True

        server.drop_session(at=0.05, notice=False)
        deadline = time.monotonic() + 1.0
        while server.session_held and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert not server.session_held
        assert server.ptt_on is True  # held: only a client unkey may clear it
        assert [state for _, state in server.ptt_transitions] == [True]

        # A fresh login re-claims the session; its unkey SET clears PTT.
        client.send(_login(), server.control_port)
        await client.recv()
        client.send(_conninfo(), server.control_port)
        await client.recv()
        seq = _Seq()
        client.send(_openclose(seq=seq.take()), server.civ_port)
        await client.recv()
        reply = await _key(client, server, seq, on=False)
        assert reply[0x15:] == _FB_FRAME
        assert server.ptt_on is False
        assert [state for _, state in server.ptt_transitions] == [True, False]
    finally:
        await server.stop()
