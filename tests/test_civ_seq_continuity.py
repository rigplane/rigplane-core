"""CI-V tracked-packet numbering across a rebuild of the CI-V transport.

A rebuild that keeps the radio session resumes the numbering of the transport
it replaces; a fresh login starts at 0. The transport under test is the real
``IcomTransport``: its ``send_tracked`` and its ``connect`` counter reset run
unmodified. Discovery, the keep-alive loops and the UDP send are replaced, so
the sequence asserted on a rebuilt transport is read from the header bytes
``send_tracked`` produced, not from its ``send_seq`` attribute.
"""

from __future__ import annotations

import errno
import struct
from collections.abc import Awaitable, Callable, Iterator
from contextlib import ExitStack, contextmanager
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.core.exceptions import ConnectionError, TimeoutError
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime._control_phase import OPENCLOSE_SIZE
from rigplane.transport import CONTROL_SIZE, ConnectionState, IcomTransport

from test_radio_connect import ConnectMockTransport, _build_conninfo, _ConnectOnceStubs

_OPENCLOSE_OPEN = 0x04
_SEQ_OFFSET = 6
_OLD_NEXT_SEQ = 1015
_NEWER_RECORDED_SEQ = 2000
_CARRIED_SEQ = 6316
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


class _FlakyDiscoveryCivTransport(_OfflineCivTransport):
    """Rebuild transport whose first ``failures`` instances miss discovery.

    Keeps a class-level list of every instance created, so a test can count
    rebuilds; the first ``failures`` instances raise the rigplane
    ``TimeoutError`` that ``IcomTransport._discover`` raises when the CI-V
    port stops answering — not an ``OSError``.
    """

    failures: ClassVar[int] = 0
    instances: ClassVar[list["_FlakyDiscoveryCivTransport"]] = []

    def __init__(self) -> None:
        super().__init__()
        self._ordinal = len(self.instances)
        self.instances.append(self)

    async def _discover(self) -> None:
        if self._ordinal < self.failures:
            raise TimeoutError("CI-V port did not answer discovery")


class _BindFailureTransport(_OfflineCivTransport):
    """Rebuild transport whose ``connect`` fails before any endpoint exists.

    Mirrors ``create_datagram_endpoint`` refusing the bind of the fixed
    local CI-V port: ``OSError`` is raised with ``_udp_transport`` still
    ``None`` (``IcomTransport.connect`` sets it only once the datagram
    endpoint exists), so the failed transport holds nothing to close.
    Counts its ``disconnect()`` calls.
    """

    instances: ClassVar[list["_BindFailureTransport"]] = []

    def __init__(self) -> None:
        super().__init__()
        self.disconnect_calls = 0
        self.instances.append(self)

    async def connect(
        self,
        host: str,
        port: int,
        *,
        local_host: str | None = None,
        local_port: int = 0,
    ) -> None:
        raise OSError(errno.EADDRINUSE, "Address already in use")

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        await super().disconnect()


async def _fail_if_called() -> None:
    pytest.fail("recovery must rebuild CI-V on the kept control session")


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
def _rebuild_stubs(
    radio: IcomRadio, transport_cls: type[IcomTransport] = _OfflineCivTransport
) -> Iterator[None]:
    """Let ``soft_reconnect`` rebuild CI-V over an offline transport."""
    with ExitStack() as stack:
        stack.enter_context(patch("rigplane.transport.IcomTransport", transport_cls))
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


async def test_retired_managed_tx_port_keeps_the_civ_numbering() -> None:
    """A managed-TX port retirement records the retired numbering.

    The retirement drops the CI-V transport while the control session stays
    up, so the ``soft_reconnect`` rebuild that follows resumes the retired
    transport's numbering instead of restarting at 0.
    """
    radio = _kept_session_radio()
    old = _OfflineCivTransport()
    old.send_seq = _OLD_NEXT_SEQ
    radio._civ_transport = old
    with _rebuild_stubs(radio):
        assert radio._capture_managed_tx_port(7, lambda _obs: None) is True
        await radio._retire_managed_tx_port(7)
        assert radio._civ_resume_seq == _OLD_NEXT_SEQ
        await radio._control_phase.soft_reconnect()
        new = radio._civ_transport
        assert isinstance(new, _OfflineCivTransport)
        try:
            assert new is not old
            assert len(new.wire) == 1
            open_packet = new.wire[0]
            assert len(open_packet) == OPENCLOSE_SIZE
            assert open_packet[0x15] == _OPENCLOSE_OPEN
            assert _header_seq(open_packet) == _OLD_NEXT_SEQ
        finally:
            await new.disconnect()


async def test_stale_port_retirement_keeps_the_newer_recorded_numbering() -> None:
    """A retirement running after the replacement is live records nothing.

    ``ManagedRadioRuntime._begin_retirement`` fires the retirement as a
    task, so its body can run after CI-V recovery installed a
    replacement transport and a newer teardown recorded that transport's
    numbering. Recording the stale token's ``send_seq`` then would drag
    the resume value back down to the retired transport's number.
    """
    radio = _kept_session_radio()
    old = _OfflineCivTransport()
    old.send_seq = _OLD_NEXT_SEQ
    radio._civ_transport = old
    with _rebuild_stubs(radio):
        assert radio._capture_managed_tx_port(7, lambda _obs: None) is True
        replacement = _OfflineCivTransport()
        radio._civ_transport = replacement
        radio._civ_resume_seq = _NEWER_RECORDED_SEQ
        await radio._retire_managed_tx_port(7)
        assert radio._civ_transport is replacement
        assert radio._civ_resume_seq == _NEWER_RECORDED_SEQ


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


@pytest.mark.parametrize(
    "failed_attempts", [1, 2], ids=["one-failed-rebuild", "two-failed-rebuilds"]
)
async def test_failed_rebuilds_keep_the_carried_numbering(
    failed_attempts: int,
) -> None:
    """The watchdog hand-off recovers the number a failed rebuild loses.

    On the bench IC-7610 a rebuild whose CI-V port does not answer raises
    the rigplane ``TimeoutError``. Unless that failed rebuild is closed and
    dropped without recording, the next attempt finds it half-open on the
    host, records its reset 0 over the carried number, and the radio then
    ignores the OpenClose of the session it still holds.
    """
    radio = _kept_session_radio()
    old = _OfflineCivTransport()
    old.send_seq = _CARRIED_SEQ
    radio._civ_transport = old
    flaky = _FlakyDiscoveryCivTransport
    flaky.instances.clear()
    flaky.failures = failed_attempts
    with (
        _rebuild_stubs(radio, flaky),
        patch.object(radio._control_phase, "_connect_once", new=_fail_if_called),
    ):
        await radio._civ_runtime._watchdog_recover()
        created = list(flaky.instances)
        new = radio._civ_transport
        assert isinstance(new, flaky)
        try:
            assert len(created) == failed_attempts + 1
            for failed in created[:-1]:
                assert failed is not new
                assert failed.wire == []
                assert failed.state is ConnectionState.DISCONNECTED
            open_packet = new.wire[0]
            assert len(open_packet) == OPENCLOSE_SIZE
            assert open_packet[0x15] == _OPENCLOSE_OPEN
            assert _header_seq(open_packet) == _CARRIED_SEQ
            assert radio._civ_resume_seq == _CARRIED_SEQ
        finally:
            await new.disconnect()


async def test_a_failed_rebuild_is_closed_and_keeps_the_carried_numbering() -> None:
    """One failed rebuild raises ConnectionError and records nothing.

    Pins the failure handling of a single rebuild: the rigplane
    ``TimeoutError`` that discovery raises becomes a ``ConnectionError``,
    the half-open transport is disconnected and dropped from the host
    without recording its reset numbering, and the carried number survives
    for the next attempt.
    """
    radio = _kept_session_radio()
    old = _OfflineCivTransport()
    old.send_seq = _CARRIED_SEQ
    radio._civ_transport = old
    flaky = _FlakyDiscoveryCivTransport
    flaky.instances.clear()
    flaky.failures = 1
    with _rebuild_stubs(radio, flaky):
        await radio._force_cleanup_civ()
        assert radio._civ_resume_seq == _CARRIED_SEQ
        with pytest.raises(ConnectionError):
            await radio._control_phase.soft_reconnect()
        assert len(flaky.instances) == 1
        failed = flaky.instances[0]
        assert failed.wire == []
        assert failed.state is ConnectionState.DISCONNECTED
        assert radio._civ_transport is None
        assert radio._civ_resume_seq == _CARRIED_SEQ


async def test_a_rebuild_failing_before_an_endpoint_is_left_unclosed() -> None:
    """A rebuild whose bind fails before an endpoint is not disconnected.

    ``IcomTransport.connect`` raises ``OSError`` out of
    ``create_datagram_endpoint`` with ``_udp_transport`` still ``None``
    when the fixed local CI-V port is already bound.  Nothing is open,
    so the failure handling must drop the transport from the host
    without calling ``disconnect()`` — a counted call here pins that —
    and the carried numbering survives for the next attempt.
    """
    radio = _kept_session_radio()
    old = _OfflineCivTransport()
    old.send_seq = _CARRIED_SEQ
    radio._civ_transport = old
    refused = _BindFailureTransport
    refused.instances.clear()
    with _rebuild_stubs(radio, refused):
        await radio._force_cleanup_civ()
        assert radio._civ_resume_seq == _CARRIED_SEQ
        with pytest.raises(ConnectionError):
            await radio._control_phase.soft_reconnect()
        assert len(refused.instances) == 1
        failed = refused.instances[0]
        assert failed.disconnect_calls == 0
        assert radio._civ_transport is None
        assert radio._civ_resume_seq == _CARRIED_SEQ
