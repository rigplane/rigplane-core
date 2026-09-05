"""Application-token retirement contracts (MOR-2361)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.web import server as web_server
from rigplane.web.server import WebConfig, WebServer


class _MemoryWriter:
    """Minimal asyncio.StreamWriter stand-in that captures written bytes."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:  # pragma: no cover - trivial
        return

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:  # pragma: no cover
        return

    def is_closing(self) -> bool:
        return self.closed

    def get_extra_info(self, *_args: Any, **_kwargs: Any) -> Any:
        return ("127.0.0.1", 0)



@pytest.mark.parametrize("token", ["retired-private-value", " ", "unicode-λ"])
def test_nonempty_legacy_config_is_rejected_without_disclosure(token):
    with pytest.raises(ValueError) as exc:
        WebConfig(auth_token=token)
    assert str(exc.value) == "Application authentication was removed; auth_token must be empty."


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "Bearer wrong", "wrong", "Bearer unicode-λ"])
@pytest.mark.parametrize("path", ["/api/v1/info", "/api/v1/state", "/api/v1/capabilities", "/api/v1/runtime", "/api/v1/station"])
async def test_http_dispatch_needs_no_application_token(header, path):
    srv = WebServer(radio=None, config=WebConfig())
    # The retained compatibility attribute cannot reactivate the retired gate.
    srv._config.auth_token = "retired-private-value"
    writer = _MemoryWriter()
    headers = {"authorization": header} if header is not None else {}
    await srv._handle_http(writer, "GET", path, headers)
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 200 ")
    assert b"retired-private-value" not in writer.buffer
    assert b'"authRequired": true' not in writer.buffer


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/ws", "/api/v1/scope", "/api/v1/audio-scope", "/api/v1/audio"])
@pytest.mark.parametrize("legacy_token", [False, True])
async def test_all_ws_channels_upgrade_without_application_auth(monkeypatch, path, legacy_token):
    srv = WebServer(radio=None, config=WebConfig())
    srv._config.auth_token = "retired-private-value"
    srv._audio_fft_scope = object()
    handlers = {}
    for name in ("ControlHandler", "ScopeHandler", "AudioHandler"):
        factory = MagicMock(return_value=MagicMock(run=AsyncMock()))
        monkeypatch.setattr(web_server, name, factory)
        handlers[name] = factory
    writer = _MemoryWriter()
    headers = {"sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ=="}
    if legacy_token:
        headers["authorization"] = "Bearer wrong"
    await srv._handle_websocket(
        asyncio.StreamReader(), writer, path, headers,
        {"token": ["wrong"]} if legacy_token else {},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 101 ")
    expected = "ControlHandler" if path.endswith("/ws") else "AudioHandler" if path.endswith("/audio") else "ScopeHandler"
    handlers[expected].return_value.run.assert_awaited_once()
    assert sum(factory.call_count for factory in handlers.values()) == 1


@pytest.mark.asyncio
async def test_ws_still_rejects_missing_key():
    srv = WebServer(radio=None, config=WebConfig())
    writer = _MemoryWriter()
    await srv._handle_websocket(asyncio.StreamReader(), writer, "/api/v1/ws", {})
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 400 ")


@pytest.mark.asyncio
@pytest.mark.parametrize("path,reason", [("/api/v1/unknown", "unknown channel"), ("/api/v1/audio-scope", "audio FFT scope not available")])
async def test_ws_still_refuses_unavailable_channels(monkeypatch, path, reason):
    srv = WebServer(radio=None, config=WebConfig())
    writer = _MemoryWriter()
    ws = MagicMock(close=AsyncMock())
    monkeypatch.setattr(web_server, "WebSocketConnection", MagicMock(return_value=ws))
    await srv._handle_websocket(
        asyncio.StreamReader(), writer, path,
        {"sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ=="},
    )
    ws.close.assert_awaited_once_with(1008, reason)
