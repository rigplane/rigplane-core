from __future__ import annotations

import asyncio

import pytest

from rigplane.commander import IcomCommander, Priority
from rigplane.exceptions import ConnectionError
from rigplane.types import CivFrame


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
