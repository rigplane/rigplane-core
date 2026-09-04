import asyncio
import gc
import inspect
from pathlib import Path
from unittest.mock import create_autospec, patch

import pytest

from rigplane.core.state_pipeline_contracts import CommandIntent
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    ManagedTxIntentKind,
    ManagedTxOutcome,
)
from test_managed_tx_authority import authority
from rigplane.runtime._poller_types import CommandQueue


def intent(name: str, **params: object) -> CommandIntent:
    return CommandIntent(f"test-{name}", name, params, "test")


async def finish(managed: ManagedTxAuthority) -> None:
    state = (await managed.snapshot()).state
    if state.release_required or state.intent.kind is not ManagedTxIntentKind.RX:
        await managed.force_off()
    await managed.close()


async def wait_for_submission_cleanup(managed: ManagedTxAuthority) -> None:
    for _ in range(10):
        if not managed._abort_fence._cancellations and not managed._settlement_tasks:
            return
        await asyncio.sleep(0)


def test_command_queue_binds_one_non_null_connection_generation_source() -> None:
    queue = CommandQueue()
    with pytest.raises(RuntimeError, match="not bound"):
        queue.capture_connection_generation()

    current: object | None = "connection-1"
    def capture() -> object | None:
        return current

    queue.bind_connection_generation(capture)
    assert queue.capture_connection_generation() == "connection-1"
    with pytest.raises(RuntimeError, match="already bound"):
        queue.bind_connection_generation(lambda: "connection-2")

    current = None
    with pytest.raises(RuntimeError, match="unavailable"):
        queue.capture_connection_generation()

    def wrong_capture() -> object | None:
        return current

    with pytest.raises(RuntimeError, match="another consumer"):
        queue.unbind_connection_generation(wrong_capture)
    queue.unbind_connection_generation(capture)
    with pytest.raises(RuntimeError, match="not bound"):
        queue.capture_connection_generation()

    def replacement() -> object:
        return "connection-2"

    queue.bind_connection_generation(replacement)
    assert queue.capture_connection_generation() == "connection-2"


@pytest.mark.asyncio
async def test_submit_ptt_remains_a_coroutine_accepted_by_create_task() -> None:
    managed, _, _, _, fence, lane = authority()
    ready = asyncio.get_running_loop().create_future()
    assert inspect.iscoroutinefunction(ManagedTxAuthority.submit_ptt)
    coroutine = managed.submit_ptt(True, "owner", ready=ready)
    assert inspect.iscoroutine(coroutine)
    admission = asyncio.create_task(coroutine)
    try:
        await asyncio.sleep(0)
        assert fence._cancellations and not lane.effects
        admission.cancel()
        await asyncio.gather(admission, return_exceptions=True)
        await wait_for_submission_cleanup(managed)
        assert not fence._cancellations and not managed._settlement_tasks
    finally:
        ready.cancel()
        await asyncio.gather(admission, return_exceptions=True)
        await finish(managed)


@pytest.mark.asyncio
async def test_unawaited_submit_ptt_coroutine_is_inert() -> None:
    managed, _, _, _, fence, lane = authority()
    ready = asyncio.get_running_loop().create_future()
    pending = managed.submit_ptt(True, "owner", ready=ready)
    try:
        assert inspect.iscoroutine(pending)
        assert not fence._cancellations and not managed._settlement_tasks
        pending.close()
        ready.set_result(None)
        await asyncio.sleep(0)
        assert not lane.effects
    finally:
        if isinstance(pending, asyncio.Task):
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        elif inspect.iscoroutine(pending):
            pending.close()
        if not ready.done():
            ready.cancel()
        await finish(managed)


@pytest.mark.asyncio
async def test_start_ptt_submission_registers_membership_before_return() -> None:
    managed, _, _, _, fence, lane = authority()
    ready = asyncio.get_running_loop().create_future()
    submission = None
    try:
        submission = managed.start_ptt_submission(True, "owner", ready=ready)
        assert isinstance(submission, asyncio.Task)
        assert any(
            scope == "owner" for _cancellation, scope in fence._cancellations.values()
        )
        assert len(managed._settlement_tasks) == 1
        assert not ready.done() and not lane.effects
    finally:
        if submission is not None:
            submission.cancel()
            await asyncio.gather(submission, return_exceptions=True)
        ready.cancel()
        await wait_for_submission_cleanup(managed)
        await finish(managed)


@pytest.mark.asyncio
async def test_absolute_expiry_rejects_transmit_before_admission_or_wire() -> None:
    managed, clock, _, _, fence, lane = authority()
    ready = asyncio.get_running_loop().create_future()
    submission = managed.start_transmit_on_submission(
        ready=ready,
        expires_at_monotonic=clock.now,
    )
    try:
        assert fence._cancellations and not lane.effects
        ready.set_result(None)
        receipt = await asyncio.wait_for(submission, 0.2)
        assert receipt.outcome is ManagedTxOutcome.REJECTED
        assert await receipt.wait_settlement() is None
        assert not lane.effects
    finally:
        if not ready.done():
            ready.cancel()
        await finish(managed)


@pytest.mark.asyncio
async def test_start_ptt_immediate_cancel_drains_before_ready_can_run() -> None:
    managed, _, _, _, fence, lane = authority()
    ready = asyncio.get_running_loop().create_future()
    submission = managed.start_ptt_submission(True, "owner", ready=ready)
    submission.cancel()
    ready.set_result(None)
    try:
        await asyncio.gather(submission, return_exceptions=True)
        await wait_for_submission_cleanup(managed)
        assert not fence._cancellations
        assert not managed._settlement_tasks
        assert not lane.effects
    finally:
        await finish(managed)


@pytest.mark.asyncio
async def test_ignored_closed_start_task_is_owned_but_await_still_raises() -> None:
    managed, _, _, _, fence, _ = authority()
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        await managed.close()
        ignored = managed.start_ptt_submission(True, "ignored")
        for _ in range(10):
            if ignored.done():
                break
            await asyncio.sleep(0)
        assert ignored.done() and not unhandled
        del ignored
        gc.collect()
        await asyncio.sleep(0)
        assert not unhandled

        awaited = managed.start_ptt_submission(True, "awaited")
        with pytest.raises(RuntimeError, match="closed") as raised:
            await awaited
        assert raised.value is awaited.exception()
        await wait_for_submission_cleanup(managed)
        assert not fence._cancellations and not managed._settlement_tasks
        assert not unhandled
    finally:
        loop.set_exception_handler(previous_handler)
        await managed.close()


@pytest.mark.asyncio
async def test_same_owner_t0_cancels_started_t1_before_ready() -> None:
    managed, _, _, _, fence, lane = authority()
    ready = asyncio.get_running_loop().create_future()
    t1 = managed.start_ptt_submission(True, "owner", ready=ready)
    try:
        assert fence._cancellations and not lane.effects
        t0 = managed.start_ptt_submission(False, "owner")
        release = await asyncio.wait_for(t0, 0.2)
        assert release.outcome is ManagedTxOutcome.REJECTED
        assert await release.wait_settlement() is None
        with pytest.raises(asyncio.CancelledError):
            await t1
        ready.set_result(None)
        await wait_for_submission_cleanup(managed)
        assert not fence._cancellations and not managed._settlement_tasks
        assert not lane.effects
    finally:
        if not ready.done():
            ready.cancel()
        await asyncio.gather(t1, return_exceptions=True)
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
    "name",
    [
        "set_antenna",
        "set_antenna_1",
        "set_antenna_2",
        "set_rx_antenna",
        "set_rx_antenna_ant1",
        "set_rx_antenna_ant2",
    ],
)
async def test_managed_antenna_alias_policy_uses_one_descriptor_lookup(
    name: str,
) -> None:
    from rigplane.core.command_dispatch import command_descriptor

    managed, _, _, _, _, _ = authority()
    command = intent(name, enabled=True)
    try:
        assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
        with patch(
            "rigplane.runtime.managed_tx_authority.command_descriptor",
            wraps=command_descriptor,
        ) as lookup:
            assert not await managed.admit_managed_write(command)
        lookup.assert_called_once_with(name)
    finally:
        await finish(managed)


@pytest.mark.asyncio
async def test_civ_output_executes_unchanged_during_managed_transmit() -> None:
    from rigplane.core.command_dispatch import (
        bind_command_intent,
        execute_command_intent,
    )

    managed, _, _, _, _, _ = authority()

    class CivOutputRadio:
        async def set_civ_output_ant(self, on: bool) -> None: ...

    radio = create_autospec(CivOutputRadio, instance=True)
    command = bind_command_intent("set_civ_output_ant", {"on": True}, source="test")
    try:
        assert await managed.transmit_on() is ManagedTxOutcome.ACCEPTED
        await execute_command_intent(radio, command, managed_tx_authority=managed)
        radio.set_civ_output_ant.assert_awaited_once_with(on=True)
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
        (intent("set_rx_antenna", antenna=2, on=True), False),
        (intent("set_rx_antenna_ant1", on=True), False),
        (intent("set_rx_antenna_ant2", on=False), False),
        (intent("set_civ_output_ant", on=True), True),
        (intent("set_tuner_status", value=1), False),
        (intent("set_tuner_status", value=2), False),
        (intent("set_tuner_status", value=0), True),
        (intent("set_tuner_status", value=False), False),
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
