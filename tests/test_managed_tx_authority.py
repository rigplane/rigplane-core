import asyncio
from collections import deque
from dataclasses import dataclass

import pytest

from rigplane.runtime.managed_tx_authority import ManagedTxAuthority, ShutdownResult
from rigplane.runtime.managed_tx_config import ManagedTxTotConfig
from rigplane.runtime.managed_tx_state import (
    AbortFailed,
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    ActuationSettled,
    EffectToken,
    ManagedTxEffect,
    ManagedTxIntentKind,
    ManagedTxOutcome,
    ReleasePlan,
)


@dataclass
class FakeClock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


class FakeWakeup:
    def __init__(self) -> None:
        self.revision = 0
        self.deadline: float | None = None
        self._entered: asyncio.Queue[int] = asyncio.Queue()
        self._gate = asyncio.Event()

    async def wait_until(self, deadline: float | None) -> None:
        self.revision += 1
        self.deadline = deadline
        self._entered.put_nowait(self.revision)
        gate = self._gate
        await gate.wait()
        if self._gate is gate:
            self._gate = asyncio.Event()

    def wake(self) -> None:
        self._gate.set()

    async def wait_after(self, revision: int) -> float | None:
        while self.revision <= revision:
            await asyncio.wait_for(self._entered.get(), 0.2)
        return self.deadline


class FakeConfigStore:
    def __init__(self, seconds: float | None = 10.0) -> None:
        self._config = ManagedTxTotConfig(seconds)
        self.values: list[object] = []
        self.fail = False

    @property
    def config(self) -> ManagedTxTotConfig:
        return self._config

    def set_timeout_seconds(self, value: object) -> ManagedTxTotConfig:
        self.values.append(value)
        if self.fail:
            raise OSError("persistence failed")
        seconds = None if value in (None, 0) else float(value)
        self._config = ManagedTxTotConfig(seconds)
        return self._config


# Event log shared between FakeFence and FakeLane within one test. Each
# entry is appended synchronously at the top of the corresponding fake
# method, before any gate/await, so its position in the log reflects call
# order rather than completion order -- what the fence-before-provider-I/O
# rule is actually about.
FenceOrderLog = list[tuple[str, object] | tuple[str]]


class FakeFence:
    def __init__(self, log: FenceOrderLog | None = None) -> None:
        self.calls = 0
        self.log: FenceOrderLog = log if log is not None else []

    async def force_off(self) -> None:
        self.calls += 1
        self.log.append(("fence",))


class FakeLane:
    def __init__(self, log: FenceOrderLog | None = None) -> None:
        self.results: deque[ActuationResult] = deque()
        self.effects: list[ManagedTxEffect] = []
        self.aborts: list[tuple[EffectToken, AbortOperation]] = []
        self.abort_results: dict[AbortOperation, ActuationResult] = {}
        self.stale_once = False
        self.gates: deque[asyncio.Event | None] = deque()
        self.started: asyncio.Queue[ManagedTxEffect] = asyncio.Queue()
        self.log: FenceOrderLog = log if log is not None else []

    def block_next(self) -> asyncio.Event:
        gate = asyncio.Event()
        self.gates.append(gate)
        return gate

    async def settle(
        self, effect: ManagedTxEffect, *, deadline_monotonic: float
    ) -> ActuationSettled:
        self.effects.append(effect)
        self.log.append(("effect", effect.operation))
        self.started.put_nowait(effect)
        result = self.results.popleft() if self.results else ActuationResult.ACCEPTED
        gate = self.gates.popleft() if self.gates else None
        if gate is not None:
            await gate.wait()
        token = effect.token
        if self.stale_once:
            self.stale_once = False
            token = EffectToken(
                token.provider_generation, token.effect_epoch - 1, "stale"
            )
        return ActuationSettled(
            token,
            effect.operation,
            result,
            None if result is ActuationResult.ACCEPTED else result.value,
        )

    async def settle_abort(
        self,
        token: EffectToken,
        operation: AbortOperation,
        *,
        deadline_monotonic: float,
    ) -> AbortFailed | None:
        self.aborts.append((token, operation))
        self.log.append(("abort", operation))
        result = self.abort_results.get(operation, ActuationResult.ACCEPTED)
        return (
            None
            if result is ActuationResult.ACCEPTED
            else AbortFailed(token, operation, result.value)
        )


def authority(
    *,
    generation: int | None = 7,
    seconds: float | None = 10.0,
) -> tuple[
    ManagedTxAuthority, FakeClock, FakeWakeup, FakeConfigStore, FakeFence, FakeLane
]:
    clock, wakeup = FakeClock(), FakeWakeup()
    log: FenceOrderLog = []
    store, fence, lane = FakeConfigStore(seconds), FakeFence(log), FakeLane(log)
    managed = ManagedTxAuthority(
        lane,
        store,
        fence,
        provider_generation=generation,
        clock=clock,
        wakeup=wakeup,
        attempt_timeout_seconds=1.0,
        retry_delay_seconds=2.0,
    )
    return managed, clock, wakeup, store, fence, lane


def _assert_force_off_precedes_provider_io(
    log: FenceOrderLog, since: int, *, prefix: tuple[str, ...] = ()
) -> None:
    """Pin the ForceOff ordering rule: within one full-force pass, the fence
    entry must be logged before the provider I/O (settle/settle_abort) that
    pass performs, and nothing but `prefix` may precede the fence.

    `since` bookmarks the log length right before the action that triggers
    the pass. `prefix` names the tags permitted before the fence entry in
    the window -- empty for a pass that starts with the fence, or
    `("effect",)` for the UNCERTAIN-followup case, where the on-attempt
    settle that itself decided to force off is logged first. Exactly one
    fence entry is required, everything before it must equal `prefix`
    exactly, and at least one provider-I/O entry must follow it.
    """
    window = log[since:]
    tags = [entry[0] for entry in window]
    assert tags.count("fence") == 1, f"expected exactly one fence entry, got {tags}"
    index = tags.index("fence")
    assert tuple(tags[:index]) == prefix, (
        f"unexpected provider I/O before the fence: {tags}"
    )
    after = window[index + 1 :]
    assert after, "expected provider I/O logged after the fence"


@pytest.mark.asyncio
async def test_ptt_owner_idempotency_and_disconnect_force_off() -> None:
    managed, clock, _, _, fence, lane = authority()
    assert await managed.ptt_down("owner-a") is ManagedTxOutcome.ACCEPTED
    first = await managed.snapshot()
    clock.now = 105
    assert await managed.ptt_down("owner-a") is ManagedTxOutcome.ACCEPTED
    assert await managed.ptt_down("owner-b") is ManagedTxOutcome.REJECTED
    assert await managed.ptt_up("owner-b") is ManagedTxOutcome.REJECTED
    assert (await managed.snapshot()).state == first.state
    since = len(fence.log)
    assert await managed.owner_disconnect("owner-a") is ManagedTxOutcome.ACCEPTED
    _assert_force_off_precedes_provider_io(fence.log, since)
    released = await managed.snapshot()
    assert released.state.intent.kind is ManagedTxIntentKind.RX
    assert not released.state.release_required
    assert fence.calls == 1
    assert [item.operation for item in lane.effects] == [
        ActuationOperation.PTT_ON,
        ActuationOperation.FORCE_RECEIVE,
    ]
    force = lane.effects[-1]
    assert lane.aborts == [
        (force.token, AbortOperation.STOP_CW),
        (force.token, AbortOperation.STOP_TUNE),
    ]
    await managed.close()


@pytest.mark.asyncio
async def test_transmit_is_latched_and_force_off_runs_one_abort_family() -> None:
    managed, clock, _, _, fence, lane = authority()
    assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
    started = await managed.snapshot()
    clock.now = 106
    assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
    assert await managed.owner_disconnect("browser") is ManagedTxOutcome.REJECTED
    assert (await managed.snapshot()).state == started.state
    since = len(fence.log)
    assert await managed.force_off() is ManagedTxOutcome.ACCEPTED
    _assert_force_off_precedes_provider_io(fence.log, since)
    assert fence.calls == 1
    force = lane.effects[-1]
    assert force.operation is ActuationOperation.FORCE_RECEIVE
    assert lane.aborts == [
        (force.token, AbortOperation.STOP_CW),
        (force.token, AbortOperation.STOP_TUNE),
    ]
    await managed.close()


@pytest.mark.asyncio
async def test_offline_force_off_retries_immediately_when_provider_appears() -> None:
    managed, _, _, _, fence, lane = authority(generation=None)
    since = len(fence.log)
    assert await managed.force_off() is ManagedTxOutcome.ACCEPTED
    # Provider offline: ForceOff's reducer emits no effect (see ForceOff
    # handling in managed_tx_state.reduce_managed_tx), so this full-force
    # pass has no provider I/O for the fence to precede -- only the fence
    # call itself is logged.
    assert fence.log[since:] == [("fence",)]
    assert (await managed.snapshot()).state.release_required
    assert lane.effects == [] and fence.calls == 1
    await managed.provider_available(8)
    assert not (await managed.snapshot()).state.release_required
    assert [effect.operation for effect in lane.effects] == [
        ActuationOperation.FORCE_RECEIVE
    ]
    assert lane.effects[-1].token.provider_generation == 8
    await managed.close()


@pytest.mark.asyncio
async def test_ptt_up_without_provider_retains_owner_scoped_release_debt() -> None:
    managed, _, _, _, fence, lane = authority(generation=7)
    assert await managed.ptt_down("owner-a") is ManagedTxOutcome.ACCEPTED

    async with managed._lock:
        managed._provider_generation = None

    assert await managed.ptt_up("owner-a") is ManagedTxOutcome.ACCEPTED
    released = await managed.snapshot()
    assert released.state.intent.kind is ManagedTxIntentKind.RX
    assert released.state.release_plan is ReleasePlan.PTT_RELEASE
    assert released.state.pending_effect is None
    assert fence.calls == 0
    assert [effect.operation for effect in lane.effects] == [ActuationOperation.PTT_ON]

    await managed.provider_available(8)
    assert [effect.operation for effect in lane.effects] == [
        ActuationOperation.PTT_ON,
        ActuationOperation.FORCE_RECEIVE,
    ]
    assert not (await managed.snapshot()).state.release_required
    await managed.close()


@pytest.mark.asyncio
async def test_rejected_on_retries_release_with_new_epoch() -> None:
    managed, clock, wakeup, _, _, lane = authority()
    lane.results.extend((ActuationResult.REJECTED, ActuationResult.ACCEPTED))
    assert await managed.ptt_down("owner") is ManagedTxOutcome.ACCEPTED
    failed = await managed.snapshot()
    assert failed.state.intent.kind is ManagedTxIntentKind.RX
    assert failed.state.release_plan is ReleasePlan.FORCE_RELEASE
    retry_epoch = failed.state.effect_epoch + 1
    revision = wakeup.revision
    clock.now += 2
    wakeup.wake()
    await wakeup.wait_after(revision + 1)
    assert lane.effects[-1].token.effect_epoch == retry_epoch
    assert not (await managed.snapshot()).state.release_required
    await managed.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ingress", "operation"),
    [
        ("ptt", ActuationOperation.PTT_ON),
        ("transmit", ActuationOperation.TRANSMIT_ON),
    ],
)
@pytest.mark.parametrize(
    ("release", "cleared"),
    [
        (ActuationResult.ACCEPTED, True),
        (ActuationResult.REJECTED, False),
        (ActuationResult.UNCERTAIN, False),
    ],
)
async def test_uncertain_on_immediately_runs_one_force_off_family(
    ingress: str,
    operation: ActuationOperation,
    release: ActuationResult,
    cleared: bool,
) -> None:
    managed, _, _, _, fence, lane = authority()
    lane.results.extend((ActuationResult.UNCERTAIN, release))
    since = len(fence.log)
    outcome = (
        await managed.ptt_down("owner")
        if ingress == "ptt"
        else await managed.transmit_on()
    )
    assert outcome is ManagedTxOutcome.ACCEPTED
    _assert_force_off_precedes_provider_io(fence.log, since, prefix=("effect",))
    assert fence.calls == 1
    assert [effect.operation for effect in lane.effects] == [
        operation,
        ActuationOperation.FORCE_RECEIVE,
    ]
    force = lane.effects[-1]
    assert force.token.effect_epoch == lane.effects[0].token.effect_epoch + 1
    assert lane.aborts == [(force.token, operation) for operation in AbortOperation]
    assert (await managed.snapshot()).state.release_required is not cleared
    if not cleared:
        await managed.force_off()
    await managed.close()


@pytest.mark.asyncio
async def test_stale_on_settlement_does_not_run_force_off() -> None:
    managed, _, _, _, fence, lane = authority()
    lane.stale_once = True
    lane.results.append(ActuationResult.UNCERTAIN)
    await managed.ptt_down("owner")
    assert fence.calls == 0 and len(lane.effects) == 1
    await managed.force_off()
    await managed.close()


@pytest.mark.asyncio
async def test_stale_effect_settlement_cannot_clear_current_debt() -> None:
    managed, _, _, _, _, lane = authority()
    lane.stale_once = True
    await managed.force_off()
    projected = await managed.snapshot()
    assert projected.state.release_required
    assert projected.state.pending_effect is not None
    await managed.force_off()
    await managed.close()


@pytest.mark.asyncio
async def test_tot_expiry_uses_the_single_scheduler_and_force_off() -> None:
    managed, clock, wakeup, _, fence, lane = authority(seconds=10)
    await managed.ptt_down("owner")
    revision = wakeup.revision
    assert (await managed.snapshot()).remaining_tot_seconds == 10
    clock.now = 110
    since = len(fence.log)
    wakeup.wake()
    await wakeup.wait_after(revision + 1)
    _assert_force_off_precedes_provider_io(fence.log, since)
    projected = await managed.snapshot()
    assert projected.state.intent.kind is ManagedTxIntentKind.RX
    assert projected.remaining_tot_seconds is None
    assert fence.calls == 1
    assert lane.effects[-1].operation is ActuationOperation.FORCE_RECEIVE
    await managed.close()


@pytest.mark.asyncio
async def test_live_tot_edit_persists_then_recomputes_from_original_start() -> None:
    managed, clock, _, store, _, _ = authority(seconds=10)
    await managed.ptt_down("owner")
    original_start = (await managed.snapshot()).state.tx_started_at_monotonic
    clock.now = 105
    assert (await managed.set_tot_seconds(20)).timeout_seconds == 20
    projected = await managed.snapshot()
    assert store.values == [20]
    assert projected.state.tx_started_at_monotonic == original_start
    assert projected.state.tot_deadline_monotonic == 120
    assert projected.remaining_tot_seconds == 15
    assert (await managed.set_tot_seconds(None)).timeout_seconds is None
    assert (await managed.snapshot()).remaining_tot_seconds is None
    await managed.force_off()
    await managed.close()


@pytest.mark.asyncio
async def test_live_tot_edit_at_or_below_elapsed_forces_off_immediately() -> None:
    managed, clock, _, store, fence, lane = authority(seconds=20)
    await managed.ptt_down("owner")
    clock.now = 105
    since = len(fence.log)
    assert (await managed.set_tot_seconds(5)).timeout_seconds == 5
    _assert_force_off_precedes_provider_io(fence.log, since)
    projected = await managed.snapshot()
    assert store.values == [5]
    assert projected.state.intent.kind is ManagedTxIntentKind.RX
    assert projected.remaining_tot_seconds is None
    assert fence.calls == 1
    assert lane.effects[-1].operation is ActuationOperation.FORCE_RECEIVE
    await managed.close()


@pytest.mark.asyncio
async def test_config_failure_does_not_activate_value() -> None:
    managed, _, _, store, _, _ = authority(seconds=10)
    await managed.ptt_down("owner")
    before = await managed.snapshot()
    store.fail = True
    with pytest.raises(OSError, match="persistence failed"):
        await managed.set_tot_seconds(30)
    after = await managed.snapshot()
    assert after.configured_tot_seconds == 10
    assert after.state.tot_deadline_monotonic == before.state.tot_deadline_monotonic
    await managed.force_off()
    await managed.close()


@pytest.mark.asyncio
async def test_simultaneous_tot_and_retry_due_chooses_force_off() -> None:
    managed, clock, _, _, fence, lane = authority()
    await managed.ptt_down("owner")
    managed._retry_due = 110
    clock.now = 110
    since = len(fence.log)
    await managed._process_due()
    _assert_force_off_precedes_provider_io(fence.log, since)
    assert fence.calls == 1
    assert lane.effects[-1].operation is ActuationOperation.FORCE_RECEIVE
    await managed.close()


@pytest.mark.asyncio
async def test_repeated_force_off_is_accepted_and_advances_epoch() -> None:
    managed, _, _, _, fence, lane = authority()
    since = len(fence.log)
    assert await managed.force_off() is ManagedTxOutcome.ACCEPTED
    _assert_force_off_precedes_provider_io(fence.log, since)
    first = lane.effects[-1].token.effect_epoch
    since = len(fence.log)
    assert await managed.force_off() is ManagedTxOutcome.ACCEPTED
    _assert_force_off_precedes_provider_io(fence.log, since)
    assert lane.effects[-1].token.effect_epoch == first + 1
    assert fence.calls == 2
    await managed.close()


@pytest.mark.asyncio
async def test_generation_is_monotonic_and_wakes_debt() -> None:
    managed, _, _, _, fence, lane = authority(generation=7)
    await managed.provider_unavailable()
    await managed.provider_unavailable()
    assert fence.calls == 0 and lane.effects == []
    with pytest.raises(ValueError, match="increase"):
        await managed.provider_available(6)
    await managed.provider_available(8)
    assert (await managed.snapshot()).provider_generation == 8
    with pytest.raises(RuntimeError, match="unavailable first"):
        await managed.provider_available(9)
    await managed.close()


@pytest.mark.asyncio
async def test_stale_old_release_acceptance_cannot_clear_replacement_debt() -> None:
    managed, _, _, _, _, lane = authority(generation=7)
    lane.results.extend((ActuationResult.ACCEPTED, ActuationResult.REJECTED))
    old_gate = lane.block_next()
    old_release = asyncio.create_task(managed.force_off())
    old_effect = await asyncio.wait_for(lane.started.get(), 0.2)
    assert old_effect.token.provider_generation == 7

    await managed.provider_unavailable()
    await managed.provider_available(8)
    current = lane.effects[-1]
    assert current.operation is ActuationOperation.FORCE_RECEIVE
    assert current.token.provider_generation == 8
    assert (await managed.snapshot()).state.release_required

    old_gate.set()
    await old_release
    projected = await managed.snapshot()
    assert (
        projected.state.release_required and projected.state.last_actuation is not None
    )
    assert projected.state.last_actuation.attempt_id == current.token.attempt_id

    await managed.force_off()
    await managed.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ingress", "operation"),
    [
        ("ptt", ActuationOperation.PTT_ON),
        ("transmit", ActuationOperation.TRANSMIT_ON),
    ],
)
@pytest.mark.parametrize("shutdown", [False, True], ids=["replacement", "shutdown"])
async def test_active_replacement_never_replays_on(
    ingress: str, operation: ActuationOperation, shutdown: bool
) -> None:
    managed, _, _, _, fence, lane = authority(generation=7)
    outcome = (
        await managed.ptt_down("owner")
        if ingress == "ptt"
        else await managed.transmit_on()
    )
    assert outcome is ManagedTxOutcome.ACCEPTED

    retired: list[int] = []
    if shutdown:
        assert (
            await managed.shutdown(
                retire_provider=lambda generation: _append_async(retired, generation),
                termination=asyncio.Event(),
            )
            is ShutdownResult.DRAINED
        )
    else:
        await managed.provider_unavailable()
        assert (await managed.snapshot()).provider_generation is None
        assert (await managed.snapshot()).state.release_required
        await managed.provider_available(8)

    assert [effect.operation for effect in lane.effects] == [
        operation,
        ActuationOperation.FORCE_RECEIVE,
    ]
    assert lane.effects[-1].token.provider_generation == (7 if shutdown else 8)
    assert fence.calls == 1
    assert retired == ([7] if shutdown else [])
    assert not (await managed.snapshot()).state.release_required
    if not shutdown:
        await managed.close()


@pytest.mark.asyncio
async def test_inflight_on_settlement_is_stale_after_replacement() -> None:
    managed, _, _, _, fence, lane = authority(generation=7)
    on_gate = lane.block_next()
    pending_on = asyncio.create_task(managed.transmit_on())
    old_on = await asyncio.wait_for(lane.started.get(), 0.2)
    assert old_on.operation is ActuationOperation.TRANSMIT_ON

    await managed.provider_unavailable()
    await managed.provider_available(8)
    on_gate.set()
    assert await pending_on is ManagedTxOutcome.ACCEPTED

    assert [effect.operation for effect in lane.effects] == [
        ActuationOperation.TRANSMIT_ON,
        ActuationOperation.FORCE_RECEIVE,
    ]
    assert lane.effects[-1].token.provider_generation == 8
    assert fence.calls == 1
    assert not (await managed.snapshot()).state.release_required
    await managed.close()


@pytest.mark.asyncio
async def test_provider_arrival_wins_retry_tie_without_duplicate_effect() -> None:
    managed, clock, _, _, _, lane = authority(generation=None)
    await managed._stop_scheduler(managed._scheduler_task)
    await managed.force_off()
    managed._retry_due = clock.now

    release_gate = lane.block_next()
    arrival = asyncio.create_task(managed.provider_available(8))
    try:
        pending = await asyncio.wait_for(lane.started.get(), 0.2)
        due = asyncio.create_task(managed._process_due())
        await asyncio.wait_for(due, 0.2)
        retry_due_while_pending = managed._retry_due
    finally:
        release_gate.set()
        await arrival

    assert len(lane.effects) == 1
    assert lane.effects[0] == pending
    assert pending.operation is ActuationOperation.FORCE_RECEIVE
    assert pending.token.provider_generation == 8
    assert retry_due_while_pending is None
    settled = await managed.snapshot()
    assert not settled.state.release_required
    assert settled.state.pending_effect is None
    assert managed._retry_due is None
    await managed.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_result", [ActuationResult.REJECTED, ActuationResult.UNCERTAIN]
)
async def test_shutdown_retries_each_nonacceptance_before_retirement(
    first_result: ActuationResult,
) -> None:
    managed, clock, wakeup, _, _, lane = authority(generation=7)
    lane.results.extend((first_result, ActuationResult.ACCEPTED))
    retired: list[int] = []

    async def retire(generation: int) -> None:
        retired.append(generation)

    task = asyncio.create_task(
        managed.shutdown(retire_provider=retire, termination=asyncio.Event())
    )
    await asyncio.wait_for(lane.started.get(), 0.2)
    while managed._retry_due is None:
        await asyncio.sleep(0)
    assert retired == []
    revision = wakeup.revision
    clock.now = managed._retry_due
    wakeup.wake()
    await wakeup.wait_after(revision + 1)

    assert await asyncio.wait_for(task, 0.2) is ShutdownResult.DRAINED
    assert retired == [7]
    assert [effect.operation for effect in lane.effects] == [
        ActuationOperation.FORCE_RECEIVE,
        ActuationOperation.FORCE_RECEIVE,
    ]
    assert not (await managed.snapshot()).state.release_required
    assert managed._scheduler_task.done()


@pytest.mark.asyncio
async def test_offline_shutdown_drains_after_replacement_arrives() -> None:
    managed, _, _, _, _, lane = authority(generation=None)
    retired: list[int] = []

    async def retire(generation: int) -> None:
        retired.append(generation)

    task = asyncio.create_task(
        managed.shutdown(retire_provider=retire, termination=asyncio.Event())
    )
    await asyncio.sleep(0)
    assert (await managed.snapshot()).state.release_required
    assert retired == [] and not task.done()

    await managed.provider_available(8)
    assert await asyncio.wait_for(task, 0.2) is ShutdownResult.DRAINED
    assert retired == [8]
    assert [effect.operation for effect in lane.effects] == [
        ActuationOperation.FORCE_RECEIVE
    ]


@pytest.mark.asyncio
async def test_replacement_during_shutdown_stales_old_release() -> None:
    managed, _, _, _, _, lane = authority(generation=7)
    old_gate = lane.block_next()
    shutdown = asyncio.create_task(
        managed.shutdown(
            retire_provider=lambda _generation: asyncio.sleep(0),
            termination=asyncio.Event(),
        )
    )
    old = await asyncio.wait_for(lane.started.get(), 0.2)
    assert old.token.provider_generation == 7

    await managed.provider_unavailable()
    await managed.provider_available(8)
    old_gate.set()

    assert await asyncio.wait_for(shutdown, 0.2) is ShutdownResult.DRAINED
    assert lane.effects[-1].token.provider_generation == 8
    assert not (await managed.snapshot()).state.release_required


@pytest.mark.asyncio
async def test_termination_before_initial_release_acceptance_preserves_debt() -> None:
    managed, _, _, _, _, lane = authority(generation=7)
    release_gate = lane.block_next()
    termination = asyncio.Event()
    retired: list[int] = []
    shutdown = asyncio.create_task(
        managed.shutdown(
            retire_provider=lambda generation: _append_async(retired, generation),
            termination=termination,
        )
    )
    await asyncio.wait_for(lane.started.get(), 0.2)
    before = await managed.snapshot()
    termination.set()
    asyncio.get_running_loop().call_soon(release_gate.set)
    result = await asyncio.wait_for(shutdown, 0.2)

    assert result is ShutdownResult.TERMINATED
    assert retired == []
    assert await managed.snapshot() == before
    assert managed._scheduler_task.done()
    with pytest.raises(RuntimeError, match="clean RX state"):
        await managed.close()


@pytest.mark.asyncio
async def test_termination_poisons_a_concurrent_arrival_settlement() -> None:
    managed, _, _, _, _, lane = authority(generation=7)
    lane.results.append(ActuationResult.REJECTED)
    termination = asyncio.Event()
    retired: list[int] = []
    shutdown = asyncio.create_task(
        managed.shutdown(
            retire_provider=lambda generation: _append_async(retired, generation),
            termination=termination,
        )
    )
    await asyncio.wait_for(lane.started.get(), 0.2)
    while managed._retry_due is None:
        await asyncio.sleep(0)

    await managed.provider_unavailable()
    arrival_gate = lane.block_next()
    arrival = asyncio.create_task(managed.provider_available(8))
    current_release = await asyncio.wait_for(lane.started.get(), 0.2)
    assert current_release.token.provider_generation == 8
    termination.set()

    assert await asyncio.wait_for(shutdown, 0.2) is ShutdownResult.TERMINATED
    before = await managed.snapshot()
    assert before.state.release_required and retired == []
    assert managed._scheduler_task.done()
    arrival_gate.set()
    await arrival
    assert await managed.snapshot() == before
    with pytest.raises(RuntimeError, match="clean RX state"):
        await managed.close()


@pytest.mark.asyncio
async def test_retirement_failure_does_not_close_authority() -> None:
    managed, _, _, _, _, _ = authority(generation=7)
    failure = RuntimeError("retirement failed")

    async def fail_retirement(_generation: int) -> None:
        raise failure

    with pytest.raises(RuntimeError, match="retirement failed") as raised:
        await managed.shutdown(
            retire_provider=fail_retirement, termination=asyncio.Event()
        )
    assert raised.value is failure
    assert not managed._scheduler_task.done()
    await managed.close()


@pytest.mark.asyncio
async def test_shutdown_is_shielded_idempotent_and_blocks_new_ingress() -> None:
    managed, _, _, _, _, lane = authority(generation=7)
    gate = lane.block_next()
    retired: list[int] = []
    termination = asyncio.Event()

    async def retire(generation: int) -> None:
        retired.append(generation)

    first = asyncio.create_task(
        managed.shutdown(retire_provider=retire, termination=termination)
    )
    await asyncio.wait_for(lane.started.get(), 0.2)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)

    with pytest.raises(RuntimeError, match="shutting down"):
        await managed.ptt_down("owner")
    second = asyncio.create_task(
        managed.shutdown(retire_provider=retire, termination=termination)
    )
    gate.set()

    assert await asyncio.wait_for(second, 0.2) is ShutdownResult.DRAINED
    assert retired == [7]


async def _append_async(values: list[int], value: int) -> None:
    values.append(value)


@pytest.mark.asyncio
async def test_close_refuses_active_debt_or_pending_without_mutation() -> None:
    managed, _, _, _, fence, lane = authority()
    scheduler = managed._scheduler_task
    await managed.transmit_on()
    for prepare in (
        None,
        ActuationResult.REJECTED,
        "stale",
    ):
        if prepare is ActuationResult.REJECTED:
            await managed.force_off()
            lane.results.append(prepare)
            await managed.ptt_down("owner")
        elif prepare == "stale":
            await managed.force_off()
            lane.stale_once = True
            await managed.force_off()
        state, calls, effects = managed._state, fence.calls, tuple(lane.effects)
        with pytest.raises(RuntimeError, match="clean RX state"):
            await managed.close()
        assert managed._state is state
        assert managed._scheduler_task is scheduler and not scheduler.done()
        assert (fence.calls, tuple(lane.effects)) == (calls, effects)
    await managed.force_off()
    await managed.close()


@pytest.mark.asyncio
async def test_close_disposes_clean_scheduler_idempotently_without_effects() -> None:
    managed, _, _, _, fence, lane = authority()
    scheduler, state = managed._scheduler_task, managed._state
    await managed.close()
    assert scheduler.done() and managed._state is state
    assert fence.calls == 0 and lane.effects == [] and lane.aborts == []
    await managed.close()
    assert fence.calls == 0


@pytest.mark.asyncio
async def test_ten_thousand_idempotent_commands_keep_one_scheduler() -> None:
    managed, _, _, _, _, lane = authority()
    scheduler = managed._scheduler_task
    await managed.transmit_on()
    for _ in range(10_000):
        assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
    assert managed._scheduler_task is scheduler and not scheduler.done()
    assert [effect.operation for effect in lane.effects] == [
        ActuationOperation.TRANSMIT_ON
    ]
    assert not hasattr(managed, "observed_ptt")
    await managed.force_off()
    await managed.close()
