from __future__ import annotations

import ast
import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from rigplane.runtime import local_tx_work
from rigplane.runtime.local_tx_work import LocalTxWorkRunner
from rigplane.runtime.managed_tx_fence import (
    Cancellation,
    TxAbortFence,
    TxAbortToken,
)


class RecordingFence(TxAbortFence):
    def __init__(self) -> None:
        super().__init__()
        self.registered = False
        self.last_token: TxAbortToken | None = None
        self.cancellation_calls = 0

    def register(
        self,
        token: TxAbortToken,
        cancellation: Cancellation,
        *,
        scope: str | None = None,
    ) -> None:
        self.registered = True
        self.last_token = token

        def counted_cancellation() -> Awaitable[None] | None:
            self.cancellation_calls += 1
            return cancellation()

        super().register(token, counted_cancellation, scope=scope)


@pytest.mark.asyncio
async def test_runner_registers_before_operation_gets_first_turn() -> None:
    fence = RecordingFence()
    runner = LocalTxWorkRunner(fence)

    async def operation(is_current: Callable[[], bool]) -> str:
        assert fence.registered
        assert is_current()
        return "done"

    assert await runner.run(operation) == "done"


@pytest.mark.asyncio
async def test_force_off_poisons_predicate_before_cancellation_runs() -> None:
    fence = TxAbortFence()
    runner = LocalTxWorkRunner(fence)
    started = asyncio.Event()
    predicates: list[Callable[[], bool]] = []

    async def operation(is_current: Callable[[], bool]) -> None:
        predicates.append(is_current)
        started.set()
        await asyncio.Event().wait()

    work = asyncio.create_task(runner.run(operation))
    await started.wait()

    cleanup = fence.force_off()
    assert predicates[0]() is False
    assert not work.done()

    result = await cleanup
    assert result.failures == ()
    with pytest.raises(asyncio.CancelledError):
        await work


@pytest.mark.asyncio
async def test_poisoned_operation_cannot_return_stale_success_before_cleanup() -> None:
    fence = TxAbortFence()
    runner = LocalTxWorkRunner(fence)
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation(is_current: Callable[[], bool]) -> str:
        assert is_current()
        started.set()
        await release.wait()
        return "stale success"

    work = asyncio.create_task(runner.run(operation))
    await started.wait()
    cleanup = fence.force_off()
    try:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
    finally:
        result = await cleanup

    assert result.failures == ()


@pytest.mark.asyncio
async def test_blocked_local_cleanup_does_not_block_separate_urgent_off() -> None:
    fence = TxAbortFence()
    runner = LocalTxWorkRunner(fence)
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    events: list[str] = []

    async def operation(is_current: Callable[[], bool]) -> None:
        del is_current
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await release_cleanup.wait()
            raise

    work = asyncio.create_task(runner.run(operation))
    await started.wait()
    cleanup = asyncio.create_task(fence.force_off())
    await cleanup_started.wait()

    events.append("urgent_off_submitted")
    assert events == ["urgent_off_submitted"]
    assert not cleanup.done()

    release_cleanup.set()
    result = await cleanup
    assert result.failures == ()
    with pytest.raises(asyncio.CancelledError):
        await work


@pytest.mark.asyncio
async def test_force_off_cancels_and_drains_active_operation_once() -> None:
    fence = TxAbortFence()
    runner = LocalTxWorkRunner(fence)
    started = asyncio.Event()
    cancellations = 0

    async def operation(is_current: Callable[[], bool]) -> None:
        nonlocal cancellations
        del is_current
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1
            raise

    work = asyncio.create_task(runner.run(operation))
    await started.wait()

    first = await fence.force_off()
    second = await fence.force_off()

    assert first.failures == second.failures == ()
    assert cancellations == 1
    with pytest.raises(asyncio.CancelledError):
        await work


@pytest.mark.asyncio
async def test_force_off_reports_provider_cancellation_cleanup_failure() -> None:
    fence = TxAbortFence()
    runner = LocalTxWorkRunner(fence)
    started = asyncio.Event()

    async def operation(is_current: Callable[[], bool]) -> None:
        del is_current
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError("provider cancellation cleanup failed")

    work = asyncio.create_task(runner.run(operation))
    await started.wait()
    result = await fence.force_off()

    assert len(result.failures) == 1
    assert result.failures[0].error.args == ("provider cancellation cleanup failed",)
    with pytest.raises(RuntimeError, match="provider cancellation cleanup failed"):
        await work


@pytest.mark.asyncio
async def test_ordinary_completion_removes_registration() -> None:
    fence = RecordingFence()
    runner = LocalTxWorkRunner(fence)

    async def operation(is_current: Callable[[], bool]) -> int:
        assert is_current()
        return 7

    assert await runner.run(operation) == 7
    assert fence.last_token is not None
    assert fence.remove(fence.last_token) is False
    result = await fence.force_off()

    assert result.failures == ()
    assert fence.cancellation_calls == 0


@pytest.mark.asyncio
async def test_provider_error_propagates_and_removes_registration() -> None:
    fence = TxAbortFence()
    runner = LocalTxWorkRunner(fence)

    async def operation(is_current: Callable[[], bool]) -> None:
        assert is_current()
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        await runner.run(operation)

    assert (await fence.force_off()).failures == ()


@pytest.mark.asyncio
async def test_caller_cancellation_drains_provider_before_returning() -> None:
    fence = TxAbortFence()
    runner = LocalTxWorkRunner(fence)
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def operation(is_current: Callable[[], bool]) -> None:
        del is_current
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await release_cleanup.wait()
            raise

    work = asyncio.create_task(runner.run(operation))
    await started.wait()
    work.cancel()
    await cleanup_started.wait()
    assert not work.done()

    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert (await fence.force_off()).failures == ()


def test_local_runner_never_constructs_an_abort_fence() -> None:
    source = Path(local_tx_work.__file__).read_text()
    tree = ast.parse(source)

    constructions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "TxAbortFence"
    ]

    assert constructions == []
