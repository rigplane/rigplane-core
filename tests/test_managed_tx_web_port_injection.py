"""Composition-owned managed-TX port reaches every Web control seat by identity."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import rigplane.web.handlers as handlers_module
import rigplane.web.server as server_module
from rigplane.web.server import WebConfig, WebServer


class _ControlHandlerCapture:
    ports: list[object | None] = []

    def __init__(
        self,
        *args: object,
        managed_tx_port: object | None = None,
        **kwargs: object,
    ) -> None:
        self.ports.append(managed_tx_port)

    async def _enqueue_command(
        self, name: str, params: dict[str, object]
    ) -> dict[str, object]:
        return {"name": name, **params}

    async def run(self) -> None:
        return None


class _Writer:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def get_extra_info(self, name: str, default: object = None) -> object:
        if name == "peername":
            return ("127.0.0.1", 12345)
        return default


class _WebSocket:
    def __init__(self, *args: object, **kwargs: object) -> None:
        return None

    async def keepalive_loop(self, interval: float) -> None:
        await asyncio.Event().wait()


async def _cw_request(server: WebServer, path: str, body: bytes) -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(body)
    reader.feed_eof()
    await server._handle_radio_control(  # noqa: SLF001
        path,
        _Writer(),
        {"content-length": str(len(body))},
        reader,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("port", [object(), None], ids=["managed", "unmanaged"])
async def test_control_seats_receive_the_composed_port_identity(
    monkeypatch: pytest.MonkeyPatch, port: object | None
) -> None:
    """WS, HTTP, CW and WebRTC never construct or substitute the port."""
    _ControlHandlerCapture.ports = []
    monkeypatch.setattr(server_module, "ControlHandler", _ControlHandlerCapture)
    monkeypatch.setattr(handlers_module, "ControlHandler", _ControlHandlerCapture)
    monkeypatch.setattr(server_module, "WebSocketConnection", _WebSocket)

    radio = SimpleNamespace(capabilities=set())
    server = WebServer(radio, WebConfig(radio_model="IC-7300"))
    server._production_managed_tx_port = port  # noqa: SLF001

    # Generic HTTP command handler construction.
    server._control_handler_for()  # noqa: SLF001

    # The WebSocket construction seat.
    await server._handle_websocket(  # noqa: SLF001
        asyncio.StreamReader(),
        _Writer(),
        "/api/v1/ws",
        {"sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ=="},
        {},
    )

    # The dedicated CW send and stop construction seats.
    await _cw_request(server, "/api/v1/radio/cw/send", b'{"text":"CQ"}')
    await _cw_request(server, "/api/v1/radio/cw/stop", b"{}")

    # WebRTC retains the exact port passed by the server, then injects it.
    manager = server._webrtc_session_manager()  # noqa: SLF001
    assert manager._managed_tx_port is port  # noqa: SLF001
    session = SimpleNamespace(pc=object(), tasks=[])
    manager._dispatch_channel(session, SimpleNamespace(label="control"))  # noqa: SLF001
    await asyncio.gather(*session.tasks)

    assert _ControlHandlerCapture.ports == [port, port, port, port, port]
