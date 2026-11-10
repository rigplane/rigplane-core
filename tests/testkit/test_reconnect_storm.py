"""Recovery of a production ``IcomRadio`` after the radio ends its LAN session.

An ``IcomRadio`` with ``auto_reconnect=True`` and the default
``watchdog_timeout`` is connected to a single-owner ``MockIcomRadio``; a task
that asks ``get_freq()`` in a loop stands in for the web layer's poller. Each
test schedules one radio-side event on the simulator -- a session drop with or
without the busy=0 conninfo notice, or a power-off with the recorded notice --
and checks that the client holds a usable session with the radio's new token
within a budget, how many logins and CI-V OpenClose(open) packets the
simulator counted, and that the client then stays CONNECTED for
``_OBSERVE_S`` seconds without logging the
``civ.soft_reconnect.already_open_stalled`` line.

The no-notice drop also pins how it recovered: the watchdog probes the
control session before its OpenClose patience and escalates on exactly one
rejected renewal. A standby scenario (a simulator scenario, not recorded
radio behaviour) pins the other side: a radio that keeps answering renewals
while its CI-V DATA is silent for 20 s never escalates and never re-logs in.

``_Run.report`` prints the state sequence, reconnect-status callbacks,
counters and the first log lines when an assert fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from rigplane.radio import IcomRadio
from rigplane.runtime._civ_rx import (
    _CIV_DATA_WATCHDOG_TIMEOUT,
    _SILENT_OPENCLOSE_DEADLINE,
)
from rigplane.runtime._connection_state import RadioConnectionState

from rigplane_testkit.icom import FaultPlan, MockIcomRadio

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


async def _sample(
    radio: IcomRadio,
    states: list[RadioConnectionState],
    seconds: float,
    done: Callable[[], bool] = lambda: False,
) -> bool:
    """Record ``conn_state`` changes every 20 ms until ``done()`` or the deadline."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        state = radio.conn_state
        if not states or states[-1] is not state:
            states.append(state)
        if done():
            return True
        await asyncio.sleep(0.02)
    return False


async def _ask_freq(radio: IcomRadio, timeout: float) -> int | str:
    """The answer to ``get_freq()``, or text saying why there is none.

    The call runs in its own task, so a cancelled request is reported as an
    answer here instead of cancelling the caller.
    """
    ask = asyncio.ensure_future(radio.get_freq())
    ask.add_done_callback(lambda t: t.cancelled() or t.exception())  # mark retrieved
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


async def _poll(radio: IcomRadio) -> None:
    """Ask for the frequency in a loop, standing in for the web layer's poller."""
    while True:
        await _ask_freq(radio, 1.0)
        await asyncio.sleep(0.5)


def _is_rigplane(record: logging.LogRecord) -> bool:
    return record.name == "rigplane" or record.name.startswith("rigplane.")


async def _ride_out(
    sim: MockIcomRadio,
    caplog: pytest.LogCaptureFixture,
    schedule: Callable[[MockIcomRadio], None],
    recovery_budget_s: float,
) -> _Run:
    """Connect, schedule the event, wait for recovery, then watch ``_OBSERVE_S``."""
    caplog.set_level(logging.INFO, logger="rigplane")
    radio = _client(sim)
    run = _Run()
    with fast_connect():
        await radio.connect()
        poller = asyncio.create_task(_poll(radio))
        try:
            run.old_token = radio._token
            logins0 = sim.login_count
            opens0 = sim.openclose_open_count
            rejects0 = sim.busy_rejects
            first_record = len(caplog.records)
            wall0, t0 = time.time(), time.monotonic()
            radio.set_reconnect_status_callback(
                lambda payload: run.statuses.append((time.monotonic() - t0, payload))
            )

            schedule(sim)
            if await _sample(
                radio,
                run.states,
                recovery_budget_s,
                lambda: (
                    sim.token != run.old_token  # the event has fired
                    and radio.conn_state is _CONNECTED
                    and radio.connected
                    and radio._token == sim.token
                ),
            ):
                run.recovered_after_s = time.monotonic() - t0
                after = len(caplog.records)
                opens_at_recovery = sim.openclose_open_count
                run.states_after.append(radio.conn_state)
                await _sample(radio, run.states_after, _OBSERVE_S)
                run.opens_after = sim.openclose_open_count - opens_at_recovery
                run.storm_lines = [
                    record.getMessage()
                    for record in caplog.records[after:]
                    if _is_rigplane(record) and _STORM_MARKER in record.getMessage()
                ]
                run.freq = await _ask_freq(radio, 5.0)
            run.sim_token = sim.token
            run.logins = sim.login_count - logins0
            run.opens = sim.openclose_open_count - opens0
            run.busy_rejects = sim.busy_rejects - rejects0
            run.log_head = [
                f"{record.created - wall0:6.2f}s {record.levelname} "
                f"{record.name}: {record.getMessage()}"
                for record in caplog.records[first_record:]
                if _is_rigplane(record)
            ][:30]
            run.dead_hold_lines = [
                record.getMessage()
                for record in caplog.records[first_record:]
                if _is_rigplane(record)
                and "control session dead (" in record.getMessage()
            ]
        finally:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller
            await asyncio.wait_for(radio.disconnect(), 10.0)
    return run


async def test_session_drop_with_notice_recovers_with_one_login(
    sim: MockIcomRadio, caplog: pytest.LogCaptureFixture
) -> None:
    run = await _ride_out(
        sim,
        caplog,
        lambda s: s.drop_session(at=_EVENT_AT_S, notice=True),
        _RECOVERY_S,
    )
    assert run.recovered_after_s is not None, run.report()
    assert run.logins == 1, run.report()
    assert run.opens <= 3, run.report()
    assert run.states_after == [_CONNECTED], run.report()
    assert run.storm_lines == [], run.report()
    assert run.freq == _FREQ_HZ, run.report()


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

    async def parked_audio_transport() -> None:
        entries.append(time.monotonic())
        try:
            await release_tail.wait()
        except asyncio.CancelledError:
            cancels.append(time.monotonic())
            raise

    with fast_connect():
        await radio.connect()
        monkeypatch.setattr(
            radio,
            "_ensure_audio_transport",
            parked_audio_transport,  # type: ignore[method-assign]
        )
        poller = asyncio.create_task(_poll(radio))
        try:
            old_token = radio._token
            logins0 = sim.login_count
            first_record = len(caplog.records)
            wall0, t0 = time.time(), time.monotonic()
            states: list[RadioConnectionState] = []

            def report() -> str:
                head = [
                    f"{record.created - wall0:6.2f}s {record.levelname} "
                    f"{record.name}: {record.getMessage()}"
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
                """20 ms sampler: first usable session on a fresh token."""
                end = t0 + drop_offset + 1.5
                while True:
                    state = radio.conn_state
                    if not states or states[-1] is not state:
                        states.append(state)
                    if (
                        state is _CONNECTED
                        and radio.connected
                        and radio._token == sim.token
                        and sim.token != token_before
                    ):
                        break
                    if time.monotonic() >= end:
                        pytest.fail(
                            f"{what} drop not followed by a usable session "
                            f"within 1.5 s: {report()}"
                        )
                    await asyncio.sleep(0.02)
                recovered_after = time.monotonic() - (t0 + drop_offset)
                freq = await _ask_freq(radio, 5.0)
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
            end = time.monotonic() + 5.0
            while len(entries) < 2:
                if time.monotonic() >= end:
                    pytest.fail(
                        "the second recovery never reached its tail: " + report()
                    )
                await asyncio.sleep(0.02)
            assert cancels, "the first recovery tail was never cancelled: " + report()
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
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller
            await asyncio.wait_for(radio.disconnect(), 10.0)


async def test_session_drop_without_notice_recovers_within_the_watchdog_budget(
    sim: MockIcomRadio,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("rigplane.runtime._civ_rx._OPENCLOSE_DEADLINE", _PATIENCE_S)
    run = await _ride_out(
        sim,
        caplog,
        lambda s: s.drop_session(at=_EVENT_AT_S, notice=False),
        _SILENT_DROP_RECOVERY_S,
    )
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


async def test_silent_drop_with_default_patience_recovers_within_10s(
    sim: MockIcomRadio,
    caplog: pytest.LogCaptureFixture,
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
    run = await _ride_out(
        sim,
        caplog,
        lambda s: s.drop_session(at=_EVENT_AT_S, notice=False),
        _FAST_SILENT_RECOVERY_S,
    )
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


async def test_long_civ_silence_with_live_control_never_reconnects(
    sim: MockIcomRadio,
    caplog: pytest.LogCaptureFixture,
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
        poller = asyncio.create_task(_poll(radio))
        try:
            logins0 = sim.login_count
            first_record = len(caplog.records)
            sim.civ_standby(True)

            states: list[RadioConnectionState] = []
            await _sample(radio, states, _LONG_CIV_SILENCE_S + 3.0)

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
            assert await _ask_freq(radio, 5.0) == _FREQ_HZ
            assert radio.conn_state is _CONNECTED
        finally:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller
            await asyncio.wait_for(radio.disconnect(), 10.0)


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
    run = await _ride_out(
        sim,
        caplog,
        lambda s: s.reboot(
            at=_EVENT_AT_S, down_for=_DOWN_FOR_S, busy_for=_BUSY_FOR_S, notice=True
        ),
        _EVENT_AT_S + _DOWN_FOR_S + _BUSY_FOR_S + _RECOVERY_S,
    )
    assert run.recovered_after_s is not None, run.report()
    assert run.logins == 1, run.report()
    assert run.opens_after <= 3, run.report()
    assert run.states_after == [_CONNECTED], run.report()
    assert run.storm_lines == [], run.report()
    assert run.freq == _FREQ_HZ, run.report()
