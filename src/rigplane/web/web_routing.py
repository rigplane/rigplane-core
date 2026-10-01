"""HTTP method/path dispatch for :class:`rigplane.web.server.WebServer`.

Extracted from ``web/server.py`` to keep ``WebServer._handle_http`` as a
thin delegator (issue #1262, Tier 3 wave 4 of #1063).

The dispatch function accesses ``WebServer`` state via the ``server``
argument (clean dependency injection — no module-level state). Handler
implementations themselves stay on :class:`WebServer` and on the
``web/handlers/`` modules; this file owns only the route table.

The module imports :mod:`rigplane.web.server` lazily inside the dispatch
function so unit tests that patch ``rigplane.web.server._send_response``
continue to work — the patch reaches the binding through the module
reference rather than an early ``from .server import _send_response``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from .host_guard import (  # noqa: TID251
    MISDIRECTED_BODY,
    ORIGIN_FORBIDDEN_BODY,
    host_header_allowed,
    same_origin_allowed,
)

if TYPE_CHECKING:
    from .server import WebServer  # noqa: TID251

__all__ = ["dispatch_http_request"]

logger = logging.getLogger(__name__)


async def dispatch_http_request(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    reader: asyncio.StreamReader | None = None,
    query: dict[str, list[str]] | None = None,
) -> None:
    """Route an HTTP request to its handler.

    Mirrors the original :meth:`WebServer._handle_http` body verbatim — the
    method now delegates here so the public API and route semantics are
    preserved.

    An exception that escapes any handler is answered with a 500 and logged
    with its route (MOR-2899): the connection is never dropped silently.
    The 500 body names no internals — any remote client can reach this net.
    """
    # Lazy import: keeps test patches of ``rigplane.web.server._send_response``
    # effective (binding lookup goes through the module).
    from . import server as _server_mod  # noqa: TID251

    try:
        await _dispatch_http_request(
            server, writer, method, path, headers, reader, query
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "web: unhandled exception in HTTP handler for %s %s",
            method,
            _server_mod._redact_token_in_path(path),
        )
        try:
            await _server_mod._send_response(
                writer,
                500,
                "Internal Server Error",
                b'{"error":"internal server error"}',
                {"Content-Type": "application/json"},
            )
        except Exception:
            # The client may already be gone, or a response may already be
            # on the wire; nothing left to deliver.
            logger.debug(
                "web: could not deliver the 500 response for %s",
                _server_mod._redact_token_in_path(path),
                exc_info=True,
            )


async def _dispatch_http_request(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    reader: asyncio.StreamReader | None = None,
    query: dict[str, list[str]] | None = None,
) -> None:
    """The route table itself; called only by :func:`dispatch_http_request`."""
    # Lazy import: keeps test patches of ``rigplane.web.server._send_response``
    # effective (binding lookup goes through the module).
    from . import server as _server_mod  # noqa: TID251

    _send_response = _server_mod._send_response

    # MOR-2880: Host allowlist before any handler, on every HTTP route.
    raw_host = (headers or {}).get("host")
    if not host_header_allowed(raw_host, server._config.allowed_hosts):
        server._log_refused_request("http host", path, headers or {})
        await _send_response(
            writer,
            421,
            "Misdirected Request",
            MISDIRECTED_BODY,
            {"Content-Type": "application/json"},
        )
        return

    # MOR-2881: the same Origin rule as the WebSocket upgrades, on every
    # state-changing request, before any handler. A missing Origin is
    # admitted (non-browser clients; Pro's proxy sends none on its HTTP
    # leg); a present one must be same-origin with the request and its
    # host must pass the Host rule above — or match the explicit trusted
    # public-Origin allowlist (MOR-3108), which never bypasses the Host
    # rule above. GET /clearcache is covered too: it changes client
    # state (Clear-Site-Data wipes the browser's storage for this
    # origin) though it is a GET.
    origin = (headers or {}).get("origin")
    if origin is not None and (
        method in ("POST", "PUT", "PATCH", "DELETE") or path == "/clearcache"
    ):
        scheme = "https" if server._config.tls else "http"
        if not same_origin_allowed(
            origin,
            raw_host,
            scheme,
            server._config.allowed_hosts,
            server._config.trusted_origins,
        ):
            server._log_refused_request("http origin", path, headers or {})
            await _send_response(
                writer,
                403,
                "Forbidden",
                ORIGIN_FORBIDDEN_BODY,
                {"Content-Type": "application/json"},
            )
            return

    if path == "/healthz":
        if method not in ("GET", "HEAD"):
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        await server._serve_health(writer, headers)
        return

    if path == "/readyz":
        if method not in ("GET", "HEAD"):
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        await server._serve_ready(writer, headers)
        return

    # Routes that accept POST/DELETE
    if path in (
        "/api/v1/managed-transmit",
        "/api/v1/managed-transmit/command",
        "/api/v1/managed-transmit/tot",
    ):
        expected_methods = {
            "/api/v1/managed-transmit": ("GET",),
            "/api/v1/managed-transmit/command": ("POST",),
            "/api/v1/managed-transmit/tot": ("PUT",),
        }[path]
        if method not in expected_methods:
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        await server._handle_http_managed_tx(path, writer, headers, reader)
        return

    if path == "/api/v1/bridge":
        if method not in ("GET", "HEAD", "POST", "DELETE"):
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        await server._handle_bridge(method, writer)
        return
    if path in (
        "/api/v1/radio/disconnect",
        "/api/v1/radio/connect",
        "/api/v1/radio/power",
        "/api/v1/radio/cw/send",
        "/api/v1/radio/cw/stop",
        "/api/v1/commands",
        "/api/v1/commands/batch",
        "/api/v1/civ/transaction",
    ):
        if method != "POST":
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        if path == "/api/v1/civ/transaction":
            await server._handle_http_civ_transaction(writer, headers, reader)
            return
        if path.startswith("/api/v1/commands"):
            await server._handle_http_commands(path, writer, headers, reader)
        else:
            await server._handle_radio_control(path, writer, headers, reader)
        return

    if path == "/api/v1/band-plan/config":
        if method == "GET":
            await server._serve_band_plan_config(writer)
        elif method == "POST":
            await server._handle_band_plan_config(writer, headers, reader)
        else:
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
        return

    # EiBi routes (POST for fetch, GET for queries)
    if path == "/api/v1/eibi/fetch":
        if method == "POST":
            await server._handle_eibi_fetch(writer, headers, reader)
        else:
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
        return

    if path == "/api/v1/eibi/status":
        body = json.dumps(
            server._eibi.status(),
            separators=(",", ":"),
        ).encode()
        await _send_response(
            writer,
            200,
            "OK",
            body,
            {"Content-Type": "application/json"},
        )
        return

    if path == "/api/v1/eibi/stations":
        await server._serve_eibi_stations(writer, query or {})
        return

    if path == "/api/v1/eibi/segments":
        await server._serve_eibi_segments(writer, query or {})
        return

    if path == "/api/v1/eibi/identify":
        freq = int((query or {}).get("freq", ["0"])[0])
        tol = int((query or {}).get("tolerance", ["5000"])[0])
        matches = server._eibi.identify(freq, tol)

        # Fallback to FCC for US AM stations if no EiBi match
        if not matches and 530_000 <= freq <= 1_700_000:
            try:
                from .eibi import fcc_identify  # noqa: TID251

                matches = await fcc_identify(freq, tol)
            except Exception:
                logger.debug("fcc identify fallback failed")

        body = json.dumps(
            {"stations": matches, "freq_hz": freq},
            separators=(",", ":"),
        ).encode()
        await _send_response(
            writer,
            200,
            "OK",
            body,
            {"Content-Type": "application/json"},
        )
        return

    if path == "/api/v1/eibi/bands":
        bands = server._eibi.get_bands()
        body = json.dumps(
            {"bands": bands},
            separators=(",", ":"),
        ).encode()
        await _send_response(
            writer,
            200,
            "OK",
            body,
            {"Content-Type": "application/json"},
        )
        return

    # Gated WebRTC transport entrypoint (A2.3 / MOR-307): stateless SDP
    # exchange + ICE trickle, behind WebConfig.webrtc_enabled + [webrtc] extra.
    if path == "/api/v1/transport/webrtc/offer":
        if method != "POST":
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        await server._handle_webrtc_offer(writer, headers, reader)
        return
    if path == "/api/v1/transport/webrtc/ice-candidate":
        if method != "POST":
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        await server._handle_webrtc_ice(writer, headers, reader)
        return

    # Diagnostic upload endpoints (issue #1396).
    if path == "/api/v1/diagnose/preview":
        if method != "POST":
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        await server._handle_diagnose_preview(writer, headers, reader)
        return
    if path == "/api/v1/diagnose/send":
        if method != "POST":
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        await server._handle_diagnose_send(writer, headers, reader)
        return
    if path == "/api/v1/diagnose/save":
        if method != "POST":
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        await server._handle_diagnose_save(writer, headers, reader)
        return
    # DELETE /api/v1/diagnose/preview/<preview_id> — prefix dispatch.
    # The trailing slash check prevents collision with POST /preview above.
    if path.startswith("/api/v1/diagnose/preview/"):
        if method != "DELETE":
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        preview_id = path.removeprefix("/api/v1/diagnose/preview/")
        await server._handle_diagnose_delete(writer, headers, preview_id)
        return

    if method not in ("GET", "HEAD"):
        await _send_response(writer, 405, "Method Not Allowed", b"", {})
        return

    # Nuclear SW cleanup: Clear-Site-Data on /?clearcache
    if path == "/clearcache":
        await _send_response(
            writer,
            200,
            "OK",
            b"<h2>Site data cleared. <a href='/'>Reload</a></h2>",
            {
                "Content-Type": "text/html",
                "Clear-Site-Data": '"cache", "storage"',
            },
        )
        return

    if path == "/api/v1/info":
        await server._serve_info(writer, headers)
    elif path == "/api/v1/station":
        await server._serve_station_status(writer, headers)
    elif path == "/api/v1/runtime":
        await server._serve_runtime(writer, headers)
    elif path == "/api/v1/state":
        await server._serve_state(writer, headers)
    elif path == "/api/v1/capabilities":
        await server._serve_capabilities(writer, headers)
    elif path == "/api/v1/dx/spots":
        await server._serve_dx_spots(writer)
    elif path == "/api/v1/band-plan/segments":
        await server._serve_band_plan_segments(writer, query or {})
    elif path == "/api/v1/band-plan/layers":
        await server._serve_band_plan_layers(writer)
    elif path == "/api/v1/audio/analysis":
        await server._serve_audio_analysis(writer, headers)
    elif path == "/" or path == "/index.html":
        await server._serve_static(writer, "index.html")
    elif path.startswith("/"):
        # Try to serve as static file
        rel = path.lstrip("/") or "index.html"
        await server._serve_static(writer, rel)
    else:
        await _send_response(writer, 404, "Not Found", b"404 Not Found", {})
