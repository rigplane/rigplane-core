"""Explicit subscriptions to the shared scope hub over WebSocket."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from rigplane.scope.hub import HubSubscription
from rigplane.ws import WS_OP_TEXT

from ..protocol import (  # noqa: TID251
    decode_json,
    decode_scope_subscription,
    encode_json,
    encode_scope_granted,
)
from ..transport import Connection  # noqa: TID251

if TYPE_CHECKING:
    from ..server import WebServer  # noqa: TID251

__all__ = ["ScopeHandler"]
logger = logging.getLogger(__name__)


class ScopeHandler:
    """Carry granted control messages followed by hub-owned v2 binary frames."""

    def __init__(self, ws: Connection, server: WebServer | None = None) -> None:
        self._ws = ws
        self._server = server
        self._subscription: HubSubscription | None = None
        self._sender_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        if self._server is not None:
            self._server.register_scope_handler(self)
        try:
            while True:
                opcode, payload = await self._ws.recv()
                if opcode == WS_OP_TEXT:
                    try:
                        await self._handle_control(payload.decode("utf-8"))
                    except UnicodeDecodeError:
                        continue
        except EOFError:
            pass
        finally:
            try:
                await self._unsubscribe()
            finally:
                if self._server is not None:
                    self._server.unregister_scope_handler(self)

    async def _unsubscribe(self) -> None:
        subscription, self._subscription = self._subscription, None
        try:
            if self._sender_task is not None:
                task, self._sender_task = self._sender_task, None
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        finally:
            if subscription is not None:
                await subscription.close()

    async def _handle_control(self, text: str) -> None:
        try:
            message = decode_json(text)
            kind = message.get("type")
            if kind == "ping":
                await self._ws.send_text(encode_json({"type": "pong"}))
            elif kind == "unsubscribe":
                await self._unsubscribe()
            elif kind == "subscribe":
                request = decode_scope_subscription(text)
                if self._server is None:
                    raise ValueError("scope source service is unavailable")
                if self._server._stopping:
                    raise RuntimeError("scope server is stopping")
                subscription = await self._server.scope_hub.subscribe(request)
                try:
                    await self._unsubscribe()
                except BaseException:
                    await subscription.close()
                    raise
                self._subscription = subscription
                await self._ws.send_text(encode_scope_granted(subscription.granted))
                self._sender_task = asyncio.create_task(self._sender(subscription))
        except Exception as exc:
            await self._ws.send_text(
                encode_json({"type": "error", "message": str(exc)})
            )

    async def _sender(self, subscription: HubSubscription) -> None:
        try:
            while True:
                await self._ws.send_binary(await subscription.receive())
        except StopAsyncIteration:
            pass
        except Exception:
            logger.debug("scope sender failed", exc_info=True)
            await self._ws.close()
