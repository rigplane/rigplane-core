# fmt: off
from __future__ import annotations
import asyncio
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from rigplane.core.command_service import CommandService
from rigplane.core.command_dispatch import execute_command_intent
from rigplane.core.exceptions import ConnectionError as ProviderConnectionError
from rigplane.core.state_pipeline_contracts import CommandIntent, FieldPath, Observation, SourceMetadata
from rigplane.core.state_store import StateStore
from rigplane.core.tx_interlock_contract import TxInterlockDisposition
from rigplane.rigctld import handler as rigctld_handler
from rigplane.rigctld.contract import HamlibError, RigctldConfig
from rigplane.rigctld.handler import RigctldHandler, _RigctldCommandExecutor, _RigctldCommandFailure
from rigplane.rigctld.server import _MAX_PENDING_CLIENT_RESPONSES, RigctldServer
from rigplane.runtime._poller_types import CommandQueue, execute_command_queue_entry, execute_positive_tx_queue_entry, validate_command_queue_entry_currency
from rigplane.runtime.managed_tx_state import ActuationOperation, ActuationResult, ManagedTxIntentKind, ManagedTxOutcome
from serial_stub import SerialMockRadio
from test_managed_tx_authority import authority
from test_controller_authority import Safety
from test_rigctld_split import _hold_commander_before_set, _pause_mode_preservation
from test_rigctld_wsjtx_split_mk2 import MODELS, _assert_no_relative_overlays, _client, _exchange, _peer, _rf_state
_FREQUENCY, _WAIT = 14_074_000, 2.0
class _NoTruthProbe:
    def __bool__(self): raise AssertionError("identity checks must use is None")
class _Default:
    def __init__(self): self.calls = []
    async def execute(self, intent):
        self.calls.append(intent)
        raise AssertionError("shared default executor selected")
class _Radio(SerialMockRadio):
    def __init__(self, store):
        super().__init__()
        self.state_store, self.fail_frequency, self.calls = store, False, []
        self.entered, self.release, self.finished = (asyncio.Event() for _ in range(3))
    def supports_command(self, _name, **_kwargs): return True
    async def set_freq(self, freq, receiver=0):
        self.calls.append(("F", freq, receiver))
        self.entered.set()
        await self.release.wait()
        self.finished.set()
        if self.fail_frequency:
            raise ProviderConnectionError("injected")
    async def set_tuner_status(self, value):
        self.calls.append(("tuner", value))
@pytest.fixture
async def managed():
    managed, _, _, _, fence, lane = authority()
    store = StateStore()
    store.apply_current(Observation(path=FieldPath.global_("tx_state", "ptt"), value=False, source=SourceMetadata(source="test", provider="tests"),
        timestamp_monotonic=asyncio.get_running_loop().time(), max_age=1e9))
    radio, default = _Radio(store), _Default()
    await radio.connect()
    service = CommandService(executor=default, state_store=store)
    queue = CommandQueue()
    queue.bind_connection_generation(lambda: "rigctld-connection-1")
    server = RigctldServer(radio, RigctldConfig(host="127.0.0.1", port=0, command_timeout=1.0), managed_tx_authority=managed, command_queue=queue, command_service=service)
    await server.start()
    drained_entries = []
    async def drain():
        while True:
            await queue.wait(.01)
            while (entry := queue.take_entry()) is not None:
                drained_entries.append(entry)
                async def execute(claimed):
                    validate_command_queue_entry_currency(claimed, now=asyncio.get_running_loop().time(), provider_generation=store.provider_generation, connection_generation="rigctld-connection-1", session_is_live=queue.session_is_live, require_connection_generation=True)
                    if claimed.positive_tx_submission is not None:
                        await execute_positive_tx_queue_entry(claimed)
                    elif isinstance(claimed.command, CommandIntent):
                        await execute_command_intent(
                            radio,
                            claimed.command,
                            managed_tx_authority=managed,
                            validate_currency=lambda: validate_command_queue_entry_currency(
                                claimed,
                                now=asyncio.get_running_loop().time(),
                                provider_generation=store.provider_generation,
                                connection_generation="rigctld-connection-1",
                                session_is_live=queue.session_is_live,
                            ),
                        )
                try:
                    await execute_command_queue_entry(entry, execute)
                except Exception:
                    pass
    drain_task = asyncio.create_task(drain())
    reader, writer = await asyncio.open_connection("127.0.0.1", server._server.sockets[0].getsockname()[1])
    rig = SimpleNamespace(authority=managed, fence=fence, lane=lane, store=store, radio=radio, default=default, service=service, server=server, queue=queue, reader=reader, writer=writer, drained_entries=drained_entries)
    try:
        yield rig
    finally:
        radio.release.set()
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        await server.stop()
        drain_task.cancel()
        await asyncio.gather(drain_task, return_exceptions=True)
        await managed.close()
        await radio.disconnect()
async def _send(rig, *commands):
    rig.writer.write(("\n".join(commands) + "\n").encode())
    await rig.writer.drain()
async def _replies(rig, count):
    return [await asyncio.wait_for(rig.reader.readline(), _WAIT) for _ in range(count)]
async def _key(rig):
    receipt = await rig.authority.submit_ptt(True, "rigctld-client-1")
    assert receipt.outcome is ManagedTxOutcome.ACCEPTED
    await receipt.wait_settlement()
    assert rig.lane.started.get_nowait().operation is ActuationOperation.PTT_ON
async def _clear_release_debt(rig):
    receipt = await rig.authority.submit_force_off()
    assert await receipt.wait_settlement() is not None
def _observe_starts(rig, on_start=None):
    start, submissions, off_started = rig.authority.start_ptt_submission, [], asyncio.Event()
    def observed(on, owner, *, ready=None, expires_at_monotonic=None):
        task = (
            on_start()
            if on and on_start
            else start(
                on,
                owner,
                ready=ready,
                expires_at_monotonic=expires_at_monotonic,
            )
        )
        if on:
            submissions.append((task, ready))
        else:
            off_started.set()
        return task
    rig.authority.start_ptt_submission = observed
    return submissions, off_started
def _gate_on_handler(rig, *, after=False):
    entered, release, execute = asyncio.Event(), asyncio.Event(), rig.server._rig_handler.execute
    async def gated(cmd, **kwargs):
        on = cmd.long_cmd == "set_ptt" and int(cmd.args[0])
        if on and not after:
            entered.set()
            await release.wait()
        response = await execute(cmd, **kwargs)
        if on and after:
            entered.set()
            await release.wait()
        return response
    rig.server._rig_handler.execute = gated
    return entered, release
@pytest.mark.parametrize("present", [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0)])
def test_partial_managed_configuration_is_rejected_before_bootstrap(present):
    refs = {name: (_NoTruthProbe() if name != "command_queue" else CommandQueue()) if value else None for name, value in zip(("managed_tx_authority", "command_queue", "command_service"), present, strict=True)}
    for constructor in (RigctldServer, RigctldHandler):
        with pytest.raises(ValueError, match="together"):
            constructor(object(), RigctldConfig(), **refs)
def test_managed_configuration_rejects_custom_handler():
    with pytest.raises(ValueError, match="_handler"):
        RigctldServer(object(), _handler=object(), managed_tx_authority=_NoTruthProbe(), command_queue=CommandQueue(), command_service=_NoTruthProbe())
def _intent(name, **params):
    return CommandIntent(id="test-" + name, name=name, params=params, source="rigctld")
class _LeafRadio:
    def __init__(self): self.calls = []
    async def set_freq(self, freq, receiver=0): self.calls.append(("freq", freq, receiver))
    async def set_mode(self, mode, *, filter_width=None, receiver=0): self.calls.append(("mode", mode, filter_width, receiver))
    async def set_rit_frequency(self, hz): self.calls.append(("rit", hz))
    async def set_rit_status(self, on): self.calls.append(("rit-on", on))
    async def set_rit_tx_status(self, on): self.calls.append(("xit-on", on))
    async def _send_civ_raw(self, raw): self.calls.append(("raw", raw))
class _LeafHandler:
    def __init__(self, error=HamlibError.OK):
        self._radio, self._managed_tx_authority = _LeafRadio(), object()
        self._has_canonical_state_store, self.error, self.calls = False, error, []
    async def _apply_packet_data_mode(self, *, receiver): self.calls.append(("packet", receiver))
    async def _execute_set_vfo(self, vfo): return self.calls.append(("vfo", vfo)) or self.error
    async def _execute_set_level(self, level, value, *, receiver, vfo_arg): return self.calls.append(("level", level, value, receiver, vfo_arg)) or self.error
    async def _execute_set_func(self, func, on, *, receiver, vfo_arg): return self.calls.append(("func", func, on, receiver, vfo_arg)) or self.error
    async def _execute_set_split_vfo(self, on, tx_vfo): return self.calls.append(("split", on, tx_vfo)) or self.error
async def test_managed_non_ptt_families_keep_exact_legacy_leaf_parameters(monkeypatch):
    class Classification:
        disposition = TxInterlockDisposition.ALWAYS_PASS
        @property
        def command(self): raise AssertionError("classification.command read")
    monkeypatch.setattr(rigctld_handler, "_classify_rigctld_tx_intent", lambda _: Classification())
    handler, predecessor = _LeafHandler(), asyncio.get_running_loop().create_future()
    predecessor.set_result(None)
    executor = _RigctldCommandExecutor(handler, predecessor)
    intents = [
        _intent("set_freq", freq_hz=_FREQUENCY, receiver=1), _intent("set_mode", mode="USB", filter_width=2400, receiver=1, packet_mode=True),
        _intent("set_vfo", vfo="VFOB"), _intent("set_rit", hz=125), _intent("set_xit", hz=-75),
        _intent("set_level", level="AF", value=0.5, receiver=1, vfo_arg="VFOB"), _intent("set_func", func="NB", on=True, receiver=1, vfo_arg="VFOB"),
        _intent("set_split_vfo", on=True, tx_vfo="VFOB"), _intent("send_raw", frame_bytes=bytes.fromhex("FEFE98E003FD")),
    ]
    results = [await executor.execute(intent) for intent in intents]
    assert handler._radio.calls == [("freq", _FREQUENCY, 1), ("mode", "USB", 2400, 1), ("rit", 125), ("rit-on", True), ("rit", -75), ("xit-on", True), ("raw", bytes.fromhex("FEFE98E003FD"))]
    assert handler.calls == [("packet", 1), ("vfo", "VFOB"), ("level", "AF", .5, 1, "VFOB"), ("func", "NB", True, 1, "VFOB"), ("split", True, "VFOB")]
    assert results[-1].details == {"values": []}
@pytest.mark.parametrize("intent", [_intent("set_vfo", vfo="VFOB"), _intent("set_level", level="AF", value=.5),
    _intent("set_func", func="NB", on=True), _intent("set_split_vfo", on=True, tx_vfo="VFOB")], ids=["vfo", "level", "func", "split"])
async def test_managed_non_ptt_leaf_errors_are_not_rewritten(intent):
    with pytest.raises(_RigctldCommandFailure) as failure:
        await _RigctldCommandExecutor(_LeafHandler(HamlibError.EIO)).execute(intent)
    assert failure.value.error is HamlibError.EIO
async def test_managed_tuner_normalizes_into_shared_queue_without_direct_admission():
    class Authority:
        async def admit_managed_write(self, _intent):
            raise AssertionError("rigctld adapter pre-admitted tuner write")
    class Radio:
        def supports_command(self, name, **_kwargs): return name == "set_tuner_status"
        async def set_tuner_status(self, value): raise AssertionError(f"direct tuner wire {value}")
    queue = CommandQueue()
    queue.bind_connection_generation(lambda: "rigctld-connection-1")
    handler = SimpleNamespace(
        _managed_tx_authority=Authority(), _command_queue=queue, _radio=Radio(),
        _config=RigctldConfig(command_timeout=1.0),
        _provider_generation_capture=lambda: 7,
    )
    task = asyncio.create_task(_RigctldCommandExecutor(handler).execute(
        _intent("set_func", func="TUNER", on=True, session_id="rigctld-client-1")
    ))
    await asyncio.sleep(0)
    entry = queue.take_entry()
    assert entry is not None and isinstance(entry.command, CommandIntent)
    assert (entry.command.name, entry.command.params["value"], entry.provider_generation, entry.connection_generation) == ("set_tuner_status", 1, 7, "rigctld-connection-1")
    assert entry.future is not None and not entry.future.done()
    entry.future.set_result(None)
    await task
async def test_managed_tuner_ignores_observed_rf_and_uses_authority_policy(managed):
    managed.store.apply_current(Observation(
        path=FieldPath.global_("tx_state", "ptt"), value=True,
        source=SourceMetadata(source="test", provider="tests"),
        timestamp_monotonic=asyncio.get_running_loop().time(), max_age=1e9,
    ))
    await _send(managed, "U TUNER 1")
    assert await _replies(managed, 1) == [b"RPRT 0\n"]
    assert ("tuner", 1) in managed.radio.calls

    await _key(managed)
    before = managed.radio.calls.count(("tuner", 1))
    await _send(managed, "U TUNER 1", "U TUNER 0")
    assert await _replies(managed, 2) == [b"RPRT -9\n", b"RPRT 0\n"]
    assert managed.radio.calls.count(("tuner", 1)) == before
    assert ("tuner", 0) in managed.radio.calls
async def test_injected_references_and_per_call_leaf_preserve_shared_default(managed):
    handler = managed.server._rig_handler
    assert (handler._managed_tx_authority, handler._command_service, handler._state_store) == (managed.authority, managed.service, managed.store)
    assert handler._command_queue is managed.queue and managed.server._command_queue is managed.queue
    managed.radio.release.set()
    await _send(managed, f"F {_FREQUENCY}")
    assert await _replies(managed, 1) == [b"RPRT 0\n"]
    assert managed.default.calls == []
async def test_rigctld_success_waits_for_shared_queue_provider_settlement(managed):
    gate = managed.lane.block_next()
    submissions, _ = _observe_starts(managed)
    await _send(managed, "T 1")
    reply = asyncio.create_task(_replies(managed, 1))
    try:
        while not submissions or not submissions[0][0].done():
            await asyncio.sleep(0)
        assert not submissions[0][0].result().settlement_done and not reply.done()
        gate.set()
        assert await reply == [b"RPRT 0\n"]
        assert submissions[0][0].result().settlement_done
        assert any(
            entry.positive_tx_submission is not None
            and entry.connection_generation == "rigctld-connection-1"
            for entry in managed.drained_entries
        )
    finally:
        gate.set()
        if not reply.done():
            reply.cancel()
        await asyncio.gather(reply, return_exceptions=True)
async def test_active_owner_off_provider_error_retains_release_debt(managed):
    await _key(managed)
    managed.lane.results.append(ActuationResult.UNCERTAIN)
    await _send(managed, "T 0")
    assert await _replies(managed, 1) == [b"RPRT 0\n"]
    while (await managed.authority.snapshot()).state.pending_effect is not None:
        await asyncio.sleep(0)
    state = (await managed.authority.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX and state.release_required
    await _clear_release_debt(managed)
@pytest.mark.parametrize("result", [ActuationResult.ACCEPTED, ActuationResult.UNCERTAIN])
async def test_active_owner_off_settles_while_frequency_is_pending(managed, result):
    await _key(managed)
    gate = managed.lane.block_next()
    managed.lane.results.append(result)
    await _send(managed, f"F {_FREQUENCY}", "T 0")
    await asyncio.wait_for(managed.radio.entered.wait(), _WAIT)
    await asyncio.wait_for(managed.lane.started.get(), _WAIT)
    assert not managed.radio.finished.is_set() and (await managed.authority.snapshot()).state.intent.kind is ManagedTxIntentKind.RX
    managed.radio.release.set()
    assert await _replies(managed, 2) == [b"RPRT 0\n", b"RPRT 0\n"]
    gate.set()
    if result is ActuationResult.UNCERTAIN:
        while (await managed.authority.snapshot()).state.pending_effect is not None:
            await asyncio.sleep(0)
        await _clear_release_debt(managed)
async def test_managed_off_execution_keeps_frozen_predecessor_barrier(managed):
    registered, writes = asyncio.Event(), []
    class ObservedFuture(asyncio.Future):
        def add_done_callback(self, callback, *, context=None):
            registered.set()
            return super().add_done_callback(callback, context=context)
    async def immediate(*args, **kwargs): return SimpleNamespace(values=[], error=HamlibError.OK)
    loop = asyncio.get_running_loop()
    predecessor_execution, predecessor_retirement = ObservedFuture(), loop.create_future()
    execution, retirement = loop.create_future(), loop.create_future()
    predecessor_retirement.set_result(None)
    managed.server._handler_execute_call = immediate
    session = SimpleNamespace(client_id=1, peername="test", extended_mode=False, vfo_mode=False)
    command = managed.server._protocol.parse_line(b"T 0", session)
    command_task = loop.create_task(managed.server._execute_and_retire_client_command(
        writer=SimpleNamespace(write=writes.append, drain=lambda: immediate()), session=session,
        session_id="rigctld-client-1", client_id=1, cmd=command, immediate_error=None,
        predecessor_execution=predecessor_execution, predecessor_retirement=predecessor_retirement,
        execution=execution, retirement=retirement, ptt_submission=None, ptt_ready=None))
    barrier_task = loop.create_task(registered.wait())
    try:
        done, _ = await asyncio.wait((command_task, barrier_task), return_when=asyncio.FIRST_COMPLETED)
        assert done == {barrier_task}
        assert not predecessor_execution.done() and not execution.done() and not command_task.done() and writes == []
    finally:
        if not predecessor_execution.done():
            predecessor_execution.set_result(None)
        await command_task
        barrier_task.cancel()
        await asyncio.gather(barrier_task, return_exceptions=True)
    assert execution.done() and retirement.done() and writes == [b"RPRT 0\n"]
async def test_capacity_reserve_admits_off_and_retires_all_prior_error_slots(managed):
    await _key(managed)
    count = _MAX_PENDING_CLIENT_RESPONSES - 1
    await _send(managed, f"F {_FREQUENCY}", *(["unknown"] * count), "T 0")
    await asyncio.wait_for(managed.radio.entered.wait(), _WAIT)
    await asyncio.wait_for(managed.lane.started.get(), _WAIT)
    assert (await managed.authority.snapshot()).state.intent.kind is ManagedTxIntentKind.RX
    managed.radio.release.set()
    assert await _replies(managed, count + 2) == [b"RPRT 0\n", *([b"RPRT -4\n"] * count), b"RPRT 0\n"]
async def test_no_non_ptt_queue_or_classification_execution(managed):
    managed.radio.release.set()
    await _send(managed, f"F {_FREQUENCY}")
    assert await _replies(managed, 1) == [b"RPRT 0\n"] and managed.default.calls == []
    assert managed.server._command_queue is managed.queue and managed.queue.pending_count == 0
async def test_frequency_provider_rejection_preserves_terminal_error(managed):
    managed.radio.fail_frequency = True
    await _send(managed, f"F {_FREQUENCY}")
    await asyncio.wait_for(managed.radio.entered.wait(), _WAIT)
    managed.radio.release.set()
    assert await _replies(managed, 1) == [b"RPRT -6\n"]
@pytest.mark.parametrize("vfo", ["VFOA", "VFOB"], ids=["valid-vfo", "invalid-vfo"])
async def test_pending_on_is_revoked_between_spawn_and_validation(managed, vfo):
    managed.radio._profile = SerialMockRadio(model="IC-7300" if vfo == "VFOB" else "IC-7610").profile
    entered, release = _gate_on_handler(managed)
    submissions, _ = _observe_starts(managed)
    await _send(managed, f"T {vfo} 1")
    await asyncio.wait_for(entered.wait(), _WAIT)
    await _send(managed, "T 0")
    while not submissions[0][0].done():
        await asyncio.sleep(0)
    assert (submissions[0][0].cancelled() or submissions[0][0].result().outcome is ManagedTxOutcome.REJECTED) and not submissions[0][1].done()
    release.set()
    assert await _replies(managed, 2) == [b"RPRT -9\n", b"RPRT 0\n"] and not submissions[0][1].done()
    assert [effect.operation for effect in managed.lane.effects] == []
@pytest.mark.parametrize(("outcome", "first"), [(ManagedTxOutcome.ACCEPTED, b"RPRT 0\n"),
    (ManagedTxOutcome.REJECTED, b"RPRT -9\n"), (None, b"RPRT -6\n")], ids=["accepted", "rejected", "authority-error"])
async def test_revocation_marker_preserves_exact_submission_result(managed, outcome, first):
    entered, release = _gate_on_handler(managed)
    settle = asyncio.Event()
    async def controlled():
        await settle.wait()
        if outcome is None:
            raise RuntimeError("injected authority failure")
        return SimpleNamespace(outcome=outcome, wait_settlement=lambda: asyncio.sleep(0))
    submissions, off_started = _observe_starts(managed, lambda: asyncio.create_task(controlled()))
    await _send(managed, "T 1")
    await asyncio.wait_for(entered.wait(), _WAIT)
    await _send(managed, "T 0")
    await asyncio.wait_for(off_started.wait(), _WAIT)
    settle.set()
    release.set()
    assert await _replies(managed, 2) == [first, b"RPRT 0\n"] and not submissions[0][1].done()
async def test_invalid_validation_committed_before_off_preserves_evfo(managed):
    managed.radio._profile = SerialMockRadio(model="IC-7300").profile
    committed, release = _gate_on_handler(managed, after=True)
    submissions, off_started = _observe_starts(managed)
    await _send(managed, "T VFOB 1")
    await asyncio.wait_for(committed.wait(), _WAIT)
    assert submissions[0][1].validation_committed and not submissions[0][1].done()
    await _send(managed, "T 0")
    await asyncio.wait_for(off_started.wait(), _WAIT)
    release.set()
    assert await _replies(managed, 2) == [b"RPRT -16\n", b"RPRT 0\n"]
    assert len(submissions) == 1 and not submissions[0][1].done() and managed.lane.effects == []
async def test_pending_on_defers_handler_state_evaluation_until_predecessor(managed):
    handler, seen = managed.server._rig_handler, []
    execute = handler.execute
    async def observed(cmd, **kwargs):
        if cmd.long_cmd == "set_ptt":
            seen.append(managed.radio.finished.is_set())
        return await execute(cmd, **kwargs)
    handler.execute = observed
    await _send(managed, f"F {_FREQUENCY}", "T 1")
    await asyncio.wait_for(managed.radio.entered.wait(), _WAIT)
    await asyncio.sleep(0)
    assert seen == []
    managed.radio.release.set()
    assert await _replies(managed, 2) == [b"RPRT 0\n"] * 2
    assert seen == [True]
@pytest.mark.parametrize("provider_error", [False, True])
async def test_off_bypasses_but_later_on_keeps_held_frequency_barrier(managed, provider_error):
    await _key(managed)
    later_on_received = asyncio.Event()
    start, execute = managed.authority.start_ptt_submission, managed.server._rig_handler.execute
    def observed(on, owner, **kwargs):
        submission = start(on, owner, **kwargs)
        if on:
            later_on_received.set()
        return submission
    async def gated(cmd, **kwargs):
        if cmd.long_cmd == "set_ptt" and not int(cmd.args[0]):
            await asyncio.wait_for(later_on_received.wait(), _WAIT)
        return await execute(cmd, **kwargs)
    managed.authority.start_ptt_submission = observed
    managed.server._rig_handler.execute = gated
    managed.radio.fail_frequency = provider_error
    await _send(managed, f"F {_FREQUENCY}", "T 0", "T 1")
    await asyncio.wait_for(managed.radio.entered.wait(), _WAIT)
    await asyncio.wait_for(managed.lane.started.get(), _WAIT)
    assert [effect.operation for effect in managed.lane.effects] == [ActuationOperation.PTT_ON, ActuationOperation.FORCE_RECEIVE]
    managed.radio.release.set()
    first = b"RPRT -6\n" if provider_error else b"RPRT 0\n"
    assert await _replies(managed, 3) == [first, b"RPRT 0\n", b"RPRT 0\n"]
    assert managed.lane.effects[-1].operation is ActuationOperation.PTT_ON
@pytest.mark.parametrize("ending", ["eof", "server-stop"])
async def test_disconnect_revokes_pending_on(managed, ending):
    await _send(managed, f"F {_FREQUENCY}", "T 1")
    await asyncio.wait_for(managed.radio.entered.wait(), _WAIT)
    connections = tuple(managed.server._client_tasks)
    if ending == "eof":
        managed.writer.close()
        await managed.writer.wait_closed()
    else:
        await managed.server.stop()
    managed.radio.release.set()
    await asyncio.gather(*connections, return_exceptions=True)
    assert all(task.done() for task in connections) and managed.lane.effects == []
async def test_sequential_frequency_then_ptt_preserves_wire_order(managed):
    managed.radio.release.set()
    for command in (f"F {_FREQUENCY}", "T 1", "T 0"):
        await _send(managed, command)
        assert await _replies(managed, 1) == [b"RPRT 0\n"]
    assert [effect.operation for effect in managed.lane.effects] == [ActuationOperation.PTT_ON, ActuationOperation.FORCE_RECEIVE]
_FREQ_LINE = f"{_FREQUENCY}\n".encode("ascii")
def _spawn_slot(server, *, writer, session, cmd, predecessor_execution, predecessor_retirement, execution, retirement):
    return asyncio.get_running_loop().create_task(server._execute_and_retire_client_command(
        writer=writer, session=session, session_id="rigctld-client-1", client_id=1, cmd=cmd, immediate_error=None,
        predecessor_execution=predecessor_execution, predecessor_retirement=predecessor_retirement,
        execution=execution, retirement=retirement, ptt_submission=None, ptt_ready=None))
async def _cancel_and_join(*tasks):
    owned = [task for task in tasks if task is not None]
    for task in owned:
        if not task.done():
            task.cancel()
    gathered = asyncio.gather(*owned, return_exceptions=True)
    results = await asyncio.wait_for(asyncio.shield(gathered), _WAIT)
    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            raise result
async def test_cancelled_shield_wait_still_settles_both_futures_and_unblocks_successor(managed):
    registered = asyncio.Event()
    class ObservedFuture(asyncio.Future):
        def add_done_callback(self, callback, *, context=None):
            registered.set()
            return super().add_done_callback(callback, context=context)
    calls = []
    async def fake_handler(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("cmd"))
        return SimpleNamespace(values=[str(_FREQUENCY)], error=HamlibError.OK)
    managed.server._handler_execute_call = fake_handler
    loop = asyncio.get_running_loop()
    writes = []
    async def no_drain(): return None
    writer = SimpleNamespace(write=writes.append, drain=lambda: no_drain())
    predecessor_execution, predecessor_retirement = ObservedFuture(), loop.create_future()
    predecessor_retirement.set_result(None)
    execution, retirement = loop.create_future(), loop.create_future()
    successor_execution, successor_retirement = loop.create_future(), loop.create_future()
    session = SimpleNamespace(client_id=1, peername="test", extended_mode=False, vfo_mode=False)
    command = managed.server._protocol.parse_line(b"f", session)
    successor_command = managed.server._protocol.parse_line(b"f", session)
    command_task = _spawn_slot(managed.server, writer=writer, session=session, cmd=command,
        predecessor_execution=predecessor_execution, predecessor_retirement=predecessor_retirement,
        execution=execution, retirement=retirement)
    successor_task = None
    try:
        await asyncio.wait_for(registered.wait(), _WAIT)
        command_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(command_task, _WAIT)
        assert command_task.cancelled() and writes == [] and calls == []
        assert execution.done() and retirement.done()
        assert not predecessor_execution.done() and not predecessor_execution.cancelled()
        successor_task = _spawn_slot(managed.server, writer=writer, session=session, cmd=successor_command,
            predecessor_execution=execution, predecessor_retirement=retirement,
            execution=successor_execution, retirement=successor_retirement)
        await asyncio.wait_for(successor_task, _WAIT)
        assert successor_execution.done() and successor_retirement.done()
        assert calls == [successor_command] and writes == [_FREQ_LINE]
    finally:
        if not predecessor_execution.done():
            predecessor_execution.set_result(None)
        await _cancel_and_join(command_task, successor_task)
async def test_cancelled_handler_execution_still_settles_both_futures_and_unblocks_successor(managed):
    entered, release, exited, calls = asyncio.Event(), asyncio.Event(), asyncio.Event(), []
    async def gated_handler(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("cmd"))
        if len(calls) == 1:
            entered.set()
            try:
                await release.wait()
            finally:
                exited.set()
        return SimpleNamespace(values=[str(_FREQUENCY)], error=HamlibError.OK)
    managed.server._handler_execute_call = gated_handler
    loop = asyncio.get_running_loop()
    writes = []
    async def no_drain(): return None
    writer = SimpleNamespace(write=writes.append, drain=lambda: no_drain())
    predecessor_execution, predecessor_retirement = loop.create_future(), loop.create_future()
    predecessor_execution.set_result(None)
    predecessor_retirement.set_result(None)
    execution, retirement = loop.create_future(), loop.create_future()
    successor_execution, successor_retirement = loop.create_future(), loop.create_future()
    session = SimpleNamespace(client_id=1, peername="test", extended_mode=False, vfo_mode=False)
    command = managed.server._protocol.parse_line(b"f", session)
    successor_command = managed.server._protocol.parse_line(b"f", session)
    command_task = _spawn_slot(managed.server, writer=writer, session=session, cmd=command,
        predecessor_execution=predecessor_execution, predecessor_retirement=predecessor_retirement,
        execution=execution, retirement=retirement)
    successor_task = None
    try:
        await asyncio.wait_for(entered.wait(), _WAIT)
        command_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(command_task, _WAIT)
        assert command_task.cancelled()
        await asyncio.wait_for(exited.wait(), _WAIT)
        assert writes == [] and len(calls) == 1
        assert execution.done() and retirement.done()
        assert predecessor_execution.done() and not predecessor_execution.cancelled()
        successor_task = _spawn_slot(managed.server, writer=writer, session=session, cmd=successor_command,
            predecessor_execution=execution, predecessor_retirement=retirement,
            execution=successor_execution, retirement=successor_retirement)
        await asyncio.wait_for(successor_task, _WAIT)
        assert successor_execution.done() and successor_retirement.done()
        assert len(calls) == 2 and writes == [_FREQ_LINE]
    finally:
        release.set()
        await _cancel_and_join(command_task, successor_task)
async def test_pre_start_cancelled_response_task_still_retires_successor(managed, monkeypatch):
    loop = asyncio.get_running_loop()
    seen = []
    real_create_task = type(loop).create_task
    def spy(self, coro, *args, **kwargs):
        task = real_create_task(self, coro, *args, **kwargs)
        if getattr(coro, "__qualname__", "") == "RigctldServer._execute_and_retire_client_command":
            seen.append(task)
            if len(seen) == 2:
                task.cancel()
        return task
    monkeypatch.setattr(type(loop), "create_task", spy)
    await _send(managed, "f", "f", "f")
    first, third = await _replies(managed, 2)
    assert len(seen) == 3 and seen[1].cancelled()
    expected = f"{_FREQUENCY}\n".encode("ascii")
    assert first == expected and third == expected
    await _send(managed, "f")
    (fourth,) = await _replies(managed, 1)
    assert fourth == expected
@pytest.mark.parametrize("cancel_phase", ["handler", "retirement"])
async def test_cancelled_slot_does_not_let_successor_bypass_earlier_retirement(managed, cancel_phase):
    entered_b, release_b, exited_b, calls = asyncio.Event(), asyncio.Event(), asyncio.Event(), []
    drain_entered, drain_release = asyncio.Event(), asyncio.Event()
    b_waiting_retirement, c_waiting_retirement = asyncio.Event(), asyncio.Event()
    class ObservedFuture(asyncio.Future):
        def __init__(self, observed):
            super().__init__()
            self.observed = observed
        def add_done_callback(self, callback, *, context=None):
            self.observed.set()
            return super().add_done_callback(callback, context=context)
    session = SimpleNamespace(client_id=1, peername="test", extended_mode=False, vfo_mode=False)
    command_a = managed.server._protocol.parse_line(b"f", session)
    command_b = managed.server._protocol.parse_line(b"f", session)
    command_c = managed.server._protocol.parse_line(b"f", session)
    async def gated_handler(*args, **kwargs):
        command = args[0] if args else kwargs.get("cmd")
        calls.append(command)
        if command is command_b:
            entered_b.set()
            try:
                await release_b.wait()
            finally:
                exited_b.set()
        return SimpleNamespace(values=[str(_FREQUENCY)], error=HamlibError.OK)
    managed.server._handler_execute_call = gated_handler
    async def gated_drain():
        drain_entered.set()
        await drain_release.wait()
    async def no_drain(): return None
    loop = asyncio.get_running_loop()
    writes = []
    writer_a = SimpleNamespace(write=writes.append, drain=lambda: gated_drain())
    writer_after = SimpleNamespace(write=writes.append, drain=lambda: no_drain())
    base_execution, base_retirement = loop.create_future(), loop.create_future()
    base_execution.set_result(None)
    base_retirement.set_result(None)
    execution_a, retirement_a = loop.create_future(), ObservedFuture(b_waiting_retirement)
    execution_b, retirement_b = loop.create_future(), ObservedFuture(c_waiting_retirement)
    execution_c, retirement_c = loop.create_future(), loop.create_future()
    task_a = _spawn_slot(managed.server, writer=writer_a, session=session, cmd=command_a,
        predecessor_execution=base_execution, predecessor_retirement=base_retirement,
        execution=execution_a, retirement=retirement_a)
    task_b, task_c = None, None
    try:
        await asyncio.wait_for(drain_entered.wait(), _WAIT)
        assert writes == [_FREQ_LINE] and not task_a.done() and not retirement_a.done()
        task_b = _spawn_slot(managed.server, writer=writer_after, session=session, cmd=command_b,
            predecessor_execution=execution_a, predecessor_retirement=retirement_a,
            execution=execution_b, retirement=retirement_b)
        await asyncio.wait_for(entered_b.wait(), _WAIT)
        if cancel_phase == "retirement":
            release_b.set()
            await asyncio.wait_for(b_waiting_retirement.wait(), _WAIT)
        assert calls == [command_a, command_b]
        task_b.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task_b, _WAIT)
        assert exited_b.is_set() and execution_b.done() and not retirement_b.done()
        task_c = _spawn_slot(managed.server, writer=writer_after, session=session, cmd=command_c,
            predecessor_execution=execution_b, predecessor_retirement=retirement_b,
            execution=execution_c, retirement=retirement_c)
        await asyncio.wait_for(c_waiting_retirement.wait(), _WAIT)
        assert execution_c.done() and not retirement_c.done() and not task_c.done()
        assert not retirement_a.done() and not retirement_b.done()
        assert calls == [command_a, command_b, command_c] and writes == [_FREQ_LINE]
        drain_release.set()
        await asyncio.wait_for(task_a, _WAIT)
        await asyncio.wait_for(task_c, _WAIT)
        assert task_b.cancelled()
        assert execution_a.done() and retirement_a.done()
        assert execution_b.done() and retirement_b.done()
        assert execution_c.done() and retirement_c.done()
        assert writes == [_FREQ_LINE, _FREQ_LINE]
    finally:
        drain_release.set()
        release_b.set()
        await _cancel_and_join(task_a, task_b, task_c)
async def test_repeated_cancellation_waits_for_owned_handler_cleanup(managed):
    entered, cleanup_entered, cleanup_release, exited = (asyncio.Event() for _ in range(4))
    async def gated_handler(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_entered.set()
            await cleanup_release.wait()
            exited.set()
    managed.server._handler_execute_call = gated_handler
    loop = asyncio.get_running_loop()
    writes = []
    async def no_drain(): return None
    writer = SimpleNamespace(write=writes.append, drain=lambda: no_drain())
    predecessor_execution, predecessor_retirement = loop.create_future(), loop.create_future()
    predecessor_execution.set_result(None)
    predecessor_retirement.set_result(None)
    execution, retirement = loop.create_future(), loop.create_future()
    session = SimpleNamespace(client_id=1, peername="test", extended_mode=False, vfo_mode=False)
    command = managed.server._protocol.parse_line(b"f", session)
    task = _spawn_slot(managed.server, writer=writer, session=session, cmd=command,
        predecessor_execution=predecessor_execution, predecessor_retirement=predecessor_retirement,
        execution=execution, retirement=retirement)
    try:
        await asyncio.wait_for(entered.wait(), _WAIT)
        task.cancel()
        await asyncio.wait_for(cleanup_entered.wait(), _WAIT)
        task.cancel()
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, _WAIT)
        assert exited.is_set() and task.cancelled() and writes == []
        assert execution.done() and retirement.done()
    finally:
        cleanup_release.set()
        await _cancel_and_join(task)
async def test_pre_start_cancelled_response_drains_fake_submission_before_disconnect(managed, monkeypatch):
    cleanup_entered, cleanup_release, exited = (asyncio.Event() for _ in range(3))
    submissions, responses = [], []
    async def fake_submission():
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_entered.set()
            await cleanup_release.wait()
            exited.set()
    def fake_start(*args, **kwargs):
        task = asyncio.create_task(fake_submission())
        submissions.append(task)
        return task
    managed.authority.start_ptt_submission = fake_start
    loop = asyncio.get_running_loop()
    real_create_task = type(loop).create_task
    def spy(self, coro, *args, **kwargs):
        task = real_create_task(self, coro, *args, **kwargs)
        if getattr(coro, "__qualname__", "") == "RigctldServer._execute_and_retire_client_command":
            responses.append(task)
            task.cancel()
        return task
    monkeypatch.setattr(type(loop), "create_task", spy)
    retirement_entered, retiring_tasks = asyncio.Event(), []
    retire_connection = managed.server._retire_client_connection
    async def observed_retirement(**kwargs):
        retiring_tasks.extend(kwargs["tasks"])
        retirement_entered.set()
        return await retire_connection(**kwargs)
    managed.server._retire_client_connection = observed_retirement
    clients = ()
    try:
        await _send(managed, "T 1")
        await asyncio.wait_for(cleanup_entered.wait(), _WAIT)
        assert len(responses) == 1 and responses[0].cancelled()
        clients = tuple(managed.server._client_tasks)
        assert len(clients) == 1
        await _send(managed, "q")
        await asyncio.wait_for(retirement_entered.wait(), _WAIT)
        assert submissions[0] in retiring_tasks
        assert not clients[0].done() and not submissions[0].done() and not exited.is_set()
        assert managed.lane.effects == []
        cleanup_release.set()
        await asyncio.wait_for(asyncio.gather(*clients), _WAIT)
        assert exited.is_set() and submissions[0].cancelled()
        assert await asyncio.wait_for(managed.reader.read(), _WAIT) == b""
        assert managed.lane.effects == []
    finally:
        cleanup_release.set()
        await _cancel_and_join(*responses, *submissions, *clients)
async def test_managed_vox_on_is_refused_while_another_owner_holds_tx(managed):
    # MOR-3534: VOX arms keying, so it is a TX start a non-holder may not issue.
    receipt = await managed.authority.submit_ptt(True, "websocket-1")
    assert receipt.outcome is ManagedTxOutcome.ACCEPTED
    await receipt.wait_settlement()
    await _send(managed, "U VOX 1", "U VOX 0")
    assert await _replies(managed, 2) == [b"RPRT -9\n", b"RPRT 0\n"]
    assert managed.radio._vox_on is False
    await _clear_release_debt(managed)
    receipt = await managed.authority.submit_ptt(True, "rigctld-client-1")
    assert receipt.outcome is ManagedTxOutcome.ACCEPTED
    await receipt.wait_settlement()
    await _send(managed, "U VOX 1")
    assert await _replies(managed, 1) == [b"RPRT 0\n"]
    assert managed.radio._vox_on is True
    await _clear_release_debt(managed)

# fmt: on


@pytest.fixture(params=MODELS)
async def managed_split(request):
    queue = CommandQueue()
    queue.bind_connection_generation(lambda: "split-connection")
    refs = {}

    def configure(radio):
        controller = Safety().authority()
        radio._controller_authority = controller
        queue.bind_controller(controller)
        managed, *_ = authority(generation=radio.state_store.provider_generation)
        refs["authority"] = managed
        default = _Default()
        service = CommandService(executor=default, state_store=radio.state_store)
        refs["default"] = default
        return {
            "managed_tx_authority": managed,
            "command_queue": queue,
            "command_service": service,
        }

    with patch.object(
        queue, "put_ordered", wraps=queue.put_ordered, spec=queue.put_ordered
    ) as ordered:
        try:
            async with _peer(request.param, configure=configure) as (
                radio,
                peer,
                server,
                reader,
                writer,
            ):
                yield SimpleNamespace(
                    queue=queue,
                    ordered=ordered,
                    authority=refs["authority"],
                    default=refs["default"],
                    radio=radio,
                    peer=peer,
                    server=server,
                    reader=reader,
                    writer=writer,
                    clock=asyncio.get_running_loop().time,
                )
        finally:
            if "authority" in refs:
                await refs["authority"].close()


async def _drain_split_entry(rig, entry):
    async def execute(claimed):
        assert isinstance(claimed.command, CommandIntent)

        def validate():
            validate_command_queue_entry_currency(
                claimed,
                now=rig.clock(),
                provider_generation=rig.radio.state_store.provider_generation,
                connection_generation="split-connection",
                session_is_live=rig.queue.session_is_live,
                require_connection_generation=True,
            )

        validate()
        await execute_command_intent(
            rig.radio,
            claimed.command,
            managed_tx_authority=rig.authority,
            validate_currency=validate,
        )

    await execute_command_queue_entry(entry, execute)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"I 14080100\n", b"X PKTUSB 2400\n"])
@pytest.mark.parametrize(
    "change", [None, "authority", "provider", "session", "deadline"]
)
async def test_managed_split_currency_change_while_commander_queued_never_sets(
    managed_split, monkeypatch, command, change
):
    rig = managed_split
    before = dict(rig.peer.slots)
    async with _hold_commander_before_set(monkeypatch, rig.radio, rig.peer) as (
        queued,
        release,
    ):
        reply = asyncio.create_task(_exchange(rig.writer, rig.reader, command))
        drain = None
        try:
            await rig.queue.wait(1)
            entry = rig.queue.take_entry()
            assert entry is not None and entry.future is not None
            drain = asyncio.create_task(_drain_split_entry(rig, entry))
            await asyncio.wait_for(queued.wait(), 1)
            assert rig.radio._commander._queue.qsize() == 1
            assert not entry.future.done()
            if change == "authority":
                await rig.radio._controller_authority.set_mode("remote")
            elif change == "provider":
                rig.radio.state_store.begin_provider_generation()
            elif change == "session":
                assert entry.session_id is not None
                assert rig.queue.session_is_live(entry.session_id)
                rig.queue.unregister_session(entry.session_id)
            elif change == "deadline":
                assert entry.expires_at_monotonic is not None
                rig.clock = lambda: entry.expires_at_monotonic + 1
            assert not entry.future.done()
            release.set()
            await asyncio.wait_for(asyncio.gather(drain, return_exceptions=True), 2)
            if change is None:
                assert await reply == b"RPRT 0\n"
                assert rig.peer.slots["B"] != before["B"]
            else:
                expected = b"RPRT -22\n" if change == "authority" else b"RPRT -11\n"
                assert await reply == expected
                assert not any(
                    cmd in (0x25, 0x26) and len(data) > 1
                    for cmd, _sub, data in rig.peer.requests
                )
                assert rig.peer.slots == before
            assert rig.peer.slots["A"] == before["A"]
            assert rig.peer.selected == "A"
            assert not rig.default.calls
            _assert_no_relative_overlays(rig.server)
        finally:
            release.set()
            for task in (reply, drain):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (reply, drain) if task is not None),
                return_exceptions=True,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"X USB\n", b"X USB 0\n"])
async def test_managed_split_selection_change_after_preservation_never_sets(
    managed_split, monkeypatch, command
):
    rig = managed_split
    before = dict(rig.peer.slots)
    read_done, release = _pause_mode_preservation(monkeypatch, rig.radio)
    reply = asyncio.create_task(_exchange(rig.writer, rig.reader, command))
    drain = None
    try:
        await rig.queue.wait(1)
        entry = rig.queue.take_entry()
        assert entry is not None
        rig.ordered.assert_called_once()
        drain = asyncio.create_task(_drain_split_entry(rig, entry))
        await asyncio.wait_for(read_done.wait(), 1)
        async with _client(rig.server) as (other_reader, other_writer):
            assert (
                await _exchange(other_writer, other_reader, b"V VFOB\n") == b"RPRT 0\n"
            )
        assert rig.peer.selected == "B"
        release.set()
        await asyncio.wait_for(asyncio.gather(drain, return_exceptions=True), 2)
        assert await reply == b"RPRT -9\n"
        assert not any(
            cmd == 0x26 and len(data) > 1 for cmd, _sub, data in rig.peer.requests
        )
        assert rig.peer.slots == before
        assert rig.queue.pending_count == 0
        assert not rig.default.calls
        rig.ordered.assert_called_once()
        _assert_no_relative_overlays(rig.server)
    finally:
        release.set()
        for task in (reply, drain):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (reply, drain) if task is not None),
            return_exceptions=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "name"),
    [(b"I 14080100\n", "set_split_freq"), (b"X PKTUSB 2400\n", "set_split_mode")],
)
async def test_managed_split_writes_use_one_shared_ordered_entry(
    managed_split, command, name
):
    rig = managed_split
    _rf_state(rig.server, True)
    before = dict(rig.peer.slots)
    reply = asyncio.create_task(_exchange(rig.writer, rig.reader, command))
    try:
        await rig.queue.wait(1)
        entry = rig.queue.take_entry()
        assert entry is not None
        rig.ordered.assert_called_once()
        assert entry.command.name == name
        assert entry.command.params["selected"] is False
        assert entry.provider_generation == rig.radio.state_store.provider_generation
        assert entry.connection_generation == "split-connection"
        assert rig.queue.take_entry() is None
        assert rig.peer.slots == before
        assert not rig.peer.requests
        assert not reply.done()
        await _drain_split_entry(rig, entry)
        assert await reply == b"RPRT 0\n"
        if name == "set_split_freq":
            assert rig.peer.slots["B"][0] == 14_080_100
        else:
            assert rig.peer.slots["B"][1:] == (1, 2, 1)
        assert rig.peer.slots["A"] == before["A"]
        assert rig.peer.selected == "A"
        assert not rig.default.calls
        _assert_no_relative_overlays(rig.server)
    finally:
        if not reply.done():
            reply.cancel()
        await asyncio.gather(reply, return_exceptions=True)


def _shield_split_completion_wait(monkeypatch, rig):
    wait_for = asyncio.wait_for

    async def wait_without_cancelling_completion(awaitable, timeout):
        call = rig.ordered.call_args
        if call is not None and awaitable is call.kwargs["future"]:
            return await wait_for(asyncio.shield(awaitable), timeout=timeout)
        return await wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(
        rigctld_handler.asyncio, "wait_for", wait_without_cancelling_completion
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"I 14080100\n", b"X USB 2400\n"])
@pytest.mark.parametrize("ending", ["timeout", "disconnect"])
async def test_managed_split_cancelled_or_timed_out_entry_never_mutates_late(
    managed_split, monkeypatch, command, ending
):
    rig = managed_split
    _shield_split_completion_wait(monkeypatch, rig)
    rig.server._config.command_timeout = 0.05 if ending == "timeout" else 2
    before = dict(rig.peer.slots)
    reply = asyncio.create_task(_exchange(rig.writer, rig.reader, command))
    try:
        await rig.queue.wait(1)
        entry = rig.queue.take_entry()
        assert entry is not None and entry.future is not None
        rig.ordered.assert_called_once()
        if ending == "timeout":
            assert await reply == b"RPRT -5\n"
        else:
            rig.writer.close()
            await rig.writer.wait_closed()
            await asyncio.wait_for(
                asyncio.gather(*tuple(rig.server._client_tasks)),
                2,
            )
        assert entry.future.cancelled()
        await _drain_split_entry(rig, entry)
        assert rig.peer.slots == before
        assert not rig.peer.requests
        assert rig.queue.pending_count == 0
        _assert_no_relative_overlays(rig.server)
    finally:
        if not reply.done():
            reply.cancel()
        await asyncio.gather(reply, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["timeout", "disconnect"])
async def test_managed_split_mode_cancelled_during_preservation_read_never_sets(
    managed_split, monkeypatch, ending
):
    rig = managed_split
    _shield_split_completion_wait(monkeypatch, rig)
    rig.server._config.command_timeout = 0.05 if ending == "timeout" else 2
    before = dict(rig.peer.slots)
    gate = rig.peer.relative_mode_read_gate = asyncio.Event()
    reply = asyncio.create_task(_exchange(rig.writer, rig.reader, b"X USB\n"))
    drain = None
    try:
        await rig.queue.wait(1)
        entry = rig.queue.take_entry()
        assert entry is not None and entry.future is not None
        drain = asyncio.create_task(_drain_split_entry(rig, entry))
        await asyncio.wait_for(rig.peer.relative_mode_read.wait(), 1)
        assert rig.peer.slots == before
        if ending == "timeout":
            assert await reply == b"RPRT -5\n"
        else:
            rig.writer.close()
            await rig.writer.wait_closed()
            await asyncio.wait_for(asyncio.gather(*tuple(rig.server._client_tasks)), 2)
        assert entry.future.cancelled()
        gate.set()
        await asyncio.wait_for(asyncio.gather(drain, return_exceptions=True), 2)
        assert not any(
            command == 0x26 and len(data) > 1
            for command, _sub, data in rig.peer.requests
        )
        assert rig.peer.slots == before
        assert rig.peer.selected == "A"
        _assert_no_relative_overlays(rig.server)
    finally:
        gate.set()
        for task in (reply, drain):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (reply, drain) if task is not None),
            return_exceptions=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"X USB\n", b"X USB 0\n"])
async def test_managed_split_mode_authority_change_during_preservation_never_sets(
    managed_split, command
):
    rig = managed_split
    before = dict(rig.peer.slots)
    gate = rig.peer.relative_mode_read_gate = asyncio.Event()
    reply = asyncio.create_task(_exchange(rig.writer, rig.reader, command))
    drain = None
    try:
        await rig.queue.wait(1)
        entry = rig.queue.take_entry()
        assert entry is not None and entry.controller_ticket is not None
        drain = asyncio.create_task(_drain_split_entry(rig, entry))
        await asyncio.wait_for(rig.peer.relative_mode_read.wait(), 1)
        await rig.radio._controller_authority.set_mode("remote")
        gate.set()
        await asyncio.wait_for(asyncio.gather(drain, return_exceptions=True), 2)
        assert await reply == b"RPRT -22\n"
        assert not any(
            cmd == 0x26 and len(data) > 1 for cmd, _sub, data in rig.peer.requests
        )
        assert rig.peer.slots == before
        assert rig.peer.selected == "A"
        _assert_no_relative_overlays(rig.server)
    finally:
        gate.set()
        for task in (reply, drain):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (reply, drain) if task is not None),
            return_exceptions=True,
        )
