"""Explicit scope subscriptions on the shared v2 WebSocket channel."""

import asyncio

import pytest

from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.radio_scope_source import RadioScopeSource
from rigplane.scope import ScopeFrame
from rigplane.scope.models import Subscription, View
from rigplane.testing.timing import await_until
from rigplane.web.api_contract import STABLE_WEBSOCKET_ROUTES
from rigplane.web.handlers.scope import ScopeHandler
from rigplane.web.protocol import decode_scope_granted, encode_scope_subscription
from rigplane.web.server import WebServer
from rigplane.ws import WS_OP_TEXT


class Writer:
    def __init__(self):
        self.output = []

    def tuned_frequency(self, receiver):
        return 14_100_000

    async def set_output(self, enabled):
        self.output.append(enabled)

    async def set_center(self, center_hz, span_hz, receiver):
        pass


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.text = []
        self.binary = []

    async def recv(self):
        message = await self.incoming.get()
        if message is None:
            raise EOFError
        return WS_OP_TEXT, message.encode()

    async def send_text(self, text):
        self.text.append(text)

    async def send_binary(self, data):
        self.binary.append(data)


@pytest.mark.asyncio
async def test_explicit_subscription_granted_before_v2_and_last_close_stops():
    server = WebServer(None)
    writer = Writer()
    source = RadioScopeSource(resolve_radio_profile(model='ic705'), writer)
    server.scope_hub.register('test-radio', source)
    ws = Socket()
    handler = ScopeHandler(ws, server)
    task = asyncio.create_task(handler.run())
    try:
        await await_until(lambda: handler in server._scope_handlers)
        assert writer.output == []
        await ws.incoming.put(encode_scope_subscription(Subscription(
            'test-radio', 0, View(14_090_000, 14_110_000), 1024, 10,
        )))
        await await_until(lambda: bool(ws.text))
        grant = decode_scope_granted(ws.text[0])
        assert grant.source_id == 'test-radio'
        assert grant.width == 1024
        source.feed_frame(ScopeFrame(0, 0, 14_090_000, 14_110_000, bytes(range(160)), False))
        await await_until(lambda: bool(ws.binary))
        assert ws.binary[0][0] == 2
        assert len(ws.binary[0]) == 56 + len('test-radio') + 1024
        assert writer.output == [True]
    finally:
        await ws.incoming.put(None)
        await task
    assert writer.output == [True, False]


def test_audio_scope_route_and_legacy_dispatch_removed():
    assert '/api/v1/audio-scope' not in {route['path'] for route in STABLE_WEBSOCKET_ROUTES}
    for name in ('_broadcast_scope', '_broadcast_audio_scope', '_dispatch_audio_fft_frame', '_on_hardware_scope_frame'):
        assert not hasattr(WebServer, name)
