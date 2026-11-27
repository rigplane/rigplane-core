"""``rigplane.testing.timing``: the one virtual clock and real-event waits (MOR-3920, MOR-4075)."""

from __future__ import annotations

import asyncio
import math
import threading
import time
import types
from unittest.mock import Mock

import pytest

from rigplane_testkit.sim.engine import ManualClock
from rigplane.testing.timing import VirtualClock, await_until, wait_until
from rigplane.testing.timing import virtual_clock as clocked
from rigplane.testing import timing


def _globals() -> tuple[object, ...]:
    return (asyncio.sleep, asyncio.wait, asyncio.wait_for, time.monotonic, time.sleep)


def test_it_is_the_testkit_manual_clock() -> None:
    clock = VirtualClock(start=5.0)
    assert isinstance(clock, ManualClock)
    assert clock() == 5.0
    assert clock.advance(2.5) == 7.5


@pytest.mark.realtime(
    reason="compares the virtual clock with the real one to prove it is not"
)
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


@pytest.mark.realtime(
    reason="reads the real clock to prove other threads keep real time"
)
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


@pytest.mark.realtime(reason="reads the real clock to prove the loop got its own back")
async def test_the_loop_gets_its_own_clock_back_after_the_fixture() -> None:
    loop = asyncio.get_running_loop()
    clock = VirtualClock(start=loop.time())
    clock.install(loop)
    assert loop.time is clock
    clock.uninstall(loop)
    assert "time" not in vars(loop)
    assert abs(loop.time() - time.monotonic()) < 60


@pytest.mark.timeout(30)
async def test_install_swaps_the_loop_timer_resolution_and_uninstall_restores_it() -> (
    None
):
    """MOR-3938: the virtual time comes with a virtual timer resolution.

    ``_run_once`` fires a timer while its deadline is inside ``time() +
    _clock_resolution``, so the install must not leave the host clock's
    resolution (Windows: 1/64 s) on the loop, and the uninstall must put the
    real one back.
    """
    loop = asyncio.get_running_loop()
    real_resolution = loop._clock_resolution  # type: ignore[attr-defined]
    clock = VirtualClock(start=0.0)
    clock.install(loop)
    try:
        assert 0 < loop._clock_resolution <= 1e-6  # type: ignore[attr-defined]
    finally:
        clock.uninstall(loop)
    assert loop._clock_resolution == real_resolution  # type: ignore[attr-defined]


@pytest.mark.timeout(30)
async def test_an_exactly_due_timer_completes_at_a_large_clock_offset() -> None:
    """MOR-3938 review follow-up: the resolution must stay representable.

    ``on_running_loop()`` starts at the host monotonic offset, which on a
    long-running host sits at ``2**24`` seconds and beyond. There the float
    ulp (~3.7 ns) exceeds ``1e-9``: ``time() + _clock_resolution`` rounded
    back to ``time()``, so an exactly-due timer stayed scheduled and
    ``elapse()`` kept advancing by zero until the real-time watchdog
    tripped."""
    loop = asyncio.get_running_loop()
    clock = VirtualClock(start=2**24)
    clock.install(loop)
    try:
        assert loop._clock_resolution >= 2 * math.ulp(2**24)  # type: ignore[attr-defined]
        done = asyncio.Event()
        loop.call_later(0.0, done.set)
        await clock.elapse(0.0)
        assert done.is_set()
    finally:
        clock.uninstall(loop)


@pytest.mark.timeout(30)
async def test_a_coarse_host_resolution_does_not_fire_virtual_timers_early() -> None:
    """MOR-3938 regression: a Windows-scale host resolution must not leak
    into virtual time. With the real resolution left on the loop, a settle
    after ``elapse(0.005)`` fires a 10 ms ``wait_for`` inside the coarse
    window; with the virtual resolution it waits for its own deadline."""
    loop = asyncio.get_running_loop()
    real_resolution = loop._clock_resolution  # type: ignore[attr-defined]
    loop._clock_resolution = 0.01  # type: ignore[attr-defined]
    clock = VirtualClock(start=0.0)
    clock.install(loop)
    try:
        waiter = asyncio.ensure_future(
            asyncio.wait_for(asyncio.Event().wait(), timeout=0.01)
        )
        await clock.elapse(0.005)
        assert not waiter.done()
        await clock.elapse(0.006)
        assert waiter.done()
    finally:
        clock.uninstall(loop)
        loop._clock_resolution = real_resolution  # type: ignore[attr-defined]


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
async def test_await_until_advances_only_the_installed_clock(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = virtual_clock()
    wall = Mock(side_effect=AssertionError("virtual wait used wall time"))
    monkeypatch.setattr(
        timing, "time", types.SimpleNamespace(monotonic=wall, sleep=wall)
    )
    delivered: list[int] = []
    asyncio.get_running_loop().call_later(0.25, delivered.append, 42)
    assert await await_until(lambda: delivered and delivered[0], interval=0.125) == 42
    assert virtual_clock() - started == pytest.approx(0.25)
    wall.assert_not_called()


@pytest.mark.parametrize("synchronous", [False, True], ids=["async", "sync"])
@pytest.mark.parametrize("interval", [0.125, 1.0])
async def test_virtual_wait_deadline_uses_no_wall_time(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    synchronous: bool,
    interval: float,
) -> None:
    started = virtual_clock()
    wall = Mock(side_effect=AssertionError("virtual wait used wall time"))
    monkeypatch.setattr(
        timing, "time", types.SimpleNamespace(monotonic=wall, sleep=wall)
    )
    with pytest.raises(AssertionError, match="waiting for a missing event"):
        if synchronous:
            wait_until(
                lambda: False,
                timeout=0.25,
                interval=interval,
                description="a missing event",
            )
        else:
            await await_until(
                lambda: False,
                timeout=0.25,
                interval=interval,
                description="a missing event",
            )
    assert virtual_clock() - started == pytest.approx(0.25)
    wall.assert_not_called()


async def test_wait_until_reads_the_installed_clock(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = virtual_clock()
    wall = Mock(side_effect=AssertionError("virtual wait used wall time"))
    monkeypatch.setattr(
        timing, "time", types.SimpleNamespace(monotonic=wall, sleep=wall)
    )
    assert (
        wait_until(
            lambda: 42 if virtual_clock() >= started + 0.25 else 0, interval=0.125
        )
        == 42
    )
    assert virtual_clock() - started == pytest.approx(0.25)
    wall.assert_not_called()


async def test_await_until_real_loop_timeout_keeps_wall_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wall = Mock(wraps=time.monotonic)
    monkeypatch.setattr(timing, "time", types.SimpleNamespace(monotonic=wall))
    with pytest.raises(AssertionError, match="waiting for a missing event"):
        await await_until(lambda: False, timeout=0.01, description="a missing event")
    assert wall.call_count >= 2


@pytest.mark.parametrize("interval", [0.0, -0.01, math.nan, math.inf])
@pytest.mark.parametrize("synchronous", [False, True], ids=["async", "sync"])
async def test_virtual_wait_rejects_intervals_that_cannot_advance_its_deadline(
    virtual_clock: VirtualClock,
    interval: float,
    synchronous: bool,
) -> None:
    started = virtual_clock()
    with pytest.raises(ValueError, match="finite and positive"):
        if synchronous:
            wait_until(lambda: False, interval=interval)
        else:
            await await_until(lambda: False, interval=interval)
    assert virtual_clock() == started


# -- the decorator entry point (rigplane-pro style, one fresh loop per test) ------------


@clocked
async def test_decorator_runs_the_test_on_a_virtual_loop(clock: VirtualClock) -> None:
    assert asyncio.get_running_loop().time is clock
    task = asyncio.ensure_future(asyncio.sleep(3600.0))
    await clock.elapse(3599.9)
    assert not task.done()
    await clock.elapse(0.2)
    assert task.done()
    assert clock.now == pytest.approx(3600.1)


@clocked
async def test_to_thread_runs_inline_so_thread_hops_cannot_reorder(
    clock: VirtualClock,
) -> None:
    thread_names: list[str] = []

    def work() -> int:
        thread_names.append(threading.current_thread().name)
        return 7

    assert await asyncio.to_thread(work) == 7
    assert thread_names == [threading.current_thread().name]


def test_the_global_functions_are_restored_after_a_run() -> None:
    before = _globals()
    clock = VirtualClock()
    loops: list[asyncio.AbstractEventLoop] = []

    async def scenario() -> None:
        loops.append(asyncio.get_running_loop())
        await clock.elapse(1.0)

    clock.run(scenario)
    assert _globals() == before
    assert "time" not in vars(loops[0])  # the loop's own clock was put back


@pytest.mark.parametrize("period", [0.5, 2.0])
@clocked
async def test_decorator_keeps_pytest_parametrize_and_fixtures(
    clock: VirtualClock, period: float, tmp_path: object
) -> None:
    assert tmp_path is not None
    task = asyncio.ensure_future(asyncio.sleep(period))
    await clock.elapse(period)
    assert task.done()


def test_a_scenario_that_waits_for_the_impossible_fails_instead_of_hanging() -> None:
    clock = VirtualClock()

    async def scenario() -> None:
        await asyncio.Event().wait()

    with pytest.raises(AssertionError, match="still waiting"):
        clock.run(scenario, real_timeout_s=0.2)


def test_a_scenario_that_swallows_the_watchdog_still_fails() -> None:
    clock = VirtualClock()

    async def scenario() -> str:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return "swallowed"  # e.g. a reconnect loop that absorbs cancellation
        return "never"

    with pytest.raises(AssertionError, match="still waiting"):
        clock.run(scenario, real_timeout_s=0.2)


def test_the_decorator_hands_the_clock_in_and_keeps_the_other_parameters_typed() -> (
    None
):
    seen: list[float] = []

    @clocked
    async def scenario(clock: VirtualClock, period: float) -> None:
        await clock.elapse(period)
        seen.append(clock.now)

    scenario(2.5)  # type-checks only because the clock parameter was stripped
    assert seen == [2.5]


@pytest.mark.realtime(reason="after the watchdog fires the loop runs on the real clock")
@pytest.mark.timeout(30)
def test_cleanup_that_sleeps_after_the_watchdog_fired_still_completes() -> None:
    clock = VirtualClock()
    cleaned: list[bool] = []

    async def scenario() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(
                0.01
            )  # the virtual clock is frozen; cleanup must still end
            cleaned.append(True)

    with pytest.raises(AssertionError, match="still waiting"):
        clock.run(scenario, real_timeout_s=0.2)
    assert cleaned == [True]


def test_the_first_parameter_must_be_the_clock() -> None:
    with pytest.raises(TypeError, match="first parameter"):

        @clocked
        async def scenario(other: object) -> None:  # pragma: no cover - never runs
            pass
