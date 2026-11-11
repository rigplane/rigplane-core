"""Explicit scope subscriptions on the shared v2 WebSocket channel."""

import asyncio

import pytest

from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.radio_scope_source import RadioScopeSource
from rigplane.scope import ScopeFrame
from rigplane.scope.models import Coverage, ScopeTarget, Subscription, View
from rigplane.testing.timing import await_until
from rigplane.web.api_contract import STABLE_WEBSOCKET_ROUTES
from rigplane.web.handlers.scope import ScopeHandler
from rigplane.web.protocol import decode_scope_granted, encode_scope_subscription
from rigplane.web.server import WebServer
from rigplane.ws import WS_OP_TEXT


class Writer:
    # MOR-4392 round 2: no observed mode on this fake — the source
    # drives the radio as before.
    async def observed_scope_mode(self, receiver):
        return None

    def __init__(self):
        self.output = []

    async def set_fixed(self, edges, receiver):
        pass

    def on_frame(self, frame):
        pass

    def tuned_frequency(self, receiver):
        return 14_100_000

    async def set_output(self, enabled, *, release=True):
        self.output.append(enabled)

    async def set_center(self, center_hz, span_hz, receiver):
        pass


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.text = []
        self.binary = []
        self.events = []

    async def recv(self):
        message = await self.incoming.get()
        if message is None:
            raise EOFError
        return WS_OP_TEXT, message.encode()

    async def send_text(self, text):
        self.text.append(text)
        self.events.append(("text", text))

    async def send_binary(self, data):
        self.binary.append(data)
        self.events.append(("binary", data))


@pytest.mark.asyncio
async def test_explicit_subscription_granted_before_v2_and_last_close_stops():
    server = WebServer(None)
    writer = Writer()
    source = RadioScopeSource(resolve_radio_profile(model="ic705"), writer)
    server.scope_hub.register("test-radio", source)
    ws = Socket()
    handler = ScopeHandler(ws, server)
    task = asyncio.create_task(handler.run())
    try:
        await await_until(lambda: handler in server._scope_handlers)
        assert writer.output == []
        await ws.incoming.put(
            encode_scope_subscription(
                Subscription(
                    "test-radio",
                    0,
                    View(14_090_000, 14_110_000),
                    1024,
                    10,
                )
            )
        )
        await await_until(lambda: bool(ws.text))
        grant = decode_scope_granted(ws.text[0])
        assert grant.source_id == "test-radio"
        assert grant.width == 1024
        source.feed_frame(
            ScopeFrame(0, 0, 14_090_000, 14_110_000, bytes(range(160)), False)
        )
        await await_until(lambda: bool(ws.binary))
        assert ws.binary[0][0] == 2
        assert len(ws.binary[0]) == 56 + len("test-radio") + 160
        assert writer.output == [True]
    finally:
        await ws.incoming.put(None)
        await task
    assert writer.output == [True, False]


def test_audio_scope_route_and_legacy_dispatch_removed():
    assert "/api/v1/audio-scope" not in {
        route["path"] for route in STABLE_WEBSOCKET_ROUTES
    }
    for name in (
        "_broadcast_scope",
        "_broadcast_audio_scope",
        "_dispatch_audio_fft_frame",
        "_on_hardware_scope_frame",
    ):
        assert not hasattr(WebServer, name)


@pytest.mark.asyncio
async def test_two_clients_resample_and_pace_simulator_frames(
    virtual_clock, monkeypatch
):
    from rigplane.scope import ScopeAssembler
    from rigplane.web.protocol import decode_scope_frame_v2, encode_scope_frame_v2
    from rigplane_testkit.icom.scope_control import (
        ScopeControlSimulator as MockIcomRadio,
    )

    server = WebServer(None)
    writer = Writer()
    source = RadioScopeSource(resolve_radio_profile(model="ic7610"), writer)
    server.scope_hub.register("radio", source)
    encoded = []

    def encode(frame):
        encoded.append(frame)
        return encode_scope_frame_v2(frame)

    monkeypatch.setattr(server.scope_hub, "_encoder", encode)
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
            await ws.incoming.put(
                encode_scope_subscription(
                    Subscription(
                        "radio",
                        0,
                        View(native.start_freq_hz, native.end_freq_hz),
                        width,
                        fps,
                    )
                )
            )
        await await_until(lambda: all(ws.text for ws in sockets))
        for _ in range(30):
            source.feed_frame(native)
            await virtual_clock.elapse(1 / 30)
        assert 10 <= len(sockets[0].binary) <= 11
        assert 20 <= len(sockets[1].binary) <= 21
        # Both requests exceed the observed native width and share one encode.
        assert len(encoded) == 30
        assert {frame.width for frame in encoded} == {len(native.pixels)}
        for ws in sockets:
            assert all(
                decode_scope_frame_v2(wire).width == len(native.pixels)
                for wire in ws.binary
            )
    finally:
        for ws in sockets:
            await ws.incoming.put(None)
        await asyncio.gather(*tasks)
    assert writer.output == [True, False]


@pytest.mark.asyncio
async def test_real_websockets_negotiate_independent_views_and_audio_selection():
    import aiohttp
    from rigplane.scope import ScopeAssembler
    from rigplane.web.protocol import decode_scope_frame_v2
    from rigplane.web.server import WebConfig
    from rigplane_testkit.icom.scope_control import (
        ScopeControlSimulator as MockIcomRadio,
    )
    from test_web_server import _addr

    server = WebServer(None, WebConfig(host="127.0.0.1", port=0))
    sim = MockIcomRadio(scope_stream=True)
    assembler = ScopeAssembler()
    native = None
    for raw in sim._scope_templates[0]:
        native = assembler.feed(raw[7:-1], raw[6]) or native
    assert native is not None
    radio = RadioScopeSource(resolve_radio_profile(model="ic7610"), Writer())
    server.scope_hub.register("radio", radio)
    await server.start()
    try:
        host, port = _addr(server)
        async with aiohttp.ClientSession() as client:
            a = await client.ws_connect(f"http://{host}:{port}/api/v1/scope")
            b = await client.ws_connect(f"http://{host}:{port}/api/v1/scope")
            try:
                assert (
                    server._radio_scope_session is None
                    or server._radio_scope_session.active is False
                )
                for ws, width, fps in ((a, 1024, 10), (b, 2800, 20)):
                    await ws.send_str(
                        encode_scope_subscription(
                            Subscription(
                                "radio",
                                0,
                                View(native.start_freq_hz, native.end_freq_hz),
                                width,
                                fps,
                            )
                        )
                    )
                    grant = await ws.receive_json(timeout=5)
                    assert grant["type"] == "granted", grant
                    assert grant["width"] == width
                sim = MockIcomRadio(scope_stream=True)
                assembler = ScopeAssembler()
                for raw in sim._scope_templates[0]:
                    frame = assembler.feed(raw[7:-1], raw[6])
                    if frame is not None:
                        radio.feed_frame(frame)
                for ws in (a, b):
                    updated = await ws.receive_json(timeout=5)
                    assert updated["width"] == len(native.pixels)
                    message = await ws.receive(timeout=5)
                    assert message.type == aiohttp.WSMsgType.BINARY
                    decoded = decode_scope_frame_v2(message.data)
                    assert decoded.width == len(native.pixels)
                    assert decoded.pixels is not None and len(decoded.pixels) == len(
                        native.pixels
                    )
                await a.send_json(
                    {
                        "type": "subscribe",
                        "sourceId": "missing",
                        "receiver": None,
                        "view": {"startHz": 1, "endHz": 2},
                        "width": 5,
                        "maxFps": 10,
                    }
                )
                assert (await a.receive_json(timeout=5))["type"] == "error"
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
                server.scope_hub.register(
                    "audio_fft", AudioFftSource(tap, tap.facts, fft_size=2048)
                )
                await a.send_str(
                    encode_scope_subscription(
                        Subscription(
                            "audio_fft", None, View(14_074_000, 14_076_400), 1024, 20
                        )
                    )
                )
                assert (await a.receive_json(timeout=5))["sourceId"] == "audio_fft"
                server._audio_broadcaster.taps(STAGE_RX_POST_DSP).feed(
                    np.ones(8192, dtype=np.int16).tobytes()
                )
                updated = await a.receive_json(timeout=5)
                assert updated["type"] == "granted" and updated["width"] == 103
                audio = await a.receive(timeout=5)
                assert decode_scope_frame_v2(audio.data).source == "audio_fft"
                await a.send_json({"type": "unsubscribe"})
                await await_until(
                    lambda: server._audio_broadcaster._subscription is None
                )
            finally:
                await a.close()
                await b.close()
    finally:
        await server.stop()


@pytest.mark.realtime(reason="real simulator UDP and two WebSocket clients")
@pytest.mark.timeout(30)
async def test_real_server_two_clients_simulator_pacing_and_last_viewer_output_off(
    monkeypatch,
):
    import aiohttp
    from rigplane.radio import IcomRadio
    from rigplane.scope import ScopeAssembler
    from rigplane.scope.models import Coverage
    from rigplane.scope.resample import max_hold
    from rigplane.web.protocol import decode_scope_frame_v2, encode_scope_frame_v2
    from rigplane.web.server import WebConfig
    from rigplane_testkit.icom import simulator as testkit
    from test_web_server import _addr

    monkeypatch.setattr(
        testkit, "_SCOPE_GAP_S", {key: 1 / 30 for key in testkit._SCOPE_GAP_S}
    )
    from rigplane_testkit.icom.scope_control import ScopeControlSimulator

    sim = ScopeControlSimulator(scope_stream=True)
    assembler = ScopeAssembler()
    native = None
    for raw in sim._scope_templates[0]:
        native = assembler.feed(raw[7:-1], raw[6]) or native
    assert native is not None
    sim.set_frequency((native.start_freq_hz + native.end_freq_hz) // 2)
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    server = None
    try:
        await radio.connect()
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        await server.start()
        hub = server.scope_hub
        encoded = []
        native_count = []

        def encode(frame):
            encoded.append(frame)
            return encode_scope_frame_v2(frame)

        monkeypatch.setattr(hub, "_encoder", encode)
        source = server._radio_scope_source
        assert source is not None
        feed = source.feed_frame

        def count(frame):
            native_count.append(frame)
            feed(frame)

        monkeypatch.setattr(source, "feed_frame", count)
        server._set_scope_data_callback(count)
        host, port = _addr(server)
        received = [[], []]
        async with aiohttp.ClientSession() as client:
            sockets = [
                await client.ws_connect(f"http://{host}:{port}/api/v1/scope")
                for _ in range(2)
            ]
            readers = []
            try:
                assert not server._radio_scope_session.enabled
                for ws, width, fps in zip(sockets, (1024, 2800), (10, 20), strict=True):
                    await ws.send_str(
                        encode_scope_subscription(
                            Subscription(
                                "radio",
                                0,
                                View(native.start_freq_hz, native.end_freq_hz),
                                width,
                                fps,
                            )
                        )
                    )
                    grant = await ws.receive_json(timeout=5)
                    assert grant["type"] == "granted", grant

                async def receive(index, ws):
                    async for message in ws:
                        if message.type == aiohttp.WSMsgType.BINARY:
                            received[index].append(decode_scope_frame_v2(message.data))

                readers = [
                    asyncio.create_task(receive(index, ws))
                    for index, ws in enumerate(sockets)
                ]
                await await_until(lambda: all(received), timeout=5)
                start_seq = encoded[-1].seq
                start_count = len(native_count)
                start_encode = len(encoded)
                started = asyncio.get_running_loop().time()
                await await_until(
                    lambda: len(native_count) >= start_count + 60, timeout=5
                )
                elapsed = asyncio.get_running_loop().time() - started
                end_seq = encoded[-1].seq
                # Clients share one encode at the observed native width.
                assert len(encoded) - start_encode == (len(native_count) - start_count)
                assert {frame.width for frame in encoded[start_encode:]} == {
                    len(native.pixels)
                }
                await await_until(
                    lambda: all(
                        frames and frames[-1].seq >= end_seq - 3 for frames in received
                    ),
                    timeout=2,
                )
                for frames, fps in zip(received, (10, 20), strict=True):
                    sample = [
                        frame for frame in frames if start_seq < frame.seq <= end_seq
                    ]
                    assert abs(len(sample) / elapsed - fps) <= 1.5
                    assert all(
                        frame.pixels is not None
                        and len(frame.pixels) == len(native.pixels)
                        for frame in sample
                    )
                    expected = max_hold(
                        native.pixels,
                        Coverage(native.start_freq_hz, native.end_freq_hz),
                        View(native.start_freq_hz, native.end_freq_hz),
                        len(native.pixels),
                    )
                    assert all(frame.pixels == expected for frame in sample)
                assert sim._scope_11 == 1
            finally:
                for ws in sockets:
                    await ws.close()
                await asyncio.gather(*readers, return_exceptions=True)
            await await_until(
                lambda: not server._radio_scope_session.enabled, timeout=5
            )
            assert sim._scope_11 == 0
    finally:
        if server is not None:
            await server.stop()
        await radio.disconnect()
        await sim.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "out_of_range,pixels", [(True, b"\x01"), (False, b""), (False, b"\x00")]
)
async def test_typed_oor_and_uncovered_arrivals_never_publish_hidden(
    virtual_clock, monkeypatch, out_of_range, pixels
):
    from rigplane.radio import IcomRadio
    from rigplane.web.protocol import decode_scope_frame_v2

    from test_web_server_coverage import _scope_radio
    from _scope_session_fake import attach_scope_session

    radio = _scope_radio()
    radio.profile = resolve_radio_profile(model="ic705")
    session = attach_scope_session(radio, clock=virtual_clock)
    source = RadioScopeSource(radio.profile, session)
    server = WebServer(None)
    server.scope_hub.register("radio", source, on_demand=session.set_demand)
    server._radio_scope_source = source
    server._radio_scope_session = session
    server._radio = radio
    radio.last_valid_frame_monotonic = virtual_clock()
    server._hardware_scope_available = True
    server._scope_health_interval = 0.1
    monkeypatch.setattr(server, "_radio_ready", lambda: True)
    monkeypatch.setattr(server, "_scope_tx_known", lambda: False)
    sub = await server.scope_hub.subscribe(
        Subscription("radio", 0, View(20_000_000, 20_100_000), 1024, 20)
    )
    publisher = IcomRadio("127.0.0.1", model="IC-705")
    publisher._control_scope_session = session
    publisher.on_scope_data(source.feed_frame)
    task = asyncio.create_task(server._scope_health_monitor())
    try:
        for _ in range(6):
            publisher._civ_runtime._publish_scope_frame(
                ScopeFrame(0, 1, 14_000_000, 14_100_000, pixels, out_of_range)
            )
            decoded = decode_scope_frame_v2(await sub.receive())
            assert decoded.out_of_range is out_of_range
            assert decoded.pixels is None
            assert decoded.coverage_start_hz == decoded.coverage_end_hz
            assert session.health(civ_flowing=True).last_frame_at == virtual_clock()
            server._radio.last_valid_frame_monotonic = virtual_clock()
            await virtual_clock.elapse(0.05)
        assert server._scope_hidden_published is False
        for _ in range(3):
            server._radio.last_valid_frame_monotonic = virtual_clock()
            await virtual_clock.elapse(0.05)
        assert server._scope_hidden_published is True
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await sub.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300", "IC-7610"])
async def test_writer_maps_profile_span_index_and_never_retunes_off_vfo_view(
    model, monkeypatch
):
    from _scope_session_fake import attach_scope_session
    from test_web_server_coverage import _scope_radio

    radio = _scope_radio()
    radio.profile = resolve_radio_profile(model=model)
    writer = attach_scope_session(radio)
    source = RadioScopeSource(radio.profile, writer)
    from rigplane.core.types import ScopeFixedEdge

    requested = View(14_180_000, 14_200_000)
    grant = await source.set_view(requested, 0)
    assert grant.view == requested
    assert grant.coverage is not None
    if radio.profile.scope_fixed_edge_ranges:
        assert grant.coverage.start_hz == requested.start_hz
        assert grant.coverage.end_hz == requested.end_hz
        assert radio.scope_writes == [
            ("fixed_edge", ScopeFixedEdge(6, 1, 14180000, 14200000), 0),
            ("edge", 1, 0),
            ("mode", 1, 0),
        ]
    else:
        assert (grant.coverage.start_hz + grant.coverage.end_hz) / 2 == 14_100_000
        # MOR-4385: Icom presets are ±half-widths, so coverage is twice
        # the preset sent to the radio: a view reaching 100 kHz off center
        # gets the ±100 kHz preset and a 200 kHz wide grant.
        assert grant.coverage == Coverage(14_000_000, 14_200_000)
        preset_hz = (grant.coverage.end_hz - grant.coverage.start_hz) / 2
        index = radio.profile.scope_span_presets_hz.index(preset_hz)
        assert radio.scope_writes == [("mode", 0, 0), ("span", index, 0)]
    assert writer.tuned_frequency(0) == 14_100_000
    radio.scope_writes.clear()
    repeated = await source.set_view(requested, 0)
    assert repeated.coverage == grant.coverage
    assert radio.scope_writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,native_width", [("radio", 2800), ("audio_fft", 2800)])
async def test_subscription_updates_keep_demand_and_invalid_request_keeps_previous(
    kind,
    native_width,
):
    from rigplane.audio.scope_source import AudioFftSource, AudioPassband

    server = WebServer(None)
    writer = Writer()
    receiver = 0
    if kind == "radio":
        source = RadioScopeSource(resolve_radio_profile(model="ic705"), writer)
    else:

        class Tap:
            async def acquire(self, callback):
                writer.output.append(True)

            async def release(self):
                writer.output.append(False)

        source = AudioFftSource(
            Tap(), lambda: AudioPassband(14_100_000, 0, 2400, "upper")
        )
        receiver = None
    server.scope_hub.register("selected", source)
    ws = Socket()
    handler = ScopeHandler(ws, server)
    try:
        first = Subscription(
            "selected", receiver, View(14_100_000, 14_102_400), 1024, 10
        )
        await handler._handle_control(encode_scope_subscription(first))
        previous = next(iter(handler._streams.values())).subscription
        await handler._handle_control(
            encode_scope_subscription(Subscription("selected", 9, first.view, 1024, 10))
        )
        assert next(iter(handler._streams.values())).subscription is previous
        assert writer.output == [True]
        await handler._handle_control(
            encode_scope_subscription(
                Subscription("selected", receiver, first.view, 2800, 20)
            )
        )
        assert next(iter(handler._streams.values())).subscription is not previous
        # Both observed-source widths are provisional until their first frame.
        assert (
            next(iter(handler._streams.values())).subscription.granted.width
            == native_width
        )
        assert writer.output == [True]
    finally:
        await handler._unsubscribe()
    assert writer.output == [True, False]


@pytest.mark.asyncio
async def test_server_drains_scope_before_stopping_command_executor(monkeypatch):
    from rigplane.web import web_startup

    server = WebServer(None)
    writer = Writer()
    server.scope_hub.register(
        "radio", RadioScopeSource(resolve_radio_profile(model="ic705"), writer)
    )
    handler = ScopeHandler(Socket(), server)
    server.register_scope_handler(handler)
    await handler._handle_control(
        encode_scope_subscription(
            Subscription("radio", 0, View(14_090_000, 14_110_000), 1024, 10)
        )
    )

    real_stop = web_startup.stop_web_server

    async def stop_executor(received):
        assert received is server
        await real_stop(received)
        assert writer.output == [True, False]
        assert not handler._streams

    monkeypatch.setattr(web_startup, "stop_web_server", stop_executor)
    try:
        await server.stop()
    finally:
        await handler._unsubscribe()


@pytest.mark.asyncio
async def test_failed_grant_send_releases_new_subscription():
    from unittest.mock import AsyncMock

    server = WebServer(None)
    writer = Writer()
    server.scope_hub.register(
        "radio", RadioScopeSource(resolve_radio_profile(model="ic705"), writer)
    )
    ws = Socket()
    ws.send_text = AsyncMock(side_effect=OSError("closed"))
    await ws.incoming.put(
        encode_scope_subscription(
            Subscription("radio", 0, View(14_090_000, 14_110_000), 1024, 10)
        )
    )
    with pytest.raises(OSError, match="closed"):
        await ScopeHandler(ws, server).run()
    assert writer.output == [True, False]
    assert not server._scope_handlers


@pytest.mark.asyncio
async def test_stopping_server_rejects_new_demand():
    server = WebServer(None)
    writer = Writer()
    server.scope_hub.register(
        "radio", RadioScopeSource(resolve_radio_profile(model="ic705"), writer)
    )
    server._stopping = True
    ws = Socket()
    handler = ScopeHandler(ws, server)
    await handler._handle_control(
        encode_scope_subscription(
            Subscription("radio", 0, View(14_090_000, 14_110_000), 1024, 10)
        )
    )
    assert writer.output == []
    assert not handler._streams


@pytest.mark.asyncio
async def test_replacement_cancels_old_send_before_new_grant():
    from rigplane.web.protocol import decode_scope_frame_v2

    class BlockingSocket(Socket):
        def __init__(self):
            super().__init__()
            self.old_send_started = asyncio.Event()
            self.block_old = asyncio.Event()

        async def send_binary(self, data):
            if decode_scope_frame_v2(data).width == 100:
                self.old_send_started.set()
                await self.block_old.wait()
            await super().send_binary(data)

    writer = Writer()
    source = RadioScopeSource(resolve_radio_profile(model="ic705"), writer)
    server = WebServer(None)
    server.scope_hub.register("radio", source)
    ws = BlockingSocket()
    handler = ScopeHandler(ws, server)
    frame = ScopeFrame(0, 0, 14_090_000, 14_110_000, bytes([160]) * 475, False)
    try:
        await handler._handle_control(
            encode_scope_subscription(
                Subscription("radio", 0, View(14_090_000, 14_110_000), 100, 10)
            )
        )
        source.feed_frame(frame)
        await await_until(ws.old_send_started.is_set)
        await handler._handle_control(
            encode_scope_subscription(
                Subscription("radio", 0, View(14_090_000, 14_110_000), 2800, 20)
            )
        )
        grant_index = len(ws.events) - 1
        source.feed_frame(frame)
        await await_until(lambda: bool(ws.binary))
        # The 2800 request is granted at the 475-bin native width.
        assert all(decode_scope_frame_v2(data).width == 475 for data in ws.binary)
        assert ws.events[grant_index][0] == "text"
        assert ws.events[grant_index + 1][0] == "binary"
        assert writer.output == [True]
    finally:
        await handler._unsubscribe()


@pytest.mark.asyncio
async def test_shutdown_cleanup_failure_keeps_other_sources_releasing(
    monkeypatch, caplog
):
    from rigplane.web import web_startup

    class FailingWriter(Writer):
        fail = True

        async def set_output(self, enabled, *, release=True):
            await super().set_output(enabled)
            if not enabled and self.fail:
                raise RuntimeError("output restoration failed")

    server = WebServer(None)
    failing = FailingWriter()
    healthy = Writer()
    handlers = []
    for source_id, writer in [("failing", failing), ("healthy", healthy)]:
        server.scope_hub.register(
            source_id, RadioScopeSource(resolve_radio_profile(model="ic705"), writer)
        )
        handler = ScopeHandler(Socket(), server)
        server.register_scope_handler(handler)
        await handler._handle_control(
            encode_scope_subscription(
                Subscription(source_id, 0, View(14_090_000, 14_110_000), 1024, 10)
            )
        )
        handlers.append(handler)
    failed_subscription = next(iter(handlers[0]._streams.values())).subscription

    real_stop = web_startup.stop_web_server

    async def stop_executor(received):
        assert received is server
        await real_stop(received)
        assert healthy.output == [True, False]
        assert all(not handler._streams for handler in handlers)

    monkeypatch.setattr(web_startup, "stop_web_server", stop_executor)
    try:
        await server.stop()
        assert "restoration remains pending" in caplog.text
    finally:
        failing.fail = False
        await failed_subscription.close()


@pytest.mark.asyncio
async def test_reconnect_waiting_behind_last_close_cannot_revive_output(monkeypatch):
    from unittest.mock import AsyncMock
    from test_web_server_coverage import _scope_radio
    from rigplane.web.server import WebConfig

    radio = _scope_radio()
    radio._fetch_initial_state = AsyncMock()
    server = WebServer(radio, WebConfig(radio_model="IC-7610"))
    server.scope_hub
    session = server._radio_scope_session
    await session.set_output(True)
    changes = []
    held = asyncio.Event()
    release = asyncio.Event()

    async def enable(**kwargs):
        changes.append(True)
        held.set()
        await release.wait()

    async def disable(**kwargs):
        changes.append(False)

    radio.enable_scope.side_effect = enable
    radio.disable_scope.side_effect = disable
    first = asyncio.create_task(server._refetch_and_reenable())
    close = second = None
    try:
        assert await await_until(held.is_set)
        close = asyncio.create_task(session.set_output(False))
        assert await await_until(lambda: len(session._lock._waiters or ()) == 1)
        second = asyncio.create_task(server._refetch_and_reenable())
        assert await await_until(lambda: len(session._lock._waiters or ()) == 2)
        release.set()
        await asyncio.gather(first, close, second)
        assert changes == [True, False]
        assert session.active is False
        assert session.enabled is False
    finally:
        release.set()
        tasks = [task for task in (first, close, second) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_previous_session_health_cannot_override_new_session_grace(
    virtual_clock, monkeypatch
):
    from test_web_server_coverage import (
        _virtual_scope_server,
        _feed_scope_health,
        _live_scope_frame,
    )

    server, _, _ = await _virtual_scope_server(monkeypatch, virtual_clock)
    _feed_scope_health(server, _live_scope_frame())
    await virtual_clock.elapse(100)
    await server._radio_scope_session.set_output(False)
    await server._radio_scope_session.set_output(True)
    monkeypatch.setattr(server, "_civ_link_flowing", lambda now: True)
    import rigplane.web.server as server_module

    monkeypatch.setattr(server_module, "asyncio", asyncio)
    monitor = asyncio.create_task(server._scope_health_monitor())
    try:
        await virtual_clock.elapse(server._scope_health_interval)
        assert server._scope_hidden_published is not True
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_path", ["deferred", "reconnect"])
async def test_scope_enable_success_starts_new_health_grace(
    virtual_clock, monkeypatch, enable_path
):
    from test_web_server_coverage import _virtual_scope_server

    server, _, _ = await _virtual_scope_server(monkeypatch, virtual_clock)
    await virtual_clock.elapse(100)
    await server._radio_scope_session.invalidate()
    if enable_path == "deferred":
        await server._radio_scope_session.set_ready(True)
    else:
        await server._refetch_and_reenable()
    assert server._radio_scope_session.enabled is True
    assert server._radio_scope_session.health(civ_flowing=True).hidden is False


@pytest.mark.parametrize(
    "model,widths", [("ic7300", (475, 689, 475)), ("ic7610", (689, 475, 689))]
)
async def test_observed_native_width_regrants_before_binary_for_existing_client(
    model, widths, virtual_clock
):
    from rigplane.web.protocol import decode_scope_frame_v2

    server = WebServer(None)
    source = RadioScopeSource(resolve_radio_profile(model=model), Writer())
    server.scope_hub.register("radio", source)
    ws = Socket()
    handler = ScopeHandler(ws, server)
    try:
        await handler._handle_control(
            encode_scope_subscription(
                Subscription("radio", 0, View(14_090_000, 14_110_000), 2800, 20)
            )
        )
        assert decode_scope_granted(ws.text[-1]).width == 2800
        for index, width in enumerate(widths):
            source.feed_frame(
                ScopeFrame(0, 0, 14_090_000, 14_110_000, bytes([160]) * width, False)
            )
            await virtual_clock.elapse(0.1)
            await await_until(lambda: len(ws.binary) == index + 1)
            assert source.capabilities().native_width_for(0) == width
            assert (
                next(iter(handler._streams.values())).subscription.granted.width
                == width
            )
            grant_event, wire_event = ws.events[-2:]
            assert grant_event[0] == "text"
            assert decode_scope_granted(grant_event[1]).width == width
            assert wire_event[0] == "binary"
            frame = decode_scope_frame_v2(wire_event[1])
            assert frame.width == width
            assert frame.pixels == bytes([160]) * width
    finally:
        await handler._unsubscribe()


async def test_one_real_scope_socket_multiplexes_radio_and_audio():
    import aiohttp
    import numpy as np
    from rigplane.audio.bus import STAGE_RX_POST_DSP
    from rigplane.audio.scope_source import AudioFftSource
    from rigplane.scope import ScopeAssembler
    from rigplane.web.handlers.audio import AudioBroadcaster
    from rigplane.web.protocol import decode_scope_frame_v2
    from rigplane.web.scope_sources import WebScopePcmTap
    from rigplane.web.server import WebConfig
    from rigplane_testkit.icom.scope_control import (
        ScopeControlSimulator as MockIcomRadio,
    )
    from test_handlers_coverage import _RelayRadio
    from test_audio_scope_dispatch import _seed_audio_facts
    from test_web_server import _addr

    writer = Writer()
    server = WebServer(None, WebConfig(host="127.0.0.1", port=0))
    radio = RadioScopeSource(resolve_radio_profile(model="ic7610"), writer)
    server.scope_hub.register("radio", radio)
    server._audio_broadcaster = AudioBroadcaster(_RelayRadio())
    _seed_audio_facts(server)
    tap = WebScopePcmTap(server)
    server.scope_hub.register(
        "audio_fft", AudioFftSource(tap, tap.facts, fft_size=2048)
    )
    simulator = MockIcomRadio(scope_stream=True)
    assembler = ScopeAssembler()
    native = None
    for raw in simulator._scope_templates[0]:
        native = assembler.feed(raw[7:-1], raw[6]) or native
    assert native is not None
    await server.start()
    host, port = _addr(server)
    try:
        async with aiohttp.ClientSession() as client:
            async with client.ws_connect(f"http://{host}:{port}/api/v1/scope") as ws:
                for source_id, receiver, view in (
                    ("radio", 0, View(native.start_freq_hz, native.end_freq_hz)),
                    ("audio_fft", None, View(14_074_000, 14_076_400)),
                ):
                    await ws.send_str(
                        encode_scope_subscription(
                            Subscription(source_id, receiver, view, 1024, 20)
                        )
                    )
                    assert (await ws.receive_json(timeout=5))["sourceId"] == source_id
                assert writer.output == [True]
                radio.feed_frame(native)
                server._audio_broadcaster.taps(STAGE_RX_POST_DSP).feed(
                    np.ones(8192, dtype=np.int16).tobytes()
                )
                received = set()
                while len(received) < 2:
                    message = await ws.receive(timeout=5)
                    if message.type == aiohttp.WSMsgType.BINARY:
                        received.add(decode_scope_frame_v2(message.data).source_id)
                    else:
                        assert message.type == aiohttp.WSMsgType.TEXT
                assert received == {"radio", "audio_fft"}
                await ws.send_json(
                    {"type": "unsubscribe", "sourceId": "audio_fft", "receiver": None}
                )
                await await_until(
                    lambda: server._audio_broadcaster._subscription is None
                )
                assert writer.output == [True]
                radio.feed_frame(native)
                message = await ws.receive(timeout=5)
                assert decode_scope_frame_v2(message.data).source_id == "radio"
        await await_until(lambda: writer.output == [True, False])
    finally:
        await server.stop()


async def test_multiplex_updates_and_targeted_errors_preserve_sibling_streams():
    import json

    server = WebServer(None)
    writer = Writer()
    source = RadioScopeSource(resolve_radio_profile(model="ic7610"), writer)
    server.scope_hub.register("radio", source)
    ws = Socket()
    handler = ScopeHandler(ws, server)
    view = View(14_090_000, 14_110_000)
    try:
        for receiver in (0, 1):
            await handler._handle_control(
                encode_scope_subscription(
                    Subscription("radio", receiver, view, 200, 20)
                )
            )
        original = dict(handler._streams)
        await handler._handle_control(
            encode_scope_subscription(Subscription("radio", 0, view, 100, 10))
        )
        assert len(handler._streams) == 2
        assert (
            handler._streams[ScopeTarget("radio", 1)]
            is original[ScopeTarget("radio", 1)]
        )
        assert (
            handler._streams[ScopeTarget("radio", 0)]
            is not original[ScopeTarget("radio", 0)]
        )
        assert writer.output == [True]
        previous = dict(handler._streams)
        invalid = json.loads(
            encode_scope_subscription(Subscription("radio", 0, view, 100, 10))
        )
        invalid["width"] = 0
        await handler._handle_control(json.dumps(invalid))
        assert handler._streams == previous
        error = json.loads(ws.text[-1])
        assert error["type"] == "error"
        assert (error["sourceId"], error["receiver"]) == ("radio", 0)
        await handler._handle_control(
            '{"type":"unsubscribe","sourceId":"radio","receiver":true}'
        )
        assert handler._streams == previous
        await handler._handle_control(
            '{"type":"unsubscribe","sourceId":"radio","receiver":0}'
        )
        assert set(handler._streams) == {ScopeTarget("radio", 1)}
        assert writer.output == [True]
        await handler._handle_control('{"type":"unsubscribe"}')
        assert not handler._streams
        assert writer.output == [True, False]
    finally:
        await handler._unsubscribe()


async def test_multiplex_cleanup_attempts_all_streams_after_one_stop_fails():
    class FailingWriter(Writer):
        fail = True

        async def set_output(self, enabled, *, release=True):
            await super().set_output(enabled)
            if not enabled and self.fail:
                raise RuntimeError("restore failed")

    server = WebServer(None)
    bad, good = FailingWriter(), Writer()
    handler = ScopeHandler(Socket(), server)
    failed = None
    try:
        for name, writer in (("bad", bad), ("good", good)):
            server.scope_hub.register(
                name, RadioScopeSource(resolve_radio_profile(model="ic7300"), writer)
            )
            await handler._handle_control(
                encode_scope_subscription(
                    Subscription(name, 0, View(14_090_000, 14_110_000), 100, 20)
                )
            )
        failed = handler._streams[ScopeTarget("bad", 0)].subscription
        with pytest.raises(RuntimeError, match="restore failed"):
            await handler._unsubscribe()
        assert not handler._streams
        assert good.output == [True, False]
    finally:
        bad.fail = False
        if failed is not None:
            await failed.close()
        await handler._unsubscribe()


async def test_cancelling_multiplex_socket_releases_every_subscription():
    server = WebServer(None)
    writers = [Writer(), Writer()]
    handler = ScopeHandler(Socket(), server)
    for name, writer in zip(("first", "second"), writers, strict=True):
        server.scope_hub.register(
            name, RadioScopeSource(resolve_radio_profile(model="ic7300"), writer)
        )
        await handler._handle_control(
            encode_scope_subscription(
                Subscription(name, 0, View(14_090_000, 14_110_000), 100, 20)
            )
        )
    task = asyncio.create_task(handler.run())
    await await_until(lambda: handler in server._scope_handlers)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not handler._streams
    assert handler not in server._scope_handlers
    assert all(writer.output == [True, False] for writer in writers)
