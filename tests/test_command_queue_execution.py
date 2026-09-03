"""Pending-entry and whole-operation execution contracts."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from rigplane.runtime import _poller_types as queue_types
from rigplane.runtime._poller_types import (
    CommandQueue,
    CommandQueueEntry,
    PttOff,
    PttOn,
    SetFreq,
    SetMode,
)


async def wait_for_event_or_exit(event, task):
    waiter = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait(
            (waiter, task), timeout=5, return_when=asyncio.FIRST_COMPLETED
        )
        assert done, "no event or consumer exit within the failure guard"
        assert event.is_set(), "consumer exited before the expected event"
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
async def test_ordered_handle_is_frozen_stored_identity() -> None:
    queue = CommandQueue()
    reply = asyncio.get_running_loop().create_future()
    entry = queue.put_ordered(SetFreq(1), future=reply, command_id="first")
    assert isinstance(entry, CommandQueueEntry)
    with pytest.raises(FrozenInstanceError):
        entry.command_id = "other"
    assert queue.pending_count == 1
    assert queue.take_entry() is entry
    assert queue.pending_count == 0
    assert queue.take_entry() is None
    assert not queue.remove_pending(entry)
    reply.cancel()


def test_single_claim_preserves_segments_ptt_priority_and_replacement() -> None:
    queue = CommandQueue()
    for command in (SetFreq(1), PttOn(), SetFreq(2), PttOff()):
        queue.put(command)
    queue.put_ordered(SetMode("USB"))
    queue.put(SetFreq(3))
    assert queue.pending_count == 5
    assert queue.take_entry().command == PttOn()
    assert queue.pending_count == 4
    queue.put(SetFreq(4))
    assert [queue.take_entry().command for _ in range(4)] == [
        PttOff(),
        SetFreq(2),
        SetMode("USB"),
        SetFreq(4),
    ]
    assert not queue.has_commands
    assert bool(queue)


@pytest.mark.asyncio
async def test_remove_pending_uses_identity_and_cancel_callback_claim_boundary() -> (
    None
):
    queue = CommandQueue()
    reply = asyncio.get_running_loop().create_future()
    first = queue.put_ordered(SetFreq(1), future=reply)
    equal = queue.put_ordered(SetFreq(1), future=reply)
    assert first is not equal and first == equal
    assert not queue.remove_pending(replace(first))
    assert queue.remove_pending(first)
    assert not queue.remove_pending(first)
    assert queue.take_entry() is equal
    reply.cancel()
    pending = asyncio.get_running_loop().create_future()
    queue.put_ordered(SetFreq(2), future=pending)
    queue.put(SetFreq(3))
    pending.cancel()
    await asyncio.sleep(0)
    assert queue.pending_count == 1
    assert not queue.remove_pending(equal)
    assert [entry.command for entry in queue.drain_entries()] == [SetFreq(3)]
    assert queue.drain_entries() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["result", "error"])
async def test_non_cancelled_terminal_reply_does_not_remove_pending_entry(outcome):
    queue = CommandQueue()
    reply = asyncio.get_running_loop().create_future()
    queue.put_ordered(SetFreq(1), future=reply)
    if outcome == "result":
        reply.set_result(None)
    else:
        reply.set_exception(ValueError("caller terminal"))
        reply.exception()
    await asyncio.sleep(0)
    assert queue.has_commands
    assert queue.drain_entries()[0].future is reply


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["result", "error", "cancel"])
@pytest.mark.parametrize("reply_state", ["pending", "none", "result", "error"])
async def test_kernel_terminal_identity_and_hooks_before_reply(outcome, reply_state):
    execute = queue_types.execute_command_queue_entry
    reply = (
        None if reply_state == "none" else asyncio.get_running_loop().create_future()
    )
    previous, failure, value = RuntimeError("previous"), ValueError("leaf"), object()
    if reply_state == "result":
        reply.set_result(value)
    elif reply_state == "error":
        reply.set_exception(previous)
    hooks = []

    async def leaf(entry):
        assert entry.future is reply
        if reply_state == "pending":
            assert not reply.done()
        hooks.append(outcome)
        if outcome == "error":
            raise failure
        if outcome == "cancel":
            raise asyncio.CancelledError("leaf only")
        return value

    if outcome == "error":
        with pytest.raises(ValueError) as caught:
            await execute(CommandQueueEntry(SetFreq(1), future=reply), leaf)
        assert caught.value is failure
    else:
        await execute(CommandQueueEntry(SetFreq(1), future=reply), leaf)
    assert hooks == [outcome]
    if reply_state == "result" or (reply_state == "pending" and outcome == "result"):
        assert reply.result() is value
    elif reply_state == "error":
        assert reply.exception() is previous
    elif reply_state == "pending" and outcome == "error":
        assert reply.exception() is failure
    elif reply_state == "pending":
        assert reply.cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["outer", "child"])
async def test_kernel_checks_cancelled_reply_before_leaf(when):
    execute = queue_types.execute_command_queue_entry
    reply = asyncio.get_running_loop().create_future()
    seen = []

    async def leaf(_entry):
        seen.append("invoked")

    if when == "outer":
        reply.cancel()
    task = asyncio.create_task(execute(CommandQueueEntry(SetFreq(1), reply), leaf))
    if when == "child":
        asyncio.get_running_loop().call_soon(reply.cancel)
    try:
        await task
        assert reply.cancelled()
        assert seen == []
    finally:
        reply.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_kernel_drainer_cancel_before_child_handoff_terminalizes_reply():
    execute = queue_types.execute_command_queue_entry
    loop = asyncio.get_running_loop()
    reply = loop.create_future()
    previous_factory, children, invoked = loop.get_task_factory(), [], []
    abort_reason = RuntimeError("owner before child handoff")

    async def leaf(_entry):
        invoked.append("leaf")

    task = asyncio.create_task(execute(CommandQueueEntry(SetFreq(1), reply), leaf))

    def cancel_before_first_step(event_loop, coroutine, **kwargs):
        event_loop.set_task_factory(previous_factory)
        child = asyncio.Task(coroutine, loop=event_loop, **kwargs)
        children.append(child)
        child.cancel("child before first step")
        task.cancel(abort_reason)
        return child

    loop.set_task_factory(cancel_before_first_step)
    try:
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == (abort_reason,)
        assert caught.value.args[0] is abort_reason
        assert children and all(child.cancelled() for child in children)
        assert invoked == [], "child must never enter the leaf"
        assert reply.cancelled(), "pre-child cancellation must not orphan the reply"
    finally:
        loop.set_task_factory(previous_factory)
        await asyncio.gather(task, *children, return_exceptions=True)
        reply.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_reply", [True, False], ids=["reply", "coalesced"])
async def test_kernel_caller_cancel_does_not_end_active_leaf(has_reply):
    execute = queue_types.execute_command_queue_entry
    reply = asyncio.get_running_loop().create_future() if has_reply else None
    started, release, terminal = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def leaf(_entry):
        try:
            started.set()
            await release.wait()
        finally:
            terminal.set()

    task = asyncio.create_task(execute(CommandQueueEntry(SetFreq(1), reply), leaf))
    try:
        await wait_for_event_or_exit(started, task)
        if reply is not None:
            reply.cancel()
        await asyncio.sleep(0)
        assert not terminal.is_set()
        assert not task.done()
        release.set()
        await task
        assert terminal.is_set()
        if reply is not None:
            assert reply.cancelled()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["cancel", "return", "error"])
@pytest.mark.parametrize("has_reply", [True, False], ids=["reply", "coalesced"])
async def test_kernel_repeated_drainer_cancel_joins_terminal_cleanup(
    outcome, has_reply
):
    execute = queue_types.execute_command_queue_entry
    reply = asyncio.get_running_loop().create_future() if has_reply else None
    started, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    active_release = asyncio.Event()
    terminal, interrupted = asyncio.Event(), asyncio.Event()

    async def leaf(_entry):
        try:
            started.set()
            await active_release.wait()
        finally:
            try:
                cleanup.set()
                await release.wait()
                if outcome == "return":
                    return "suppressed"
                if outcome == "error":
                    raise ValueError("late cleanup error")
            except asyncio.CancelledError:
                interrupted.set()
                raise
            finally:
                terminal.set()

    task = asyncio.create_task(execute(CommandQueueEntry(SetFreq(1), reply), leaf))
    try:
        await wait_for_event_or_exit(started, task)
        task.cancel("owner stop")
        await wait_for_event_or_exit(cleanup, task)
        for _ in range(2):
            task.cancel("repeat stop")
            await asyncio.sleep(0)
        assert not interrupted.is_set(), "child cancellation must be sent only once"
        assert not terminal.is_set()
        assert not task.done(), "drainer must join awaited child cleanup"
        if reply is not None:
            assert not reply.done()
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("owner stop",)
        assert terminal.is_set()
        if reply is not None:
            assert reply.cancelled(), (
                "drainer abort must not acknowledge suppressed work"
            )
    finally:
        active_release.set()
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def assert_live_pending_turn(
    queue, drain, install_leaf, *, mode, boundary=None, install_readback=None
):
    """Exercise one real consumer turn with arrivals behind a held write."""
    started, release = asyncio.Event(), asyncio.Event()
    reply = asyncio.get_running_loop().create_future()
    second = asyncio.get_running_loop().create_future()
    seen, failed, tracked = [], [], []
    failure = ValueError("write failed")

    def note_readback():
        assert not reply.done(), "readback hook must complete before the reply"
        tracked.append("readback")

    if mode == "readback":
        install_readback(note_readback)

    def fail_command(command_id, **params):
        assert not reply.done(), "backend failure hook must precede reply completion"
        failed.append((command_id, params["source"], params["session_id"]))

    service = SimpleNamespace(
        fail_command=fail_command,
        retain_readback_expectations_for_dispatch=lambda **_: (),
    )

    async def leaf(command, **_kwargs):
        seen.append(command.freq)
        if command.freq == 1:
            started.set()
            await release.wait()
            if mode == "error":
                raise failure

    install_leaf(leaf)
    queue.put_ordered(
        SetFreq(1),
        future=reply,
        command_id="first",
        source="websocket",
        session_id="caller",
        command_service=service,
    )
    if mode == "replace":
        queue.put(SetFreq(2))
    else:
        queue.put_ordered(SetFreq(2), future=second)
    task = asyncio.create_task(drain())
    try:
        await wait_for_event_or_exit(started, task)
        assert queue.has_commands, "second entry must stay pending during first leaf"
        if mode in ("cancel", "replace"):
            reply.cancel()
        if mode == "cancel":
            second.cancel()
            await asyncio.sleep(0)
            assert not queue.has_commands, "cancel callback must unlink pending second"
            queue.put_ordered(SetFreq(3))
        elif mode == "replace":
            queue.put(SetFreq(3))
        else:
            queue.put_ordered(SetFreq(3))
        queue.put_ordered(SetFreq(4))
        await asyncio.sleep(0)
        assert not task.done(), "held leaf must retain the operation"
        assert seen == [1]
        release.set()
        if boundary is None:
            await task
        else:
            await wait_for_event_or_exit(boundary, task)
        expected = [1, 3] if mode in ("cancel", "replace") else [1, 2]
        assert seen == expected, "turn must use live pending claims, not a snapshot"
        remaining = (
            [SetFreq(4)] if mode in ("cancel", "replace") else [SetFreq(3), SetFreq(4)]
        )
        assert [e.command for e in queue.drain_entries()] == remaining, (
            "producer arrivals must not refill the initial two-claim quota"
        )
        if mode == "error":
            assert failed == [("first", "websocket", "caller")]
            assert reply.exception() is failure
            assert second.result() is None
        elif mode == "readback":
            assert tracked == ["readback"]
            assert reply.result() is None and second.result() is None
    finally:
        release.set()
        reply.cancel()
        second.cancel()
        if boundary is not None:
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for future in (reply, second):
            if future.done() and not future.cancelled():
                future.exception()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "keep_pending"),
    [(SetFreq(1), False), (PttOn(), False), (PttOff(), True)],
    ids=["ordinary", "ptt-on", "ptt-off"],
)
async def test_cancelled_ordered_reply_keeps_only_legacy_unkey(command, keep_pending):
    queue = CommandQueue()
    reply = asyncio.get_running_loop().create_future()
    queue.put_ordered(command, future=reply)
    reply.cancel()
    await asyncio.sleep(0)
    entries = queue.drain_entries()
    assert reply.cancelled()
    expected = [command] if keep_pending else []
    assert [entry.command for entry in entries] == expected, (
        "cancel callback must remove ordinary and ON entries but retain legacy PttOff"
    )
    if keep_pending:
        assert entries[0].future is reply
