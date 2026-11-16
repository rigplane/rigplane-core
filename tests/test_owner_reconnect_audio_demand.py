"""Held audio RX demand across an owner disconnect + connect, and the
recovery paths that re-establish it.

The soft and hard reconnect paths re-establish session-held audio demand
after ``connect()`` returns: ``radio_reconnect.reconnect_loop`` and the
owner-connect tail of ``CoreRadio.connect`` both funnel into
``CoreRadio._recover_audio_after_connect``, and the soft path recovers in
``ControlPhaseRuntime._after_reconnect_parts``. After an owner
``disconnect()`` -> ``connect()`` the still-held demand is re-established
exactly once. The loop test runs the resident hard-reconnect round with the
real ``connect()``; the not-the-reconnect-task guard in ``connect()`` keeps
that round single. The negative tests pin silence with no held demand, with
``auto_recover_audio=False``, and when the owner stops the radio while
``connect()`` fetches initial state. The positive control runs the same
recovery the reconnect loop runs and passes, proving the fixture can observe
success.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from rigplane.audio import AudioPacket, AudioState
from rigplane.audio.session import AudioSession, AudioSessionState, RxSubscription
from rigplane.core.transport import IcomTransport
from rigplane.runtime._audio_recovery import AudioRecoveryState
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import IcomRadio

from test_audio_recovery_session import _StatefulFakeStream
from test_radio import MockTransport

_PACKET = AudioPacket(ident=0x0080, send_seq=1, data=b"\x01\x00" * 160)


def _fake_audio_transport() -> MagicMock:
    """Audio-transport double safe for the real teardown paths.

    ``ControlPhaseRuntime.disconnect``/``release`` pack OpenClose headers
    from ``my_id``/``remote_id`` and await ``send_tracked``/``disconnect``,
    so a bare ``MagicMock`` fails there; this one carries the int fields
    the packet builders read.
    """
    transport = MagicMock(spec=IcomTransport)
    transport.my_id = 0x00010001
    transport.remote_id = 0xDEADBEEF
    transport.send_seq = 0
    transport.send_tracked = AsyncMock()
    transport.disconnect = AsyncMock()
    return transport


def _owner_connect_radio(recovery_cb: MagicMock) -> IcomRadio:
    """A real CoreRadio whose owner connect/disconnect run for real.

    The real ``CoreRadio.connect`` runs over a shared ctrl/CI-V double with a lifecycle double
    mirroring ``ControlPhaseSessionMechanism.release`` (graceful
    ``ControlPhaseRuntime.disconnect`` when CONNECTED, else ``release`` —
    both stop the audio stream, as production teardown does). The audio
    rebuild seam mirrors ``AudioRuntimeMixin._ensure_audio_transport``: a
    fresh fake stream per rebuild, kept while the slot is populated.
    """
    radio = IcomRadio(
        "192.168.1.100",
        model="IC-7610",
        timeout=2.0,
        on_audio_recovery=recovery_cb,
    )
    transport = MockTransport()
    transport._udp_transport = object()  # control session looks open
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._token = 0x1234

    async def _lc_connect() -> None:
        radio._conn_state = RadioConnectionState.CONNECTED
        radio._ctrl_transport = transport
        radio._civ_transport = transport
        transport.connected = True
        transport.disconnected = False
        radio._ctrl_transport._token_reply_callback = (
            radio._control_phase._on_token_reply
        )
        radio._civ_runtime.start_data_watchdog()

    async def _lc_disconnect() -> None:
        if radio._conn_state is RadioConnectionState.CONNECTED:
            await radio._control_phase.disconnect()
        else:
            await radio._control_phase.release()

    lifecycle = AsyncMock()
    lifecycle.connect = AsyncMock(side_effect=_lc_connect)
    lifecycle.disconnect = AsyncMock(side_effect=_lc_disconnect)
    radio._session_lifecycle = lifecycle

    radio._fetch_initial_state = AsyncMock()
    radio._arm_managed_tx = AsyncMock()
    radio.rearm_managed_tx = AsyncMock()

    async def _ensure_audio_transport() -> None:
        if radio._audio_stream is not None:
            return
        radio._audio_transport = _fake_audio_transport()
        radio._audio_stream = _StatefulFakeStream()

    radio._ensure_audio_transport = AsyncMock(side_effect=_ensure_audio_transport)

    radio._connected = True  # an already-connected radio serving a consumer
    return radio


async def _assert_rx_reestablished_once(
    radio: IcomRadio,
    session: AudioSession,
    sub: RxSubscription,
    recovery_cb: MagicMock,
) -> None:
    """The shared RED/GREEN battery: exactly one re-establish, RX live."""
    states = [c.args[0] for c in recovery_cb.call_args_list]
    assert states == [AudioRecoveryState.RECOVERING, AudioRecoveryState.RECOVERED]
    fresh = radio._audio_stream
    assert isinstance(fresh, _StatefulFakeStream)
    assert fresh.start_rx_count == 1  # re-established exactly once
    assert fresh.state is AudioState.RECEIVING
    assert fresh.last_start_rx_callback == session.bus._on_opus_packet
    assert session.state is AudioSessionState.RX_ONLY
    assert session.bus.subscriber_count == 1  # no leaked/duplicated demand
    fresh.emit_rx(_PACKET)  # real fan-out through the rebuilt leg
    assert await sub.get(timeout=1.0) == _PACKET


async def test_owner_connect_reestablishes_held_rx_demand() -> None:
    """Owner disconnect + connect must re-arm the still-held RX demand."""
    recovery_cb = MagicMock()
    radio = _owner_connect_radio(recovery_cb)
    session = radio.audio_session
    sub = await session.subscribe_rx("web")
    first = radio._audio_stream
    assert isinstance(first, _StatefulFakeStream)
    assert first.start_rx_count == 1  # the held demand armed the first leg
    snapshot = radio._audio_runtime.capture_snapshot()
    assert snapshot is not None  # live session demand is recoverable

    await radio.disconnect()  # the owner tears the control path down
    assert radio._audio_stream is None  # the audio stream went with it
    await radio.connect()  # the owner brings the link back — no hold
    assert radio.connected

    try:
        fresh = radio._audio_stream
        assert isinstance(fresh, _StatefulFakeStream)
        assert fresh is not first  # a rebuilt leg, not the torn-down one
        await _assert_rx_reestablished_once(radio, session, sub, recovery_cb)
    finally:
        await sub.release()
        await radio.disconnect()


async def test_positive_control_manual_recover_restores_rx_after_owner_connect() -> (
    None
):
    """The fixture observes success: the reconnect loop's own recovery works.

    Running by hand what ``radio_reconnect.reconnect_loop`` runs after its
    ``connect()`` — ``radio._audio_runtime.recover`` with the snapshot
    captured before the disconnect — makes every assertion of the
    owner-connect test pass. ``auto_recover_audio=False`` keeps the manual
    call the only recovery now that ``connect()`` recovers on its own.
    """
    recovery_cb = MagicMock()
    radio = _owner_connect_radio(recovery_cb)
    radio._auto_recover_audio = False
    session = radio.audio_session
    sub = await session.subscribe_rx("web")
    snapshot = radio._audio_runtime.capture_snapshot()
    assert snapshot is not None

    await radio.disconnect()
    await radio.connect()
    assert [c.args[0] for c in recovery_cb.call_args_list] == []  # nothing yet
    await radio._audio_runtime.recover(snapshot)

    try:
        await _assert_rx_reestablished_once(radio, session, sub, recovery_cb)
    finally:
        await sub.release()
        await radio.disconnect()


def _run_resident_reconnect(radio: IcomRadio) -> "asyncio.Task[None]":
    """Start one resident hard-reconnect round the way the watchdog does."""
    radio._conn_state = RadioConnectionState.RECONNECTING
    radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
    return radio._reconnect_task


async def test_hard_reconnect_loop_recovers_held_demand_once() -> None:
    """A resident hard-reconnect round over the real ``connect()`` recovers
    the held demand exactly once — ``connect()`` itself must leave the
    recovery to the loop (the not-the-reconnect-task guard)."""
    recovery_cb = MagicMock()
    radio = _owner_connect_radio(recovery_cb)
    session = radio.audio_session
    sub = await session.subscribe_rx("web")

    loop_task = _run_resident_reconnect(radio)
    await asyncio.wait_for(loop_task, timeout=5.0)
    assert radio.connected

    try:
        await _assert_rx_reestablished_once(radio, session, sub, recovery_cb)
    finally:
        await sub.release()
        await radio.disconnect()


async def test_owner_connect_without_held_demand_runs_no_recovery() -> None:
    """No session demand held across the owner disconnect → connect ends
    with no recovery events and no audio leg."""
    recovery_cb = MagicMock()
    radio = _owner_connect_radio(recovery_cb)

    await radio.disconnect()
    await radio.connect()
    assert radio.connected

    assert [c.args[0] for c in recovery_cb.call_args_list] == []
    assert radio._audio_stream is None
    await radio.disconnect()


async def test_owner_connect_with_auto_recover_disabled_runs_no_recovery() -> None:
    """``auto_recover_audio=False`` → the owner connect recovers nothing."""
    recovery_cb = MagicMock()
    radio = _owner_connect_radio(recovery_cb)
    radio._auto_recover_audio = False
    session = radio.audio_session
    sub = await session.subscribe_rx("web")

    await radio.disconnect()
    await radio.connect()
    assert radio.connected

    assert [c.args[0] for c in recovery_cb.call_args_list] == []
    assert radio._audio_stream is None  # the held demand stays un-armed
    await sub.release()
    await radio.disconnect()


async def test_owner_stop_during_connect_runs_no_recovery() -> None:
    """Owner ``disconnect()`` landing while ``connect()`` is inside
    ``_fetch_initial_state`` leaves no recovery behind: the owner-connect
    tail must not run against the radio the owner just tore down."""
    recovery_cb = MagicMock()
    radio = _owner_connect_radio(recovery_cb)
    session = radio.audio_session
    sub = await session.subscribe_rx("web")

    in_fetch = asyncio.Event()
    release_fetch = asyncio.Event()

    async def _gated_fetch() -> None:
        in_fetch.set()
        await release_fetch.wait()

    radio._fetch_initial_state = AsyncMock(side_effect=_gated_fetch)

    await radio.disconnect()
    connect_task = asyncio.create_task(radio.connect())
    assert await asyncio.wait_for(in_fetch.wait(), timeout=5.0)
    await radio.disconnect()  # the owner stops the radio mid-connect
    release_fetch.set()
    await asyncio.wait_for(connect_task, timeout=5.0)

    assert [c.args[0] for c in recovery_cb.call_args_list] == []
    assert radio._audio_stream is None  # nothing re-armed on the dead link
    assert radio._audio_runtime.capture_snapshot() is not None  # demand held
    assert session.bus.subscriber_count == 1
    await sub.release()
