"""Carry rigctld lines over the rigctl WebSocket or WebRTC data channel.

Only the transport lives here (MOR-3536). Each text message from the client
carries one or more rigctld command lines (a missing final newline is implied;
a line never spans two messages). WebRTC requires the final LF and bounds
each message in both directions. The lines run through the rigctld server's own
connection loop — parser, handler, managed-PTT path and teardown release —
reached through the ``open_channel`` callable the composition root attaches,
so the web layer never imports ``rigctld`` (``independence-top``).
"""

from __future__ import annotations

import asyncio
import collections
from collections.abc import Callable
from typing import Any

from ..channel_attach import (  # noqa: TID251
    RIGCTL_MAX_FRAME_BYTES,
    ChannelAttachedAck,
    RigctlAttachErrorV1,
)
from ..transport import Connection  # noqa: TID251
from ...ws import WS_OP_TEXT, WebSocketError  # noqa: TID251

__all__ = ["OpenRigctlChannel", "RigctlChannelHandler"]

#: ``RigctldServer.open_channel``: serve one connection over the given line
#: reader and message writer as ``owner``; ``None`` when refused.
OpenRigctlChannel = Callable[[Any, Any, str], "asyncio.Task[None] | None"]

# Lines received but not yet read by the rigctld loop. Past this the channel
# stops reading the WebSocket, as a TCP socket stops reading its peer.
_MAX_QUEUED_LINES = 64
_MAX_RIGCTL_REPLY_BYTES = 4 * RIGCTL_MAX_FRAME_BYTES


class _LineReader:
    """``readline()`` over WebSocket messages, the one reader call rigctld makes."""

    def __init__(
        self, limit: int = _MAX_QUEUED_LINES, *, datachannel: bool = False
    ) -> None:
        self._lines: collections.deque[bytes] = collections.deque()
        self._limit = limit
        self._datachannel = datachannel
        self._eof = False
        self._changed = asyncio.Event()

    async def _wait(self) -> None:
        self._changed.clear()
        await self._changed.wait()

    async def feed(self, text: str) -> None:
        lines = text.split("\n")
        if lines[-1] == "":
            lines.pop()
        if self._datachannel and len(self._lines) + len(lines) > self._limit:
            raise ValueError("rigctl command queue full")
        for line in lines:
            while len(self._lines) >= self._limit and not self._eof:
                await self._wait()
            if self._eof:
                return
            self._lines.append(line.encode("utf-8") + b"\n")
            self._changed.set()

    def feed_eof(self) -> None:
        self._eof = True
        if self._datachannel:
            self._lines.clear()
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
        self,
        ws: Connection,
        reader: _LineReader,
        peername: tuple[Any, ...],
        *,
        datachannel: bool = False,
    ) -> None:
        self._ws = ws
        self._reader = reader
        self._peername = peername
        self._datachannel = datachannel
        self._buffer = bytearray()

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return self._peername if name == "peername" else default

    def write(self, data: bytes) -> None:
        if (
            self._datachannel
            and len(self._buffer) + len(data) > _MAX_RIGCTL_REPLY_BYTES
        ):
            self._ws.abort()
            raise OSError("rigctl reply buffer full")
        self._buffer += data

    async def drain(self) -> None:
        if self._datachannel:
            while self._buffer:
                end = min(RIGCTL_MAX_FRAME_BYTES, len(self._buffer))
                while end < len(self._buffer) and self._buffer[end] & 0xC0 == 0x80:
                    end -= 1  # keep each UTF-8 codepoint in one message
                if not end:
                    raise UnicodeError("invalid rigctl reply encoding")
                text = self._buffer[:end].decode("utf-8")
                del self._buffer[:end]
                await self._ws.send_text(text)
        elif self._buffer:
            text = self._buffer.decode("utf-8", "replace")
            self._buffer.clear()
            await self._ws.send_text(text)

    def close(self) -> None:
        # What a closed socket does to its own reader: the loop sees EOF.
        self._reader.feed_eof()

    async def wait_closed(self) -> None:
        await self._ws.close()


class RigctlChannelHandler:
    """Carry one rigctl connection into the rigctld server.

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
        *,
        datachannel: bool = False,
    ) -> None:
        self._ws = ws
        self._open = open_channel
        self._owner = owner
        self._peername = peername
        self._datachannel = datachannel

    async def run(self) -> None:
        if self._datachannel and not self._ws.is_alive():
            return
        reader = _LineReader(datachannel=self._datachannel)
        writer = _MessageWriter(
            self._ws, reader, self._peername, datachannel=self._datachannel
        )
        # Opened here, in the session's controller context: the rigctld task
        # copies it, so the session's currency fences every key it issues.
        session = self._open(reader, writer, self._owner)
        if session is None:
            try:
                if self._datachannel:
                    await self._ws.send_text(
                        RigctlAttachErrorV1("rigctl_busy").encode()
                    )
            finally:
                await self._ws.close(1013, "rigctl channel unavailable")
            return
        pump: asyncio.Task[None] | None = None
        try:
            if self._datachannel:
                await self._ws.send_text(ChannelAttachedAck("rigctl").encode())
            pump = asyncio.create_task(self._pump(reader, session))
            await asyncio.shield(session)
        finally:
            if pump is not None:
                pump.cancel()
            if not session.done() and not session.cancelling():
                session.cancel()
            tasks = (session,) if pump is None else (session, pump)
            cleanup = asyncio.gather(*tasks, return_exceptions=True)
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
            finally:
                if self._datachannel:
                    await self._ws.close()

    async def _pump(self, reader: _LineReader, session: asyncio.Task[None]) -> None:
        try:
            while True:
                opcode, payload = await self._ws.recv()
                if opcode != WS_OP_TEXT:
                    if not self._datachannel:
                        await self._ws.close(1003, "rigctl channel carries text only")
                        return
                    raise ValueError("rigctl data channel carries text only")
                if self._datachannel and (
                    not payload.endswith(b"\n")
                    or b"\r" in payload.replace(b"\r\n", b"")
                ):
                    raise ValueError("rigctl data channel needs complete command lines")
                await reader.feed(payload.decode("utf-8", "replace"))
        except ValueError:
            self._ws.abort()
        except (EOFError, OSError, WebSocketError):
            pass
        finally:
            reader.feed_eof()
            if self._datachannel and not session.done() and not session.cancelling():
                # End queued execution even if rigctld is waiting for replies.
                session.cancel()
