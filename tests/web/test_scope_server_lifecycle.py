"""Scope lifecycle failures must not take down unrelated web services."""

import asyncio
import json
from unittest.mock import AsyncMock

import aiohttp
import numpy as np
import pytest

from rigplane.audio.bus import STAGE_RX_POST_DSP
from rigplane.audio.scope_source import AudioFftSource
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.radio_scope_source import RadioScopeSource
from rigplane.scope import ScopeFrame
from rigplane.scope.models import Subscription, View
from rigplane.sdr.fake import FakeIqSource
from rigplane.sdr.types import SdrConfig
from rigplane.testing.timing import await_until
from rigplane.web.handlers.audio import AudioBroadcaster
from rigplane.web.handlers.scope import ScopeHandler
from rigplane.web.protocol import decode_scope_frame_v2, encode_scope_subscription
from rigplane.web.scope_sources import WebScopePcmTap
from rigplane.web.server import WebConfig, WebServer

from test_audio_scope_dispatch import _seed_audio_facts
from test_handlers_coverage import _RelayRadio
from tests.web.test_scope_hub_channel import Socket, Writer
from tests.web.test_sdr_scope_source import _run_cmd_web
from test_web_server import _addr
from test_web_server_coverage import _scope_radio


@pytest.mark.parametrize(
    "configured,expected",
    [
        ("hardware", "radio"),
        ("audio_fft", "audio_fft"),
        ("sdr", "iq_fft"),
        ("auto", "iq_fft"),
    ],
)
async def test_cli_selection_is_separate_from_all_available_sources(
    monkeypatch, configured, expected
):
    _, config = await _run_cmd_web(
        monkeypatch, {}, "--scope-source", configured, "--sdr-device", "fake"
    )
    config.radio_model = "IC-7300"
    radio = _scope_radio()
    server = WebServer(radio, config)
    server._audio_broadcaster = AudioBroadcaster(_RelayRadio())
    tap = WebScopePcmTap(server)
    server._audio_scope_source = AudioFftSource(tap, tap.facts, fft_size=2048)
    chunks = []

    class Capture:
        def write(self, data):
            chunks.append(data)

        async def drain(self):
            pass

    await server._serve_capabilities(Capture())
    payload = json.loads(b"".join(chunks).split(b"\r\n\r\n", 1)[1])
    assert payload["scopeSources"] == ["radio", "audio_fft", "iq_fft"]
    assert payload["scopeDefaultSourceId"] == expected
    assert payload["scopeSource"] is None


async def test_optional_sdr_factory_failure_keeps_http_radio_audio_and_retries():
    attempts = 0
    available = False
    provider = FakeIqSource(center_freq_hz=14_074_000, sample_rate_hz=48_000)

    def factory(config):
        nonlocal attempts
        attempts += 1
        if not available:
            raise ImportError("optional SDR binding unavailable")
        return provider

    server = WebServer(
        None,
        WebConfig(
            host="127.0.0.1",
            port=0,
            radio_model="IC-7610",
            sdr_config=SdrConfig("fake", sample_rate_hz=48_000),
            sdr_source_factory=factory,
        ),
    )
    writer = Writer()
    radio = RadioScopeSource(resolve_radio_profile(model="ic7610"), writer)
    server._audio_broadcaster = AudioBroadcaster(_RelayRadio())
    _seed_audio_facts(server)
    tap = WebScopePcmTap(server)
    server._audio_scope_source = AudioFftSource(tap, tap.facts, fft_size=2048)
    await server.start()
    assert (
        server._sdr_status_payload()["lastError"] == "optional SDR binding unavailable"
    )
    server.scope_hub.register("radio", radio)
    try:
        host, port = _addr(server)
        async with aiohttp.ClientSession() as client:
            async with client.get(
                f"http://{host}:{port}/api/v1/capabilities"
            ) as response:
                assert response.status == 200
            async with client.ws_connect(f"http://{host}:{port}/api/v1/scope") as ws:
                for source_id, receiver in [("radio", 0), ("audio_fft", None)]:
                    await ws.send_str(
                        encode_scope_subscription(
                            Subscription(
                                source_id,
                                receiver,
                                View(14_074_000, 14_076_400),
                                100,
                                20,
                            )
                        )
                    )
                    assert (await ws.receive_json(timeout=2))["type"] == "granted"
                    if source_id == "radio":
                        radio.feed_frame(
                            ScopeFrame(
                                0, 0, 14_074_000, 14_076_400, b"\x10" * 100, False
                            )
                        )
                    else:
                        server._audio_broadcaster.taps(STAGE_RX_POST_DSP).feed(
                            np.ones(8192, dtype=np.int16).tobytes()
                        )
                    message = await ws.receive(timeout=2)
                    assert decode_scope_frame_v2(message.data).source == source_id
                assert attempts == 1
                available = True
                await ws.send_str(
                    encode_scope_subscription(
                        Subscription(
                            "iq_fft", None, View(14_064_000, 14_084_000), 100, 20
                        )
                    )
                )
                assert (await ws.receive_json(timeout=2))["type"] == "granted"
                assert provider.is_open
                assert server._sdr_status_payload()["lastError"] is None
        await await_until(lambda: not provider.is_open)
    finally:
        await server.stop()


@pytest.mark.realtime(reason="local simulator and real stopped radio poller")
@pytest.mark.parametrize("cancelled_close", [False, True])
@pytest.mark.timeout(20)
async def test_server_shutdown_retires_real_radio_subscription_after_poller_stop(
    cancelled_close, monkeypatch
):
    from rigplane.radio import IcomRadio
    from rigplane_testkit.icom import MockIcomRadio

    sim = MockIcomRadio(scope_stream=True)
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    await radio.connect()
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    stopping = None
    closing = None
    await server.start()
    futures = []
    put_ordered = server.command_queue.put_ordered

    def record_command(command, **kwargs):
        if kwargs.get("future") is not None:
            futures.append(kwargs["future"])
        return put_ordered(command, **kwargs)

    monkeypatch.setattr(server.command_queue, "put_ordered", record_command)
    try:
        handler = ScopeHandler(Socket(), server)
        server.register_scope_handler(handler)
        await handler._handle_control(
            encode_scope_subscription(
                Subscription("radio", 0, View(14_090_000, 14_110_000), 100, 10)
            )
        )
        assert handler._streams
        provider = FakeIqSource(center_freq_hz=14_100_000, sample_rate_hz=48_000)
        from rigplane.sdr.scope_source import IqFftSource

        server.scope_hub.register("test-iq", IqFftSource(provider, start_worker=False))
        await handler._handle_control(
            encode_scope_subscription(
                Subscription("test-iq", None, View(14_090_000, 14_110_000), 100, 10)
            )
        )
        poller = server._radio_poller
        poll_task = poller._task
        poller.stop()
        await asyncio.gather(poll_task, return_exceptions=True)
        if cancelled_close:
            closing = asyncio.create_task(handler._unsubscribe())
            await await_until(lambda: server.command_queue.has_commands)
            closing.cancel()
        restore = AsyncMock(wraps=poller.restore_scope_after_shutdown)
        monkeypatch.setattr(poller, "restore_scope_after_shutdown", restore)
        stopping = asyncio.create_task(server.stop())
        done, _ = await asyncio.wait({stopping}, timeout=3)
        assert stopping in done, (
            "shutdown waited forever on the stopped scope-command consumer"
        )
        await stopping
        if closing is not None:
            assert closing.done(), (
                "cancelled unsubscribe retained its shielded queue wait"
            )
            await asyncio.gather(closing, return_exceptions=True)
        assert not handler._streams
        assert not provider.is_open
        assert server._server is None
        assert not server._bg_tasks
        assert not server.command_queue.has_commands
        assert not server._scope_command_futures
        assert not server._radio_scope_source._active
        restore.assert_awaited_once()
        assert not server._scope_enabled
    finally:
        # Recover the old implementation's blocked queue wait after the RED assertion.
        for future in futures:
            if not future.done():
                future.set_exception(RuntimeError("test shutdown recovery"))
        if stopping is not None:
            await asyncio.gather(stopping, return_exceptions=True)
        else:
            await server.stop()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await radio.disconnect()
        await sim.stop()


@pytest.mark.parametrize("pending_stage", [0, 1, 2])
@pytest.mark.timeout(10)
async def test_shutdown_cancels_pending_replacement_view_at_each_command_stage(
    pending_stage, virtual_clock, monkeypatch
):
    from unittest.mock import MagicMock

    from rigplane.web.radio_poller import (
        PttOff,
        SetScopeMode,
        SetScopeSpan,
        SwitchScopeReceiver,
    )
    server = WebServer(_scope_radio(), WebConfig(radio_model="IC-7610"))
    server._radio_state.main.freq = 14_100_000
    assert server.scope_hub is not None
    source = server._radio_scope_source
    assert source is not None
    listener = MagicMock()
    listener.wait_closed = AsyncMock()
    server._server = listener
    events = []
    futures = []
    replacement_commands = []
    replacing = False
    put_ordered = server.command_queue.put_ordered

    def enqueue(command, **kwargs):
        put_ordered(command, **kwargs)
        future = kwargs.get("future")
        if future is None:
            return
        futures.append(future)
        if replacing:
            replacement_commands.append(command)
        if not replacing or len(replacement_commands) <= pending_stage:
            future.set_result(None)

    monkeypatch.setattr(server.command_queue, "put_ordered", enqueue)
    poller = MagicMock()
    poller.stop.side_effect = lambda: events.append("poller-stopped")

    async def drain(*, timeout):
        assert events[-1] == "client-teardown"
        entries = server.command_queue.drain_entries()
        assert any(isinstance(entry.command, PttOff) for entry in entries)
        events.append("tx-drain")

    async def restore():
        assert events[-1] == "tx-drain"
        events.append("scope-restored")

    poller.drain_tx_safety_commands = AsyncMock(side_effect=drain)
    poller.restore_scope_after_shutdown = AsyncMock(side_effect=restore)
    server._radio_poller = poller
    handler = ScopeHandler(Socket(), server)
    server.register_scope_handler(handler)
    request = encode_scope_subscription(
        Subscription("radio", 0, View(14_090_000, 14_110_000), 100, 10)
    )
    await handler._handle_control(request)
    assert handler._streams, handler._ws.text
    server.command_queue.drain_entries()
    poller.stop()
    replacing = True
    replacement = asyncio.create_task(handler._handle_control(request))

    async def client():
        try:
            await replacement
            await asyncio.Event().wait()
        finally:
            server.command_queue.put_ordered(PttOff())
            events.append("client-teardown")

    client_task = asyncio.create_task(client())
    server._client_tasks.add(client_task)
    stopping = None
    try:
        await virtual_clock.elapse(0)
        assert not replacement.done()
        assert [type(command) for command in replacement_commands] == [
            SwitchScopeReceiver, SetScopeMode, SetScopeSpan
        ][: pending_stage + 1]
        pending = futures[-1]
        assert not pending.done()
        stopping = asyncio.create_task(server.stop())
        await virtual_clock.elapse(0.5)
        assert stopping.done(), "pending replacement view prevented shutdown drain"
        await stopping
        assert pending.cancelled()
        assert replacement.done()
        assert client_task.done()
        assert len(replacement_commands) == pending_stage + 1
        assert all(future.done() for future in futures)
        assert not server._scope_command_futures
        assert not server.command_queue.has_commands
        assert not handler._streams
        assert not source._active
        assert not source._callbacks
        assert not server._scope_enabled
        assert not server._scope_enable_failed
        assert server._server is None
        listener.close.assert_called_once()
        listener.wait_closed.assert_awaited_once()
        assert events[-3:] == ["client-teardown", "tx-drain", "scope-restored"]
    finally:
        for future in futures:
            if not future.done():
                future.set_exception(RuntimeError("test shutdown recovery"))
        if stopping is None:
            stopping = asyncio.create_task(server.stop())
        await virtual_clock.elapse(0)
        await asyncio.gather(stopping, client_task, replacement, return_exceptions=True)
