"""A hard reconnect that ends in the LAN identity hold recovers audio when
the hold clears.

``radio_reconnect.reconnect_loop`` captures an audio snapshot, releases the
session and calls ``recover`` right after ``connect()`` returns.  On LAN,
``connect()`` returns normally while the identity hold owns the link, so the
immediate ``recover`` ran ``start_rx`` into ``_check_connected``'s hold
refusal and reported FAILED; when the hold later cleared,
``CoreRadio._finish_lan_identity_completion`` fetched initial state and armed
managed TX only — audio was never re-armed and RX stayed silent.

The tests pin the deferral: during the hold no recovery is attempted; the
hold's completion on the same epoch recovers exactly once from the snapshot
the reconnect captured; an owner stop or a new epoch before the completion
drops the deferred recovery; and without a hold the recovery runs at once,
exactly once, as before.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from rigplane.audio import AudioState
from rigplane.core.radio_protocol import RadioIdentity, RadioIdentityStatus
from rigplane.runtime._audio_recovery import AudioRecoveryState
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import IcomRadio

from test_audio_recovery_session import (
    _StatefulFakeStream,
    _install_stream,
    _make_radio,
)


def _hold_link(radio: IcomRadio) -> None:
    """connect() as production leaves it when the identity gate holds.

    ``CoreRadio.connect`` returns normally (radio.py ``connect``) while
    ``_gate_lan_identity`` holds the link: CONNECTING state, a NO_RESPONSE
    identity, and command refusal via
    ``_lan_identity_unanswered_for_current_epoch``.
    """
    radio._conn_state = RadioConnectionState.CONNECTING
    radio._connection_identity = RadioIdentity(
        status=RadioIdentityStatus.NO_RESPONSE, expected_model=radio.model
    )
    radio._lan_identity_owned_epoch = radio._civ_epoch
    radio._lan_identity_answer_epoch = None
    radio._lan_identity_completed_epoch = None


async def _run_hard_reconnect(radio: IcomRadio, connect: object) -> None:
    """Run one resident hard-reconnect round with the given connect()."""
    radio._conn_state = RadioConnectionState.RECONNECTING
    with (
        patch.object(radio._session_lifecycle, "disconnect", new=AsyncMock()),
        patch.object(radio, "connect", new=AsyncMock(side_effect=connect)),
    ):
        await asyncio.wait_for(radio._reconnect_loop(), timeout=5.0)


async def _complete_hold(radio: IcomRadio) -> None:
    """Accept the hold's identity answer on the current epoch and finish.

    Mirrors what ``_lan_identity_reread_loop`` does when the radio answers.
    """
    radio._fetch_initial_state = AsyncMock()
    radio._arm_managed_tx = AsyncMock()
    radio._connection_identity = RadioIdentity(
        status=RadioIdentityStatus.VERIFIED, expected_model=radio.model
    )
    radio._lan_identity_answer_epoch = radio._civ_epoch
    with patch.object(radio._civ_runtime, "start_data_watchdog"):
        await radio._finish_lan_identity_completion()


async def test_hold_defers_recovery_until_completion_then_recovers_once() -> None:
    """(1) Hold active after connect(): no recovery is attempted during the
    hold; the hold's completion on the same epoch recovers exactly once."""
    recovery_cb = MagicMock()
    radio = _make_radio(on_audio_recovery=recovery_cb)
    _install_stream(radio)
    session = radio.audio_session
    sub = await session.subscribe_rx("web")

    async def connect_into_hold() -> None:
        _hold_link(radio)

    await _run_hard_reconnect(radio, connect_into_hold)

    # During the hold no recovery was attempted — nothing FAILED.
    states = [c.args[0] for c in recovery_cb.call_args_list]
    assert states == []
    fresh: _StatefulFakeStream = _install_stream(radio)

    await _complete_hold(radio)

    states = [c.args[0] for c in recovery_cb.call_args_list]
    assert states == [AudioRecoveryState.RECOVERING, AudioRecoveryState.RECOVERED]
    assert fresh.start_rx_count == 1  # exactly one recovery
    assert fresh.state is AudioState.RECEIVING
    assert fresh.last_start_rx_callback == session.bus._on_opus_packet
    assert session.bus.subscriber_count == 1
    await sub.release()


async def test_no_hold_recovers_at_once_exactly_once() -> None:
    """(2) No hold after connect(): recovery runs at once, exactly once —
    unchanged from the pre-fix behavior."""
    recovery_cb = MagicMock()
    radio = _make_radio(on_audio_recovery=recovery_cb)
    stream = _install_stream(radio)
    sub = await radio.audio_session.subscribe_rx("web")

    async def connect_clean() -> None:
        radio._conn_state = RadioConnectionState.CONNECTED

    await _run_hard_reconnect(radio, connect_clean)

    states = [c.args[0] for c in recovery_cb.call_args_list]
    assert states == [AudioRecoveryState.RECOVERING, AudioRecoveryState.RECOVERED]
    assert stream.start_rx_count == 1
    assert stream.state is AudioState.RECEIVING
    # Nothing was deferred: no later completion can recover a second time.
    assert getattr(radio, "_hard_reconnect_deferred_audio", None) is None
    await sub.release()


async def test_owner_stop_before_completion_drops_deferred_recovery() -> None:
    """(3a) An owner stop — ``CoreRadio.disconnect`` bumps
    ``_owner_stop_epoch`` — before the hold completes drops the deferred
    recovery."""
    radio = _make_radio()
    _install_stream(radio)
    sub = await radio.audio_session.subscribe_rx("web")

    async def connect_into_hold() -> None:
        _hold_link(radio)

    await _run_hard_reconnect(radio, connect_into_hold)
    fresh = _install_stream(radio)
    radio._owner_stop_epoch += 1  # what CoreRadio.disconnect does while held

    await _complete_hold(radio)

    assert fresh.start_rx_count == 0  # dropped: the owner stopped the radio
    await sub.release()


async def test_epoch_change_before_completion_drops_deferred_recovery() -> None:
    """(3b) A new CI-V epoch before the hold completes drops the deferred
    recovery: the snapshot belongs to the replaced link."""
    radio = _make_radio()
    _install_stream(radio)
    sub = await radio.audio_session.subscribe_rx("web")

    async def connect_into_hold() -> None:
        _hold_link(radio)

    await _run_hard_reconnect(radio, connect_into_hold)
    fresh = _install_stream(radio)
    radio._civ_runtime.advance_generation("test-superseded")

    await _complete_hold(radio)

    assert fresh.start_rx_count == 0  # dropped: the link was replaced
    await sub.release()
