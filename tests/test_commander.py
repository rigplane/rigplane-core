from __future__ import annotations

import asyncio
import contextlib

import pytest

from rigplane.commander import IcomCommander, Priority
from rigplane.exceptions import ConnectionError
from rigplane.types import CivFrame


class _HeldExecute:
    def __init__(self, *, fail: bool = False) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.seen: list[bytes] = []
        self.task: asyncio.Task[CivFrame | None] | None = None
        self.fail = fail

    async def __call__(self, payload: bytes, wait_response: bool) -> CivFrame | None:
        self.seen.append(payload)
        self.task = asyncio.current_task()
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
        if self.fail:
            raise RuntimeError("late execute failure")
        return None


async def _stop_and_check_actual_join(
    commander: IcomCommander,
    worker: asyncio.Task[None],
    execute_task: asyncio.Task[CivFrame | None],
) -> None:
    await commander.stop()
    assert worker.done() and execute_task.done(), (
        "successful stop did not join captured worker and execute"
    )


async def _cleanup_held_commander(
    commander: IcomCommander,
    execute: _HeldExecute,
    worker: asyncio.Task[None],
    tasks: list[asyncio.Task],
) -> None:
    execute.release.set()
    captured = [*tasks, worker]
    if execute.task is not None:
        captured.append(execute.task)
    # Cleanup may issue another cancellation; no verdict observes this phase.
    for _ in range(2):
        for task in captured:
            if not task.done():
                task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    await asyncio.gather(*captured, return_exceptions=True)
    await commander.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True], ids=["returns", "raises"])
async def test_stop_joins_cancel_resistant_execute_before_worker_exit(
    failure: bool,
) -> None:
    execute = _HeldExecute(fail=failure)
    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    worker = c._worker
    assert worker is not None
    send = asyncio.create_task(c.send(b"held"))
    tasks = [send]
    try:
        await execute.started.wait()
        execute_task = execute.task
        assert execute_task is not None
        queued = asyncio.create_task(c.send(b"queued"))
        tasks.append(queued)
        await asyncio.sleep(0)
        stop = asyncio.create_task(_stop_and_check_actual_join(c, worker, execute_task))
        tasks.append(stop)
        await execute.cancelled.wait()
        done, _ = await asyncio.wait({stop, worker}, timeout=0.05)
        if stop.done() and not stop.cancelled():
            stop.result()
        assert not execute_task.done(), "held execute became terminal before release"
        assert not done, "stop returned before actual execute completed"
        execute.release.set()
        _, pending = await asyncio.wait({stop, worker, send, queued}, timeout=1)
        assert not pending, "cancel-resistant execute left stopping worker parked"
        stop.result()
        assert worker.done() and execute_task.done()
        assert execute.seen == [b"held"], "stopped queue dispatched a later command"
        assert send.exception() is not None, "stopped in-flight send reported success"
        assert isinstance(queued.exception(), ConnectionError)
        assert c._worker is None
        assert c._queue is None
    finally:
        await _cleanup_held_commander(c, execute, worker, tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cancel_first", [False, True], ids=["concurrent", "cancel-waiter"]
)
async def test_overlapping_stop_keeps_actual_worker_until_execute_unwinds(
    cancel_first: bool,
) -> None:
    execute = _HeldExecute()
    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    worker = c._worker
    assert worker is not None
    send = asyncio.create_task(c.send(b"held"))
    tasks = [send]
    try:
        await execute.started.wait()
        execute_task = execute.task
        assert execute_task is not None
        first = asyncio.create_task(
            _stop_and_check_actual_join(c, worker, execute_task)
        )
        tasks.append(first)
        await execute.cancelled.wait()
        if cancel_first:
            worker_cancels = worker.cancelling()
            execute_cancels = execute_task.cancelling()
            first.cancel()
            await asyncio.wait({first}, timeout=0.05)
            if first.done() and not first.cancelled():
                first.result()
            assert worker.cancelling() == worker_cancels, (
                "cancelled stop waiter added a worker cancellation request"
            )
            assert execute_task.cancelling() == execute_cancels, (
                "cancelled stop waiter added an execute cancellation request"
            )
            assert not worker.done(), "cancelled stop waiter terminated actual worker"
            assert not execute_task.done(), "cancelled stop waiter terminated execute"
            assert c._worker is worker, "cancelled stop waiter lost worker handle"
        second = asyncio.create_task(
            _stop_and_check_actual_join(c, worker, execute_task)
        )
        tasks.append(second)
        done, _ = await asyncio.wait({second, worker}, timeout=0.05)
        if second.done() and not second.cancelled():
            second.result()
        assert not execute_task.done(), "held execute became terminal before release"
        assert not done, "overlapping stop did not join the held execute"
        execute.release.set()
        _, pending = await asyncio.wait(set(tasks) | {worker}, timeout=1)
        assert not pending, "overlapping stops did not settle after execute"
        if not first.cancelled():
            first.result()
        second.result()
        assert worker.done() and execute_task.done()
        assert execute.seen == [b"held"]
        assert c._worker is None
        assert c._queue is None
        await c.stop()
    finally:
        await _cleanup_held_commander(c, execute, worker, tasks)


@pytest.mark.asyncio
async def test_stop_accepts_terminal_execute_without_release() -> None:
    execute = _HeldExecute()
    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    worker = c._worker
    assert worker is not None
    send = asyncio.create_task(c.send(b"held"))
    tasks = [send]
    try:
        await execute.started.wait()
        execute_task = execute.task
        assert execute_task is not None
        worker.cancel()
        await execute.cancelled.wait()
        worker.cancel()
        _, pending = await asyncio.wait({worker, execute_task, send}, timeout=1)
        assert not pending, "explicit second cancellation did not finish captured tasks"
        assert worker.result() is None
        assert execute_task.cancelled()
        assert isinstance(send.exception(), ConnectionError)
        assert not execute.release.is_set()
        await _stop_and_check_actual_join(c, worker, execute_task)
        assert c._worker is None
        assert c._queue is None
    finally:
        await _cleanup_held_commander(c, execute, worker, tasks)


@pytest.mark.asyncio
async def test_old_stop_preserves_restart_from_worker_done_callback() -> None:
    seen = []

    async def execute(payload: bytes, wait_response: bool) -> None:
        seen.append(payload)

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    worker, queue = c._worker, c._queue
    assert worker is not None
    replacements = []
    tasks = []

    def restart(_worker: asyncio.Task[None]) -> None:
        c.start()
        replacements.append((c._worker, c._queue))

    # Registered before stop awaits the worker, so restart precedes stop's resume.
    worker.add_done_callback(restart)
    try:
        await asyncio.sleep(0)
        stop = asyncio.create_task(c.stop())
        tasks.append(stop)
        _, pending = await asyncio.wait({stop, worker}, timeout=1)
        assert not pending, "old stop did not finish"
        stop.result()
        replacement, replacement_queue = replacements[0]
        assert replacement is not None and replacement is not worker
        assert replacement_queue is not queue
        assert c._worker is replacement, "old stop erased restarted worker"
        assert c._queue is replacement_queue, "old stop erased restarted queue"
        assert not replacement.done()
        send = asyncio.create_task(c.send(b"current"))
        tasks.append(send)
        _, pending = await asyncio.wait({send}, timeout=1)
        assert not pending, "restarted commander did not execute"
        assert send.result() is None
        assert seen == [b"current"]
    finally:
        captured = [*tasks, worker, *(item[0] for item in replacements)]
        for task in captured:
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in captured if task is not None), return_exceptions=True
        )
        await c.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["stopping", "dead"])
async def test_send_refuses_stopping_or_dead_worker(state: str) -> None:
    execute = _HeldExecute()
    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    worker = c._worker
    assert worker is not None
    tasks = []
    try:
        if state == "stopping":
            tasks.append(asyncio.create_task(c.send(b"held")))
            await execute.started.wait()
            tasks.append(asyncio.create_task(c.stop()))
            await execute.cancelled.wait()
        else:
            await asyncio.sleep(0)
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        late = asyncio.create_task(c.send(b"late", wait_dispatch=False))
        tasks.append(late)
        _, pending = await asyncio.wait({late}, timeout=1)
        assert not pending, "send waited on an unavailable worker"
        result = await asyncio.gather(late, return_exceptions=True)
        assert isinstance(result[0], ConnectionError), (
            "send admitted an unavailable worker"
        )
        assert b"late" not in execute.seen
    finally:
        await _cleanup_held_commander(c, execute, worker, tasks)


@pytest.mark.asyncio
async def test_priority_ordering() -> None:
    order: list[bytes] = []

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        await asyncio.sleep(0)
        order.append(cmd)
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    try:
        t1 = asyncio.create_task(c.send(b"normal-1", priority=Priority.NORMAL))
        t2 = asyncio.create_task(c.send(b"bg-1", priority=Priority.BACKGROUND))
        t3 = asyncio.create_task(c.send(b"immediate-1", priority=Priority.IMMEDIATE))
        await asyncio.gather(t1, t2, t3)
    finally:
        await c.stop()

    assert order == [b"immediate-1", b"normal-1", b"bg-1"]


@pytest.mark.asyncio
async def test_normal_command_preempts_queued_backgrounds() -> None:
    """A NORMAL command enqueued after several BACKGROUND polls must dispatch
    before them (priority preemption, not FIFO).

    This is the queue-level invariant behind MOR-497(i): polls run at
    BACKGROUND so a user command never queues behind a burst of polls.
    """
    order: list[bytes] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        # Block the worker on the very first dispatched item so the rest of
        # the items are all queued together before any of them dispatch.
        if cmd == b"gate":
            started.set()
            await release.wait()
        order.append(cmd)
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    try:
        gate = asyncio.create_task(c.send(b"gate", priority=Priority.NORMAL))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        # Several BACKGROUND polls queue first, then one NORMAL command.
        bgs = [
            asyncio.create_task(
                c.send(f"bg-{i}".encode(), priority=Priority.BACKGROUND)
            )
            for i in range(5)
        ]
        await asyncio.sleep(0.01)  # let backgrounds enqueue
        normal = asyncio.create_task(c.send(b"normal-1", priority=Priority.NORMAL))
        await asyncio.sleep(0.01)  # let the normal enqueue

        release.set()
        await asyncio.gather(gate, normal, *bgs)
    finally:
        await c.stop()

    # gate dispatched first (it was in-flight). The NORMAL command, though
    # enqueued AFTER all five backgrounds, must dispatch before every one of
    # them.
    assert order[0] == b"gate"
    normal_idx = order.index(b"normal-1")
    bg_indices = [order.index(f"bg-{i}".encode()) for i in range(5)]
    assert all(normal_idx < bg_idx for bg_idx in bg_indices)


@pytest.mark.asyncio
async def test_send_wait_dispatch_false_returns_before_dispatch() -> None:
    """MOR-497(ii): a fire-and-forget send (wait_dispatch=False) must return
    immediately even while the worker is parked on a prior in-flight item.

    The poller relies on this so the poll burst does not park the poll loop;
    the response still arrives via the RX path, not the commander future.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    dispatched: list[bytes] = []

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        if cmd == b"gate":
            started.set()
            await release.wait()
        dispatched.append(cmd)
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    try:
        # Park the worker on the gate item.
        gate = asyncio.create_task(c.send(b"gate", priority=Priority.NORMAL))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        # Fire-and-forget background send must return promptly (None), even
        # though the worker is still blocked on `gate` so nothing has
        # actually dispatched the poll yet.
        result = await asyncio.wait_for(
            c.send(
                b"poll",
                priority=Priority.BACKGROUND,
                wait_response=False,
                wait_dispatch=False,
            ),
            timeout=0.5,
        )
        assert result is None
        assert b"poll" not in dispatched  # did NOT wait for dispatch

        release.set()
        await asyncio.gather(gate)
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_wait_dispatch_true_still_awaits_result() -> None:
    """Default path (wait_dispatch=True) still awaits and returns the execute
    result — regression guard that the additive param does not change the
    blocking contract for commands."""
    sentinel = CivFrame(
        to_addr=0xE0, from_addr=0x98, command=0x03, sub=None, data=b"\x01\x02"
    )

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        return sentinel

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    try:
        result = await c.send(b"cmd", priority=Priority.NORMAL)
        assert result is sentinel
        result2 = await c.send(b"cmd", priority=Priority.NORMAL, wait_dispatch=True)
        assert result2 is sentinel
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_background_inflight_cap_bounds_queue() -> None:
    """MOR-497(ii): bounded growth. With the worker gated, enqueuing more than
    ``_MAX_BG_INFLIGHT`` fire-and-forget BACKGROUND sends must drop-newest so
    the commander queue never exceeds the cap (plus the one in-flight item)."""
    from rigplane.commands.commander import _MAX_BG_INFLIGHT

    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        if cmd == b"gate":
            started.set()
            await release.wait()
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    try:
        # Park the worker so nothing drains.
        gate = asyncio.create_task(c.send(b"gate", priority=Priority.NORMAL))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        overflow = 10
        for i in range(_MAX_BG_INFLIGHT + overflow):
            result = await asyncio.wait_for(
                c.send(
                    f"bg-{i}".encode(),
                    priority=Priority.BACKGROUND,
                    wait_response=False,
                    wait_dispatch=False,
                ),
                timeout=0.5,
            )
            assert result is None

        # Queue holds at most the cap (the gate item is already in-flight,
        # popped off the queue, so it is not counted here).
        assert c._queue is not None
        assert c._queue.qsize() <= _MAX_BG_INFLIGHT

        release.set()
        await asyncio.gather(gate)
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_dedupe_with_wait_dispatch_false_registers_and_cleans_key() -> None:
    """A fire-and-forget send with a key still registers in ``_pending_by_key``
    and is cleaned up by the worker — dedupe bookkeeping is unaffected."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        if cmd == b"gate":
            started.set()
            await release.wait()
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    try:
        gate = asyncio.create_task(c.send(b"gate", priority=Priority.NORMAL))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        await c.send(
            b"poll",
            priority=Priority.BACKGROUND,
            key="meter",
            wait_response=False,
            wait_dispatch=False,
        )
        # Key registered while the item is queued/in-flight.
        assert "meter" in c._pending_by_key

        release.set()
        await asyncio.gather(gate)
        # Let the worker drain the background item and run its finally cleanup.
        await asyncio.sleep(0.02)
        assert "meter" not in c._pending_by_key
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_transaction_restores_on_error() -> None:
    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.0)
    c.start()

    calls: list[str] = []

    async def snapshot() -> dict[str, int]:
        calls.append("snapshot")
        return {"x": 1}

    async def restore(state: dict[str, int]) -> None:
        assert state == {"x": 1}
        calls.append("restore")

    async def body() -> None:
        calls.append("body")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await c.transaction(snapshot=snapshot, restore=restore, body=body)

    await c.stop()
    assert calls == ["snapshot", "body", "restore"]


@pytest.mark.asyncio
async def test_min_interval_throttling() -> None:
    times: list[float] = []

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        times.append(asyncio.get_running_loop().time())
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.03)
    c.start()
    try:
        await c.send(b"a")
        await c.send(b"b")
    finally:
        await c.stop()

    assert len(times) == 2
    assert times[1] - times[0] >= 0.02


@pytest.mark.asyncio
async def test_dedupe_returns_existing_future() -> None:
    count = 0

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        nonlocal count
        count += 1
        await asyncio.sleep(0.02)
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    try:
        t1 = asyncio.create_task(
            c.send(b"poll", priority=Priority.BACKGROUND, key="meter", dedupe=True)
        )
        t2 = asyncio.create_task(
            c.send(b"poll", priority=Priority.BACKGROUND, key="meter", dedupe=True)
        )
        await asyncio.gather(t1, t2)
    finally:
        await c.stop()

    assert count == 1


@pytest.mark.asyncio
async def test_stop_fails_pending() -> None:
    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        await asyncio.sleep(0.5)
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    task = asyncio.create_task(c.send(b"long"))
    await asyncio.sleep(0.01)
    await c.stop()
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(task, timeout=0.1)


@pytest.mark.asyncio
async def test_stop_fails_inflight_command() -> None:
    started = asyncio.Event()

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        started.set()
        await asyncio.sleep(10)
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    task = asyncio.create_task(c.send(b"slow"))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    await c.stop()

    with pytest.raises(ConnectionError):
        await asyncio.wait_for(task, timeout=0.2)


@pytest.mark.asyncio
async def test_caller_timeout_cancels_inflight_and_unblocks_queue() -> None:
    """A caller-side timeout must cancel the in-flight CI-V command at the
    worker and let queued items proceed.

    Regression test for #1188: PR #1186 wrapped scope-getter calls with
    ``asyncio.wait_for(getter(), 0.2)``.  When ``wait_for`` fired, only the
    caller future was cancelled — the worker was still ``await``-ing the
    in-flight ``_execute`` for the (dropped) response.  Subsequent items
    were enqueued while the worker was blocked, and their own ``wait_for``
    timers expired before they reached the head of the queue, so the worker
    saw their futures as already cancelled and skipped them.  Effect: a
    single dropped reply caused the rest of ``_fetch_scope_controls()`` to
    be silently dropped.

    Fix: worker runs ``_execute`` as an inner task and cancels it when the
    caller future is cancelled, so the queue keeps draining at full speed.
    """
    seen: list[bytes] = []
    started = asyncio.Event()
    block = asyncio.Event()

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        seen.append(cmd)
        if cmd == b"slow":
            started.set()
            await block.wait()
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    try:
        # First command hangs; caller waits with a tight timeout.
        slow = asyncio.create_task(c.send(b"slow", timeout=0.05))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        # Enqueue 11 fast followers (no per-call timeout).  Without the
        # fix, these get pre-cancelled while the worker is stuck on
        # ``slow`` — only ``b"slow"`` would land in ``seen``.
        fast = [asyncio.create_task(c.send(f"fast-{i}".encode())) for i in range(11)]

        # Slow command must surface as TimeoutError to the caller.
        with pytest.raises(asyncio.TimeoutError):
            await slow

        # All 11 fast followers must complete normally and reach execute().
        await asyncio.wait_for(asyncio.gather(*fast), timeout=2.0)
    finally:
        block.set()  # unblock any leftover slow execute (defensive)
        await c.stop()

    # Worker dispatched all 12 items: the slow one (cancelled in-flight)
    # plus all 11 fast followers.
    assert seen[0] == b"slow"
    assert sorted(seen[1:]) == sorted(f"fast-{i}".encode() for i in range(11))
    assert len(seen) == 12


@pytest.mark.asyncio
async def test_cancelled_queued_request_is_not_executed() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    seen: list[bytes] = []

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        seen.append(cmd)
        if cmd == b"block":
            started.set()
            await release.wait()
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    c = IcomCommander(execute, min_interval=0.0)
    c.start()
    try:
        t1 = asyncio.create_task(c.send(b"block"))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        t2 = asyncio.create_task(c.send(b"abandoned"))
        await asyncio.sleep(0.01)  # let request enqueue
        t2.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t2

        release.set()
        await asyncio.wait_for(t1, timeout=0.5)

        # Worker must skip cancelled queued request.
        await asyncio.sleep(0.05)
        assert seen == [b"block"]
    finally:
        await c.stop()


async def _assert_stop_completes(c: IcomCommander) -> None:
    """Await c.stop() via asyncio.wait, failing loudly if it wedges.

    Deliberately NOT asyncio.wait_for(c.stop(), ...): on the buggy path
    wait_for's timeout cancels the stop task, and that cancel cascades into
    a second worker.cancel() delivered at queue.get() — un-wedging the
    worker and letting wait_for return normally, masking the hang.
    asyncio.wait leaves the pending task untouched, so the wedge is visible.
    """
    stop_task = asyncio.ensure_future(c.stop())
    done, _pending = await asyncio.wait({stop_task}, timeout=2.0)
    if stop_task not in done:
        stop_task.cancel()  # cascade un-wedges the worker for cleanup
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task
        pytest.fail("stop() hung: worker swallowed its own teardown cancel")


@pytest.mark.asyncio
async def test_stop_unblocks_when_caller_cancel_races_teardown() -> None:
    """Worker teardown must win when it races a caller-driven cancel.

    A caller timeout/disconnect cancels item.future and, via the worker's
    done-callback, the in-flight execute task.  If stop() cancels the worker
    while it is still parked on that already-cancelled execute, the worker's
    own CancelledError arrives through the same await — treating it as the
    caller-cancel case (continue) swallows the teardown request, _loop goes
    back to queue.get(), and stop() hangs forever on await self._worker.
    """
    started = asyncio.Event()
    hang = asyncio.Event()

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        started.set()
        await hang.wait()
        return None

    c = IcomCommander(execute, min_interval=0.0)
    c.start()

    send_task = asyncio.create_task(c.send(b"never-answered"))
    await asyncio.wait_for(started.wait(), timeout=1.0)

    # Caller goes away: cancels item.future, which propagates into the
    # in-flight execute task via the worker's done-callback.
    send_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await send_task

    # No yields between here and stop(): the worker must still be parked
    # on the already-cancelled execute when its own cancel arrives.
    await _assert_stop_completes(c)


@pytest.mark.asyncio
async def test_stop_returns_after_send_timeout_without_yield() -> None:
    """Field repro (Python 3.11): timed-out send immediately followed by
    stop() must not wedge the worker.

    3.11's wait_for unwinds the timed-out caller in fewer loop ticks than
    3.12+, leaving the worker still parked on the cancelled execute when
    stop()'s worker.cancel() lands.
    """
    hang = asyncio.Event()

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        await hang.wait()
        return None

    c = IcomCommander(execute, min_interval=0.0)
    c.start()

    with pytest.raises(asyncio.TimeoutError):
        await c.send(b"no-reply", timeout=0.05)

    # No yields between the timeout and stop().
    await _assert_stop_completes(c)
