"""MOR-4648: stalled command ACKs must not hold managed PTT keyed."""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Callable
from pathlib import Path

import pytest
from serial_stub import SerialMockRadio

from rigplane.runtime.controller_authority import ControllerAuthority
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
    ManagedTxIntentKind,
)
from rigplane.testing.timing import VirtualClock
from rigplane.web import server as server_module
from rigplane.web.handlers import control as control_module
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from rigplane.ws import (
    WS_OP_CLOSE,
    WS_OP_CONTINUATION,
    WS_OP_PING,
    WS_OP_PONG,
    WS_OP_TEXT,
    WebSocketConnection,
    make_frame,
)


class _PttRadio(SerialMockRadio):
    _controller_authority: ControllerAuthority
    _civ_epoch = 1


class _PttActuator:
    def __init__(self, radio: _PttRadio) -> None:
        self.radio = radio
        self.operations: list[ActuationOperation | AbortOperation] = []

    async def actuate(
        self,
        token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        if not is_current():
            return ActuationResult.REJECTED
        self.operations.append(operation)
        if isinstance(operation, ActuationOperation):
            await self.radio.set_ptt(operation is not ActuationOperation.FORCE_RECEIVE)
        return ActuationResult.ACCEPTED


class _StalledTransport(asyncio.Transport):
    def __init__(self, protocol: asyncio.StreamReaderProtocol) -> None:
        super().__init__()
        self.protocol = protocol
        self.buffer = bytearray()
        self.closed = False
        self.aborted = False

    def write(self, data: bytes) -> None:
        if self.closed:
            raise BrokenPipeError("closed outbound transport")
        self.buffer.extend(data)

    def is_closing(self) -> bool:
        return self.closed

    def get_write_buffer_size(self) -> int:
        return len(self.buffer)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.protocol.connection_lost(None)

    def abort(self) -> None:
        self.aborted = True
        self.close()


class _StalledWriter(asyncio.StreamWriter):
    def __init__(self, reader: asyncio.StreamReader) -> None:
        protocol = asyncio.StreamReaderProtocol(reader)
        transport = _StalledTransport(protocol)
        protocol.connection_made(transport)
        super().__init__(transport, protocol, reader, asyncio.get_running_loop())
        self.outbound = transport
        self.stall = False
        self.drain_parked = asyncio.Event()
        self.resume = asyncio.Event()

    async def drain(self) -> None:
        if self.stall:
            self.drain_parked.set()
            await self.resume.wait()
        await super().drain()


def _command(command_id: str, name: str, params: dict[str, object]) -> bytes:
    return make_frame(
        WS_OP_TEXT,
        json.dumps(
            {"type": "cmd", "id": command_id, "name": name, "params": params}
        ).encode(),
        mask=True,
    )


@pytest.mark.parametrize("attached", [False, True], ids=["sessionless", "auxiliary"])
@pytest.mark.parametrize("blocked_write", ["ack", "pong", "close"])
@pytest.mark.parametrize(
    ("name", "params"), [("ptt_off", {}), ("ptt", {"state": False})]
)
def test_stalled_ack_unkeys_through_real_websocket_accept(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    attached: bool,
    blocked_write: str,
    name: str,
    params: dict[str, object],
) -> None:
    clock = VirtualClock(start=1000.0)
    for module in (server_module, control_module):
        clock.scope_to(monkeypatch, module)
        monkeypatch.setattr(module, "asyncio", asyncio)

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
        monkeypatch.setattr(srv, "_managed_tx_authority", lambda: authority)
        poller = RadioPoller(
            radio,
            srv.command_queue,
            state_store=srv.command_state_store,
            managed_tx_authority=authority,
            clock=clock,
        )
        readers: list[asyncio.StreamReader] = []
        writers: list[_StalledWriter] = []
        tasks: list[asyncio.Task[None]] = []

        async def connect(key: str | None, role: str) -> None:
            reader = asyncio.StreamReader()
            writer = _StalledWriter(reader)
            readers.append(reader)
            writers.append(writer)
            protocol = (
                f"Sec-WebSocket-Protocol: rigplane-controller-v1.{key}\r\n"
                if key is not None
                else ""
            )
            reader.feed_data(
                (
                    f"GET /api/v1/ws?controller_role={role} HTTP/1.1\r\n"
                    "Host: localhost\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    f"{protocol}\r\n"
                ).encode()
            )
            before = set(srv._client_tasks)
            srv._accept_client(reader, writer)
            (task,) = srv._client_tasks - before
            tasks.append(task)
            await clock.elapse(0)
            assert b"101 Switching Protocols" in writer.outbound.buffer
            assert not task.done()
            assert len(srv._control_event_queues) == len(tasks)

        try:
            key = controller.acquire("browser")["controller_key"] if attached else None
            if attached:
                await connect(key, "primary")
            await connect(key, "auxiliary")
            writer = writers[-1]
            writer.outbound.buffer.clear()
            writer.stall = blocked_write == "ack"
            unkey = _command("unkey", name, params)
            readers[-1].feed_data(_command("key", "ptt_on", {}))
            await clock.elapse(0)
            entry = srv.command_queue.take_entry()
            assert entry is not None
            await poller._execute_queued_entry(entry)
            await clock.elapse(0)
            assert radio._ptt is True
            assert actuator.operations == [ActuationOperation.PTT_ON]
            assert authority.tx_holder_nowait() is not None
            assert b'"id":"key"' in writer.outbound.buffer
            if blocked_write == "ack":
                readers[-1].feed_data(unkey)
            if blocked_write != "ack":
                assert not writer.drain_parked.is_set()
                # The key-down ACK completed before the event sender filled
                # the downlink. Uplink remains open with key-up behind PING/CLOSE.
                writer.stall = True
                srv.broadcast_notification("info", "x" * (70 * 1024))
                await clock.elapse(0)
                assert writer.outbound.get_write_buffer_size() > 64 * 1024
                opcode = WS_OP_PING if blocked_write == "pong" else WS_OP_CLOSE
                payload = b"probe" if blocked_write == "pong" else b"\x03\xe8"
                readers[-1].feed_data(
                    make_frame(WS_OP_PONG, b"alive", mask=True)
                    + make_frame(opcode, payload, mask=True)
                    + unkey
                )
                await clock.elapse(0)
                reply_opcode = WS_OP_PONG if blocked_write == "pong" else WS_OP_CLOSE
                assert writer.outbound.buffer.endswith(
                    make_frame(reply_opcode, payload)
                )
                assert readers[-1]._buffer == unkey
            assert writer.drain_parked.is_set()
            assert b'"id":"unkey"' not in writer.outbound.buffer
            assert readers[-1]._buffer

            await clock.elapse(2.99)
            assert radio._ptt is True
            assert not writer.outbound.aborted
            assert not tasks[-1].done()
            await clock.elapse(0.12)

            # These assertions precede rescue cleanup and keep output stalled.
            assert not writer.resume.is_set()
            assert radio._ptt is False, (
                f"stalled {blocked_write} prevented hardware unkey"
            )
            assert writer.outbound.aborted
            aborts = [
                record.getMessage()
                for record in caplog.records
                if record.getMessage().startswith("websocket write deadline abort:")
            ]
            assert len(aborts) == 1
            assert "connection_id=" in aborts[0]
            assert "stalled_s=3." in aborts[0]
            assert "outbound_progress=" in aborts[0]
            assert tasks[-1].done()
            assert tasks[-1].exception() is None
            state = (await authority.snapshot()).state
            assert state.intent.kind is ManagedTxIntentKind.RX
            assert state.intent.owner_token is None
            assert not state.release_required
            assert state.pending_effect is None
            assert authority.tx_holder_nowait() is None
            assert ActuationOperation.FORCE_RECEIVE in actuator.operations
            assert not srv.command_queue.has_commands
            if attached:
                assert not tasks[0].done()
                assert not writers[0].outbound.closed
                assert len(srv._control_event_queues) == 1
            else:
                assert not srv._control_event_queues
        finally:
            for writer in writers:
                writer.resume.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await authority.force_off()
            await authority.close()
            await srv.stop()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
            await radio.disconnect()

    clock.run(scenario)


@pytest.mark.parametrize("kind", ["pong", "close-echo", "frame-too-large", "fragments"])
def test_receive_loop_write_deadline_without_other_senders(kind: str) -> None:
    clock = VirtualClock()

    async def scenario() -> None:
        reader = asyncio.StreamReader()
        writer = _StalledWriter(reader)
        ws = WebSocketConnection(reader, writer, clock=clock)
        writer.stall = True
        if kind == "pong":
            incoming = make_frame(WS_OP_PING, b"probe", mask=True)
            reply = make_frame(WS_OP_PONG, b"probe")
        elif kind == "close-echo":
            incoming = make_frame(WS_OP_CLOSE, b"\x03\xe8", mask=True)
            reply = make_frame(WS_OP_CLOSE, b"\x03\xe8")
        elif kind == "frame-too-large":
            incoming = struct.pack("!BBQ", 0x81, 0xFF, 64 * 1024 + 1)
            reply = make_frame(WS_OP_CLOSE, b"\x03\xf1frame too large")
        else:
            incoming = make_frame(WS_OP_TEXT, b"x" * (64 * 1024), fin=False)
            incoming += (
                make_frame(WS_OP_CONTINUATION, b"x" * (64 * 1024), fin=False) * 4
            )
            reply = make_frame(WS_OP_CLOSE, b"\x03\xf1message too large")
        reader.feed_data(incoming)
        task = asyncio.create_task(ws.recv())
        try:
            await clock.elapse(0)
            assert writer.drain_parked.is_set()
            assert writer.outbound.buffer == reply
            await clock.elapse(2.99)
            assert not task.done()
            assert not writer.outbound.aborted
            await clock.elapse(0.12)
            assert task.done()
            assert isinstance(task.exception(), EOFError)
            assert writer.outbound.aborted
            assert not writer.resume.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            ws.abort()
            await writer.wait_closed()

    clock.run(scenario)


def test_healthy_slow_direct_write_completes_before_deadline() -> None:
    clock = VirtualClock()

    async def scenario() -> None:
        reader = asyncio.StreamReader()
        writer = _StalledWriter(reader)
        ws = WebSocketConnection(reader, writer, clock=clock)
        handler = control_module.ControlHandler(ws, None, "test", "IC-7610")
        writer.stall = True
        task = asyncio.create_task(handler._send_direct('{"type":"pong"}'))
        try:
            await clock.elapse(2.5)
            assert writer.drain_parked.is_set()
            assert not task.done()
            writer.resume.set()
            await clock.elapse(0)
            assert task.done()
            assert task.exception() is None
            assert not writer.outbound.closed
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            ws.abort()
            await writer.wait_closed()

    clock.run(scenario)


@pytest.mark.parametrize("resume", [False, True], ids=["then-stalls", "completes"])
@pytest.mark.parametrize("start", [0.0, 1000.0])
def test_progressing_direct_write_can_outlive_deadline(
    resume: bool, start: float
) -> None:
    clock = VirtualClock(start=start)

    async def scenario() -> None:
        reader = asyncio.StreamReader()
        writer = _StalledWriter(reader)
        ws = WebSocketConnection(reader, writer, clock=clock)
        handler = control_module.ControlHandler(ws, None, "test", "IC-7610")
        writer.stall = True
        task = asyncio.create_task(handler._send_direct('"' + "x" * 1000 + '"'))
        try:
            await clock.elapse(0)
            assert writer.drain_parked.is_set()
            for _ in range(5):
                del writer.outbound.buffer[:100]
                await clock.elapse(1.0)
                assert not task.done()
                assert not writer.outbound.aborted
            if resume:
                writer.resume.set()
                await clock.elapse(0)
                assert task.done()
                assert task.exception() is None
                assert not writer.outbound.closed
            else:
                # Drains occurred at t=0,1,2,3,4; the final one is
                # observed by t=4.1. Its stall deadline is in [7,7.1].
                assert clock() == pytest.approx(start + 5.0)
                await clock.elapse(1.99)
                assert not task.done()
                await clock.elapse(0.12)
                assert task.done()
                assert isinstance(task.exception(), EOFError)
                assert writer.outbound.aborted
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            ws.abort()
            await writer.wait_closed()

    clock.run(scenario)
