import asyncio

import pytest

from rigplane.runtime.managed_tx_fence import TxAbortFence, TxAbortFailure


def test_token_is_current_only_for_its_fence_epoch() -> None:
    fence = TxAbortFence()
    token = fence.issue()

    assert fence.is_current(token)


@pytest.mark.asyncio
async def test_force_off_advances_before_every_registered_cancellation() -> None:
    fence = TxAbortFence()
    old = fence.issue()
    seen: list[bool] = []

    async def cancel() -> None:
        seen.append(fence.is_current(old))

    fence.register(old, cancel)

    result = await fence.force_off()

    assert result.epoch == 1
    assert result.failures == ()
    assert seen == [False]
    assert not fence.is_current(old)


@pytest.mark.asyncio
async def test_force_off_collects_sync_and_async_failures_and_continues() -> None:
    fence = TxAbortFence()
    first, second, third = fence.issue(), fence.issue(), fence.issue()
    calls: list[str] = []

    def sync_failure() -> None:
        calls.append("sync")
        raise RuntimeError("sync failure")

    async def async_failure() -> None:
        calls.append("async")
        raise ValueError("async failure")

    def succeeds() -> None:
        calls.append("success")

    fence.register(first, sync_failure)
    fence.register(second, async_failure)
    fence.register(third, succeeds)

    result = await fence.force_off()

    assert calls == ["sync", "async", "success"]
    assert [type(failure) for failure in result.failures] == [
        TxAbortFailure,
        TxAbortFailure,
    ]
    assert [type(failure.error) for failure in result.failures] == [
        RuntimeError,
        ValueError,
    ]
    assert [failure.token for failure in result.failures] == [first, second]


def test_registration_and_removal_are_token_specific_and_deterministic() -> None:
    fence = TxAbortFence()
    token = fence.issue()

    fence.register(token, lambda: None)
    with pytest.raises(ValueError, match="already registered"):
        fence.register(token, lambda: None)
    assert fence.remove(token) is True
    assert fence.remove(token) is False


@pytest.mark.asyncio
async def test_fenced_work_is_cancelled_once_and_repeated_force_off_is_local_noop() -> (
    None
):
    fence = TxAbortFence()
    token = fence.issue()
    calls: list[int] = []
    fence.register(token, lambda: calls.append(1))

    first = await fence.force_off()
    second = await fence.force_off()

    assert first.epoch == 1
    assert second.epoch == 2
    assert calls == [1]
    assert first.failures == second.failures == ()


@pytest.mark.asyncio
async def test_late_old_token_completion_cannot_become_current() -> None:
    fence = TxAbortFence()
    token = fence.issue()
    started = asyncio.Event()
    release = asyncio.Event()
    completion_is_current: list[bool] = []

    async def work() -> None:
        started.set()
        await release.wait()
        completion_is_current.append(fence.is_current(token))

    task = asyncio.create_task(work())
    await started.wait()
    await fence.force_off()
    release.set()
    await task

    assert completion_is_current == [False]


def test_another_fence_cannot_accept_this_fences_token() -> None:
    fence = TxAbortFence()
    another = TxAbortFence()
    token = fence.issue()

    assert another.is_current(token) is False
    with pytest.raises(ValueError, match="current token"):
        another.register(token, lambda: None)


@pytest.mark.asyncio
async def test_force_off_invalidates_at_call_and_keeps_each_batch_epoch() -> None:
    fence = TxAbortFence()
    old = fence.issue()
    called: list[int] = []
    fence.register(old, lambda: called.append(1))
    first = fence.force_off()
    try:
        assert not fence.is_current(old)
        assert called == []
        second = await fence.force_off()
        assert second.epoch == 2
    finally:
        result = await first
    assert result.epoch == 1
    assert called == [1]


@pytest.mark.asyncio
async def test_held_cleanup_does_not_starve_other_cancellations() -> None:
    fence = TxAbortFence()
    held, other, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def first() -> None:
        held.set()
        await release.wait()

    async def second() -> None:
        other.set()
        raise RuntimeError("cleanup failed")

    fence.register(fence.issue(), first)
    fence.register(fence.issue(), second)
    cleanup = asyncio.create_task(fence.force_off())
    try:
        await asyncio.wait_for(held.wait(), 1)
        await asyncio.wait_for(other.wait(), 1)
        assert not cleanup.done()
    finally:
        release.set()
        result = await cleanup
    assert len(result.failures) == 1
    assert str(result.failures[0].error) == "cleanup failed"


@pytest.mark.asyncio
async def test_cancel_scope_revokes_equal_scope_before_cleanup_and_keeps_others() -> None:
    fence = TxAbortFence()
    owner = "operator-" + "same"
    equal_owner = "".join(("operator-", "same"))
    assert owner == equal_owner and owner is not equal_owner
    selected, other, unscoped = fence.issue(), fence.issue(), fence.issue()
    calls: list[str] = []
    fence.register(selected, lambda: calls.append("selected"), scope=owner)
    fence.register(other, lambda: calls.append("other"), scope="another-owner")
    fence.register(unscoped, lambda: calls.append("unscoped"))

    cleanup = fence.cancel_scope(equal_owner)
    try:
        assert fence.remove(selected) is False
        assert fence.remove(other) is True
        assert fence.remove(unscoped) is True
        assert fence.is_current(selected)
        assert fence.epoch == 0
        assert calls == []
    finally:
        result = await cleanup
    assert result.failures == ()
    assert calls == ["selected"]


@pytest.mark.asyncio
async def test_cancel_scope_cleanup_does_not_rescan_new_same_scope_registration() -> None:
    fence = TxAbortFence()
    old = fence.issue()
    calls: list[str] = []
    fence.register(old, lambda: calls.append("old"), scope="operator")

    cleanup = fence.cancel_scope("operator")
    try:
        new = fence.issue()
        fence.register(new, lambda: calls.append("new"), scope="operator")
    finally:
        result = await cleanup

    assert result.failures == ()
    assert calls == ["old"]
    assert fence.remove(new) is True


def test_cancel_scope_rejects_non_builtin_str_without_equality_probe() -> None:
    class DangerousStr(str):
        def __eq__(self, other: object) -> bool:
            raise AssertionError("scope equality must not run")

    fence = TxAbortFence()
    unscoped = fence.issue()
    fence.register(unscoped, lambda: None, scope=None)
    for scope in (None, object(), DangerousStr("operator")):
        with pytest.raises(TypeError):
            fence.cancel_scope(scope)  # type: ignore[arg-type]
    for scope in (object(), DangerousStr("operator")):
        token = fence.issue()
        with pytest.raises(TypeError):
            fence.register(token, lambda: None, scope=scope)  # type: ignore[arg-type]
        assert fence.remove(token) is False
    assert fence.remove(unscoped) is True


@pytest.mark.asyncio
async def test_cancel_scope_reports_cancellation_and_callback_failures() -> None:
    fence = TxAbortFence()
    cancellation_error, raising = fence.issue(), fence.issue()

    def raise_cancellation_error() -> None:
        raise asyncio.CancelledError()

    async def fail() -> None:
        raise RuntimeError("cleanup failed")

    fence.register(cancellation_error, raise_cancellation_error, scope="operator")
    fence.register(raising, fail, scope="operator")
    result = await fence.cancel_scope("operator")

    assert fence.remove(cancellation_error) is False
    assert fence.remove(raising) is False
    assert fence.is_current(cancellation_error) and fence.is_current(raising)
    assert fence.epoch == 0
    assert [type(failure.error) for failure in result.failures] == [
        asyncio.CancelledError,
        RuntimeError,
    ]


@pytest.mark.asyncio
async def test_force_off_invalidates_scoped_and_unscoped_callbacks_before_cleanup() -> (
    None
):
    fence = TxAbortFence()
    scoped, unscoped = fence.issue(), fence.issue()
    calls: list[str] = []
    fence.register(scoped, lambda: calls.append("scoped"), scope="operator")
    fence.register(unscoped, lambda: calls.append("unscoped"), scope=None)

    cleanup = fence.force_off()
    try:
        assert fence.epoch == 1
        assert not fence.is_current(scoped)
        assert not fence.is_current(unscoped)
        assert fence.remove(scoped) is False
        assert fence.remove(unscoped) is False
        assert calls == []
    finally:
        result = await cleanup

    assert result.failures == ()
    assert calls == ["scoped", "unscoped"]
