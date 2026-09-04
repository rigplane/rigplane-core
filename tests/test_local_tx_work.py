"""Local transmit-adjacent work shares the runtime's abort fence."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from rigplane.runtime.local_tx_work import LocalTxWorkRunner
from rigplane.runtime.managed_tx_fence import TxAbortFence


@pytest.mark.asyncio
async def test_force_off_is_immediate_and_registered_cleanup_cancels_work() -> None:
    fence = TxAbortFence()
    runner = LocalTxWorkRunner(fence)
    started = asyncio.Event()
    held = asyncio.Event()

    async def work(_is_current: Callable[[], bool]) -> None:
        started.set()
        await held.wait()

    task = asyncio.create_task(runner.run(work))
    await started.wait()

    cleanup = fence.force_off()
    assert fence.epoch == 1
    assert not task.done()

    result = await cleanup
    assert result.failures == ()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.1)


@pytest.mark.asyncio
async def test_work_currency_tracks_the_externally_owned_fence() -> None:
    fence = TxAbortFence()
    runner = LocalTxWorkRunner(fence)
    started = asyncio.Event()
    release = asyncio.Event()
    observed: list[bool] = []

    async def work(is_current: Callable[[], bool]) -> None:
        observed.append(is_current())
        started.set()
        await release.wait()
        observed.append(is_current())

    task = asyncio.create_task(runner.run(work))
    await started.wait()
    cleanup = fence.force_off()
    release.set()
    await task
    await cleanup

    assert observed == [True, False]


@pytest.mark.asyncio
async def test_external_task_cancellation_propagates_and_unregisters() -> None:
    fence = TxAbortFence()
    runner = LocalTxWorkRunner(fence)
    started = asyncio.Event()

    async def work(_is_current: Callable[[], bool]) -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(runner.run(work))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    result = await fence.force_off()
    assert result.failures == ()
