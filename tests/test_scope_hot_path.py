"""Scope fan-out, liveness and compression cost regressions (MOR-4268)."""

import asyncio
import random
from unittest.mock import MagicMock

import aiohttp
import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core._bounded_queue import BoundedQueue
from rigplane.radio import IcomRadio
from rigplane.scope import ScopeFrame
from rigplane.types import bcd_encode
from rigplane.web.handlers.scope import ScopeHandler
from rigplane.web.protocol import encode_scope_frame
from rigplane.web.server import WebConfig, WebServer
from rigplane.ws import WebSocketConnection, negotiate_deflate
from test_radio import MockTransport
from test_scope import _seq1_payload


@pytest.mark.parametrize("channel", ["scope", "audio_scope", "audio_fft"])
def test_scope_encode_once_shared_sequence(monkeypatch, channel):
    server = WebServer()
    handlers = [ScopeHandler(MagicMock()) for _ in range(3)]
    for handler in handlers:
        handler._running = True
    targets = (
        server._scope_handlers
        if channel == "audio_fft"
        else getattr(server, f"_{channel}_handlers")
    )
    targets.update(handlers[:2])
    encoder = MagicMock(wraps=encode_scope_frame)
    monkeypatch.setattr(
        "rigplane.web.server.encode_scope_frame", encoder, raising=False
    )
    monkeypatch.setattr(
        "rigplane.web.handlers.scope.encode_scope_frame", encoder, raising=False
    )
    frame = ScopeFrame(0, 0, 14_000_000, 14_100_000, b"\x01\x02", False)
    broadcast = (
        server._dispatch_audio_fft_frame
        if channel == "audio_fft"
        else getattr(server, f"_broadcast_{channel}")
    )
    broadcast(frame)
    assert encoder.call_count == 1
    late_targets = server._audio_scope_handlers if channel == "audio_fft" else targets
    late_targets.add(handlers[2])
    broadcast(frame)
    assert encoder.call_count == 2
    first = [h._frame_queue.get_nowait() for h in handlers[:2]]
    assert first[0] is first[1]
    second = [h._frame_queue.get_nowait() for h in handlers]
    assert second[0] is second[1] is second[2]
    assert (
        int.from_bytes(second[0][11:13], "little")
        == (int.from_bytes(first[0][11:13], "little") + 1) & 0xFFFF
    )
    if channel == "audio_fft":
        # Selecting an SDR splits main scope from the audio-scope channel.
        monkeypatch.setattr(server, "_sdr_scope_active", lambda: True)
        broadcast(frame)
        split = handlers[2]._frame_queue.get_nowait()
        assert int.from_bytes(split[11:13], "little") == 2
        for _ in range(3):
            broadcast(frame)
        monkeypatch.setattr(server, "_sdr_scope_active", lambda: False)
        broadcast(frame)
        joined = [handlers[2]._frame_queue.get_nowait() for _ in range(4)][-1]
        assert int.from_bytes(joined[11:13], "little") == 6


@pytest.mark.parametrize("model", ["IC-7610", "IC-9700"])
async def test_scope_stamps_without_control_wakeup(monkeypatch, model):
    radio = IcomRadio("127.0.0.1", model=model)
    radio._civ_transport = MockTransport()
    radio._connected = True
    server = WebServer(radio, config=WebConfig())
    radio._on_state_change = server._on_radio_state_change
    queue = BoundedQueue(maxsize=32)
    server._control_event_queues.add(queue)
    event = MagicMock(wraps=server.broadcast_event)
    state = MagicMock(wraps=server._broadcast_state_update)
    monkeypatch.setattr(server, "broadcast_event", event)
    monkeypatch.setattr(server, "_broadcast_state_update", state)
    wave = parse_civ_frame(
        build_civ_frame(
            CONTROLLER_ADDR,
            radio._radio_addr,
            0x27,
            data=b"\x00\x00"
            + _seq1_payload(
                receiver=0,
                seq=1,
                seq_max=1,
                mode=0,
                start_hz=14_000_000,
                end_hz=50_000,
                oor=False,
                extra_pixels=b"\x01\x02",
            ),
        )
    )
    # The first wave can legitimately publish a changed scope mode/span.
    await radio._civ_runtime._route_civ_frame(wave, generation=radio._civ_epoch)
    event.reset_mock()
    state.reset_mock()
    while not queue.empty():
        queue.get_nowait()
    radio.last_valid_frame_monotonic = 0.0
    await radio._civ_runtime._route_civ_frame(wave, generation=radio._civ_epoch)
    assert radio.last_valid_frame_monotonic > 0
    event.assert_not_called()
    state.assert_not_called()
    assert queue.empty()
    frequency = parse_civ_frame(
        build_civ_frame(
            CONTROLLER_ADDR, radio._radio_addr, 0x03, data=bcd_encode(14_074_000)
        )
    )
    await radio._civ_runtime._route_civ_frame(frequency, generation=radio._civ_epoch)
    assert event.called and state.called and not queue.empty()
    radio._connected = False


@pytest.mark.parametrize("model", ["IC-7610", "IC-9700"])
async def test_scope_stream_queue_only_while_iterated(model):
    radio = IcomRadio("127.0.0.1", model=model)
    radio._connected = True
    frame = ScopeFrame(0, 0, 14_000_000, 14_100_000, b"\x01", False)
    assert not hasattr(radio, "_civ_event_queue")
    assert radio._scope_frame_queue is None
    radio._civ_runtime._publish_scope_frame(frame)
    assert radio._scope_frame_queue is None

    stream = radio.scope_stream()
    assert radio._scope_frame_queue is None
    pending = asyncio.create_task(anext(stream))
    try:
        await asyncio.sleep(0)
        assert radio._scope_frame_queue is not None
        radio._civ_runtime._publish_scope_frame(frame)
        assert await pending is frame
        second_stream = radio.scope_stream()
        second = asyncio.create_task(anext(second_stream))
        try:
            await asyncio.sleep(0)
            queue = radio._scope_frame_queue
            await stream.aclose()
            assert radio._scope_frame_queue is queue
            radio._civ_runtime._publish_scope_frame(frame)
            assert await second is frame
        finally:
            second.cancel()
            await second_stream.aclose()
    finally:
        pending.cancel()
        await stream.aclose()
        radio._connected = False
    assert radio._scope_frame_queue is None


def test_deflate_negotiates_context_takeover():
    assert (
        negotiate_deflate("permessage-deflate; client_max_window_bits")
        == "permessage-deflate"
    )
    assert negotiate_deflate("permessage-deflate; server_no_context_takeover") is None
    assert negotiate_deflate("permessage-deflate; server_max_window_bits=10") is None


async def test_compressor_once_and_aiohttp_peer_decodes(monkeypatch):
    import rigplane.ws as ws_module

    compressor = MagicMock(wraps=ws_module.zlib.compressobj)
    monkeypatch.setattr(ws_module.zlib, "compressobj", compressor)
    # A discarded compressed candidate must not poison later context.
    noise = random.Random(4268).randbytes(512)
    payloads = [noise, noise, b"scope" * 200, b"scope" * 200]
    completed = asyncio.get_running_loop().create_future()

    async def serve(reader, writer):
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            headers = dict(
                (key.lower(), value)
                for line in request.decode().split("\r\n")[1:]
                if ": " in line
                for key, value in [line.split(": ", 1)]
            )
            upgrade = ws_module.upgrade_response(headers, allow=lambda _: None)
            writer.write(upgrade.response)
            await writer.drain()
            conn = WebSocketConnection(reader, writer, deflate=upgrade.deflate)
            for payload in payloads:
                await conn.send_binary(payload)
            # aiohttp sends several messages using its own takeover compressor.
            for payload in payloads:
                opcode, received = await conn.recv()
                assert opcode == ws_module.WS_OP_BINARY and received == payload
            await conn.close()
            completed.set_result(None)
        except BaseException as exc:
            if not completed.done():
                completed.set_exception(exc)
        finally:
            writer.close()

    async with await asyncio.start_server(serve, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        async with aiohttp.ClientSession() as client:
            async with client.ws_connect(
                f"http://127.0.0.1:{port}", compress=15
            ) as peer:
                for payload in payloads:
                    message = await peer.receive(timeout=3)
                    assert message.type == aiohttp.WSMsgType.BINARY
                    assert message.data == payload
                # Count the server's compressor before the peer creates its own.
                assert compressor.call_count == 1
                for payload in payloads:
                    await peer.send_bytes(payload)
                await asyncio.wait_for(completed, 3)
