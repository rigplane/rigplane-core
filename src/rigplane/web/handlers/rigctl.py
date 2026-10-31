"""rigctl WebSocket channel: rigctld protocol lines over ``/api/v1/rigctl``.

Only the transport lives here (MOR-3536). Each text message from the client
carries one or more rigctld command lines (a missing final newline is implied;
a line never spans two messages), and every reply the rigctld server writes
goes back as one text message. The lines run through the rigctld server's own
connection loop — parser, handler, managed-PTT path and teardown release —
reached through the ``open_channel`` callable the composition root attaches,
so the web layer never imports ``rigctld`` (``independence-top``).
"""

from __future__ import annotations

import asyncio
import collections
from collections.abc import Callable
from typing import Any

from ..transport import Connection  # noqa: TID251
from ...ws import WS_OP_TEXT, WebSocketError  # noqa: TID251

__all__ = ["OpenRigctlChannel", "RigctlChannelHandler"]

#: ``RigctldServer.open_channel``: serve one connection over the given line
#: reader and message writer as ``owner``; ``None`` when refused.
OpenRigctlChannel = Callable[[Any, Any, str], "asyncio.Task[None] | None"]

# Lines received but not yet read by the rigctld loop. Past this the channel
# stops reading the WebSocket, as a TCP socket stops reading its peer.
_MAX_QUEUED_LINES = 64


class _LineReader:
    """``readline()`` over WebSocket messages, the one reader call rigctld makes."""

    def __init__(self, limit: int = _MAX_QUEUED_LINES) -> None:
        self._lines: collections.deque[bytes] = collections.deque()
        self._limit = limit
        self._eof = False
        self._changed = asyncio.Event()

    async def _wait(self) -> None:
        self._changed.clear()
        await self._changed.wait()

    async def feed(self, text: str) -> None:
        lines = text.split("\n")
        if lines[-1] == "":
            lines.pop()
        for line in lines:
            while len(self._lines) >= self._limit and not self._eof:
                await self._wait()
            if self._eof:
                return
            self._lines.append(line.encode("utf-8") + b"\n")
            self._changed.set()

    def feed_eof(self) -> None:
        self._eof = True
        self._changed.set()

    async def readline(self) -> bytes:
        while not self._lines:
            if self._eof:
                return b""
            await self._wait()
        line = self._lines.popleft()
        self._changed.set()
        return line


class _MessageWriter:
    """The writer surface rigctld uses; each drain sends one text message."""

    def __init__(
        self, ws: Connection, reader: _LineReader, peername: tuple[Any, ...]
    ) -> None:
        self._ws = ws
        self._reader = reader
        self._peername = peername
        self._buffer = bytearray()

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return self._peername if name == "peername" else default

    def write(self, data: bytes) -> None:
        self._buffer += data

    async def drain(self) -> None:
        if self._buffer:
            text = self._buffer.decode("utf-8", "replace")
            self._buffer.clear()
            await self._ws.send_text(text)

    def close(self) -> None:
        # What a closed socket does to its own reader: the loop sees EOF.
        self._reader.feed_eof()

    async def wait_closed(self) -> None:
        await self._ws.close()


class RigctlChannelHandler:
    """Carry one ``/api/v1/rigctl`` WebSocket into the rigctld server.

    Args:
        ws: Established WebSocket connection.
        open_channel: ``RigctldServer.open_channel``.
        owner: Managed-TX owner of the controller session the channel joined.
        peername: The WebSocket peer, for rigctld's logs.
    """

    def __init__(
        self,
        ws: Connection,
        open_channel: OpenRigctlChannel,
        owner: str,
        peername: tuple[Any, ...],
    ) -> None:
        self._ws = ws
        self._open = open_channel
        self._owner = owner
        self._peername = peername

    async def run(self) -> None:
        reader = _LineReader()
        # Opened here, in the session's controller context: the rigctld task
        # copies it, so the session's currency fences every key it issues.
        session = self._open(
            reader, _MessageWriter(self._ws, reader, self._peername), self._owner
        )
        if session is None:
            await self._ws.close(1013, "rigctl channel unavailable")
            return
        pump = asyncio.create_task(self._pump(reader))
        try:
            await asyncio.shield(session)
        except asyncio.CancelledError:
            # rigctld absorbs a cancel to finish its teardown and then
            # returns; wait that out, but stay cancelled.
            session.cancel()
            await asyncio.gather(session, return_exceptions=True)
            raise
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)

    async def _pump(self, reader: _LineReader) -> None:
        try:
            while True:
                opcode, payload = await self._ws.recv()
                if opcode != WS_OP_TEXT:
                    await self._ws.close(1003, "rigctl channel carries text only")
                    return
                await reader.feed(payload.decode("utf-8", "replace"))
        except (EOFError, OSError, WebSocketError):
            pass
        finally:
            # The client closing the WebSocket is EOF to rigctld, which then
            # retires the connection.
            reader.feed_eof()
