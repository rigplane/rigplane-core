"""LAN transport rebuilds preserve session-owned RX demand and bus wiring."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import get_args

import pytest
import pytest_asyncio
from test_audio import _make_audio_bytes
from test_audio_reconnect_rearm import _connect_fd_only, _reserve_audio_socket
from test_managed_tx_authority import FakeConfigStore, FakeFence, FakeLane
from test_web_command_batch_http import _post_json
from test_web_transport_connection import FakeConnection

from rigplane.audio import AudioState, AudioStream
from rigplane.audio.usb_driver import AudioAlreadyStartedError
from rigplane.audio.session import (
    AudioSession,
    AudioSessionState,
    RX_LIVENESS_TIMEOUT_S,
    TxLease,
)
from rigplane.core.transport import DISCOVERY_RETRIES, DISCOVERY_TIMEOUT, IcomTransport
from rigplane.core.exceptions import StaleBeforeWireError
from rigplane.core.radio_protocol import AudioRetryCapable
from rigplane.core.state_pipeline_contracts import Observation, SourceMetadata
from rigplane.core.types import CivFrame
from rigplane.core.tx_observation import OBSERVED_PTT_PATH, ObservedPtt
from rigplane.runtime._audio_recovery import AudioRecoveryState
from rigplane.runtime.controller_authority import ControllerTicket
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_state import ActuationOperation, ManagedTxOutcome
from rigplane.runtime.radio import IcomRadio
from rigplane.testing.timing import ManualClock, VirtualClock, await_until
from rigplane.web.api_contract import AudioRetryRefusalReason
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.protocol import decode_json, encode_json
from rigplane.web.server import WebConfig, WebServer
from rigplane.ws import WS_OP_TEXT


async def _request_audio_retry(radio, ingress: str, *, receiver: int = 0) -> dict:
    request = {
        "type": "cmd",
        "id": "retry-audio",
        "name": "audio_retry",
        "params": {"receiver": receiver},
    }
    if ingress == "websocket":
        connection = FakeConnection()
        handler = ControlHandler(connection, radio, "test", "test", read_only=True)
        await handler._handle_text(encode_json(request))
        assert len(connection.sent_text) == 1
        response = decode_json(connection.sent_text[0])
        assert response["type"] == "response"
    else:
        assert ingress == "http"
        server = WebServer(radio, config=WebConfig(read_only=True))
        writer = await _post_json(server, "/api/v1/commands", request)
        assert writer.response_status == 200
        response = writer.response_body
        assert response["name"] == "audio_retry"
    assert response["id"] == "retry-audio"
    assert response["ok"] is True
    return response["result"]


@pytest_asyncio.fixture
async def demanded_radio(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IcomRadio]:
    async def connect(transport: IcomTransport, host: str, port: int, **kwargs) -> None:
        sock, _ = _reserve_audio_socket()
        await _connect_fd_only(transport, host, port, sock=sock)

    async def open_close(*, open_stream: bool) -> None:
        return None

    radio = IcomRadio("127.0.0.1", model="IC-7610")
    radio._connected = True
    radio._civ_transport = IcomTransport()
    radio._audio_port = 50001
    clock = ManualClock(start=1000.0)
    session = AudioSession(radio, monotonic=clock)
    radio._audio_session = session
    session.bus._monotonic = clock
    session.bus._jitter_depth = 0
    session.add_listener(radio._on_audio_session_event)
    monkeypatch.setattr(session, "_watchdog_loop", asyncio.Event().wait)
    monkeypatch.setattr(IcomTransport, "connect", connect)
    monkeypatch.setattr(radio, "_send_audio_open_close", open_close)
    monkeypatch.setattr(radio, "_start_audio_watchdog", lambda: None)
    try:
        yield radio
    finally:
        task = radio._audio_silence_repair_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for lease in list(session._tx_leases):
            await lease.release()
        for sub in list(session._rx_subs):
            await sub.release()
        await radio._teardown_audio_transport()
        await radio.disconnect()


@pytest.mark.asyncio
async def test_ensure_rebuild_with_held_demand_arms_bus_reader(
    demanded_radio: IcomRadio,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    sub = await session.subscribe_rx("held")
    old_transport = radio._audio_transport
    await radio._teardown_audio_transport()
    assert session.bus.rx_active

    await asyncio.wait_for(radio._ensure_audio_transport(), timeout=2.0)

    transport = radio._audio_transport
    stream = radio._audio_stream
    assert transport is not None and transport is not old_transport
    assert stream is not None and stream.state is AudioState.RECEIVING
    assert stream._rx_task is not None and not stream._rx_task.done()
    assert stream._rx_callback == session.bus._on_opus_packet
    assert session.bus.last_rx_frame_monotonic is None
    transport._handle_packet(_make_audio_bytes(b"fresh-after-ensure", send_seq=1))
    packet = await sub.get(timeout=2.0)
    assert packet is not None and packet.data == b"fresh-after-ensure"
    assert session.bus.last_rx_frame_monotonic == session._monotonic()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
@pytest.mark.parametrize(
    "state", [AudioSessionState.RECOVERING, AudioSessionState.FAILED]
)
async def test_client_reopen_after_failed_repair_delivers_and_renews_episode(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    state: AudioSessionState,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    held = await session.subscribe_rx("held")
    recovery: list[AudioRecoveryState] = []
    radio._on_audio_recovery = recovery.append
    decision_clock = session._monotonic
    assert isinstance(decision_clock, ManualClock)

    async def offline(*args, **kwargs) -> None:
        raise OSError("audio UDP unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(IcomTransport, "connect", offline)
        decision_clock.advance(RX_LIVENESS_TIMEOUT_S)
        session._check_liveness_locked()
        task = radio._audio_silence_repair_task
        assert task is not None
        async with VirtualClock.on_running_loop() as clock:
            await clock.elapse(4.0)
        assert task.done()
        await task

    assert recovery == [AudioRecoveryState.FAILED]
    assert radio._audio_silence_repair_attempts == 3
    assert radio._audio_stream is None
    assert session.state is AudioSessionState.RECOVERING
    assert session.bus.rx_active
    assert session.bus.last_rx_frame_monotonic is None
    session._state = state

    reopened = await asyncio.wait_for(session.subscribe_rx("reopened"), timeout=2.0)

    assert reopened._inner.active
    assert session.rx_demand == 2
    assert session.state is AudioSessionState.RX_ONLY
    assert radio._audio_silence_repair_attempts == 0
    assert radio._audio_silence_repair_task is None
    stream = radio._audio_stream
    transport = radio._audio_transport
    assert stream is not None and stream.state is AudioState.RECEIVING
    assert stream._rx_callback == session.bus._on_opus_packet
    assert transport is not None
    transport._handle_packet(_make_audio_bytes(b"fresh-after-failed", send_seq=1))
    for sub in (held, reopened):
        packet = await sub.get(timeout=2.0)
        assert packet is not None and packet.data == b"fresh-after-failed"
    assert session.bus.last_rx_frame_monotonic == session._monotonic()

    decision_clock.advance(RX_LIVENESS_TIMEOUT_S)
    session._check_liveness_locked()
    next_episode = radio._audio_silence_repair_task
    assert next_episode is not None
    next_episode.cancel()
    await asyncio.gather(next_episode, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.timeout(15)
@pytest.mark.parametrize("cut_seconds", [10.0, 60.0])
@pytest.mark.parametrize("retry_ingress", ["python", "websocket", "http"])
@pytest.mark.parametrize("idle_tx", [False, True])
async def test_audio_cut_returns_with_one_retry_after_failed(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    cut_seconds: float,
    retry_ingress: str,
    idle_tx: bool,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    recovery: list[AudioRecoveryState] = []
    failed_after: list[tuple[int, float]] = []

    def report(state: AudioRecoveryState) -> None:
        recovery.append(state)
        if state is AudioRecoveryState.FAILED:
            failed_after.append((len(attempts), clock() - cut_at))

    radio._on_audio_recovery = report
    sub = await session.subscribe_rx("held-through-cut")
    if idle_tx:
        await session.acquire_tx("idle-two-way-bridge")
        assert session.tx_demand == 1
    initial_transport = radio._audio_transport
    assert initial_transport is not None
    initial_transport._handle_packet(_make_audio_bytes(b"before-cut", send_seq=1))
    assert await sub.get(timeout=2.0) is not None
    connect = IcomTransport.connect
    reestablish = session._reestablish_locked
    attempts: list[float] = []
    reestablished: list[int] = []
    link_up = asyncio.Event()

    async def cut_connect(transport, host, port, **kwargs) -> None:
        attempts.append(clock() - cut_at)
        try:
            await asyncio.wait_for(
                link_up.wait(), timeout=DISCOVERY_RETRIES * DISCOVERY_TIMEOUT
            )
        except TimeoutError as exc:
            raise OSError("audio link cut") from exc
        await connect(transport, host, port, **kwargs)

    async def record_reestablish() -> None:
        await reestablish()
        reestablished.append(1)

    async with VirtualClock.on_running_loop() as clock:
        monkeypatch.setattr(session, "_monotonic", clock)
        monkeypatch.setattr(session.bus, "_monotonic", clock)
        session._rx_armed_at = clock()
        cut_at = clock()
        restore = asyncio.get_running_loop().call_later(cut_seconds, link_up.set)
        monkeypatch.setattr(IcomTransport, "connect", cut_connect)
        monkeypatch.setattr(session, "_reestablish_locked", record_reestablish)
        try:
            await initial_transport.disconnect()
            await clock.elapse(RX_LIVENESS_TIMEOUT_S)
            session._check_liveness_locked()
            repair = radio._audio_silence_repair_task
            assert repair is not None
            await clock.elapse(cut_seconds - RX_LIVENESS_TIMEOUT_S)
            assert link_up.is_set()
            assert await await_until(repair.done, timeout=2.0)
            await repair

            if cut_seconds == 60.0:
                assert len(attempts) == 3
                assert recovery == [AudioRecoveryState.FAILED]
                assert failed_after == [(3, 36.0)]
                assert attempts == [3.0, 14.0, 26.0]
                assert radio._audio_silence_repair_attempts == 3
                assert radio._audio_transport is None
                assert reestablished == []
                # A restored link alone does not fund background retries.
                session._check_liveness_locked()
                await clock.elapse(1.0)
                assert len(attempts) == 3
                assert radio._audio_silence_repair_task is None
                assert isinstance(radio, AudioRetryCapable)
                if retry_ingress == "python":
                    assert await radio.retry_audio()
                else:
                    assert await _request_audio_retry(
                        radio, retry_ingress, receiver=1
                    ) == {"accepted": True}
                    assert await await_until(
                        lambda: (
                            len(reestablished) == 1
                            and radio._audio_silence_repair_task is None
                        ),
                        timeout=2.0,
                    )
                assert len(attempts) == 4
                assert radio._audio_silence_repair_attempts == 1
            else:
                assert len(attempts) == 1
                assert recovery == []

            assert reestablished == [1]
            assert recovery.count(AudioRecoveryState.FAILED) == (cut_seconds == 60.0)
            assert session.rx_demand == 1
            assert session.state is (
                AudioSessionState.RX_TX if idle_tx else AudioSessionState.RX_ONLY
            )
            stream = radio._audio_stream
            transport = radio._audio_transport
            assert stream is not None and transport is not None
            assert stream._rx_callback == session.bus._on_opus_packet
            assert stream._rx_task is not None and not stream._rx_task.done()
            assert transport is not initial_transport
            transport._handle_packet(_make_audio_bytes(b"after-cut", send_seq=1))
            packet = await sub.get(timeout=2.0)
            assert packet is not None and packet.data == b"after-cut"
            assert session.bus.last_rx_frame_monotonic == clock()
            session._check_liveness_locked()
            assert radio._audio_silence_repair_attempts == 0
            assert radio._audio_silence_repair_task is None
            assert session.last_event is not None
            assert session.last_event.reason == "rx_resumed"

            # A subsequent silence starts a new episode with all three attempts.
            await clock.elapse(RX_LIVENESS_TIMEOUT_S)
            session._check_liveness_locked()
            next_episode = radio._audio_silence_repair_task
            assert next_episode is not None
            assert radio._audio_silence_repair_attempts == 0
            next_episode.cancel()
            await asyncio.gather(next_episode, return_exceptions=True)
        finally:
            restore.cancel()


@pytest.mark.asyncio
async def test_failed_client_reopen_preserves_demand_and_spent_budget(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    held = await session.subscribe_rx("held")
    await radio._teardown_audio_transport()
    session._state = AudioSessionState.RECOVERING
    radio._audio_silence_repair_attempts = 3

    async def offline(*args, **kwargs) -> None:
        raise OSError("audio UDP unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(IcomTransport, "connect", offline)
        with pytest.raises(RuntimeError, match="failed to re-establish"):
            await asyncio.wait_for(session.subscribe_rx("failed-open"), timeout=2.0)

    assert session.rx_demand == 1
    assert session._rx_subs == [held]
    assert held._inner.active
    assert session.state is AudioSessionState.RECOVERING
    assert radio._audio_silence_repair_attempts == 3
    assert radio._audio_silence_repair_task is None


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_explicit_audio_retry_joins_active_repair_and_survives_caller_cancel(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    sub = await session.subscribe_rx("held")
    connect = IcomTransport.connect
    started = asyncio.Event()
    resume = asyncio.Event()
    calls: list[int] = []

    async def delayed_connect(transport, host, port, **kwargs) -> None:
        calls.append(1)
        started.set()
        await resume.wait()
        await connect(transport, host, port, **kwargs)

    monkeypatch.setattr(IcomTransport, "connect", delayed_connect)
    decision_clock = session._monotonic
    assert isinstance(decision_clock, ManualClock)
    decision_clock.advance(RX_LIVENESS_TIMEOUT_S)
    session._check_liveness_locked()
    repair = radio._audio_silence_repair_task
    assert repair is not None
    await asyncio.wait_for(started.wait(), timeout=2.0)

    first_entered = asyncio.Event()
    second_entered = asyncio.Event()

    async def retry(entered: asyncio.Event) -> bool:
        entered.set()
        return await radio.retry_audio()

    first = asyncio.create_task(retry(first_entered))
    second = asyncio.create_task(retry(second_entered))
    try:
        await asyncio.wait_for(first_entered.wait(), timeout=2.0)
        await asyncio.wait_for(second_entered.wait(), timeout=2.0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not repair.done()
        assert radio._audio_silence_repair_task is repair
        resume.set()
        assert await asyncio.wait_for(second, timeout=2.0)
        assert repair.done()
        assert calls == [1]
        assert radio._audio_silence_repair_attempts == 1
        transport = radio._audio_transport
        assert transport is not None
        transport._handle_packet(_make_audio_bytes(b"joined-retry", send_seq=1))
        packet = await sub.get(timeout=2.0)
        assert packet is not None and packet.data == b"joined-retry"
    finally:
        resume.set()
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_explicit_audio_retry_while_still_offline_caps_new_episode(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    held = await session.subscribe_rx("held")
    radio._audio_silence_repair_attempts = 3
    recovery: list[AudioRecoveryState] = []
    radio._on_audio_recovery = recovery.append
    calls: list[int] = []

    async def offline(*args, **kwargs) -> None:
        calls.append(1)
        raise OSError("audio still offline")

    monkeypatch.setattr(IcomTransport, "connect", offline)
    retry = asyncio.create_task(radio.retry_audio())
    async with VirtualClock.on_running_loop() as clock:
        await clock.elapse(60.0)
    assert retry.done() and await retry
    assert calls == [1, 1, 1]
    assert recovery == [AudioRecoveryState.FAILED]
    assert radio._audio_silence_repair_attempts == 3
    assert radio._audio_silence_repair_task is None
    assert session._rx_subs == [held]
    assert held._inner.active


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["no_demand", "owner_stopped", "non_lan"])
async def test_explicit_audio_retry_requires_existing_repair_preconditions(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    unavailable: str,
) -> None:
    radio = demanded_radio
    if unavailable != "no_demand":
        await radio.audio_session.subscribe_rx("held")
    if unavailable == "owner_stopped":
        radio._connected = False
    elif unavailable == "non_lan":
        monkeypatch.setattr(type(radio), "backend_id", "icom_serial")
    transport = radio._audio_transport
    assert not await radio.retry_audio()
    assert radio._audio_silence_repair_task is None
    assert radio._audio_transport is transport


@pytest.mark.asyncio
@pytest.mark.parametrize("ingress", ["websocket", "http"])
@pytest.mark.parametrize("unavailable", ["no_demand", "owner_stopped", "non_lan"])
async def test_web_audio_retry_refuses_when_no_lan_repair_applies(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    ingress: str,
    unavailable: str,
) -> None:
    radio = demanded_radio
    if unavailable != "no_demand":
        await radio.audio_session.subscribe_rx("held")
    if unavailable == "owner_stopped":
        radio._connected = False
    elif unavailable == "non_lan":
        monkeypatch.setattr(type(radio), "backend_id", "icom_serial")
    transport = radio._audio_transport

    assert await _request_audio_retry(radio, ingress, receiver=1) == {
        "accepted": False,
        "reason": "not_applicable",
    }
    assert radio._audio_silence_repair_task is None
    assert radio._audio_transport is transport


@pytest.mark.asyncio
@pytest.mark.parametrize("ingress", ["websocket", "http"])
async def test_web_audio_retry_refuses_backend_without_optional_capability(
    ingress: str,
) -> None:
    assert await _request_audio_retry(SimpleNamespace(capabilities=set()), ingress) == {
        "accepted": False,
        "reason": "unsupported",
    }


def test_web_audio_retry_refusal_reasons_are_closed() -> None:
    assert set(get_args(AudioRetryRefusalReason)) == {"unsupported", "not_applicable"}


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_explicit_audio_retry_does_not_rearm_after_owner_epoch_moves(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    await session.subscribe_rx("held")
    connect = IcomTransport.connect
    calls: list[IcomTransport] = []

    async def superseded_connect(transport, host, port, **kwargs) -> None:
        calls.append(transport)
        await connect(transport, host, port, **kwargs)
        radio._owner_stop_epoch += 1

    monkeypatch.setattr(IcomTransport, "connect", superseded_connect)
    async with VirtualClock.on_running_loop() as clock:
        retry = asyncio.create_task(radio.retry_audio())
        assert await await_until(
            lambda: radio._audio_silence_repair_attempts == 1, timeout=2.0
        )
        await clock.elapse(3.0)
        assert retry.done() and await retry
    assert len(calls) == 1
    assert calls[0]._udp_transport is None
    assert radio._audio_transport is None
    assert radio._audio_stream is None
    assert radio._audio_silence_repair_task is None


@pytest.mark.asyncio
@pytest.mark.timeout(15)
@pytest.mark.parametrize("disconnect", [False, True])
@pytest.mark.parametrize("join_active", [False, True])
async def test_control_run_receives_while_audio_retry_connect_is_pending(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    disconnect: bool,
    join_active: bool,
) -> None:
    radio = demanded_radio
    sub = await radio.audio_session.subscribe_rx("held")
    connect = IcomTransport.connect
    connecting = asyncio.Event()
    release_connect = asyncio.Event()

    async def gated_connect(transport, host, port, **kwargs) -> None:
        connecting.set()
        await release_connect.wait()
        await connect(transport, host, port, **kwargs)

    incoming: asyncio.Queue[dict | None] = asyncio.Queue()
    connection = FakeConnection()

    async def recv() -> tuple[int, bytes]:
        message = await incoming.get()
        if message is None:
            raise EOFError("peer disconnected")
        return WS_OP_TEXT, encode_json(message).encode()

    server = WebServer(radio, config=WebConfig(read_only=True))
    controller = server._controller
    assert radio._controller_authority is controller
    ticket = ControllerTicket(controller, controller.generation, False, "test", True)
    controller._primary = ticket
    lane = FakeLane()
    authority = ManagedTxAuthority(
        lane, FakeConfigStore(), FakeFence(), provider_generation=7
    )
    owner = "retry-socket"
    keyed = await authority.submit_ptt(True, owner)
    assert keyed.outcome is ManagedTxOutcome.ACCEPTED
    await keyed.wait_settlement()
    handler = ControlHandler(
        connection,
        radio,
        "test",
        "test",
        session_id=owner,
        managed_tx_authority=authority,
    )
    monkeypatch.setattr(handler, "_radio_ready", lambda: True)
    monkeypatch.setattr(connection, "recv", recv)
    monkeypatch.setattr(IcomTransport, "connect", gated_connect)
    existing_repair = None
    if join_active:
        clock = radio.audio_session._monotonic
        assert isinstance(clock, ManualClock)
        clock.advance(RX_LIVENESS_TIMEOUT_S)
        radio.audio_session._check_liveness_locked()
        existing_repair = radio._audio_silence_repair_task
        assert existing_repair is not None
    with controller.bind(ticket):
        receiver = asyncio.create_task(handler.run())
    try:
        incoming.put_nowait(
            {"type": "cmd", "id": "retry", "name": "audio_retry", "params": {}}
        )
        assert await await_until(connecting.is_set, timeout=2.0)
        repair = radio._audio_silence_repair_task
        assert repair is not None and not repair.done()
        if join_active:
            assert repair is existing_repair
        if disconnect:
            incoming.put_nowait(None)
            assert await await_until(receiver.done, timeout=2.0)
            await receiver
        else:
            incoming.put_nowait(
                {"type": "cmd", "id": "off", "name": "ptt_off", "params": {}}
            )
            incoming.put_nowait({"type": "controller_heartbeat"})
            incoming.put_nowait({"type": "ping"})
            assert await await_until(
                lambda: any(
                    decode_json(text).get("type") == "pong"
                    for text in connection.sent_text
                ),
                timeout=2.0,
            )
            messages = [decode_json(text) for text in connection.sent_text]
            assert any(m.get("type") == "controller_heartbeat" for m in messages)
            off = next(m for m in messages if m.get("id") == "off")
            assert off["ok"] is True
        assert any(
            e.operation is ActuationOperation.FORCE_RECEIVE for e in lane.effects
        )
        responses = [decode_json(text) for text in connection.sent_text]
        retry = next(m for m in responses if m.get("id") == "retry")
        assert retry["ok"] is True and retry["result"] == {"accepted": True}
        assert not release_connect.is_set()
        assert not repair.done()
        assert radio._audio_silence_repair_task is repair
        release_connect.set()
        assert await await_until(repair.done, timeout=2.0)
        await repair
        transport = radio._audio_transport
        assert transport is not None
        transport._handle_packet(_make_audio_bytes(b"after-peer-wait", send_seq=1))
        packet = await sub.get(timeout=2.0)
        assert packet is not None and packet.data == b"after-peer-wait"
    finally:
        release_connect.set()
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)
        await authority.force_off()
        await authority.close()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
@pytest.mark.parametrize("spent_budget", [False, True])
@pytest.mark.parametrize("frames_resumed", [False, True])
async def test_direct_tx_release_resumes_silence_repair_with_existing_budget(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
    spent_budget: bool,
    frames_resumed: bool,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    clock = virtual_clock
    monkeypatch.setattr(session, "_monotonic", clock)
    monkeypatch.setattr(session.bus, "_monotonic", clock)
    sub = await session.subscribe_rx("held-through-tx")
    transport = radio._audio_transport
    stream = radio._audio_stream
    assert transport is not None and stream is not None
    reader = stream._rx_task
    assert reader is not None and not reader.done()
    transport._handle_packet(_make_audio_bytes(b"before-tx-silence", send_seq=1))
    assert await sub.get(timeout=2.0) is not None
    await radio.start_tx()
    if spent_budget:
        radio._audio_silence_repair_attempts = radio._LAN_AUDIO_SILENCE_REPAIR_ATTEMPTS
        radio._audio_silence_repair_episode_epoch = radio._civ_epoch
        radio._audio_silence_repair_baseline_heartbeat = (
            session.bus.last_rx_frame_monotonic
        )
    try:
        await clock.elapse(RX_LIVENESS_TIMEOUT_S)
        await radio.push_tx(b"active-direct-transmission")
        session._check_liveness_locked()
        assert session.state is AudioSessionState.RECOVERING
        deferred = radio._audio_silence_repair_task
        assert (deferred is None) is spent_budget
        assert not await radio.retry_audio()
        assert radio._audio_transport is transport
        assert stream._rx_task is reader and not reader.done()
        if frames_resumed:
            transport._handle_packet(
                _make_audio_bytes(b"resumed-during-tx", send_seq=2)
            )
            packet = await sub.get(timeout=2.0)
            assert packet is not None and packet.data == b"resumed-during-tx"
            session._check_liveness_locked()

        await radio.stop_tx()
        await clock.elapse(0.1)
        if deferred is not None:
            assert await await_until(deferred.done, timeout=2.0)
            await deferred

        repair = radio._audio_silence_repair_task
        if spent_budget or frames_resumed:
            assert repair is None
            assert radio._audio_silence_repair_attempts == (0 if frames_resumed else 3)
            assert radio._audio_transport is transport
            return
        assert repair is None
        assert radio._audio_silence_repair_attempts == 1
        assert session.rx_demand == 1
        assert reader.done()
        rebuilt = radio._audio_transport
        assert rebuilt is not None and rebuilt is not transport
        rebuilt._handle_packet(_make_audio_bytes(b"after-tx-silence", send_seq=1))
        packet = await sub.get(timeout=2.0)
        assert packet is not None and packet.data == b"after-tx-silence"
        assert session.state is AudioSessionState.RX_ONLY
        session._check_liveness_locked()
        assert radio._audio_silence_repair_attempts == 0
    finally:
        await radio.stop_tx()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
@pytest.mark.parametrize("ingress", ["session", "direct"])
async def test_tx_rebuild_after_spent_repair_rearms_held_rx(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    ingress: str,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    sub = await session.subscribe_rx("held")
    recovery: list[AudioRecoveryState] = []
    radio._on_audio_recovery = recovery.append

    async def offline(*args, **kwargs) -> None:
        raise OSError("audio UDP unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(IcomTransport, "connect", offline)
        clock = session._monotonic
        assert isinstance(clock, ManualClock)
        clock.advance(RX_LIVENESS_TIMEOUT_S)
        session._check_liveness_locked()
        repair = radio._audio_silence_repair_task
        assert repair is not None
        async with VirtualClock.on_running_loop() as virtual:
            await virtual.elapse(4.0)
        assert repair.done()
        await repair
    assert recovery == [AudioRecoveryState.FAILED]
    assert radio._audio_silence_repair_attempts == 3
    assert radio._audio_stream is None
    assert session.bus.rx_active
    lease = None
    try:
        if ingress == "session":
            lease = await asyncio.wait_for(session.acquire_tx("tx"), timeout=2.0)
            assert session.state is AudioSessionState.RX_TX
        else:
            await asyncio.wait_for(radio.start_tx(), timeout=2.0)
        stream = radio._audio_stream
        transport = radio._audio_transport
        assert stream is not None and stream.state is AudioState.TRANSMITTING
        assert stream._rx_task is not None and not stream._rx_task.done()
        assert stream._rx_callback == session.bus._on_opus_packet
        assert transport is not None
        transport._handle_packet(_make_audio_bytes(b"after-tx-rebuild", send_seq=1))
        packet = await sub.get(timeout=2.0)
        assert packet is not None and packet.data == b"after-tx-rebuild"
    finally:
        if lease is not None:
            await lease.release()
        else:
            await radio.stop_tx()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
@pytest.mark.parametrize(
    "ingress", ["session", "direct", "direct_pcm", "direct_opus", "full_duplex"]
)
async def test_audio_retry_teardown_serializes_session_tx_admission(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    ingress: str,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    stopping = asyncio.Event()
    resume = asyncio.Event()
    acquiring = asyncio.Event()
    old_stream: AudioStream | None = None
    rx_loop = AudioStream._rx_loop

    async def gated_reader(stream: AudioStream) -> None:
        try:
            await rx_loop(stream)
        finally:
            if stream is old_stream:
                stopping.set()
                await resume.wait()

    monkeypatch.setattr(AudioStream, "_rx_loop", gated_reader)
    sub = await session.subscribe_rx("held-through-retry-tx")
    old_stream = radio._audio_stream
    old_transport = radio._audio_transport
    assert old_stream is not None and old_transport is not None
    reader = old_stream._rx_task
    assert reader is not None
    old_transport._handle_packet(_make_audio_bytes(b"before-retry-tx", send_seq=1))
    packet = await sub.get(timeout=2.0)
    assert packet is not None and packet.data == b"before-retry-tx"

    async def acquire() -> TxLease | None:
        acquiring.set()
        if ingress == "session":
            return await session.acquire_tx("during-retry-teardown")
        if ingress == "direct":
            await radio.start_tx()
        elif ingress == "direct_pcm":
            await radio.start_audio_tx_pcm()
        elif ingress == "direct_opus":
            await radio.start_audio_tx_opus()
        else:
            assert ingress == "full_duplex"
            await radio.start_audio_opus(session.bus._on_opus_packet, jitter_depth=0)
        await radio.push_tx(b"direct-tx-after-admission")
        return None

    assert await radio.retry_audio(wait=False)
    repair = radio._audio_silence_repair_task
    assert repair is not None
    tx_task: asyncio.Task[TxLease | None] | None = None
    try:
        await asyncio.wait_for(stopping.wait(), timeout=2.0)
        assert old_stream.state is AudioState.IDLE
        assert radio._audio_stream is old_stream
        assert old_stream._rx_task is reader and not reader.done()

        tx_task = asyncio.create_task(acquire())
        await asyncio.wait_for(acquiring.wait(), timeout=2.0)
        assert not tx_task.done()
        assert session.tx_demand == 0
        assert old_stream.state is AudioState.IDLE
        assert old_stream.get_audio_stats()["tx_packets_sent"] == 0

        resume.set()
        if ingress == "full_duplex":
            with pytest.raises(AudioAlreadyStartedError):
                await asyncio.wait_for(tx_task, timeout=2.0)
            lease = None
        else:
            lease = await asyncio.wait_for(tx_task, timeout=2.0)
        await asyncio.wait_for(asyncio.shield(repair), timeout=2.0)
        try:
            stream = radio._audio_stream
            transport = radio._audio_transport
            assert reader.done()
            assert stream is not None and stream is not old_stream
            assert transport is not None and transport is not old_transport
            assert session.tx_demand == (1 if ingress == "session" else 0)
            if ingress == "session":
                assert session.state is AudioSessionState.RX_TX
            assert stream.state is (
                AudioState.RECEIVING
                if ingress == "full_duplex"
                else AudioState.TRANSMITTING
            )
            assert stream._rx_task is not None and not stream._rx_task.done()
            assert stream._rx_callback == session.bus._on_opus_packet
            transport._handle_packet(_make_audio_bytes(b"after-retry-tx", send_seq=1))
            packet = await sub.get(timeout=2.0)
            assert packet is not None and packet.data == b"after-retry-tx"
            if ingress == "full_duplex":
                assert stream.get_audio_stats()["tx_packets_sent"] == 0
                return
            sent: list[bytes] = []
            monkeypatch.setattr(transport, "_raw_send", sent.append)
            if lease is not None:
                await lease.push(b"tx-after-retry")
            else:
                await radio.push_tx(b"tx-after-retry")
            assert stream.get_audio_stats()["tx_packets_sent"] == (
                1 if ingress == "session" else 2
            )
            assert len(sent) == 1 and sent[0].endswith(b"tx-after-retry")
        finally:
            if lease is not None:
                await lease.release()
            else:
                await radio.stop_tx()
    finally:
        resume.set()
        if tx_task is not None:
            tx_task.cancel()
            results = await asyncio.gather(tx_task, return_exceptions=True)
            if isinstance(results[0], TxLease):
                await results[0].release()
        await radio.stop_tx()
        repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_audio_retry_waiting_for_direct_tx_admission_preserves_started_tx(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    sub = await session.subscribe_rx("held-through-direct-admission")
    stream = radio._audio_stream
    transport = radio._audio_transport
    assert stream is not None and transport is not None
    reader = stream._rx_task
    assert reader is not None
    starting = asyncio.Event()
    resume = asyncio.Event()
    start_tx = AudioStream.start_tx

    async def gated_start(candidate: AudioStream) -> None:
        assert candidate is stream
        starting.set()
        await resume.wait()
        await start_tx(candidate)
        await candidate.push_tx(b"tx-before-repair-admission")

    monkeypatch.setattr(AudioStream, "start_tx", gated_start)
    tx_task = asyncio.create_task(radio.start_tx())
    repair: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(starting.wait(), timeout=2.0)
        assert await radio.retry_audio(wait=False)
        repair = radio._audio_silence_repair_task
        assert repair is not None
        assert await await_until(session._lock.locked, timeout=2.0)
        assert not repair.done()
        assert radio._audio_stream is stream
        assert stream.state is AudioState.RECEIVING
        assert stream._rx_task is reader and not reader.done()

        resume.set()
        await asyncio.wait_for(tx_task, timeout=2.0)
        assert not repair.done()
        assert radio._audio_silence_repair_attempts == 0
        assert radio._audio_stream is stream
        assert radio._audio_transport is transport
        assert stream.state is AudioState.TRANSMITTING
        assert stream._rx_task is reader and not reader.done()
        transport._handle_packet(
            _make_audio_bytes(b"after-direct-admission", send_seq=1)
        )
        packet = await sub.get(timeout=2.0)
        assert packet is not None and packet.data == b"after-direct-admission"
        sent: list[bytes] = []
        monkeypatch.setattr(transport, "_raw_send", sent.append)
        await radio.push_tx(b"direct-tx-preserved")
        assert stream.get_audio_stats()["tx_packets_sent"] == 2
        assert len(sent) == 1 and sent[0].endswith(b"direct-tx-preserved")
    finally:
        resume.set()
        tx_task.cancel()
        await asyncio.gather(tx_task, return_exceptions=True)
        if repair is not None:
            repair.cancel()
            await asyncio.gather(repair, return_exceptions=True)
        await radio.stop_tx()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
@pytest.mark.parametrize("ingress", ["python", "websocket", "http"])
@pytest.mark.parametrize("tx_mode", ["session", "direct"])
async def test_audio_retry_refuses_transmitting_without_replacing_reader(
    demanded_radio: IcomRadio,
    ingress: str,
    tx_mode: str,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    sub = await session.subscribe_rx("held")
    lease = None
    if tx_mode == "session":
        lease = await session.acquire_tx("tx")
    else:
        await radio.start_tx()
    await radio.push_tx(b"active-transmission")
    stream = radio._audio_stream
    transport = radio._audio_transport
    assert stream is not None and transport is not None
    reader = stream._rx_task
    assert reader is not None and not reader.done()
    assert stream.state is AudioState.TRANSMITTING
    try:
        if ingress == "python":
            assert not await radio.retry_audio()
        else:
            assert await _request_audio_retry(radio, ingress) == {
                "accepted": False,
                "reason": "not_applicable",
            }
        assert radio._audio_silence_repair_task is None
        assert radio._audio_stream is stream
        assert radio._audio_transport is transport
        assert stream._rx_task is reader and not reader.done()
        assert stream.state is AudioState.TRANSMITTING
        transport._handle_packet(_make_audio_bytes(b"reader-preserved", send_seq=1))
        packet = await sub.get(timeout=2.0)
        assert packet is not None and packet.data == b"reader-preserved"
    finally:
        try:
            if lease is not None:
                await lease.release()
            else:
                await radio.stop_tx()
        finally:
            if radio._audio_stream is not stream:
                await stream.stop_tx()
                await stream.stop_rx()
    await sub.release()
    assert reader.done()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
@pytest.mark.parametrize("tx_source", ["ptt", "managed", "frames"])
async def test_idle_bridge_repair_waits_for_transmission_to_end(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    tx_source: str,
) -> None:
    from rigplane.runtime.managed_tx_state import ManagedTxIntent

    radio = demanded_radio
    session = radio.audio_session
    async with VirtualClock.on_running_loop() as clock:
        monkeypatch.setattr(session, "_monotonic", clock)
        monkeypatch.setattr(session.bus, "_monotonic", clock)
        sub = await session.subscribe_rx("idle-bridge-rx")
        await session.acquire_tx("idle-bridge-tx")
        stream = radio._audio_stream
        transport = radio._audio_transport
        assert stream is not None and transport is not None
        reader = stream._rx_task
        assert reader is not None
        transport._handle_packet(_make_audio_bytes(b"before-key", send_seq=1))
        assert await sub.get(timeout=2.0) is not None
        await clock.elapse(RX_LIVENESS_TIMEOUT_S)

        def observe_ptt(value: ObservedPtt) -> None:
            monkeypatch.setattr(
                radio.state_store, "_freshness_clock", SimpleNamespace(now=clock)
            )
            radio.state_store.apply_current(
                Observation(
                    path=OBSERVED_PTT_PATH,
                    value=value,
                    source=SourceMetadata(source="civ_unsolicited", provider="test"),
                    timestamp_monotonic=clock(),
                    quality=("confirmed",),
                    max_age=60.0,
                )
            )

        state = SimpleNamespace(intent=ManagedTxIntent.ptt("test-owner"))
        if tx_source == "ptt":
            observe_ptt(ObservedPtt.ON)
        elif tx_source == "managed":
            authority = SimpleNamespace(
                snapshot_nowait=lambda: SimpleNamespace(state=state)
            )
            monkeypatch.setattr(
                radio, "_managed_tx_composition", SimpleNamespace(authority=authority)
            )
        else:
            await radio.push_tx(b"flowing-before-silence")
        try:
            session._check_liveness_locked()
            await clock.elapse(0.5)
            assert radio._audio_silence_repair_attempts == 0
            assert radio._audio_transport is transport
            assert stream._rx_task is reader and not reader.done()
            assert not await radio.retry_audio()

            if tx_source == "ptt":
                observe_ptt(ObservedPtt.OFF)
            elif tx_source == "managed":
                state.intent = ManagedTxIntent.rx()
            else:
                await clock.elapse(RX_LIVENESS_TIMEOUT_S)
            await clock.elapse(0.1)
            assert await await_until(
                lambda: (
                    radio._audio_silence_repair_attempts == 1
                    and radio._audio_silence_repair_task is None
                ),
                timeout=2.0,
            )
            rebuilt = radio._audio_transport
            assert rebuilt is not None and rebuilt is not transport
            assert reader.done()
            assert session.tx_demand == 1
            rebuilt._handle_packet(_make_audio_bytes(b"after-unkey", send_seq=1))
            packet = await sub.get(timeout=2.0)
            assert packet is not None and packet.data == b"after-unkey"
            session._check_liveness_locked()
            assert radio._audio_silence_repair_attempts == 0
            assert session.state is AudioSessionState.RX_TX
            assert session.last_event is not None
            assert session.last_event.reason == "rx_resumed"
        finally:
            # Restore the fake composition before fixture-owned disconnect.
            monkeypatch.setattr(radio, "_managed_tx_composition", None)


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_audio_retry_waits_for_inflight_tx_frame_before_replacing_stream(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    await session.subscribe_rx("held-through-frame")
    await session.acquire_tx("idle-bridge")
    transport = radio._audio_transport
    stream = radio._audio_stream
    assert transport is not None and stream is not None
    sending = asyncio.Event()
    finish = asyncio.Event()
    send_tracked = transport.send_tracked

    async def gated_send(packet: bytes) -> None:
        sending.set()
        await finish.wait()
        await send_tracked(packet)

    monkeypatch.setattr(transport, "send_tracked", gated_send)
    # Freeze admission while retry is accepted, then let the frame win it.
    async with radio._audio_tx_admission_lock:
        push = asyncio.create_task(radio.push_tx(b"inflight-frame"))
        assert await radio.retry_audio(wait=False)
    repair = radio._audio_silence_repair_task
    assert repair is not None
    try:
        await asyncio.wait_for(sending.wait(), timeout=2.0)
        assert radio._audio_transport is transport
        assert radio._audio_stream is stream
        assert radio._audio_silence_repair_attempts == 0
        assert not repair.done()
        finish.set()
        await asyncio.wait_for(push, timeout=2.0)
        assert stream.get_audio_stats()["tx_packets_sent"] == 1
        assert radio._audio_transport is transport
    finally:
        finish.set()
        push.cancel()
        repair.cancel()
        await asyncio.gather(push, repair, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_real_frames_during_retry_close_episode_when_task_settles(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    armed = asyncio.Event()
    finish = asyncio.Event()
    reestablish = session._reestablish_locked

    async def gated_reestablish() -> None:
        await reestablish()
        armed.set()
        await finish.wait()

    async with VirtualClock.on_running_loop() as clock:
        monkeypatch.setattr(session, "_monotonic", clock)
        monkeypatch.setattr(session.bus, "_monotonic", clock)
        sub = await session.subscribe_rx("real-frames-before-task-settles")
        await session.acquire_tx("idle-two-way-bridge")
        monkeypatch.setattr(session, "_reestablish_locked", gated_reestablish)
        assert await radio.retry_audio(wait=False)
        repair = radio._audio_silence_repair_task
        assert repair is not None
        try:
            await asyncio.wait_for(armed.wait(), timeout=2.0)
            await clock.elapse(0.01)
            transport = radio._audio_transport
            assert transport is not None
            transport._handle_packet(_make_audio_bytes(b"during-rearm", send_seq=1))
            packet = await sub.get(timeout=2.0)
            assert packet is not None and packet.data == b"during-rearm"
            assert not repair.done()
            finish.set()
            await asyncio.wait_for(asyncio.shield(repair), timeout=2.0)
            assert radio._audio_silence_repair_task is None
            assert radio._audio_silence_repair_attempts == 0
            assert session.state is AudioSessionState.RX_TX
        finally:
            finish.set()
            repair.cancel()
            await asyncio.gather(repair, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.timeout(15)
@pytest.mark.parametrize("ingress", ["raw", "websocket", "managed_transmit", "legacy"])
@pytest.mark.parametrize("release_in_flight", [False, True])
async def test_ptt_does_not_wait_for_blocked_audio_ensure(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
    ingress: str,
    release_in_flight: bool,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    clock = virtual_clock
    monkeypatch.setattr(session, "_monotonic", clock)
    monkeypatch.setattr(session.bus, "_monotonic", clock)
    await session.subscribe_rx("ptt-during-repair")
    connecting = asyncio.Event()
    never_connected = asyncio.Event()
    key_started = asyncio.Event()
    finish_key = asyncio.Event()
    wire: list[tuple[bool, float]] = []

    async def blocked_ensure(*, reestablish: bool = True) -> None:
        connecting.set()
        await never_connected.wait()

    async def send(civ: bytes, **kwargs) -> CivFrame:
        on = civ == radio._commands.ptt_on(to_addr=radio._radio_addr)
        if on:
            key_started.set()
            await finish_key.wait()
        current = kwargs.get("is_current")
        if current is not None and not current():
            raise StaleBeforeWireError("superseded test key")
        if on or civ == radio._commands.ptt_off(to_addr=radio._radio_addr):
            wire.append((on, clock()))
        return CivFrame(to_addr=0xE0, from_addr=radio._radio_addr, command=0xFB)

    async def legacy_send(civ: bytes, provider_generation: int, **kwargs) -> None:
        assert provider_generation == 7
        await send(civ, **kwargs)

    monkeypatch.setattr(radio, "_ensure_audio_transport", blocked_ensure)
    monkeypatch.setattr(radio, "_send_civ_raw", send)
    monkeypatch.setattr(radio._civ_runtime, "write_managed_ptt", legacy_send)
    authority = ManagedTxAuthority(
        ManagedTxEffectLane(radio, clock=clock),
        FakeConfigStore(),
        FakeFence(),
        provider_generation=7,
        clock=clock,
    )
    monkeypatch.setattr(
        radio, "_managed_tx_composition", SimpleNamespace(authority=authority)
    )

    async def enqueue(*, ready, submission, **kwargs):
        ready.set_result(None)
        return await submission

    handler = ControlHandler(
        FakeConnection(),
        radio,
        "test",
        "test",
        server=SimpleNamespace(
            command_queue=SimpleNamespace(capture_connection_generation=lambda: 1),
            enqueue_managed_positive_tx=enqueue,
        ),
        session_id="repair-owner",
        managed_tx_authority=authority,
        clock=clock,
    )

    async def key() -> None:
        if ingress == "raw":
            await radio.set_ptt(True)
        elif ingress == "legacy":
            await radio._write_managed_ptt(7, True)
        elif ingress == "websocket":
            await handler._enqueue_managed_ptt("ptt_on", {}, source="websocket")
        else:
            submission = await authority.submit_transmit_on()
            assert submission.outcome is ManagedTxOutcome.ACCEPTED
            await submission.wait_settlement()

    key_task: asyncio.Task[None] | None = None
    assert await radio.retry_audio(wait=False)
    repair = radio._audio_silence_repair_task
    assert repair is not None
    try:
        await clock.elapse(0)
        assert connecting.is_set() and not repair.done()
        if not release_in_flight:
            finish_key.set()
        started = clock()
        key_task = asyncio.create_task(key())
        await clock.elapse(0.199)
        assert key_started.is_set(), "key-on waited behind audio repair"
        if release_in_flight:
            assert wire == []
            if ingress == "raw":
                await radio.set_ptt(False)
            elif ingress == "legacy":
                await radio._write_managed_ptt(7, False)
            elif ingress == "websocket":
                await handler._enqueue_managed_ptt("ptt_off", {}, source="websocket")
            else:
                await authority.force_off()
            assert [on for on, _ in wire] == [False]
            finish_key.set()
            await clock.elapse(0)
            if ingress in ("raw", "legacy"):
                with pytest.raises(StaleBeforeWireError):
                    await key_task
            else:
                await key_task
            assert [on for on, _ in wire] == [False]
        else:
            assert [on for on, _ in wire] == [True]
            assert wire[0][1] - started < 0.2
            await key_task
        assert not repair.done() and not never_connected.is_set()
    finally:
        finish_key.set()
        if key_task is not None:
            key_task.cancel()
            await asyncio.gather(key_task, return_exceptions=True)
        repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)
        await authority.force_off()
        await authority.close()
        monkeypatch.setattr(radio, "_managed_tx_composition", None)


@pytest.mark.asyncio
@pytest.mark.timeout(15)
@pytest.mark.parametrize("blocked_phase", ["teardown", "ensure"])
async def test_audio_repair_backs_off_at_next_check_after_key_on(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
    blocked_phase: str,
) -> None:
    radio = demanded_radio
    session = radio.audio_session
    clock = virtual_clock
    monkeypatch.setattr(session, "_monotonic", clock)
    monkeypatch.setattr(session.bus, "_monotonic", clock)
    await session.subscribe_rx("key-interrupts-repair")
    entered = asyncio.Event()
    finish = asyncio.Event()
    phases: list[str] = []
    teardown = radio._teardown_audio_transport

    async def stop() -> None:
        await teardown()
        phases.append("teardown")
        if blocked_phase == "teardown":
            entered.set()
            await finish.wait()

    async def ensure(*, reestablish: bool = True) -> None:
        phases.append("ensure")
        entered.set()
        await finish.wait()

    async def reestablish() -> None:
        phases.append("reestablish")

    monkeypatch.setattr(radio, "_teardown_audio_transport", stop)
    monkeypatch.setattr(radio, "_ensure_audio_transport", ensure)
    monkeypatch.setattr(session, "_reestablish_locked", reestablish)
    assert await radio.retry_audio(wait=False)
    repair = radio._audio_silence_repair_task
    assert repair is not None
    try:
        await clock.elapse(0)
        assert entered.is_set()
        monkeypatch.setattr(
            radio.state_store, "_freshness_clock", SimpleNamespace(now=clock)
        )
        radio.state_store.apply_current(
            Observation(
                path=OBSERVED_PTT_PATH,
                value=ObservedPtt.ON,
                source=SourceMetadata(source="civ_unsolicited", provider="test"),
                timestamp_monotonic=clock(),
                quality=("confirmed",),
                max_age=60.0,
            )
        )
        finish.set()
        await clock.elapse(0.1)
        assert phases == (
            ["teardown"] if blocked_phase == "teardown" else ["teardown", "ensure"]
        )
        assert not repair.done()
        assert radio._audio_silence_repair_attempts == 0
    finally:
        finish.set()
        repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)
