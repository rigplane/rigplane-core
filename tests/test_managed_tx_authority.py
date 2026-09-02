import asyncio
from collections import deque
from dataclasses import dataclass

import pytest

from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
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


class FakeFence:
    def __init__(self) -> None:
        self.calls = 0

    async def force_off(self) -> None:
        self.calls += 1


class FakeLane:
    def __init__(self) -> None:
        self.results: deque[ActuationResult] = deque()
        self.effects: list[ManagedTxEffect] = []
        self.aborts: list[tuple[EffectToken, AbortOperation]] = []
        self.abort_results: dict[AbortOperation, ActuationResult] = {}
        self.stale_once = False

    async def settle(
        self, effect: ManagedTxEffect, *, deadline_monotonic: float
    ) -> ActuationSettled:
        self.effects.append(effect)
        result = self.results.popleft() if self.results else ActuationResult.ACCEPTED
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
    store, fence, lane = FakeConfigStore(seconds), FakeFence(), FakeLane()
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
    assert await managed.owner_disconnect("owner-a") is ManagedTxOutcome.ACCEPTED
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
    assert await managed.force_off() is ManagedTxOutcome.ACCEPTED
    assert fence.calls == 1
    force = lane.effects[-1]
    assert force.operation is ActuationOperation.FORCE_RECEIVE
    assert lane.aborts == [
        (force.token, AbortOperation.STOP_CW),
        (force.token, AbortOperation.STOP_TUNE),
    ]
    await managed.close()


@pytest.mark.asyncio
async def test_offline_force_off_retries_when_provider_appears() -> None:
    managed, _, wakeup, _, fence, lane = authority(generation=None)
    assert await managed.force_off() is ManagedTxOutcome.ACCEPTED
    assert (await managed.snapshot()).state.release_required
    assert lane.effects == [] and fence.calls == 1
    revision = wakeup.revision
    await managed.set_provider_generation(8)
    await wakeup.wait_after(revision + 1)
    assert not (await managed.snapshot()).state.release_required
    assert lane.effects[-1].operation is ActuationOperation.FORCE_RECEIVE
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
    outcome = (
        await managed.ptt_down("owner")
        if ingress == "ptt"
        else await managed.transmit_on()
    )
    assert outcome is ManagedTxOutcome.ACCEPTED
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
    wakeup.wake()
    await wakeup.wait_after(revision + 1)
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
    assert (await managed.set_tot_seconds(5)).timeout_seconds == 5
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
    await managed._process_due()
    assert fence.calls == 1
    assert lane.effects[-1].operation is ActuationOperation.FORCE_RECEIVE
    await managed.close()


@pytest.mark.asyncio
async def test_repeated_force_off_is_accepted_and_advances_epoch() -> None:
    managed, _, _, _, fence, lane = authority()
    assert await managed.force_off() is ManagedTxOutcome.ACCEPTED
    first = lane.effects[-1].token.effect_epoch
    assert await managed.force_off() is ManagedTxOutcome.ACCEPTED
    assert lane.effects[-1].token.effect_epoch == first + 1
    assert fence.calls == 2
    await managed.close()


@pytest.mark.asyncio
async def test_generation_is_monotonic_and_wakes_debt() -> None:
    managed, _, _, _, _, _ = authority(generation=7)
    await managed.set_provider_generation(None)
    with pytest.raises(ValueError, match="increase"):
        await managed.set_provider_generation(6)
    await managed.set_provider_generation(8)
    assert (await managed.snapshot()).provider_generation == 8
    await managed.close()


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
