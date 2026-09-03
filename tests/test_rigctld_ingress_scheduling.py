"""Socket ingress scheduling with an unmanaged, gated provider."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from rigplane.core.exceptions import ConnectionError as ProviderConnectionError
from rigplane.rigctld import protocol
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.server import RigctldServer

_WAIT_TIMEOUT = 2.0
_FREQUENCY = 14_074_000


class _GatedRadio:
    def __init__(self, *, fail_frequency: bool = False) -> None:
        self.capabilities: set[str] = set()
        self.fail_frequency = fail_frequency
        self.frequency_entered = asyncio.Event()
        self.release_frequency = asyncio.Event()
        self.frequency_finished = asyncio.Event()
        self.ptt_off_written = asyncio.Event()
        self.calls: list[str] = []

    async def set_freq(self, freq: int, receiver: int = 0) -> None:
        assert (freq, receiver) == (_FREQUENCY, 0)
        self.calls.append("frequency entered")
        self.frequency_entered.set()
        await self.release_frequency.wait()
        self.calls.append("frequency finished")
        self.frequency_finished.set()
        if self.fail_frequency:
            raise ProviderConnectionError("frequency provider failure")

    async def set_ptt(self, on: bool) -> None:
        self.calls.append("ptt on" if on else "ptt off")
        if not on:
            self.ptt_off_written.set()


@asynccontextmanager
async def _connected(
    radio: _GatedRadio,
) -> AsyncIterator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    config = RigctldConfig(command_timeout=30.0)
    handler = RigctldHandler(radio, config)  # type: ignore[arg-type]
    server = RigctldServer(
        radio,  # type: ignore[arg-type]
        config,
        _protocol=protocol,
        _handler=handler,
    )
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
