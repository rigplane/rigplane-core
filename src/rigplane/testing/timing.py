"""Controlled time and real-event waits for tests (MOR-3920, MOR-4075).

One clock, one wait, for the whole suite and for downstream projects (rigplane-pro
imports this module). Do not write another per-file clock class or ``_wait_until``
copy.

This module SHIPS in the ``rigplane`` wheel so a downstream test suite can import it
through its ordinary ``rigplane`` dependency (the repository's ``rigplane_testkit`` does
not ship). It is pure standard-library asyncio with no runtime dependency, and nothing on
the runtime path imports it: ``import rigplane`` must not load ``rigplane.testing``.

* :class:`VirtualClock` is a :class:`ManualClock` (the simulator engine's clock too) plus three ways to put it where the code
  under test reads time:

  - **a ``clock=`` seam**: pass the clock itself, it is a ``Callable[[], float]``;
    ``clock.advance(dt)`` moves it;
  - **the test's own event loop**: ``loop.time`` of THIS loop is the clock, so
    ``asyncio.sleep`` / ``wait_for`` / ``asyncio.timeout`` and every
    ``loop.call_later`` follow it and nothing else does. ``await clock.elapse(dt)``
    moves time forward timer by timer, letting each woken task run to quiescence
    before the next timer fires. Three entry points, one behaviour:

    * :meth:`VirtualClock.on_running_loop`, which a ``virtual_clock`` fixture wraps
      (a three-line fixture in the suite's ``conftest.py``), for an ``async def``
      test run by the suite's asyncio plugin;
    * the :func:`virtual_clock` decorator, for an ``async def test_x(clock)`` that
      should run on its own fresh loop (``asyncio.run`` style suites);
    * :meth:`VirtualClock.run`, which the decorator uses;

  - **a module that calls ``time.monotonic()`` / ``asyncio.sleep()`` itself**:
    ``clock.scope_to(monkeypatch, module)`` swaps that module's own ``time`` and
    ``asyncio`` names for views whose ``monotonic`` reads the clock and whose
    ``sleep(d)`` jumps the clock by ``d`` and yields once.

  Nothing global is touched: ``asyncio.sleep``, ``asyncio.wait``, ``time.monotonic``
  stay the real functions and every other loop or thread keeps real time
  (``tests/testkit/test_timing.py`` pins it). Never patch them process-wide.
* :func:`wait_until` / :func:`await_until` wait for a REAL event (child process,
  socket, worker thread) against the real clock with one bound. They also work on a
  loop that carries a virtual clock (the virtual clock does not move while they wait).

Inside a :func:`virtual_clock` test, ``run_in_executor`` / ``asyncio.to_thread`` run
inline on the loop thread, so a thread hop cannot make the order depend on
scheduling (switch it off with ``inline_threads=False``).

Real I/O on a virtual loop: ``elapse`` settles the loop's own callbacks, not bytes
still in the kernel. Wait for the event that proves a socket round trip happened
(``await_until``, an ``asyncio.Event``) before advancing time past the timer that
depends on it.

Example::

    @virtual_clock
    async def test_gives_up_after_five_seconds(clock):
        task = asyncio.ensure_future(thing_that_waits_five_seconds())
        await clock.elapse(4.9)
        assert not task.done()
        await clock.elapse(0.2)
        assert task.done()
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import threading
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from math import isfinite
from typing import (
    TYPE_CHECKING,
    Any,
    Concatenate,
    ParamSpec,
    Protocol,
    TypeVar,
    overload,
)

if TYPE_CHECKING:
    import pytest

__all__ = [
    "Clock",
    "ManualClock",
    "VirtualClock",
    "await_until",
    "virtual_clock",
    "wait_until",
]

T = TypeVar("T")
P = ParamSpec("P")

# How many loop turns one settle may take before the loop counts as busy.
_MAX_SETTLE_TURNS = 10_000
_DEFAULT_REAL_TIMEOUT_S = 30.0


class Clock(Protocol):
    def __call__(self) -> float: ...


@dataclass
class ManualClock:
    """Deterministic test clock; ``advance(dt)`` moves and returns time."""

    start: float = 0.0

    def __post_init__(self) -> None:
        start = float(self.start)
        if not isfinite(start):
            raise ValueError(f"ManualClock start must be finite: {start}")
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, dt: float) -> float:
        step = float(dt)
        if not isfinite(step) or step < 0:
            raise ValueError(f"advance dt must be finite and non-negative: {step}")
        moved = self._now + step
        if not isfinite(moved):
            raise ValueError(f"advance to non-finite time: {self._now} + {step}")
        self._now = moved
        return self._now


class _InlineExecutor(ThreadPoolExecutor):
    """Executor that runs each job at once on the caller's thread."""

    def submit(  # type: ignore[override]
        self, fn: Callable[..., T], /, *args: Any, **kwargs: Any
    ) -> Future[T]:
        future: Future[T] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # same contract as a real executor
            future.set_exception(exc)
        return future


class VirtualClock(ManualClock):
    """A :class:`ManualClock` that can also drive an event loop's timers."""

    def __init__(self, start: float = 0.0) -> None:
        super().__init__(start)
        self.sleeps: list[float] = []

    @property
    def now(self) -> float:
        """The current virtual time (same as calling the clock)."""
        return self()

    # -- the test's own loop -------------------------------------------------

    @classmethod
    @contextlib.asynccontextmanager
    async def on_running_loop(cls) -> AsyncGenerator[VirtualClock, None]:
        """A clock installed on the loop this coroutine runs on, for the ``with`` block.

        It starts at the loop's current time and gives the loop its own clock back on
        exit. Pair the test with a real-time watchdog (``@pytest.mark.timeout``): a
        test that awaits a timer nobody advances waits forever.
        """
        loop = asyncio.get_running_loop()
        clock = cls(start=loop.time())
        clock.install(loop)
        try:
            yield clock
        finally:
            clock.uninstall(loop)

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

    def run(
        self,
        scenario: Callable[[], Awaitable[T]],
        *,
        inline_threads: bool = True,
        real_timeout_s: float = _DEFAULT_REAL_TIMEOUT_S,
    ) -> T:
        """Run ``scenario`` on a new event loop whose time is this clock.

        ``real_timeout_s`` is a watchdog on REAL time: a scenario that awaits
        something only an ``elapse`` could release would otherwise hang.
        """

        async def main() -> T:
            loop = asyncio.get_running_loop()
            task = asyncio.current_task()
            assert task is not None
            tripped = False

            def stop_scenario() -> None:
                # Give the loop its own, progressing clock before the cancellation: the
                # scenario's cleanup (a `finally` that sleeps) must not wait on a frozen one.
                self.uninstall(loop)
                task.cancel()

            def trip() -> None:
                nonlocal tripped
                tripped = True
                loop.call_soon_threadsafe(stop_scenario)

            watchdog = threading.Timer(real_timeout_s, trip)
            watchdog.daemon = True
            self.install(loop)
            if inline_threads:
                loop.set_default_executor(_InlineExecutor())
            watchdog.start()
            try:
                result = await scenario()
                if tripped:  # the scenario swallowed the watchdog's cancellation
                    raise asyncio.CancelledError
                return result
            except asyncio.CancelledError:
                if tripped:
                    raise AssertionError(
                        f"virtual-clock scenario still waiting after {real_timeout_s}s of "
                        f"real time (virtual now={self.now}); something awaits an event "
                        "that only clock.elapse() or a real event could deliver"
                    ) from None
                raise
            finally:
                watchdog.cancel()
                self.uninstall(loop)

        return asyncio.run(main())

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
        ``asyncio`` modules are never modified. ``monkeypatch`` is pytest's.
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


@overload
def virtual_clock(
    test: Callable[Concatenate[VirtualClock, P], Awaitable[None]],
    *,
    start: float = 0.0,
    inline_threads: bool = True,
    real_timeout_s: float = _DEFAULT_REAL_TIMEOUT_S,
) -> Callable[P, None]: ...


@overload
def virtual_clock(
    test: None = None,
    *,
    start: float = 0.0,
    inline_threads: bool = True,
    real_timeout_s: float = _DEFAULT_REAL_TIMEOUT_S,
) -> Callable[
    [Callable[Concatenate[VirtualClock, P], Awaitable[None]]], Callable[P, None]
]: ...


def virtual_clock(
    test: Callable[..., Awaitable[None]] | None = None,
    *,
    start: float = 0.0,
    inline_threads: bool = True,
    real_timeout_s: float = _DEFAULT_REAL_TIMEOUT_S,
) -> Any:
    """Decorator: run an ``async def test_x(clock, ...)`` on a virtual clock.

    The first parameter must be named ``clock``; the decorator supplies it.
    The remaining parameters stay pytest fixtures / parametrize arguments.
    """

    def decorate(fn: Callable[..., Awaitable[None]]) -> Callable[..., None]:
        signature = inspect.signature(fn)
        params = list(signature.parameters.values())
        if not params or params[0].name != "clock":
            raise TypeError(f"{fn.__name__}: the first parameter must be named 'clock'")

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> None:
            clock = VirtualClock(start)
            clock.run(
                lambda: fn(clock, *args, **kwargs),
                inline_threads=inline_threads,
                real_timeout_s=real_timeout_s,
            )

        wrapper.__signature__ = signature.replace(parameters=params[1:])  # type: ignore[attr-defined]
        return wrapper

    return decorate if test is None else decorate(test)


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

    On a loop that carries a :class:`VirtualClock` the pause is a short blocking
    sleep plus a bare loop turn (so thread and socket callbacks still run); the
    virtual clock does not move.
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
