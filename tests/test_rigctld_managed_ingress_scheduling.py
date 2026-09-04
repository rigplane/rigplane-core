# fmt: off
from __future__ import annotations
import asyncio
from types import SimpleNamespace
import pytest
from rigplane.core.command_service import CommandService
from rigplane.core.exceptions import ConnectionError as ProviderConnectionError
from rigplane.core.state_pipeline_contracts import CommandIntent, FieldPath, Observation, SourceMetadata
from rigplane.core.state_store import StateStore
from rigplane.core.tx_interlock_contract import TxInterlockDisposition
from rigplane.rigctld import handler as rigctld_handler
from rigplane.rigctld.contract import HamlibError, RigctldConfig
from rigplane.rigctld.handler import RigctldHandler, _RigctldCommandExecutor, _RigctldCommandFailure
from rigplane.rigctld.server import _MAX_PENDING_CLIENT_RESPONSES, RigctldServer
from rigplane.runtime.managed_tx_state import ActuationOperation, ActuationResult, ManagedTxIntentKind, ManagedTxOutcome
from serial_stub import SerialMockRadio
from test_managed_tx_authority import authority
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
    async def set_freq(self, freq, receiver=0):
        self.calls.append(("F", freq, receiver))
        self.entered.set()
        await self.release.wait()
        self.finished.set()
        if self.fail_frequency:
            raise ProviderConnectionError("injected")
@pytest.fixture
async def managed():
    managed, _, _, _, fence, lane = authority()
    store = StateStore()
    store.apply_current(Observation(path=FieldPath.global_("tx_state", "ptt"), value=False, source=SourceMetadata(source="test", provider="tests"),
        timestamp_monotonic=asyncio.get_running_loop().time(), max_age=1e9))
    radio, default = _Radio(store), _Default()
    await radio.connect()
    service = CommandService(executor=default, state_store=store)
    server = RigctldServer(radio, RigctldConfig(host="127.0.0.1", port=0, command_timeout=1.0), managed_tx_authority=managed, command_service=service)
    await server.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", server._server.sockets[0].getsockname()[1])
    rig = SimpleNamespace(authority=managed, fence=fence, lane=lane, store=store, radio=radio, default=default, service=service, server=server, reader=reader, writer=writer)
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
    def observed(on, owner, *, ready=None):
        task = on_start() if on and on_start else start(on, owner, ready=ready)
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
@pytest.mark.parametrize("present", [(1, 0), (0, 1)])
def test_partial_managed_configuration_is_rejected_before_bootstrap(present):
    refs = {name: _NoTruthProbe() if value else None for name, value in zip(("managed_tx_authority", "command_service"), present, strict=True)}
    for constructor in (RigctldServer, RigctldHandler):
        with pytest.raises(ValueError, match="together"):
            constructor(object(), RigctldConfig(), **refs)
def test_managed_configuration_rejects_custom_handler():
    with pytest.raises(ValueError, match="_handler"):
        RigctldServer(object(), _handler=object(), managed_tx_authority=_NoTruthProbe(), command_service=_NoTruthProbe())
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
        _intent("set_split_vfo", on=True, tx_vfo="VFOB"), _intent("send_raw", frame_bytes=b"\xfe\xfd"),
    ]
    results = [await executor.execute(intent) for intent in intents]
    assert handler._radio.calls == [("freq", _FREQUENCY, 1), ("mode", "USB", 2400, 1), ("rit", 125), ("rit-on", True), ("rit", -75), ("xit-on", True), ("raw", b"\xfe\xfd")]
    assert handler.calls == [("packet", 1), ("vfo", "VFOB"), ("level", "AF", .5, 1, "VFOB"), ("func", "NB", True, 1, "VFOB"), ("split", True, "VFOB")]
    assert results[-1].details == {"values": []}
@pytest.mark.parametrize("intent", [_intent("set_vfo", vfo="VFOB"), _intent("set_level", level="AF", value=.5),
    _intent("set_func", func="NB", on=True), _intent("set_split_vfo", on=True, tx_vfo="VFOB")], ids=["vfo", "level", "func", "split"])
async def test_managed_non_ptt_leaf_errors_are_not_rewritten(intent):
    with pytest.raises(_RigctldCommandFailure) as failure:
        await _RigctldCommandExecutor(_LeafHandler(HamlibError.EIO)).execute(intent)
    assert failure.value.error is HamlibError.EIO
async def test_injected_references_and_per_call_leaf_preserve_shared_default(managed):
    handler = managed.server._rig_handler
    assert (handler._managed_tx_authority, handler._command_service, handler._state_store) == (managed.authority, managed.service, managed.store)
    assert not hasattr(handler, "_command_queue") and not hasattr(managed.server, "_command_queue")
    managed.radio.release.set()
    await _send(managed, f"F {_FREQUENCY}")
    assert await _replies(managed, 1) == [b"RPRT 0\n"]
    assert managed.default.calls == []
async def test_real_authority_receipt_does_not_wait_for_provider_settlement(managed):
    gate = managed.lane.block_next()
    submissions, _ = _observe_starts(managed)
    await _send(managed, "T 1")
    assert await _replies(managed, 1) == [b"RPRT 0\n"]
    assert len(submissions) == 1 and submissions[0][0].done() and not submissions[0][0].result().settlement_done
    gate.set()
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
    assert not hasattr(managed.server, "_command_queue")
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
        return SimpleNamespace(outcome=outcome)
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
