"""Controlled time and real-event waits for tests (MOR-3920).

One clock, one wait, for the whole suite. Do not write another per-file clock
class or ``_wait_until`` copy.

* :class:`VirtualClock` is the testkit's :class:`ManualClock`
  (``rigplane_testkit.sim.engine``) plus two ways to put it where the code
  under test reads time:

  - **a ``clock=`` seam** (MOR-3215 and the simulator): pass the clock itself,
    it is a ``Callable[[], float]``; ``clock.advance(dt)`` moves it;
  - **the test's own event loop**, through the ``virtual_clock`` fixture
    (``tests/conftest.py``): ``loop.time`` of THIS loop is the clock, so
    ``asyncio.sleep`` / ``wait_for`` / ``asyncio.timeout`` and every
    ``loop.call_later`` follow it and nothing else does. ``await
    clock.elapse(dt)`` moves time forward timer by timer, letting each woken
    task run to quiescence before the next timer fires;
  - **a module that calls ``time.monotonic()`` / ``asyncio.sleep()`` itself**:
    ``clock.scope_to(monkeypatch, module)`` swaps that module's own ``time``
    and ``asyncio`` names for views whose ``monotonic`` reads the clock and
    whose ``sleep(d)`` jumps the clock by ``d`` and yields once.

  Nothing global is touched: ``asyncio.sleep``, ``asyncio.wait``,
  ``time.monotonic`` stay the real functions and every other loop or thread
  keeps real time (``tests/test_timing_helpers.py`` pins it). Never patch them
  process-wide.
* :func:`wait_until` / :func:`await_until` wait for a REAL event (child
  process, socket, worker thread) against the real clock with one bound. They
  also work on a loop that carries a virtual clock (the virtual clock does not
  move while they wait).

Real I/O on a virtual loop: ``elapse`` settles the loop's own callbacks, not
bytes still in the kernel. Wait for the event that proves a socket round trip
happened (``await_until``, an ``asyncio.Event``) before advancing time past
the timer that depends on it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, TypeVar

import pytest

from rigplane_testkit.sim.engine import ManualClock

T = TypeVar("T")

# How many loop turns one settle may take before the loop counts as busy.
_MAX_SETTLE_TURNS = 10_000


class VirtualClock(ManualClock):
    """A :class:`ManualClock` that can also drive an event loop's timers."""

    def __init__(self, start: float = 0.0) -> None:
        super().__init__(start)
        self.sleeps: list[float] = []

    # -- the test's own loop -------------------------------------------------

    def install(self, loop: asyncio.AbstractEventLoop) -> None:
        """Make this clock the ``loop.time`` of ``loop`` (that loop only)."""
        loop.time = self  # type: ignore[method-assign]

    def uninstall(self, loop: asyncio.AbstractEventLoop) -> None:
        """Give ``loop`` its own clock back."""
        loop.__dict__.pop("time", None)

    async def elapse(self, delta: float) -> None:
        """Move time forward by ``delta``, firing every timer on the way."""
        if delta < 0:
            raise ValueError("a virtual clock cannot run backwards")
        loop = asyncio.get_running_loop()
        if loop.time is not self:
            raise RuntimeError("elapse() needs the loop this clock is installed on")
        target = self() + delta
        await _settle(loop)
        while (next_timer := _next_timer(loop)) is not None and next_timer <= target:
            self.advance(max(0.0, next_timer - self()))
            await _settle(loop)
        self.advance(target - self())
        await _settle(loop)

    # -- a module that reads time itself -------------------------------------

    async def sleep(self, delay: float) -> None:
        """Stand-in for ``asyncio.sleep`` under :meth:`scope_to`.

        Records ``delay`` in :attr:`sleeps`, jumps the clock by it, and yields
        once so parked tasks wake without waiting the jump out in real time.
        """
        self.sleeps.append(delay)
        self.advance(max(0.0, delay))
        await asyncio.sleep(0)

    def scope_to(self, monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
        """Point ``module``'s own ``time`` / ``asyncio`` names at this clock.

        Only the names inside ``module`` are replaced (by views that forward
        everything else to the real module); the process-wide ``time`` and
        ``asyncio`` modules are never modified.
        """
        clock = self

        class _TimeView:
            monotonic = staticmethod(clock)

            def __getattr__(self, name: str) -> Any:
                return getattr(time, name)

        class _AsyncioView:
            sleep = staticmethod(clock.sleep)

            def __getattr__(self, name: str) -> Any:
                return getattr(asyncio, name)

        monkeypatch.setattr(module, "time", _TimeView())
        monkeypatch.setattr(module, "asyncio", _AsyncioView())


def _next_timer(loop: asyncio.AbstractEventLoop) -> float | None:
    """When the earliest live timer of ``loop`` is due (CPython: ``loop._scheduled``)."""
    due = [h.when() for h in loop._scheduled if not h.cancelled()]  # type: ignore[attr-defined]
    return min(due) if due else None


async def _settle(loop: asyncio.AbstractEventLoop) -> None:
    """Yield until no callback is left ready (CPython: ``loop._ready``)."""
    for _ in range(_MAX_SETTLE_TURNS):
        await asyncio.sleep(0)
        if not loop._ready:  # type: ignore[attr-defined]
            return
    raise AssertionError(
        "the event loop never went quiet; a task is spinning on sleep(0)"
    )


def wait_until(
    condition: Callable[[], T],
    timeout: float = 5.0,
    *,
    interval: float = 0.01,
    description: str = "the condition",
) -> T:
    """Block until ``condition()`` is truthy and return its value (REAL time).

    For an event that really happens elsewhere (child process, socket, worker
    thread). Fails with an ``AssertionError`` naming ``description`` when the
    ``timeout`` runs out. Give the timeout generously: it is a bound for a
    stuck run, not the expected wait.
    """
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"timed out after {timeout}s waiting for {description}"
            )
        time.sleep(interval)


async def await_until(
    condition: Callable[[], T],
    timeout: float = 5.0,
    *,
    interval: float = 0.01,
    description: str = "the condition",
) -> T:
    """:func:`wait_until` for async tests: polls on the loop, in REAL time.

    On a loop that carries a :class:`VirtualClock` the pause is a short
    blocking sleep plus a bare loop turn (so thread and socket callbacks still
    run); the virtual clock does not move.
    """
    virtual = isinstance(asyncio.get_running_loop().time, VirtualClock)
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"timed out after {timeout}s waiting for {description}"
            )
        if virtual:
            time.sleep(interval)
            await asyncio.sleep(0)
        else:
            await asyncio.sleep(interval)
