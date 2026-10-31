"""The shared timing helpers in ``tests/_timing.py`` (MOR-3920)."""

from __future__ import annotations

import asyncio
import threading
import time
import types

import pytest

from _timing import VirtualClock, await_until, wait_until
from rigplane_testkit.sim.engine import ManualClock


def _globals() -> tuple[object, ...]:
    return (asyncio.sleep, asyncio.wait, asyncio.wait_for, time.monotonic, time.sleep)


def test_it_is_the_testkit_manual_clock() -> None:
    clock = VirtualClock(start=5.0)
    assert isinstance(clock, ManualClock)
    assert clock() == 5.0
    assert clock.advance(2.5) == 7.5


@pytest.mark.timeout(30)
async def test_time_moves_only_when_the_test_elapses_it(
    virtual_clock: VirtualClock,
) -> None:
    started = virtual_clock()
    woke: list[float] = []

    async def sleeper() -> None:
        await asyncio.sleep(3600.0)
        woke.append(virtual_clock() - started)

    task = asyncio.ensure_future(sleeper())
    for _ in range(50):
        await asyncio.sleep(0)
    assert virtual_clock() == started and not woke  # yielding is not time passing

    wall = time.monotonic()
    await virtual_clock.elapse(3599.9)
    assert not woke
    await virtual_clock.elapse(0.2)
    assert woke == [pytest.approx(3600.0)]
    await task
    assert time.monotonic() - wall < 5.0  # an hour of virtual time, no real hour


@pytest.mark.timeout(30)
async def test_timers_fire_in_order_and_chained_timers_fire_inside_one_elapse(
    virtual_clock: VirtualClock,
) -> None:
    started = virtual_clock()
    seen: list[tuple[str, float]] = []

    async def ticker(name: str, period: float, count: int) -> None:
        for _ in range(count):
            await asyncio.sleep(period)
            seen.append((name, round(virtual_clock() - started, 6)))

    tasks = [
        asyncio.ensure_future(ticker("a", 1.0, 3)),
        asyncio.ensure_future(ticker("b", 1.7, 2)),
    ]
    await virtual_clock.elapse(10.0)
    assert seen == [("a", 1.0), ("b", 1.7), ("a", 2.0), ("a", 3.0), ("b", 3.4)]
    await asyncio.gather(*tasks)


@pytest.mark.timeout(30)
async def test_wait_for_times_out_at_the_virtual_deadline(
    virtual_clock: VirtualClock,
) -> None:
    waiter = asyncio.ensure_future(
        asyncio.wait_for(asyncio.Event().wait(), timeout=2.0)
    )
    await virtual_clock.elapse(1.99)
    assert not waiter.done()
    await virtual_clock.elapse(0.02)
    assert waiter.done()
    with pytest.raises(TimeoutError):
        await waiter


@pytest.mark.timeout(30)
async def test_the_global_asyncio_and_time_functions_stay_untouched(
    virtual_clock: VirtualClock,
) -> None:
    """The clock lives on this test's loop only; nothing process-wide is patched."""
    real = _globals()
    assert asyncio.get_running_loop().time is virtual_clock
    sibling: dict[str, object] = {}

    def other_thread() -> None:
        async def probe() -> None:
            sibling["time"] = asyncio.get_running_loop().time
            sibling["now"] = asyncio.get_running_loop().time()
            await asyncio.sleep(0.01)  # a real sleep: nobody elapses this loop

        asyncio.run(probe())

    await virtual_clock.elapse(5.0)
    assert _globals() == real
    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    # A loop in another thread keeps the real clock and the real sleep.
    assert sibling["time"] is not virtual_clock
    assert abs(float(sibling["now"]) - time.monotonic()) < 60  # type: ignore[arg-type]
    assert _globals() == real


async def test_the_loop_gets_its_own_clock_back_after_the_fixture() -> None:
    loop = asyncio.get_running_loop()
    clock = VirtualClock(start=loop.time())
    clock.install(loop)
    assert loop.time is clock
    clock.uninstall(loop)
    assert "time" not in vars(loop)
    assert abs(loop.time() - time.monotonic()) < 60


def test_scope_to_changes_only_the_named_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("fake_runtime_module")
    module.time = time  # type: ignore[attr-defined]
    module.asyncio = asyncio  # type: ignore[attr-defined]
    clock = VirtualClock(start=10.0)
    real = _globals()

    clock.scope_to(monkeypatch, module)

    assert module.time.monotonic() == 10.0  # type: ignore[attr-defined]
    assert module.time.perf_counter is time.perf_counter  # type: ignore[attr-defined]
    assert module.asyncio.wait_for is asyncio.wait_for  # type: ignore[attr-defined]
    assert _globals() == real  # the real modules were never touched

    async def run() -> None:
        await module.asyncio.sleep(0.25)  # type: ignore[attr-defined]
        await module.asyncio.sleep(0.5)  # type: ignore[attr-defined]

    asyncio.run(run())
    assert clock() == 10.75
    assert clock.sleeps == [0.25, 0.5]
    monkeypatch.undo()
    assert module.time is time and module.asyncio is asyncio  # type: ignore[attr-defined]


async def test_elapse_outside_an_installed_loop_is_refused() -> None:
    with pytest.raises(RuntimeError, match="installed"):
        await VirtualClock().elapse(1.0)


def test_wait_until_returns_the_value_once_a_real_thread_delivers_it() -> None:
    box: list[int] = []
    threading.Timer(0.05, lambda: box.append(41)).start()
    assert wait_until(lambda: box and box[0] + 1, timeout=10.0) == 42


def test_wait_until_names_what_it_waited_for_when_the_bound_runs_out() -> None:
    with pytest.raises(AssertionError, match="the radio to answer"):
        wait_until(lambda: False, timeout=0.05, description="the radio to answer")


async def test_await_until_polls_real_time_on_a_real_loop() -> None:
    box: list[int] = []
    threading.Timer(0.05, lambda: box.append(1)).start()
    assert await await_until(lambda: box and box[0], timeout=10.0) == 1


@pytest.mark.timeout(30)
async def test_await_until_waits_for_a_real_thread_without_moving_the_virtual_clock(
    virtual_clock: VirtualClock,
) -> None:
    started = virtual_clock()
    box: list[int] = []
    threading.Timer(0.05, lambda: box.append(1)).start()
    assert await await_until(lambda: box and box[0], timeout=10.0) == 1
    assert virtual_clock() == started
