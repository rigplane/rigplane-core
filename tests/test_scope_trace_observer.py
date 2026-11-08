"""Scope sender errors close the connection and do not retain demand."""

from unittest.mock import AsyncMock

from rigplane.scope.hub import HubSubscription
from rigplane.scope.models import GrantedSubscription, View
from rigplane.web.handlers.scope import ScopeHandler


async def test_sender_closes_failed_connection():
    ws = AsyncMock()
    ws.send_binary.side_effect = OSError("closed")
    subscription = AsyncMock(spec=HubSubscription)
    grant = GrantedSubscription("radio", None, View(14_000_000, 14_100_000), None, 2, 30)
    subscription.receive_delivery.return_value = (grant, b"frame")
    handler = ScopeHandler(ws)
    await handler._sender(subscription, grant.width)
    ws.close.assert_awaited_once()
    assert not hasattr(handler, "_frame_queue")
    assert not hasattr(handler, "_trace_observer")
