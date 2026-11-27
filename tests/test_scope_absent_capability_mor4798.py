"""MOR-4798: the FTX-1 serves no hardware spectrum scope over HTTP.

The FTX-1 profile declares no ``scope`` capability tag, so the served
``GET /api/v1/capabilities`` document — the panel's contract (``scope``,
``scopeSources``, ``scopeDefaultSourceId``, ``span_choices_hz`` at the
payload root, ``src/rigplane/web/server.py::_serve_capabilities``) —
reports ``scope is False`` with no ``"radio"`` hardware scope source, and
the ``GET /api/v1/info`` document — ``model`` at its root, ``hasSpectrum``
and ``tags`` inside its ``capabilities`` object
(``src/rigplane/web/server.py::_serve_info``) — agrees. The IC-7300 is the
control: it publishes a hardware ``"radio"`` scope source on both
endpoints.

Both cases drive the real public API path: a real ``WebServer`` on a real
socket wrapping the real backend object (constructed, never connected —
the transport is a class-level fact of the backend), asserting on the
served bodies over the wire, never on profile dict fields or helper
output. This mirrors
``tests/test_mor4437_transport_capabilities.py`` (the nearest existing
seam test), whose serial-G7250 case pins the same no-``"radio"`` shape.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from rigplane.backends.ic7300.serial import Ic7300SerialRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.web.server import WebConfig, WebServer


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


async def _served_docs(radio: Any) -> dict[str, dict[str, Any]]:
    """Serve both capability endpoints from a real WebServer, no lifecycle.

    Binds ``_accept_client`` directly (the
    ``test_mor4437_transport_capabilities`` seam): no ``start()`` poller
    threads, so an unconnected backend is enough — connection state is a
    served fact, not a serving precondition.
    """
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)
    try:
        host, port = web._server.sockets[0].getsockname()
        docs: dict[str, dict[str, Any]] = {}
        for path in ("/api/v1/capabilities", "/api/v1/info"):
            status, body = await _http_get(host, port, path)
            assert status == 200, path
            docs[path] = json.loads(body)
        return docs
    finally:
        web._server.close()
        await web._server.wait_closed()
        web._server = None
        tasks = tuple(web._client_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_ftx1_publishes_no_scope_source_over_http() -> None:
    """FTX-1 manuals/CAT: no spectrum scope command exists, so the served
    documents carry ``scope is False`` and no ``"radio"`` hardware scope
    source of any kind."""
    docs = await _served_docs(YaesuCatRadio("/dev/null", profile="ftx1"))

    capabilities = docs["/api/v1/capabilities"]
    assert capabilities["model"] == "FTX-1"
    assert capabilities["scope"] is False
    assert "scope" not in capabilities["capabilities"]
    assert "radio" not in capabilities["scopeSources"]
    assert capabilities["scopeDefaultSourceId"] != "radio"
    assert capabilities["span_choices_hz"] == []

    info = docs["/api/v1/info"]
    assert info["model"] == "FTX-1"
    assert info["capabilities"]["hasSpectrum"] is False
    assert "scope" not in info["capabilities"]["tags"]


async def test_scope_capable_radio_publishes_a_scope_source_over_http() -> None:
    """Control: the IC-7300 publishes a hardware scope source on both
    endpoints."""
    radio = Ic7300SerialRadio(device="/dev/null", model="IC-7300")
    docs = await _served_docs(radio)

    capabilities = docs["/api/v1/capabilities"]
    assert capabilities["model"] == "IC-7300"
    assert capabilities["scope"] is True
    assert "scope" in capabilities["capabilities"]
    assert "radio" in capabilities["scopeSources"]
    assert capabilities["scopeDefaultSourceId"] == "radio"
    assert capabilities["span_choices_hz"] == list(
        radio.profile.scope_span_view_widths_hz
    )

    info = docs["/api/v1/info"]
    assert info["model"] == "IC-7300"
    assert info["capabilities"]["hasSpectrum"] is True
    assert "scope" in info["capabilities"]["tags"]
