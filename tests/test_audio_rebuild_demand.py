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
from rigplane.core.transport import IcomTransport
from rigplane.runtime._audio_recovery import AudioRecoveryState
from rigplane.runtime.radio import IcomRadio
from rigplane.testing.timing import ManualClock, VirtualClock


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
