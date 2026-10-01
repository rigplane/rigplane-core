"""MOR-3064: connect-time Icom LAN identity gate (CoreRadio) tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.radio_protocol import RadioIdentity, RadioIdentityStatus
from rigplane.exceptions import ConnectionError
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState

from _helpers import wrap_civ_in_udp
from test_radio import MockTransport
from test_radio_connect import ConnectMockTransport, _ConnectOnceStubs

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


def _session_lifecycle_double(radio: IcomRadio) -> AsyncMock:
    """Drive CONNECTED/DISCONNECTED as the actual lifecycle does."""

    async def _connect() -> None:
        radio._conn_state = RadioConnectionState.CONNECTED
        radio._civ_runtime.start_data_watchdog()

    async def _disconnect() -> None:
        await radio._civ_runtime.stop_data_watchdog()
        radio._conn_state = RadioConnectionState.DISCONNECTED

    lifecycle = AsyncMock()
    lifecycle.connect = AsyncMock(side_effect=_connect)
    lifecycle.disconnect = AsyncMock(side_effect=_disconnect)
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


def _status(radio: IcomRadio) -> RadioIdentityStatus | None:
    identity = radio.connection_identity
    return None if identity is None else identity.status


def _identity_sends(transport: MockTransport) -> int:
    """Count CI-V ``19 00`` identity requests that went out on ``transport``."""
    return sum(1 for pkt in transport.sent_packets if pkt.endswith(b"\x19\x00\xfd"))


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
    "expected_ids,answer,answered_id",
    [
        (("0b90",), _identity_response(b"\x0b\x90"), "0B90"),
        (("0B90",), _identity_response(b"\x01\x06"), "0106"),
        (("0B90",), None, None),
        (
            ("0B90",),
            wrap_civ_in_udp(build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0xFB)),
            None,
        ),
        (("0B90",), _identity_response(b""), None),
    ],
)
async def test_missing_or_wrong_identity_holds_the_link(
    expected_ids: tuple[str, ...], answer: bytes | None, answered_id: str | None
) -> None:
    radio, transport = make_radio(expected_ids=expected_ids)
    if answer is not None:
        transport.queue_response(answer)
    await radio.connect()
    assert radio.connected is False
    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is (
        RadioIdentityStatus.NO_RESPONSE
        if answered_id is None
        else RadioIdentityStatus.IDENTITY_MISMATCH
    )
    assert identity.answered_id == answered_id
    assert radio._conn_state is RadioConnectionState.CONNECTING
    radio._fetch_initial_state.assert_not_awaited()
    radio._arm_managed_tx.assert_not_awaited()
    assert transport.disconnected is False
    watchdog = radio._civ_data_watchdog_task
    assert watchdog is None or watchdog.done()
    task = radio._lan_identity_reread_task
    assert task is not None and not task.done()
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
    assert _status(radio) is RadioIdentityStatus.NO_RESPONSE

    with pytest.raises(ConnectionError):
        await radio.power_control(False)

    radio._civ_runtime.start_data_watchdog()  # a detached-finally re-arm cannot restart it held
    watchdog = radio._civ_data_watchdog_task
    assert watchdog is None or watchdog.done()
    await radio.disconnect()


async def test_duplicate_connect_while_held_is_a_noop() -> None:
    radio, transport = make_radio(expected_ids=("0B90",))
    await radio.connect()
    await radio.connect()  # must not re-read or reopen anything

    assert radio._session_lifecycle.connect.await_count == 1
    sent_ids = [pkt for pkt in transport.sent_packets if pkt.endswith(b"\x19\x00\xfd")]
    assert len(sent_ids) == 1
    radio._cancel_lan_identity_reread()
    await asyncio.sleep(0)
    transport.queue_response_on_send(
        len(transport.sent_packets) + 1, _identity_response(b"\x0b\x90")
    )
    await radio.connect()
    assert _status(radio) is RadioIdentityStatus.VERIFIED
    assert _identity_sends(transport) == 2
    await radio.disconnect()


async def test_legacy_injected_connected_radio_is_not_re_gated() -> None:
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
    watchdog = radio._civ_data_watchdog_task
    assert watchdog is None or watchdog.done()

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
    watchdog = radio._civ_data_watchdog_task
    assert watchdog is not None and not watchdog.done()
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
    for _ in range(5):
        await asyncio.sleep(0)
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


async def test_checking_hold_refuses_a_normal_write_at_the_command_gate() -> None:
    radio, transport = make_radio(expected_ids=("0B90",))
    # Park the status exactly where an in-flight gate leaves it.
    radio._connected = True
    radio._connection_identity = RadioIdentity(
        status=RadioIdentityStatus.CHECKING, expected_model="IC-7610"
    )
    with pytest.raises(ConnectionError):
        await radio.set_freq(7_074_000)
    assert transport.sent_packets == []


@pytest.mark.parametrize("pre_answered", [True, False], ids=["immediate", "late"])
async def test_reconnect_tail_gates_then_runs_parts_once(pre_answered: bool) -> None:
    radio, transport = make_radio()
    _fast_reread(radio)
    transport.queue_response(_identity_response(b"\x0b\x90"))
    await radio.connect()
    on_reconnect = MagicMock()
    radio._on_reconnect = on_reconnect

    if pre_answered:
        transport.queue_response_on_send(
            len(transport.sent_packets) + 1, _identity_response(b"\x0b\x90")
        )
        await radio._control_phase._after_reconnect(None, None)
        assert _status(radio) is RadioIdentityStatus.UNVERIFIED
    else:
        # Nothing queued for the tail: its fresh gate goes unanswered.
        await radio._control_phase._after_reconnect(None, None)
        assert _status(radio) is RadioIdentityStatus.NO_RESPONSE
        assert radio.connected is False
        radio.rearm_managed_tx.assert_not_awaited()
        on_reconnect.assert_not_called()
        radio._ensure_audio_transport.assert_not_awaited()
        transport.queue_response_on_send(
            len(transport.sent_packets) + 1, _identity_response(b"\x0b\x90")
        )
        assert await _wait_until(lambda: radio.connected)
    radio._fetch_initial_state.assert_awaited_once()
    radio.rearm_managed_tx.assert_awaited_once()
    on_reconnect.assert_called_once()
    radio._ensure_audio_transport.assert_awaited_once()
    await radio.disconnect()


async def test_revalidation_error_reaches_the_recovery_owner() -> None:
    radio, transport = make_radio()
    transport.queue_response(_identity_response(b"\x0b\x90"))
    await radio.connect()
    radio.revalidate_connection_identity = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await radio._control_phase._after_reconnect(None, None)
    await radio.disconnect()


async def test_epoch_ownership_fences_stale_answers() -> None:
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

    await radio._civ_runtime.stop_data_watchdog()
    radio._civ_runtime.start_data_watchdog()  # stale answer epoch: refused
    watchdog = radio._civ_data_watchdog_task
    assert watchdog is None or watchdog.done()
    await radio.disconnect()


async def test_stale_epoch_answer_is_fenced_and_never_completes() -> None:
    radio, transport = make_radio(expected_ids=("0B90",))
    _fast_reread(radio)
    await radio.connect()
    assert _status(radio) is RadioIdentityStatus.NO_RESPONSE

    radio._civ_runtime.advance_generation("test-fence")
    assert radio._lan_identity_phase_owns_open_link() is False
    with pytest.raises(ConnectionError):
        await radio.power_control(True)
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


# ---------------------------------------------------------------------------
# Real cold ``_connect_once``: the native identity gate must read CI-V
# ``19 00`` after pump/worker/control ownership and BEFORE the public
# startup-readiness wait. Only the auth handshake and transport
# construction are stubbed; gate, pump, worker, watchdog, token renewal
# and ``wait_for_radio_startup_ready`` all stay real.
# ---------------------------------------------------------------------------


class _ReconnectingCtrl(ConnectMockTransport):
    """Control transport that looks gone until a real (re)connect revives it."""

    def __init__(self) -> None:
        super().__init__()
        self._udp_transport = None

    async def connect(
        self,
        host: str,
        port: int,
        *,
        local_host: str | None = None,
        local_port: int = 0,
        sock: object | None = None,
    ) -> None:
        await super().connect(
            host, port, local_host=local_host, local_port=local_port, sock=sock
        )
        self._udp_transport = object()

    async def reconnect(
        self, host: str, port: int, *, local_host: str | None = None
    ) -> None:
        await super().reconnect(host, port, local_host=local_host)
        self._udp_transport = object()


def _cold_radio(
    *, expected_ids: tuple[str, ...], timeout: float = 0.5
) -> tuple[IcomRadio, ConnectMockTransport, ConnectMockTransport]:
    """Real CoreRadio aimed at a real cold ``_connect_once`` over mock transports."""
    radio = IcomRadio(
        "192.168.1.100", username="u", password="p", model="IC-7610", timeout=timeout
    )
    radio._profile = replace(radio._profile, expected_identity_ids=expected_ids)
    radio._session_lifecycle = _session_lifecycle_double(radio)
    radio._fetch_initial_state = AsyncMock()
    radio._arm_managed_tx = AsyncMock()
    radio._ensure_audio_transport = AsyncMock()
    radio.rearm_managed_tx = AsyncMock()
    ctrl = _ReconnectingCtrl()
    radio._ctrl_transport = ctrl
    civ = ConnectMockTransport()
    civ._udp_transport = object()
    return radio, ctrl, civ


_COLD_STUBS_EXCLUDED = frozenset(
    {
        "_start_token_renewal",
        "advance_generation",
        "start_pump",
        "start_data_watchdog",
        "start_worker",
        "IcomTransport",
        "sleep",
        "wait_for_radio_startup_ready",
    }
)


class _ColdConnectPatches(_ConnectOnceStubs):
    """``_ConnectOnceStubs`` narrowed to the auth handshake, with the fake
    CI-V transport under the test's control."""

    def __init__(self, radio: IcomRadio, civ: ConnectMockTransport) -> None:
        super().__init__(radio)

        self._patches = tuple(
            p for p in self._patches if p.attribute not in _COLD_STUBS_EXCLUDED
        ) + (
            patch.object(
                radio._control_phase,
                "_receive_guid",
                new=AsyncMock(return_value=None),
            ),
            patch("rigplane.transport.IcomTransport", return_value=civ),
        )


async def _cold_teardown(radio: IcomRadio) -> None:
    """Stop what a real cold attempt started, without the lifecycle."""
    radio._control_phase._close_pending_sockets()
    reread = radio._lan_identity_reread_task
    radio._cancel_lan_identity_reread()
    if reread is not None:
        with suppress(asyncio.CancelledError):
            await reread
    await radio._civ_runtime.stop_data_watchdog()
    await radio._civ_runtime.stop_worker()
    await radio._civ_runtime.stop_pump()
    token = radio._token_task
    if token is not None and not token.done():
        token.cancel()
        with suppress(asyncio.CancelledError):
            await token
    radio._token_task = None
    radio._conn_state = RadioConnectionState.DISCONNECTED


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (_identity_response(b"\x0b\x90"), RadioIdentityStatus.VERIFIED),
        (_identity_response(b"\x01\x06"), RadioIdentityStatus.IDENTITY_MISMATCH),
        (None, RadioIdentityStatus.NO_RESPONSE),
    ],
    ids=["verified", "wrong", "silent"],
)
async def test_cold_connect_once_gates_identity_before_startup_readiness(
    answer: bytes | None, status: RadioIdentityStatus
) -> None:
    radio, _ctrl, civ = _cold_radio(expected_ids=("0B90",))
    renew = AsyncMock()
    radio._control_phase.TOKEN_RENEWAL_INTERVAL = 0.01
    with (
        _ColdConnectPatches(radio, civ),
        patch.object(radio._control_phase, "_send_token", new=renew),
    ):
        if answer is not None:
            civ.queue_response_on_send(2, answer)  # reply to the gate's own read
        try:
            await radio._control_phase._connect_once()  # the readiness wait stays real
            assert _status(radio) is status
            assert _identity_sends(civ) == 1
            radio._fetch_initial_state.assert_not_awaited()
            radio._arm_managed_tx.assert_not_awaited()
            watchdog = radio._civ_data_watchdog_task
            if status is RadioIdentityStatus.VERIFIED:
                assert radio.radio_ready is True
                assert watchdog is not None and not watchdog.done()
                assert radio._lan_identity_answer_epoch == radio._civ_epoch
            else:
                # Owned hold, not a fatal readiness timeout.
                assert radio._conn_state is RadioConnectionState.CONNECTING
                assert watchdog is None or watchdog.done()
                task = radio._lan_identity_reread_task
                assert task is not None and not task.done()
            # The LAN session stays maintained while the hold owns the link.
            assert radio._token_task is not None and not radio._token_task.done()
            renew.reset_mock()
            assert await _wait_until(lambda: renew.await_count >= 2)
            assert all(call.args == (0x05,) for call in renew.await_args_list)
        finally:
            await _cold_teardown(radio)


async def test_cold_late_answer_before_lifecycle_returns_fetches_and_arms_once() -> (
    None
):
    radio, _ctrl, civ = _cold_radio(expected_ids=("0B90",))
    _fast_reread(radio)
    park = asyncio.Event()

    async def connect() -> None:
        await radio._control_phase._connect_once()  # native gate holds inside
        await park.wait()

    radio._session_lifecycle.connect.side_effect = connect
    with _ColdConnectPatches(radio, civ):
        civ.queue_response_on_send(3, _identity_response(b"\x0b\x90"))
        task = asyncio.create_task(radio.connect())
        try:
            assert await _wait_until(lambda: radio.connected)
            assert await _wait_until(lambda: radio._arm_managed_tx.await_count >= 1)
            park.set()  # the accepted read landed before the lifecycle returned
            await asyncio.wait_for(task, 2)
            assert _status(radio) is RadioIdentityStatus.VERIFIED
            radio._fetch_initial_state.assert_awaited_once()
            radio._arm_managed_tx.assert_awaited_once()
            assert radio._session_lifecycle.connect.await_count == 1
            assert _identity_sends(civ) == 2  # gate read + one re-read, no re-gate
            assert radio._lan_identity_answer_epoch == radio._civ_epoch
        finally:
            park.set()
            try:
                await asyncio.wait_for(task, 2)
            except BaseException:  # teardown must not mask the real failure
                pass
            await _cold_teardown(radio)


@pytest.mark.parametrize(
    "gate_answer,before_return",
    [(b"\x0b\x90", False), (None, False), (None, True)],
    ids=["answered", "held", "late-before-return"],
)
async def test_full_control_recovery_snapshots_audio_before_gate_and_runs_one_tail(
    gate_answer: bytes | None,
    before_return: bool,
) -> None:
    radio, _ctrl, civ = _cold_radio(expected_ids=("0B90",))
    _fast_reread(radio)
    snapshots = [object(), object()]
    audio_runtime = SimpleNamespace(
        capture_snapshot=MagicMock(side_effect=snapshots),
        recover=AsyncMock(),
    )
    radio._audio_runtime = audio_runtime
    radio._auto_recover_audio = True
    radio._civ_transport = None  # CI-V gone; _ReconnectingCtrl looks gone too
    on_reconnect = MagicMock()
    radio._on_reconnect = on_reconnect

    native_read = radio._read_lan_identity_payload
    connect_once = radio._control_phase._connect_once

    async def read_after_snapshot() -> bytes | None:
        assert radio._control_phase._deferred_retail == (audio_runtime, snapshots[0])
        assert radio._lan_identity_revalidate_origin is True
        return await native_read()

    async def connect_before_return() -> None:
        await connect_once()
        if before_return:
            assert await _wait_until(lambda: audio_runtime.recover.await_count == 1)

    with (
        _ColdConnectPatches(radio, civ),
        patch.object(
            radio, "_read_lan_identity_payload", side_effect=read_after_snapshot
        ),
        patch.object(
            radio._control_phase, "_connect_once", side_effect=connect_before_return
        ),
    ):
        # Send 2 is the native gate's read; send 3 the held link's re-read.
        civ.queue_response_on_send(
            2 if gate_answer is not None else 3, _identity_response(b"\x0b\x90")
        )
        try:
            await radio._control_phase.soft_reconnect()
            if gate_answer is None and not before_return:
                assert _status(radio) is RadioIdentityStatus.NO_RESPONSE
            assert await _wait_until(lambda: radio.connected)
            assert await _wait_until(lambda: audio_runtime.recover.await_count >= 1)
            assert _status(radio) is RadioIdentityStatus.VERIFIED
            # One gate for the whole recovery: the tail reuses it, no re-gate.
            assert _identity_sends(civ) == (1 if gate_answer is not None else 2)
            radio.rearm_managed_tx.assert_awaited_once()
            on_reconnect.assert_called_once()
            radio._ensure_audio_transport.assert_awaited_once()
            # The snapshot captured BEFORE the gate is the one consumed.
            audio_runtime.capture_snapshot.assert_called_once()
            audio_runtime.recover.assert_awaited_once_with(snapshots[0])
            radio._fetch_initial_state.assert_not_awaited()
            radio._arm_managed_tx.assert_not_awaited()
            watchdog = radio._civ_data_watchdog_task
            assert watchdog is not None and not watchdog.done()
        finally:
            await _cold_teardown(radio)


async def test_soft_reconnect_while_cold_identity_phase_owns_link_is_a_noop() -> None:
    radio, transport = make_radio(expected_ids=("0B90",))
    await radio.connect()
    assert _status(radio) is RadioIdentityStatus.NO_RESPONSE
    reread = radio._lan_identity_reread_task

    await radio.soft_reconnect()

    assert radio._civ_transport is transport
    assert transport.disconnected is False
    assert radio._lan_identity_reread_task is reread and not reread.done()
    assert radio._conn_state is RadioConnectionState.CONNECTING
    assert _status(radio) is RadioIdentityStatus.NO_RESPONSE
    assert _identity_sends(transport) == 1
    await radio.disconnect()


async def test_stale_gate_exit_cannot_clear_the_new_gate_owner() -> None:
    radio, _transport = make_radio(expected_ids=("0B90",))
    release = asyncio.Event()

    async def parked_read() -> bytes | None:
        await release.wait()
        return b"\x01\x06"

    radio._read_lan_identity_payload = AsyncMock(side_effect=parked_read)
    stale_gate = asyncio.create_task(radio._gate_lan_identity())
    await asyncio.sleep(0)  # the first gate is parked inside its read
    radio._civ_runtime.advance_generation("replaced")
    # A newer gate now owns the replaced link (same footprint: current
    # owned epoch plus a running gate) and is parked the same way.
    radio._lan_identity_owned_epoch = radio._civ_epoch

    release.set()
    assert await stale_gate is False  # stale: its answer belongs to a replaced link
    assert radio._lan_identity_gate_running is True  # the new owner kept its flag

    radio._lan_identity_gate_running = False
    radio._lan_identity_owned_epoch = None
    await radio.disconnect()
