import asyncio
from types import SimpleNamespace

import pytest

from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    ActuationSettled,
    ManagedTxIntentKind,
    ManagedTxOutcome,
)
from test_managed_tx_authority import FakeClock, FakeConfigStore, FakeWakeup
from test_managed_tx_effect_lane import FakeActuator, blocking_action


@pytest.fixture
async def rig():
    clock, actuator, fence = FakeClock(), FakeActuator(), TxAbortFence()
    managed = ManagedTxAuthority(
        ManagedTxEffectLane(actuator, clock=clock),
        FakeConfigStore(10),
        fence,
        provider_generation=7,
        clock=clock,
        wakeup=FakeWakeup(),
        attempt_timeout_seconds=1,
    )
    await managed._stop_scheduler(managed._scheduler_task)
    fixture = SimpleNamespace(
        managed=managed,
        actuator=actuator,
        fence=fence,
        clock=clock,
        tasks=[],
        releases=[],
    )
    try:
        yield fixture
    finally:
        for release in fixture.releases:
            if isinstance(release, asyncio.Future):
                if not release.done():
                    release.set_result(None)
            else:
                release.set()
        for task in fixture.tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(
            asyncio.gather(*fixture.tasks, return_exceptions=True), 2
        )
        state = (await managed.snapshot()).state
        if state.release_required or state.intent.kind is not ManagedTxIntentKind.RX:
            await asyncio.wait_for(managed.force_off(), 2)
        await asyncio.wait_for(managed.close(), 2)


def ready_future(rig):
    future = asyncio.get_running_loop().create_future()
    rig.releases.append(future)
    return future


def track(rig, task):
    assert isinstance(task, asyncio.Task)
    rig.tasks.append(task)
    return task


async def submit(rig, on, owner, *, ready=None):
    async with asyncio.timeout(1):
        return track(rig, await rig.managed.submit_ptt(on, owner, ready=ready))


async def checkpoint():
    reached = asyncio.Event()
    asyncio.get_running_loop().call_soon(reached.set)
    await reached.wait()


async def prevented(task):
    try:
        transition, settled = await asyncio.wait_for(asyncio.shield(task), 1)
    except asyncio.CancelledError:
        return
    except RuntimeError as error:
        assert str(error) == "predecessor failed"
        return
    assert transition.outcome is ManagedTxOutcome.REJECTED
    assert not transition.effects and settled is None


async def test_prestart_on_cancel_removes_registration_without_dispatch(rig):
    worker = await submit(rig, True, "A")
    assert rig.fence._cancellations
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert not rig.fence._cancellations
    assert not rig.actuator.calls
    assert not (await rig.managed.snapshot()).state.release_required


async def test_scoped_up_revokes_old_down_before_contended_lock_not_new_down(rig):
    old_ready, foreign_ready, new_ready = (ready_future(rig) for _ in range(3))
    old = await submit(rig, True, "A", ready=old_ready)
    old_token = next(iter(rig.fence._cancellations))
    foreign = await submit(rig, True, "B", ready=foreign_ready)
    foreign_token = next(t for t in rig.fence._cancellations if t is not old_token)
    entered = asyncio.Event()

    async def submit_up():
        entered.set()
        return await submit(rig, False, "A")

    await rig.managed._lock.acquire()
    try:
        submission = track(rig, asyncio.create_task(submit_up()))
        await asyncio.wait_for(entered.wait(), 1)
        assert not submission.done()
        assert old_token not in rig.fence._cancellations
        assert foreign_token in rig.fence._cancellations
    finally:
        rig.managed._lock.release()
    up = await asyncio.wait_for(asyncio.shield(submission), 1)
    await asyncio.wait_for(asyncio.shield(up), 1)
    new = await submit(rig, True, "A", ready=new_ready)
    new_token = next(t for t in rig.fence._cancellations if t is not foreign_token)
    assert new_token is not old_token and rig.fence.is_current(new_token)
    assert not foreign.done() and not foreign_ready.cancelled()
    old_ready.set_result(None)
    await prevented(old)
    assert not rig.actuator.calls
    new_ready.set_result(None)
    transition, settled = await asyncio.wait_for(asyncio.shield(new), 1)
    assert transition.outcome is ManagedTxOutcome.ACCEPTED
    assert settled.result is ActuationResult.ACCEPTED
    assert [op for _, op in rig.actuator.calls] == [ActuationOperation.PTT_ON]


async def test_off_cancel_before_execute_entry_drains_despite_repeated_cancel(
    rig, monkeypatch
):
    assert await rig.managed.ptt_down("A") is ManagedTxOutcome.ACCEPTED
    entered, release, provider_started, provider_release, provider_cancelled = (
        asyncio.Event() for _ in range(5)
    )
    rig.releases.extend((release, provider_release))
    original_execute = rig.managed._execute
    original_entered = asyncio.Event()

    async def gated_execute(*args, **kwargs):
        entered.set()
        await release.wait()
        original_entered.set()
        return await original_execute(*args, **kwargs)

    monkeypatch.setattr(rig.managed, "_execute", gated_execute)
    rig.actuator.actions[ActuationOperation.FORCE_RECEIVE] = blocking_action(
        provider_started,
        provider_release,
        cancelled=provider_cancelled,
        resist_cancellation=True,
    )
    worker = await submit(rig, False, "A")
    assert not original_entered.is_set()
    state = (await rig.managed.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX and state.pending_effect
    worker.cancel()
    await asyncio.wait_for(entered.wait(), 1)
    release.set()
    await asyncio.wait_for(provider_started.wait(), 1)
    worker.cancel()
    await checkpoint()
    worker.cancel()
    await checkpoint()
    assert not worker.done() and not provider_cancelled.is_set()
    provider_release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(worker), 1)
    state = (await rig.managed.snapshot()).state
    assert state.pending_effect is None and not state.release_required
    assert state.last_actuation.result is ActuationResult.ACCEPTED


async def test_on_cancel_does_not_cancel_readiness_predecessor(rig):
    ready = ready_future(rig)
    worker = await submit(rig, True, "A", ready=ready)
    await checkpoint()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert not ready.done() and not rig.fence._cancellations
    assert not rig.actuator.calls


@pytest.mark.parametrize("failure", ["cancel", "error"])
async def test_failed_predecessor_completion_allows_independent_on(rig, failure):
    ready = ready_future(rig)
    worker = await submit(rig, True, "A", ready=ready)
    await checkpoint()
    assert not ready.done() and not worker.done()
    assert not rig.actuator.calls
    if failure == "cancel":
        ready.cancel()
        with pytest.raises(asyncio.CancelledError):
            await ready
    else:
        error = RuntimeError("predecessor failed")
        ready.set_exception(error)
        with pytest.raises(RuntimeError) as original:
            await ready
        assert original.value is error
    done, _ = await asyncio.wait((worker,), timeout=1)
    assert worker in done
    assert not worker.cancelled()
    assert worker.exception() is None
    transition, settled = await worker
    assert transition.outcome is ManagedTxOutcome.ACCEPTED
    assert isinstance(settled, ActuationSettled)
    assert settled.result is ActuationResult.ACCEPTED
    assert settled.token == transition.effects[0].token
    assert rig.actuator.calls == [(settled.token, ActuationOperation.PTT_ON)]
    assert not rig.fence._cancellations
    state = (await rig.managed.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.PTT
    assert state.intent.owner_token == "A" and state.release_required


async def test_pending_only_disconnect_revokes_owner_while_rx_clean(rig):
    ready_a, ready_b = ready_future(rig), ready_future(rig)
    old = await submit(rig, True, "A", ready=ready_a)
    old_token = next(iter(rig.fence._cancellations))
    foreign = await submit(rig, True, "B", ready=ready_b)
    foreign_token = next(t for t in rig.fence._cancellations if t is not old_token)
    assert (await rig.managed.snapshot()).state.intent.kind is ManagedTxIntentKind.RX
    await rig.managed.owner_disconnect("A")
    assert old_token not in rig.fence._cancellations
    assert foreign_token in rig.fence._cancellations and not foreign.done()
    ready_a.set_result(None)
    await prevented(old)
    assert not rig.actuator.calls
    assert tuple(rig.fence._cancellations) == (foreign_token,)


async def test_provider_replacement_while_rx_clean_prevents_old_pending_on(rig):
    ready = ready_future(rig)
    worker = await submit(rig, True, "A", ready=ready)
    await rig.managed.provider_unavailable()
    await rig.managed.provider_available(8)
    ready.set_result(None)
    await prevented(worker)
    assert not rig.fence._cancellations and not rig.actuator.calls
    projection = await rig.managed.snapshot()
    assert projection.provider_generation == 8
    assert projection.state.intent.kind is ManagedTxIntentKind.RX
    assert not projection.state.release_required


@pytest.mark.parametrize(
    "result",
    [ActuationResult.UNCERTAIN, OSError("provider")],
    ids=["uncertain", "provider_error"],
)
async def test_original_on_uncertainty_not_replaced_by_accepted_compensation(
    rig, result
):
    rig.actuator.actions[ActuationOperation.PTT_ON] = result
    worker = await submit(rig, True, "A")
    transition, settled = await asyncio.wait_for(asyncio.shield(worker), 1)
    assert transition.outcome is ManagedTxOutcome.ACCEPTED
    assert isinstance(settled, ActuationSettled)
    assert settled.token == transition.effects[0].token
    assert settled.operation is ActuationOperation.PTT_ON
    assert settled.result is ActuationResult.UNCERTAIN
    state = (await rig.managed.snapshot()).state
    assert state.last_actuation.operation is ActuationOperation.FORCE_RECEIVE
    assert state.last_actuation.result is ActuationResult.ACCEPTED
    assert not state.release_required


async def test_scoped_up_membership_blocks_on_when_cancel_handle_does_nothing(
    rig, monkeypatch
):
    callback_called = asyncio.Event()
    original_register = rig.fence.register

    def register_without_cancelling(token, cancellation, *, scope=None):
        original_register(token, callback_called.set, scope=scope)

    monkeypatch.setattr(rig.fence, "register", register_without_cancelling)
    ready = ready_future(rig)
    worker = await submit(rig, True, "A", ready=ready)
    token = next(iter(rig.fence._cancellations))
    await checkpoint()
    up = await submit(rig, False, "A")
    await asyncio.wait_for(asyncio.shield(up), 1)
    await asyncio.wait_for(callback_called.wait(), 1)
    assert rig.fence.is_current(token) and token not in rig.fence._cancellations
    assert not worker.done() and not ready.done()
    ready.set_result(None)
    await prevented(worker)
    assert not rig.actuator.calls and not rig.fence._cancellations
    assert not (await rig.managed.snapshot()).state.release_required


async def test_cancel_after_on_admission_settles_without_implicit_up(rig):
    entered, release = asyncio.Event(), asyncio.Event()
    rig.releases.append(release)
    rig.actuator.actions[ActuationOperation.PTT_ON] = blocking_action(entered, release)
    worker = await submit(rig, True, "A")
    await asyncio.wait_for(entered.wait(), 1)
    worker.cancel()
    await checkpoint()
    assert not worker.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(worker), 1)
    state = (await rig.managed.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.PTT and state.release_required
    assert state.pending_effect is None
    assert state.last_actuation.result is ActuationResult.ACCEPTED
    assert [op for _, op in rig.actuator.calls] == [ActuationOperation.PTT_ON]


async def test_idempotent_admission_returns_no_fabricated_settlement(rig):
    assert await rig.managed.ptt_down("A") is ManagedTxOutcome.ACCEPTED
    first = (await rig.managed.snapshot()).state
    rig.clock.now += 2
    worker = await submit(rig, True, "A")
    transition, settled = await asyncio.wait_for(asyncio.shield(worker), 1)
    assert transition.outcome is ManagedTxOutcome.ACCEPTED
    assert not transition.effects and settled is None
    assert (await rig.managed.snapshot()).state == first
    assert len(rig.actuator.calls) == 1


async def test_public_owner_idempotency_and_tot_are_unchanged(rig):
    assert await rig.managed.ptt_down("A") is ManagedTxOutcome.ACCEPTED
    first = (await rig.managed.snapshot()).state
    rig.clock.now += 2
    assert await rig.managed.ptt_down("A") is ManagedTxOutcome.ACCEPTED
    assert await rig.managed.ptt_down("B") is ManagedTxOutcome.REJECTED
    assert await rig.managed.ptt_up("B") is ManagedTxOutcome.REJECTED
    assert (await rig.managed.snapshot()).state == first
    assert len(rig.actuator.calls) == 1
    rig.clock.now = first.tot_deadline_monotonic
    await rig.managed._process_due()
    state = (await rig.managed.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX and not state.release_required
    assert state.last_actuation.operation is ActuationOperation.FORCE_RECEIVE


async def test_public_owner_up_settles_release(rig):
    assert await rig.managed.ptt_down("A") is ManagedTxOutcome.ACCEPTED
    assert await rig.managed.ptt_up("A") is ManagedTxOutcome.ACCEPTED
    state = (await rig.managed.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX
    assert state.pending_effect is None and not state.release_required
    assert [op for _, op in rig.actuator.calls] == [
        ActuationOperation.PTT_ON,
        ActuationOperation.FORCE_RECEIVE,
    ]


async def test_non_builtin_owner_is_rejected_without_equality_or_registration(rig):
    equality_calls = []

    class DangerousStr(str):
        def __eq__(self, other: object) -> bool:
            equality_calls.append(other)
            raise AssertionError("owner equality must not run")

    owner = DangerousStr("operator")
    assert owner
    before = await rig.managed.snapshot()
    with pytest.raises(TypeError):
        await submit(rig, True, owner)
    with pytest.raises(TypeError):
        await submit(rig, False, owner)
    with pytest.raises(TypeError):
        await rig.managed.ptt_down(owner)
    with pytest.raises(TypeError):
        await rig.managed.ptt_up(owner)
    with pytest.raises(TypeError):
        await rig.managed.owner_disconnect(owner)
    assert not equality_calls
    assert rig.fence._next_number == 0 and not rig.fence._cancellations
    assert not rig.actuator.calls
    assert await rig.managed.snapshot() == before
