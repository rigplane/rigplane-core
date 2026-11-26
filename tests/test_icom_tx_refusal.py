"""MOR-4846: simulated CI-V key replies through production ingress mapping."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from _perf_helpers import fast_connect
from rigplane.cli import _ManagedTxRadioSession
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
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
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    ActuationSettled,
    EffectToken,
    ManagedTxIntentKind,
    ManagedTxOutcome,
    ManagedTxRefusalReason,
)
from rigplane.runtime.managed_tx_submission import ManagedPttTracker
from rigplane.runtime.radio import IcomRadio
from rigplane.testing.timing import await_until
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.protocol import decode_json, encode_json
from rigplane.web.web_startup import attach_managed_tx_composition
from rigplane_testkit.icom import MockIcomRadio
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
    script = {"answer": "ack", "keys": 0, "offs": 0}
    script["key_sent"] = asyncio.Event()
    script["off_sent"] = asyncio.Event()
    dispatch = sim._dispatch_civ

    def injected_reply(command, payload, from_addr):
        if command == 0x1C and payload == b"\x00\x01":
            script["keys"] += 1
            script["key_sent"].set()
            if script["answer"] == "nak":
                return build_civ_frame(from_addr, sim._radio_addr, 0xFA)
            if script["answer"] == "silence":
                return None
            if script["answer"] in ("lost-ack", "held-ack"):
                dispatch(command, payload, from_addr)
                return None
        if command == 0x1C and payload == b"\x00\x00":
            reply = dispatch(command, payload, from_addr)
            script["offs"] += 1
            script["off_sent"].set()
            return (
                None
                if script["answer"] == "held-ack" and script["offs"] == 1
                else reply
            )
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
    receipt = SimpleNamespace(
        outcome=ManagedTxOutcome.ACCEPTED,
        wait_settlement=AsyncMock(return_value=settlement),
    )
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
        receipt.wait_settlement.assert_awaited_once()
    finally:
        if not submission.done():
            submission.cancel()
        await asyncio.gather(submission, return_exceptions=True)


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
    "error,expected",
    [
        (None, ManagedTxRefusalReason.RADIO_REFUSED),
        ("rejected", ManagedTxRefusalReason.RADIO_REFUSED),
        ("stale before dispatch", ManagedTxRefusalReason.OTHER),
    ],
)
async def test_shared_queue_does_not_name_pre_dispatch_failure_as_radio_refusal(
    error, expected
):
    settlement = ActuationSettled(
        EffectToken(1, 1, "refused-key"),
        ActuationOperation.PTT_ON,
        ActuationResult.REJECTED,
        error,
    )
    receipt = SimpleNamespace(
        outcome=ManagedTxOutcome.ACCEPTED,
        wait_settlement=AsyncMock(return_value=settlement),
    )
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


@pytest.mark.parametrize("ingress", ["http", "ws", "rigctld"])
@pytest.mark.parametrize("answer", ["nak", "ack", "silence", "lost-ack"])
async def test_simulator_reply_reaches_ingress_and_managed_document(
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


@pytest.mark.parametrize("release", ["ptt_up", "force_off"])
@pytest.mark.parametrize("late_reply", [0xFB, 0xFA])
async def test_release_preempts_held_key_reply_without_late_reply_state_change(
    simulated_key, release, late_reply
):
    radio, sim, script, composition = simulated_key
    script["answer"] = "held-ack"
    radio._civ_get_timeout = 2.0
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    await composition.transport_ready(radio)
    await composition.bind_state_store(server.command_state_store)
    await composition.authority._stop_scheduler(composition.authority._scheduler_task)
    script["off_sent"].clear()
    script["offs"] = 0
    try:
        key = await composition.authority.submit_ptt(True, "slow-client")
        await await_until(
            script["key_sent"].is_set, timeout=1.0, description="key frame dispatched"
        )
        assert sim.ptt_on
        assert not key.settlement_done
        off = await (
            composition.authority.submit_ptt(False, "slow-client")
            if release == "ptt_up"
            else composition.authority.submit_force_off()
        )
        # Key-off must reach the simulator while the key reply is still withheld.
        await await_until(
            script["off_sent"].is_set,
            timeout=1.0,
            description="release dispatched before key reply timeout",
        )
        assert not sim.ptt_on
        frame = parse_civ_frame(
            build_civ_frame(CONTROLLER_ADDR, radio._radio_addr, late_reply)
        )
        await radio._civ_runtime._route_civ_frame(frame, generation=radio._civ_epoch)
        await asyncio.wait_for(off.wait_settlement(), 3.0)
        await key.wait_settlement()
        state = (await composition.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
        assert state.last_actuation.operation is ActuationOperation.FORCE_RECEIVE
        assert state.last_actuation.result is ActuationResult.ACCEPTED
        assert not state.release_required
        assert state.last_error is None
        assert not sim.ptt_on
        assert radio._civ_request_tracker.pending_count == 0
        await radio._civ_runtime._route_civ_frame(frame, generation=radio._civ_epoch)
        assert (await composition.authority.snapshot()).state == state
    finally:
        await composition.shutdown(asyncio.Event())


@pytest.mark.parametrize(
    "key_name,key_params", [("ptt_on", {}), ("ptt", {"state": True})]
)
async def test_ws_release_before_queue_dispatch_answers_revoked_key(
    simulated_key, key_name, key_params
):
    radio, sim, script, composition = simulated_key
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    server.command_queue.bind_connection_generation(lambda: radio)
    await composition.bind_state_store(server.command_state_store)
    attach_managed_tx_composition(server, composition)
    await composition.authority._stop_scheduler(composition.authority._scheduler_task)
    ws = SimpleNamespace(outbound_progress=0, send_text=AsyncMock())
    handler = ControlHandler(
        ws,
        radio,
        "test",
        radio.model,
        server=server,
        session_id="early-release-client",
        managed_tx_authority=composition.authority,
    )
    handler._publish_session_liveness(live=True)
    drain = None
    try:
        key_task = asyncio.create_task(
            handler._handle_text(
                encode_json(
                    {
                        "type": "cmd",
                        "id": "key",
                        "name": key_name,
                        "params": key_params,
                    }
                )
            )
        )
        await await_until(key_task.done, description="key command handled")
        await key_task
        assert server.command_queue.has_commands
        await handler._handle_text(
            encode_json({"type": "cmd", "id": "off", "name": "ptt_off", "params": {}})
        )
        drain = asyncio.create_task(_drain_queue(server))
        await await_until(
            lambda: not handler._managed_ptt_responses,
            timeout=1.0,
            description="revoked key answered",
        )
        responses = {
            body["id"]: body
            for call in ws.send_text.await_args_list
            for body in (decode_json(call.args[0]),)
        }
        assert set(responses) == {"key", "off"}
        assert responses["key"]["ok"] is False
        assert responses["key"]["code"] == ManagedTxRefusalReason.OTHER
        assert script["keys"] == 0
        assert not sim.ptt_on
        state = (await composition.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
        assert state.pending_effect is None
        assert not state.release_required
    finally:
        response_tasks = tuple(handler._managed_ptt_responses)
        handler._cancel_pending_command_flushes()
        if drain is not None:
            drain.cancel()
            await asyncio.gather(drain, return_exceptions=True)
        await asyncio.gather(*response_tasks, return_exceptions=True)
        await composition.shutdown(asyncio.Event())


@pytest.mark.parametrize(
    "key_name,key_params", [("ptt_on", {}), ("ptt", {"state": True})]
)
async def test_ws_receive_path_accepts_release_while_key_reply_is_held(
    simulated_key, key_name, key_params
):
    radio, sim, script, composition = simulated_key
    script["answer"] = "held-ack"
    radio._civ_get_timeout = 2.0
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    server.command_queue.bind_connection_generation(lambda: radio)
    await composition.transport_ready(radio)
    await composition.bind_state_store(server.command_state_store)
    attach_managed_tx_composition(server, composition)
    await composition.authority._stop_scheduler(composition.authority._scheduler_task)
    script["off_sent"].clear()
    script["offs"] = 0
    ws = SimpleNamespace(outbound_progress=0, send_text=AsyncMock())
    handler = ControlHandler(
        ws,
        radio,
        "test",
        radio.model,
        server=server,
        session_id="slow-client",
        managed_tx_authority=composition.authority,
    )
    handler._publish_session_liveness(live=True)
    drain = asyncio.create_task(_drain_queue(server))
    key_request = asyncio.create_task(
        handler._handle_text(
            encode_json(
                {
                    "type": "cmd",
                    "id": "key",
                    "name": key_name,
                    "params": key_params,
                }
            )
        )
    )
    try:
        await await_until(
            script["key_sent"].is_set, timeout=1.0, description="WS key dispatched"
        )
        await await_until(
            key_request.done, timeout=1.0, description="WS reader free before key reply"
        )
        await key_request
        assert sim.ptt_on
        assert not ws.send_text.await_args_list
        await handler._handle_text(
            encode_json(
                {
                    "type": "cmd",
                    "id": "off",
                    "name": "ptt_off",
                    "params": {},
                }
            )
        )
        await await_until(
            script["off_sent"].is_set,
            timeout=1.0,
            description="same WS release dispatched",
        )
        assert not sim.ptt_on
        frame = parse_civ_frame(
            build_civ_frame(CONTROLLER_ADDR, radio._radio_addr, 0xFB)
        )
        await radio._civ_runtime._route_civ_frame(frame, generation=radio._civ_epoch)
        await await_until(
            lambda: not handler._managed_ptt_responses,
            timeout=3.0,
            description="key response settled",
        )
        assert {
            decode_json(call.args[0])["id"] for call in ws.send_text.await_args_list
        } == {"key", "off"}
        assert (
            await composition.authority.snapshot()
        ).state.intent.kind is ManagedTxIntentKind.RX
    finally:
        key_request.cancel()
        handler._cancel_pending_command_flushes()
        drain.cancel()
        await asyncio.gather(key_request, drain, return_exceptions=True)
        await composition.shutdown(asyncio.Event())
