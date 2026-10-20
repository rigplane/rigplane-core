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
import time
from collections.abc import Callable

import pytest

from rigplane.audio import AudioPacket
from rigplane.audio.session import AudioSessionEvent
from rigplane.core.exceptions import ConnectionError as CoreConnectionError
from rigplane.core.radio_protocol import RadioIdentity, RadioIdentityStatus
from rigplane.runtime._audio_recovery import AudioRecoveryState
from rigplane.runtime._connection_state import RadioConnectionState
from scripts.soak import soak_driver

#: Bound for "frames reach the subscription again" (the property a fix must give).
RESUME_BOUND_S = 10.0
#: Each test must stay well under ~30 s wall-clock.
_SANITY_FIRST_FRAME_S = 10.0
_CIV_CHECK_TIMEOUT_S = 5.0


async def _next_frame_or_none(sub: object, timeout_s: float) -> AudioPacket | None:
    """Next RX packet, or None on timeout (``asyncio.TimeoutError`` → None)."""
    try:
        return await sub.get(timeout=timeout_s)  # type: ignore[union-attr]
    except asyncio.TimeoutError:
        return None


async def _wait_until(predicate: Callable[[], bool], timeout_s: float) -> bool:
    """Poll *predicate* until it holds or *timeout_s* elapses."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return bool(predicate())


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
        t_death = time.monotonic()
        _kill_client_audio_only(radio)

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

        # Let the liveness detector observe the death first (3.0 s timeout).
        for _ in range(60):
            if any(e.reason == "rx_silent" for e in events):
                break
            await asyncio.sleep(0.1)

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
            t_death = time.monotonic()
            deadline = t_death + RESUME_BOUND_S
            audio_t = _kill_client_audio_only(radio)

            def remaining() -> float:
                budget = deadline - time.monotonic()
                assert budget > 0, "RX repair exceeded the original death+10s bound"
                return budget

            await asyncio.wait_for(silent_seen.wait(), remaining())
            assert repair_tasks, "rx_silent did not start a repair task"
            await asyncio.wait_for(asyncio.shield(repair_tasks[0]), remaining())
            replacement = radio._audio_transport
            assert replacement is not None and replacement is not audio_t
            assert session.rx_demand > 0
            assert session._bus.rx_active, "repair did not re-arm RX before subscribing"

            # stop_rx flushes the old jitter buffer; those queued frames cannot
            # prove recovery. Keep its demand while opening a fresh empty queue.
            _drain(sub)
            post_sub = await asyncio.wait_for(
                session.subscribe_rx("post-repair"), remaining()
            )
            packet = None
            while packet is None:
                packet = await _next_frame_or_none(post_sub, remaining())
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
            assert await asyncio.wait_for(_civ_answers(radio), remaining()) > 0, (
                "CI-V stopped answering"
            )
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
            await asyncio.sleep(0.5)
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
            await asyncio.sleep(0.5)
            assert calls == []
            assert radio._audio_silence_repair_task is None
        finally:
            radio._conn_state = RadioConnectionState.CONNECTED
            restore()
        await sub.release()
    finally:
        await stack.aclose()


async def test_no_repair_when_identity_hold_owns_link() -> None:
    """A LAN identity hold owning the open link blocks the repair."""
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        calls, restore = _spy_method(radio, "_teardown_audio_transport")
        try:
            radio._connection_identity = RadioIdentity(
                status=RadioIdentityStatus.NO_RESPONSE,
                expected_model=radio.model,
            )
            radio._lan_identity_owned_epoch = radio._civ_epoch
            radio._lan_identity_gate_running = True
            try:
                assert radio._lan_identity_phase_owns_open_link()
                radio.audio_session._emit("rx_silent")  # noqa: SLF001
                await asyncio.sleep(0.5)
                assert calls == []
                assert radio._audio_silence_repair_task is None
            finally:
                radio._lan_identity_gate_running = False
                radio._connection_identity = None
        finally:
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

        async def _fail_ensure() -> None:
            ensure_calls.append(1)
            raise CoreConnectionError("audio leg never returns")

        radio._ensure_audio_transport = _fail_ensure  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)
            # Wait for the first attempt to fail and the task to enter backoff.
            assert await _wait_until(lambda: len(ensure_calls) >= 1, timeout_s=8.0)
            await radio.disconnect()  # owner stops during the backoff sleep
            # The stop cancels the pending repair synchronously: no task
            # lingers in its backoff sleep after disconnect() returns.
            assert radio._audio_silence_repair_task is None
            await asyncio.sleep(3.0)  # past every backoff window
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

        async def _fail_ensure() -> None:
            ensure_calls.append(1)
            raise CoreConnectionError("audio leg never returns")

        radio._ensure_audio_transport = _fail_ensure  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)

            # 3 attempts with 1 s + 2 s backoff, then FAILED once.
            def _repair_done() -> bool:
                done = radio._audio_silence_repair_task is None
                return done and len(ensure_calls) == 3

            assert await _wait_until(
                _repair_done,
                timeout_s=15.0,
            ), f"expected 3 attempts and a finished task, got {len(ensure_calls)}"
            assert len(ensure_calls) == 3
            assert radio._audio_silence_repair_attempts == 3
            assert recovery == [AudioRecoveryState.FAILED]

            # No further attempts for ≥ 5 s after the cap.
            await asyncio.sleep(5.0)
            assert len(ensure_calls) == 3
            assert radio._audio_silence_repair_attempts == 3
            assert recovery == [AudioRecoveryState.FAILED]
        finally:
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
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

        async def _fail_ensure() -> None:
            ensure_calls.append(1)
            raise CoreConnectionError("audio leg never returns")

        radio._ensure_audio_transport = _fail_ensure  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)
            assert await _wait_until(lambda: len(ensure_calls) >= 1, timeout_s=8.0)
            # A buffered frame landing mid-episode must not fund another round.
            radio.audio_session._emit("rx_resumed")  # noqa: SLF001
            assert await _wait_until(
                lambda: radio._audio_silence_repair_task is None,
                timeout_s=15.0,
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

        async def _fail_first_ensure() -> None:
            ensure_calls.append(1)
            if len(ensure_calls) == 1:
                raise CoreConnectionError("first round fails")
            await orig_ensure()

        radio._ensure_audio_transport = _fail_first_ensure  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)
            _drain(sub)
            assert await _next_frame_or_none(sub, RESUME_BOUND_S) is not None
            assert await _wait_until(
                lambda: radio._audio_silence_repair_task is None,
                timeout_s=15.0,
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
        orig_reestablish = session.reestablish
        bus = radio.audio_bus
        orig_callback = bus._on_opus_packet
        allow_frames = asyncio.Event()
        delivered_at: list[float] = []
        returned_at: list[float] = []
        ensure_calls: list[int] = []
        release_frames: asyncio.TimerHandle | None = None

        def _gate_real_frames(packet: AudioPacket | None) -> None:
            if allow_frames.is_set() and packet is not None:
                delivered_at.append(time.monotonic())
                orig_callback(packet)

        async def _fail_first_two_ensures() -> None:
            ensure_calls.append(1)
            if len(ensure_calls) <= 2:
                raise CoreConnectionError("first two rounds fail")
            await orig_ensure()

        async def _reestablish_with_frame_gate() -> None:
            nonlocal release_frames
            await orig_reestablish()
            if frames_before_return:
                allow_frames.set()
                assert await _wait_until(lambda: bool(delivered_at), timeout_s=2.0)
            else:
                release_frames = asyncio.get_running_loop().call_later(
                    0.3, allow_frames.set
                )
            returned_at.append(time.monotonic())

        radio._ensure_audio_transport = _fail_first_two_ensures  # type: ignore[method-assign]
        session.reestablish = _reestablish_with_frame_gate  # type: ignore[method-assign]
        bus._on_opus_packet = _gate_real_frames  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)
            _drain(sub)
            assert await _wait_until(lambda: bool(returned_at), timeout_s=12.0)
            assert await _next_frame_or_none(sub, 2.0) is not None
            assert await _wait_until(
                lambda: radio._audio_silence_repair_task is None, timeout_s=5.0
            ), "third-round frame observation did not finish"
            assert len(teardown_calls) == 3
            assert radio._audio_silence_repair_attempts == 3
            assert recovery == [], "healthy third round falsely reported FAILED"
            assert (delivered_at[0] < returned_at[0]) is frames_before_return
            assert delivered_at[0] > radio._audio_silence_repair_teardown_completed
        finally:
            if release_frames is not None:
                release_frames.cancel()
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            session.reestablish = orig_reestablish  # type: ignore[method-assign]
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

        async def _fail_ensure() -> None:
            ensure_calls.append(1)
            raise CoreConnectionError("audio leg never returns")

        radio._ensure_audio_transport = _fail_ensure  # type: ignore[method-assign]
        try:
            # Spend the whole budget: 3 failed attempts, FAILED once.
            _kill_client_audio_only(radio)
            assert await _wait_until(
                lambda: (
                    radio._audio_silence_repair_task is None and len(ensure_calls) == 3
                ),
                timeout_s=15.0,
            ), f"expected 3 attempts and a finished task, got {len(ensure_calls)}"
            assert recovery == [AudioRecoveryState.FAILED]
            resumed_before = sum(1 for e in events if e.reason == "rx_resumed")

            # Another path rebuilds the transport and re-arms the stream
            # underneath the session but never reestablishes demand, so the
            # session stays RECOVERING until real frames arrive — and the
            # watchdog itself emits rx_resumed.
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            await radio._teardown_audio_transport()
            await radio._ensure_audio_transport()
            await radio.audio_bus.restart_rx()
            assert await _wait_until(
                lambda: (
                    sum(1 for e in events if e.reason == "rx_resumed") > resumed_before
                ),
                timeout_s=8.0,
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


async def test_two_rx_silent_start_one_repair_task() -> None:
    """Two rx_silent in quick succession schedule exactly one repair task.

    The silence is real (a killed leg): with healthy frames a repair task
    would correctly exit quietly (MOR-3401 r3), so single-flight is pinned
    against a death instead. Both emits land before the watchdog's own
    edge — the first schedules the task synchronously, the second sees it
    running.
    """
    stack = await soak_driver._start_simulator_stack()
    try:
        radio = stack.radio
        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None

        calls, restore = _spy_method(radio, "_teardown_audio_transport")
        try:
            _kill_client_audio_only(radio)
            _drain(sub)
            radio.audio_session._emit("rx_silent")  # noqa: SLF001
            radio.audio_session._emit("rx_silent")  # noqa: SLF001

            # The single repair task still re-establishes RX.
            assert await _next_frame_or_none(sub, RESUME_BOUND_S) is not None
            assert await _wait_until(
                lambda: radio._audio_silence_repair_task is None,
                timeout_s=5.0,
            )
            assert len(calls) == 1, "a second rx_silent started a second repair"
        finally:
            restore()
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
            await asyncio.sleep(0.5)
            assert calls == []
            assert radio._audio_silence_repair_task is None
        finally:
            restore()
        await sub.release()
    finally:
        await radio.disconnect()


async def test_spent_budget_reopens_after_audio_fixed_by_another_path() -> None:
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

        async def _fail_ensure() -> None:
            ensure_calls.append(1)
            raise CoreConnectionError("audio leg never returns")

        radio._ensure_audio_transport = _fail_ensure  # type: ignore[method-assign]
        try:
            # Spend the whole budget: 3 failed attempts, FAILED once.
            _kill_client_audio_only(radio)
            assert await _wait_until(
                lambda: (
                    radio._audio_silence_repair_task is None and len(ensure_calls) == 3
                ),
                timeout_s=15.0,
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
            _kill_client_audio_only(radio)
            _drain(sub)
            assert await _next_frame_or_none(sub, RESUME_BOUND_S) is not None, (
                "MOR-3401 r3: the budget spent on the first episode leaked — "
                "a later real death was never repaired"
            )
            assert len(teardown_calls) > repaired_teardowns, (
                "no repair round ran for the second death"
            )
        finally:
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            restore_teardown()
        await sub.release()
    finally:
        await stack.aclose()


async def test_successful_rounds_without_frames_cap_and_report_failed() -> None:
    """Rounds that re-arm the leg but never see an RX frame still cap out
    (MOR-3401 r3): exactly 3 attempts, one FAILED, then no further
    attempts for ≥ 5 s — no endless teardown/reestablish loop."""
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
        session = radio.audio_session
        orig_reestablish = session.reestablish
        reestablished: list[int] = []

        async def _dead_ensure() -> None:
            await orig_ensure()
            # The new leg is dead on arrival, but the call succeeds: the
            # radio accepted the handshake yet sends no RX frames. The RX
            # callback is only (re-)registered by reestablish below, so no
            # frame can slip through between the rebuild and the kill.
            _kill_client_audio_only(radio)

        async def _spy_reestablish() -> None:
            await orig_reestablish()
            reestablished.append(1)

        radio._ensure_audio_transport = _dead_ensure  # type: ignore[method-assign]
        session.reestablish = _spy_reestablish  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)
            _drain(sub)

            assert await _wait_until(
                lambda: recovery == [AudioRecoveryState.FAILED],
                timeout_s=25.0,
            ), (
                "MOR-3401 r3: successful-but-frameless rounds never "
                "reported FAILED "
                f"(teardowns={len(teardown_calls)} "
                f"reestablished={len(reestablished)})"
            )
            # Every round genuinely re-armed the leg…
            assert len(reestablished) == 3
            # …exactly 3 attempts, then silence for ≥ 5 s.
            assert len(teardown_calls) == 3
            await asyncio.sleep(5.0)
            assert len(teardown_calls) == 3
            assert len(reestablished) == 3
            assert recovery == [AudioRecoveryState.FAILED]
        finally:
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            session.reestablish = orig_reestablish  # type: ignore[method-assign]
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

        async def _fail_ensure() -> None:
            ensure_calls.append(1)
            raise CoreConnectionError("audio leg never returns")

        radio._ensure_audio_transport = _fail_ensure  # type: ignore[method-assign]
        try:
            _kill_client_audio_only(radio)
            # Round 1 fails; the task now sleeps the 1 s backoff.
            assert await _wait_until(lambda: len(ensure_calls) >= 1, timeout_s=8.0)
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
            await asyncio.sleep(3.0)
            assert len(teardown_calls) == 2
            assert recovery == []
        finally:
            radio._ensure_audio_transport = orig_ensure  # type: ignore[method-assign]
            restore_teardown()
        await sub.release()
    finally:
        await stack.aclose()
