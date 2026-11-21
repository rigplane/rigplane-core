"""MOR-4214: key-up must survive auxiliary control delivery failure."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import pytest

from serial_stub import SerialMockRadio

from rigplane.runtime.controller_authority import ControllerAuthority
from rigplane.runtime.managed_tx_authority import (
    ManagedTxAuthority,
    ManagedTxSubmission,
)
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
    ManagedTxIntentKind,
    ManagedTxOutcome,
)
from rigplane.testing.timing import VirtualClock
from rigplane.web import runtime_helpers
from rigplane.web import server as server_module
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from rigplane.ws import WS_OP_TEXT, WebSocketConnection, make_frame

Failure = Literal["write_error", "drain_error", "transport_abort", "sender_only_error"]
Phase = Literal["before_admission", "after_admission"]


class _PttRadio(SerialMockRadio):
    _controller_authority: ControllerAuthority
    _civ_epoch = 1


class _PttActuator:
    def __init__(self, radio: _PttRadio) -> None:
        self.radio = radio
        self.operations: list[ActuationOperation | AbortOperation] = []
        self.release_allowed = asyncio.Event()

    async def actuate(
        self,
        token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        if operation is ActuationOperation.FORCE_RECEIVE:
            await self.release_allowed.wait()
        if not is_current():
            return ActuationResult.REJECTED
        self.operations.append(operation)
        if isinstance(operation, ActuationOperation):
            await self.radio.set_ptt(operation is not ActuationOperation.FORCE_RECEIVE)
        return ActuationResult.ACCEPTED


class _FaultTransport(asyncio.Transport):
    def __init__(self, protocol: asyncio.StreamReaderProtocol) -> None:
        super().__init__()
        self.protocol = protocol
        self.buffer = bytearray()
        self.closed = False
        self.write_error = False
        self.failed = asyncio.Event()

    def write(self, data: bytes) -> None:
        if self.closed or self.write_error:
            self.failed.set()
            raise BrokenPipeError("auxiliary outbound write failed")
        self.buffer.extend(data)

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.protocol.connection_lost(None)

    def abort(self) -> None:
        self.close()


class _FaultWriter(asyncio.StreamWriter):
    def __init__(self, reader: asyncio.StreamReader) -> None:
        protocol = asyncio.StreamReaderProtocol(reader)
        transport = _FaultTransport(protocol)
        protocol.connection_made(transport)
        super().__init__(transport, protocol, reader, asyncio.get_running_loop())
        self.outbound = transport
        self.drain_error = False
        self.fail_one_drain = False

    async def drain(self) -> None:
        if self.drain_error or self.fail_one_drain:
            self.fail_one_drain = False
            self.outbound.failed.set()
            raise ConnectionError("auxiliary outbound drain failed")
        await super().drain()


@pytest.mark.parametrize(
    "failure",
    [
        "write_error",
        "drain_error",
        "transport_abort",
        # Known gap (MOR-4214, same on the rc4 tag and main): when ONLY the event
        # sender dies (no EOF, no write failure afterwards) the radio still ends
        # in RX (every assertion before the handler check passes) but the
        # handler task keeps running until the client goes away. Tracked for the
        # 3.3.1 ACK-ordering work; strict xfail so that fixing it flips this red.
        pytest.param(
            "sender_only_error",
            marks=pytest.mark.xfail(
                strict=True, reason="handler lingers after the event sender dies"
            ),
        ),
    ],
)
@pytest.mark.parametrize("phase", ["before_admission", "after_admission"])
@pytest.mark.parametrize(
    ("name", "params"), [("ptt_off", {}), ("ptt", {"state": False})]
)
def test_auxiliary_outbound_failure_during_key_up_releases_radio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: Failure,
    phase: Phase,
    name: str,
    params: dict[str, object],
) -> None:
    clock = VirtualClock(start=1000.0)
    clock.scope_to(monkeypatch, server_module)
    monkeypatch.setattr(server_module, "asyncio", asyncio)
    monkeypatch.setattr(runtime_helpers, "time", server_module.time)

    async def scenario() -> None:
        radio = _PttRadio(model="IC-7610")
        await radio.connect()
        srv = WebServer(radio, WebConfig(radio_model="IC-7610"))
        actuator = _PttActuator(radio)
        authority = ManagedTxAuthority(
            ManagedTxEffectLane(actuator, clock=clock),
            ManagedTxTotConfigStore(tmp_path / "managed-tx.json"),
            TxAbortFence(),
            provider_generation=7,
            clock=clock,
        )
        controller = srv._controller
        monkeypatch.setattr(controller, "_clock", clock)
        authority.bind_controller(controller)
        radio._controller_authority = controller
        key = controller.acquire("browser")["controller_key"]
        primary = controller.attach(key, "primary", "live-primary")
        auxiliary = controller.attach(key, "auxiliary", "releasing-auxiliary")
        assert primary.tx_owner == auxiliary.tx_owner
        assert auxiliary.tx_owner is not None
        poller = RadioPoller(
            radio,
            srv.command_queue,
            state_store=srv.command_state_store,
            managed_tx_authority=authority,
            clock=clock,
        )
        readers = [asyncio.StreamReader(), asyncio.StreamReader()]
        writers = [_FaultWriter(reader) for reader in readers]
        sockets = [
            WebSocketConnection(reader, writer, clock=clock)
            for reader, writer in zip(readers, writers)
        ]
        handlers = [
            ControlHandler(
                ws,
                radio,
                "test",
                "IC-7610",
                server=srv,
                session_id=ticket.session_id,
                tx_owner=ticket.tx_owner,
                managed_tx_authority=authority,
                clock=clock,
            )
            for ws, ticket in zip(sockets, (primary, auxiliary))
        ]
        sender_stopped = asyncio.Event()
        event_sender = handlers[1]._event_sender_loop

        async def observe_sender() -> None:
            try:
                await event_sender()
            finally:
                sender_stopped.set()

        monkeypatch.setattr(handlers[1], "_event_sender_loop", observe_sender)
        release_started = asyncio.Event()
        release_admitted = asyncio.Event()
        return_admission = asyncio.Event()
        releases: list[ManagedTxSubmission] = []
        submit_ptt = authority.submit_ptt

        async def observe_release(
            on: bool,
            owner: str,
            *,
            ready: asyncio.Future[Any] | None = None,
            expires_at_monotonic: float | None = None,
        ) -> ManagedTxSubmission:
            assert on is False
            release_started.set()
            submission = await submit_ptt(
                on, owner, ready=ready, expires_at_monotonic=expires_at_monotonic
            )
            releases.append(submission)
            release_admitted.set()
            # Hold only publication to the handler; admission/settlement stay real.
            await return_admission.wait()
            return submission

        monkeypatch.setattr(authority, "submit_ptt", observe_release)

        def command(
            command_id: str, command_name: str, values: dict[str, object]
        ) -> None:
            readers[1].feed_data(
                make_frame(
                    WS_OP_TEXT,
                    json.dumps(
                        {
                            "type": "cmd",
                            "id": command_id,
                            "name": command_name,
                            "params": values,
                        }
                    ).encode(),
                )
            )

        async def fail_outbound() -> None:
            if failure == "transport_abort":
                sockets[1].abort()
                assert writers[1].outbound.closed
                assert readers[1].at_eof()
            elif failure == "write_error":
                writers[1].outbound.write_error = True
            elif failure == "drain_error":
                writers[1].drain_error = True
            else:
                writers[1].fail_one_drain = True
            handlers[1]._event_queue.put_nowait({"type": "notification"})
            await clock.elapse(0)
            assert writers[1].outbound.failed.is_set()
            assert sender_stopped.is_set()

        tasks: list[asyncio.Task[None]] = []
        for handler, ticket, ws in zip(handlers, (primary, auxiliary), sockets):
            controller.register_control(ticket)
            controller.add_channel(ticket, ws.abort)
            with controller.bind(ticket):
                tasks.append(asyncio.create_task(handler.run()))
        try:
            await clock.elapse(0)
            assert all(
                srv.command_queue.session_is_live(ticket.session_id)
                for ticket in (primary, auxiliary)
            )
            command("key", "ptt_on", {})
            await clock.elapse(0)
            entry = srv.command_queue.take_entry()
            assert entry is not None
            await poller._execute_queued_entry(entry)
            await clock.elapse(0)
            assert entry.positive_tx_submission is not None
            assert entry.positive_tx_submission.done()
            assert not entry.positive_tx_submission.cancelled()
            assert entry.positive_tx_submission.exception() is None
            assert radio._ptt is True
            assert authority.tx_holder_nowait() == auxiliary.tx_owner
            assert actuator.operations == [ActuationOperation.PTT_ON]

            if phase == "before_admission":
                async with authority._lock:
                    command("unkey", name, params)
                    await clock.elapse(0)
                    assert release_started.is_set()
                    assert not release_admitted.is_set()
                    assert not tasks[1].done()
                    assert authority.tx_holder_nowait() == auxiliary.tx_owner
                    await fail_outbound()
            else:
                command("unkey", name, params)
                await clock.elapse(0)
                assert release_started.is_set()
                assert release_admitted.is_set()
                assert not tasks[1].done()
                assert releases[0].outcome is ManagedTxOutcome.ACCEPTED
                assert not releases[0].settlement_done
                await fail_outbound()
            assert radio._ptt is True
            return_admission.set()
            actuator.release_allowed.set()
            await clock.elapse(0)

            # Assert before rescue cleanup: force_off/cancel must not mask a failure.
            assert radio._ptt is False
            state = (await authority.snapshot()).state
            assert state.intent.kind is ManagedTxIntentKind.RX
            assert state.intent.owner_token is None
            assert not state.release_required
            assert state.pending_effect is None
            assert authority.tx_holder_nowait() is None
            assert ActuationOperation.FORCE_RECEIVE in actuator.operations
            assert len(releases) == 1
            assert releases[0].outcome is ManagedTxOutcome.ACCEPTED
            assert releases[0].settlement_done
            settled = await releases[0].wait_settlement()
            assert settled is not None
            assert settled.result is ActuationResult.ACCEPTED
            assert tasks[1].done(), "outbound failure left auxiliary handler running"
            result = (await asyncio.gather(tasks[1], return_exceptions=True))[0]
            assert result is None or isinstance(result, ConnectionError)
            assert handlers[1]._event_queue not in srv._control_event_queues
            assert not srv.command_queue.session_is_live("releasing-auxiliary")
            assert "releasing-auxiliary" not in controller.control_session_ids(primary)
            assert not srv.command_queue.has_commands
            assert not tasks[0].done()
            assert srv.command_queue.session_is_live("live-primary")
            controller.validate(primary)
            assert controller.heartbeat(primary)
        finally:
            return_admission.set()
            actuator.release_allowed.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await authority.force_off()
            await authority.close()
            await srv.stop()
            for ws in sockets:
                ws.abort()
            await asyncio.gather(*(writer.wait_closed() for writer in writers))
            await radio.disconnect()

    clock.run(scenario)
