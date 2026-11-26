"""MOR-4846: simulated CI-V key replies through production ingress mapping."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from _helpers import wrap_civ_in_udp
from _perf_helpers import fast_connect
from rigplane.cli import _ManagedTxRadioSession
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.commands.commander import IcomCommander
from rigplane.command_map import CommandMap
from rigplane.commands.bound import BoundCommands
from rigplane.core import civ as civ_module
from rigplane.core.civ import PttReplyLedger
from rigplane.core.exceptions import CommandError
from rigplane.rigctld.contract import HamlibError, RigctldCommand, RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.runtime._poller_types import (
    CommandQueueEntry,
    execute_command_queue_entry,
    execute_positive_tx_queue_entry,
    validate_command_queue_entry_currency,
)
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_authority import ManagedTxSubmission
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    ActuationSettled,
    EffectToken,
    ManagedTxIntentKind,
    ManagedTxOutcome,
    ManagedTxRefusalReason,
    ReleasePlan,
    ManagedTxState,
    ManagedTxTransition,
)
from rigplane.runtime.managed_tx_submission import ManagedPttTracker
from rigplane.runtime import (
    _civ_rx,
    managed_tx_authority as authority_module,
    managed_tx_effect_lane as effect_lane_module,
    radio as radio_module,
)
from rigplane.runtime.radio import CoreRadio, IcomRadio
from rigplane.testing.timing import virtual_clock
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.protocol import decode_json, encode_json
from rigplane.web.web_startup import attach_managed_tx_composition
from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.leaked_radio_guard import fake_connected
from test_radio import MockTransport
from test_managed_tx_http_route import _Writer, _reader


@pytest.fixture
async def simulated_key(monkeypatch, tmp_path):
    sim = MockIcomRadio(stateful_ptt=True, unsolicited_civ=False)
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    script = {"answer": "ack", "keys": 0}
    dispatch = sim._dispatch_civ

    def injected_reply(command, payload, from_addr):
        if command == 0x1C and payload == b"\x00\x01":
            script["keys"] += 1
            if script["answer"] == "nak":
                return build_civ_frame(from_addr, sim._radio_addr, 0xFA)
            if script["answer"] == "silence":
                return None
            if script["answer"] == "lost-ack":
                dispatch(command, payload, from_addr)
                return None
        return dispatch(command, payload, from_addr)

    monkeypatch.setattr(sim, "_dispatch_civ", injected_reply)
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    session = _ManagedTxRadioSession(radio, composition)
    try:
        with fast_connect():
            await session.__aenter__()
        radio._civ_get_timeout = 0.2
        radio._civ_min_interval = 0
        yield radio, sim, script, composition
    finally:
        try:
            with fast_connect():
                await session.__aexit__(None, None, None)
        finally:
            await sim.stop()


@pytest.mark.parametrize(
    "operation", [ActuationOperation.PTT_ON, ActuationOperation.TRANSMIT_ON]
)
@pytest.mark.parametrize(
    "answer,expected,keyed",
    [
        ("nak", ActuationResult.REJECTED, False),
        ("ack", ActuationResult.ACCEPTED, True),
        ("silence", ActuationResult.ACCEPTED, False),
        ("lost-ack", ActuationResult.ACCEPTED, True),
    ],
)
async def test_simulator_key_reply_is_the_typed_write_result(
    simulated_key, operation, answer, expected, keyed
):
    radio, sim, script, _composition = simulated_key
    script["answer"] = answer

    result = await radio.actuate(
        EffectToken(1, 1, "simulated-key"), operation, is_current=lambda: True
    )

    assert result is expected
    assert script["keys"] == 1
    assert sim.ptt_on is keyed
    if not keyed:
        assert radio.radio_state.ptt is False


@pytest.mark.parametrize("result", list(ActuationResult))
async def test_shared_positive_queue_maps_typed_refusal_for_every_backend(result):
    settlement = ActuationSettled(
        EffectToken(1, 1, "shared-key"), ActuationOperation.PTT_ON, result
    )
    transition = ManagedTxTransition(ManagedTxState(), ManagedTxOutcome.ACCEPTED)

    async def complete():
        return transition, settlement

    completion = asyncio.create_task(complete())
    receipt = ManagedTxSubmission(transition, completion)
    ready = asyncio.get_running_loop().create_future()

    async def admitted():
        await ready
        return receipt

    submission = asyncio.create_task(admitted())
    entry = CommandQueueEntry(
        command=None, positive_tx_ready=ready, positive_tx_submission=submission
    )
    try:
        if result is ActuationResult.REJECTED:
            with pytest.raises(CommandError, match="actuation was rejected") as refused:
                await execute_positive_tx_queue_entry(entry)
            assert refused.value.reason is ManagedTxRefusalReason.RADIO_REFUSED
        else:
            await execute_positive_tx_queue_entry(entry)
        assert receipt.settlement_done
        assert await receipt.wait_settlement() is settlement
    finally:
        if not submission.done():
            submission.cancel()
        await asyncio.gather(submission, completion, return_exceptions=True)


async def _drain_queue(server):
    while True:
        await server.command_queue.wait()
        while (entry := server.command_queue.take_entry()) is not None:
            try:
                validate_command_queue_entry_currency(
                    entry,
                    now=asyncio.get_running_loop().time(),
                    provider_generation=server.command_state_store.provider_generation,
                    connection_generation=server.command_queue.capture_connection_generation(),
                    session_is_live=server.command_queue.session_is_live,
                    require_connection_generation=True,
                )
                await execute_command_queue_entry(
                    entry, execute_positive_tx_queue_entry
                )
            except CommandError:
                # The entry future carries the rejection back to its ingress.
                pass


@pytest.mark.parametrize(
    "error,reason,expected",
    [
        (None, None, ManagedTxRefusalReason.RADIO_REFUSED),
        ("rejected", None, ManagedTxRefusalReason.RADIO_REFUSED),
        ("stale before dispatch", None, ManagedTxRefusalReason.OTHER),
        (None, ManagedTxRefusalReason.OTHER, ManagedTxRefusalReason.OTHER),
        ("rejected", ManagedTxRefusalReason.OTHER, ManagedTxRefusalReason.OTHER),
    ],
)
async def test_shared_queue_does_not_name_pre_dispatch_failure_as_radio_refusal(
    error, reason, expected
):
    settlement = ActuationSettled(
        EffectToken(1, 1, "refused-key"),
        ActuationOperation.PTT_ON,
        ActuationResult.REJECTED,
        error,
        reason,
    )
    transition = ManagedTxTransition(ManagedTxState(), ManagedTxOutcome.ACCEPTED)

    async def complete():
        return transition, settlement

    receipt = ManagedTxSubmission(transition, asyncio.create_task(complete()))
    ready = asyncio.get_running_loop().create_future()
    submission = asyncio.get_running_loop().create_future()
    submission.set_result(receipt)
    with pytest.raises(CommandError) as refused:
        await execute_positive_tx_queue_entry(
            CommandQueueEntry(
                command=None,
                positive_tx_ready=ready,
                positive_tx_submission=submission,
            )
        )
    assert refused.value.reason is expected
    assert receipt.settlement_done
    assert await receipt.wait_settlement() is settlement


@pytest.mark.parametrize("ingress", ["http", "ws", "rigctld"])
@pytest.mark.parametrize("answer", ["nak", "ack", "silence", "lost-ack"])
async def test_ingress_radio_refused_only_for_nak_after_key_written(
    simulated_key, ingress, answer
):
    radio, sim, script, composition = simulated_key
    script["answer"] = answer
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    server.command_queue.bind_connection_generation(lambda: radio)
    await composition.transport_ready(radio)
    await composition.bind_state_store(server.command_state_store)
    attach_managed_tx_composition(server, composition)
    await composition.authority._stop_scheduler(composition.authority._scheduler_task)
    drain = asyncio.create_task(_drain_queue(server))
    try:
        if ingress == "http":
            reader, headers = _reader({"operation": "transmit_on"})
            writer = _Writer()
            await server._handle_http(
                writer,
                "POST",
                "/api/v1/managed-transmit/command",
                headers=headers,
                reader=reader,
            )
            assert writer.status == (409 if answer == "nak" else 202)
            assert writer.payload["result"] == (
                "rejected" if answer == "nak" else "accepted"
            )
            if answer == "nak":
                assert writer.payload["code"] == ManagedTxRefusalReason.RADIO_REFUSED
        elif ingress == "ws":
            ws = SimpleNamespace(outbound_progress=0, send_text=AsyncMock())
            handler = ControlHandler(
                ws,
                radio,
                "test",
                radio.model,
                server=server,
                managed_tx_authority=composition.authority,
                session_id="refusal-client",
            )
            handler._publish_session_liveness(live=True)
            await handler._dispatch_command("refusal-key", "ptt_on", {})
            body = decode_json(ws.send_text.await_args.args[0])
            assert body["ok"] is (answer != "nak")
            if answer == "nak":
                assert body["code"] == ManagedTxRefusalReason.RADIO_REFUSED
                assert body["message"] != "managed transmit authority unavailable"
        else:
            handler = RigctldHandler(
                radio,
                RigctldConfig(),
                state_store=server.command_state_store,
                managed_tx_authority=composition.authority,
                command_queue=server.command_queue,
                command_service=server.command_service,
            )
            handler.bind_provider_generation(
                lambda: server.command_state_store.provider_generation
            )
            submission, ready = ManagedPttTracker().begin_key(
                composition.authority,
                owner="refusal-client",
                on=True,
                expires_at_monotonic=asyncio.get_running_loop().time() + 10.0,
                connection_generation=radio,
            )
            response = await handler.execute(
                RigctldCommand(
                    short_cmd="T", long_cmd="set_ptt", args=("1",), is_set=True
                ),
                session_id="refusal-client",
                ptt_submission=submission,
                ptt_ready=ready,
            )
            assert response.error == (
                HamlibError.ERJCTED if answer == "nak" else HamlibError.OK
            )

        assert script["keys"] == 1
        assert sim.ptt_on is (answer in ("ack", "lost-ack"))
        state = (await composition.authority.snapshot()).state
        if answer in ("silence", "lost-ack"):
            assert state.intent.kind is not ManagedTxIntentKind.RX
            assert state.last_error is None
            assert state.last_actuation.result is ActuationResult.ACCEPTED
        if answer == "nak":
            assert state.intent.kind is ManagedTxIntentKind.RX
            assert state.last_error == ManagedTxRefusalReason.RADIO_REFUSED
            assert radio.radio_state.ptt is False

            writer = _Writer()
            await server._handle_http(writer, "GET", "/api/v1/managed-transmit")
            assert writer.payload["managedTransmit"]["lastError"] == (
                ManagedTxRefusalReason.RADIO_REFUSED
            )
            assert (
                writer.payload["managedTransmit"]["lastActuation"]["result"]
                == "rejected"
            )
    finally:
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)
        await composition.shutdown(asyncio.Event())


class _LostKeyAckTransport(MockTransport):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.ptt_on = False
        self.emitted = []
        self.silent_commands = set()

    def answer(self, command):
        self.queue_response(
            wrap_civ_in_udp(
                build_civ_frame(CONTROLLER_ADDR, self.emitted[-1][1].to_addr, command),
                seq=self.send_seq,
            )
        )

    async def send_tracked(self, data, **kwargs):
        await super().send_tracked(data, **kwargs)
        frame = parse_civ_frame(data[0x15:])
        self.emitted.append((self.clock(), frame))
        if frame.command in self.silent_commands:
            return
        if frame.command == 0x1C and frame.sub == 0:
            if frame.data == b"\x01":
                self.ptt_on = True
                return  # Apply the key, but lose its ACK.
            if frame.data == b"\x00":
                self.ptt_on = False
            else:
                self.queue_response(
                    wrap_civ_in_udp(
                        build_civ_frame(
                            CONTROLLER_ADDR,
                            frame.to_addr,
                            0x1C,
                            sub=0,
                            data=bytes([self.ptt_on]),
                        ),
                        seq=self.send_seq,
                    )
                )
                return
        self.queue_response(
            wrap_civ_in_udp(
                build_civ_frame(CONTROLLER_ADDR, frame.to_addr, 0xFB),
                seq=self.send_seq,
            )
        )


@asynccontextmanager
async def _lost_key_ack_radio(clock, monkeypatch):
    for module in (
        _civ_rx,
        radio_module,
        civ_module,
        authority_module,
        effect_lane_module,
    ):
        clock.scope_to(monkeypatch, module)
        # Keep sleeps timer-driven on this loop; only monotonic reads need a view.
        monkeypatch.setattr(module, "asyncio", asyncio)
    radio = IcomRadio("", model="IC-7610")
    transport = _LostKeyAckTransport(clock)
    transport.connected = True
    radio._civ_transport = transport
    radio._civ_min_interval = 0
    radio._civ_get_timeout = 2.0
    radio._civ_runtime._ptt_ledger = PttReplyLedger(clock=clock, answer_window=2.0)
    radio._commander = IcomCommander(radio._execute_civ_raw)
    radio._commander.start()
    try:
        yield radio, transport
    finally:
        try:
            await radio._commander.stop()
        finally:
            await radio._civ_runtime.stop_pump()
            await transport.disconnect()
            radio._connected = False


@asynccontextmanager
async def _managed_virtual_server(radio, clock, tmp_path):
    composition = ManagedTxComposition(radio, config_path=tmp_path / "tx.json")
    install_managed_tx_composition(radio, composition)
    fake_connected(radio)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    server.command_queue.bind_connection_generation(lambda: radio)
    await composition.transport_ready(radio)
    binding = asyncio.create_task(
        composition.bind_state_store(server.command_state_store)
    )
    await clock.elapse(radio._PTT_READBACK_SETTLE_S)
    assert binding.done()
    await binding
    attach_managed_tx_composition(server, composition)
    await composition.authority._stop_scheduler(composition.authority._scheduler_task)
    drain = asyncio.create_task(_drain_queue(server))
    try:
        yield server, composition
    finally:
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)
        termination = asyncio.Event()
        shutdown = asyncio.create_task(composition.shutdown(termination))
        await clock.elapse(10)
        if not shutdown.done():
            termination.set()
            await clock.elapse(0)
        assert shutdown.done()
        await shutdown


async def _http_key(server, writer):
    reader, headers = _reader({"operation": "transmit_on"})
    await server._handle_http(
        writer,
        "POST",
        "/api/v1/managed-transmit/command",
        headers=headers,
        reader=reader,
    )


@pytest.mark.parametrize("dispatch", [True, False])
@virtual_clock
async def test_http_key_is_accepted_only_after_written_despite_commander_queue_delay(
    clock, monkeypatch, tmp_path, dispatch
):
    bound = CoreRadio.ICOM_PTT_ON_ACK_TIMEOUT_S
    async with _lost_key_ack_radio(clock, monkeypatch) as (radio, transport):
        async with _managed_virtual_server(radio, clock, tmp_path) as (server, _):
            transport.emitted.clear()
            transport.silent_commands.add(0x0F)
            busy = asyncio.create_task(
                radio._send_civ_raw(
                    build_civ_frame(
                        radio._radio_addr, CONTROLLER_ADDR, 0x0F, data=b"\x01"
                    )
                )
            )
            writer = _Writer()
            key = None
            try:
                await clock.elapse(0)
                assert transport.emitted[-1][1].command == 0x0F
                key = asyncio.create_task(_http_key(server, writer))
                await clock.elapse(2 * bound)
                assert not transport.ptt_on
                assert not key.done(), "a key never written must not be accepted"
                if not dispatch:
                    await radio._commander.stop()
                    await clock.elapse(0)
                    assert key.done()
                    await key
                    assert not transport.ptt_on
                    assert writer.status == 409
                    assert writer.payload["code"] == ManagedTxRefusalReason.OTHER
                    assert [frame.command for _, frame in transport.emitted] == [0x0F]
                    return
                transport.answer(0xFB)
                await clock.elapse(0)
                assert busy.done()
                await busy
                assert transport.ptt_on
                written_at = transport.emitted[-1][0]
                assert not key.done()
                await clock.elapse(bound - 0.000001)
                assert not key.done()
                await clock.elapse(0.000001)
                assert key.done()
                await key
                assert writer.status == 202
                assert writer.payload["result"] == "accepted"
                assert clock() - written_at == pytest.approx(bound)
            finally:
                for task in (busy, key):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *(task for task in (busy, key) if task is not None),
                    return_exceptions=True,
                )


@virtual_clock
async def test_unsupported_profile_is_other_before_any_io_through_queue_and_document(
    clock, monkeypatch, tmp_path
):
    async with _lost_key_ack_radio(clock, monkeypatch) as (radio, transport):
        async with _managed_virtual_server(radio, clock, tmp_path) as (
            server,
            composition,
        ):
            transport.emitted.clear()
            radio._commands = BoundCommands(
                CommandMap({}), {"ptt_on": "test manufacturer command table"}
            )
            writer = _Writer()
            request = asyncio.create_task(_http_key(server, writer))
            try:
                await clock.elapse(0)
                assert request.done()
                await request
                assert transport.emitted == []
                assert writer.status == 409
                assert writer.payload["code"] == ManagedTxRefusalReason.OTHER
                state = (await composition.authority.snapshot()).state
                assert state.release_plan is ReleasePlan.FORCE_RELEASE
                assert state.last_error == ManagedTxRefusalReason.OTHER
                document = _Writer()
                await server._handle_http(document, "GET", "/api/v1/managed-transmit")
                assert document.payload["managedTransmit"]["lastError"] == "other"
            finally:
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)


@virtual_clock
async def test_written_key_cancelled_by_release_stays_uncertain_and_force_release_is_written(
    clock, monkeypatch, tmp_path
):
    async with _lost_key_ack_radio(clock, monkeypatch) as (radio, transport):
        async with _managed_virtual_server(radio, clock, tmp_path) as (_, composition):
            transport.emitted.clear()
            admitted = asyncio.create_task(composition.authority.submit_transmit_on())
            off = None
            try:
                await clock.elapse(0)
                assert admitted.done()
                receipt = await admitted
                assert transport.ptt_on
                assert not receipt.settlement_done
                await clock.elapse(0.05)
                off = asyncio.create_task(composition.authority.submit_force_off())
                await clock.elapse(0)
                assert receipt.settlement_done
                settlement = await receipt.wait_settlement()
                assert settlement.result is ActuationResult.UNCERTAIN
                assert any(
                    (frame.command, frame.sub, frame.data) == (0x1C, 0, b"\x00")
                    for _, frame in transport.emitted
                )
                assert not transport.ptt_on
                state = (await composition.authority.snapshot()).state
                assert state.intent.kind is ManagedTxIntentKind.RX
                assert state.release_plan is ReleasePlan.FORCE_RELEASE
                await clock.elapse(3)
                assert off.done()
                off_receipt = await off
                assert off_receipt.settlement_done
                await off_receipt.wait_settlement()
            finally:
                for task in (admitted, off):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *(task for task in (admitted, off) if task is not None),
                    return_exceptions=True,
                )


@pytest.mark.parametrize(
    "operation", [ActuationOperation.PTT_ON, ActuationOperation.TRANSMIT_ON]
)
@virtual_clock
async def test_lost_key_ack_waits_only_the_explicit_bound_and_stays_accepted(
    clock, monkeypatch, operation
):
    bound = CoreRadio.ICOM_PTT_ON_ACK_TIMEOUT_S
    assert bound == 0.15
    assert 0 < bound <= 0.5
    async with _lost_key_ack_radio(clock, monkeypatch) as (radio, transport):
        fake_connected(radio)
        task = asyncio.create_task(
            radio.actuate(
                EffectToken(1, 1, "lost-ack"), operation, is_current=lambda: True
            )
        )
        try:
            await clock.elapse(0)
            assert transport.ptt_on
            assert not task.done()
            started = transport.emitted[0][0]
            await clock.elapse(bound - 0.000001)
            assert not task.done()
            await clock.elapse(0.000001)
            assert task.done()
            assert await task is ActuationResult.ACCEPTED
            assert clock() - started == pytest.approx(bound)
            assert len(transport.emitted) == 1
            assert radio._civ_request_tracker.pending_count == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@virtual_clock
async def test_release_after_a_lost_key_ack_goes_out_within_the_ack_bound(
    clock, monkeypatch, tmp_path
):
    # The fallback lets this same measurement run on main, before the constant.
    bound = getattr(CoreRadio, "ICOM_PTT_ON_ACK_TIMEOUT_S", 0.15)
    scheduling_tick = 0.000001
    async with _lost_key_ack_radio(clock, monkeypatch) as (radio, transport):
        composition = ManagedTxComposition(radio, config_path=tmp_path / "tx.json")
        install_managed_tx_composition(radio, composition)
        fake_connected(radio)
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        server.command_queue.bind_connection_generation(lambda: radio)
        await composition.transport_ready(radio)
        binding = asyncio.create_task(
            composition.bind_state_store(server.command_state_store)
        )
        await clock.elapse(radio._PTT_READBACK_SETTLE_S)
        assert binding.done()
        await binding
        attach_managed_tx_composition(server, composition)
        await composition.authority._stop_scheduler(
            composition.authority._scheduler_task
        )
        ws = SimpleNamespace(outbound_progress=0, send_text=AsyncMock())
        handler = ControlHandler(
            ws,
            radio,
            "test",
            radio.model,
            server=server,
            managed_tx_authority=composition.authority,
            session_id="lost-ack-client",
            clock=clock,
        )
        handler._publish_session_liveness(live=True)
        requests = asyncio.Queue()

        async def read_commands():
            for _ in range(2):
                await handler._handle_text(await requests.get())

        drain = asyncio.create_task(_drain_queue(server))
        reader = asyncio.create_task(read_commands())
        try:
            transport.emitted.clear()
            requests.put_nowait(
                encode_json(
                    {"type": "cmd", "id": "key", "name": "ptt_on", "params": {}}
                )
            )
            await clock.elapse(0)
            assert transport.ptt_on
            keyed_at = transport.emitted[0][0]
            await clock.elapse(0.05)
            released_at = clock()
            requests.put_nowait(
                encode_json(
                    {"type": "cmd", "id": "off", "name": "ptt_off", "params": {}}
                )
            )
            await clock.elapse(bound + scheduling_tick)
            offs = [
                emitted_at
                for emitted_at, frame in transport.emitted
                if (frame.command, frame.sub, frame.data) == (0x1C, 0, b"\x00")
            ]
            assert offs, "no release frame was emitted after the lost key ACK"
            delay = offs[0] - released_at
            print(
                f"lost key ACK release delay: {delay:.6f} s; key-to-off: "
                f"{offs[0] - keyed_at:.6f} s; ACK bound: {bound:.6f} s"
            )
            assert released_at - keyed_at == pytest.approx(0.05)
            assert 0 <= delay <= bound + scheduling_tick
            assert not transport.ptt_on
            assert reader.done()
            await reader
            responses = [
                decode_json(call.args[0]) for call in ws.send_text.await_args_list
            ]
            assert [body["id"] for body in responses] == ["key", "off"]
            assert all(body["ok"] for body in responses)
            assert (
                await composition.authority.snapshot()
            ).state.intent.kind is ManagedTxIntentKind.RX
        finally:
            reader.cancel()
            drain.cancel()
            await asyncio.gather(reader, drain, return_exceptions=True)
            shutdown = asyncio.create_task(composition.shutdown(asyncio.Event()))
            await clock.elapse(10.0)
            assert shutdown.done()
            await shutdown
