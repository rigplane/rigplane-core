"""Ordered same-socket rigctld ingress without managed TX."""
# fmt: off

from __future__ import annotations

import asyncio
import gc
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from rigplane.core.exceptions import ConnectionError as ProviderConnectionError
from rigplane.rigctld import protocol
from rigplane.rigctld.contract import HamlibError, RigctldConfig, RigctldResponse
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.server import _MAX_PENDING_CLIENT_RESPONSES, RigctldServer

_WAIT_TIMEOUT = 2.0
_FREQUENCY = 14_074_000
class _Radio:
    def __init__(self, *, fail: bool = False) -> None:
        self.capabilities: set[str] = set()
        self.fail = fail
        self.entered, self.release, self.finished = (asyncio.Event() for _ in range(3))
        self.calls: list[str] = []

    async def set_freq(self, freq: int, receiver: int = 0) -> None:
        assert (freq, receiver) == (_FREQUENCY, 0)
        self.calls.append("F-enter")
        self.entered.set()
        await self.release.wait()
        self.calls.append("F-finish")
        self.finished.set()
        if self.fail:
            raise ProviderConnectionError("injected")

    async def set_ptt(self, on: bool) -> None:
        self.calls.append(f"T-{int(on)}")
class _FailingWriter:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer, self.closed = writer, asyncio.Event()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._writer, name)

    def write(self, data: bytes) -> None:
        raise ConnectionResetError("injected")

    def close(self) -> None:
        self.closed.set()
        self._writer.close()
@asynccontextmanager
async def _connected(
    radio: _Radio | None = None,
    *,
    handler: Any = None,
    command_timeout: float = 30.0,
    fail_writer: bool = False,
) -> AsyncIterator[SimpleNamespace]:
    radio = radio or _Radio()
    config = RigctldConfig(command_timeout=command_timeout)
    server = RigctldServer(
        radio,  # type: ignore[arg-type]
        config,
        _protocol=protocol,
        _handler=handler or RigctldHandler(radio, config),  # type: ignore[arg-type]
    )
    lines: list[bytes] = []
    readline = server._readline
    async def observed(reader: asyncio.StreamReader) -> bytes | None:
        raw = await readline(reader)
        if raw:
            lines.append(raw)
        return raw

    server._readline = observed  # type: ignore[method-assign]
    failing: list[_FailingWriter] = []
    if fail_writer:
        handle = server._handle_client

        async def wrapped(reader, writer):
            probe = _FailingWriter(writer)
            failing.append(probe)
            await handle(reader, probe)  # type: ignore[arg-type]

        server._handle_client = wrapped  # type: ignore[method-assign]
    listener = await asyncio.start_server(server._accept_client, "127.0.0.1", 0)
    server._server = listener
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", listener.sockets[0].getsockname()[1]
    )
    try:
        yield SimpleNamespace(
            radio=radio,
            server=server,
            reader=reader,
            writer=writer,
            lines=lines,
            failing=failing,
        )
    finally:
        radio.release.set()
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        await server.stop()
async def _replies(rig: SimpleNamespace, count: int) -> list[bytes]:
    return [
        await asyncio.wait_for(rig.reader.readline(), _WAIT_TIMEOUT)
        for _ in range(count)
    ]
async def test_pipelined_unmanaged_off_is_read_without_overtaking_frequency() -> None:
    async with _connected() as rig:
        rig.writer.write(f"F {_FREQUENCY}\nT 0\n".encode())
        await rig.writer.drain()
        await asyncio.wait_for(rig.radio.entered.wait(), _WAIT_TIMEOUT)
        while len(rig.lines) < 2:
            await asyncio.sleep(0)
        assert rig.lines == [f"F {_FREQUENCY}".encode(), b"T 0"]
        assert rig.radio.calls == ["F-enter"]
        rig.radio.release.set()
        assert await _replies(rig, 2) == [b"RPRT 0\n", b"RPRT 0\n"]
        assert rig.radio.calls == ["F-enter", "F-finish", "T-0"]
async def test_frequency_provider_failure_is_not_an_enqueue_ack() -> None:
    async with _connected(_Radio(fail=True)) as rig:
        rig.writer.write(f"F {_FREQUENCY}\nT 0\n".encode())
        await rig.writer.drain()
        await asyncio.wait_for(rig.radio.entered.wait(), _WAIT_TIMEOUT)
        rig.radio.release.set()
        assert await _replies(rig, 2) == [b"RPRT -6\n", b"RPRT 0\n"]
async def test_sequential_frequency_then_off_preserves_call_and_reply_order() -> None:
    async with _connected() as rig:
        rig.writer.write(f"F {_FREQUENCY}\n".encode())
        await rig.writer.drain()
        await asyncio.wait_for(rig.radio.entered.wait(), _WAIT_TIMEOUT)
        rig.radio.release.set()
        assert await _replies(rig, 1) == [b"RPRT 0\n"]
        rig.writer.write(b"T 0\n")
        await rig.writer.drain()
        assert await _replies(rig, 1) == [b"RPRT 0\n"]
        assert rig.radio.calls == ["F-enter", "F-finish", "T-0"]
async def test_completed_response_slots_are_pruned_before_capacity_limit() -> None:
    async with _connected() as rig:
        original, refs = rig.server._execute_and_retire_client_command, []

        async def observed(*args, **kwargs):
            refs.append(weakref.ref(asyncio.current_task()))
            await original(*args, **kwargs)

        rig.server._execute_and_retire_client_command = observed
        for _ in range(_MAX_PENDING_CLIENT_RESPONSES + 2):
            rig.writer.write(b"T 0\n")
            await rig.writer.drain()
            assert await _replies(rig, 1) == [b"RPRT 0\n"]
        await asyncio.sleep(0)
        gc.collect()
        assert len(refs) == _MAX_PENDING_CLIENT_RESPONSES + 2
        assert sum(ref() is not None for ref in refs) <= 1
async def test_response_writer_failure_closes_and_drains_connection_owner() -> None:
    async with _connected(fail_writer=True) as rig:
        while not rig.failing:
            await asyncio.sleep(0)
        connections = tuple(rig.server._client_tasks)
        rig.writer.write(b"T 0\n")
        await rig.writer.drain()
        await asyncio.wait_for(rig.failing[0].closed.wait(), _WAIT_TIMEOUT)
        assert await asyncio.wait_for(rig.reader.readline(), _WAIT_TIMEOUT) == b""
        await asyncio.gather(*connections)
        assert all(task.done() for task in connections)
class _StateHandler:
    def __init__(self) -> None:
        self.entered, self.release, self.second = (asyncio.Event() for _ in range(3))
        self.changed = False

    async def execute(self, cmd) -> RigctldResponse:
        if cmd.long_cmd == "set_freq":
            self.entered.set()
            await self.release.wait()
            self.changed = True
        else:
            self.second.set()
        return RigctldResponse(
            error=HamlibError.OK if self.changed else HamlibError.ERJCTED
        )
async def test_legacy_handler_invocation_observes_predecessor_state_change() -> None:
    handler = _StateHandler()
    async with _connected(handler=handler) as rig:
        rig.writer.write(f"F {_FREQUENCY}\nT 1\n".encode())
        await rig.writer.drain()
        await asyncio.wait_for(handler.entered.wait(), _WAIT_TIMEOUT)
        await asyncio.sleep(0)
        assert not handler.second.is_set()
        handler.release.set()
        assert await _replies(rig, 2) == [b"RPRT 0\n", b"RPRT 0\n"]
class _TimedHandler:
    async def execute(self, cmd, *, predecessor) -> RigctldResponse:
        await asyncio.shield(predecessor)
        await asyncio.sleep(0.15)
        return RigctldResponse()
async def test_command_timeout_starts_after_predecessor_finishes() -> None:
    async with _connected(handler=_TimedHandler(), command_timeout=0.25) as rig:
        rig.writer.write(f"F {_FREQUENCY}\nT 0\n".encode())
        await rig.writer.drain()
        assert await _replies(rig, 2) == [b"RPRT 0\n", b"RPRT 0\n"]
async def test_quit_retires_an_already_admitted_response() -> None:
    async with _connected() as rig:
        rig.writer.write(f"F {_FREQUENCY}\nq\n".encode())
        await rig.writer.drain()
        await asyncio.wait_for(rig.radio.entered.wait(), _WAIT_TIMEOUT)
        rig.radio.release.set()
        assert await _replies(rig, 2) == [b"RPRT 0\n", b""]
async def test_pipelined_on_does_not_overtake_held_frequency() -> None:
    async with _connected() as rig:
        rig.writer.write(f"F {_FREQUENCY}\nT 1\n".encode())
        await rig.writer.drain()
        await asyncio.wait_for(rig.radio.entered.wait(), _WAIT_TIMEOUT)
        assert rig.radio.calls == ["F-enter"]
        rig.radio.release.set()
        assert await _replies(rig, 2) == [b"RPRT 0\n", b"RPRT 0\n"]
        assert rig.radio.calls == ["F-enter", "F-finish", "T-1"]
