"""Regression tests for MOR-2081: a stopper's own teardown cancellation must
not be absorbed by the ``except asyncio.CancelledError`` handler wrapped
around the task it awaits.

Mechanism (see MOR-2081 for the full investigation): ``CivRuntime.stop_pump``
and ``IcomCommander.stop`` each cancel a task they own and then ``await`` it,
catching ``asyncio.CancelledError`` around that await. If the *stopper's own
task* is itself cancelled from outside while parked on that same await (the
window opened by ``_IcomSerialRadioBase._stop_civ_data_watchdog`` cancelling
the watchdog task while it is inside ``soft_reconnect``), the bare
``except CancelledError: pass`` cannot distinguish "the task I just cancelled
finished" from "someone cancelled me" and swallows both.

These tests exercise ``CivRuntime.stop_pump`` and ``IcomCommander.stop``
directly (not through a radio): the natural race window is one-shot and
load-dependent, so a test that waits for it to occur naturally is a flake
generator. Instead, each test builds the exact interleaving by hand.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import SimpleNamespace

import pytest

from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.commands.commander import IcomCommander
from rigplane.runtime._civ_rx import CivRuntime


async def _two_ticks() -> None:
    """Yield exactly two event-loop turns via a bare ``Event.wait()``.

    Measured on Python 3.11.13 (this repo's floor, and the only version
    ``quick.yml`` runs) with a self-rescheduling ``call_soon`` callback as
    the turn counter (it re-queues itself, so it fires exactly once per
    ``_run_once()``): ``asyncio.sleep(0)`` yields 1 turn, this helper
    yields 2, ``asyncio.wait_for(Event.wait(), ...)`` yields 3.

    Confirmed by substitution on the tests below, same interpreter:
    swapping this body for ``asyncio.sleep(0)`` (1 turn, too few) or for
    ``asyncio.wait_for(tick.wait(), ...)`` (3 turns, too many) reddens both
    ``test_stop_pump_...`` and ``test_commander_stop_...`` with
    ``DID NOT RAISE CancelledError`` (2 failed, 1 passed, either way);
    ``asyncio.sleep(0)`` twice is turn-for-turn equivalent to this helper
    (3 passed).
    """
    tick = asyncio.Event()
    asyncio.get_running_loop().call_soon(tick.set)
    await tick.wait()


async def _swallow_own_cancellation(parked: asyncio.Event) -> None:
    """Stand-in for a ``while True`` loop with its own ``except CancelledError: pass``.

    Mirrors ``_civ_rx_loop`` / ``IcomCommander._loop``: it parks forever and,
    when cancelled, catches its own ``CancelledError`` and returns normally
    rather than propagating it.
    """
    forever = asyncio.Event()
    try:
        parked.set()
        await forever.wait()
    except asyncio.CancelledError:
        pass


class _FakeRequestTracker:
    def fail_all(self, exc: BaseException) -> None:
        del exc


@pytest.mark.asyncio
async def test_stop_pump_reraises_its_own_teardown_cancellation() -> None:
    """CivRuntime.stop_pump must propagate a cancel aimed at its own task.

    Construction: an inner task parks forever and swallows its own
    cancellation. An outer task runs ``stop_pump()`` directly against it —
    one tick after starting, ``stop_pump`` has already cancelled the inner
    task and is parked awaiting it, exactly the window
    ``_stop_civ_data_watchdog`` lands in when it cancels the watchdog task
    (MOR-2081). Cancelling the outer task there must leave it cancelled.
    """
    parked = asyncio.Event()
    inner = asyncio.create_task(_swallow_own_cancellation(parked))
    await parked.wait()

    host = SimpleNamespace(
        _civ_request_tracker=_FakeRequestTracker(), _civ_rx_task=inner
    )
    runtime = CivRuntime(host)

    outer = asyncio.create_task(runtime.stop_pump())
    await _two_ticks()

    outer.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(outer), timeout=0.5)

    assert outer.cancelled() is True


@pytest.mark.asyncio
async def test_commander_stop_reraises_its_own_teardown_cancellation() -> None:
    """IcomCommander.stop must propagate a cancel aimed at its own task.

    Same construction as above, against ``IcomCommander.stop`` directly:
    the queue is set to ``None`` and ``_worker`` is replaced with the fake
    parked task so the drain loop is skipped and only the worker-await shape
    under test runs.
    """
    parked = asyncio.Event()
    worker = asyncio.create_task(_swallow_own_cancellation(parked))
    await parked.wait()

    async def _execute(payload: bytes, wait_response: bool = True) -> None:
        raise AssertionError("execute() must not run in this test")

    commander = IcomCommander(_execute)
    commander._worker = worker
    commander._queue = None

    outer = asyncio.create_task(commander.stop())
    await _two_ticks()

    outer.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(outer), timeout=0.5)

    assert outer.cancelled() is True


@pytest.mark.asyncio
async def test_stop_civ_data_watchdog_bounds_a_stuck_watchdog_task(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Change C (defence in depth): a watchdog task that never finishes must
    not hang teardown forever.

    ``_IcomSerialRadioBase._stop_civ_data_watchdog`` bounds its await with a
    timeout; when the bound is exceeded it logs an ERROR naming the
    watchdog task's state and lets teardown continue (``disconnect()`` must
    complete). The primary fix (the two discriminators above) makes this
    path unreachable for the traced MOR-2081 mechanism; this only proves the
    bound itself, not elapsed time.

    The fake watchdog task below mirrors the traced mechanism precisely: it
    absorbs its *first* cancellation (like the real watchdog's own inner
    ``except CancelledError: pass``) and moves on to a second, ordinary
    sleep rather than terminating. On unfixed code (a bare ``await task``)
    this hangs forever, since nothing ever cancels it a second time.
    ``asyncio.wait`` (not ``asyncio.wait_for``) detects that hang without
    masking it: cancelling the awaiting coroutine itself would just
    delegate into a second cancel of the fake watchdog and race the very
    mechanism under test (same idiom as
    ``tests/test_commander.py: _assert_stop_completes``, PR #2145).
    """
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0")
    radio._SERIAL_CIV_WATCHDOG_TEARDOWN_TIMEOUT_S = 0.05  # type: ignore[attr-defined]

    async def _absorbs_first_cancel_then_hangs() -> None:
        try:
            await asyncio.sleep(1000)
        except asyncio.CancelledError:
            await asyncio.sleep(1000)

    stuck = asyncio.create_task(_absorbs_first_cancel_then_hangs())
    radio._civ_data_watchdog_task = stuck

    try:
        with caplog.at_level(
            logging.ERROR, logger="rigplane.backends._icom_serial_base"
        ):
            stop_task = asyncio.ensure_future(radio._stop_civ_data_watchdog())
            done, _pending = await asyncio.wait({stop_task}, timeout=0.4)
            if stop_task not in done:
                pytest.fail(
                    "_stop_civ_data_watchdog() hung: no bound on the "
                    "watchdog-task await"
                )
            stop_task.result()  # re-raise if it failed for an unexpected reason
    finally:
        for pending_task in (stop_task, stuck):
            if not pending_task.done():
                pending_task.cancel()
        for pending_task in (stop_task, stuck):
            if not pending_task.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await pending_task

    assert radio._civ_data_watchdog_task is None
    error_messages = [
        r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
    ]
    assert any("civ-data-watchdog" in msg for msg in error_messages), error_messages
