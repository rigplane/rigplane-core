"""MOR-4214: the shared command consumer outlives a late command.

Failing-first: the OLD inline pattern (``queue.wait(2)`` + ``assert entry is
not None``) dies when the command arrives after the deadline, so the command
is never executed; ``run_command_consumer`` waits with no deadline and
executes it. An empty wake-up (the wait returns with nothing queued) must not
kill it either. Everything runs on the virtual clock: no real sleeps.
"""

from __future__ import annotations

import asyncio

import pytest

from rigplane.testing.timing import VirtualClock
from rigplane.web.radio_poller import CommandQueue, CommandQueueEntry, SetFreq
from support.command_consumer import run_command_consumer


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_old_pattern_dies_when_command_arrives_after_timeout(
    virtual_clock: VirtualClock,
) -> None:
    """Reproduction of the MOR-4214 flake with the pre-fix inline copy."""
    queue = CommandQueue()
    executed: list[CommandQueueEntry] = []

    async def fake_execute(entry: CommandQueueEntry) -> None:
        executed.append(entry)

    async def old_consume() -> None:
        # The pre-MOR-4214 inline copy: a 2 s deadline, then a hard assert.
        while True:
            await queue.wait(2)
            entry = queue.take_entry()
            assert entry is not None
            await fake_execute(entry)

    consumer = asyncio.create_task(old_consume())
    try:
        # Nothing is queued for 2.5 s: the deadline fires on an empty queue
        # and the assert kills the consumer before the command exists.
        await virtual_clock.elapse(2.5)
        assert consumer.done()
        assert isinstance(consumer.exception(), AssertionError)

        queue.put(SetFreq(1))  # the production enqueue API
        await virtual_clock.elapse(0)
        assert executed == []
        assert queue.pending_count == 1
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("delay", [2.5, 5.0], ids=["after-2.5s", "after-5s"])
async def test_helper_executes_command_arriving_after_delay(
    virtual_clock: VirtualClock, delay: float
) -> None:
    """The helper has no deadline: a late command is still executed."""
    queue = CommandQueue()
    entries: list[CommandQueueEntry] = []
    executed: list[CommandQueueEntry] = []

    async def fake_execute(entry: CommandQueueEntry) -> None:
        executed.append(entry)

    consumer = asyncio.create_task(
        run_command_consumer(queue, fake_execute, entries=entries)
    )
    try:
        await virtual_clock.elapse(delay)
        assert not consumer.done()
        queue.put(SetFreq(1))  # the production enqueue API
        await virtual_clock.elapse(0)
        assert [entry.command for entry in executed] == [SetFreq(1)]
        assert entries == executed
        assert queue.pending_count == 0
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_helper_survives_empty_wakeup(virtual_clock: VirtualClock) -> None:
    """A wait that returns with no entry must not kill the helper."""
    queue = CommandQueue()
    executed: list[CommandQueueEntry] = []

    async def fake_execute(entry: CommandQueueEntry) -> None:
        executed.append(entry)

    consumer = asyncio.create_task(run_command_consumer(queue, fake_execute))
    try:
        await virtual_clock.elapse(0)
        assert not consumer.done()
        queue._notify.set()  # spurious wake-up: nothing queued
        await virtual_clock.elapse(0)
        assert not consumer.done()

        queue.put(SetFreq(1))  # the production enqueue API
        await virtual_clock.elapse(0)
        assert [entry.command for entry in executed] == [SetFreq(1)]
        assert queue.pending_count == 0
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
