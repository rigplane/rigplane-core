"""Wire-level tests for the drop_session event and the recorded power-off notice.

Bare asyncio UDP clients against a real ``MockIcomRadio`` — no production
client involved — reusing the ``_UdpClient`` and packet builders of
``tests/testkit/test_simulator_faults.py``. The notice itself is
additionally driven through the production packet path: an
``IcomTransport`` wired the way ``ControlPhaseRuntime`` leaves the control
transport after connect must forward the notice bytes it received to
``_on_conninfo_notice``, which classifies them as "session free" (busy=0),
per MOR-3315.
"""

from __future__ import annotations

import asyncio
import struct
import time
from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import pytest

from rigplane.core.transport import IcomTransport
from rigplane.radio import IcomRadio

from rigplane_testkit.icom import MockIcomRadio
from testkit.test_simulator_faults import (
    _UdpClient,
    _ayt,
    _conninfo,
    _freq_get,
    _header,
    _is_freq_reply,
    _login,
    _openclose,
    _ping,
    _recv_reply,
)

_HOST = "127.0.0.1"

# Packet type codes beyond what test_simulator_faults mirrors.
_PT_ARE_YOU_READY = 0x06
_PT_I_AM_HERE = 0x04
_PT_PING = 0x07

_ERR_PREV_SESSION_ACTIVE = 0xFFFFFFFF


def _ayr() -> bytes:
    return bytes(_header(0x10, _PT_ARE_YOU_READY))


async def _login_and_claim(client: _UdpClient, server: MockIcomRadio) -> None:
    """Login + conninfo, which claims the session in single-owner mode."""
    client.send(_login(), server.control_port)
    await client.recv()
    client.send(_conninfo(), server.control_port)
    await client.recv()


def _conninfo_declaring(civ_local_port: int) -> bytes:
    """Client conninfo declaring its local CI-V port.

    The big-endian u32 at 0x7C is exactly where ``core/auth.py``:
    ``build_conninfo_packet`` packs ``civ_local_port`` — the bound local
    port the production client opens its CI-V stream from
    (``runtime/_control_phase.py``: ``_connect_once`` / ``soft_reconnect``).
    """
    pkt = bytearray(_conninfo())
    struct.pack_into(">I", pkt, 0x7C, civ_local_port)
    return bytes(pkt)


def _assert_notice_reaches_production_callback(notice: bytes) -> None:
    """Drive the notice through the production packet path, not just the parser.

    Wires an ``IcomTransport`` the way ``ControlPhaseRuntime`` leaves the
    control transport after connect — ``_discard_data_packets = True`` and
    ``_conninfo_notice_callback = self._on_conninfo_notice``
    (``runtime/_control_phase.py``) — then feeds the bytes received from
    the simulator to ``IcomTransport._handle_packet``: only a ptype-0x00
    packet of this length reaches the notice callback
    (``_handle_data_packet``), so a mistyped notice fails here. The
    production parser must then read busy=0 ("session free") and request
    CI-V recovery.
    """
    radio = IcomRadio("127.0.0.1", username="u", password="p", model="IC-7610")
    radio._civ_runtime.request_recovery_now = MagicMock()
    forwarded: list[bytes] = []

    def notice_callback(data: bytes) -> None:
        forwarded.append(data)
        radio._control_phase._on_conninfo_notice(data)

    transport = IcomTransport()
    transport._discard_data_packets = True
    transport._conninfo_notice_callback = notice_callback
    transport._handle_packet(notice)

    assert forwarded == [notice]  # the transport routed it to the callback
    radio._civ_runtime.request_recovery_now.assert_called_once_with(
        "radio reported the LAN session free (conninfo busy=0)"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def server() -> AsyncGenerator[MockIcomRadio]:
    s = MockIcomRadio(single_owner=True, keepalive_hold_s=5.0)
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
async def client() -> AsyncGenerator[_UdpClient]:
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    yield proto
    proto.close()


async def test_drop_session_sends_one_notice_classified_session_free(server, client):
    """(a) One unsolicited 0x90 notice ~0.1 s after the drop; the production
    parser classifies it as "session free"."""
    await _login_and_claim(client, server)
    assert server.session_held

    t0 = time.monotonic()
    server.drop_session(at=0.2)
    notice = await client.recv(timeout=2.0)
    dt = time.monotonic() - t0

    assert len(notice) == 0x90
    assert notice[0x15] == 0x00  # radio-originated request type
    assert struct.unpack_from("<I", notice, 0x60)[0] == 0  # busy=0 → free
    # The drop fires at +0.2 s and the notice follows ~0.1 s later.
    assert 0.25 <= dt <= 0.4
    # Exactly one notice.
    await client.expect_silence(0.4)

    _assert_notice_reaches_production_callback(notice)
    assert not server.session_held
    assert server.last_release_reason == "drop_session"


async def test_drop_session_without_notice_sends_no_notice(server, client):
    await _login_and_claim(client, server)
    server.drop_session(at=0.2, notice=False)
    await client.expect_silence(0.6)
    assert not server.session_held


async def test_pings_answered_on_both_ports_after_drop(server, client):
    """(b) Recorded: the radio's ports keep answering pings after the drop."""
    await _login_and_claim(client, server)
    server.drop_session(at=0.2, notice=False)
    await asyncio.sleep(0.5)  # past the drop

    client.send(_ping(), server.control_port)
    reply = await client.recv()
    assert struct.unpack_from("<H", reply, 4)[0] == _PT_PING
    assert reply[0x10] == 0x01  # reply flag

    client.send(_ping(), server.civ_port)
    reply = await client.recv()
    assert struct.unpack_from("<H", reply, 4)[0] == _PT_PING
    assert reply[0x10] == 0x01


async def test_civ_data_from_dropped_client_gets_no_answer(server, client):
    """(c) After the drop the old client's CI-V DATA is ignored."""
    await _login_and_claim(client, server)
    client.send(_openclose(), server.civ_port)
    await client.recv()  # unsolicited CI-V frame on open
    client.send(_freq_get(), server.civ_port)
    assert _is_freq_reply(await _recv_reply(client, _freq_get()))  # baseline: served

    server.drop_session(at=0.2, notice=False)
    await asyncio.sleep(0.5)  # past the drop

    client.send(_freq_get(), server.civ_port)
    await client.expect_silence(0.4)


async def test_stale_openclose_does_not_reopen_stream_and_counters(server, client):
    """(d)+(e) A stale OpenClose(open) does not lift the fence; a fresh login
    followed by OpenClose(open) does. Counters count accepted logins and every
    OpenClose(open) received, accepted or fenced."""
    await _login_and_claim(client, server)
    assert server.login_count == 1
    client.send(_openclose(), server.civ_port)
    await client.recv()  # unsolicited CI-V frame on open
    assert server.openclose_open_count == 1
    client.send(_freq_get(), server.civ_port)
    assert _is_freq_reply(await _recv_reply(client, _freq_get()))  # baseline: served

    server.drop_session(at=0.2, notice=False)
    await asyncio.sleep(0.5)  # past the drop

    # The stale client's OpenClose(open) gets no stream and does not reopen.
    client.send(_openclose(), server.civ_port)
    await client.expect_silence(0.3)
    assert server.openclose_open_count == 2  # fenced opens are still counted
    client.send(_freq_get(), server.civ_port)
    await client.expect_silence(0.3)

    # A fresh login authorises the next OpenClose(open), which reopens the stream.
    client.send(_login(), server.control_port)
    login = await client.recv()
    assert struct.unpack_from("<I", login, 0x30)[0] == 0  # error OK
    assert server.login_count == 2
    client.send(_conninfo(), server.control_port)
    await client.recv()
    client.send(_openclose(), server.civ_port)
    await client.recv()  # unsolicited CI-V frame on open
    assert server.openclose_open_count == 3
    client.send(_freq_get(), server.civ_port)
    assert _is_freq_reply(await _recv_reply(client, _freq_get()))


async def test_fence_permission_is_per_client_not_shared(server, client):
    """(d, two clients) The fence permission belongs to the client whose
    fresh login's own conninfo was accepted: B (fresh login + accepted
    conninfo + OpenClose from its declared CI-V port) is served, stale A
    (never re-logs) still gets nothing — even though both clients sit on
    the same IP."""
    await _login_and_claim(client, server)
    client.send(_openclose(), server.civ_port)
    await client.recv()  # unsolicited CI-V frame on open
    client.send(_freq_get(), server.civ_port)
    assert _is_freq_reply(await _recv_reply(client, _freq_get()))  # A baseline: served

    server.drop_session(at=0.2, notice=False)
    await asyncio.sleep(0.5)  # past the drop

    loop = asyncio.get_running_loop()
    client_b = _UdpClient()
    await loop.create_datagram_endpoint(lambda: client_b, local_addr=(_HOST, 0))
    try:
        b_civ_port = client_b.transport.get_extra_info("sockname")[1]

        # B logs in afresh and declares the CI-V port it will open from.
        client_b.send(_login(), server.control_port)
        login_b = await client_b.recv()
        assert struct.unpack_from("<I", login_b, 0x30)[0] == 0  # error OK
        assert server.login_count == 2
        client_b.send(_conninfo_declaring(b_civ_port), server.control_port)
        status_b = await client_b.recv()
        assert struct.unpack_from(">H", status_b, 0x42)[0] == server.civ_port

        # B's OpenClose(open) comes from the declared port → fence lifts.
        client_b.send(_openclose(), server.civ_port)
        await client_b.recv()  # unsolicited CI-V frame on open
        client_b.send(_freq_get(), server.civ_port)
        assert _is_freq_reply(await _recv_reply(client_b, _freq_get()))  # B is served

        # A never re-logs: its OpenClose(open) still gets nothing — B's
        # accepted login did not open the gate for the shared IP.
        client.send(_openclose(), server.civ_port)
        await client.expect_silence(0.3)
        client.send(_freq_get(), server.civ_port)
        await client.expect_silence(0.3)
        # A baseline, B's accepted open, A's fenced open.
        assert server.openclose_open_count == 3
    finally:
        client_b.close()


async def test_reboot_notice_then_discovery_grace_then_silence(server, client):
    """(f) Recorded power-off order: the 0x90 notice first, then a window in
    which AreYouThere/AreYouReady are still answered, then total silence.

    The order is what this pins, not the recorded 0.02 s: the probes cross the
    same event loop as the simulator, and the first one of a cold process or a
    loaded runner is read later than 20 ms, so with the recorded window this
    test failed every time it ran alone. The test holds the window open and
    ends it itself."""
    await _login_and_claim(client, server)

    server.reboot(
        at=0.2, down_for=5.0, busy_for=0.0, notice=True, hold_notice_window=True
    )

    # The notice goes out before the radio goes dark.
    notice = await client.recv(timeout=2.0)
    assert len(notice) == 0x90
    assert notice[0x15] == 0x00
    assert struct.unpack_from("<I", notice, 0x60)[0] == 0
    _assert_notice_reaches_production_callback(notice)

    # Inside the grace window discovery is still answered.
    client.send(_ayt(), server.control_port)
    client.send(_ayr(), server.control_port)
    first = await client.recv(timeout=2.0)
    second = await client.recv(timeout=2.0)
    types = {struct.unpack_from("<H", p, 4)[0] for p in (first, second)}
    assert types == {_PT_I_AM_HERE, _PT_ARE_YOU_READY}

    # Then total silence for the rest of the down window.
    server.end_notice_window()
    client.send(_ayt(), server.control_port)
    await client.expect_silence(0.3)
    assert server.reboot_count == 1


async def test_reboot_fences_stale_openclose_until_fresh_login(server, client):
    """(g) After a reboot the same fence holds: a stale OpenClose(open) gets
    no stream; a fresh login + OpenClose(open) is served again."""
    await _login_and_claim(client, server)
    client.send(_openclose(), server.civ_port)
    await client.recv()
    client.send(_freq_get(), server.civ_port)
    assert _is_freq_reply(await _recv_reply(client, _freq_get()))  # baseline: served

    server.reboot(at=0.2, down_for=0.4, busy_for=0.0)  # default notice=False
    await asyncio.sleep(1.0)  # radio back (down ended at ~+0.62)

    # The stale client is fenced: OpenClose(open) alone does not reopen.
    client.send(_openclose(), server.civ_port)
    await client.expect_silence(0.3)
    client.send(_freq_get(), server.civ_port)
    await client.expect_silence(0.3)

    # A fresh login + OpenClose(open) lifts the fence.
    client.send(_login(), server.control_port)
    login = await client.recv()
    assert struct.unpack_from("<I", login, 0x30)[0] == 0
    client.send(_conninfo(), server.control_port)
    await client.recv()
    client.send(_openclose(), server.civ_port)
    await client.recv()
    client.send(_freq_get(), server.civ_port)
    assert _is_freq_reply(await _recv_reply(client, _freq_get()))


async def test_busy_rejected_conninfo_does_not_steal_fence_permission(server, client):
    """MOR-3340: the fence permission is recorded at accepted conninfo, not
    at login — a second login whose conninfo is busy-rejected cannot take it
    from the session owner. Two clients on the same IP."""
    await _login_and_claim(client, server)
    client.send(_openclose(), server.civ_port)
    await client.recv()  # unsolicited CI-V frame on open
    request = _freq_get()
    client.send(request, server.civ_port)
    assert _is_freq_reply(await _recv_reply(client, request))  # A served

    server.drop_session(at=0.2, notice=False)
    await asyncio.sleep(0.5)  # past the drop

    loop = asyncio.get_running_loop()
    client_b = _UdpClient()
    await loop.create_datagram_endpoint(lambda: client_b, local_addr=(_HOST, 0))
    try:
        b_civ_port = client_b.transport.get_extra_info("sockname")[1]

        # B logs in afresh and its conninfo is accepted: the permission is B's.
        client_b.send(_login(), server.control_port)
        login_b = await client_b.recv()
        assert struct.unpack_from("<I", login_b, 0x30)[0] == 0  # error OK
        client_b.send(_conninfo_declaring(b_civ_port), server.control_port)
        status_b = await client_b.recv()
        assert struct.unpack_from(">H", status_b, 0x42)[0] == server.civ_port
        client_b.send(_openclose(), server.civ_port)
        await client_b.recv()  # unsolicited CI-V frame on open
        b_request = _freq_get()
        client_b.send(b_request, server.civ_port)
        assert _is_freq_reply(await _recv_reply(client_b, b_request))  # B served

        # A logs in again, but B holds the session: A's conninfo is
        # busy-rejected and must not move the permission.
        client.send(_login(), server.control_port)
        await client.recv()
        client.send(_conninfo(), server.control_port)
        busy = await client.recv()
        assert struct.unpack_from("<I", busy, 0x30)[0] == _ERR_PREV_SESSION_ACTIVE
        assert struct.unpack_from(">H", busy, 0x42)[0] == 0

        # B is still served; A is still fenced.
        client_b.send(b_request, server.civ_port)
        assert _is_freq_reply(await _recv_reply(client_b, b_request))
        client.send(_openclose(), server.civ_port)
        await client.expect_silence(0.3)
        client.send(request, server.civ_port)
        await client.expect_silence(0.3)
    finally:
        client_b.close()


async def test_open_before_conninfo_stays_fenced(server, client):
    """A fresh login alone grants no fence permission — not even the
    declared-port-0 IP fallback: an OpenClose(open) sent before the login's
    own conninfo is accepted stays fenced, and so does CI-V data."""
    await _login_and_claim(client, server)
    server.drop_session(at=0.2, notice=False)
    await asyncio.sleep(0.5)  # past the drop

    # Fresh login, conninfo not yet sent: the open is fenced, and so is data.
    client.send(_login(), server.control_port)
    await client.recv()
    client.send(_openclose(), server.civ_port)
    await client.expect_silence(0.3)
    request = _freq_get()
    client.send(request, server.civ_port)
    await client.expect_silence(0.3)

    # The conninfo arrives and is accepted: the next open is served.
    client.send(_conninfo(), server.control_port)
    await client.recv()
    client.send(_openclose(), server.civ_port)
    await client.recv()  # unsolicited CI-V frame on open
    client.send(request, server.civ_port)
    assert _is_freq_reply(await _recv_reply(client, request))


async def test_foreign_conninfo_does_not_move_permission(server, client):
    """The ``(addr, sender_id) == _last_login`` guard: a conninfo from any
    other socket leaves the fence permission where it was — even when the
    session is free so the conninfo itself is accepted."""
    await _login_and_claim(client, server)
    server.drop_session(at=0.2, notice=False)
    await asyncio.sleep(0.5)  # past the drop

    loop = asyncio.get_running_loop()
    client_b = _UdpClient()
    await loop.create_datagram_endpoint(lambda: client_b, local_addr=(_HOST, 0))
    try:
        # B is the fresh login, but A's stale socket sends a conninfo first:
        # the session is free so it is accepted (the real CI-V port comes
        # back), yet the guard holds the permission back — A's open is fenced.
        client_b.send(_login(), server.control_port)
        await client_b.recv()
        client.send(_conninfo(), server.control_port)
        status = await client.recv()
        assert struct.unpack_from(">H", status, 0x42)[0] == server.civ_port
        client.send(_openclose(), server.civ_port)
        await client.expect_silence(0.3)

        # B's own conninfo is now busy-rejected (A holds the session); it
        # moves nothing either, so B's open is fenced too.
        client_b.send(_conninfo(), server.control_port)
        busy = await client_b.recv()
        assert struct.unpack_from("<I", busy, 0x30)[0] == _ERR_PREV_SESSION_ACTIVE
        client_b.send(_openclose(), server.civ_port)
        await client_b.expect_silence(0.3)

        # A logs in afresh; its accepted conninfo records the permission and
        # its open is served again.
        client.send(_login(), server.control_port)
        await client.recv()
        client.send(_conninfo(), server.control_port)
        await client.recv()
        client.send(_openclose(), server.civ_port)
        await client.recv()  # unsolicited CI-V frame on open
        request = _freq_get()
        client.send(request, server.civ_port)
        assert _is_freq_reply(await _recv_reply(client, request))
    finally:
        client_b.close()
