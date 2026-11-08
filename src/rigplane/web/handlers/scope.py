"""Explicit subscriptions to the shared scope hub over WebSocket."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rigplane.scope.hub import HubSubscription
from rigplane.scope.models import ScopeTarget
from rigplane.ws import WS_OP_TEXT

from ..protocol import (  # noqa: TID251
    decode_json,
    decode_scope_subscription,
    decode_scope_target,
    decode_scope_unsubscribe,
    encode_json,
    encode_scope_granted,
)
from ..transport import Connection  # noqa: TID251

if TYPE_CHECKING:
    from ..server import WebServer  # noqa: TID251

__all__ = ["ScopeHandler"]
logger = logging.getLogger(__name__)


@dataclass
class _Stream:
    subscription: HubSubscription
    sender: asyncio.Task[None] | None = None


class ScopeHandler:
    """Carry granted control messages followed by hub-owned v2 binary frames."""

    def __init__(self, ws: Connection, server: WebServer | None = None) -> None:
        self._ws = ws
        self._server = server
        self._streams: dict[ScopeTarget, _Stream] = {}

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

    async def _unsubscribe(self, target: ScopeTarget | None = None) -> None:
        streams = (
            ([self._streams.pop(target)] if target in self._streams else [])
            if target is not None
            else list(self._streams.values())
        )
        if target is None:
            self._streams.clear()
        failure: BaseException | None = None
        for stream in streams:
            try:
                try:
                    if stream.sender is not None:
                        stream.sender.cancel()
                        try:
                            await stream.sender
                        except asyncio.CancelledError:
                            pass
                finally:
                    await stream.subscription.close()
            except BaseException as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure

    async def _handle_control(self, text: str) -> None:
        target: ScopeTarget | None = None
        try:
            message = decode_json(text)
            kind = message.get("type")
            if kind == "ping":
                await self._ws.send_text(encode_json({"type": "pong"}))
            elif kind == "unsubscribe":
                try:
                    target = decode_scope_target(text)
                except (KeyError, ValueError):
                    pass
                target = decode_scope_unsubscribe(text)
                await self._unsubscribe(target)
            elif kind == "subscribe":
                try:
                    target = decode_scope_target(text)
                except (KeyError, ValueError):
                    pass
                request = decode_scope_subscription(text)
                target = ScopeTarget(request.source_id, request.receiver)
                if self._server is None:
                    raise ValueError("scope source service is unavailable")
                if self._server._stopping:
                    raise RuntimeError("scope server is stopping")
                hub = self._server.scope_hub
                if request.source_id == "iq_fft":
                    self._server._register_iq_scope_source(hub)
                subscription = await hub.subscribe(request)
                try:
                    await self._unsubscribe(target)
                except BaseException:
                    await subscription.close()
                    raise
                stream = _Stream(subscription)
                self._streams[target] = stream
                grant = subscription.granted
                try:
                    await self._ws.send_text(encode_scope_granted(grant))
                except BaseException:
                    await self._unsubscribe(target)
                    raise
                stream.sender = asyncio.create_task(
                    self._sender(subscription, grant.width)
                )
        except Exception as exc:
            error: dict[str, object] = {"type": "error", "message": str(exc)}
            if target is not None:
                error.update(sourceId=target.source_id, receiver=target.receiver)
            await self._ws.send_text(encode_json(error))

    async def _sender(self, subscription: HubSubscription, sent_width: int) -> None:
        try:
            while True:
                grant, data = await subscription.receive_delivery()
                if grant.width != sent_width:
                    await self._ws.send_text(encode_scope_granted(grant))
                    sent_width = grant.width
                await self._ws.send_binary(data)
        except StopAsyncIteration:
            pass
        except Exception:
            logger.debug("scope sender failed", exc_info=True)
            await self._ws.close()
