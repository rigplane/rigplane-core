"""One shared command-queue consumer for web/memory tests (MOR-4214).

Several web/memory tests drive a real ``WebServer`` with a real ``RadioPoller``
and need a stand-in for the production poller loop: take each queued command
and execute it through ``poller._execute_queued_entry``. The eight copies this
replaces all waited with a short deadline (``queue.wait(2)``) and asserted the
take was non-empty, so a slow start (a cold xdist worker on a loaded host)
killed the consumer before the command was ever queued and the request then
timed out. This helper waits with no deadline and survives empty wake-ups; a
command that never arrives still fails loudly through the caller's own
``asyncio.wait_for`` deadline on its ``taken``/``executed`` signal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from rigplane.web.radio_poller import CommandQueue, CommandQueueEntry


async def run_command_consumer(
    queue: CommandQueue,
    execute: Callable[[CommandQueueEntry], Awaitable[object]],
    *,
    entries: list[CommandQueueEntry] | None = None,
    count: int | None = None,
    taken: asyncio.Event | None = None,
    executed: asyncio.Event | None = None,
    suppress: tuple[type[BaseException], ...] = (),
) -> None:
    """Take queued commands and run each through ``execute``.

    With ``count=None`` this loops until the task is cancelled (the test's
    teardown cancels it); with ``count=N`` it returns after N entries, so a
    test can ``await`` the consumer itself. ``taken`` is set after each take,
    before ``execute`` runs (for tests whose execute blocks until the test
    reacts); ``executed`` is set after each ``execute`` returns or raises a
    suppressed error (for tests asserting post-execution state). Error types
    in ``suppress`` are swallowed per entry so negative-path tests can drive
    failing executions; anything else propagates and fails the consumer.
    """
    handled = 0
    while count is None or handled < count:
        await queue.wait()
        entry = queue.take_entry()
        if entry is None:
            # An empty wake-up (the wait returned with nothing claimed):
            # keep waiting, never die.
            continue
        if entries is not None:
            entries.append(entry)
        if taken is not None:
            taken.set()
        try:
            await execute(entry)
        except suppress:
            pass
        if executed is not None:
            executed.set()
        handled += 1
