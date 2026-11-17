"""Explicit subscriptions to the shared scope hub over WebSocket."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from rigplane.scope.hub import HubSubscription
from rigplane.scope.models import GrantedSubscription, ScopeTarget
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
        # MOR-4532, per connection: the last requestSeq whose follow width
        # was applied, and the highest one sent as settled. A fresh
        # connection starts at 0, which is never a request.
        self._applied_seq = 0
        self._settled_seq = 0

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
                # Only a NEW requestSeq applies its follow width; a re-sent
                # subscription (resize, visibility, fps) and requestSeq 0
                # carry the width as context. No requestSeq: as before.
                applies = (
                    request.request_seq is None
                    or request.request_seq > self._applied_seq
                )
                subscription = await hub.subscribe(request, apply_width=applies)
                if applies and request.request_seq is not None:
                    self._applied_seq = request.request_seq
                try:
                    await self._unsubscribe(target)
                except BaseException:
                    await subscription.close()
                    raise
                stream = _Stream(subscription)
                self._streams[target] = stream
                grant = self._final(subscription.granted)
                try:
                    await self._ws.send_text(encode_scope_granted(grant))
                except BaseException:
                    await self._unsubscribe(target)
                    raise
                stream.sender = asyncio.create_task(self._sender(subscription, grant))
        except Exception as exc:
            error: dict[str, object] = {"type": "error", "message": str(exc)}
            if target is not None:
                error.update(sourceId=target.source_id, receiver=target.receiver)
            await self._ws.send_text(encode_json(error))

    def _final(self, grant: GrantedSubscription) -> GrantedSubscription:
        """Settled is final per requestSeq on this connection (MOR-4532).

        Every grant this connection sends passes here: a requestSeq at or
        below the highest one already sent as settled (0 included) is sent
        settled, so a delivery buffered before the request settled, or a
        re-sent subscription, can never send it back to pending.
        """
        if grant.request_seq is None:
            return grant
        if grant.request_seq <= self._settled_seq:
            return replace(grant, request_state="settled")
        if grant.request_state == "settled":
            self._settled_seq = grant.request_seq
        return grant

    async def _sender(
        self, subscription: HubSubscription, sent: GrantedSubscription
    ) -> None:
        try:
            while True:
                grant, data = await subscription.receive_delivery()
                if (
                    grant.request_state == "pending"
                    and subscription.granted.request_state == "settled"
                ):
                    # Buffered before this request settled; the hub already
                    # offered the settled frame behind it. Retire it rather
                    # than send its pre-request geometry under a settled grant.
                    continue
                grant = self._final(grant)
                # A regrant on a width change, and when the request settles
                # (MOR-4532: the re-sent grant is the only "settled" signal).
                if (grant.width, grant.request_state) != (
                    sent.width,
                    sent.request_state,
                ):
                    await self._ws.send_text(encode_scope_granted(grant))
                    sent = grant
                await self._ws.send_binary(data)
        except StopAsyncIteration:
            pass
        except Exception:
            logger.debug("scope sender failed", exc_info=True)
            await self._ws.close()
