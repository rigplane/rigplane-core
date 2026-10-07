from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_managed_tx_authority import authority
from test_managed_tx_http_route import _reader
from test_managed_tx_web_projection import _GatePort
from test_rigctld_handler import make_mock_radio, set_cmd
from test_web_audio_tx_session import _SessionLanRadio, _pcm_tx_frame, _start_tx
from test_web_auth_compare_digest import _MemoryWriter

from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    Observation,
    SourceMetadata,
)
from rigplane.core.tx_observation import OBSERVED_PTT_PATH, ObservedPtt
from rigplane.rigctld.contract import HamlibError, RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.runtime.controller_authority import ControllerError
from rigplane.runtime.managed_tx_state import ManagedTxOutcome
from rigplane.web import server as server_module
from rigplane.web.handlers.audio import AudioHandler
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.server import WebConfig, WebServer, _HttpCommandExecutor


class Writer(_MemoryWriter):
    peer = "127.0.0.1"

    def get_extra_info(self, *_args, **_kwargs):
        return (self.peer, 1)

    @property
    def status(self):
        return int(self.buffer.split(b" ", 2)[1])

    @property
    def payload(self):
        return json.loads(self.buffer.split(b"\r\n\r\n", 1)[1])


@pytest.fixture
async def station():
    managed, *_ = authority()
    server = WebServer(None, WebConfig(radio_model="IC-7610"))
    server._production_managed_tx_port = _GatePort(managed)
    server._controller_installed = True
    server._controller_enforcement_ready = True
    server.command_state_store.apply_current(
        Observation(
            path=OBSERVED_PTT_PATH,
            value=ObservedPtt.OFF,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=time.monotonic(),
            max_age=60,
        )
    )
    yield server, managed
    server._controller.revoke()
    await server._controller.settle()
    await managed.close()


async def request(server, method, path, body=None, key=None, peer="127.0.0.1"):
    writer = Writer()
    writer.peer = peer
    reader, headers = _reader(body) if body is not None else (None, {})
    if key is not None:
        headers["x-rigplane-controller"] = key
    await server._handle_http(writer, method, path, headers, reader)
    return writer


async def remote(server):
    await server._controller.set_mode("remote")
    grant = server._controller.acquire("pro")
    primary = server._controller.attach(grant["controller_key"], "primary", "native")
    server._controller.register_control(primary)
    return grant["controller_key"], primary


async def test_http_acquisition_is_atomic_anonymous_and_mode_is_local_only(station):
    server, _ = station
    status = await request(server, "GET", "/api/v1/controller")
    assert status.payload["protocol_version"] == 1
    assert status.payload["mode"] == "local"
    denied = await request(
        server, "POST", "/api/v1/controller/mode", {"mode": "remote"}, peer="100.64.0.1"
    )
    assert denied.status == 403
    server._controller_enforcement_ready = False
    refused = await request(
        server, "POST", "/api/v1/controller/mode", {"mode": "remote"}
    )
    assert refused.status == 503
    server._controller_enforcement_ready = True
    armed = await request(server, "POST", "/api/v1/controller/mode", {"mode": "remote"})
    assert armed.status == 200
    winners = await asyncio.gather(
        *(
            request(server, "POST", "/api/v1/controller", {"kind": kind})
            for kind in ("browser", "pro")
        )
    )
    assert sorted(item.status for item in winners) == [201, 409]
    key = next(item.payload["controller_key"] for item in winners if item.status == 201)
    assert len(key) == 64
    status = await request(server, "GET", "/api/v1/controller")
    assert key not in repr(status.payload)
    assert set(status.payload) == {
        "protocol_version",
        "mode",
        "state",
        "generation",
        "ttl_ms",
        "heartbeat_ms",
        "remaining_ms",
    }
    released = await request(server, "DELETE", "/api/v1/controller", key=key)
    assert released.payload["state"] == "idle"


@pytest.mark.parametrize(
    "path", ["/api/v1/ws", "/api/v1/audio", "/api/v1/scope", "/api/v1/audio-scope"]
)
@pytest.mark.parametrize("bad", ["missing", "wrong", "duplicate"])
async def test_all_ws_channels_refuse_unclaimed_or_ambiguous_keys(station, path, bad):
    server, _ = station
    key, _ = await remote(server)
    headers = {"sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ=="}
    if bad != "missing":
        protocol = "rigplane-controller-v1." + ("0" * 64 if bad == "wrong" else key)
        headers["sec-websocket-protocol"] = (
            protocol if bad == "wrong" else protocol + "," + protocol
        )
    writer = Writer()
    await server._handle_websocket(asyncio.StreamReader(), writer, path, headers)
    assert writer.status == 403
    assert key not in writer.payload.values()
    assert server._controller.status()["state"] == "held"


@pytest.mark.parametrize(
    "path,name",
    [
        ("/api/v1/ws", "ControlHandler"),
        ("/api/v1/audio", "AudioHandler"),
        ("/api/v1/scope", "ScopeHandler"),
        ("/api/v1/audio-scope", "ScopeHandler"),
    ],
)
async def test_grouped_ws_echoes_only_valid_protocol_and_binds_currency(
    station, monkeypatch, path, name
):
    server, _ = station
    key, _ = await remote(server)
    server._audio_fft_scope = object()
    captured = []

    class Handler:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self):
            captured.append(server._controller.capture())

    monkeypatch.setattr(server_module, name, Handler)
    writer = Writer()
    await server._handle_websocket(
        asyncio.StreamReader(),
        writer,
        path,
        {
            "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
            "sec-websocket-protocol": "unused,rigplane-controller-v1." + key,
        },
        {"controller_role": ["auxiliary"]},
    )
    assert writer.status == 101
    assert (
        b"Sec-WebSocket-Protocol: rigplane-controller-v1." + key.encode()
        in writer.buffer
    )
    assert b"Sec-WebSocket-Protocol: unused" not in writer.buffer
    assert captured[0].remote and not captured[0].primary
    assert not server._controller_handler_tasks


async def test_mutable_http_admin_and_raw_transactions_cannot_bypass_lease(station):
    server, _ = station
    key, primary = await remote(server)
    for path in ("/api/v1/commands", "/api/v1/commands/batch", "/api/v1/radio/connect"):
        assert (await request(server, "POST", path, {})).status == 403
    assert (
        await request(
            server, "PUT", "/api/v1/managed-transmit/tot", {"seconds": 30}, key
        )
    ).status == 403
    assert (await request(server, "GET", "/api/v1/state")).status == 200
    radio = SimpleNamespace(send_civ_transaction=AsyncMock())
    server._radio = radio
    with (
        server._controller.bind(primary),
        pytest.raises(ControllerError, match="controller_tx_unsupported"),
    ):
        await _HttpCommandExecutor(server).execute(
            CommandIntent("raw-test", "raw_civ_transaction", {}, "http")
        )
    radio.send_civ_transaction.assert_not_awaited()


@pytest.mark.parametrize(
    "name,params",
    [
        ("send_civ", {}),
        ("send_cw_text", {"text": "test"}),
        ("set_vox", {"on": True}),
        ("set_break_in", {"mode": 2}),
        ("set_tuner_status", {"value": 2}),
    ],
)
async def test_remote_unsupported_tx_is_rejected_before_enqueue(station, name, params):
    server, _ = station
    _, primary = await remote(server)
    radio = SimpleNamespace(_controller_authority=server._controller)
    handler = ControlHandler(SimpleNamespace(), radio, "test", "IC-7610", server=server)
    with server._controller.bind(primary), pytest.raises(ControllerError):
        await handler._enqueue_command(name, params)
    assert len(server.command_queue) == 0


async def test_remote_blocks_local_rigctld_but_preserves_safety_off(station):
    server, _ = station
    await remote(server)
    radio = make_mock_radio()
    radio._controller_authority = server._controller
    handler = RigctldHandler(radio, RigctldConfig())
    response = await handler.execute(set_cmd("set_freq", "14074000"))
    assert response.error == HamlibError.EACCESS
    radio.set_frequency.assert_not_awaited()
    await handler.execute(set_cmd("set_ptt", "0"))
    radio.set_ptt.assert_awaited_once_with(False)


def audio_handler(server, radio, ticket, *, track=None):
    source = object()
    return AudioHandler(
        SimpleNamespace(send_text=AsyncMock(), send_binary=AsyncMock()),
        radio,
        None,
        tx_gate=lambda: server._controller_audio_gate(ticket, source),
        controller=server._controller,
        controller_ticket=ticket,
        controller_audio_source=source,
        controller_audio_loss=server._controller_audio_loss,
        controller_track_stop=track or server._controller_track_audio_stop,
    )


async def test_audio_requires_matching_actual_ptt_owner_and_one_writer(station):
    server, managed = station
    key, primary = await remote(server)
    ticket = server._controller.attach(key, "auxiliary", "audio")
    radio = _SessionLanRadio()
    handler = audio_handler(server, radio, ticket)
    other = audio_handler(server, radio, ticket)
    await _start_tx(handler)
    await _start_tx(other)
    assert handler._tx_active and not other._tx_active
    await handler._handle_tx_audio(_pcm_tx_frame(b"before-key"))
    assert radio.pushed == []
    with server._controller.bind(primary):
        assert await managed.ptt_down("native") is ManagedTxOutcome.ACCEPTED
    await handler._handle_tx_audio(_pcm_tx_frame(b"current"))
    assert radio.pushed == [b"current"]
    await managed.ptt_up("native")
    aux = server._controller.attach(key, "auxiliary", "frontend")
    server._controller.register_control(aux)
    with server._controller.bind(aux):
        await managed.ptt_down("frontend")
    await handler._handle_tx_audio(_pcm_tx_frame(b"wrong-source"))
    assert radio.pushed == [b"current"]
    await handler._stop_tx(reason="disconnect")
    assert (await managed.snapshot()).state.intent.kind.value == "rx"


async def test_audio_cleanup_timeout_retains_owner_and_late_success_releases_it(
    station,
):
    server, managed = station
    key, primary = await remote(server)
    ticket = server._controller.attach(key, "auxiliary", "audio")
    gate = asyncio.Event()

    async def slow_stop(stop):
        await gate.wait()
        await stop

    handler = audio_handler(
        server,
        _SessionLanRadio(),
        ticket,
        track=lambda stop: server._controller_track_audio_stop(slow_stop(stop)),
    )
    await _start_tx(handler)
    with server._controller.bind(primary):
        await managed.ptt_down("native")
    with pytest.raises(TimeoutError):
        await handler._stop_tx(reason="lost", timeout=0.001)
    assert server._controller_audio_cleanup
    assert all(not task.cancelled() for task in server._controller_audio_cleanup)
    with pytest.raises(ControllerError, match="controller_audio_busy"):
        server._controller.claim_audio(ticket, object())
    with server._controller.bind(primary):
        assert await managed.ptt_down("native") is ManagedTxOutcome.REJECTED
    gate.set()
    await asyncio.gather(*server._controller_audio_cleanup)
    await asyncio.sleep(0)
    assert not server._controller_audio_cleanup
    server._controller.claim_audio(ticket, object())
    with server._controller.bind(primary):
        assert await managed.ptt_down("native") is ManagedTxOutcome.ACCEPTED


@pytest.mark.parametrize("result", [False, 0, 1, "error"])
async def test_refused_or_failed_close_never_claims_ready_rx(station, result):
    server, _ = station
    key, _ = await remote(server)

    async def close():
        if result == "error":
            raise OSError("test close refusal")
        return result

    stop = server._controller_track_audio_stop(close())
    await asyncio.gather(stop, return_exceptions=True)
    await asyncio.sleep(0)
    await server._controller.settle()
    assert server._controller.status()["state"] == "blocked"
    assert server._controller_audio_cleanup
    assert (
        await request(server, "POST", "/api/v1/controller", {"kind": "browser"})
    ).status == 503
    with pytest.raises(ControllerError):
        server._controller.credential(key)


async def test_provider_and_primary_loss_fence_before_cleanup_and_no_old_replay(
    station,
):
    server, _ = station
    local = server._controller.capture()
    server._on_provider_generation(8)
    with pytest.raises(ControllerError):
        server._controller.validate(local)
    key, primary = await remote(server)
    server._controller.disconnect_control("native")
    with pytest.raises(ControllerError):
        server._controller.validate(primary)
    await server._controller.settle()
    fresh, current = await remote(server)
    source = object()
    audio = server._controller.attach(fresh, "auxiliary", "fresh-audio")
    server._controller.claim_audio(audio, source)
    assert not server._controller.release_audio(primary, source)
    server._controller.detach(primary)
    server._controller.validate(current)
    server._on_provider_generation(9)
    with pytest.raises(ControllerError):
        server._controller.credential(key)
    with pytest.raises(ControllerError):
        server._controller.validate(current)
