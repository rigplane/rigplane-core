"""MOR-3064: connect-time Icom LAN identity gate (CoreRadio) tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.radio_protocol import RadioIdentity, RadioIdentityStatus
from rigplane.exceptions import ConnectionError
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState

from _helpers import wrap_civ_in_udp
from test_radio import MockTransport

_IDENTITY_CMD = 0x19
_IDENTITY_SUB = 0x00


def _identity_response(payload: bytes, *, address: int = IC_7610_ADDR) -> bytes:
    """A well-formed 19 00 model-id reply from the radio, wrapped in UDP."""
    civ = build_civ_frame(
        CONTROLLER_ADDR,
        address,
        _IDENTITY_CMD,
        sub=_IDENTITY_SUB,
        data=payload,
    )
    return wrap_civ_in_udp(civ)


def _ack_response() -> bytes:
    """A bare 0xFB ACK — never a model-id answer, whatever its address is."""
    civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0xFB)
    return wrap_civ_in_udp(civ)


def _malformed_identity_response() -> bytes:
    """A 19 00 echo with an empty payload — not a model identification."""
    civ = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, _IDENTITY_CMD, sub=_IDENTITY_SUB
    )
    return wrap_civ_in_udp(civ)


def _session_lifecycle_double(radio: IcomRadio) -> AsyncMock:
    """Drive CONNECTED/DISCONNECTED as the actual lifecycle does."""

    async def _connect() -> None:
        radio._conn_state = RadioConnectionState.CONNECTED
        radio._civ_runtime.start_data_watchdog()

    async def _disconnect() -> None:
        await radio._civ_runtime.stop_data_watchdog()
        radio._conn_state = RadioConnectionState.DISCONNECTED

    async def _soft_reconnect() -> None:
        radio._conn_state = RadioConnectionState.CONNECTED

    lifecycle = AsyncMock()
    lifecycle.connect = AsyncMock(side_effect=_connect)
    lifecycle.disconnect = AsyncMock(side_effect=_disconnect)
    lifecycle.soft_reconnect = AsyncMock(side_effect=_soft_reconnect)
    return lifecycle


def make_radio(
    *,
    expected_ids: tuple[str, ...] | None = (),
    timeout: float = 0.5,
) -> tuple[IcomRadio, MockTransport]:
    """Real CoreRadio over a queued-response mock transport."""
    radio = IcomRadio("192.168.1.100", model="IC-7610", timeout=timeout)
    if expected_ids is not None:
        radio._profile = replace(radio._profile, expected_identity_ids=expected_ids)
    transport = MockTransport()
    transport._udp_transport = object()  # control session looks open
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._session_lifecycle = _session_lifecycle_double(radio)
    radio._fetch_initial_state = AsyncMock()
    radio._arm_managed_tx = AsyncMock()
    radio._ensure_audio_transport = AsyncMock()
    radio.rearm_managed_tx = AsyncMock()
    return radio, transport


def _fast_reread(radio: IcomRadio) -> None:
    """Collapse the re-read backoff so a late answer lands in one loop turn."""
    radio._LAN_IDENTITY_REREAD_BACKOFF_S = (0.02,)
    radio._LAN_IDENTITY_REREAD_STEADY_S = 0.02


async def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.005)
    return True


async def _click(radio: IcomRadio) -> None:
    """Event-loop nudge helpers (best-effort, bounded)."""
    for _ in range(5):
        await asyncio.sleep(0)


def _status(radio: IcomRadio) -> RadioIdentityStatus | None:
    identity = radio.connection_identity
    return None if identity is None else identity.status


@pytest.mark.parametrize(
    "payload,status",
    [
        (b"\x98", RadioIdentityStatus.VERIFIED),
        (b"\x94", RadioIdentityStatus.IDENTITY_MISMATCH),
    ],
)
async def test_real_profile_id_is_independent_of_configured_bus_address(
    payload: bytes,
    status: RadioIdentityStatus,
) -> None:
    radio, transport = make_radio(expected_ids=None)
    radio._radio_addr = 0x90
    assert radio._profile.expected_identity_ids == ("98",)
    transport.queue_response(_identity_response(payload, address=0x90))

    await radio.connect()

    assert _status(radio) is status
    assert radio.connected is (status is RadioIdentityStatus.VERIFIED)
    await radio.disconnect()


async def test_identity_is_none_before_any_connect() -> None:
    radio, _transport = make_radio()
    assert radio.connection_identity is None


async def test_transport_open_during_connect_still_refuses_commands() -> None:
    radio, transport = make_radio(expected_ids=("98",))
    opened, release = asyncio.Event(), asyncio.Event()

    async def connect() -> None:
        radio._conn_state = RadioConnectionState.CONNECTED
        opened.set()
        await release.wait()

    radio._session_lifecycle.connect.side_effect = connect
    radio._read_lan_identity_payload = AsyncMock(return_value=b"\x98")
    task = asyncio.create_task(radio.connect())
    await asyncio.wait_for(opened.wait(), 1)
    try:
        assert radio.connected is False
        with pytest.raises(ConnectionError):
            await radio.set_freq(14_200_000)
        assert transport.sent_packets == []
    finally:
        release.set()
        await task
        await radio.disconnect()


async def test_two_connect_callers_share_one_identity_read() -> None:
    radio, _ = make_radio(expected_ids=("98",))
    opened, answer = asyncio.Event(), asyncio.Event()

    async def connect() -> None:
        await opened.wait()
        radio._conn_state = RadioConnectionState.CONNECTED

    radio._session_lifecycle.connect.side_effect = connect

    async def read() -> bytes:
        await answer.wait()
        return b"\x98"

    radio._read_lan_identity_payload = AsyncMock(side_effect=read)
    calls = [asyncio.create_task(radio.connect()) for _ in range(2)]
    await asyncio.sleep(0)
    opened.set()
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert radio._read_lan_identity_payload.await_count == 1
    finally:
        answer.set()
        await asyncio.gather(*calls)
        await radio.disconnect()


async def test_verified_answer_completes_the_connect() -> None:
    radio, transport = make_radio(expected_ids=("0B90",))
    transport.queue_response(_identity_response(b"\x0b\x90"))
    await radio.connect()

    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.VERIFIED
    assert identity.expected_model == "IC-7610"
    assert identity.answered_id == "0B90"
    assert identity.answered_model == "IC-7610"
    radio._fetch_initial_state.assert_awaited_once()
    radio._arm_managed_tx.assert_awaited_once()
    assert radio.connected is True
    task = radio._lan_identity_reread_task
    assert task is None or task.done()


async def test_raw_answer_without_expected_metadata_is_unverified() -> None:
    radio, transport = make_radio()
    transport.queue_response(_identity_response(b"\x0b\x90"))
    await radio.connect()

    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.UNVERIFIED
    assert identity.answered_id == "0B90"
    assert identity.answered_model is None
    assert radio.connected is True


@pytest.mark.parametrize(
    "expected_ids,answer",
    [
        (("0b90",), b"\x0b\x90"),
        (("0B90",), b"\x01\x06"),
    ],
)
async def test_wrong_or_low_token_records_mismatch_and_holds(
    expected_ids: tuple[str, ...], answer: bytes
) -> None:
    """MISMATCH: a wrong radio, or a noncanonical (low-case) expected token."""
    radio, transport = make_radio(expected_ids=expected_ids)
    transport.queue_response(_identity_response(answer))
    await radio.connect()

    assert radio.connected is False
    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.IDENTITY_MISMATCH
    assert identity.answered_id == answer.hex().upper()
    radio._fetch_initial_state.assert_not_awaited()
    radio._arm_managed_tx.assert_not_awaited()
    assert transport.disconnected is False
    await radio.disconnect()


async def test_silent_radio_holds_no_response_without_false_connected() -> None:
    radio, transport = make_radio(expected_ids=("0B90",))
    await radio.connect()  # must return, not raise, on silence

    assert radio.connected is False
    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.NO_RESPONSE
    assert identity.answered_id is None
    assert radio._conn_state is RadioConnectionState.CONNECTING
    radio._fetch_initial_state.assert_not_awaited()
    radio._arm_managed_tx.assert_not_awaited()
    assert transport.disconnected is False
    task = radio._lan_identity_reread_task
    assert task is not None and not task.done()
    await radio.disconnect()


@pytest.mark.parametrize("answer", [_ack_response, _malformed_identity_response])
async def test_non_identity_answers_are_not_answers(
    answer: Callable[[], bytes],
) -> None:
    """Bare ACKs and empty 19 00 payload never count as identification."""
    radio, transport = make_radio(expected_ids=("0B90",))
    transport.queue_response(answer())
    await radio.connect()

    assert _status(radio) is RadioIdentityStatus.NO_RESPONSE
    assert radio.connected is False
    await radio.disconnect()


async def test_narrow_power_on_passes_during_no_response_hold() -> None:
    radio, transport = make_radio(expected_ids=("0B90",))
    await radio.connect()
    assert _status(radio) is RadioIdentityStatus.NO_RESPONSE

    await radio.set_powerstat(True)  # POWER ON only — must not raise
    sent = transport.sent_packets[-1]
    assert sent.endswith(b"\xfe\xfe\x98\xe0\x18\x01\xfd"), (
        "POWER ON 0x18 0x01 went out on the held session"
    )
    await radio.disconnect()


async def test_power_off_is_still_refused_while_held() -> None:
    radio, _transport = make_radio(expected_ids=("0B90",))
    await radio.connect()
    assert _status(radio) is RadioIdentityStatus.NO_RESPONSE

    with pytest.raises(ConnectionError):
        await radio.power_control(False)
    await radio.disconnect()


async def test_duplicate_connect_while_held_is_a_noop() -> None:
    radio, transport = make_radio(expected_ids=("0B90",))
    await radio.connect()
    await radio.connect()  # must not re-read or reopen anything

    assert radio._session_lifecycle.connect.await_count == 1
    sent_ids = [pkt for pkt in transport.sent_packets if pkt.endswith(b"\x19\x00\xfd")]
    assert len(sent_ids) == 1
    await radio.disconnect()


async def test_legacy_injected_connected_radio_is_not_re_gated() -> None:
    """Manually-injected legacy test states keep working: no gate re-read."""
    radio, transport = make_radio()
    radio._connected = True
    await radio.connect()

    assert radio._session_lifecycle.connect.await_count == 0
    assert transport.sent_packets == []
    assert radio.connection_identity is None
    assert radio.connected is True
    radio._connected = False  # keep __del__ quiet, like the shared fixture


async def test_late_answer_completes_the_held_connect_then_tears_down() -> None:
    radio, transport = make_radio(expected_ids=("0B90",))
    _fast_reread(radio)
    await radio.connect()
    assert _status(radio) is RadioIdentityStatus.NO_RESPONSE

    transport.queue_response_on_send(
        len(transport.sent_packets) + 1, _identity_response(b"\x0b\x90")
    )
    assert await _wait_until(lambda: radio.connected)

    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.VERIFIED
    radio._fetch_initial_state.assert_awaited_once()
    radio._arm_managed_tx.assert_awaited_once()
    assert radio._session_lifecycle.connect.await_count == 1
    assert radio._lan_identity_answer_epoch == radio._civ_epoch
    task = radio._lan_identity_reread_task
    assert task is None or task.done()
    await radio.disconnect()


async def test_disconnect_cancels_the_re_read_and_clears_identity() -> None:
    radio, transport = make_radio(expected_ids=("0B90",))
    _fast_reread(radio)
    await radio.connect()
    task = radio._lan_identity_reread_task
    assert task is not None and not task.done()

    await radio.disconnect()
    while not task.done():
        await asyncio.sleep(0)
    assert task.done()
    assert radio.connection_identity is None
    assert radio._lan_identity_answer_epoch is None

    transport.queue_response(_identity_response(b"\x0b\x90"))
    await _click(radio)
    assert radio.connected is False
    assert radio.connection_identity is None
    radio._fetch_initial_state.assert_not_awaited()


async def test_reconnect_after_disconnect_re_answers_at_the_new_epoch() -> None:
    radio, transport = make_radio(expected_ids=("0B90",))
    transport.queue_response(_identity_response(b"\x0b\x90"))
    await radio.connect()
    await radio.disconnect()
    assert radio.connection_identity is None

    transport.queue_response_on_send(
        len(transport.sent_packets) + 1, _identity_response(b"\x0b\x90")
    )
    await radio.connect()
    assert _status(radio) is RadioIdentityStatus.VERIFIED
    assert radio.connected is True
    assert radio._lan_identity_answer_epoch == radio._civ_epoch
    await radio.disconnect()


async def test_silence_hold_stops_data_watchdog_until_answer_restarts_it() -> None:
    """The identity wait owns the session: watchdog paused, never a reopen."""
    radio, transport = make_radio(expected_ids=("0B90",))
    _fast_reread(radio)
    await radio.connect()
    assert _status(radio) is RadioIdentityStatus.NO_RESPONSE

    wd = radio._civ_data_watchdog_task
    assert wd is None or wd.done()
    transport.queue_response_on_send(
        len(transport.sent_packets) + 1, _identity_response(b"\x0b\x90")
    )
    assert await _wait_until(lambda: radio.connected)
    wd = radio._civ_data_watchdog_task
    assert wd is not None and not wd.done()
    assert radio._session_lifecycle.connect.await_count == 1
    await radio.disconnect()


async def test_mismatch_hold_stops_data_watchdog_without_restarting() -> None:
    radio, transport = make_radio(expected_ids=("0B90",))
    transport.queue_response(_identity_response(b"\x01\x06"))
    await radio.connect()
    assert _status(radio) is RadioIdentityStatus.IDENTITY_MISMATCH

    wd = radio._civ_data_watchdog_task
    assert wd is None or wd.done()
    assert radio._session_lifecycle.connect.await_count == 1
    await radio.disconnect()


async def test_checking_hold_refuses_a_normal_write_at_the_command_gate() -> None:
    """While ``checking``, plain writes raise ConnectionError, no wire bytes."""
    radio, transport = make_radio(expected_ids=("0B90",))
    # Park the status exactly where an in-flight gate leaves it.
    radio._connected = True
    radio._connection_identity = RadioIdentity(
        status=RadioIdentityStatus.CHECKING, expected_model="IC-7610"
    )
    with pytest.raises(ConnectionError):
        await radio.set_freq(7_074_000)
    assert transport.sent_packets == []


async def test_reconnect_tail_revalidates_then_runs_parts_once() -> None:
    radio, transport = make_radio()
    _fast_reread(radio)
    transport.queue_response(_identity_response(b"\x0b\x90"))
    await radio.connect()
    on_reconnect = MagicMock()
    radio._on_reconnect = on_reconnect

    await radio._control_phase._after_reconnect(None, None)
    assert _status(radio) is RadioIdentityStatus.NO_RESPONSE
    assert radio.connected is False
    radio._fetch_initial_state.assert_awaited_once()
    radio.rearm_managed_tx.assert_not_awaited()
    on_reconnect.assert_not_called()
    radio._ensure_audio_transport.assert_not_awaited()

    transport.queue_response_on_send(
        len(transport.sent_packets) + 1, _identity_response(b"\x0b\x90")
    )
    assert await _wait_until(lambda: radio.connected)
    radio.rearm_managed_tx.assert_awaited_once()
    on_reconnect.assert_called_once()
    radio._ensure_audio_transport.assert_awaited_once()
    await radio.disconnect()


async def test_reconnect_tail_runs_immediately_when_answered() -> None:
    """With the gate answered per the current epoch, the tail runs once."""
    radio, transport = make_radio()
    transport.queue_response(_identity_response(b"\x0b\x90"))
    await radio.connect()
    on_reconnect = MagicMock()
    radio._on_reconnect = on_reconnect

    transport.queue_response_on_send(
        len(transport.sent_packets) + 1, _identity_response(b"\x0b\x90")
    )
    await radio._control_phase._after_reconnect(None, None)
    radio.rearm_managed_tx.assert_awaited_once()
    on_reconnect.assert_called_once()
    radio._ensure_audio_transport.assert_awaited_once()
    assert _status(radio) is RadioIdentityStatus.UNVERIFIED
    await radio.disconnect()


async def test_revalidation_error_reaches_the_recovery_owner() -> None:
    """A broken hook must not be labelled verified: reach the ladder owner."""
    radio, transport = make_radio()
    transport.queue_response(_identity_response(b"\x0b\x90"))
    await radio.connect()
    radio.revalidate_connection_identity = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await radio._control_phase._after_reconnect(None, None)
    await radio.disconnect()


async def test_epoch_ownership_fences_stale_answers() -> None:
    """The recorded answer belongs to its CI-V generation, never older."""
    radio, transport = make_radio(expected_ids=("0B90",))
    transport.queue_response(_identity_response(b"\x0b\x90"))
    await radio.connect()
    assert radio._lan_identity_answer_epoch is not None

    radio._civ_runtime.advance_generation("test-fence")
    assert radio._lan_identity_unanswered_for_current_epoch() is True
    sent_before = len(transport.sent_packets)
    with pytest.raises(ConnectionError):
        await radio.set_freq(14_200_000)
    assert len(transport.sent_packets) == sent_before
    await radio.disconnect()


async def test_stale_epoch_answer_is_fenced_and_never_completes() -> None:
    """An old gate's read must not stamp a newer epoch or resurrect state."""
    radio, transport = make_radio(expected_ids=("0B90",))
    _fast_reread(radio)
    await radio.connect()
    assert _status(radio) is RadioIdentityStatus.NO_RESPONSE

    radio._civ_runtime.advance_generation("test-fence")
    transport.queue_response(_identity_response(b"\x0b\x90"))
    assert await _wait_until(
        lambda: (
            radio._lan_identity_reread_task is None
            or radio._lan_identity_reread_task.done()
        )
    )
    assert radio.connected is False
    assert radio._lan_identity_answer_epoch is None
    await radio.disconnect()
