from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_managed_tx_production_fake_wire import (
    _IcomActuator,
    _drain_managed_positive_queue,
)
from test_web_controller import request
from test_web_server import _read_http_response, _ws_recv_frame, _ws_send_text

from rigplane.core.state_pipeline_contracts import Observation, SourceMetadata
from rigplane.core.tx_observation import OBSERVED_PTT_PATH, ObservedPtt
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.controller_authority import ControllerError
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind, ManagedTxOutcome
from rigplane.web import server as server_module
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import (
    _validate_managed_tx,
    attach_managed_tx_composition,
)
from rigplane.web.websocket import make_accept_key


def observe_ptt(server: WebServer, value: ObservedPtt) -> None:
    server.command_state_store.apply_current(
        Observation(
            path=OBSERVED_PTT_PATH,
            value=value,
            source=SourceMetadata(source="poll_response", provider="fake_wire"),
            timestamp_monotonic=asyncio.get_running_loop().time(),
            max_age=60,
        )
    )


async def joined_station(
    tmp_path: Path, *, controller_conflict: bool = False, attach: bool = True
):
    radio = _IcomActuator()
    radio.release_on.set()
    profile = resolve_radio_profile(model="IC-9700")
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio.connected = True
    radio.radio_ready = True

    async def raw_set_ptt(on: bool) -> None:
        radio.raw_ptt.append(on)

    radio.set_ptt = raw_set_ptt
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    if controller_conflict:
        radio._controller_authority = object()
    server = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, discovery=False, keepalive_interval=9999.0),
    )
    server.command_queue.bind_connection_generation(lambda: radio)
    await composition.transport_ready(radio)
    await composition.bind_state_store(server.command_state_store)
    if attach:
        attach_managed_tx_composition(server, composition)
        assert _validate_managed_tx(server) is composition
    observe_ptt(server, ObservedPtt.OFF)
    return radio, composition, server


@pytest.fixture
async def production_station(tmp_path):
    radio, composition, server = await joined_station(tmp_path)
    try:
        yield radio, composition, server
    finally:
        server._controller.revoke()
        await server._controller.settle()
        await composition.shutdown(asyncio.Event())


async def test_complete_production_composition_admits_host_remote_and_fences_old_input(
    production_station,
):
    radio, composition, server = production_station
    controller = server._controller
    local = controller.capture()
    assert server._controller_installed
    assert radio._controller_authority is controller
    assert (await request(server, "GET", "/api/v1/controller")).payload[
        "mode"
    ] == "local"
    assert True not in radio.wire
    with pytest.raises(RuntimeError, match="raw PTT ON is blocked"):
        await radio.set_ptt(True)
    assert radio.raw_ptt == []
    nonhost = await request(
        server,
        "POST",
        "/api/v1/controller/mode",
        {"mode": "remote"},
        peer="192.0.2.1",
    )
    assert nonhost.status == 403
    assert nonhost.payload == {"error": "controller_local_required"}
    armed = await request(server, "POST", "/api/v1/controller/mode", {"mode": "remote"})
    assert armed.status == 200
    acquired = await request(server, "POST", "/api/v1/controller", {"kind": "pro"})
    assert acquired.status == 201
    key = acquired.payload["controller_key"]
    with pytest.raises(ControllerError, match="controller_invalid"):
        controller.validate(local)
    primary = controller.attach(key, "primary", "memory-primary")
    controller.register_control(primary)
    audio = controller.attach(key, "auxiliary", "memory-audio")
    source = object()
    controller.claim_audio(audio, source)
    with pytest.raises(ControllerError, match="controller_audio_busy"):
        controller.claim_audio(audio, object())
    duplicate = await request(server, "POST", "/api/v1/controller", {"kind": "browser"})
    assert duplicate.status == 409
    assert True not in radio.wire
    with controller.bind(primary):
        pressed = await composition.authority.submit_ptt(True, "memory-primary")
    assert pressed.outcome is ManagedTxOutcome.ACCEPTED
    await pressed.wait_settlement()
    assert radio.wire[-1] is True
    assert await server._controller_audio_gate(audio, source)
    ready = asyncio.get_running_loop().create_future()
    with controller.bind(primary):
        delayed = composition.authority.start_ptt_submission(
            True, "memory-primary", ready=ready
        )
    controller.disconnect_control("memory-primary")
    with pytest.raises(ControllerError, match="controller_invalid"):
        controller.validate(primary)
    await controller.settle()
    assert radio.wire[-1] is False
    assert (
        await composition.authority.snapshot()
    ).state.intent.kind is ManagedTxIntentKind.RX
    assert not await server._controller_audio_gate(audio, source)
    before = list(radio.wire)
    fresh = await request(server, "POST", "/api/v1/controller", {"kind": "browser"})
    assert fresh.status == 201
    assert fresh.payload["controller_key"] != key
    assert fresh.payload["generation"] > acquired.payload["generation"]
    ready.set_result(None)
    await asyncio.wait_for(asyncio.gather(delayed, return_exceptions=True), 2)
    controller.detach(primary)
    assert radio.wire == before
    assert radio.raw_ptt == []
    released = await request(
        server, "DELETE", "/api/v1/controller", key=fresh.payload["controller_key"]
    )
    assert released.status == 200


@pytest.mark.parametrize(
    "inhibitor",
    [
        "read_only",
        "stopping",
        "webrtc",
        "legacy_bridge",
        "native_inflight",
        "native_open",
        "native_stop",
        "native_cleanup",
        "audio_cleanup",
        "old_handler",
        "observed_on",
        "observed_unknown",
        "provider_unavailable",
        "managed_active",
    ],
)
async def test_production_remote_admission_refuses_readiness_inhibitor(
    production_station, monkeypatch, inhibitor
):
    radio, composition, server = production_station
    if inhibitor == "read_only":
        monkeypatch.setattr(server._config, "read_only", True)
    elif inhibitor == "legacy_bridge":
        monkeypatch.setattr(
            server, "_audio_bridge", SimpleNamespace(_drop_queued_tx=lambda: None)
        )
    elif inhibitor in {"stopping", "webrtc"}:
        field = {
            "stopping": "_stopping",
            "webrtc": "_webrtc_sessions",
        }[inhibitor]
        monkeypatch.setattr(
            server, field, True if inhibitor == "stopping" else object()
        )
    elif inhibitor.startswith("native_"):
        pool = server_module.bounded_portaudio_pool
        if inhibitor == "native_inflight":
            monkeypatch.setattr(pool, "inflight", 1)
        else:
            field = {
                "native_open": "_opens",
                "native_stop": "_stops",
                "native_cleanup": "_cleanup_streams",
            }[inhibitor]
            monkeypatch.setattr(pool, field, {1: object()})
    elif inhibitor in {"audio_cleanup", "old_handler"}:
        pending = asyncio.get_running_loop().create_future()
        if inhibitor == "audio_cleanup":
            monkeypatch.setattr(server, "_controller_audio_cleanup", {pending})
        else:
            monkeypatch.setattr(
                server, "_controller_handler_tasks", {object(): pending}
            )
    elif inhibitor in {"observed_on", "observed_unknown"}:
        observe_ptt(
            server,
            ObservedPtt.ON if inhibitor == "observed_on" else ObservedPtt.UNKNOWN,
        )
    elif inhibitor == "provider_unavailable":
        await composition.transport_unavailable(radio)
    else:
        pressed = await composition.authority.submit_ptt(True, "local-fixture")
        await pressed.wait_settlement()
    refused = await request(
        server, "POST", "/api/v1/controller/mode", {"mode": "remote"}
    )
    assert refused.status == 503
    assert refused.payload == {"error": "controller_not_ready"}
    assert server._controller.status()["mode"] == "local"


@pytest.mark.parametrize("missing", ["controller_install", "composition_attachment"])
async def test_production_remote_admission_requires_actual_installation(
    tmp_path, missing
):
    radio, composition, server = await joined_station(
        tmp_path,
        controller_conflict=missing == "controller_install",
        attach=missing != "composition_attachment",
    )
    try:
        if missing == "controller_install":
            assert not server._controller_installed
        else:
            with pytest.raises(RuntimeError, match="composition is not attached"):
                _validate_managed_tx(server)
        refused = await request(
            server, "POST", "/api/v1/controller/mode", {"mode": "remote"}
        )
        assert refused.status == 503
        assert server._controller.status()["mode"] == "local"
        assert True not in radio.wire
    finally:
        await composition.shutdown(asyncio.Event())


class FakeManagedPoller:
    def __init__(self, server: WebServer) -> None:
        self.server = server
        self.authority = None
        self.task = None

    def bind_managed_tx_authority(self, authority) -> None:
        self.authority = authority

    async def start(self) -> None:
        self.task = asyncio.current_task()
        await _drain_managed_positive_queue(self.server)

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


async def loopback_http(server: WebServer, method: str, path: str, body=None):
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", server.port), 3
    )
    try:
        payload = b"" if body is None else json.dumps(body).encode()
        writer.write(
            (
                f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
            ).encode()
            + payload
        )
        await asyncio.wait_for(writer.drain(), 3)
        status, _, response = await asyncio.wait_for(_read_http_response(reader), 3)
        return status, json.loads(response)
    finally:
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), 3)


async def loopback_control(server: WebServer, key: str):
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", server.port), 3
    )
    nonce = "dGhlIHNhbXBsZSBub25jZQ=="
    protocol = f"rigplane-controller-v1.{key}"
    try:
        writer.write(
            (
                f"GET /api/v1/ws HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {nonce}\r\nSec-WebSocket-Version: 13\r\n"
                f"Sec-WebSocket-Protocol: {protocol}\r\n\r\n"
            ).encode()
        )
        await asyncio.wait_for(writer.drain(), 3)
        response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
        assert response.startswith(b"HTTP/1.1 101 ")
        assert make_accept_key(nonce).encode() in response
        assert f"Sec-WebSocket-Protocol: {protocol}\r\n".encode() in response
        return reader, writer
    except BaseException:
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), 3)
        raise


async def loopback_message(reader, *, kind: str, command_id: str | None = None):
    async with asyncio.timeout(3):
        while True:
            _, payload = await _ws_recv_frame(reader, timeout=3)
            message = json.loads(payload)
            if message.get("type") == kind and (
                command_id is None or message.get("id") == command_id
            ):
                return message


async def test_os_loopback_remote_controller_keys_fake_wire_and_releases_on_close(
    production_station,
):
    radio, composition, server = production_station
    poller = FakeManagedPoller(server)

    def create_state_poller(*, callback, command_queue):
        assert command_queue is server.command_queue
        return poller

    radio.create_state_poller = create_state_poller
    writer = None
    try:
        await asyncio.wait_for(server.start(), 5)
        assert server.port > 0
        assert poller.authority is composition.authority
        assert _validate_managed_tx(server) is composition
        status, initial = await loopback_http(server, "GET", "/api/v1/controller")
        assert status == 200 and initial["mode"] == "local"
        assert True not in radio.wire
        status, _ = await loopback_http(
            server, "POST", "/api/v1/controller/mode", {"mode": "remote"}
        )
        assert status == 200
        status, grant = await loopback_http(
            server, "POST", "/api/v1/controller", {"kind": "pro"}
        )
        assert status == 201
        reader, writer = await loopback_control(server, grant["controller_key"])
        hello = await loopback_message(reader, kind="hello")
        assert hello["connected"] is True and hello["radio_ready"] is True
        await asyncio.wait_for(
            _ws_send_text(writer, json.dumps({"type": "controller_heartbeat"})), 3
        )
        heartbeat = await loopback_message(reader, kind="controller_heartbeat")
        assert heartbeat["generation"] == grant["generation"]
        before = list(radio.wire)
        assert True not in before
        await asyncio.wait_for(
            _ws_send_text(
                writer,
                json.dumps(
                    {"type": "cmd", "id": "key", "name": "ptt_on", "params": {}}
                ),
            ),
            3,
        )
        keyed = await loopback_message(reader, kind="response", command_id="key")
        assert keyed["ok"] is True
        await radio.wait_for_wire([*before, True])
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), 3)
        writer = None
        await radio.wait_for_wire([*before, True, False])
        await asyncio.wait_for(server._controller.settle(), 3)
        assert (
            await composition.authority.snapshot()
        ).state.intent.kind is ManagedTxIntentKind.RX
        status, renewed = await loopback_http(
            server, "POST", "/api/v1/controller", {"kind": "browser"}
        )
        assert status == 201
        assert renewed["controller_key"] != grant["controller_key"]
        assert renewed["generation"] > grant["generation"]
        assert radio.wire == [*before, True, False]
        assert radio.raw_ptt == []
    finally:
        try:
            if writer is not None:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), 3)
        finally:
            try:
                await asyncio.wait_for(server.stop(), 10)
            finally:
                await asyncio.wait_for(server._controller.settle(), 3)
    assert server._server is None
