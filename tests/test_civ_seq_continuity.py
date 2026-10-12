"""CI-V tracked-packet numbering across a rebuild of the CI-V transport.

A rebuild that keeps the radio session resumes the numbering of the transport
it replaces; a fresh login starts at 0. The transport under test is the real
``IcomTransport``: its ``send_tracked`` and its ``connect`` counter reset run
unmodified. Discovery, the keep-alive loops and the UDP send are replaced, so
the sequence asserted on a rebuilt transport is read from the header bytes
``send_tracked`` produced, not from its ``send_seq`` attribute.
"""

from __future__ import annotations

import struct
from collections.abc import Awaitable, Callable, Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime._control_phase import OPENCLOSE_SIZE
from rigplane.transport import CONTROL_SIZE, ConnectionState, IcomTransport

from test_radio_connect import ConnectMockTransport, _build_conninfo, _ConnectOnceStubs

_OPENCLOSE_OPEN = 0x04
_SEQ_OFFSET = 6
_OLD_NEXT_SEQ = 1015
_RETRANSMIT_REQUEST = 0x01
_IDLE = 0x00


class _OfflineCivTransport(IcomTransport):
    """The real tracked-send path and ``connect`` reset, with no radio behind it.

    ``connect`` still opens a loopback UDP socket; every datagram the send
    path emits lands in ``wire`` instead of that socket.
    """

    def __init__(self) -> None:
        super().__init__()
        self.wire: list[bytes] = []
        self._raw_send = self.wire.append

    async def _discover(self) -> None:
        return None

    async def _ready_handshake(self) -> None:
        return None

    def start_ping_loop(self) -> None: ...

    def start_retransmit_loop(self) -> None: ...

    def start_idle_loop(self) -> None: ...


def _header_seq(packet: bytes) -> int:
    return int(struct.unpack_from("<H", packet, _SEQ_OFFSET)[0])


def _kept_session_radio() -> IcomRadio:
    """A LAN radio whose control session is up; the test sets its CI-V transport."""
    radio = IcomRadio("127.0.0.1", model="IC-7610", timeout=0.5)
    ctrl = ConnectMockTransport()
    ctrl.state = ConnectionState.CONNECTED
    ctrl._udp_transport = object()  # the control socket looks open
    radio._ctrl_transport = ctrl
    radio._civ_port = 50002
    radio._local_bind_host = "127.0.0.1"
    radio._conn_state = RadioConnectionState.CONNECTED
    return radio


@contextmanager
def _rebuild_stubs(radio: IcomRadio) -> Iterator[None]:
    """Let ``soft_reconnect`` rebuild CI-V over ``_OfflineCivTransport``."""
    with ExitStack() as stack:
        stack.enter_context(
            patch("rigplane.transport.IcomTransport", _OfflineCivTransport)
        )
        stack.enter_context(
            patch.object(radio._control_phase, "_after_reconnect", new=AsyncMock())
        )
        for name in (
            "advance_generation",
            "start_pump",
            "start_worker",
            "start_data_watchdog",
        ):
            stack.enter_context(patch.object(radio._civ_runtime, name))
        yield


async def _no_prior_teardown(radio: IcomRadio) -> None:
    """Leave the old transport on the host: ``soft_reconnect`` tears it down itself."""


async def _watchdog_force_cleanup(radio: IcomRadio) -> None:
    await radio._force_cleanup_civ()


async def _soft_disconnect(radio: IcomRadio) -> None:
    await radio.soft_disconnect()


@pytest.mark.parametrize(
    ("teardown", "expected_first_seq"),
    [
        pytest.param(
            _no_prior_teardown,
            _OLD_NEXT_SEQ,
            id="stalled-transport-torn-down-inside-soft-reconnect",
        ),
        pytest.param(
            _watchdog_force_cleanup, _OLD_NEXT_SEQ, id="watchdog-force-cleanup"
        ),
        # soft_disconnect sends OpenClose(close) first, which uses 1015.
        pytest.param(_soft_disconnect, _OLD_NEXT_SEQ + 1, id="soft-disconnect"),
    ],
)
async def test_rebuild_on_a_kept_session_resumes_the_old_numbering(
    teardown: Callable[[IcomRadio], Awaitable[None]], expected_first_seq: int
) -> None:
    radio = _kept_session_radio()
    old = _OfflineCivTransport()
    old.send_seq = _OLD_NEXT_SEQ
    radio._civ_transport = old
    with _rebuild_stubs(radio):
        await teardown(radio)
        assert old.send_seq == expected_first_seq
        await radio._control_phase.soft_reconnect()
        new = radio._civ_transport
        assert isinstance(new, _OfflineCivTransport)
        try:
            assert new is not old
            assert len(new.wire) == 1
            open_packet = new.wire[0]
            assert len(open_packet) == OPENCLOSE_SIZE
            assert open_packet[0x15] == _OPENCLOSE_OPEN
            assert _header_seq(open_packet) == expected_first_seq
        finally:
            await new.disconnect()


async def test_fresh_login_starts_the_civ_numbering_at_zero() -> None:
    radio = IcomRadio(
        "127.0.0.1", username="u", password="p", model="IC-7610", timeout=0.5
    )
    ctrl = ConnectMockTransport()
    ctrl.queue_response(_build_conninfo())
    radio._ctrl_transport = ctrl
    radio._civ_resume_seq = _OLD_NEXT_SEQ  # recorded by an earlier teardown
    try:
        with (
            _ConnectOnceStubs(radio),
            patch("rigplane.transport.IcomTransport", _OfflineCivTransport),
            patch.object(radio, "_gate_lan_identity", new=AsyncMock(return_value=True)),
        ):
            await radio._control_phase._connect_once()
        new = radio._civ_transport
        assert isinstance(new, _OfflineCivTransport)
        assert len(new.wire) == 1
        assert new.wire[0][0x15] == _OPENCLOSE_OPEN
        assert _header_seq(new.wire[0]) == 0
        assert radio._civ_resume_seq is None
    finally:
        new = radio._civ_transport
        if new is not None:
            await new.disconnect()
        radio._control_phase._close_pending_sockets()


@pytest.mark.parametrize("teardown", ["release", "disconnect"])
async def test_a_released_session_forgets_the_recorded_numbering(teardown: str) -> None:
    radio = _kept_session_radio()
    radio._civ_resume_seq = _OLD_NEXT_SEQ
    await getattr(radio._control_phase, teardown)()
    assert radio._civ_resume_seq is None


def _range_request(transport: IcomTransport, first: int, last: int) -> bytes:
    """A multi retransmit request for one inclusive (first, last) range."""
    pkt = bytearray(CONTROL_SIZE)
    struct.pack_into("<H", pkt, 4, _RETRANSMIT_REQUEST)
    struct.pack_into("<II", pkt, 8, transport.remote_id, transport.my_id)
    pkt.extend(struct.pack("<HH", first, last))
    struct.pack_into("<I", pkt, 0, len(pkt))
    return bytes(pkt)


async def test_a_request_for_the_outage_numbers_is_answered_with_idle_packets() -> None:
    """The restored number sits above everything the radio processed.

    The radio asks for the gap; the rebuilt transport holds none of it, so each
    request is answered with an untracked idle packet and the counter stays.
    """
    radio = _kept_session_radio()
    old = _OfflineCivTransport()
    old.send_seq = _OLD_NEXT_SEQ
    radio._civ_transport = old
    with _rebuild_stubs(radio):
        await radio._control_phase.soft_reconnect()
        new = radio._civ_transport
        assert isinstance(new, _OfflineCivTransport)
        try:
            assert [_header_seq(p) for p in new.wire] == [_OLD_NEXT_SEQ]
            assert new.send_seq == _OLD_NEXT_SEQ + 1
            sent_before = len(new.wire)

            new._handle_packet(_range_request(new, 1000, 1014))

            replies = new.wire[sent_before:]
            assert len(replies) == 15
            for expected_seq, reply in zip(range(1000, 1015), replies, strict=True):
                assert len(reply) == CONTROL_SIZE
                assert struct.unpack_from("<H", reply, 4)[0] == _IDLE
                assert _header_seq(reply) == expected_seq
                assert reply == new._build_control(ptype=_IDLE, seq=expected_seq)
            assert new.send_seq == _OLD_NEXT_SEQ + 1

            await new.send_tracked(bytes(CONTROL_SIZE))
            assert len(new.wire) == sent_before + 15 + 1
            assert _header_seq(new.wire[-1]) == _OLD_NEXT_SEQ + 1
        finally:
            await new.disconnect()
