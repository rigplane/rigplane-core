"""Direct fixed-slot frequency writes must never select a VFO."""

import asyncio
import dataclasses
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.commands import parse_civ_frame, build_civ_frame
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
from rigplane.exceptions import CommandError
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime._poller_types import CommandQueue, SelectVfo, SetVfoFreq
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.runtime_helpers import projected_vfo_capability_tags


FREQ = 14_074_000
ACK = parse_civ_frame(bytes.fromhex("FE FE E0 94 FB FD"))
READBACK = parse_civ_frame(bytes.fromhex("FE FE E0 94 25 01 00 40 07 14 00 FD"))


def setup(active="A", *, age=0, generation=None):
    store = StateStore()
    store.begin_provider_generation()
    radio = SimpleNamespace(
        profile=resolve_radio_profile(model="IC-7300"),
        capabilities=set(),
        _civ_epoch=1,
        send_civ=AsyncMock(side_effect=[ACK, READBACK]),
        set_freq=AsyncMock(),
        set_vfo=AsyncMock(),
        select_receiver=AsyncMock(),
    )
    queue = CommandQueue()
    poller = RadioPoller(radio, queue, state_store=store)
    if active is not None:
        store.apply(
            Observation(
                path=FieldPath.active_slot("0"),
                value=active,
                source=SourceMetadata(
                    source="command_response", provider="vfo_binding"
                ),
                timestamp_monotonic=time.monotonic() - age,
                max_age=5,
                provider_generation=store.provider_generation
                if generation is None
                else generation,
            )
        )
    return poller, radio, store, queue


def command(store, slot="B", expected="A", **changes):
    params = dict(
        freq=FREQ,
        receiver=0,
        slot=slot,
        expected_active_slot=expected,
        provider_generation=store.provider_generation,
    )
    params.update(changes)
    return SetVfoFreq(**params)


@pytest.mark.parametrize(
    "active,slot,selector", [("A", "A", 0), ("A", "B", 1), ("B", "A", 1), ("B", "B", 0)]
)
async def test_direct_wire_and_no_selection(active, slot, selector):
    poller, radio, store, _ = setup(active)
    radio.send_civ.side_effect = [
        ACK,
        parse_civ_frame(
            bytes.fromhex(f"FE FE E0 94 25 {selector:02X} 00 40 07 14 00 FD")
        ),
    ]
    await poller._execute(command(store, slot, active))
    frames = []
    for call in radio.send_civ.await_args_list:
        opcode, sub = call.args
        assert call.kwargs["wait_response"] is True
        frames.append(
            build_civ_frame(0x94, 0xE0, opcode, sub=sub, data=call.kwargs["data"])
        )
    assert frames == [
        bytes.fromhex(f"FE FE 94 E0 25 {selector:02X} 00 40 07 14 00 FD"),
        bytes.fromhex(f"FE FE 94 E0 25 {selector:02X} FD"),
    ]
    radio.set_vfo.assert_not_called()
    radio.select_receiver.assert_not_called()
    radio.set_freq.assert_not_called()
    assert store.snapshot().field(FieldPath.active_slot("0")).value == active
    # ACK/readback return values alone are not observations; RX owns truth.
    with pytest.raises(KeyError):
        store.snapshot().field(FieldPath.unselected("0", "freq_mode", "freq_hz"))


@pytest.mark.parametrize(
    "active,age,generation",
    [(None, 0, None), ("B", 0, None), ("A", 8, None), ("A", 0, 0)],
)
async def test_unknown_stale_or_mismatched_identity_never_writes(
    active, age, generation
):
    poller, radio, store, _ = setup(active, age=age, generation=generation)
    with pytest.raises(CommandError):
        await poller._execute(command(store))
    radio.send_civ.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        dict(freq=True),
        dict(freq=14.5),
        dict(freq="14074000"),
        dict(freq=-1),
        dict(receiver=True),
        dict(receiver=1),
        dict(slot="C"),
        dict(expected_active_slot="unknown"),
        dict(provider_generation=True),
        dict(provider_generation=-1),
    ],
)
def test_strict_command_fields(changes):
    _, _, store, _ = setup()
    with pytest.raises(ValueError):
        command(store, **changes)


@pytest.mark.parametrize(
    "changes",
    [dict(freq=29_999), dict(freq=100_000_000), dict(provider_generation=999)],
)
async def test_bounds_and_provider_mismatch_never_write(changes):
    poller, radio, store, _ = setup()
    with pytest.raises(CommandError):
        await poller._execute(command(store, **changes))
    radio.send_civ.assert_not_called()


@pytest.mark.parametrize("model", ["IC-7610", "IC-705", "FTX-1"])
async def test_unsupported_profile_has_no_route(model):
    poller, radio, store, _ = setup()
    radio.profile = resolve_radio_profile(model=model)
    poller._profile = radio.profile
    assert "vfo_freq_direct" not in projected_vfo_capability_tags(radio, None)
    with pytest.raises(CommandError):
        await poller._execute(command(store))
    radio.send_civ.assert_not_called()


async def test_nak_is_failure_without_readback():
    poller, radio, store, _ = setup()
    radio.send_civ.side_effect = [parse_civ_frame(bytes.fromhex("FE FE E0 94 FA FD"))]
    with pytest.raises(CommandError):
        await poller._execute(command(store))
    assert radio.send_civ.await_count == 1


@pytest.mark.parametrize(
    "slot,expected,path",
    [("A", "A", "active"), ("B", "A", "unselected"), ("A", "B", "unselected")],
)
def test_pending_intent_targets_exact_relative_slot(slot, expected, path):
    _, _, store, _ = setup()
    intent = command_intent_from_request(
        "set_vfo_freq",
        dataclasses.asdict(command(store, slot, expected)),
        source="websocket",
    )
    target = (FieldPath.active if path == "active" else FieldPath.unselected)(
        "0", "freq_mode", "freq_hz"
    )
    assert intent.target == target
    assert intent.expected_observations == (target,)
    assert intent.params["freq_hz"] == FREQ


async def test_handler_waits_and_pins_connection_generation():
    poller, radio, store, queue = setup()
    handler = ControlHandler.__new__(ControlHandler)
    handler._radio = radio
    handler._read_only = False
    handler._command_service = None
    handler._server = SimpleNamespace(command_queue=queue, command_state_store=store)
    intent = command_intent_from_request(
        "set_vfo_freq", dataclasses.asdict(command(store)), source="websocket"
    )
    task = asyncio.create_task(handler._execute_intent(intent))
    await asyncio.sleep(0)
    entry = queue.take_entry()
    assert entry is not None and not task.done()
    assert entry.provider_generation == store.provider_generation
    assert entry.connection_generation == 1
    await poller._execute_queued_entry(entry)
    assert (await task).details["slot"] == "B"


async def test_queued_reconnect_rejects_before_wire():
    poller, radio, store, queue = setup()
    entry = queue.put_ordered(
        command(store),
        provider_generation=store.provider_generation,
        connection_generation=1,
    )
    radio._civ_epoch = 2
    with pytest.raises(CommandError):
        await poller._execute_queued_entry_action(entry)
    radio.send_civ.assert_not_called()


async def test_internal_selection_waits_for_direct_write_and_readback():
    poller, radio, store, _ = setup()
    store.apply(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=time.monotonic(),
            provider_generation=store.provider_generation,
        )
    )
    started, release = asyncio.Event(), asyncio.Event()

    async def send(*args, **kwargs):
        started.set()
        await release.wait()
        return ACK if radio.send_civ.await_count == 1 else READBACK

    radio.send_civ.side_effect = send
    poller._select_and_bind_vfo_slot = AsyncMock()
    write = asyncio.create_task(poller._execute(command(store)))
    await started.wait()
    select = asyncio.create_task(
        poller._execute(SelectVfo("B"), source="internal_policy")
    )
    await asyncio.sleep(0)
    poller._select_and_bind_vfo_slot.assert_not_called()
    release.set()
    await asyncio.gather(write, select)
    poller._select_and_bind_vfo_slot.assert_awaited_once()


async def test_ack_keeps_inactive_pending_until_matching_inactive_observation():
    store = StateStore()
    store.begin_provider_generation()
    service = CommandService(
        executor=SimpleNamespace(
            execute=AsyncMock(return_value=CommandExecutionResult())
        ),
        state_store=store,
    )
    intent = command_intent_from_request(
        "set_vfo_freq",
        dataclasses.asdict(command(store)),
        source="websocket",
        session_id="test",
    )
    await service.execute(intent)
    assert service.pending_overlays(source="websocket", session_id="test")
    for factory in (FieldPath.active, FieldPath.unselected):
        service.apply_observation(
            Observation(
                path=factory("0", "freq_mode", "freq_hz"),
                value=FREQ,
                source=SourceMetadata(
                    source="command_response",
                    provider="test",
                    command_source="websocket",
                    session_id="test",
                ),
                correlation_id=intent.id,
                timestamp_monotonic=time.monotonic(),
                provider_generation=store.provider_generation,
            )
        )
        pending = service.pending_overlays(source="websocket", session_id="test")
        assert bool(pending) == (factory == FieldPath.active)


@pytest.mark.parametrize("phase", ["ack", "readback"])
async def test_generation_change_during_transaction_does_not_confirm(phase):
    poller, radio, store, _ = setup()

    async def send(*args, **kwargs):
        index = radio.send_civ.await_count
        if index == (1 if phase == "ack" else 2):
            store.begin_provider_generation()
        return ACK if index == 1 else READBACK

    radio.send_civ.side_effect = send
    with pytest.raises(CommandError, match="changed"):
        await poller._execute(command(store))
    assert radio.send_civ.await_count == (1 if phase == "ack" else 2)
    assert not store.snapshot().fields


async def test_mismatched_readback_selector_cannot_confirm_other_slot():
    poller, radio, store, _ = setup()
    radio.send_civ.side_effect = [
        ACK,
        parse_civ_frame(bytes.fromhex("FE FE E0 94 25 00 00 40 07 14 00 FD")),
    ]
    with pytest.raises(CommandError, match="selector mismatch"):
        await poller._execute(command(store))


async def test_ack_alone_does_not_change_frequency_truth():
    poller, radio, store, _ = setup()
    reading, release = asyncio.Event(), asyncio.Event()

    async def send(*args, **kwargs):
        if radio.send_civ.await_count == 1:
            return ACK
        reading.set()
        await release.wait()
        return READBACK

    radio.send_civ.side_effect = send
    task = asyncio.create_task(poller._execute(command(store)))
    await reading.wait()
    assert not any(field.path.name == "freq_hz" for field in store.snapshot().fields)
    release.set()
    await task


async def test_cancelled_queued_request_cannot_write():
    poller, radio, store, queue = setup()
    future = asyncio.get_running_loop().create_future()
    entry = queue.put_ordered(command(store), future=future)
    future.cancel()
    await poller._execute_queued_entry(entry)
    radio.send_civ.assert_not_called()


@pytest.mark.parametrize(
    "active,slot", [("A", "A"), ("A", "B"), ("B", "A"), ("B", "B")]
)
@pytest.mark.parametrize("outcome", ["success", "disconnect", "cancel", "rebind"])
async def test_real_service_handler_queue_poller_completes_correlated_readback(
    active, slot, outcome
):
    poller, radio, store, queue = setup(active)
    now = time.monotonic()
    store.apply_relative_vfo_observations(
        tuple(
            Observation(
                path=factory("0", "freq_mode", leaf),
                value=value,
                source=SourceMetadata(source="poll_response", provider="icom_civ"),
                timestamp_monotonic=now,
                provider_generation=store.provider_generation,
            )
            for factory in (FieldPath.active, FieldPath.unselected)
            for leaf, value in (("freq_hz", 7_100_000), ("mode", "USB"))
        ),
        generation=store.provider_generation,
    )
    selector = 0 if active == slot else 1
    radio.send_civ.side_effect = [
        ACK,
        parse_civ_frame(
            bytes.fromhex(f"FE FE E0 94 25 {selector:02X} 00 40 07 14 00 FD")
        ),
    ]
    entered, release = asyncio.Event(), asyncio.Event()
    if outcome in ("disconnect", "cancel"):

        async def delayed_send(*args, **kwargs):
            if radio.send_civ.await_count == 1:
                entered.set()
                await release.wait()
                return ACK
            return parse_civ_frame(
                bytes.fromhex(f"FE FE E0 94 25 {selector:02X} 00 40 07 14 00 FD")
            )

        radio.send_civ.side_effect = delayed_send
    if outcome == "rebind":
        original_put = queue.put_ordered

        def put_with_rebind(*args, **kwargs):
            entry = original_put(*args, **kwargs)

            def rebind(_future):
                store.apply(
                    Observation(
                        path=FieldPath.active_slot("0"),
                        value="B" if active == "A" else "A",
                        source=SourceMetadata(
                            source="command_response", provider="vfo_binding"
                        ),
                        timestamp_monotonic=time.monotonic(),
                        provider_generation=store.provider_generation,
                    )
                )

            entry.future.add_done_callback(rebind)
            return entry

        queue.put_ordered = put_with_rebind
    handler = ControlHandler.__new__(ControlHandler)
    handler._radio, handler._read_only = radio, False
    handler._server = SimpleNamespace(command_queue=queue, command_state_store=store)
    service = CommandService(
        executor=SimpleNamespace(execute=handler._execute_intent),
        state_store=store,
    )
    handler._command_service = service
    intent = command_intent_from_request(
        "set_vfo_freq",
        dataclasses.asdict(command(store, slot, active)),
        source="websocket",
        session_id="integrated",
    )
    task = asyncio.create_task(service.execute(intent))
    await queue.wait(timeout=1)
    entry = queue.take_entry()
    assert entry is not None
    dispatch = asyncio.create_task(poller._execute_queued_entry(entry))
    if outcome in ("disconnect", "cancel"):
        await entered.wait()
        if outcome == "disconnect":
            radio._civ_epoch += 1
        else:
            task.cancel()
        release.set()
        results = await asyncio.gather(dispatch, task, return_exceptions=True)
        if outcome == "disconnect":
            assert all(isinstance(result, CommandError) for result in results)
        else:
            assert isinstance(results[1], asyncio.CancelledError)
        assert not service.pending_overlays(source="websocket", session_id="integrated")
        assert "reconciled" not in [event.state for event in service.lifecycle_events()]
        return
    await dispatch
    if outcome == "rebind":
        with pytest.raises(CommandError, match="before readback delivery"):
            await task
        assert not service.pending_overlays(source="websocket", session_id="integrated")
        assert not any(
            field.path.name == "freq_hz" and field.value == FREQ
            for field in store.snapshot().fields
        )
        return
    result = await task
    assert result.executor_result.details["slot"] == slot
    assert [event.state for event in result.lifecycle_events][-2:] == [
        "acknowledged",
        "reconciled",
    ]
    assert not service.pending_overlays(source="websocket", session_id="integrated")
    target = (FieldPath.active if selector == 0 else FieldPath.unselected)(
        "0", "freq_mode", "freq_hz"
    )
    other = (FieldPath.unselected if selector == 0 else FieldPath.active)(
        "0", "freq_mode", "freq_hz"
    )
    assert store.snapshot().field(target).value == FREQ
    assert store.snapshot().field(other).value == 7_100_000
