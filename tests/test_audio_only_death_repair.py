"""MOR-3401: repair an audio-only death from the existing ``rx_silent`` signal.

Phase 1 characterized the defect: killing only the client's LAN audio leg
(``radio._audio_transport._udp_transport.close()``) while CI-V keeps answering
is never repaired — the session surfaces ``rx_silent``, but
``audio_error_watchdog_loop`` needs ≥ 50 UDP errors and ``soft_reconnect`` is
a no-op while CI-V flows. Phase 2 (this file) lands the fix and pins it.

The fix: ``CoreRadio`` registers a local ``AudioSession`` listener that
schedules ONE bounded repair task per ``rx_silent`` episode. Each round runs
the positive-control calls — ``_teardown_audio_transport`` →
``_ensure_audio_transport`` → ``AudioSession.reestablish`` — up to three
rounds (1 s then 2 s backoff); after the cap it reports FAILED once through
the existing ``on_audio_recovery`` callback. (``AudioRecoveryRuntime.recover``
is deliberately NOT called per round: it would report FAILED on every failed
round instead of once at the cap.) Serial Icom and Yaesu backends are
excluded (``backend_id`` is not ``"rigplane"``). The tests below use the same
``IcomRadio`` + ``MockIcomRadio`` stack as
``tests/test_web_owner_reconnect_audio.py``
(``scripts/soak/soak_driver.py::_start_simulator_stack``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from rigplane.audio import AudioPacket, build_audio_packet
from rigplane.audio.session import (
    AudioSessionEvent,
    AudioSessionState,
    RX_LIVENESS_TIMEOUT_S,
)
from rigplane.core.exceptions import ConnectionError as CoreConnectionError
from rigplane.runtime._audio_recovery import AudioRecoveryState
from rigplane.runtime._connection_state import RadioConnectionState
from scripts.soak import soak_driver
from rigplane.testing.timing import ManualClock, VirtualClock, await_until
from rigplane.web.server import WebConfig, WebServer

#: Bound for "frames reach the subscription again" (the property a fix must give).
RESUME_BOUND_S = 10.0
_SANITY_FIRST_FRAME_S = 10.0
_CIV_CHECK_TIMEOUT_S = 5.0


async def _next_frame_or_none(sub: object, timeout_s: float) -> AudioPacket | None:
    """Next RX packet, or None on timeout (``asyncio.TimeoutError`` → None)."""
    try:
        return await sub.get(timeout=timeout_s)  # type: ignore[union-attr]
    except asyncio.TimeoutError:
        return None


async def _resume_within_one_deadline(
    die_and_repair: Callable[[], Awaitable[None]],
    subscribe: Callable[[], Awaitable[Any]],
    bound_s: float = RESUME_BOUND_S,
) -> AudioPacket | None:
    """Repair completion, a fresh subscription and its first fresh frame, all
    inside ONE ``bound_s`` window that starts at the death (MOR-4214).

    Two stacked ``bound_s`` waits would let a 9 s repair plus a 2 s frame pass
    as an 11 s recovery. Overrunning the shared window raises
    ``TimeoutError``; returns ``None`` only when the frame wait itself times out.
    """
    async with asyncio.timeout(bound_s):
        await die_and_repair()
        post_sub = await subscribe()
        try:
            return await _next_frame_or_none(post_sub, bound_s)
        finally:
            await post_sub.release()


def _kill_client_audio_only(radio: object) -> object:
    """Kill ONLY the client's audio leg; CI-V and control stay untouched.

    Closes the underlying asyncio datagram endpoint under the client's
    audio ``IcomTransport``. The transport object stays attached (so the
    audio-error watchdog still sees a transport, just one with no error
    storm), the simulator keeps streaming into the void, and CI-V keeps
    flowing — a client-side audio death.
    """
    audio_t = radio._audio_transport  # noqa: SLF001
    assert audio_t is not None, "no client audio transport to kill"
    udp = audio_t._udp_transport  # noqa: SLF001
    assert udp is not None, "client audio transport has no UDP endpoint"
    udp.close()
    return audio_t


async def _drive_rx_silent(radio: Any) -> float:
    session = radio.audio_session
    heartbeat = radio.audio_bus.last_rx_frame_monotonic
    assert heartbeat is not None, "no RX heartbeat to drive silence from"
    started = max(heartbeat, session._rx_armed_at)
    decision = ManualClock(start=started)
    decision.advance(RX_LIVENESS_TIMEOUT_S)
    async with session._lock:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(session, "_monotonic", decision)
            session._check_liveness_locked()
    return started


async def _civ_answers(radio: object) -> int:
    """Wire-level frequency read (bypasses the state cache fallback)."""
    return await asyncio.wait_for(
        radio.get_freq(bypass_cache=True),  # type: ignore[union-attr]
        _CIV_CHECK_TIMEOUT_S,
    )


def _drain(sub: object) -> None:
    while True:
        try:
            sub.get_nowait()  # type: ignore[union-attr]
        except asyncio.QueueEmpty:
            break


def _spy_method(radio: object, name: str) -> tuple[list[int], Callable[[], object]]:
    """Wrap ``radio.<name>`` to count calls; returns ``(calls, restore)``."""
    calls: list[int] = []
    original = getattr(radio, name)

    async def wrapper(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return await original(*args, **kwargs)

    setattr(radio, name, wrapper)

    def restore() -> None:
        setattr(radio, name, original)

    return calls, restore


async def test_audio_only_death_never_repaired() -> None:
    """GREEN: an audio-only death with live CI-V is repaired by the fix."""
    stack = await soak_driver._start_simulator_stack()
    events: list[AudioSessionEvent] = []
    try:
        radio = stack.radio
        sim = stack.sim
        radio.audio_session.add_listener(events.append)

        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        _drain(sub)
        sent_before = sim.audio_rx_frames_sent
        _kill_client_audio_only(radio)
        t_death = await _drive_rx_silent(radio)

        # CI-V stayed healthy throughout: isolate the death to audio-only.
        freq = await _civ_answers(radio)
        assert freq > 0, "CI-V did not answer right after the audio death"

        packet = await _next_frame_or_none(sub, RESUME_BOUND_S)

        freq_after = await _civ_answers(radio)
        assert freq_after > 0, "CI-V stopped answering during the audio death"
        assert sim.audio_rx_frames_sent > sent_before, (
            "simulator stopped sending RX audio — the death was not client-side"
        )

        silent_at = [e.timestamp - t_death for e in events if e.reason == "rx_silent"]
        print(f"[MOR-3401] rx_silent delays after death: {silent_at}")
        print(f"[MOR-3401] session state at bound: {radio.audio_session.state}")

        # The fix rebuilds the dead audio leg, so a frame arrives in the bound.
        assert packet is not None, (
            "MOR-3401: RX frames never resumed on the held subscription within "
            f"{RESUME_BOUND_S}s after a client-side audio death while CI-V "
            "kept answering — the audio-only death was not repaired"
        )
        await sub.release()
    finally:
        radio.audio_session.remove_listener(events.append)
        await stack.aclose()


async def test_manual_audio_rebuild_repairs_audio_only_death() -> None:
    """GREEN positive control: the hand-driven repair restores RX frames."""
    stack = await soak_driver._start_simulator_stack()
    events: list[AudioSessionEvent] = []
    try:
        radio = stack.radio
        radio.audio_session.add_listener(events.append)

        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        _drain(sub)
        old_audio_t = _kill_client_audio_only(radio)

        await _drive_rx_silent(radio)
        assert any(e.reason == "rx_silent" for e in events)

        # The repair a fix would trigger — the same path
        # ``ControlPhaseRuntime._after_reconnect_parts`` runs for audio.
        await radio._teardown_audio_transport()  # noqa: SLF001
        await radio._ensure_audio_transport()  # noqa: SLF001
        await radio.audio_session.reestablish()
        assert radio._audio_transport is not old_audio_t  # noqa: SLF001

        _drain(sub)
        packet = await _next_frame_or_none(sub, RESUME_BOUND_S)
        assert packet is not None, (
            "manual audio rebuild (teardown → ensure → reestablish) did not "
            "restore RX frames — the positive control is broken"
        )
        assert await _civ_answers(radio) > 0, "CI-V stopped answering"
        await sub.release()
    finally:
        radio.audio_session.remove_listener(events.append)
        await stack.aclose()


async def test_rx_silent_repairs_but_soft_reconnect_and_error_watchdog_do_not() -> None:
    """Require completed repair and fresh RX, without another recovery trigger."""
    stack = await soak_driver._start_simulator_stack()
    events: list[AudioSessionEvent] = []
    repair_tasks: list[asyncio.Task[None]] = []
    silent_seen = asyncio.Event()
    radio = stack.radio
    session = radio.audio_session

    def record_event(event: AudioSessionEvent) -> None:
        events.append(event)
        if event.reason == "rx_silent":
            task = radio._audio_silence_repair_task
            if task is not None:
                repair_tasks.append(task)
            silent_seen.set()

    session.add_listener(record_event)
    try:
        soft_calls: list[str] = []
        orig_soft = radio.soft_reconnect

        async def _spy() -> None:
            soft_calls.append("called")
            await orig_soft()

        radio.soft_reconnect = _spy  # type: ignore[method-assign]
        sub = post_sub = None
        try:
            sub = await session.subscribe_rx("web-audio")
            assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None
            _drain(sub)
            async with asyncio.timeout(RESUME_BOUND_S):
                audio_t = _kill_client_audio_only(radio)
                t_death = await _drive_rx_silent(radio)
                assert silent_seen.is_set()
                assert repair_tasks, "rx_silent did not start a repair task"
                await asyncio.shield(repair_tasks[0])
                replacement = radio._audio_transport
                assert replacement is not None and replacement is not audio_t
                assert session.rx_demand > 0
                assert session._bus.rx_active, (
                    "repair did not re-arm RX before subscribing"
                )

                # Keep demand while opening a fresh, empty post-repair queue.
                _drain(sub)
                post_sub = await session.subscribe_rx("post-repair")
                packet = await _next_frame_or_none(post_sub, RESUME_BOUND_S)
                assert packet is not None
                assert radio._audio_transport is replacement

                silent = [e for e in events if e.reason == "rx_silent"]
                assert silent, "rx_silent never fired during the audio-only death"
                delay = silent[0].timestamp - t_death
                assert 2.9 <= delay < 10.0, f"rx_silent fired after {delay:.1f}s"
                assert not soft_calls, "soft_reconnect ran during healthy CI-V"
                assert audio_t._udp_error_count < 50, (  # noqa: SLF001
                    "UDP error storm — audio_error_watchdog_loop territory, "
                    "not a silent death"
                )
                assert await _civ_answers(radio) > 0, "CI-V stopped answering"
        finally:
            radio.soft_reconnect = orig_soft  # type: ignore[method-assign]
            if post_sub is not None:
                await post_sub.release()
            if sub is not None:
                await sub.release()
    finally:
        session.remove_listener(record_event)
        await stack.aclose()


async def test_no_repair_without_rx_demand() -> None:
    """No RX demand → the rx_silent listener schedules no repair task."""
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        session = radio.audio_session  # registers the runtime listener
        assert session.rx_demand == 0

        calls, restore = _spy_method(radio, "_teardown_audio_transport")
        try:
            session._emit("rx_silent")  # noqa: SLF001
            async with VirtualClock.on_running_loop() as clock:
                await clock.elapse(0.5)
            assert calls == []
            assert radio._audio_silence_repair_task is None
        finally:
            restore()
    finally:
        await stack.aclose()


async def test_no_repair_when_civ_not_healthy() -> None:
    """A soft-reconnect-in-progress (``RECONNECTING``) blocks the repair."""
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        calls, restore = _spy_method(radio, "_teardown_audio_transport")
        try:
            radio._conn_state = RadioConnectionState.RECONNECTING
            radio.audio_session._emit("rx_silent")  # noqa: SLF001
            async with VirtualClock.on_running_loop() as clock:
                await clock.elapse(0.5)
            assert calls == []
            assert radio._audio_silence_repair_task is None
        finally:
            radio._conn_state = RadioConnectionState.CONNECTED
            restore()
        await sub.release()
    finally:
        await stack.aclose()


async def test_owner_stop_during_backoff_cancels_pending_repair() -> None:
    """Owner ``disconnect()`` during the backoff cancels the pending repair."""
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        ensure_calls: list[int] = []
        orig_ensure = radio._ensure_audio_transport

        async def _fail_ensure(*, reestablish: bool = True) -> None:
            ensure_calls.append(1)
            raise CoreConnectionError("audio leg never returns")

        radio._ensure_audio_transport = _fail_ensure  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)
            await _drive_rx_silent(radio)
            # Wait for the first attempt to fail and the task to enter backoff.
            assert await await_until(lambda: len(ensure_calls) >= 1, timeout=8.0)
            await radio.disconnect()  # owner stops during the backoff sleep
            # The stop cancels the pending repair synchronously: no task
            # lingers in its backoff sleep after disconnect() returns.
            assert radio._audio_silence_repair_task is None
            async with VirtualClock.on_running_loop() as clock:
                await clock.elapse(3.0)  # past every backoff window
            assert len(ensure_calls) == 1, "a stopped owner still got another attempt"
        finally:
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
        await sub.release()
    finally:
        await stack.aclose()


async def test_silence_repair_caps_at_three_attempts_and_reports_failed_once() -> None:
    """An audio leg that never returns: exactly 3 attempts, one FAILED, then
    nothing for ≥ 5 s."""
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        recovery: list[AudioRecoveryState] = []
        radio._on_audio_recovery = recovery.append

        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        ensure_calls: list[int] = []
        orig_ensure = radio._ensure_audio_transport

        async def _fail_ensure(*, reestablish: bool = True) -> None:
            ensure_calls.append(1)
            raise CoreConnectionError("audio leg never returns")

        radio._ensure_audio_transport = _fail_ensure  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)
            await _drive_rx_silent(radio)

            # 3 attempts with 1 s + 2 s backoff, then FAILED once.
            def _repair_done() -> bool:
                done = radio._audio_silence_repair_task is None
                return done and len(ensure_calls) == 3

            assert await await_until(_repair_done, timeout=15.0), (
                f"expected 3 attempts and a finished task, got {len(ensure_calls)}"
            )
            assert len(ensure_calls) == 3
            assert radio._audio_silence_repair_attempts == 3
            assert recovery == [AudioRecoveryState.FAILED]

            # No further attempts for ≥ 5 s after the cap.
            async with VirtualClock.on_running_loop() as clock:
                await clock.elapse(5.0)
            assert len(ensure_calls) == 3
            assert radio._audio_silence_repair_attempts == 3
            assert recovery == [AudioRecoveryState.FAILED]
        finally:
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
        await sub.release()
    finally:
        await stack.aclose()


@pytest.mark.parametrize("callback", ["absent", "present", "raises"])
async def test_rx_restore_failure_is_retained_at_third_attempt_and_clears_on_frames(
    callback: str, caplog: pytest.LogCaptureFixture
) -> None:
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        session = radio.audio_session
        recovery: list[AudioRecoveryState] = []

        def report(state: AudioRecoveryState) -> None:
            recovery.append(state)
            if callback == "raises":
                raise RuntimeError("embedder callback failed")

        radio._on_audio_recovery = None if callback == "absent" else report
        server = WebServer(radio, WebConfig(radio_model="IC-7610"))
        server._attach_audio_session_listener()
        broadcaster = server._audio_broadcaster
        deliveries: list[object] = []

        def record_delivery() -> None:
            deliveries.append(broadcaster.rx_start_status()["failure"])

        server._on_rx_start_status_change = record_delivery
        sub = await session.subscribe_rx("restore-failure-test")
        try:
            assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None
            assert session.rx_restore_failed is False
            assert broadcaster.rx_start_status()["failure"] is None
            attempts: list[int] = []
            observed_failures: list[tuple[bool, object]] = []
            original_ensure = radio._ensure_audio_transport

            async def fail_ensure(*, reestablish: bool = True) -> None:
                assert reestablish is False
                attempts.append(1)
                # Neither a partial budget nor an in-flight final attempt is FAILED.
                observed_failures.append(
                    (
                        session.rx_restore_failed,
                        broadcaster.rx_start_status()["failure"],
                    )
                )
                raise CoreConnectionError("audio leg never returns")

            radio._ensure_audio_transport = fail_ensure
            try:
                _kill_client_audio_only(radio)
                await _drive_rx_silent(radio)
                assert await await_until(
                    lambda: (
                        len(attempts) == 3 and radio._audio_silence_repair_task is None
                    ),
                    timeout=15.0,
                )
            finally:
                radio._ensure_audio_transport = original_ensure

            assert session.rx_restore_failed is True
            assert observed_failures == [(False, None)] * 3
            assert recovery == (
                [] if callback == "absent" else [AudioRecoveryState.FAILED]
            )
            failure = {
                "stage": "rx_recovery",
                "code": "core_rx_restore_failed",
                "reason": "retries_exhausted",
            }
            assert broadcaster.rx_start_status()["failure"] == failure
            assert server.build_public_state()["audioReceive"]["failure"] == failure
            assert deliveries[-1] == failure
            deliveries.clear()
            warnings = [
                record
                for record in caplog.records
                if "event=rx_audio_restore_failed" in record.getMessage()
            ]
            assert len(warnings) == 1
            assert warnings[0].levelname == "WARNING"
            assert "RX audio could not be restored" in warnings[0].getMessage()
            async with session._lock:
                session._check_liveness_locked()
            assert session.rx_restore_failed is True

            # Re-arming alone is not evidence of recovery.
            await radio._ensure_audio_transport()
            assert session.rx_restore_failed is True
            await session.reestablish()
            _drain(sub)
            assert await _next_frame_or_none(sub, RESUME_BOUND_S) is not None
            assert await await_until(
                lambda: not session.rx_restore_failed, timeout=RESUME_BOUND_S
            )
            assert broadcaster.rx_start_status()["failure"] is None
            assert server.build_public_state()["audioReceive"]["failure"] is None
            assert deliveries[-1] is None
        finally:
            server._detach_audio_session_listener()
            await sub.release()
    finally:
        await stack.aclose()


async def test_rx_resumed_during_backoff_does_not_grant_extra_attempt() -> None:
    """A stray ``rx_resumed`` while the repair task still runs does not reset
    the episode budget: still exactly 3 rounds, one FAILED (MOR-3401 r2)."""
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        recovery: list[AudioRecoveryState] = []
        radio._on_audio_recovery = recovery.append

        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        ensure_calls: list[int] = []
        orig_ensure = radio._ensure_audio_transport

        async def _fail_ensure(*, reestablish: bool = True) -> None:
            ensure_calls.append(1)
            raise CoreConnectionError("audio leg never returns")

        radio._ensure_audio_transport = _fail_ensure  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)
            await _drive_rx_silent(radio)
            assert await await_until(lambda: len(ensure_calls) >= 1, timeout=8.0)
            # A buffered frame landing mid-episode must not fund another round.
            radio.audio_session._emit("rx_resumed")  # noqa: SLF001
            assert await await_until(
                lambda: radio._audio_silence_repair_task is None, timeout=15.0
            ), f"repair never finished, got {len(ensure_calls)} ensure calls"
            assert len(ensure_calls) == 3
            assert radio._audio_silence_repair_attempts == 3
            assert recovery == [AudioRecoveryState.FAILED]
        finally:
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
        await sub.release()
    finally:
        await stack.aclose()


async def test_successful_round_consumes_budget_and_frames_reopen_episode() -> None:
    """A successful round does NOT reset the budget — only real frames (or
    a new connection epoch) close the episode (MOR-3401 r3): one failed
    round plus a successful one leaves the counter at 2, and a later death
    is still repaired because frames flowed in between."""
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        teardown_calls, restore_teardown = _spy_method(
            radio, "_teardown_audio_transport"
        )
        ensure_calls: list[int] = []
        orig_ensure = radio._ensure_audio_transport

        async def _fail_first_ensure(*, reestablish: bool = True) -> None:
            ensure_calls.append(1)
            if len(ensure_calls) == 1:
                raise CoreConnectionError("first round fails")
            await orig_ensure(reestablish=reestablish)

        radio._ensure_audio_transport = _fail_first_ensure  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)
            _drain(sub)
            await _drive_rx_silent(radio)
            assert await _next_frame_or_none(sub, RESUME_BOUND_S) is not None
            assert await await_until(
                lambda: radio._audio_silence_repair_task is None, timeout=15.0
            ), "repair never finished"
            # Both rounds consumed budget (the successful round itself
            # re-enters ensure via reestablish → start_rx, so no exact
            # ensure count is asserted); success did not reset anything.
            assert len(teardown_calls) == 2
            assert radio._audio_silence_repair_attempts == 2

            # Frames flowed after the repair, so a later death opens a new
            # episode with a fresh budget and is repaired again.
            _kill_client_audio_only(radio)
            _drain(sub)
            await _drive_rx_silent(radio)
            assert await _next_frame_or_none(sub, RESUME_BOUND_S) is not None
            assert len(teardown_calls) > 2
        finally:
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            restore_teardown()
        await sub.release()
    finally:
        await stack.aclose()


@pytest.mark.parametrize("frames_before_return", [False, True])
async def test_third_successful_round_waits_for_real_frames(
    frames_before_return: bool,
) -> None:
    """Real frames on the third round prevent FAILED across the return edge."""
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        recovery: list[AudioRecoveryState] = []
        radio._on_audio_recovery = recovery.append
        session = radio.audio_session
        sub = await session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        teardown_calls, restore_teardown = _spy_method(
            radio, "_teardown_audio_transport"
        )
        orig_ensure = radio._ensure_audio_transport
        orig_reestablish = session._reestablish_locked
        bus = radio.audio_bus
        orig_callback = bus._on_opus_packet
        allow_frames = asyncio.Event()
        delivery_order: list[str] = []
        heartbeats: list[float] = []
        ensure_calls: list[int] = []
        release_frames: asyncio.TimerHandle | None = None

        def _gate_real_frames(packet: AudioPacket | None) -> None:
            if allow_frames.is_set() and packet is not None:
                orig_callback(packet)
                heartbeat = bus.last_rx_frame_monotonic
                assert heartbeat is not None
                heartbeats.append(heartbeat)
                delivery_order.append("frame")

        async def _fail_first_two_ensures(*, reestablish: bool = True) -> None:
            ensure_calls.append(1)
            if len(ensure_calls) <= 2:
                raise CoreConnectionError("first two rounds fail")
            await orig_ensure(reestablish=reestablish)

        async def _reestablish_with_frame_gate() -> None:
            nonlocal release_frames
            await orig_reestablish()
            if frames_before_return:
                allow_frames.set()
                assert await await_until(lambda: bool(heartbeats), timeout=2.0)
            else:
                release_frames = asyncio.get_running_loop().call_later(
                    0.3, allow_frames.set
                )
            delivery_order.append("return")

        radio._ensure_audio_transport = _fail_first_two_ensures  # type: ignore[method-assign]
        session._reestablish_locked = _reestablish_with_frame_gate  # type: ignore[method-assign]
        bus._on_opus_packet = _gate_real_frames  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)
            _drain(sub)
            await _drive_rx_silent(radio)
            assert await await_until(lambda: "return" in delivery_order, timeout=12.0)
            assert await _next_frame_or_none(sub, 2.0) is not None
            assert await await_until(
                lambda: radio._audio_silence_repair_task is None, timeout=5.0
            ), "third-round frame observation did not finish"
            assert len(teardown_calls) == 3
            assert radio._audio_silence_repair_attempts == 3
            assert recovery == [], "healthy third round falsely reported FAILED"
            assert (
                delivery_order.index("frame") < delivery_order.index("return")
            ) is frames_before_return
            assert heartbeats[0] > radio._audio_silence_repair_teardown_completed
        finally:
            if release_frames is not None:
                release_frames.cancel()
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            session._reestablish_locked = orig_reestablish  # type: ignore[method-assign]
            bus._on_opus_packet = orig_callback  # type: ignore[method-assign]
            restore_teardown()
        await sub.release()
    finally:
        await stack.aclose()


async def test_genuine_rx_resumed_reopens_episode() -> None:
    """After FAILED, audio healed by another path WITHOUT reestablish lets
    the session watchdog emit a genuine ``rx_resumed`` (not a test emit):
    the episode reopens and the next death is repaired (MOR-3401 r3)."""
    stack = await soak_driver._start_simulator_stack()
    events: list[AudioSessionEvent] = []
    try:
        radio = stack.radio
        recovery: list[AudioRecoveryState] = []
        radio._on_audio_recovery = recovery.append
        radio.audio_session.add_listener(events.append)

        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        teardown_calls, restore_teardown = _spy_method(
            radio, "_teardown_audio_transport"
        )
        orig_ensure = radio._ensure_audio_transport
        ensure_calls: list[int] = []

        async def _fail_ensure(*, reestablish: bool = True) -> None:
            ensure_calls.append(1)
            raise CoreConnectionError("audio leg never returns")

        radio._ensure_audio_transport = _fail_ensure  # type: ignore[method-assign]
        try:
            # Spend the whole budget: 3 failed attempts, FAILED once.
            _kill_client_audio_only(radio)
            assert await await_until(
                lambda: (
                    radio._audio_silence_repair_task is None and len(ensure_calls) == 3
                ),
                timeout=15.0,
            ), f"expected 3 attempts and a finished task, got {len(ensure_calls)}"
            assert recovery == [AudioRecoveryState.FAILED]
            resumed_before = sum(1 for e in events if e.reason == "rx_resumed")
            assert resumed_before == 0, (
                "watchdog resumed from a pre-death heartbeat before audio was healed"
            )

            # Another path rebuilds the transport and re-arms the stream
            # underneath the session but never reestablishes demand, so the
            # session stays RECOVERING until real frames arrive — and the
            # watchdog itself emits rx_resumed.
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            await radio._teardown_audio_transport()
            await radio._ensure_audio_transport(reestablish=False)
            await radio.audio_bus.restart_rx()
            assert await await_until(
                lambda: (
                    sum(1 for e in events if e.reason == "rx_resumed") > resumed_before
                ),
                timeout=8.0,
            ), "the session watchdog never emitted a genuine rx_resumed"
            assert radio._audio_silence_repair_attempts == 0
            repaired_teardowns = len(teardown_calls)

            # A new real death gets a fresh budget and is repaired again.
            _kill_client_audio_only(radio)
            _drain(sub)
            assert await _next_frame_or_none(sub, RESUME_BOUND_S) is not None
            assert len(teardown_calls) > repaired_teardowns
        finally:
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            restore_teardown()
        await sub.release()
    finally:
        radio.audio_session.remove_listener(events.append)
        await stack.aclose()


@pytest.mark.parametrize("later_watchdog_edge", [False, True])
async def test_two_rx_silent_start_one_repair_task(
    monkeypatch: pytest.MonkeyPatch, later_watchdog_edge: bool
) -> None:
    """Duplicate emits share a task; a later watchdog recovery may start another."""
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        session = radio.audio_session
        sub = await session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        # Inject the watchdog loop so wall time cannot add an unrequested edge.
        watchdog = session._watchdog_task
        assert watchdog is not None
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)
        monkeypatch.setattr(session, "_watchdog_loop", asyncio.Event().wait)

        repair_tasks: list[asyncio.Task[None]] = []

        def record_task(event: AudioSessionEvent) -> None:
            if event.reason == "rx_silent":
                task = radio._audio_silence_repair_task
                assert task is not None
                repair_tasks.append(task)

        session.add_listener(record_task)
        bus = session.bus
        on_packet = bus._on_opus_packet
        allow_frames = not later_watchdog_edge

        def gate_frames(packet: AudioPacket | None) -> None:
            if allow_frames:
                on_packet(packet)

        monkeypatch.setattr(bus, "_on_opus_packet", gate_frames)
        teardown_tasks: list[asyncio.Task[None] | None] = []
        teardown = radio._teardown_audio_transport

        async def record_teardown() -> None:
            nonlocal allow_frames
            teardown_tasks.append(asyncio.current_task())
            await teardown()
            if teardown_tasks[-1] is not first_task:
                allow_frames = True

        monkeypatch.setattr(radio, "_teardown_audio_transport", record_teardown)
        try:
            _kill_client_audio_only(radio)
            _drain(sub)
            session._emit("rx_silent")  # noqa: SLF001
            first_task = radio._audio_silence_repair_task
            assert first_task is not None
            session._emit("rx_silent")  # noqa: SLF001
            assert repair_tasks == [first_task, first_task]
            await asyncio.wait_for(asyncio.shield(first_task), RESUME_BOUND_S)
            assert radio._audio_silence_repair_task is None
            assert teardown_tasks == [first_task]

            if later_watchdog_edge:
                # A replacement without frames gets the real liveness decision,
                # with only its clock advanced; no socket callback runs here.
                heartbeat = bus.last_rx_frame_monotonic
                assert heartbeat is not None
                clock = ManualClock(start=max(heartbeat, session._rx_armed_at))
                clock.advance(RX_LIVENESS_TIMEOUT_S)
                async with session._lock:
                    with monkeypatch.context() as clock_patch:
                        clock_patch.setattr(session, "_monotonic", clock)
                        session._check_liveness_locked()
                second_task = radio._audio_silence_repair_task
                assert second_task is not None
                assert second_task is not first_task
                assert first_task.done()
                assert repair_tasks == [first_task, first_task, second_task]
                await asyncio.wait_for(asyncio.shield(second_task), RESUME_BOUND_S)
                assert radio._audio_silence_repair_task is None
                assert teardown_tasks == [first_task, second_task]

            assert await _next_frame_or_none(sub, RESUME_BOUND_S) is not None
            print(
                f"[MOR-4351] tasks={[id(task) for task in repair_tasks]} "
                f"teardowns={[id(task) for task in teardown_tasks]} "
                f"later_watchdog_edge={later_watchdog_edge}"
            )
        finally:
            session.remove_listener(record_task)
            monkeypatch.undo()
        await sub.release()
    finally:
        await stack.aclose()


async def test_serial_backend_does_not_run_lan_repair(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A serial Icom backend with RX demand and an rx_silent does not run the
    LAN repair (``backend_id`` is ``"icom_serial"``, not ``"rigplane"``)."""
    from rigplane.backends._icom_serial_base import _IcomSerialRadioBase
    from rigplane.backends.icom7610 import Icom7610SerialRadio
    from test_icom7610_serial_radio import _FakeSerialCivLink, _FakeUsbAudioDriver

    monkeypatch.setattr(
        _IcomSerialRadioBase,
        "_default_enumerate_serial_ports",
        lambda self: [],
    )

    async def _no_probe(self: object, port: str) -> int | None:
        raise AssertionError(f"unexpected real serial I/O on {port!r}")

    monkeypatch.setattr(_IcomSerialRadioBase, "_default_civ_identity_probe", _no_probe)

    link = _FakeSerialCivLink()
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
        audio_driver=usb_audio,
    )
    await radio.connect()
    try:
        session = radio.audio_session
        sub = await session.subscribe_rx("web-audio")
        assert session.rx_demand == 1

        calls, restore = _spy_method(radio, "_teardown_audio_transport")
        try:
            session._emit("rx_silent")  # noqa: SLF001
            async with VirtualClock.on_running_loop() as clock:
                await clock.elapse(0.5)
            assert calls == []
            assert radio._audio_silence_repair_task is None
        finally:
            restore()
        await sub.release()
    finally:
        await radio.disconnect()


async def test_spent_budget_reopens_after_audio_fixed_by_another_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A budget spent to FAILED does not leak into the next death (MOR-3401
    r3): audio fixed by another path (teardown → ensure → reestablish,
    which emits no ``rx_resumed``) reopens the episode, so a later real
    death is repaired again with a fresh budget."""
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        recovery: list[AudioRecoveryState] = []
        radio._on_audio_recovery = recovery.append

        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        teardown_calls, restore_teardown = _spy_method(
            radio, "_teardown_audio_transport"
        )
        orig_ensure = radio._ensure_audio_transport
        ensure_calls: list[int] = []

        async def _fail_ensure(*, reestablish: bool = True) -> None:
            ensure_calls.append(1)
            raise CoreConnectionError("audio leg never returns")

        radio._ensure_audio_transport = _fail_ensure  # type: ignore[method-assign]
        try:
            # Spend the whole budget: 3 failed attempts, FAILED once.
            _kill_client_audio_only(radio)
            await _drive_rx_silent(radio)
            assert await await_until(
                lambda: (
                    radio._audio_silence_repair_task is None and len(ensure_calls) == 3
                ),
                timeout=15.0,
            ), f"expected 3 attempts and a finished task, got {len(ensure_calls)}"
            assert recovery == [AudioRecoveryState.FAILED]
            assert radio._audio_silence_repair_attempts == 3

            # Another path fixes audio (a soft reconnect tail's reestablish,
            # or demand dropped and re-added): the session goes straight
            # back to RX_ONLY, so no rx_resumed is emitted on this path.
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            await radio._teardown_audio_transport()
            await radio._ensure_audio_transport()
            await radio.audio_session.reestablish()
            _drain(sub)
            assert await _next_frame_or_none(sub, RESUME_BOUND_S) is not None
            repaired_teardowns = len(teardown_calls)

            # A new real death gets a fresh budget and is repaired again.
            # Kill first, then drain: only post-death frames may pass.
            session = radio.audio_session

            async def die_and_repair() -> None:
                _kill_client_audio_only(radio)
                _drain(sub)
                # Drive the 3 s silence DECISION deterministically (MOR-4214):
                # the same ManualClock advance the two_rx_silent test uses for
                # its later-watchdog edge. The liveness budget is a decision
                # threshold, not a real-time wait: the repair execution and
                # the frames below stay real, and the real watchdog firing
                # later sees RECOVERING with a stale heartbeat and schedules
                # nothing new.
                heartbeat = radio.audio_bus.last_rx_frame_monotonic
                assert heartbeat is not None, "no RX heartbeat before the second death"
                decision = ManualClock(start=max(heartbeat, session._rx_armed_at))
                decision.advance(RX_LIVENESS_TIMEOUT_S)
                with monkeypatch.context() as patch:
                    patch.setattr(session, "_monotonic", decision)
                    async with session._lock:
                        session._check_liveness_locked()
                task = radio._audio_silence_repair_task
                assert task is not None, (
                    "MOR-3401 r3: rx_silent after the second death scheduled no "
                    "repair — the budget spent on the first episode leaked"
                )
                await asyncio.shield(task)
                assert len(teardown_calls) > repaired_teardowns, (
                    "no repair round ran for the second death"
                )

            # Fresh frames on a fresh subscription: the old queue can still
            # hold a buffered pre-death frame that would pass vacuously.
            frame = await _resume_within_one_deadline(
                die_and_repair,
                lambda: session.subscribe_rx("post-repair-second"),
            )
            assert frame is not None, (
                "MOR-3401 r3: no fresh frames after the second death was repaired"
            )
        finally:
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            restore_teardown()
        await sub.release()
    finally:
        await stack.aclose()


class _SlowSub:
    """A subscription whose first frame arrives ``delay_s`` after the ask."""

    def __init__(self, delay_s: float) -> None:
        self._delay_s = delay_s

    async def get(self, timeout: float) -> object:
        await asyncio.sleep(self._delay_s)
        return object()

    async def release(self) -> None:
        return None


@pytest.mark.timeout(30)
async def test_resume_deadline_is_shared_across_repair_and_fresh_frame(
    virtual_clock: VirtualClock,
) -> None:
    """Negative control (MOR-4214): the 10 s bound covers repair AND the fresh
    frame together. Each stage takes under 10 s, together 12 s: rejected. The
    same stages at 4 s + 4 s pass, so the control is not just always red."""

    async def run(repair_s: float, frame_s: float) -> object:
        async def die_and_repair() -> None:
            await asyncio.sleep(repair_s)

        async def subscribe() -> _SlowSub:
            return _SlowSub(frame_s)

        task = asyncio.ensure_future(
            _resume_within_one_deadline(die_and_repair, subscribe)
        )
        await virtual_clock.elapse(RESUME_BOUND_S + 2.0)
        assert task.done()
        return await task

    assert await run(4.0, 4.0) is not None
    with pytest.raises(TimeoutError):
        await run(6.0, 6.0)


@pytest.mark.parametrize("healthy_frame_before_death", [False, True])
async def test_successful_rounds_without_frames_cap_and_report_failed(
    monkeypatch: pytest.MonkeyPatch,
    healthy_frame_before_death: bool,
) -> None:
    """Rounds that re-arm the leg but never see an RX frame still cap out
    (MOR-3401 r3): exactly 3 attempts, one FAILED, then no further
    attempts — no endless teardown/reestablish loop.

    MOR-4214: one successful-but-frameless round ends its repair task, so
    rounds 2 and 3 are re-triggered by the session watchdog's next
    rx_silent — three liveness waits of RX_LIVENESS_TIMEOUT_S plus the
    watchdog interval each. Those are DECISION thresholds, not real-time
    waits: they are driven on a ManualClock here (the
    test_two_rx_silent_start_one_repair_task later-watchdog-edge
    precedent), and the real watchdog loop is stubbed so wall time cannot
    add or delay an edge. The repair itself — backoff sleeps, the rebuild,
    and the final round's frame observation window — runs for real.
    """
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        recovery: list[AudioRecoveryState] = []
        radio._on_audio_recovery = recovery.append
        session = radio.audio_session

        bus = radio.audio_bus
        sub = await session.subscribe_rx("web-audio")
        first_frame = await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S)
        assert first_frame is not None
        initial_heartbeat = bus.last_rx_frame_monotonic

        teardown_calls, restore_teardown = _spy_method(
            radio, "_teardown_audio_transport"
        )
        orig_ensure = radio._ensure_audio_transport
        orig_reestablish = session._reestablish_locked
        reestablished: list[int] = []
        release_reader = asyncio.Event()
        reader_started = asyncio.Event()

        async def _dead_ensure(*, reestablish: bool = True) -> None:
            assert reestablish is False
            await orig_ensure(reestablish=reestablish)
            _kill_client_audio_only(radio)
            # A setup burst arrived before re-arm, then ingress stopped.
            transport = radio._audio_transport
            assert transport is not None
            _drain(transport._packet_queue)
            for seq in range(bus._jitter_depth):
                raw = build_audio_packet(
                    first_frame.data,
                    sender_id=transport.remote_id,
                    receiver_id=transport.my_id,
                    send_seq=seq,
                    ident=first_frame.ident,
                )
                transport._route_data_packet(raw, 0, seq, transport.remote_id)
            assert transport._packet_queue.qsize() == bus._jitter_depth
            receive_packet = transport.receive_packet

            async def delayed_receive(timeout: float = 5.0) -> bytes:
                await release_reader.wait()
                reader_started.set()
                return await receive_packet(timeout)

            monkeypatch.setattr(transport, "receive_packet", delayed_receive)

        async def _spy_reestablish() -> None:
            await orig_reestablish()
            reestablished.append(1)

        radio._ensure_audio_transport = _dead_ensure  # type: ignore[method-assign]
        session._reestablish_locked = _spy_reestablish  # type: ignore[method-assign]

        # Inject the watchdog loop so wall time cannot add an unrequested
        # edge (the test_two_rx_silent_start_one_repair_task precedent);
        # every rx_silent below comes from a driven liveness decision.
        watchdog = session._watchdog_task
        assert watchdog is not None
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)
        monkeypatch.setattr(session, "_watchdog_loop", asyncio.Event().wait)

        if healthy_frame_before_death:
            # Force healthy delivery across the watchdog-stop yield. A
            # negative-control baseline taken before it would be stale.
            assert await await_until(
                lambda: bus.last_rx_frame_monotonic != initial_heartbeat,
                timeout=RESUME_BOUND_S,
                description="healthy RX during watchdog shutdown",
            )

        async def _drive_rx_silent() -> None:
            """One liveness DECISION, driven deterministically (MOR-4214):
            the same ManualClock advance the spent-budget and two_rx_silent
            tests use. The repair execution and frame observation stay
            real; only the decision threshold is driven."""
            heartbeat = radio.audio_bus.last_rx_frame_monotonic
            assert heartbeat is not None, "no RX heartbeat to drive silence from"
            decision = ManualClock(start=max(heartbeat, session._rx_armed_at))
            decision.advance(RX_LIVENESS_TIMEOUT_S)
            with monkeypatch.context() as patch:
                patch.setattr(session, "_monotonic", decision)
                async with session._lock:
                    session._check_liveness_locked()

        try:
            _kill_client_audio_only(radio)
            # Closing UDP does not stop its consumer. Join the original
            # reader before taking the stable negative-control heartbeat.
            await radio.stop_rx()
            _drain(sub)
            heartbeat_before_death = bus.last_rx_frame_monotonic

            # One driven decision per round. Each successful-but-frameless
            # round ends its own task; the next edge continues the SAME
            # episode (no frames since the settled baseline, same CI-V
            # epoch), so the budget carries over: 1 s backoff before round
            # 2, 2 s before round 3, and the third round observes frames
            # for RX_LIVENESS_TIMEOUT_S before reporting FAILED once.
            for round_no in range(1, 4):
                release_reader.clear()
                reader_started.clear()
                await _drive_rx_silent()
                task = radio._audio_silence_repair_task
                assert task is not None, (
                    f"round {round_no}: rx_silent scheduled no repair task"
                )
                assert await await_until(
                    lambda: len(reestablished) == round_no, timeout=RESUME_BOUND_S
                )
                if round_no < 3:
                    # Let the baseline settle before the queued reader runs.
                    await asyncio.wait_for(asyncio.shield(task), RESUME_BOUND_S)
                release_reader.set()
                assert await await_until(
                    lambda: (
                        reader_started.is_set()
                        and radio._audio_transport._packet_queue.empty()
                    ),
                    timeout=RESUME_BOUND_S,
                    description=f"round {round_no} queued RX retired or consumed",
                )
                await asyncio.wait_for(asyncio.shield(task), RESUME_BOUND_S)
                assert bus.last_rx_frame_monotonic == heartbeat_before_death
                assert radio._audio_silence_repair_task is None
                assert radio._audio_silence_repair_attempts == round_no
                assert session.rx_restore_failed is (round_no == 3)
                assert len(reestablished) == round_no
                assert recovery == (
                    [AudioRecoveryState.FAILED] if round_no == 3 else []
                )

            # Every round genuinely re-armed the leg…
            assert len(reestablished) == 3
            # …exactly 3 attempts, then silence: later rx_silent edges —
            # the ones the old ≥ 5 s wall wait collected from the real
            # watchdog — schedule nothing with the budget spent.
            assert len(teardown_calls) == 3
            for _ in range(2):
                await _drive_rx_silent()
                assert radio._audio_silence_repair_task is None, (
                    "a spent budget still scheduled a repair"
                )
            assert len(teardown_calls) == 3
            assert len(reestablished) == 3
            assert radio._audio_silence_repair_attempts == 3
            assert recovery == [AudioRecoveryState.FAILED]

            # Positive control: new ingress after re-arm still reaches the
            # unmodified bus callback and opens a fresh repair episode.
            transport = radio._audio_transport
            assert transport is not None
            for seq in range(bus._jitter_depth):
                raw = build_audio_packet(
                    first_frame.data,
                    sender_id=transport.remote_id,
                    receiver_id=transport.my_id,
                    send_seq=seq,
                    ident=first_frame.ident,
                )
                transport._route_data_packet(raw, 0, seq, transport.remote_id)
            assert await _next_frame_or_none(sub, RESUME_BOUND_S) is not None
            assert bus.last_rx_frame_monotonic != heartbeat_before_death
            assert session.state is AudioSessionState.RECOVERING
            # The real watchdog is disabled above. Observe the fresh
            # heartbeat before advancing to a later silence decision:
            # RECOVERING handles rx_resumed, not another rx_silent edge.
            heartbeat = bus.last_rx_frame_monotonic
            assert heartbeat is not None
            with monkeypatch.context() as patch:
                patch.setattr(session, "_monotonic", ManualClock(start=heartbeat))
                async with session._lock:
                    session._check_liveness_locked()
            assert session.state is AudioSessionState.RX_ONLY, (
                "fresh RX was not observed by a healthy liveness decision"
            )
            assert radio._audio_silence_repair_attempts == 0
            assert session.rx_restore_failed is False
            await _drive_rx_silent()
            task = radio._audio_silence_repair_task
            assert task is not None, "fresh ingress did not reopen the episode"
            await asyncio.wait_for(asyncio.shield(task), RESUME_BOUND_S)
            assert radio._audio_silence_repair_attempts == 1
            assert len(teardown_calls) == 4
            assert len(reestablished) == 4
            assert recovery == [AudioRecoveryState.FAILED]
        finally:
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            session._reestablish_locked = orig_reestablish  # type: ignore[method-assign]
            restore_teardown()
        await sub.release()
    finally:
        await stack.aclose()


async def test_frames_resumed_during_backoff_stop_repair_without_teardown() -> None:
    """Frames resuming during the backoff end the silence (MOR-3401 r3,
    review finding 3): the repair stops without another teardown."""
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        recovery: list[AudioRecoveryState] = []
        radio._on_audio_recovery = recovery.append

        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        teardown_calls, restore_teardown = _spy_method(
            radio, "_teardown_audio_transport"
        )
        orig_ensure = radio._ensure_audio_transport
        ensure_calls: list[int] = []

        async def _fail_ensure(*, reestablish: bool = True) -> None:
            ensure_calls.append(1)
            raise CoreConnectionError("audio leg never returns")

        radio._ensure_audio_transport = _fail_ensure  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)
            await _drive_rx_silent(radio)
            # Round 1 fails; the task now sleeps the 1 s backoff.
            assert await await_until(lambda: len(ensure_calls) >= 1, timeout=8.0)
            task = radio._audio_silence_repair_task
            assert task is not None
            # Another path heals audio mid-backoff: frames flow again well
            # before the repair wakes up.
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            await radio._teardown_audio_transport()
            await radio._ensure_audio_transport()
            await radio.audio_session.reestablish()
            _drain(sub)
            assert await _next_frame_or_none(sub, 2.0) is not None

            # Past every backoff window the repair ran no second round: one
            # teardown from the repair, one from the healing path.
            await asyncio.wait_for(asyncio.shield(task), 3.0)
            assert len(teardown_calls) == 2
            assert recovery == []
        finally:
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            restore_teardown()
        await sub.release()
    finally:
        await stack.aclose()
