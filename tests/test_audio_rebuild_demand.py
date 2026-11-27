"""LAN transport rebuilds preserve session-owned RX demand and bus wiring."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from test_audio import _make_audio_bytes
from test_audio_reconnect_rearm import _connect_fd_only, _reserve_audio_socket

from rigplane.audio import AudioState
from rigplane.audio.session import (
    AudioSession,
    AudioSessionState,
    RX_LIVENESS_TIMEOUT_S,
)
from rigplane.core.transport import DISCOVERY_RETRIES, DISCOVERY_TIMEOUT, IcomTransport
from rigplane.core.radio_protocol import AudioRetryCapable
from rigplane.runtime._audio_recovery import AudioRecoveryState
from rigplane.runtime.radio import IcomRadio
from rigplane.testing.timing import ManualClock, VirtualClock, await_until


@pytest_asyncio.fixture
async def demanded_radio(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IcomRadio]:
    async def connect(transport: IcomTransport, host: str, port: int, **kwargs) -> None:
        sock, _ = _reserve_audio_socket()
        await _connect_fd_only(transport, host, port, sock=sock)

    async def open_close(*, open_stream: bool) -> None:
        return None

    radio = IcomRadio("127.0.0.1")
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
        for sub in list(session._rx_subs):
            await sub.release()
        await radio._teardown_audio_transport()


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
async def test_audio_cut_returns_with_one_retry_after_failed(
    demanded_radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    cut_seconds: float,
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
    initial_transport = radio._audio_transport
    assert initial_transport is not None
    initial_transport._handle_packet(_make_audio_bytes(b"before-cut", send_seq=1))
    assert await sub.get(timeout=2.0) is not None
    connect = IcomTransport.connect
    reestablish = session.reestablish
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
        monkeypatch.setattr(session, "reestablish", record_reestablish)
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
                assert await radio.retry_audio()
                assert len(attempts) == 4
                assert radio._audio_silence_repair_attempts == 1
            else:
                assert len(attempts) == 1
                assert recovery == []

            assert reestablished == [1]
            assert recovery.count(AudioRecoveryState.FAILED) == (cut_seconds == 60.0)
            assert session.rx_demand == 1
            assert session.state is AudioSessionState.RX_ONLY
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
