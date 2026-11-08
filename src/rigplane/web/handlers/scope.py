"""Scope WebSocket handler — binary scope frame channel with backpressure."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, Literal, TypedDict

from ..protocol import decode_json, encode_json  # noqa: TID251
from ..transport import Connection  # noqa: TID251
from ...ws import WS_OP_TEXT  # noqa: TID251

__all__ = ["HIGH_WATERMARK", "ScopeHandler"]

logger = logging.getLogger(__name__)

HIGH_WATERMARK = 5  # Max queued scope/meter frames before dropping


class ScopeTraceEvent(TypedDict):
    """Scalar, connection-local diagnostics; no payload or source-age claim."""

    stage: str
    channel: str
    wire_sequence: int | None
    queue_depth: int
    monotonic_ns: int


TraceStage = Literal[
    "enqueue",
    "drop_oldest",
    "drop_new",
    "send_start",
    "send_complete",
    "send_cancelled",
    "send_error",
]


class ScopeHandler:
    """Handles the /api/v1/scope binary WebSocket channel.

    Receives scope frames from the radio and forwards them to the client.
    Implements backpressure: drops frames when the send queue exceeds
    HIGH_WATERMARK.

    Args:
        ws: Established WebSocket connection.
    """

    def __init__(
        self,
        ws: Connection,
        server: Any = None,
        audio_mode: bool = False,
        *,
        trace_observer: Callable[[ScopeTraceEvent], None] | None = None,
    ) -> None:
        self._ws = ws
        self._server = server
        self._audio_mode = audio_mode
        self._frame_queue: asyncio.Queue[bytes] = asyncio.Queue(
            maxsize=HIGH_WATERMARK * 2
        )
        self._running = False
        # Opt-in observers must be bounded/non-blocking. Disable a failed
        # observer without changing frame delivery or sender cancellation.
        self._trace_observer = trace_observer

    def _observe(self, stage: TraceStage, data: bytes) -> None:
        observer = self._trace_observer
        if observer is None:
            return
        try:
            observer(
                ScopeTraceEvent(
                    stage=stage,
                    channel="audio-scope" if self._audio_mode else "scope",
                    wire_sequence=int.from_bytes(data[11:13], "little")
                    if len(data) >= 13
                    else None,
                    queue_depth=self._frame_queue.qsize(),
                    monotonic_ns=time.monotonic_ns(),
                )
            )
        except Exception:
            self._trace_observer = None
            logger.warning("scope: diagnostic observer disabled after failure")

    def enqueue_frame(self, encoded: bytes) -> None:
        """Enqueue the server's shared wire frame (protocol.encode_scope_frame)."""
        if not self._running:
            return
        if self._frame_queue.full():
            try:
                dropped = self._frame_queue.get_nowait()
                self._observe("drop_oldest", dropped)
            except asyncio.QueueEmpty:
                pass
        try:
            self._frame_queue.put_nowait(encoded)
            self._observe("enqueue", encoded)
        except asyncio.QueueFull:
            self._observe("drop_new", encoded)
            logger.debug("ScopeHandler: frame queue full, dropping frame")

    async def run(self) -> None:
        """Run the scope channel lifecycle."""
        self._running = True
        # Register with server — server handles enable_scope() once
        if self._server is not None:
            if self._audio_mode:
                await self._server.ensure_audio_scope_enabled(self)
            else:
                await self._server.ensure_scope_enabled(self)
        try:
            sender_task = asyncio.create_task(self._sender())
            try:
                while True:
                    opcode, payload = await self._ws.recv()
                    if opcode == WS_OP_TEXT:
                        try:
                            text = payload.decode("utf-8")
                        except UnicodeDecodeError:
                            continue
                        await self._handle_control(text)
            except EOFError:
                pass
            finally:
                sender_task.cancel()
                try:
                    await sender_task
                except asyncio.CancelledError:
                    pass
        finally:
            self._running = False
            if self._server is not None:
                if self._audio_mode:
                    self._server.unregister_audio_scope_handler(self)
                else:
                    self._server.unregister_scope_handler(self)

    async def _handle_control(self, text: str) -> None:
        """Handle optional JSON control messages on the scope channel."""
        try:
            msg = decode_json(text)
        except ValueError:
            return
        if msg.get("type") == "ping":
            await self._ws.send_text(encode_json({"type": "pong"}))
            return
        logger.debug("scope: control message: %r", msg.get("type"))

    async def _sender(self) -> None:
        """Continuously dequeues and sends scope frames."""
        sent = 0
        while True:
            try:
                data = await asyncio.wait_for(
                    self._frame_queue.get(),
                    timeout=1.0,
                )
                self._observe("send_start", data)
                try:
                    await self._ws.send_binary(data)
                except asyncio.CancelledError:
                    self._observe("send_cancelled", data)
                    raise
                except Exception:
                    self._observe("send_error", data)
                    raise
                self._observe("send_complete", data)
                sent += 1
                if sent <= 3 or sent % 300 == 0:
                    logger.debug("scope: sent frame #%d (%d bytes)", sent, len(data))
            except TimeoutError:
                if sent == 0:
                    logger.debug(
                        "scope: sender timeout, no frames yet, qsize=%d",
                        self._frame_queue.qsize(),
                    )
            except Exception as exc:
                logger.warning("scope: sender error: %s", exc)
                break

    def push_frame(self, encoded: bytes) -> None:
        """Push a wire frame directly for testing/injection."""
        self.enqueue_frame(encoded)
