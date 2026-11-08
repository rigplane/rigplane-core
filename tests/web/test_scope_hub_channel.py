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


@pytest.mark.asyncio
async def test_two_clients_resample_and_pace_simulator_frames(virtual_clock, monkeypatch):
    from rigplane.scope import ScopeAssembler
    from rigplane.web.protocol import decode_scope_frame_v2, encode_scope_frame_v2
    from rigplane_testkit.icom import MockIcomRadio

    server = WebServer(None)
    writer = Writer()
    source = RadioScopeSource(resolve_radio_profile(model='ic7610'), writer)
    server.scope_hub.register('radio', source)
    encoded = []

    def encode(frame):
        encoded.append(frame)
        return encode_scope_frame_v2(frame)

    monkeypatch.setattr(server.scope_hub, '_encoder', encode)
    sockets = [Socket(), Socket()]
    handlers = [ScopeHandler(ws, server) for ws in sockets]
    tasks = [asyncio.create_task(handler.run()) for handler in handlers]
    sim = MockIcomRadio(scope_stream=True)
    assembler = ScopeAssembler()
    native = None
    for raw in sim._scope_templates[0]:
        native = assembler.feed(raw[7:-1], raw[6]) or native
    assert native is not None
    try:
        for ws, width, fps in zip(sockets, (1024, 2800), (10, 20), strict=True):
            await ws.incoming.put(encode_scope_subscription(Subscription(
                'radio', 0, View(native.start_freq_hz, native.end_freq_hz), width, fps,
            )))
        await await_until(lambda: all(ws.text for ws in sockets))
        for _ in range(30):
            source.feed_frame(native)
            await virtual_clock.elapse(1 / 30)
        assert 10 <= len(sockets[0].binary) <= 11
        assert 20 <= len(sockets[1].binary) <= 21
        assert len(encoded) == 60
        assert {frame.width for frame in encoded} == {1024, 2800}
        for ws, width in zip(sockets, (1024, 2800), strict=True):
            assert all(decode_scope_frame_v2(wire).width == width for wire in ws.binary)
    finally:
        for ws in sockets:
            await ws.incoming.put(None)
        await asyncio.gather(*tasks)
    assert writer.output == [True, False]


@pytest.mark.asyncio
async def test_real_websockets_negotiate_independent_views_and_audio_selection():
    import json
    from unittest.mock import AsyncMock
    import aiohttp
    from rigplane.scope import ScopeAssembler
    from rigplane.web.protocol import decode_scope_frame_v2
    from rigplane.web.server import WebConfig
    from rigplane_testkit.icom import MockIcomRadio
    from test_web_server import _addr

    server = WebServer(None, WebConfig(host='127.0.0.1', port=0))
    sim = MockIcomRadio(scope_stream=True)
    assembler = ScopeAssembler()
    native = None
    for raw in sim._scope_templates[0]:
        native = assembler.feed(raw[7:-1], raw[6]) or native
    assert native is not None
    radio = RadioScopeSource(resolve_radio_profile(model='ic7610'), Writer())
    server.scope_hub.register('radio', radio)
    await server.start()
    try:
        host, port = _addr(server)
        async with aiohttp.ClientSession() as client:
            a = await client.ws_connect(f'http://{host}:{port}/api/v1/scope')
            b = await client.ws_connect(f'http://{host}:{port}/api/v1/scope')
            try:
                assert server._radio_scope_demand is False
                for ws, width, fps in ((a, 1024, 10), (b, 2800, 20)):
                    await ws.send_str(encode_scope_subscription(Subscription('radio', 0, View(native.start_freq_hz, native.end_freq_hz), width, fps)))
                    grant = await ws.receive_json(timeout=5)
                    assert grant['type'] == 'granted', grant and grant['width'] == width
                sim = MockIcomRadio(scope_stream=True)
                assembler = ScopeAssembler()
                for raw in sim._scope_templates[0]:
                    frame = assembler.feed(raw[7:-1], raw[6])
                    if frame is not None:
                        radio.feed_frame(frame)
                for ws, width in ((a, 1024), (b, 2800)):
                    message = await ws.receive(timeout=5)
                    assert message.type == aiohttp.WSMsgType.BINARY
                    decoded = decode_scope_frame_v2(message.data)
                    assert decoded.width == width
                    assert decoded.pixels is not None and len(decoded.pixels) == width
                await a.send_json({'type': 'subscribe', 'sourceId': 'missing', 'receiver': None, 'view': {'startHz': 1, 'endHz': 2}, 'width': 5, 'maxFps': 10})
                assert (await a.receive_json(timeout=5))['type'] == 'error'
                from test_handlers_coverage import _RelayRadio
                from test_audio_scope_dispatch import _seed_audio_facts
                from rigplane.audio.bus import STAGE_RX_POST_DSP
                from rigplane.audio.scope_source import AudioFftSource
                from rigplane.web.scope_sources import WebScopePcmTap
                from rigplane.web.handlers.audio import AudioBroadcaster
                import numpy as np
                server._audio_broadcaster = AudioBroadcaster(_RelayRadio())
                _seed_audio_facts(server)
                tap = WebScopePcmTap(server)
                server.scope_hub.register('audio_fft', AudioFftSource(tap, tap.facts, fft_size=2048))
                await a.send_str(encode_scope_subscription(Subscription('audio_fft', None, View(14_074_000, 14_076_400), 1024, 20)))
                assert (await a.receive_json(timeout=5))['sourceId'] == 'audio_fft'
                server._audio_broadcaster.taps(STAGE_RX_POST_DSP).feed(np.ones(8192, dtype=np.int16).tobytes())
                audio = await a.receive(timeout=5)
                assert decode_scope_frame_v2(audio.data).source == 'audio_fft'
                await a.send_json({'type': 'unsubscribe'})
                await await_until(lambda: server._audio_broadcaster._subscription is None)
            finally:
                await a.close()
                await b.close()
    finally:
        await server.stop()


@pytest.mark.realtime(reason='real simulator UDP and two WebSocket clients')
@pytest.mark.timeout(30)
async def test_real_server_two_clients_simulator_pacing_and_last_viewer_output_off(monkeypatch):
    import aiohttp
    from rigplane.radio import IcomRadio
    from rigplane.scope import ScopeAssembler
    from rigplane.scope.models import Coverage
    from rigplane.scope.resample import max_hold
    from rigplane.web.protocol import decode_scope_frame_v2, encode_scope_frame_v2
    from rigplane.web.server import WebConfig
    from rigplane_testkit.icom import simulator as testkit
    from test_web_server import _addr

    monkeypatch.setattr(testkit, '_SCOPE_GAP_S', {key: 1 / 30 for key in testkit._SCOPE_GAP_S})
    sim = testkit.MockIcomRadio(scope_stream=True)
    assembler = ScopeAssembler()
    native = None
    for raw in sim._scope_templates[0]:
        native = assembler.feed(raw[7:-1], raw[6]) or native
    assert native is not None
    sim.set_frequency((native.start_freq_hz + native.end_freq_hz) // 2)
    await sim.start()
    radio = IcomRadio('127.0.0.1', port=sim.control_port, username='testuser', password='testpass', model='IC-7610')
    server = None
    try:
        await radio.connect()
        server = WebServer(radio, WebConfig(host='127.0.0.1', port=0))
        await server.start()
        hub = server.scope_hub
        encoded = []
        native_count = []

        def encode(frame):
            encoded.append(frame)
            return encode_scope_frame_v2(frame)

        monkeypatch.setattr(hub, '_encoder', encode)
        source = server._radio_scope_source
        assert source is not None
        feed = source.feed_frame

        def count(frame):
            native_count.append(frame)
            feed(frame)

        monkeypatch.setattr(source, 'feed_frame', count)
        host, port = _addr(server)
        received = [[], []]
        async with aiohttp.ClientSession() as client:
            sockets = [await client.ws_connect(f'http://{host}:{port}/api/v1/scope') for _ in range(2)]
            readers = []
            try:
                assert not server._scope_enabled
                for ws, width, fps in zip(sockets, (1024, 2800), (10, 20), strict=True):
                    await ws.send_str(encode_scope_subscription(Subscription('radio', 0, View(native.start_freq_hz, native.end_freq_hz), width, fps)))
                    grant = await ws.receive_json(timeout=5)
                    assert grant['type'] == 'granted', grant

                async def receive(index, ws):
                    async for message in ws:
                        if message.type == aiohttp.WSMsgType.BINARY:
                            received[index].append(decode_scope_frame_v2(message.data))

                readers = [asyncio.create_task(receive(index, ws)) for index, ws in enumerate(sockets)]
                await await_until(lambda: all(received), timeout=5)
                start_seq = encoded[-1].seq
                start_count = len(native_count)
                start_encode = len(encoded)
                started = asyncio.get_running_loop().time()
                await await_until(lambda: len(native_count) >= start_count + 60, timeout=5)
                elapsed = asyncio.get_running_loop().time() - started
                end_seq = encoded[-1].seq
                assert len(encoded) - start_encode == 2 * (len(native_count) - start_count)
                assert {frame.width for frame in encoded[start_encode:]} == {1024, 2800}
                await await_until(lambda: all(frames and frames[-1].seq >= end_seq - 3 for frames in received), timeout=2)
                for frames, width, fps in zip(received, (1024, 2800), (10, 20), strict=True):
                    sample = [frame for frame in frames if start_seq < frame.seq <= end_seq]
                    assert abs(len(sample) / elapsed - fps) <= 1.5
                    assert all(frame.pixels is not None and len(frame.pixels) == width for frame in sample)
                    expected = max_hold(native.pixels, Coverage(native.start_freq_hz, native.end_freq_hz), View(native.start_freq_hz, native.end_freq_hz), width)
                    assert all(frame.pixels == expected for frame in sample)
                assert sim._scope_11 == 1
            finally:
                for ws in sockets:
                    await ws.close()
                await asyncio.gather(*readers, return_exceptions=True)
            await await_until(lambda: not server._scope_enabled, timeout=5)
            assert sim._scope_11 == 0
    finally:
        if server is not None:
            await server.stop()
        await radio.disconnect()
        await sim.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('out_of_range,pixels', [(True, b'\x01'), (False, b''), (False, b'\x00')])
async def test_typed_oor_and_uncovered_arrivals_never_publish_hidden(virtual_clock, monkeypatch, out_of_range, pixels):
    from types import SimpleNamespace
    from rigplane.web.protocol import decode_scope_frame_v2
    source = RadioScopeSource(resolve_radio_profile(model='ic705'), Writer())
    server = WebServer(None)
    server.scope_hub.register('radio', source)
    server._radio_scope_source = source
    server._radio = SimpleNamespace(last_valid_frame_monotonic=virtual_clock())
    server._hardware_scope_available = True
    server._radio_scope_demand = True
    server._scope_enabled = True
    server._scope_health_interval = .1
    monkeypatch.setattr(server, '_radio_ready', lambda: True)
    monkeypatch.setattr(server, '_scope_tx_known', lambda: False)
    sub = await server.scope_hub.subscribe(Subscription('radio', 0, View(20_000_000, 20_100_000), 1024, 20))
    task = asyncio.create_task(server._scope_health_monitor())
    try:
        for _ in range(3):
            source.feed_frame(ScopeFrame(0, 1, 14_000_000, 14_100_000, pixels, out_of_range))
            decoded = decode_scope_frame_v2(await sub.receive())
            assert decoded.out_of_range is out_of_range
            assert decoded.pixels is None
            assert decoded.coverage_start_hz == decoded.coverage_end_hz
            await virtual_clock.elapse(.1)
        assert server._scope_hidden_published is False
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await sub.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['IC-7300', 'IC-7610'])
async def test_writer_maps_profile_span_index_and_never_retunes_off_vfo_view(model, monkeypatch):
    from rigplane.web.radio_poller import SetScopeMode, SetScopeSpan, SwitchScopeReceiver
    from rigplane.radio_state import RadioState
    from rigplane.web.server import WebConfig
    from rigplane.web.scope_sources import WebRadioScopeWriter
    server = WebServer(None, WebConfig(radio_model=model))
    server._radio_state = RadioState()
    server._radio_state.main.freq = 14_100_000
    writer = WebRadioScopeWriter(server)
    source = RadioScopeSource(resolve_radio_profile(model=model), writer)
    commands = []

    def execute(command, *, future):
        commands.append(command)
        future.set_result(None)

    monkeypatch.setattr(server.command_queue, 'put_ordered', execute)
    requested = View(14_180_000, 14_200_000)
    grant = await source.set_view(requested, 0)
    assert grant.view == requested
    assert grant.coverage is not None
    assert (grant.coverage.start_hz + grant.coverage.end_hz) / 2 == 14_100_000
    span = grant.coverage.end_hz - grant.coverage.start_hz
    index = server._get_profile().scope_span_presets_hz.index(span)
    assert commands == [SwitchScopeReceiver(0), SetScopeMode(0), SetScopeSpan(index)]
    assert server._radio_state.main.freq == 14_100_000


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['radio', 'audio_fft'])
async def test_subscription_updates_keep_demand_and_invalid_request_keeps_previous(kind):
    from rigplane.audio.scope_source import AudioFftSource, AudioPassband
    server = WebServer(None)
    writer = Writer()
    receiver = 0
    if kind == 'radio':
        source = RadioScopeSource(resolve_radio_profile(model='ic705'), writer)
    else:
        class Tap:
            async def acquire(self, callback):
                writer.output.append(True)

            async def release(self):
                writer.output.append(False)

        source = AudioFftSource(Tap(), lambda: AudioPassband(14_100_000, 0, 2400, 'upper'))
        receiver = None
    server.scope_hub.register('selected', source)
    ws = Socket()
    handler = ScopeHandler(ws, server)
    try:
        first = Subscription('selected', receiver, View(14_100_000, 14_102_400), 1024, 10)
        await handler._handle_control(encode_scope_subscription(first))
        previous = handler._subscription
        await handler._handle_control(encode_scope_subscription(Subscription('selected', 9, first.view, 1024, 10)))
        assert handler._subscription is previous
        assert writer.output == [True]
        await handler._handle_control(encode_scope_subscription(Subscription('selected', receiver, first.view, 2800, 20)))
        assert handler._subscription is not previous
        assert handler._subscription.granted.width == 2800
        assert writer.output == [True]
    finally:
        await handler._unsubscribe()
    assert writer.output == [True, False]


@pytest.mark.asyncio
async def test_server_drains_scope_before_stopping_command_executor(monkeypatch):
    from rigplane.web import web_startup
    server = WebServer(None)
    writer = Writer()
    server.scope_hub.register('radio', RadioScopeSource(resolve_radio_profile(model='ic705'), writer))
    handler = ScopeHandler(Socket(), server)
    server.register_scope_handler(handler)
    await handler._handle_control(encode_scope_subscription(Subscription('radio', 0, View(14_090_000, 14_110_000), 1024, 10)))

    async def stop_executor(received):
        assert received is server
        assert writer.output == [True, False]
        assert handler._subscription is None

    monkeypatch.setattr(web_startup, 'stop_web_server', stop_executor)
    try:
        await server.stop()
    finally:
        await handler._unsubscribe()
