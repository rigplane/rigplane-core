"""Managed rigctld receipt acceptance over a real external-provider transport."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import replace
from types import SimpleNamespace

import pytest

from fake_rigctld import FakeRigctldServer
from rigplane.backends.rigctld_client import RigctldClientRadio, RigctldTransport
from rigplane.backends.rigctld_client.observations import (
    RigctldClientObservationAdapter,
)
from rigplane.core.command_service import CommandService, command_intent_from_request
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import StateStore
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime._poller_types import CommandQueue, SetFreq
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    ManagedTxIntentKind,
    ManagedTxOutcome,
)
from test_rigctld_ingress_scheduling import (
    _FREQUENCY,
    _IngressTrace,
    _WAIT_TIMEOUT,
    _collect_replies,
    _ingress_checkpoint,
    _trace_server_ingress,
)


class _NoTruthProbe:
    def __bool__(self):
        raise AssertionError("managed references must be tested with is None")


@pytest.mark.parametrize(
    "present",
    [
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, False),
        (True, False, True),
        (False, True, True),
    ],
)
def test_partial_managed_configuration_is_rejected_before_bootstrap(present):
    names = ("managed_tx_authority", "command_queue", "command_service")
    kwargs = {
        name: _NoTruthProbe() if supplied else None
        for name, supplied in zip(names, present, strict=True)
    }
    for constructor in (RigctldServer, RigctldHandler):
        with pytest.raises(ValueError, match="together"):
            constructor(object(), RigctldConfig(), **kwargs)


def test_managed_configuration_rejects_custom_handler():
    with pytest.raises(ValueError, match="_handler"):
        RigctldServer(
            object(),
            _handler=object(),
            managed_tx_authority=_NoTruthProbe(),
            command_queue=_NoTruthProbe(),
            command_service=_NoTruthProbe(),
        )


class _DefaultExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, intent):
        self.calls.append(intent)
        raise AssertionError("shared default executor was selected")


class _ReceiptFence(TxAbortFence):
    def __init__(self, trace):
        super().__init__()
        self.trace = trace
        self.registered = asyncio.Event()
        self.revoked = asyncio.Event()
        self.scopes = []

    def register(self, token, cancellation, *, scope=None):
        super().register(token, cancellation, scope=scope)
        self.scopes.append(scope)
        self.trace.record("authority_registered", scope)
        self.registered.set()

    def cancel_scope(self, scope):
        cleanup = super().cancel_scope(scope)
        self.trace.record("authority_revoked", scope)
        self.revoked.set()
        return cleanup


class _GatedProvider(RigctldClientRadio):
    def __init__(self, transport, store, trace):
        super().__init__(host="127.0.0.1", transport=transport)
        self.state_store = store
        self.trace = trace
        self.frequency_entered = asyncio.Event()
        self.release_frequency = asyncio.Event()
        self.frequency_finished = asyncio.Event()

    async def set_freq(self, freq, receiver=0):
        self.trace.record("provider_entered", "set_freq")
        self.frequency_entered.set()
        try:
            await self.release_frequency.wait()
            await super().set_freq(freq, receiver=receiver)
        finally:
            self.trace.record("provider_settled", "set_freq")
            self.frequency_finished.set()


async def _finish(action, *args):
    await asyncio.wait_for(action(*args), _WAIT_TIMEOUT)


def _quiesce_before_stop(poller):
    closing = False
    parked = asyncio.Event()

    def wrap(original):
        idle = asyncio.Event()
        idle.set()

        async def batch():
            if closing:
                await parked.wait()
            idle.clear()
            try:
                await original()
            finally:
                idle.set()

        return batch, idle

    poller._poll_medium, medium_idle = wrap(poller._poll_medium)
    poller._poll_slow, slow_idle = wrap(poller._poll_slow)

    async def quiesce():
        nonlocal closing
        closing = True
        await asyncio.gather(medium_idle.wait(), slow_idle.wait())

    return quiesce


class _RecordingLane(ManagedTxEffectLane):
    def __init__(self, radio, trace):
        super().__init__(radio)
        self.trace = trace
        self.settlements = []

    async def settle(self, effect, *, deadline_monotonic, is_current=None):
        self.trace.record("provider_entered", effect.operation)
        try:
            settled = await super().settle(
                effect, deadline_monotonic=deadline_monotonic, is_current=is_current
            )
        finally:
            self.trace.record("provider_settled", effect.operation)
        self.settlements.append((effect, settled))
        return settled


class _RecordingQueue(CommandQueue):
    def __init__(self, trace):
        super().__init__()
        self.trace = trace

    def drain_entries(self):
        entries = super().drain_entries()
        for entry in entries:
            self.trace.record("queue_claimed", type(entry.command).__name__)
        return entries


@pytest.fixture
async def managed(tmp_path):
    async with FakeRigctldServer() as provider, AsyncExitStack() as cleanup:
        transport = RigctldTransport(host=provider.host, port=provider.port)
        cleanup.push_async_callback(_finish, transport.close)
        trace = _IngressTrace()
        store = StateStore()
        queue = _RecordingQueue(trace)
        fence = _ReceiptFence(trace)
        radio = _GatedProvider(transport, store, trace)
        default = _DefaultExecutor()
        service = CommandService(executor=default, state_store=store)
        unsubscribe = service.subscribe_lifecycle(
            lambda event: trace.record(
                "intent_accepted" if event.state == "accepted" else event.state,
                event.command_id,
            )
            if event.source == "rigctld"
            else None
        )
        cleanup.callback(unsubscribe)
        lane = _RecordingLane(radio, trace)
        authority = ManagedTxAuthority(
            lane,
            ManagedTxTotConfigStore(tmp_path / "tot.json"),
            fence,
            provider_generation=store.provider_generation,
        )
        cleanup.push_async_callback(_finish, authority.close)
        await authority._stop_scheduler(authority._scheduler_task)
        tasks = []
        try:
            await transport.connect()
            adapter = RigctldClientObservationAdapter(radio)
            observed = await adapter.read_ptt()
            store.apply(
                replace(observed, provider_generation=store.provider_generation)
            )
            poller = radio.create_observation_poller(
                callback=lambda batch: [store.apply(item) for item in batch],
                command_queue=queue,
            )
            poller.bind_provider_generation(capture=lambda: store.provider_generation)
            cleanup.push_async_callback(_finish, poller.stop)
            cleanup.push_async_callback(_finish, _quiesce_before_stop(poller))
            await poller.start()
            server = RigctldServer(
                radio,
                RigctldConfig(host="127.0.0.1", port=0, command_timeout=30.0),
                managed_tx_authority=authority,
                command_queue=queue,
                command_service=service,
            )
            _trace_server_ingress(server, trace)
            cleanup.push_async_callback(_finish, server.stop)
            await server.start()
            port = server._server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), _WAIT_TIMEOUT
            )
            cleanup.push_async_callback(_finish, writer.wait_closed)
            cleanup.callback(writer.close)
            cleanup.push_async_callback(_finish, authority.force_off)
            cleanup.push_async_callback(
                _finish, authority.owner_disconnect, "rigctld-client-1"
            )
            cleanup.callback(provider.behavior.malformed_responses.clear)
            provider.commands_seen.clear()
            yield SimpleNamespace(
                provider=provider,
                trace=trace,
                transport=transport,
                radio=radio,
                store=store,
                queue=queue,
                poller=poller,
                fence=fence,
                authority=authority,
                lane=lane,
                service=service,
                default=default,
                server=server,
                reader=reader,
                writer=writer,
                tasks=tasks,
            )
        finally:
            radio.release_frequency.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)


def _writes(rig):
    return [
        command
        for command in rig.provider.commands_seen
        if command.startswith(("F ", "T "))
    ]


async def _assert_ingress_accepted(rig, commands):
    await _ingress_checkpoint(rig.trace, commands)
    accepted = [
        detail for stage, detail in rig.trace.events if stage == "intent_accepted"
    ]
    assert len(accepted) == len(commands)


async def test_injected_references_and_per_call_leaf_preserve_shared_default(managed):
    rig = managed
    handler = rig.server._rig_handler
    assert handler._managed_tx_authority is rig.authority
    assert handler._command_queue is rig.queue
    assert handler._command_service is rig.service
    assert handler._state_store is rig.store
    assert rig.poller._command_queue is rig.queue
    rig.radio.release_frequency.set()
    rig.writer.write(f"F {_FREQUENCY}\n".encode())
    await rig.writer.drain()
    assert await asyncio.wait_for(rig.reader.readline(), 2) == b"RPRT 0\n"
    assert _writes(rig) == [f"F {_FREQUENCY}"]
    assert rig.default.calls == []
    intent = command_intent_from_request(
        "set_freq",
        {"freq_hz": _FREQUENCY},
        source="rigctld",
        command_id="default-probe",
        session_id="default-probe",
    )
    with pytest.raises(AssertionError, match="shared default executor"):
        await rig.service.execute(intent)
    assert rig.default.calls == [intent]


async def test_real_authority_fixture_uses_provider_ptt(managed):
    rig = managed
    assert await rig.authority.ptt_down("fixture") is ManagedTxOutcome.ACCEPTED
    assert (await rig.authority.snapshot()).state.intent.kind is ManagedTxIntentKind.PTT
    assert await rig.authority.ptt_up("fixture") is ManagedTxOutcome.ACCEPTED
    assert _writes(rig) == ["T 1", "T 0"]
    assert not (await rig.authority.snapshot()).state.release_required


async def _key_owner(rig):
    owner = "rigctld-client-1"
    worker = await rig.authority.submit_ptt(True, owner)
    rig.tasks.append(worker)
    transition, settled = await asyncio.wait_for(worker, 2)
    assert transition.outcome is ManagedTxOutcome.ACCEPTED
    assert settled is not None and settled.result is ActuationResult.ACCEPTED
    assert settled.operation is ActuationOperation.PTT_ON
    assert settled.token == transition.effects[0].token
    assert rig.lane.settlements[-1][1] is settled
    state = (await rig.authority.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.PTT
    assert state.intent.owner_token == owner
    assert state.pending_effect is None
    assert rig.provider.state.ptt == 1
    assert rig.server._next_client_id == 1
    return owner


async def test_active_owner_off_provider_error_retains_release_debt(managed):
    rig = managed
    owner = await _key_owner(rig)
    rig.provider.behavior.malformed_responses["T 0"] = b"RPRT -6\n"
    try:
        worker = await rig.authority.submit_ptt(False, owner)
        rig.tasks.append(worker)
        transition, settled = await asyncio.wait_for(worker, 2)
        assert transition.outcome is ManagedTxOutcome.ACCEPTED
        assert settled is not None and settled.result is ActuationResult.UNCERTAIN
        assert settled.operation is ActuationOperation.FORCE_RECEIVE
        assert settled.token == transition.effects[0].token
        assert rig.lane.settlements[-1][1] is settled
        state = (await rig.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
        assert state.release_required and state.pending_effect is None
        assert _writes(rig) == ["T 1", "T 0"]
        assert rig.provider.state.ptt == 1
    finally:
        rig.provider.behavior.malformed_responses.pop("T 0", None)


@pytest.mark.parametrize(
    ("provider_error", "result", "off_reply"),
    [
        (False, ActuationResult.ACCEPTED, b"RPRT 0\n"),
        (True, ActuationResult.UNCERTAIN, b"RPRT -9\n"),
    ],
    ids=["accepted", "uncertain"],
)
async def test_active_owner_off_settles_while_frequency_is_pending(
    managed, provider_error, result, off_reply
):
    rig = managed
    owner = await _key_owner(rig)
    observed = await RigctldClientObservationAdapter(rig.radio).read_ptt()
    assert observed.value is True
    rig.store.apply(
        replace(observed, provider_generation=rig.store.provider_generation)
    )
    terminal = asyncio.Event()
    events = []

    def observe(event):
        if (
            event.source == "rigctld"
            and event.target == FieldPath.global_("tx_state", "ptt")
            and event.details.get("session_id") == owner
            and event.state in {"acknowledged", "failed", "timed_out"}
        ):
            events.append(event)
            terminal.set()

    unsubscribe = rig.service.subscribe_lifecycle(observe)
    ready = asyncio.Event()
    replies = asyncio.create_task(_collect_replies(rig.reader, rig.radio, 2, ready))
    rig.tasks.append(replies)
    if provider_error:
        rig.provider.behavior.malformed_responses["T 0"] = b"RPRT -6\n"
    try:
        await asyncio.wait_for(ready.wait(), 2)
        commands = [f"F {_FREQUENCY}".encode(), b"T 0"]
        rig.writer.write(b"\n".join(commands) + b"\n")
        await rig.writer.drain()
        await asyncio.wait_for(rig.radio.frequency_entered.wait(), 2)
        await _assert_ingress_accepted(rig, commands)
        await asyncio.wait_for(terminal.wait(), 2)
        assert not rig.radio.frequency_finished.is_set()
        assert _writes(rig) == ["T 1", "T 0"]
        effect, settled = rig.lane.settlements[-1]
        assert effect.operation is ActuationOperation.FORCE_RECEIVE
        assert settled is not None and settled.token == effect.token
        assert settled.operation is effect.operation and settled.result is result
        state = (await rig.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
        assert state.pending_effect is None
        assert state.release_required is provider_error
        assert rig.provider.state.ptt == int(provider_error)
        assert [event.state for event in events] == [
            "failed" if provider_error else "acknowledged"
        ]
        assert (
            "provider_settled",
            ActuationOperation.FORCE_RECEIVE,
        ) in rig.trace.events
        assert ("provider_settled", "set_freq") not in rig.trace.events
        assert not [
            detail
            for stage, detail in rig.trace.events
            if stage == "response_written"
        ]
        rig.radio.release_frequency.set()
        assert await asyncio.wait_for(replies, 2) == [
            (b"RPRT 0\n", True),
            (off_reply, True),
        ]
        assert [
            detail
            for stage, detail in rig.trace.events
            if stage == "response_written"
        ] == [b"RPRT 0\n", off_reply]
    finally:
        rig.radio.release_frequency.set()
        rig.provider.behavior.malformed_responses.pop("T 0", None)
        unsubscribe()


async def test_real_provider_poller_drains_injected_queue(managed):
    rig = managed
    completion = asyncio.get_running_loop().create_future()
    rig.tasks.append(completion)
    rig.queue.put_ordered(cmd=SetFreq(_FREQUENCY), future=completion)
    await asyncio.wait_for(rig.radio.frequency_entered.wait(), 5)
    assert not completion.done()
    assert _writes(rig) == []
    assert ("queue_claimed", "SetFreq") in rig.trace.events
    rig.radio.release_frequency.set()
    await asyncio.wait_for(asyncio.shield(completion), 2)
    assert _writes(rig) == [f"F {_FREQUENCY}"]


async def test_frequency_provider_rejection_preserves_terminal_error(managed):
    rig = managed
    rig.provider.behavior.malformed_responses[f"F {_FREQUENCY}"] = b"RPRT -6\n"
    ready = asyncio.Event()
    replies = asyncio.create_task(_collect_replies(rig.reader, rig.radio, 1, ready))
    rig.tasks.append(replies)
    await asyncio.wait_for(ready.wait(), 2)
    rig.writer.write(f"F {_FREQUENCY}\n".encode())
    await rig.writer.drain()
    await asyncio.wait_for(rig.radio.frequency_entered.wait(), 2)
    rig.radio.release_frequency.set()
    assert await asyncio.wait_for(replies, 2) == [(b"RPRT -7\n", True)]


@pytest.mark.parametrize(
    ("provider_error", "first_reply"),
    [(False, b"RPRT 0\n"), (True, b"RPRT -7\n")],
    ids=["frequency-success", "frequency-provider-error"],
)
async def test_pending_on_is_revoked_by_pipelined_off(
    managed, provider_error, first_reply
):
    rig = managed
    if provider_error:
        rig.provider.behavior.malformed_responses[f"F {_FREQUENCY}"] = b"RPRT -6\n"
    ready = asyncio.Event()
    replies = asyncio.create_task(_collect_replies(rig.reader, rig.radio, 3, ready))
    rig.tasks.append(replies)
    await asyncio.wait_for(ready.wait(), 2)
    commands = [f"F {_FREQUENCY}".encode(), b"T 1", b"T 0"]
    rig.writer.write(b"\n".join(commands) + b"\n")
    await rig.writer.drain()
    await asyncio.wait_for(rig.radio.frequency_entered.wait(), 2)
    await _assert_ingress_accepted(rig, commands)
    await asyncio.wait_for(rig.fence.registered.wait(), 2)
    await asyncio.wait_for(rig.fence.revoked.wait(), 2)
    assert rig.fence.scopes == ["rigctld-client-1"]
    assert not rig.fence._cancellations
    assert not rig.radio.frequency_finished.is_set()
    assert "T 1" not in rig.provider.commands_seen
    assert rig.trace.events.index(
        ("authority_registered", "rigctld-client-1")
    ) < rig.trace.events.index(("authority_revoked", "rigctld-client-1"))
    assert not [
        detail
        for stage, detail in rig.trace.events
        if stage == "response_written"
    ]
    rig.radio.release_frequency.set()
    assert await asyncio.wait_for(replies, 2) == [
        (first_reply, True),
        (b"RPRT -9\n", True),
        (b"RPRT 0\n", True),
    ]
    assert "T 1" not in rig.provider.commands_seen
    state = (await rig.authority.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX and not state.release_required


@pytest.mark.parametrize("ending", ["eof", "server-stop"])
async def test_disconnect_revokes_pending_on(managed, ending):
    rig = managed
    commands = [f"F {_FREQUENCY}".encode(), b"T 1"]
    try:
        rig.writer.write(b"\n".join(commands) + b"\n")
        await rig.writer.drain()
        await asyncio.wait_for(rig.radio.frequency_entered.wait(), 2)
        await _assert_ingress_accepted(rig, commands)
        await asyncio.wait_for(rig.fence.registered.wait(), 2)
        connections = tuple(rig.server._client_tasks)
        assert len(connections) == 1
        if ending == "eof":
            rig.writer.close()
            await asyncio.wait_for(rig.writer.wait_closed(), 2)
        else:
            rig.tasks.append(asyncio.create_task(rig.server.stop()))
        await asyncio.wait_for(rig.fence.revoked.wait(), 2)
        assert not rig.fence._cancellations
        rig.radio.release_frequency.set()
        await asyncio.wait_for(
            asyncio.gather(*connections, return_exceptions=True), 2
        )
        assert "T 1" not in rig.provider.commands_seen
        assert not (await rig.authority.snapshot()).state.release_required
    finally:
        rig.radio.release_frequency.set()


async def test_sequential_frequency_then_ptt_preserves_wire_order(managed):
    rig = managed
    rig.radio.release_frequency.set()
    for command in (f"F {_FREQUENCY}", "T 1", "T 0"):
        rig.writer.write((command + "\n").encode())
        await rig.writer.drain()
        assert await asyncio.wait_for(rig.reader.readline(), 2) == b"RPRT 0\n"
    assert _writes(rig) == [f"F {_FREQUENCY}", "T 1", "T 0"]
