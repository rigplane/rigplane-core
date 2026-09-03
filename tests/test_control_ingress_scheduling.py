"""Control-frame receipt while a descriptor queue completion is pending."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

from rigplane.core.command_service import CommandService
from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import CommandIntent
from rigplane.runtime._poller_types import CommandQueue, CommandQueueEntry
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.websocket import WS_OP_TEXT

_WAIT_TIMEOUT = 2.0
_SHIFT_FRAME = (
    b'{"type":"cmd","id":"shift","name":"set_repeater_shift",'
    b'"params":{"direction":1}}'
)
_OFF_FRAME = b'{"type":"cmd","id":"off","name":"ptt_off","params":{}}'


class _ControlSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.responses: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.sent: list[dict[str, object]] = []
        self.off_received = asyncio.Event()

    async def recv(self) -> tuple[int, bytes]:
        payload = await self.incoming.get()
        if payload is None:
            raise EOFError
        if payload == _OFF_FRAME:
            self.off_received.set()
        return WS_OP_TEXT, payload

    async def send_text(self, text: str) -> None:
        message = json.loads(text)
        self.sent.append(message)
        if message.get("type") == "response":
            self.responses.put_nowait(message)

    async def close(self) -> None:
        self.incoming.put_nowait(None)


@asynccontextmanager
async def _running_control(
    *, pipelined_off: bool
) -> AsyncIterator[tuple[_ControlSocket, CommandQueueEntry]]:
    ws = _ControlSocket()
    queue = CommandQueue()
    radio = SimpleNamespace(
        connected=True,
        radio_ready=True,
        capabilities=set(),
        supports_command=lambda name: name == "set_repeater_shift",
    )
    server = SimpleNamespace(
        command_queue=queue,
        register_control_event_queue=lambda *args, **kwargs: {
            "type": "full",
            "data": {},
        },
        unregister_control_event_queue=lambda *args, **kwargs: None,
    )
    handler = ControlHandler(
        ws,  # type: ignore[arg-type]
        radio,
        "test",
        "",
        server=server,
        session_id="ws-ingress-test",
    )
    ws.incoming.put_nowait(_SHIFT_FRAME)
    if pipelined_off:
        ws.incoming.put_nowait(_OFF_FRAME)
    run = asyncio.create_task(handler.run())
    pending: asyncio.Future[None] | None = None
    try:
        await asyncio.wait_for(queue.wait(), timeout=_WAIT_TIMEOUT)
        entries = queue.drain_entries()
        shifts = [entry for entry in entries if entry.command_id == "shift"]
        assert len(shifts) == 1
        entry = shifts[0]
        pending = entry.future
        assert pending is not None and not pending.done()
        assert isinstance(entry.command, CommandIntent)
        assert entry.command.name == "set_repeater_shift"
        assert entry.source == "websocket"
        assert entry.session_id == "ws-ingress-test"
        assert isinstance(entry.command_service, CommandService)
        assert [
            event.state
            for event in entry.command_service.lifecycle_events()
            if event.command_id == "shift"
        ] == ["accepted", "queued", "sent"]
        yield ws, entry
    finally:
        if pending is not None and not pending.done():
            pending.set_result(None)
        await ws.close()
        try:
            await asyncio.wait_for(run, timeout=_WAIT_TIMEOUT)
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)


async def test_run_receives_off_while_descriptor_completion_is_pending() -> None:
    async with _running_control(pipelined_off=True) as (ws, entry):
        await asyncio.wait_for(ws.off_received.wait(), timeout=_WAIT_TIMEOUT)
        assert entry.future is not None and not entry.future.done()
        assert not any(message.get("id") == "shift" for message in ws.sent)


async def test_sequential_off_is_received_after_descriptor_completion() -> None:
    async with _running_control(pipelined_off=False) as (ws, entry):
        assert entry.future is not None
        entry.future.set_result(None)
        response = await asyncio.wait_for(ws.responses.get(), timeout=_WAIT_TIMEOUT)
        assert response == {
            "type": "response",
            "id": "shift",
            "ok": True,
            "result": {"direction": 1, "receiver": 0},
        }
        ws.incoming.put_nowait(_OFF_FRAME)
        await asyncio.wait_for(ws.off_received.wait(), timeout=_WAIT_TIMEOUT)
        response = await asyncio.wait_for(ws.responses.get(), timeout=_WAIT_TIMEOUT)
        assert response == {"type": "response", "id": "off", "ok": True, "result": {}}


async def test_descriptor_completion_failure_preserves_command_error_response() -> None:
    async with _running_control(pipelined_off=False) as (ws, entry):
        assert entry.future is not None
        entry.future.set_exception(CommandError("queued command failed"))
        response = await asyncio.wait_for(ws.responses.get(), timeout=_WAIT_TIMEOUT)
        assert response == {
            "type": "response",
            "id": "shift",
            "ok": False,
            "error": "command_failed",
            "message": "queued command failed",
        }
