"""`station --webrtc` turns on the WebRTC answerer exactly as `web --webrtc` does.

RigPlane Pro's home role launches a managed `station` child with
`--webrtc` so the home station can answer remote offers. The flag is one
shared parser definition for `web` and `station`; these tests drive the
real `_cmd_web` path with a real `WebServer` bound on its managed
loopback default and POST to the offer route over TCP.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import ipaddress
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.cli import _build_parser, _cmd_web
from rigplane.web.server import WebServer
from rigplane.web.transport.webrtc import webrtc_available

_webrtc = pytest.mark.skipif(
    not webrtc_available(), reason="aiortc extra not installed"
)


def _probe_core_station_accepts_webrtc() -> bool:
    """Mirror of Pro's `core_contract.station_accepts_webrtc` probe.

    Pro builds the parser with `rigplane.cli._build_parser`, parses this
    argv with stderr captured, and reads `webrtc_enabled is True`.
    """
    parser = _build_parser()
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            parsed = parser.parse_args(
                ["station", "--listen", "127.0.0.1", "--port", "0", "--webrtc"]
            )
        except SystemExit:
            return False
    return getattr(parsed, "webrtc_enabled", False) is True


def test_pro_station_webrtc_probe_sees_the_flag() -> None:
    assert _probe_core_station_accepts_webrtc() is True


@pytest.mark.parametrize("command", ["web", "station"])
def test_webrtc_flags_parse_identically_on_web_and_station(command: str) -> None:
    p = _build_parser()
    base = ["--host", "1.2.3.4", command]
    off = p.parse_args(base)
    assert off.webrtc_enabled is False
    assert off.webrtc_include_vpn is False
    on = p.parse_args([*base, "--webrtc", "--webrtc-include-vpn"])
    assert on.webrtc_enabled is True
    assert on.webrtc_include_vpn is True


class _FakeRigctldServer:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


async def _post_offer(port: int) -> tuple[int, dict[str, Any]]:
    """POST an SDP-less offer: 400 missing_sdp means the answerer gate is open."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        body = b"{}"
        writer.write(
            b"POST /api/v1/transport/webrtc/offer HTTP/1.1\r\n"
            + f"Host: 127.0.0.1:{port}\r\n".encode()
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=10)
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
    head, _, payload = raw.partition(b"\r\n\r\n")
    status = int(head.split(b"\r\n", 1)[0].split(b" ", 2)[1])
    return status, json.loads(payload) if payload else {}


async def _run_station(argv: list[str]) -> dict[str, Any]:
    """Run `_cmd_web` for a parsed `station` argv; probe the bound server."""
    observed: dict[str, Any] = {}

    class _ProbingWebServer(WebServer):
        def __init__(self, _radio: object, config: Any) -> None:
            # The config `_cmd_web` built is what is under test; the radio
            # is dropped so the real server runs radioless.
            super().__init__(None, config)

        async def serve_forever(self, **kwargs: Any) -> None:
            await self.start()
            try:
                assert self._server is not None
                observed["bound"] = [
                    sock.getsockname()[0] for sock in self._server.sockets
                ]
                observed["offer"] = await _post_offer(self.port)
            finally:
                await self.stop()
            raise asyncio.CancelledError

    radio = AsyncMock()
    radio.model = "IC-7610"
    args: argparse.Namespace = _build_parser().parse_args(argv)
    with (
        patch("rigplane.web.server.WebServer", _ProbingWebServer),
        patch("rigplane.rigctld.server.RigctldServer", _FakeRigctldServer),
    ):
        assert await _cmd_web(radio, args) == 0
    return observed


_STATION = ["--host", "1.2.3.4", "station", "--port", "0", "--no-discovery"]


@_webrtc
@pytest.mark.asyncio
async def test_station_webrtc_answerer_available_on_loopback_only() -> None:
    observed = await _run_station([*_STATION, "--webrtc"])
    status, body = observed["offer"]
    assert (status, body.get("code")) == (400, "missing_sdp"), (status, body)
    assert observed["bound"]
    assert all(ipaddress.ip_address(host).is_loopback for host in observed["bound"])


@pytest.mark.asyncio
async def test_station_without_webrtc_flag_has_no_answerer() -> None:
    observed = await _run_station(list(_STATION))
    status, body = observed["offer"]
    assert (status, body.get("code")) == (503, "webrtc_unavailable"), (status, body)
