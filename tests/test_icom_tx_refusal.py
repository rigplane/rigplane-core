"""MOR-4846: simulated CI-V key replies through production ingress mapping."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from _perf_helpers import fast_connect
from rigplane.commands import build_civ_frame
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
)
from rigplane.runtime.managed_tx_submission import ManagedPttTracker
from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import attach_managed_tx_composition
from rigplane_testkit.icom import MockIcomRadio
from test_managed_tx_http_route import _Writer, _reader


@pytest.fixture
async def simulated_key(monkeypatch):
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
    try:
        with fast_connect():
            await radio.connect()
        radio._civ_get_timeout = 0.2
        radio._civ_min_interval = 0
        yield radio, sim, script
    finally:
        with fast_connect():
            await radio.disconnect()
        await sim.stop()


@pytest.mark.parametrize(
    "operation", [ActuationOperation.PTT_ON, ActuationOperation.TRANSMIT_ON]
)
@pytest.mark.parametrize(
    "answer,expected,keyed",
    [
        ("nak", ActuationResult.REJECTED, False),
        ("ack", ActuationResult.ACCEPTED, True),
        ("silence", ActuationResult.UNCERTAIN, False),
        ("lost-ack", ActuationResult.UNCERTAIN, True),
    ],
)
async def test_simulator_key_reply_is_the_typed_write_result(
    simulated_key, operation, answer, expected, keyed
):
    radio, sim, script = simulated_key
    script["answer"] = answer

    result = await radio.actuate(
        EffectToken(1, 1, "simulated-key"), operation, is_current=lambda: True
    )

    assert result is expected
    assert script["keys"] == 1
    assert sim.ptt_on is keyed
    if not keyed:
        assert radio.state.ptt is False


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
            with pytest.raises(CommandError, match="actuation was rejected"):
                await execute_positive_tx_queue_entry(entry)
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


@pytest.mark.parametrize("ingress", ["http", "rigctld"])
@pytest.mark.parametrize("answer", ["nak", "ack", "silence", "lost-ack"])
async def test_simulator_reply_reaches_ingress_and_managed_document(
    simulated_key, tmp_path, ingress, answer
):
    radio, sim, script = simulated_key
    script["answer"] = answer
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
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
        assert sim.ptt_on is (answer == "ack")
        if answer == "nak":
            state = (await composition.authority.snapshot()).state
            assert state.intent.kind is ManagedTxIntentKind.RX
            assert state.last_error == "The radio refused to transmit."
            assert radio.state.ptt is False

            writer = _Writer()
            await server._handle_http(writer, "GET", "/api/v1/managed-transmit")
            assert writer.payload["managedTransmit"]["lastError"] == (
                "The radio refused to transmit."
            )
    finally:
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)
        await composition.shutdown(asyncio.Event())
