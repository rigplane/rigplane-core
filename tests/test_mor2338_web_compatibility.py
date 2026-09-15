"""MOR-2336 WEB1–4 consumer witnesses for selective v2.11.1 migration."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.web.protocol import decode_json
from rigplane.web.server import WebConfig, WebServer
from test_managed_tx_http_route import _Authority as _HttpAuthority
from test_managed_tx_http_route import _server as _managed_server
from test_web_managed_tx_ingress import _Authority as _WsAuthority
from test_web_managed_tx_ingress import _eof_ws, _handler, _radio


async def _request(
    server: WebServer,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    body = b"" if payload is None else json.dumps(payload).encode()
    listener = await asyncio.start_server(
        server._handle_connection,
        "127.0.0.1",
        0,  # noqa: SLF001
    )
    async with listener:
        port = listener.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(
                (
                    f"{method} {path} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode()
                + body
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), timeout=5)
        finally:
            writer.close()
            await writer.wait_closed()
    headers, data = response.split(b"\r\n\r\n", 1)
    return int(headers.split(b" ", 2)[1]), json.loads(data)


def _state_server(model: str = "IC-7610") -> WebServer:
    return WebServer(None, WebConfig(host="127.0.0.1", port=0, radio_model=model))


def _notch(server: WebServer, receiver: str, value: int) -> None:
    server.command_state_store.apply_current(
        Observation(
            path=FieldPath.receiver(receiver, "operator_controls", "notch_filter"),
            value=value,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=time.monotonic(),
            max_age=None,
        )
    )


@pytest.mark.asyncio
async def test_released_root_fields_migrate_to_explicit_receiver_state() -> None:
    server = _state_server()
    _notch(server, "main", 37)
    _notch(server, "sub", 192)

    status, state = await _request(server, "GET", "/api/v1/state")

    assert status == 200
    assert "notchFilter" not in state
    assert "txFreqMonitor" not in state
    assert state["main"]["notchFilter"] == 37
    assert state["sub"]["notchFilter"] == 192
    for receiver in ("main", "sub"):
        field = state["fieldStatus"][f"{receiver}.notchFilter"]
        assert field["observed"] is True
        assert field["availability"] == "available"
    assert state["txTarget"] == {"status": "unknown", "reason": "not-observed"}


@pytest.mark.asyncio
async def test_unobserved_receiver_default_is_not_observed_notch_telemetry() -> None:
    server = _state_server()
    _notch(server, "main", 37)

    status, state = await _request(server, "GET", "/api/v1/state")

    assert status == 200
    assert state["main"]["notchFilter"] == 37
    assert state["sub"]["notchFilter"] == 0
    field = state["fieldStatus"]["sub.notchFilter"]
    assert field["observed"] is False
    assert field["freshness"] == "unknown"
    assert field["availability"] == "missing"
    assert "notchFilter" not in state
    assert "txFreqMonitor" not in state


@pytest.mark.asyncio
async def test_single_receiver_payload_does_not_invent_sub_receiver() -> None:
    server = _state_server("IC-7300")
    _notch(server, "main", 37)

    status, state = await _request(server, "GET", "/api/v1/state")

    assert status == 200
    assert state["main"]["notchFilter"] == 37
    assert "sub" not in state
    assert "sub.notchFilter" not in state["fieldStatus"]
    assert "notchFilter" not in state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "params"),
    (
        ("ptt", {"state": True}),
        ("ptt", {"state": False}),
        ("ptt_on", {}),
        ("ptt_off", {}),
    ),
    ids=("ptt-on", "ptt-off", "on-alias", "off-alias"),
)
async def test_released_http_ptt_family_requires_explicit_migration(
    name: str, params: dict[str, Any]
) -> None:
    radio = _radio()
    authority = _WsAuthority()
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    server._production_managed_tx_port = SimpleNamespace(authority=authority)  # noqa: SLF001

    status, result = await _request(
        server, "POST", "/api/v1/commands", {"name": name, "params": params}
    )

    assert status == 409
    assert result == {
        "ok": False,
        "error": "unsupported_command",
        "message": "momentary PTT requires a stable WebSocket owner; "
        "use managed force_off for unconditional release",
    }
    assert authority.calls == []
    assert authority.force_off_calls == 0
    assert not server.command_queue.drain()
    radio.set_ptt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("transmit_on", "force_off"))
async def test_explicit_latched_http_contract_reports_admission_only(
    operation: str,
) -> None:
    authority = _HttpAuthority()
    server = _managed_server(authority)

    status, result = await _request(
        server,
        "POST",
        "/api/v1/managed-transmit/command",
        {"operation": operation},
    )

    assert status == 202
    assert result == {"ok": True, "operation": operation, "result": "accepted"}
    assert authority.calls == [operation]
    assert len(authority.submissions) == 1
    assert authority.submissions[0].settlement_waits == 0


@pytest.mark.asyncio
async def test_momentary_ws_commands_keep_owner_and_release_on_disconnect() -> None:
    authority = _WsAuthority()
    ws = _eof_ws()
    handler, _, radio = _handler(authority, ws=ws)

    for command_id, on in (("press", True), ("release", False), ("press-again", True)):
        await handler._dispatch_command(command_id, "ptt", {"state": on})  # noqa: SLF001
        result = decode_json(ws.send_text.await_args.args[0])
        assert result["id"] == command_id
        assert result["ok"] is True
        assert result["result"] == {"state": on}
        assert authority.intent == ("ptt:websocket-test" if on else "rx")
    await handler.run()

    assert authority.calls == [
        (True, "websocket-test"),
        (False, "websocket-test"),
        (True, "websocket-test"),
    ]
    assert authority.disconnect_calls == ["websocket-test"]
    assert authority.intent == "rx"
    assert authority.force_off_calls == 0
    radio.set_ptt.assert_not_awaited()


@pytest.mark.asyncio
async def test_released_get_metadata_and_ws_state_envelope_are_consumable() -> None:
    server = _state_server()
    _notch(server, "main", 37)
    status, info = await _request(server, "GET", "/api/v1/info")
    assert status == 200
    assert {
        "server",
        "version",
        "proto",
        "radio",
        "model",
        "capabilities",
        "connection",
    } <= info.keys()
    status, state = await _request(server, "GET", "/api/v1/state")
    assert status == 200
    assert isinstance(state["revision"], int)
    assert isinstance(state["updatedAt"], str)

    ws = SimpleNamespace(send_text=AsyncMock())
    handler, _, _ = _handler(None, ws=ws)
    handler._server = server  # noqa: SLF001
    await handler._send_state_snapshot()  # noqa: SLF001
    event = handler._event_queue.get_nowait()  # noqa: SLF001
    await handler._send_json(event)  # noqa: SLF001
    envelope = decode_json(ws.send_text.await_args.args[0])
    assert envelope["type"] == "state_update"
    assert envelope["data"]["type"] == "full"
    assert envelope["data"]["data"]["main"]["notchFilter"] == 37
    assert envelope["data"]["data"]["revision"] == state["revision"]
    assert "txFreqMonitor" not in envelope["data"]["data"]
