"""Recovery of a production ``IcomRadio`` after the radio ends its LAN session.

An ``IcomRadio`` with ``auto_reconnect=True`` and the default
``watchdog_timeout`` is connected to a single-owner ``MockIcomRadio``; a task
that asks ``get_freq()`` in a loop stands in for the web layer's poller. Each
test schedules one radio-side event on the simulator — a session drop with or
without the busy=0 conninfo notice, or a power-off with the recorded notice —
and checks that the client holds a usable session with the radio's new token
within a budget, how many logins and CI-V OpenClose(open) packets the
simulator counted, and that the client then stays CONNECTED for
``_OBSERVE_S`` virtual seconds without logging the
``civ.soft_reconnect.already_open_stalled`` line.

The no-notice drop also pins how it recovered: the watchdog probes the
control session before its OpenClose patience and escalates on exactly one
rejected renewal. A standby scenario (a simulator scenario, not recorded
radio behaviour) pins the other side: a radio that keeps answering renewals
while its CI-V DATA is silent for 20 s never escalates and never re-logs in.

All timing is virtual. Inside ``VirtualClock.on_running_loop`` every loop
timer — the watchdog checks, reconnect backoffs, simulator events,
keepalives, the poller — follows the test's clock and ``clock.elapse`` moves
it, while real socket I/O still drains in the settles between timers. Only
the modules' own ``time`` reads are redirected at the clock (their
``asyncio`` stays real so sleeps keep following the loop). One task advances
the clock: each step moves virtual time a little, then parks on a shared
real-event ``await_until`` wait (virtual time frozen, loop turning, kernel
bytes landing) before the next timers fire, and the predicate is sampled in
that same task — never by a racing driver while a sampler polls on real
time. The same discipline paces the observation soak (each step waits for a
real poller round-trip) and every frequency read (the advancer stays active
through the read). Budgets keep their real-time values as virtual seconds,
and every assertion keeps its meaning.

``_Run.report`` prints the state sequence, reconnect-status callbacks,
counters and the first log lines when an assert fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from rigplane.core import acquisition_drain as acquisition_drain_module
from rigplane.core import acquisition_scheduler as acquisition_scheduler_module
from rigplane.core import civ as core_civ_module
from rigplane.core import transport as core_transport_module
from rigplane.radio import IcomRadio
from rigplane.runtime import _civ_rx as civ_rx_module
from rigplane.runtime import _control_phase as control_phase_module
from rigplane.runtime import radio as radio_runtime_module
from rigplane.runtime import radio_reconnect as radio_reconnect_module
from rigplane.runtime import session_lifecycle as session_lifecycle_module
from rigplane.runtime._civ_rx import (
    _CIV_DATA_WATCHDOG_TIMEOUT,
    _SILENT_OPENCLOSE_DEADLINE,
)
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.testing.timing import VirtualClock, await_until

from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.icom import simulator

from _perf_helpers import fast_connect

_CONNECTED = RadioConnectionState.CONNECTED
_OBSERVE_S = 20.0
_RECOVERY_S = 10.0
_FREQ_HZ = 14_074_000  # the simulator's default frequency

_EVENT_AT_S = 0.1
_DOWN_FOR_S = 3.0
_BUSY_FOR_S = 0.5

# The drop without the notice runs with the CI-V data watchdog's patient OpenClose
# window ``_civ_rx._OPENCLOSE_DEADLINE`` (60 s in the source) set to _PATIENCE_S.
# Cap for that test, composed of the windows in ``_civ_data_watchdog_loop``: two
# stall detections (each up to 1.5 x _CIV_DATA_WATCHDOG_TIMEOUT), _PATIENCE_S, the
# shorter window _SILENT_OPENCLOSE_DEADLINE, and 10 s of slack.
_PATIENCE_S = 10.0
_SILENT_DROP_RECOVERY_S = (
    2 * 1.5 * _CIV_DATA_WATCHDOG_TIMEOUT
    + _PATIENCE_S
    + _SILENT_OPENCLOSE_DEADLINE
    + 10.0
)
_STORM_MARKER = "civ.soft_reconnect.already_open_stalled"

# MOR-3703: a silent drop with the DEFAULT 60 s OpenClose patience must
# still recover within ~10 s — the watchdog probes the control session
# before waiting the patience out, and a rejected renewal escalates at once.
_FAST_SILENT_RECOVERY_S = 10.0

# Long CI-V silence variant: CI-V DATA withheld while pings and renewals
# keep their instant answers; the default 60 s patience must hold.
_LONG_CIV_SILENCE_S = 20.0


@pytest.fixture
async def sim() -> AsyncGenerator[MockIcomRadio]:
    server = MockIcomRadio(single_owner=True, keepalive_hold_s=0.5)
    await server.start()
    yield server
    await server.stop()


def _client(sim: MockIcomRadio) -> IcomRadio:
    return IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
        model="IC-7610",
        auto_reconnect=True,
        reconnect_delay=0.2,
        reconnect_max_delay=0.5,
    )


@dataclass
class _Run:
    """What one scenario observed."""

    old_token: int = 0
    sim_token: int = 0
    recovered_after_s: float | None = None
    states: list[RadioConnectionState] = field(default_factory=list)
    states_after: list[RadioConnectionState] = field(default_factory=list)
    statuses: list[tuple[float, dict[str, Any]]] = field(default_factory=list)
    logins: int = 0  # accepted logins since the event was scheduled
    opens: int = 0  # OpenClose(open) packets since the event was scheduled
    opens_after: int = 0  # OpenClose(open) packets after recovery
    busy_rejects: int = 0  # 0xFFFFFFFF rejects since the event was scheduled
    storm_lines: list[str] = field(default_factory=list)
    freq: int | str | None = None
    log_head: list[str] = field(default_factory=list)
    # Every "control session dead (<verdict>)" line
    # of the run (log_head keeps only the first 30 lines).
    dead_hold_lines: list[str] = field(default_factory=list)

    def report(self) -> str:
        return "\n".join(
            [
                f"recovered_after_s={self.recovered_after_s}",
                f"tokens: old={self.old_token:#x} simulator={self.sim_token:#x}",
                f"states={[s.value for s in self.states]}",
                f"states_after={[s.value for s in self.states_after]}",
                f"reconnect statuses={self.statuses}",
                f"logins={self.logins} busy_rejects={self.busy_rejects} "
                f"openclose_opens={self.opens} (after recovery {self.opens_after})",
                f"freq={self.freq!r} storm_lines={self.storm_lines}",
                f"dead_hold_lines={self.dead_hold_lines}",
                *self.log_head,
            ]
        )


async def _ask_freq(
    radio: IcomRadio,
    timeout: float,
    clock: VirtualClock | None = None,
    *,
    description: str = "frequency read",
    report: Callable[[], str] | None = None,
) -> int | str:
    """The answer to ``get_freq()``, or text saying why there is none.

    The call runs in its own task, so a cancelled request is reported as an
    answer here instead of cancelling the caller. With ``clock`` given, this
    test's single advancer stays active through the read: virtual time keeps
    moving through the CI-V pacing gap and the product response timeout while
    socket completion is synchronized with the shared helper, and on failure
    the request is cancelled and awaited before answering. Without ``clock``
    (the background poller, which must never advance the clock itself) the
    read waits on the virtual timeout the advancer drives.
    """
    ask = asyncio.ensure_future(radio.get_freq())
    ask.add_done_callback(lambda t: t.cancelled() or t.exception())  # mark retrieved
    if clock is not None:
        detail = report if report is not None else (lambda: "no extra context")
        try:
            await _drive_clock_until(
                clock,
                ask.done,
                budget_s=timeout,
                step_s=_FREQ_READ_STEP_S,
                description=description,
                report=detail,
            )
        except asyncio.CancelledError:
            ask.cancel()
            raise
        except AssertionError:
            ask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ask
            return f"no answer in {timeout} s"
        if ask.cancelled():
            return "cancelled"
        error = ask.exception()
        return repr(error) if error is not None else ask.result()
    try:
        done, _ = await asyncio.wait({ask}, timeout=timeout)
        if not done:
            ask.cancel()
            await asyncio.wait({ask})
    except asyncio.CancelledError:
        ask.cancel()
        raise
    if ask.cancelled():
        return "cancelled" if done else f"no answer in {timeout} s"
    error = ask.exception()
    return repr(error) if error is not None else ask.result()


async def _poll(
    radio: IcomRadio,
    rounds: list[int] | None = None,
) -> None:
    """Ask for the frequency in a loop, standing in for the web layer's poller.

    Each finished round (answer or virtual timeout) is counted in ``rounds``
    when given, giving the observation soak a real socket-completion signal
    to pace its virtual steps on. The poller never advances the clock.
    """
    while True:
        await _ask_freq(radio, 1.0)
        if rounds is not None:
            rounds.append(1)
        await asyncio.sleep(0.5)


def _is_rigplane(record: logging.LogRecord) -> bool:
    return record.name == "rigplane" or record.name.startswith("rigplane.")


def _scope_virtual_time(
    monkeypatch: pytest.MonkeyPatch,
    sim: MockIcomRadio,
    clock: VirtualClock,
) -> None:
    """Read every wall clock on the reconnect path from ``clock``.

    Inside ``VirtualClock.on_running_loop`` the loop timers are already
    virtual; only the modules' own ``time`` reads need redirecting (their
    ``asyncio`` stays the real one so sleeps keep following the loop).
    Everything reverts with ``monkeypatch`` when the test ends.
    ``sim.clock`` is the simulator's documented test seam for its
    retransmit pacing.
    """
    sim.clock = clock
    clock.scope_to(monkeypatch, simulator)
    monkeypatch.setattr(simulator, "asyncio", asyncio)
    for module in (
        civ_rx_module,
        control_phase_module,
        radio_reconnect_module,
        session_lifecycle_module,
        radio_runtime_module,
        core_transport_module,
        core_civ_module,
        acquisition_scheduler_module,
        acquisition_drain_module,
    ):
        clock.scope_to(monkeypatch, module)
        monkeypatch.setattr(module, "asyncio", asyncio)


# Real-time budgets for the parks between virtual steps: each park lets
# kernel bytes land and peer tasks answer (the loop keeps turning) while the
# virtual clock is frozen, so virtual timers never run ahead of real delivery.
# Parks return at once when their condition holds; the full budget is paid
# only while genuinely waiting.
_REAL_DELIVERY_S = 0.2
_REAL_DELIVERY_POLL_S = 0.01
# The observation soak pays its park on almost every 0.1 s virtual step (a
# poll round takes longer than one step), so its breath stays short.
_REAL_BREATH_S = 0.02
_REAL_BREATH_POLL_S = 0.005
# Virtual step for driven frequency reads: small, since nothing after a read
# depends on how far the clock moved through it.
_FREQ_READ_STEP_S = 0.1


async def _drive_clock_until(
    clock: VirtualClock,
    cond: Callable[[], bool],
    *,
    budget_s: float,
    step_s: float,
    description: str,
    report: Callable[[], str],
) -> None:
    """Advance virtual time until ``cond()`` holds; fail with ``report()`` past the budget.

    One task advances the clock: each ``step_s`` increment is followed by a
    shared real-event ``await_until`` wait for ``cond()`` with the virtual
    clock parked, so the predicate is sampled in the advancing task right
    after the timers it depends on fired — recovery is observed on the first
    step where it holds instead of being skipped past while a separate
    sampler polls on real time. Only the virtual budget bounds the wait; the
    failure carries the run report, not a bare timeout.
    """
    start = clock.now
    while True:
        if cond():
            return
        if clock.now - start >= budget_s:
            raise AssertionError(
                f"{description} within {budget_s} s of virtual time\n{report()}"
            )
        await clock.elapse(min(step_s, budget_s - (clock.now - start)))
        # The timers above may have sent datagrams; let the kernel deliver
        # them and the peer tasks answer before the next timers fire.
        try:
            await await_until(
                cond,
                timeout=_REAL_DELIVERY_S,
                interval=_REAL_DELIVERY_POLL_S,
                description=description,
            )
        except AssertionError:
            pass  # delivery still in flight; the next virtual step follows


async def _observe_stable(
    clock: VirtualClock,
    radio: IcomRadio,
    states: list[RadioConnectionState],
    duration_s: float,
    rounds: list[int],
    *,
    step_s: float = 0.1,
) -> None:
    """Advance virtual time, sampling ``conn_state`` changes at each step.

    The same delivery discipline as the recovery waits: after each virtual
    step the clock is parked for one real poller round-trip (a finished
    ``_poll`` round is a completed socket round-trip), so keepalives and poll
    replies land before the watchdog's virtual timers advance. ``rounds`` is
    the poller's counter.
    """
    start = clock.now
    while clock.now - start < duration_s:
        mark = len(rounds)
        await clock.elapse(min(step_s, duration_s - (clock.now - start)))
        try:
            await await_until(
                lambda: len(rounds) > mark,
                timeout=_REAL_BREATH_S,
                interval=_REAL_BREATH_POLL_S,
                description="poller round-trip during observation",
            )
        except AssertionError:
            pass  # no round finished this step; the breath was still real
        state = radio.conn_state
        if states[-1] is not state:
            states.append(state)


async def _stop_watching(poller: asyncio.Task[None], radio: IcomRadio) -> None:
    """Cancel the poller and disconnect on the real loop (past the virtual block)."""
    poller.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await poller
    await asyncio.wait_for(radio.disconnect(), 10.0)


async def _run_scenario(
    sim: MockIcomRadio,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    clock: VirtualClock,
    radio: IcomRadio,
    schedule: Callable[[MockIcomRadio], None],
    recovery_budget_s: float,
    rounds: list[int],
) -> _Run:
    """Schedule the event, recover on the virtual clock, then observe.

    The caller connects on the real loop first and owns teardown: ``clock``
    is already installed on this loop here (each test's own ``async with``
    block, which is also what marks the test virtual for the realtime
    guard).
    """
    caplog.set_level(logging.INFO, logger="rigplane")
    run = _Run()
    run.old_token = radio._token
    logins0 = sim.login_count
    opens0 = sim.openclose_open_count
    rejects0 = sim.busy_rejects
    _scope_virtual_time(monkeypatch, sim, clock)
    first_record = len(caplog.records)
    t0 = clock.now
    radio.set_reconnect_status_callback(
        lambda payload: run.statuses.append((clock.now, payload))
    )

    schedule(sim)

    def usable() -> bool:
        state = radio.conn_state
        if not run.states or run.states[-1] is not state:
            run.states.append(state)
        return (
            sim.token != run.old_token  # the event has fired
            and radio.conn_state is _CONNECTED
            and radio.connected
            and radio._token == sim.token
        )

    await _drive_clock_until(
        clock,
        usable,
        budget_s=recovery_budget_s,
        step_s=0.5,
        description="a usable session on the radio's fresh token",
        report=run.report,
    )
    run.recovered_after_s = clock.now - t0
    after = len(caplog.records)
    opens_at_recovery = sim.openclose_open_count
    run.states_after.append(radio.conn_state)
    await _observe_stable(clock, radio, run.states_after, _OBSERVE_S, rounds)
    run.opens_after = sim.openclose_open_count - opens_at_recovery
    run.storm_lines = [
        record.getMessage()
        for record in caplog.records[after:]
        if _is_rigplane(record) and _STORM_MARKER in record.getMessage()
    ]
    run.freq = await _ask_freq(
        radio,
        5.0,
        clock,
        description="post-recovery frequency read",
        report=run.report,
    )
    run.sim_token = sim.token
    run.logins = sim.login_count - logins0
    run.opens = sim.openclose_open_count - opens0
    run.busy_rejects = sim.busy_rejects - rejects0
    run.log_head = [
        f"{record.levelname} {record.name}: {record.getMessage()}"
        for record in caplog.records[first_record:]
        if _is_rigplane(record)
    ][:30]
    run.dead_hold_lines = [
        record.getMessage()
        for record in caplog.records[first_record:]
        if _is_rigplane(record) and "control session dead (" in record.getMessage()
    ]
    return run


@pytest.mark.timeout(180)
async def test_session_drop_with_notice_recovers_with_one_login(
    sim: MockIcomRadio,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with fast_connect():
        radio = _client(sim)
        await radio.connect()
        rounds: list[int] = []
        poller = asyncio.create_task(_poll(radio, rounds=rounds))
        try:
            async with VirtualClock.on_running_loop() as clock:
                run = await _run_scenario(
                    sim,
                    caplog,
                    monkeypatch,
                    clock,
                    radio,
                    lambda s: s.drop_session(at=_EVENT_AT_S, notice=True),
                    _RECOVERY_S,
                    rounds,
                )
        finally:
            await _stop_watching(poller, radio)
    assert run.recovered_after_s is not None, run.report()
    assert run.logins == 1, run.report()
    assert run.opens <= 3, run.report()
    assert run.states_after == [_CONNECTED], run.report()
    assert run.storm_lines == [], run.report()
    assert run.freq == _FREQ_HZ, run.report()


@pytest.mark.timeout(120)
async def test_two_session_drops_with_notices_recover_with_two_logins(
    sim: MockIcomRadio,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3350: a second busy=0 notice arriving while the first recovery is
    still in its tail restarts the recovery instead of being dropped silently.

    The recovery tail is parked deterministically: the audio-transport re-arm
    waits on an event that is only set during teardown, standing in for the
    ~10 s production tail when the audio port does not answer.
    """
    caplog.set_level(logging.INFO, logger="rigplane")
    radio = _client(sim)
    drop2_at = 3.1
    entries: list[float] = []
    cancels: list[float] = []
    release_tail = asyncio.Event()

    with fast_connect():
        await radio.connect()
        poller = asyncio.create_task(_poll(radio))
        try:
            async with VirtualClock.on_running_loop() as clock:
                _scope_virtual_time(monkeypatch, sim, clock)

                async def parked_audio_transport() -> None:
                    entries.append(clock.now)
                    try:
                        await release_tail.wait()
                    except asyncio.CancelledError:
                        cancels.append(clock.now)
                        raise

                monkeypatch.setattr(
                    radio,
                    "_ensure_audio_transport",
                    parked_audio_transport,  # type: ignore[method-assign]
                )
                old_token = radio._token
                logins0 = sim.login_count
                first_record = len(caplog.records)
                t0 = clock.now
                states: list[RadioConnectionState] = []

                def report() -> str:
                    head = [
                        f"{record.levelname} {record.name}: {record.getMessage()}"
                        for record in caplog.records[first_record:]
                        if _is_rigplane(record)
                    ][:30]
                    return "\n".join(
                        [
                            f"tokens: old={old_token:#x} "
                            f"simulator={sim.token:#x} client={radio._token:#x}",
                            f"states={[s.value for s in states]}",
                            f"logins={sim.login_count - logins0}",
                            f"tail_entries={[f'{t - t0:.2f}' for t in entries]}",
                            f"tail_cancels={[f'{t - t0:.2f}' for t in cancels]}",
                            *head,
                        ]
                    )

                async def wait_usable(
                    token_before: int, drop_offset: float, what: str
                ) -> int:
                    """First usable session on a fresh token, in virtual time."""
                    deadline = t0 + drop_offset + 1.5

                    def usable() -> bool:
                        state = radio.conn_state
                        if not states or states[-1] is not state:
                            states.append(state)
                        return (
                            state is _CONNECTED
                            and radio.connected
                            and radio._token == sim.token
                            and sim.token != token_before
                        )

                    await _drive_clock_until(
                        clock,
                        usable,
                        budget_s=deadline - clock.now,
                        step_s=0.5,
                        description=(
                            f"{what} drop not followed by a usable session within 1.5 s"
                        ),
                        report=report,
                    )
                    recovered_after = clock.now - (t0 + drop_offset)
                    freq = await _ask_freq(
                        radio,
                        5.0,
                        clock,
                        description=f"{what} post-recovery frequency read",
                        report=report,
                    )
                    assert freq == _FREQ_HZ, report()
                    assert recovered_after <= 1.5, report()
                    return sim.token

                sim.drop_session(at=0.1, notice=True)
                sim.drop_session(at=drop2_at, notice=True)

                token1 = await wait_usable(old_token, 0.1, "first")
                assert entries, "the first recovery never parked its tail: " + report()
                assert entries[0] - t0 < drop2_at, (
                    "the first recovery parked its tail only after the second "
                    "drop: " + report()
                )
                token2 = await wait_usable(token1, drop2_at, "second")
                assert token2 != token1, report()
                assert sim.login_count - logins0 == 2, report()
                await _drive_clock_until(
                    clock,
                    lambda: len(entries) >= 2,
                    budget_s=5.0,
                    step_s=0.5,
                    description="the second recovery never reached its tail",
                    report=report,
                )
                assert cancels, (
                    "the first recovery tail was never cancelled: " + report()
                )
                assert cancels[0] - t0 > drop2_at, (
                    "the first recovery tail was cancelled before the second "
                    "drop: " + report()
                )
                ignored = [
                    record.getMessage()
                    for record in caplog.records[first_record:]
                    if _is_rigplane(record) and "notice ignored" in record.getMessage()
                ]
                assert ignored == [], report()
        finally:
            release_tail.set()
            await _stop_watching(poller, radio)


@pytest.mark.timeout(180)
async def test_session_drop_without_notice_recovers_within_the_watchdog_budget(
    sim: MockIcomRadio,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("rigplane.runtime._civ_rx._OPENCLOSE_DEADLINE", _PATIENCE_S)
    with fast_connect():
        radio = _client(sim)
        await radio.connect()
        rounds: list[int] = []
        poller = asyncio.create_task(_poll(radio, rounds=rounds))
        try:
            async with VirtualClock.on_running_loop() as clock:
                run = await _run_scenario(
                    sim,
                    caplog,
                    monkeypatch,
                    clock,
                    radio,
                    lambda s: s.drop_session(at=_EVENT_AT_S, notice=False),
                    _SILENT_DROP_RECOVERY_S,
                    rounds,
                )
        finally:
            await _stop_watching(poller, radio)
    assert run.recovered_after_s is not None, run.report()
    assert run.logins == 1, run.report()
    # The watchdog's OpenClose nudges before recovery are not counted here.
    assert run.opens_after <= 3, run.report()
    assert run.states_after == [_CONNECTED], run.report()
    assert run.storm_lines == [], run.report()
    assert run.freq == _FREQ_HZ, run.report()
    # Recovery escalated on a rejected renewal: exactly one reject verdict,
    # never the three-silent-probes verdict. The verdict comes from the watchdog's pre-patience probe.
    assert len([line for line in run.dead_hold_lines if "(reject)" in line]) == 1, (
        run.report()
    )
    assert not [
        line for line in run.dead_hold_lines if "(no renewal answer)" in line
    ], run.report()


@pytest.mark.timeout(180)
async def test_silent_drop_with_default_patience_recovers_within_10s(
    sim: MockIcomRadio,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3703: a silent drop recovers in ~10 s at the DEFAULT patience.

    ``_OPENCLOSE_DEADLINE`` is NOT patched down here: it stays at its
    production 60 s. The drop fences CI-V while pings on both ports keep
    being answered, so the patient window is the 60 s one — but before
    waiting it out the watchdog probes the control session, the radio
    answers the renewal with the recorded reject, and recovery escalates to
    a re-login at once instead of after 60 s. On the old code this test
    fails: nothing recovers the session inside the 10 s budget.
    """
    with fast_connect():
        radio = _client(sim)
        await radio.connect()
        rounds: list[int] = []
        poller = asyncio.create_task(_poll(radio, rounds=rounds))
        try:
            async with VirtualClock.on_running_loop() as clock:
                run = await _run_scenario(
                    sim,
                    caplog,
                    monkeypatch,
                    clock,
                    radio,
                    lambda s: s.drop_session(at=_EVENT_AT_S, notice=False),
                    _FAST_SILENT_RECOVERY_S,
                    rounds,
                )
        finally:
            await _stop_watching(poller, radio)
    assert run.recovered_after_s is not None, run.report()
    assert run.recovered_after_s <= _FAST_SILENT_RECOVERY_S, run.report()
    assert run.logins == 1, run.report()
    assert run.states_after == [_CONNECTED], run.report()
    assert run.storm_lines == [], run.report()
    assert run.freq == _FREQ_HZ, run.report()
    # The escalation came from the pre-patience probe: exactly one rejected
    # renewal, never the three-silent-probes verdict.
    assert len([line for line in run.dead_hold_lines if "(reject)" in line]) == 1, (
        run.report()
    )
    assert not [
        line for line in run.dead_hold_lines if "(no renewal answer)" in line
    ], run.report()


@pytest.mark.timeout(180)
async def test_long_civ_silence_with_live_control_never_reconnects(
    sim: MockIcomRadio,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI-V DATA absent for 20 s while pings and renewals answer (MOR-3703).

    ``civ_standby`` models the delayed CI-V reply the way a fault-plan rule
    cannot (a rule on the CI-V stream would delay the ping replies too and
    read as a silent port): pings on both ports and token renewals keep
    their instant answers while every CI-V payload is withheld for 20 s.
    The pre-patience probe gets its OK, the DEFAULT 60 s patience is kept,
    no hand-off and no re-login happen, and the same session answers again
    once the radio leaves standby.
    """
    caplog.set_level(logging.INFO, logger="rigplane")
    sim.keepalive_hold_s = 30.0
    radio = _client(sim)
    with fast_connect():
        await radio.connect()
        rounds: list[int] = []
        poller = asyncio.create_task(_poll(radio, rounds=rounds))
        try:
            logins0 = sim.login_count
            async with VirtualClock.on_running_loop() as clock:
                _scope_virtual_time(monkeypatch, sim, clock)
                first_record = len(caplog.records)
                sim.civ_standby(True)

                states: list[RadioConnectionState] = [radio.conn_state]

                def report() -> str:
                    head = [
                        f"{record.levelname} {record.name}: {record.getMessage()}"
                        for record in caplog.records[first_record:]
                        if _is_rigplane(record)
                    ][:30]
                    return "\n".join(
                        [
                            f"states={[s.value for s in states]}",
                            f"logins={sim.login_count - logins0}",
                            *head,
                        ]
                    )

                await _observe_stable(
                    clock, radio, states, _LONG_CIV_SILENCE_S + 3.0, rounds
                )

                dead_lines = [
                    record.getMessage()
                    for record in caplog.records[first_record:]
                    if _is_rigplane(record)
                    and "control session dead" in record.getMessage()
                ]
                handoff_lines = [
                    record.getMessage()
                    for record in caplog.records[first_record:]
                    if _is_rigplane(record)
                    and "handing off to lifecycle recovery" in record.getMessage()
                ]
                assert dead_lines == []
                assert handoff_lines == []
                # The stall never left CONNECTED: no rebuild, no reconnect.
                assert states == [_CONNECTED], [s.value for s in states]
                assert sim.login_count == logins0

                sim.civ_standby(False)
                assert (
                    await _ask_freq(
                        radio,
                        5.0,
                        clock,
                        description="post-standby frequency read",
                        report=report,
                    )
                    == _FREQ_HZ
                )
                assert radio.conn_state is _CONNECTED
        finally:
            await _stop_watching(poller, radio)


@pytest.mark.timeout(180)
async def test_power_off_with_notice_recovers_with_one_login(
    sim: MockIcomRadio,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A post-reboot login that lands while the simulator is up but still inside
    # its busy window (``reboot(down_for=…, busy_for=…)``: ``_conninfo_status``
    # answers conninfo with civ_port=0 error=0xFFFFFFFF until down_for +
    # busy_for) drives ``_connect_once`` into the within-attempt reject pause,
    # ``ControlPhaseRuntime._STATUS_REJECT_COOLDOWN`` (30 s). This test pins
    # the one-login recovery, not that pause, and 30 s exceeds every budget
    # here. A busy
    # reject is only generated for a conninfo that arrived at or after the
    # radio came back, so pausing exactly ``busy_for`` lands the retry at or
    # after the busy window ends — whatever runner load did to the login's
    # position inside the window.
    monkeypatch.setattr(
        "rigplane.runtime._control_phase.ControlPhaseRuntime._STATUS_REJECT_COOLDOWN",
        _BUSY_FOR_S,
    )
    with fast_connect():
        radio = _client(sim)
        await radio.connect()
        rounds: list[int] = []
        poller = asyncio.create_task(_poll(radio, rounds=rounds))
        try:
            async with VirtualClock.on_running_loop() as clock:
                run = await _run_scenario(
                    sim,
                    caplog,
                    monkeypatch,
                    clock,
                    radio,
                    lambda s: s.reboot(
                        at=_EVENT_AT_S,
                        down_for=_DOWN_FOR_S,
                        busy_for=_BUSY_FOR_S,
                        notice=True,
                    ),
                    _EVENT_AT_S + _DOWN_FOR_S + _BUSY_FOR_S + _RECOVERY_S,
                    rounds,
                )
        finally:
            await _stop_watching(poller, radio)
    assert run.recovered_after_s is not None, run.report()
    assert run.logins == 1, run.report()
    assert run.opens_after <= 3, run.report()
    assert run.states_after == [_CONNECTED], run.report()
    assert run.storm_lines == [], run.report()
    assert run.freq == _FREQ_HZ, run.report()
