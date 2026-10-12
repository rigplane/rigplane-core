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

from rigplane_testkit.icom import MockIcomRadio

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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "MOR-3349: after a silent session drop the soft reconnect leaves the "
        "identity hold on a dead session and no re-login follows"
    ),
)
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "MOR-3296: the second recovery attempt after a power-off rebuilds CI-V "
        "on a control transport that never logged in"
    ),
)
async def test_power_off_with_notice_recovers_with_one_login(
    sim: MockIcomRadio, caplog: pytest.LogCaptureFixture
) -> None:
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
