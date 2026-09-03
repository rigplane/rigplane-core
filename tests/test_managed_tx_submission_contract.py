import asyncio
from pathlib import Path

import pytest

from rigplane.core.state_pipeline_contracts import CommandIntent
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    ManagedTxIntentKind,
    ManagedTxOutcome,
)
from test_managed_tx_authority import authority


def intent(name: str, **params: object) -> CommandIntent:
    return CommandIntent(f"test-{name}", name, params, "test")


async def finish(managed: ManagedTxAuthority) -> None:
    state = (await managed.snapshot()).state
    if state.release_required or state.intent.kind is not ManagedTxIntentKind.RX:
        await managed.force_off()
    await managed.close()


class MembershipAck(asyncio.Future[None]):
    def __init__(self, fence: TxAbortFence, owner: str) -> None:
        super().__init__()
        self._fence = fence
        self._owner = owner

    def set_result(self, result: None) -> None:
        assert any(
            scope == self._owner
            for _cancellation, scope in self._fence._cancellations.values()
        )
        super().set_result(result)


@pytest.mark.asyncio
async def test_ptt_registration_ack_precedes_ready_and_closes_owner_cancel_race() -> (
    None
):
    managed, _, _, _, fence, lane = authority()
    ready = asyncio.get_running_loop().create_future()
    registered = MembershipAck(fence, "owner")
    admission = asyncio.create_task(
        managed.submit_ptt(
            True,
            "owner",
            ready=ready,
            _registered=registered,
        )
    )
    try:
        await asyncio.wait_for(asyncio.shield(registered), 0.2)
        assert not ready.done() and not admission.done() and not lane.effects

        release = await asyncio.wait_for(managed.submit_ptt(False, "owner"), 0.2)
        assert release.outcome is ManagedTxOutcome.REJECTED
        assert await release.wait_settlement() is None
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(admission, 0.2)
        assert not lane.effects
    finally:
        ready.cancel()
        await asyncio.gather(admission, return_exceptions=True)
        await finish(managed)


@pytest.mark.asyncio
async def test_ptt_registration_ack_settles_on_pre_registration_error() -> None:
    managed, _, _, _, _, _ = authority()
    registered = asyncio.get_running_loop().create_future()
    try:
        with pytest.raises(TypeError) as submission_error:
            await managed.submit_ptt(  # type: ignore[arg-type]
                True,
                7,
                _registered=registered,
            )
        with pytest.raises(TypeError) as registration_error:
            await asyncio.wait_for(registered, 0.2)
        assert registration_error.value is submission_error.value
    finally:
        await finish(managed)


@pytest.mark.asyncio
async def test_ptt_registration_ack_settles_on_pre_registration_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed, _, _, _, _, _ = authority()
    registered = asyncio.get_running_loop().create_future()

    def cancel_before_registration(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(managed, "_begin_ptt_operation", cancel_before_registration)
    try:
        with pytest.raises(asyncio.CancelledError):
            await managed.submit_ptt(True, "owner", _registered=registered)
        assert registered.cancelled()
    finally:
        await finish(managed)


@pytest.mark.asyncio
async def test_ptt_registration_ack_settles_when_submission_never_starts() -> None:
    managed, _, _, _, _, _ = authority()
    registered = asyncio.get_running_loop().create_future()
    pending = managed.submit_ptt(True, "owner", _registered=registered)
    submission = (
        pending if isinstance(pending, asyncio.Task) else asyncio.create_task(pending)
    )
    submission.cancel()
    try:
        await asyncio.gather(submission, return_exceptions=True)
        assert registered.cancelled()
    finally:
        if not registered.done():
            registered.cancel()
        await finish(managed)


@pytest.mark.asyncio
async def test_ptt_receipt_waits_for_predecessor_and_not_provider_settlement() -> None:
    managed, _, _, _, _, lane = authority()
    predecessor = asyncio.get_running_loop().create_future()
    provider_release = lane.block_next()
    admission = asyncio.create_task(
        managed.submit_ptt(True, "owner", ready=predecessor)
    )
    try:
        await asyncio.sleep(0)
        assert not admission.done() and not lane.effects

        predecessor.set_result(None)
        receipt = await asyncio.wait_for(admission, 0.2)
        effect = await asyncio.wait_for(lane.started.get(), 0.2)
        assert receipt.transition.outcome is ManagedTxOutcome.ACCEPTED
        assert effect.operation is ActuationOperation.PTT_ON
        assert not receipt.settlement_done
        assert (await managed.snapshot()).state.intent.kind is ManagedTxIntentKind.PTT

        provider_release.set()
        settled = await asyncio.wait_for(receipt.wait_settlement(), 0.2)
        assert settled is not None and settled.result is ActuationResult.ACCEPTED
    finally:
        predecessor.cancel()
        provider_release.set()
        await finish(managed)


@pytest.mark.asyncio
async def test_receipt_keeps_settlement_owned_when_a_waiter_is_cancelled() -> None:
    managed, _, _, _, _, lane = authority()
    provider_release = lane.block_next()
    try:
        receipt = await asyncio.wait_for(managed.submit_transmit_on(), 0.2)
        await asyncio.wait_for(lane.started.get(), 0.2)
        assert receipt.transition.outcome is ManagedTxOutcome.ACCEPTED
        assert not receipt.settlement_done
        assert len(managed._settlement_tasks) == 1

        waiter = asyncio.create_task(receipt.wait_settlement())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not receipt.settlement_done
        assert len(managed._settlement_tasks) == 1

        provider_release.set()
        settled = await asyncio.wait_for(receipt.wait_settlement(), 0.2)
        assert settled is not None and settled.result is ActuationResult.ACCEPTED
        assert receipt.settlement_done
        assert not managed._settlement_tasks
    finally:
        provider_release.set()
        await finish(managed)


@pytest.mark.asyncio
async def test_shutdown_drains_owned_settlement_before_provider_retirement() -> None:
    managed, _, _, _, _, lane = authority()
    provider_release = lane.block_next()
    retired: list[int] = []
    retire_started = asyncio.Event()

    async def retire(generation: int) -> None:
        retire_started.set()
        retired.append(generation)

    receipt = await asyncio.wait_for(managed.submit_transmit_on(), 0.2)
    await asyncio.wait_for(lane.started.get(), 0.2)
    shutdown = asyncio.create_task(
        managed.shutdown(retire_provider=retire, termination=asyncio.Event())
    )
    try:
        effect = await asyncio.wait_for(lane.started.get(), 0.2)
        assert effect.operation is ActuationOperation.FORCE_RECEIVE
        assert not receipt.settlement_done
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(retire_started.wait(), 0.02)
        assert not shutdown.done() and retired == []

        provider_release.set()
        await asyncio.wait_for(receipt.wait_settlement(), 0.2)
        await asyncio.wait_for(shutdown, 0.2)
        assert retired == [7]
        assert not managed._settlement_tasks
    finally:
        provider_release.set()
        if not shutdown.done():
            shutdown.cancel()
        await asyncio.gather(shutdown, return_exceptions=True)
        if not managed._closed:
            await finish(managed)


def test_private_ptt_operation_has_no_production_consumer() -> None:
    source = Path(__file__).parents[1] / "src"
    authority_path = source / "rigplane/runtime/managed_tx_authority.py"
    consumers = [
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if path != authority_path
        and "_start_ptt_operation" in path.read_text(encoding="utf-8")
    ]
    assert consumers == []


@pytest.mark.asyncio
async def test_force_off_receipt_changes_state_and_fence_before_settlement() -> None:
    managed, _, _, _, fence, lane = authority()
    try:
        assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
        await asyncio.wait_for(lane.started.get(), 0.2)
        old_epoch = fence.epoch
        provider_release = lane.block_next()

        receipt = await asyncio.wait_for(managed.submit_force_off(), 0.2)
        effect = await asyncio.wait_for(lane.started.get(), 0.2)
        state = (await managed.snapshot()).state
        assert receipt.transition.outcome is ManagedTxOutcome.ACCEPTED
        assert effect.operation is ActuationOperation.FORCE_RECEIVE
        assert state.intent.kind is ManagedTxIntentKind.RX
        assert state.release_required and fence.epoch == old_epoch + 1
        assert not receipt.settlement_done

        provider_release.set()
        await asyncio.wait_for(receipt.wait_settlement(), 0.2)
        assert not (await managed.snapshot()).state.release_required
    finally:
        for gate in lane.gates:
            if gate is not None:
                gate.set()
        await finish(managed)


@pytest.mark.asyncio
async def test_ptt_up_receipt_remains_owner_local() -> None:
    managed, _, _, _, _, lane = authority()
    try:
        assert await managed.ptt_down("owner-a") is ManagedTxOutcome.ACCEPTED

        rejected = await managed.submit_ptt(False, "owner-b")
        assert rejected.transition.outcome is ManagedTxOutcome.REJECTED
        assert await rejected.wait_settlement() is None
        held = (await managed.snapshot()).state
        assert held.intent.kind is ManagedTxIntentKind.PTT
        assert held.intent.owner_token == "owner-a"

        provider_release = lane.block_next()
        accepted = await asyncio.wait_for(managed.submit_ptt(False, "owner-a"), 0.2)
        assert accepted.transition.outcome is ManagedTxOutcome.ACCEPTED
        assert (await managed.snapshot()).state.intent.kind is ManagedTxIntentKind.RX
        assert not accepted.settlement_done
        provider_release.set()
        await asyncio.wait_for(accepted.wait_settlement(), 0.2)
    finally:
        for gate in lane.gates:
            if gate is not None:
                gate.set()
        await finish(managed)


@pytest.mark.asyncio
async def test_managed_write_policy_uses_intent_not_observed_ptt() -> None:
    managed, _, _, _, _, _ = authority()
    antenna_observed_on = intent(
        "set_antenna_1", on=True, observed_ptt="on", receiver=0
    )
    antenna_observed_off = intent(
        "set_antenna_1", on=True, observed_ptt="off", receiver=0
    )
    try:
        assert await managed.admit_managed_write(antenna_observed_on)
        assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
        assert not await managed.admit_managed_write(antenna_observed_on)
        assert not await managed.admit_managed_write(antenna_observed_off)
    finally:
        await finish(managed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "admitted"),
    [
        (intent("set_band", band="20m"), True),
        (intent("set_freq", freq_hz=14_200_000, receiver=1), True),
        (intent("set_mode", mode="USB", width=2400, receiver=1), True),
        (intent("set_vfo", vfo="VFOB"), True),
        (intent("set_split_vfo", on=True, tx_vfo="VFOB"), True),
        (intent("set_antenna_1", on=True), False),
        (intent("set_antenna_2", on=False), False),
        (intent("set_rx_antenna", enabled=True), False),
        (intent("set_tuner_status", value=1), False),
        (intent("set_tuner_status", value=2), False),
        (intent("set_tuner_status", value=0), True),
        (intent("set_func", func="TUNER", on=True), False),
        (intent("set_func", func="TUNER", on=False), True),
        (intent("force_off"), True),
        (intent("set_rf_gain", level=117, receiver=1), True),
    ],
)
async def test_managed_write_policy_is_the_single_relay_family_decision(
    command: CommandIntent, admitted: bool
) -> None:
    managed, _, _, _, _, _ = authority()
    before = command.to_dict()
    try:
        assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
        assert await managed.admit_managed_write(command) is admitted
        assert command.to_dict() == before
    finally:
        await finish(managed)
