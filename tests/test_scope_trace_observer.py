"""Scope sender errors close the connection and do not retain demand."""

from unittest.mock import AsyncMock

from rigplane.scope.hub import HubSubscription
from rigplane.web.handlers.scope import ScopeHandler


async def test_sender_closes_failed_connection():
    ws = AsyncMock()
    ws.send_binary.side_effect = OSError("closed")
    subscription = AsyncMock(spec=HubSubscription)
    subscription.receive.return_value = b"frame"
    handler = ScopeHandler(ws)
    await handler._sender(subscription)
    ws.close.assert_awaited_once()
    assert not hasattr(handler, "_frame_queue")
    assert not hasattr(handler, "_trace_observer")
