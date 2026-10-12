"""Audio tracked-packet numbering across a rebuild of the audio transport.

A rebuild that keeps the radio session resumes the numbering of the transport
it replaces; a fresh login starts at 0. The transport under test is the real
``IcomTransport``: its ``send_tracked`` and its ``connect`` counter reset run
unmodified. Discovery, the keep-alive loops and the UDP send are replaced, so
the sequence asserted on a rebuilt transport is read from the header bytes
``send_tracked`` produced, not from its ``send_seq`` attribute.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.core.exceptions import TimeoutError
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime._control_phase import OPENCLOSE_SIZE
from rigplane.transport import CONTROL_SIZE, ConnectionState, IcomTransport

from test_radio_connect import ConnectMockTransport, _build_conninfo, _ConnectOnceStubs

_OPENCLOSE_OPEN = 0x04
_OPENCLOSE_CLOSE = 0x00
_SEQ_OFFSET = 6
_OLD_NEXT_SEQ = 2543
_IDLE = 0x00


class _OfflineAudioTransport(IcomTransport):
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


class _FlakyDiscoveryAudioTransport(_OfflineAudioTransport):
    """Rebuild transport whose first ``failures`` instances miss discovery.

    Keeps a class-level list of every instance created, so a test can tell
    the failed attempt from the retry; the first ``failures`` instances raise
    the rigplane ``TimeoutError`` that ``IcomTransport._discover`` raises
    when the audio port stops answering — not an ``OSError``.
    """

    failures: ClassVar[int] = 0
    instances: ClassVar[list["_FlakyDiscoveryAudioTransport"]] = []

    def __init__(self) -> None:
        super().__init__()
        self._ordinal = len(self.instances)
        self.instances.append(self)

    async def _discover(self) -> None:
        if self._ordinal < self.failures:
            raise TimeoutError("Audio port did not answer discovery")


def _kept_session_radio() -> IcomRadio:
    """A LAN radio whose control session is up; the test sets its audio transport."""
    radio = IcomRadio("127.0.0.1", model="IC-7610", timeout=0.5)
    ctrl = ConnectMockTransport()
    ctrl.state = ConnectionState.CONNECTED
    ctrl._udp_transport = object()  # the control socket looks open
    radio._ctrl_transport = ctrl
    radio._audio_port = 50003
    radio._audio_local_port = 0
    radio._audio_sock_pending = None
    radio._local_bind_host = "127.0.0.1"
    radio._conn_state = RadioConnectionState.CONNECTED
    return radio


@contextmanager
def _rebuild_stubs(radio: IcomRadio) -> Iterator[None]:
    """Let ``soft_reconnect`` rebuild CI-V and audio over offline transports."""
    with ExitStack() as stack:
        stack.enter_context(
            patch("rigplane.transport.IcomTransport", _OfflineAudioTransport)
        )
        stack.enter_context(
            patch(
                "rigplane.runtime._audio_runtime_mixin.IcomTransport",
                _OfflineAudioTransport,
            )
        )
        stack.enter_context(
            patch.object(
                radio,
                "revalidate_connection_identity",
                new=AsyncMock(return_value=True),
            )
        )
        stack.enter_context(patch.object(radio, "rearm_managed_tx", new=AsyncMock()))
        stack.enter_context(patch.object(radio, "_start_audio_watchdog"))
        for name in (
            "advance_generation",
            "start_pump",
            "start_worker",
            "start_data_watchdog",
        ):
            stack.enter_context(patch.object(radio._civ_runtime, name))
        yield


async def test_rebuild_on_a_kept_session_resumes_the_old_numbering() -> None:
    radio = _kept_session_radio()
    old = _OfflineAudioTransport()
    old.send_seq = _OLD_NEXT_SEQ
    radio._audio_transport = old
    radio._audio_stream = None
    with _rebuild_stubs(radio):
        await radio._control_phase.soft_reconnect()
        assert radio._audio_resume_seq == _OLD_NEXT_SEQ
        new = radio._audio_transport
        assert isinstance(new, _OfflineAudioTransport)
        try:
            assert new is not old
            assert len(new.wire) == 1
            open_packet = new.wire[0]
            assert len(open_packet) == OPENCLOSE_SIZE
            assert open_packet[0x15] == _OPENCLOSE_OPEN
            assert _header_seq(open_packet) == _OLD_NEXT_SEQ
            idle = bytearray(CONTROL_SIZE)
            struct.pack_into("<H", idle, 4, _IDLE)
            await new.send_tracked(bytes(idle))
            assert len(new.wire) == 2
            assert _header_seq(new.wire[-1]) == _OLD_NEXT_SEQ + 1
            assert struct.unpack_from("<H", new.wire[-1], 4)[0] == _IDLE
        finally:
            await new.disconnect()
            if radio._civ_transport is not None:
                await radio._civ_transport.disconnect()


async def test_soft_disconnect_records_the_audio_numbering() -> None:
    """``soft_disconnect`` sends the audio close first, then records past it.

    The close consumes 2543, so the rebuild that follows must resume at 2544.
    """
    radio = _kept_session_radio()
    old = _OfflineAudioTransport()
    old.send_seq = _OLD_NEXT_SEQ
    radio._audio_transport = old
    radio._audio_stream = None
    await radio.soft_disconnect()
    assert radio._audio_transport is None
    assert old.send_seq == _OLD_NEXT_SEQ + 1
    assert len(old.wire) == 1
    assert old.wire[0][0x15] == _OPENCLOSE_CLOSE
    assert _header_seq(old.wire[0]) == _OLD_NEXT_SEQ
    assert radio._audio_resume_seq == _OLD_NEXT_SEQ + 1
    await old.disconnect()


async def test_fresh_login_starts_the_audio_numbering_at_zero() -> None:
    radio = IcomRadio(
        "127.0.0.1", username="u", password="p", model="IC-7610", timeout=0.5
    )
    ctrl = ConnectMockTransport()
    ctrl.queue_response(_build_conninfo())
    radio._ctrl_transport = ctrl
    radio._audio_resume_seq = _OLD_NEXT_SEQ  # recorded by an earlier teardown
    try:
        with (
            _ConnectOnceStubs(radio),
            patch(
                "rigplane.runtime._audio_runtime_mixin.IcomTransport",
                _OfflineAudioTransport,
            ),
            patch.object(radio, "_gate_lan_identity", new=AsyncMock(return_value=True)),
            patch.object(radio, "_start_audio_watchdog"),
        ):
            await radio._control_phase._connect_once()
        assert radio._audio_resume_seq is None
        with (
            patch(
                "rigplane.runtime._audio_runtime_mixin.IcomTransport",
                _OfflineAudioTransport,
            ),
            patch.object(radio, "_start_audio_watchdog"),
        ):
            await radio._ensure_audio_transport()
        new = radio._audio_transport
        assert isinstance(new, _OfflineAudioTransport)
        assert len(new.wire) == 1
        assert new.wire[0][0x15] == _OPENCLOSE_OPEN
        assert _header_seq(new.wire[0]) == 0
    finally:
        new = radio._audio_transport
        if new is not None:
            await new.disconnect()
        radio._control_phase._close_pending_sockets()


@pytest.mark.parametrize("teardown", ["release", "disconnect"])
async def test_a_released_session_forgets_the_recorded_numbering(
    teardown: str,
) -> None:
    radio = _kept_session_radio()
    radio._audio_resume_seq = _OLD_NEXT_SEQ
    await getattr(radio._control_phase, teardown)()
    assert radio._audio_resume_seq is None


async def test_a_failed_rebuild_keeps_the_carried_numbering() -> None:
    """A failed audio rebuild records nothing; the retry resumes the number.

    The failure branch drops the half-open transport from the host without
    recording its reset 0 over the carried number, so the next attempt
    restores the OpenClose numbering the teardown recorded.
    """
    radio = _kept_session_radio()
    old = _OfflineAudioTransport()
    old.send_seq = _OLD_NEXT_SEQ
    radio._audio_transport = old
    radio._audio_stream = None
    await radio._teardown_audio_transport()
    assert radio._audio_resume_seq == _OLD_NEXT_SEQ
    flaky = _FlakyDiscoveryAudioTransport
    flaky.instances.clear()
    flaky.failures = 1
    with (
        patch(
            "rigplane.runtime._audio_runtime_mixin.IcomTransport",
            _FlakyDiscoveryAudioTransport,
        ),
        patch.object(radio, "_start_audio_watchdog"),
    ):
        with pytest.raises(TimeoutError):
            await radio._ensure_audio_transport()
        assert radio._audio_transport is None
        assert radio._audio_resume_seq == _OLD_NEXT_SEQ
        await radio._ensure_audio_transport()
        new = radio._audio_transport
        assert isinstance(new, flaky)
        try:
            assert len(flaky.instances) == 2
            assert flaky.instances[0] is not new
            assert flaky.instances[0].wire == []
            assert len(new.wire) == 1
            assert new.wire[0][0x15] == _OPENCLOSE_OPEN
            assert _header_seq(new.wire[0]) == _OLD_NEXT_SEQ
            assert radio._audio_resume_seq == _OLD_NEXT_SEQ
        finally:
            await new.disconnect()
