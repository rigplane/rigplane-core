import asyncio
import time
from collections.abc import Awaitable, Callable

import pytest

from rigplane.runtime.managed_tx_effect_lane import (
    ManagedTxActuator,
    ManagedTxEffectLane,
)
from rigplane.runtime.managed_tx_state import (
    AbortFailed,
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    ActuationSettled,
    EffectToken,
    ForceOff,
    ManagedTxEffect,
    ManagedTxOutcome,
    ManagedTxState,
    reduce_managed_tx,
)

_Action = Callable[[], Awaitable[ActuationResult]]
_Operation = ActuationOperation | AbortOperation


class FakeActuator:
    def __init__(self) -> None:
        self.calls: list[tuple[EffectToken, _Operation]] = []
        self.actions: dict[_Operation, ActuationResult | BaseException | _Action] = {}

    async def actuate(self, token: EffectToken, op: _Operation) -> ActuationResult:
        self.calls.append((token, op))
        action = self.actions.get(op, ActuationResult.ACCEPTED)
        if isinstance(action, BaseException):
            raise action
        if callable(action):
            return await action()
        return action


def effect(
    operation: ActuationOperation = ActuationOperation.PTT_ON,
    generation: int = 7,
    epoch: int = 3,
    attempt: str = "attempt-1",
) -> ManagedTxEffect:
    return ManagedTxEffect(operation, EffectToken(generation, epoch, attempt))


async def settle(
    lane: ManagedTxEffectLane,
    requested: ManagedTxEffect,
    timeout: float = 1,
) -> ActuationSettled:
    deadline = time.monotonic() + timeout
    return await lane.settle(requested, deadline_monotonic=deadline)


def blocking_action(
    started: asyncio.Event,
    release: asyncio.Event,
    resist_cancellation: bool = False,
    cancelled: asyncio.Event | None = None,
) -> _Action:
    async def action() -> ActuationResult:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            if not resist_cancellation:
                raise
            if cancelled is not None:
                cancelled.set()
            await release.wait()
        return ActuationResult.ACCEPTED

    return action


def poison_lane(actuator: FakeActuator, poisoned: list[int]) -> ManagedTxEffectLane:
    async def poison(generation: int) -> None:
        poisoned.append(generation)

    return ManagedTxEffectLane(actuator, poison_generation=poison)


@pytest.mark.asyncio
@pytest.mark.parametrize("result", list(ActuationResult))
async def test_preserves_explicit_normalized_result(result: ActuationResult) -> None:
    actuator = FakeActuator()
    actuator.actions[ActuationOperation.PTT_ON] = result
    lane = ManagedTxEffectLane(actuator)
    requested = effect()
    settled = await settle(lane, requested)
    assert isinstance(actuator, ManagedTxActuator)
    assert settled == ActuationSettled(
        requested.token,
        requested.operation,
        result,
        None if result is ActuationResult.ACCEPTED else result.value,
    )


@pytest.mark.asyncio
async def test_concurrent_duplicate_claim_executes_actuator_exactly_once() -> None:
    actuator = FakeActuator()
    started, release = asyncio.Event(), asyncio.Event()
    actuator.actions[ActuationOperation.PTT_ON] = blocking_action(started, release)
    lane = ManagedTxEffectLane(actuator)
    requested = effect()
    calls = [asyncio.create_task(settle(lane, requested)) for _ in range(3)]
    await asyncio.wait_for(started.wait(), 0.2)
    assert actuator.calls == [(requested.token, requested.operation)]
    release.set()
    results = await asyncio.gather(*calls)
    assert isinstance(results[0], ActuationSettled)
    assert results[1:] == [None, None]


@pytest.mark.asyncio
async def test_replay_conflict_stale_scope_and_attempt_order_do_not_execute() -> None:
    actuator = FakeActuator()
    lane = ManagedTxEffectLane(actuator)
    base = effect(ActuationOperation.FORCE_RECEIVE, attempt="opaque-old")
    assert (await settle(lane, base)).result is ActuationResult.ACCEPTED
    assert await settle(lane, base) is None
    assert await settle(lane, effect(attempt="opaque-old")) is None
    started, release = asyncio.Event(), asyncio.Event()
    actuator.actions[ActuationOperation.PTT_ON] = blocking_action(started, release)
    current = effect(attempt="opaque-new")
    on = asyncio.create_task(settle(lane, current))
    await asyncio.wait_for(started.wait(), 0.2)
    assert await settle(lane, base) is None and not on.done()
    queued = asyncio.create_task(settle(lane, effect(attempt="queued")))
    await asyncio.sleep(0)
    actuator.actions[ActuationOperation.PTT_ON] = ActuationResult.ACCEPTED
    newer = effect(generation=8, attempt="newer")
    assert (await settle(lane, newer)).result is ActuationResult.ACCEPTED
    assert [item.result for item in await asyncio.gather(on, queued)] == [
        ActuationResult.UNCERTAIN,
        ActuationResult.REJECTED,
    ]
    assert await settle(lane, effect(attempt="late-old")) is None
    assert [token for token, _ in actuator.calls] == [
        base.token,
        current.token,
        newer.token,
    ]


@pytest.mark.asyncio
async def test_expired_before_entry_is_rejected_without_provider_call() -> None:
    actuator = FakeActuator()
    lane = ManagedTxEffectLane(actuator, clock=lambda: 20.0)
    settled = await lane.settle(effect(), deadline_monotonic=20.0)
    assert settled.result is ActuationResult.REJECTED
    assert settled.error == "deadline expired before dispatch"
    assert actuator.calls == []


@pytest.mark.asyncio
async def test_started_timeout_is_uncertain_and_poisons_generation_once() -> None:
    actuator = FakeActuator()
    started, release, ignored_cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
    poisoned: list[int] = []
    actuator.actions[ActuationOperation.PTT_ON] = blocking_action(
        started, release, resist_cancellation=True, cancelled=ignored_cancel
    )
    lane = poison_lane(actuator, poisoned)
    settled = await settle(lane, effect(), 0.01)
    assert started.is_set() and settled.result is ActuationResult.UNCERTAIN
    assert settled.error == "attempt deadline expired"
    await asyncio.wait_for(ignored_cancel.wait(), 0.2)
    await asyncio.sleep(0)
    assert poisoned == [7]
    assert not lane._claims
    release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [ConnectionError("provider disconnected"), asyncio.CancelledError()],
)
async def test_provider_failure_is_uncertain(failure: BaseException) -> None:
    actuator = FakeActuator()
    poisoned: list[int] = []
    actuator.actions[ActuationOperation.PTT_ON] = failure
    lane = poison_lane(actuator, poisoned)
    settled = await settle(lane, effect())
    await asyncio.sleep(0)
    assert settled.result is ActuationResult.UNCERTAIN
    assert poisoned == [7]


@pytest.mark.asyncio
async def test_force_receive_bypasses_and_poisons_inflight_on() -> None:
    actuator = FakeActuator()
    started, release = asyncio.Event(), asyncio.Event()
    poisoned: list[int] = []

    actuator.actions[ActuationOperation.PTT_ON] = blocking_action(
        started, release, resist_cancellation=True
    )
    lane = poison_lane(actuator, poisoned)
    on_task = asyncio.create_task(settle(lane, effect()))
    await asyncio.wait_for(started.wait(), 0.2)
    stale = effect(ActuationOperation.FORCE_RECEIVE, generation=6, attempt="stale")
    assert await settle(lane, stale) is None
    assert not on_task.done()
    force = effect(ActuationOperation.FORCE_RECEIVE, epoch=4, attempt="off")
    forced = await asyncio.wait_for(settle(lane, force), 0.2)
    displaced = await asyncio.wait_for(on_task, 0.2)
    assert not release.is_set()
    assert forced.result is ActuationResult.ACCEPTED
    assert displaced.result is ActuationResult.UNCERTAIN
    assert displaced.error == "superseded after dispatch"
    await asyncio.sleep(0)
    assert poisoned == [7]
    release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "isolation_fails,preexisting", [(False, False), (True, False), (False, True)]
)
async def test_force_receive_awaits_isolation_and_maps_failure(
    isolation_fails: bool, preexisting: bool
) -> None:
    actuator = FakeActuator()
    on_started, on_release = asyncio.Event(), asyncio.Event()
    isolation_started, isolated = asyncio.Event(), asyncio.Event()

    async def isolate(_: int) -> None:
        isolation_started.set()
        if isolation_fails:
            raise RuntimeError("isolation failed")
        await isolated.wait()

    actuator.actions[ActuationOperation.PTT_ON] = blocking_action(
        on_started, on_release, resist_cancellation=True
    )
    lane = ManagedTxEffectLane(actuator, poison_generation=isolate)
    on = asyncio.create_task(settle(lane, effect(attempt="on")))
    await asyncio.wait_for(on_started.wait(), 0.2)
    if preexisting:
        on.cancel()
        assert (await on).error == "attempt cancelled after dispatch"
        await asyncio.wait_for(isolation_started.wait(), 0.2)
    force = asyncio.create_task(
        settle(lane, effect(ActuationOperation.FORCE_RECEIVE, attempt="off"))
    )
    await asyncio.wait_for(isolation_started.wait(), 0.2)
    if not isolation_fails:
        assert not force.done() and not on_release.is_set()
        isolated.set()
    forced = await asyncio.wait_for(force, 0.2)
    expected = (
        ActuationResult.UNCERTAIN if isolation_fails else ActuationResult.ACCEPTED
    )
    assert forced.result is expected
    assert forced.error == ("isolation failed" if isolation_fails else None)
    if not preexisting:
        assert (await on).result is ActuationResult.UNCERTAIN
    on_release.set()


@pytest.mark.asyncio
async def test_force_receive_rejects_queued_on_without_dispatch_or_poison() -> None:
    actuator = FakeActuator()
    started, release = asyncio.Event(), asyncio.Event()
    poisoned: list[int] = []
    actuator.actions[ActuationOperation.PTT_ON] = blocking_action(
        started, release, resist_cancellation=True
    )
    lane = poison_lane(actuator, poisoned)
    active = asyncio.create_task(settle(lane, effect(attempt="active")))
    await asyncio.wait_for(started.wait(), 0.2)
    queued_effect = effect(attempt="queued")
    queued = asyncio.create_task(settle(lane, queued_effect))
    await asyncio.sleep(0)
    force = effect(ActuationOperation.FORCE_RECEIVE, epoch=4, attempt="off")
    await settle(lane, force)
    _, queued_result = await asyncio.gather(active, queued)
    assert queued_result.result is ActuationResult.REJECTED
    assert queued_result.error == "superseded before dispatch"
    assert queued_effect.token not in [token for token, _ in actuator.calls]
    await asyncio.sleep(0)
    assert poisoned == [7]
    release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,error",
    [
        (ActuationResult.ACCEPTED, None),
        (ActuationResult.REJECTED, "rejected"),
        (ActuationResult.UNCERTAIN, "uncertain"),
    ],
)
async def test_abort_binding(result: ActuationResult, error: str | None) -> None:
    actuator = FakeActuator()
    actuator.actions[AbortOperation.STOP_CW] = result
    lane = ManagedTxEffectLane(actuator)
    token = EffectToken(9, 5, "force")
    failed = await lane.settle_abort(
        token,
        AbortOperation.STOP_CW,
        deadline_monotonic=time.monotonic() + 1,
    )
    assert failed == (
        None if error is None else AbortFailed(token, AbortOperation.STOP_CW, error)
    )


@pytest.mark.asyncio
async def test_stale_lane_settlement_cannot_clear_current_release_debt() -> None:
    lane = ManagedTxEffectLane(FakeActuator())
    transition = reduce_managed_tx(ManagedTxState(), ForceOff(8, "current"))
    current = transition.state.pending_effect
    assert current is not None
    stale = effect(current.operation, 8, current.token.effect_epoch - 1, "stale")
    stale_settlement = await settle(lane, stale)
    reduced = reduce_managed_tx(transition.state, stale_settlement)
    assert reduced.outcome is ManagedTxOutcome.STALE
    assert reduced.state.release_required
