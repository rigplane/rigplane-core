"""Socket ingress scheduling with an unmanaged, gated provider."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from rigplane.core.exceptions import ConnectionError as ProviderConnectionError
from rigplane.rigctld import protocol
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.server import RigctldServer

_WAIT_TIMEOUT = 2.0
_FREQUENCY = 14_074_000


class _IngressTrace:
    def __init__(self, *, max_lines: int = 4) -> None:
        self.max_lines = max_lines
        self.events: list[tuple[str, object]] = []
        self.lines_received: list[bytes] = []

    def record(self, stage: str, detail: object) -> None:
        self.events.append((stage, detail))

    def line_received(self, raw: bytes) -> None:
        assert len(self.lines_received) < self.max_lines
        self.lines_received.append(raw)
        self.record("line_received", raw)


class _TracingWriter:
    def __init__(self, writer: asyncio.StreamWriter, trace: _IngressTrace) -> None:
        self._writer = writer
        self._trace = trace

    def __getattr__(self, name: str) -> Any:
        return getattr(self._writer, name)

    def write(self, data: bytes) -> None:
        self._trace.record("response_written", data)
        self._writer.write(data)


def _trace_server_ingress(server: RigctldServer, trace: _IngressTrace) -> None:
    readline = server._readline
    handle_client = server._handle_client

    async def traced_readline(reader: asyncio.StreamReader) -> bytes | None:
        raw = await readline(reader)
        if raw:
            trace.line_received(raw)
        return raw

    async def traced_handle_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        tracing_writer = _TracingWriter(writer, trace)
        await handle_client(reader, tracing_writer)  # type: ignore[arg-type]

    server._readline = traced_readline  # type: ignore[method-assign]
    server._handle_client = traced_handle_client  # type: ignore[method-assign]


async def _ingress_checkpoint(
    trace: _IngressTrace,
    expected_lines: list[bytes],
) -> None:
    await asyncio.sleep(0)
    assert trace.lines_received == expected_lines


class _GatedRadio:
    def __init__(
        self,
        *,
        fail_frequency: bool = False,
        trace: _IngressTrace | None = None,
    ) -> None:
        self.capabilities: set[str] = set()
        self.fail_frequency = fail_frequency
        self.trace = trace
        self.frequency_entered = asyncio.Event()
        self.release_frequency = asyncio.Event()
        self.frequency_finished = asyncio.Event()
        self.ptt_off_written = asyncio.Event()
        self.calls: list[str] = []

    async def set_freq(self, freq: int, receiver: int = 0) -> None:
        assert (freq, receiver) == (_FREQUENCY, 0)
        self.calls.append("frequency entered")
        if self.trace is not None:
            self.trace.record("provider_entered", "set_freq")
        self.frequency_entered.set()
        await self.release_frequency.wait()
        self.calls.append("frequency finished")
        if self.trace is not None:
            self.trace.record("provider_settled", "set_freq")
        self.frequency_finished.set()
        if self.fail_frequency:
            raise ProviderConnectionError("frequency provider failure")

    async def set_ptt(self, on: bool) -> None:
        self.calls.append("ptt on" if on else "ptt off")
        if self.trace is not None:
            self.trace.record("provider_entered", "ptt_on" if on else "ptt_off")
            self.trace.record("provider_settled", "ptt_on" if on else "ptt_off")
        if not on:
            self.ptt_off_written.set()


@asynccontextmanager
async def _connected(
    radio: _GatedRadio,
    *,
    trace: _IngressTrace | None = None,
) -> AsyncIterator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    config = RigctldConfig(command_timeout=30.0)
    handler = RigctldHandler(radio, config)  # type: ignore[arg-type]
    server = RigctldServer(
        radio,  # type: ignore[arg-type]
        config,
        _protocol=protocol,
        _handler=handler,
    )
    if trace is not None:
        _trace_server_ingress(server, trace)
        unsubscribe = handler._command_service.subscribe_lifecycle(
            lambda event: trace.record(
                "intent_accepted" if event.state == "accepted" else event.state,
                event.command_id,
            )
            if event.source == "rigctld"
            else None
        )
    else:
        unsubscribe = None
    listener = await asyncio.start_server(
        server._accept_client, host="127.0.0.1", port=0
    )
    server._server = listener
    writer: asyncio.StreamWriter | None = None
    try:
        port = listener.sockets[0].getsockname()[1]
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=_WAIT_TIMEOUT
        )
        yield reader, writer
    finally:
        radio.release_frequency.set()
        if unsubscribe is not None:
            unsubscribe()
        try:
            if writer is not None:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=_WAIT_TIMEOUT)
        finally:
            await asyncio.wait_for(server.stop(), timeout=_WAIT_TIMEOUT)


async def _collect_replies(
    reader: asyncio.StreamReader,
    radio: _GatedRadio,
    count: int,
    ready: asyncio.Event,
) -> list[tuple[bytes, bool]]:
    replies: list[tuple[bytes, bool]] = []
    ready.set()
    for _ in range(count):
        line = await reader.readline()
        replies.append((line, radio.frequency_finished.is_set()))
    return replies


async def test_pipelined_unmanaged_off_is_read_without_overtaking_frequency() -> None:
    trace = _IngressTrace(max_lines=2)
    radio = _GatedRadio(trace=trace)
    async with _connected(radio, trace=trace) as (reader, writer):
        ready = asyncio.Event()
        replies = asyncio.create_task(_collect_replies(reader, radio, 2, ready))
        try:
            await asyncio.wait_for(ready.wait(), timeout=_WAIT_TIMEOUT)
            commands = [f"F {_FREQUENCY}".encode("ascii"), b"T 0"]
            writer.write(b"\n".join(commands) + b"\n")
            await writer.drain()
            await asyncio.wait_for(
                radio.frequency_entered.wait(), timeout=_WAIT_TIMEOUT
            )

            await _ingress_checkpoint(trace, commands)
            assert not radio.ptt_off_written.is_set()
            assert ("response_written", b"RPRT 0\n") not in trace.events

            radio.release_frequency.set()
            assert await asyncio.wait_for(replies, timeout=_WAIT_TIMEOUT) == [
                (b"RPRT 0\n", True),
                (b"RPRT 0\n", True),
            ]
            assert radio.calls == [
                "frequency entered",
                "frequency finished",
                "ptt off",
            ]
        finally:
            radio.release_frequency.set()
            replies.cancel()
            await asyncio.gather(replies, return_exceptions=True)


async def test_frequency_provider_failure_is_not_an_enqueue_ack() -> None:
    radio = _GatedRadio(fail_frequency=True)
    async with _connected(radio) as (reader, writer):
        ready = asyncio.Event()
        replies = asyncio.create_task(_collect_replies(reader, radio, 2, ready))
        try:
            await asyncio.wait_for(ready.wait(), timeout=_WAIT_TIMEOUT)
            writer.write(f"F {_FREQUENCY}\nT 0\n".encode("ascii"))
            await writer.drain()
            await asyncio.wait_for(
                radio.frequency_entered.wait(), timeout=_WAIT_TIMEOUT
            )
            assert not radio.frequency_finished.is_set()
            radio.release_frequency.set()

            received = await asyncio.wait_for(replies, timeout=_WAIT_TIMEOUT)
            assert received == [(b"RPRT -6\n", True), (b"RPRT 0\n", True)]
        finally:
            radio.release_frequency.set()
            replies.cancel()
            await asyncio.gather(replies, return_exceptions=True)


async def test_sequential_frequency_then_off_preserves_call_and_reply_order() -> None:
    radio = _GatedRadio()
    async with _connected(radio) as (reader, writer):
        writer.write(f"F {_FREQUENCY}\n".encode("ascii"))
        await writer.drain()
        await asyncio.wait_for(radio.frequency_entered.wait(), timeout=_WAIT_TIMEOUT)
        radio.release_frequency.set()
        assert await asyncio.wait_for(reader.readline(), timeout=_WAIT_TIMEOUT) == (
            b"RPRT 0\n"
        )
        assert radio.calls == ["frequency entered", "frequency finished"]

        writer.write(b"T 0\n")
        await writer.drain()
        assert await asyncio.wait_for(reader.readline(), timeout=_WAIT_TIMEOUT) == (
            b"RPRT 0\n"
        )
        assert radio.calls == ["frequency entered", "frequency finished", "ptt off"]


async def test_pipelined_on_does_not_overtake_held_frequency() -> None:
    radio = _GatedRadio()
    async with _connected(radio) as (reader, writer):
        ready = asyncio.Event()
        replies = asyncio.create_task(_collect_replies(reader, radio, 2, ready))
        try:
            await asyncio.wait_for(ready.wait(), timeout=_WAIT_TIMEOUT)
            writer.write(f"F {_FREQUENCY}\nT 1\n".encode("ascii"))
            await writer.drain()
            await asyncio.wait_for(
                radio.frequency_entered.wait(), timeout=_WAIT_TIMEOUT
            )
            assert radio.calls == ["frequency entered"]
            radio.release_frequency.set()

            received = await asyncio.wait_for(replies, timeout=_WAIT_TIMEOUT)
            assert received == [(b"RPRT 0\n", True), (b"RPRT 0\n", True)]
            assert radio.calls == [
                "frequency entered",
                "frequency finished",
                "ptt on",
            ]
            writer.write(b"T 0\n")
            await writer.drain()
            assert await asyncio.wait_for(reader.readline(), timeout=_WAIT_TIMEOUT) == (
                b"RPRT 0\n"
            )
        finally:
            radio.release_frequency.set()
            replies.cancel()
            await asyncio.gather(replies, return_exceptions=True)
