"""MOR-3777 C2: radio connect/disconnect has one entry point — REST.

The control WebSocket used to accept ``radio_connect`` / ``radio_disconnect``
messages as a second, parallel path to ``POST /api/v1/radio/{connect,
disconnect}``. Nothing sends them (the core frontend and rigplane-pro both
use REST), so the WS branch is gone. These tests run a real ``WebServer``
over real sockets with a ``SerialMockRadio`` core (no hardware, no TX):

* the WS message types are no longer handled — the radio is untouched, no
  reply is sent, and the session stays usable (``ping`` → ``pong``);
* the REST routes still connect and disconnect the radio.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from serial_stub import SerialMockRadio
from test_web_server import (
    _addr,
    _close_ws,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
    _ws_skip_handshake,
)

from rigplane.web.server import WebConfig, WebServer


@pytest.fixture
async def served_radio() -> Any:
    radio = SerialMockRadio()
    await radio.connect()
    srv = WebServer(
        radio, WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
    )
    await srv.start()
    try:
        yield srv, radio
    finally:
        await srv.stop()


async def _post(server: WebServer, path: str) -> tuple[int, dict[str, Any]]:
    host, port = _addr(server)[:2]
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Length: 0\r\n"
            "Connection: close\r\n\r\n".encode()
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(-1), timeout=10.0)
    finally:
        writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), json.loads(body.decode())


async def _frames_until_pong(reader: asyncio.StreamReader) -> list[dict[str, Any]]:
    """Collect every JSON frame the server sends before the ``pong``."""
    seen: list[dict[str, Any]] = []
    while True:
        _, payload = await _ws_recv_frame(reader, timeout=5.0)
        msg = json.loads(payload)
        if msg.get("type") == "pong":
            return seen
        seen.append(msg)


def _connection_state_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Frames announcing a connection-state change, in either shape.

    The removed WS handlers announced it as an event envelope,
    ``{"type": "event", "event": "connection_state", ...}``; a bare
    ``{"type": "connection_state"}`` is rejected as well.
    """
    return [
        f
        for f in frames
        if f.get("type") == "connection_state"
        or (f.get("type") == "event" and f.get("event") == "connection_state")
    ]


def _replies_to(frames: list[dict[str, Any]], msg_id: str) -> list[dict[str, Any]]:
    return [f for f in frames if f.get("id") == msg_id]


async def test_ws_radio_disconnect_is_not_handled(served_radio: Any) -> None:
    server, radio = served_radio
    host, port = _addr(server)[:2]
    reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
    try:
        await _ws_skip_handshake(reader)
        await _ws_send_text(
            writer, json.dumps({"type": "radio_disconnect", "id": "ws-disc"})
        )
        await _ws_send_text(writer, json.dumps({"type": "ping"}))
        frames = await _frames_until_pong(reader)
    finally:
        await _close_ws(writer)

    # Checked first so a regression fails on the event frame itself.
    assert _connection_state_frames(frames) == []
    assert not [f for f in frames if f.get("type") == "connection_state"]
    assert radio.connected is True
    assert _replies_to(frames, "ws-disc") == []


async def test_ws_radio_connect_is_not_handled(served_radio: Any) -> None:
    server, radio = served_radio
    await radio.disconnect()
    host, port = _addr(server)[:2]
    reader, writer, _ = await _ws_connect(host, port, "/api/v1/ws")
    try:
        await _ws_skip_handshake(reader)
        await _ws_send_text(
            writer, json.dumps({"type": "radio_connect", "id": "ws-conn"})
        )
        await _ws_send_text(writer, json.dumps({"type": "ping"}))
        frames = await _frames_until_pong(reader)
    finally:
        await _close_ws(writer)

    # Checked first so a regression fails on the event frame itself.
    assert _connection_state_frames(frames) == []
    assert not [f for f in frames if f.get("type") == "connection_state"]
    assert radio.connected is False
    assert _replies_to(frames, "ws-conn") == []


async def test_rest_disconnect_then_connect_round_trip(served_radio: Any) -> None:
    server, radio = served_radio
    assert radio.connected is True

    status, body = await _post(server, "/api/v1/radio/disconnect")
    assert (status, body) == (200, {"status": "disconnected"})
    assert radio.connected is False

    status, body = await _post(server, "/api/v1/radio/connect")
    assert (status, body) == (200, {"status": "connecting"})
    assert radio.connected is True
