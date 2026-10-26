"""Simulated source ordering and dispatch regressions for the TX permit."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.core.command_service import (
    CommandExecutionResult,
    CommandService,
    command_intent_from_request,
)
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.core.tx_target import KnownTxTarget, current_tx_write_invalidation
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.tx_interlock import (
    TxInterlockRefusal,
    require_tx_frequency_permit,
)
from rigplane.runtime import _poller_types as commands


def _observe(store, path, value, timestamp=None):
    store.apply_current(
        Observation(
            path=path,
            value=value,
            source=SourceMetadata(source="test", provider="simulated"),
            timestamp_monotonic=time.monotonic() if timestamp is None else timestamp,
            max_age=10.0,
            clock_domain="monotonic",
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "cancel", "readback"])
async def test_dispatch_invalidates_before_uncertain_write_and_preserves_readback(
    outcome,
):
    store = StateStore()
    target = FieldPath.global_("tx_state", "tx_target")
    frequency = FieldPath.active("0", "freq_mode", "freq_hz")
    unrelated = FieldPath.active("1", "freq_mode", "freq_hz")
    _observe(store, target, KnownTxTarget("MAIN", "A", 146_520_000))
    _observe(store, frequency, 146_520_000)
    _observe(store, unrelated, 446_000_000)
    before = store.snapshot().field(unrelated)
    old_read_start = time.monotonic()

    async def execute(intent):
        # Simulate the provider's physical commit hook, before awaiting ACK.
        invalidate = current_tx_write_invalidation()
        assert invalidate is not None
        invalidate()
        # A poll issued before dispatch returns while this write is in flight.
        _observe(store, frequency, 146_520_000, old_read_start)
        with pytest.raises(TxInterlockRefusal):
            require_tx_frequency_permit(
                store.snapshot(), resolve_radio_profile(model="IC-705")
            )
        if outcome == "timeout":
            raise TimeoutError("write applied but ACK lost")
        if outcome == "cancel":
            raise asyncio.CancelledError
        # A real transaction's readback is ingested before the executor returns.
        _observe(store, frequency, 100_000_000)
        return CommandExecutionResult()

    service = CommandService(
        executor=SimpleNamespace(execute=execute), state_store=store
    )
    intent = command_intent_from_request(
        "set_freq", {"freq": 100_000_000}, source="http"
    )
    if outcome == "readback":
        await service.execute(intent)
        field = store.snapshot().field(frequency)
        assert field.value == 100_000_000 and "confirmed" in field.quality
    else:
        with pytest.raises((TimeoutError, asyncio.CancelledError)):
            await service.execute(intent)
        assert "confirmed" not in store.snapshot().field(frequency).quality
    assert store.snapshot().field(unrelated) == before


@pytest.mark.parametrize("command,data", [(0x08, b"\x01"), (0x0A, b""), (0x07, b"")])
def test_raw_memory_and_vfo_operations_invalidate_target(command, data):
    store = StateStore()
    _observe(
        store,
        FieldPath.global_("tx_state", "tx_target"),
        KnownTxTarget("MAIN", "A", 146_520_000),
    )
    service = CommandService(executor=SimpleNamespace(), state_store=store)
    service.invalidate_tx_target_before_write(
        "send_civ", {"command": command, "data": data}
    )
    with pytest.raises(TxInterlockRefusal):
        require_tx_frequency_permit(
            store.snapshot(), resolve_radio_profile(model="IC-705")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", "29 00 "])
async def test_raw_tuner_off_does_not_require_target(prefix):
    from rigplane.rigctld.contract import RigctldConfig
    from rigplane.rigctld.handler import RigctldHandler
    from test_rigctld_handler import get_cmd, make_mock_radio

    radio = make_mock_radio()
    radio._send_civ_raw = AsyncMock(return_value=None)
    handler = RigctldHandler(radio, RigctldConfig())
    frame = f"FE FE 98 E0 {prefix}1C 01 00 FD"
    reply = await handler.execute(get_cmd("send_raw", *frame.split()))
    assert reply.ok
    radio._send_civ_raw.assert_awaited_once()


@pytest.mark.asyncio
async def test_request_timestamp_cannot_heal_invalidated_frequency():
    from rigplane.core.civ import CivRequestKey, CivRequestTracker
    from rigplane.types import CivFrame

    tracker = CivRequestTracker()
    pending = tracker.register_response(CivRequestKey(command=0x03, sub=None))
    frame = CivFrame(
        to_addr=0xE0, from_addr=0xA4, command=0x03, data=bytes.fromhex("0020654601")
    )
    started = tracker.response_started_monotonic(frame)
    assert started is not None
    store = StateStore()
    path = FieldPath.active("0", "freq_mode", "freq_hz")
    _observe(store, path, 146_520_000)
    store.invalidate_tx_inputs((path,), timestamp=time.monotonic())
    _observe(store, path, 146_520_000, started)
    assert "confirmed" not in store.snapshot().field(path).quality
    _observe(store, path, 100_000_000)
    assert "confirmed" in store.snapshot().field(path).quality
    tracker.unregister(pending)
    pending.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        commands.SendCiv(0x08, data=b"\x01"),
        commands.SetBand(1),
        commands.SelectVfo("B"),
        commands.VfoSwap(),
        commands.VfoEqualize(),
        commands.MemoryToVfo(1),
        commands.SetMemoryMode(1),
        commands.QuickSplit(),
        commands.QuickSplitTrigger(),
    ],
)
async def test_every_target_changing_legacy_queue_identity_withdraws_old_proof(command):
    store = StateStore()
    target = FieldPath.global_("tx_state", "tx_target")
    _observe(store, target, KnownTxTarget("MAIN", "A", 146_520_000))
    service = CommandService(executor=SimpleNamespace(), state_store=store)
    old_read_started = time.monotonic()

    async def write(entry):
        invalidate = current_tx_write_invalidation()
        assert invalidate is not None
        invalidate()
        # The old native target read completes after this queue entry begins.
        _observe(
            store, target, KnownTxTarget("MAIN", "A", 146_520_000), old_read_started
        )

    await commands.execute_command_queue_entry(
        commands.CommandQueueEntry(command, command_service=service),
        write,
    )

    async def key(entry):
        require_tx_frequency_permit(
            store.snapshot(), resolve_radio_profile(model="IC-705")
        )
        pytest.fail("an old read authorized a key after a target-changing write")

    with pytest.raises(TxInterlockRefusal):
        await commands.execute_command_queue_entry(
            commands.CommandQueueEntry(commands.PttOn()), key
        )


@pytest.mark.asyncio
async def test_failed_enqueue_does_not_invalidate_confirmed_radio_truth():
    store = StateStore()
    target = FieldPath.global_("tx_state", "tx_target")
    _observe(store, target, KnownTxTarget("MAIN", "A", 146_520_000))
    before = store.snapshot()

    async def enqueue(intent):
        raise RuntimeError("queue admission refused before dispatch")

    service = CommandService(
        executor=SimpleNamespace(execute=enqueue), state_store=store
    )
    intent = command_intent_from_request(
        "set_freq", {"freq": 100_000_000}, source="http"
    )
    with pytest.raises(RuntimeError):
        await service.execute(intent)
    after = store.snapshot()
    assert after.fields == before.fields
    assert after.observation_seq == before.observation_seq
