"""MOR-4798: the FTX-1 serves no spectrum scope over CAT.

The FTX-1 profile declares no ``scope`` capability tag, so both capability
endpoints must publish ``scope is False`` with no scope source of any kind —
the exact fact the scope panel reads to say so plainly instead of the generic
"reconnecting" overlay. The IC-7300 is the control: it publishes a hardware
scope source.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rigplane.rig_loader import load_rig
from rigplane.web.server import WebConfig, WebServer

_RIGS_DIR = Path(__file__).resolve().parents[1] / "rigs"


def _addr(server: WebServer) -> tuple[str, int]:
    assert server._server is not None
    return server._server.sockets[0].getsockname()


async def _http_get(host: str, port: int, path: str) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        header_bytes = (await reader.readuntil(b"\r\n\r\n"))[:-4]
        lines = header_bytes.decode("ascii", errors="replace").split("\r\n")
        status = int(lines[0].split(" ", 2)[1])
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
        raw_length = headers.get("content-length")
        body = (
            await reader.readexactly(int(raw_length))
            if raw_length is not None
            else await reader.read()
        )
        return status, body
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _capabilities_by_endpoint(rig_file: str) -> dict[str, dict[str, Any]]:
    profile = load_rig(_RIGS_DIR / rig_file).to_profile()
    radio = SimpleNamespace(
        model=profile.model,
        profile=profile,
        capabilities=set(profile.capabilities),
        connected=True,
        control_connected=True,
        radio_ready=True,
    )
    srv = WebServer(
        radio, WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
    )
    await srv.start()
    try:
        host, port = _addr(srv)
        out: dict[str, dict[str, Any]] = {}
        for path in ("/api/v1/capabilities", "/api/v1/info"):
            status, body = await _http_get(host, port, path)
            assert status == 200, path
            payload = json.loads(body)
            out[path] = payload["capabilities"] if path == "/api/v1/info" else payload
        return out
    finally:
        await srv.stop()


async def test_ftx1_publishes_no_scope_source_over_http() -> None:
    """FTX-1 manuals/CAT: no spectrum scope command exists — both endpoints
    say so with ``scope is False`` and no scope source of any kind."""
    served = await _capabilities_by_endpoint("ftx1.toml")
    for path, capabilities in served.items():
        assert capabilities["model"] == "FTX-1", path
        assert capabilities["scope"] is False, path
        assert capabilities.get("scopeSources", []) == [], path
        assert capabilities.get("scopeDefaultSourceId") is None, path


async def test_scope_capable_radio_publishes_a_scope_source_over_http() -> None:
    """Control: the IC-7300 publishes a hardware scope source on both endpoints."""
    served = await _capabilities_by_endpoint("ic7300.toml")
    for path, capabilities in served.items():
        assert capabilities["scope"] is True, path
        assert "radio" in capabilities.get("scopeSources", []), path
        assert capabilities.get("scopeDefaultSourceId") == "radio", path
