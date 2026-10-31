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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from rigplane.runtime.controller_authority import ControllerAuthority, ControllerError
from rigplane.runtime.tx_interlock import (
    is_exact_raw_transmission_end_frame,
    send_civ_frame,
)

from rigplane.core.host_guard import (  # noqa: TID251
    MISDIRECTED_BODY,
    ORIGIN_FORBIDDEN_BODY,
    host_header_allowed,
    same_origin_allowed,
)

if TYPE_CHECKING:
    from .server import WebServer  # noqa: TID251

__all__ = ["dispatch_http_request"]

logger = logging.getLogger(__name__)


def _raw_transaction_frame_from_payload(payload: object) -> bytes | None:
    """Build the frame a raw CI-V transaction body would send, raise-free.

    Parses command/sub/data with the handler's own parsers; anything
    malformed returns None and keeps today's route refusal.
    """

    from . import server as _server_mod  # noqa: TID251

    if not isinstance(payload, dict):
        return None
    try:
        command = _server_mod.WebServer._parse_civ_byte(  # noqa: SLF001
            payload.get("command"), "command"
        )
        sub = _server_mod.WebServer._parse_civ_byte(  # noqa: SLF001
            payload.get("sub"), "sub", required=False
        )
        data = _server_mod.WebServer._parse_civ_hex_data(  # noqa: SLF001
            payload.get("data", "")
        )
    except ValueError:
        return None
    if command is None:
        return None
    # Annotated local: under the web-scoped mypy run the runtime import
    # resolves to Any, and a bare return would trip no-any-return while
    # the value is real bytes-or-None.
    frame: bytes | None = send_civ_frame(command, sub, data)
    return frame


def _single_command_body_ends_transmission(payload: object) -> bool:
    """True when the body is one typed command that only ends a transmission.

    Mirrors :meth:`WebServer._handle_http_single_command` parsing — the same
    JSON value the handler executes, the same name lookup against
    ``ControlHandler._COMMANDS``, the same params-object rule, the same
    level-unit normalization — so no body reads as a transmission end here
    and as keying in the handler.
    """
    from .handlers.control import (  # noqa: TID251
        ControlHandler,
        _consume_normalized_level_unit,
        is_typed_transmission_end_command,
    )

    if not isinstance(payload, dict):
        return False
    raw_name = payload.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        return False
    if raw_name not in ControlHandler._COMMANDS:  # noqa: SLF001
        return False
    raw_params = payload.get("params", {})
    if not isinstance(raw_params, dict):
        return False
    try:
        params = _consume_normalized_level_unit(raw_name, raw_params)
    except ValueError:
        return False
    return is_typed_transmission_end_command(raw_name, params)


def _batch_transaction_step_ends_transmission(raw_step: Any) -> bool:
    """True when a raw batch step sends exactly a transmission-end frame.

    Mirrors :meth:`WebServer._prepare_http_batch_transaction_step` frame
    parsing with the handler's own parsers; anything malformed returns
    False and keeps today's route refusal.
    """
    from . import server as _server_mod  # noqa: TID251

    if not isinstance(raw_step, dict):
        return False
    if set(raw_step) - _server_mod._CIV_TRANSACTION_BATCH_STEP_KEYS:
        return False
    try:
        command = _server_mod.WebServer._parse_civ_byte(  # noqa: SLF001
            raw_step.get("command"), "command"
        )
        sub = _server_mod.WebServer._parse_civ_byte(  # noqa: SLF001
            raw_step.get("sub"), "sub", required=False
        )
        data = _server_mod.WebServer._parse_civ_hex_data(  # noqa: SLF001
            raw_step.get("data", "")
        )
    except ValueError:
        return False
    if command is None:
        return False
    frame: bytes | None = send_civ_frame(command, sub, data)
    if frame is None:
        return False
    # Annotated local, as below: the predicate resolves to Any under the
    # web-scoped mypy run while the value is a real bool.
    ends: bool = is_exact_raw_transmission_end_frame(frame)
    return ends


def _batch_step_ends_transmission(raw_step: Any) -> bool:
    """True when one batch step, as executed, only ends a transmission."""
    from . import server as _server_mod  # noqa: TID251
    from .handlers.control import (  # noqa: TID251
        ControlHandler,
        _consume_normalized_level_unit,
        is_typed_transmission_end_command,
    )

    if _server_mod.WebServer._is_http_batch_transaction_step(raw_step):  # noqa: SLF001
        return _batch_transaction_step_ends_transmission(raw_step)
    if not isinstance(raw_step, dict):
        return False
    if "type" in raw_step and "name" not in raw_step:
        # The handler answers unknown_step_type and executes nothing.
        return False
    raw_name = raw_step.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        return False
    if raw_name not in ControlHandler._COMMANDS:  # noqa: SLF001
        return False
    if raw_name in ControlHandler._READ_ONLY_HANDLERS:  # noqa: SLF001
        # The handler rejects queue-bypass commands in a batch.
        return False
    raw_params = raw_step.get("params", {})
    if not isinstance(raw_params, dict):
        return False
    try:
        params = _consume_normalized_level_unit(raw_name, raw_params)
    except ValueError:
        return False
    return is_typed_transmission_end_command(raw_name, params)


def _batch_body_all_transmission_end(payload: object) -> bool:
    """True when the body is a batch whose every step ends a transmission.

    An empty or malformed batch never counts, so it keeps today's
    credential requirement.
    """
    if not isinstance(payload, dict):
        return False
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return False
    return all(_batch_step_ends_transmission(step) for step in raw_steps)


def _exhausted_reader(prefix: bytes = b"") -> asyncio.StreamReader:
    """A reader yielding *prefix* then EOF — a failed peek's bytes, spent.

    The downstream handler's capped read fails on it at once instead of
    waiting out a second read timeout.
    """
    spent = asyncio.StreamReader()
    if prefix:
        spent.feed_data(prefix)
    spent.feed_eof()
    return spent


async def _peek_http_body(
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    is_exempt: Callable[[object], bool],
) -> tuple[asyncio.StreamReader | None, bool]:
    """Read the body once, re-buffer it, and report if its payload is exempt.

    Returns a fresh reader over the same bytes plus whether the parsed
    payload is exempt for the route.  The reader is None when the body is
    missing, oversize, or has no length — the caller then keeps today's
    credential/refusal path with the original reader.  A non-exempt but
    readable body still returns its bytes, so a request that goes on to
    present a credential is handled from the same bytes it sent: the
    downstream handler re-parses them, and the peek and the handler always
    classify one payload.  When the read itself fails, the returned
    reader is spent — EOF after whatever bytes the peek saw — so the
    handler fails at once instead of waiting out a second read timeout.
    An unparsable body (including pathological nesting) is not exempt.
    """

    from . import server as _server_mod  # noqa: TID251

    try:
        content_length = int((headers or {}).get("content-length", "0"))
    except ValueError:
        return None, False
    if reader is None or content_length <= 0:
        return None, False
    try:
        body = await _server_mod._read_capped_body(reader, content_length)
    except asyncio.IncompleteReadError as exc:
        return _exhausted_reader(exc.partial), False
    except Exception:  # noqa: BLE001 — any other read failure keeps the refusal
        return _exhausted_reader(), False
    if body is None:
        return None, False
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        payload = None
    buffered = asyncio.StreamReader()
    buffered.feed_data(body)
    buffered.feed_eof()
    try:
        exempt = payload is not None and is_exempt(payload)
    except Exception:  # noqa: BLE001 — any classifier failure keeps the refusal
        return buffered, False
    return buffered, exempt


def _raw_transaction_body_ends_transmission(payload: object) -> bool:
    """True when a raw CI-V transaction body sends a transmission-end frame."""
    frame = _raw_transaction_frame_from_payload(payload)
    if frame is None:
        return False
    # Annotated local, as above: the predicate resolves to Any under the
    # web-scoped mypy run while the value is a real bool.
    ends: bool = is_exact_raw_transmission_end_frame(frame)
    return ends


async def _buffered_reader_for_transmission_end_frame(
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
) -> asyncio.StreamReader | None:
    """Re-buffer the body when it is exactly a transmission-end frame.

    Returns a fresh reader over the same bytes for the handler, or None
    when the body does not build to a frame that ends a transmission —
    missing, oversize, unreadable, or malformed — so the caller keeps
    today's route refusal.
    """
    buffered, exempt = await _peek_http_body(
        headers, reader, _raw_transaction_body_ends_transmission
    )
    return buffered if exempt else None


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

    if path in ("/api/v1/controller", "/api/v1/controller/mode"):
        await server._handle_http_controller(method, path, writer, headers, reader)
        return
    controller = getattr(server, "_controller", None)
    if not isinstance(controller, ControllerAuthority):
        await _dispatch_http_routes(
            server, writer, method, path, headers, reader, query
        )
        return
    if controller.mode == "remote" and method in ("POST", "PUT", "PATCH", "DELETE"):
        if path == "/api/v1/managed-transmit/command":
            await _dispatch_http_routes(
                server, writer, method, path, headers, reader, query
            )
            return
        try:
            if path == "/api/v1/civ/transaction":
                # A raw transaction whose frame ends a transmission
                # is exempt from the route refusal, matching the
                # executor's capture exemption: the peek rebuilds
                # the exact bytes the handler would send, so a
                # malformed or non-exempt body keeps today's
                # refusal below.
                replacement = await _buffered_reader_for_transmission_end_frame(
                    headers, reader
                )
                if replacement is not None:
                    await _dispatch_http_routes(
                        server, writer, method, path, headers, replacement, query
                    )
                    return
                raise ControllerError("controller_local_required")
            if path in ("/api/v1/commands", "/api/v1/commands/batch"):
                # A typed transmission end passes the same way: the peek
                # classifies the body with the handler's own parsing, so a
                # malformed, oversize, unreadable, or keying body keeps
                # today's credential requirement below.  A batch is exempt
                # only when every step ends a transmission.  The peeked
                # bytes are replayed downstream either way, so a request
                # that goes on to present a credential is handled from the
                # same bytes it sent.  Only POST is peeked — the only
                # method these paths serve — so other methods keep
                # today's credential-first refusal.  A failed peek hands
                # downstream a spent reader, bounding the hold to one
                # read timeout.
                buffered: asyncio.StreamReader | None = None
                exempt = False
                if method == "POST":
                    if path == "/api/v1/commands/batch":
                        is_exempt: Callable[[object], bool] = (
                            _batch_body_all_transmission_end
                        )
                    else:
                        is_exempt = _single_command_body_ends_transmission
                    buffered, exempt = await _peek_http_body(headers, reader, is_exempt)
                if exempt:
                    assert buffered is not None
                    await _dispatch_http_routes(
                        server, writer, method, path, headers, buffered, query
                    )
                    return
                if buffered is not None:
                    reader = buffered
            else:
                raise ControllerError("controller_local_required")
            ticket = controller.credential((headers or {}).get("x-rigplane-controller"))
            with controller.bind(ticket):
                await _dispatch_http_routes(
                    server, writer, method, path, headers, reader, query
                )
        except ControllerError as exc:
            await server._send_json(
                writer, exc.status, "Forbidden", {"error": exc.code}
            )
        return
    if controller.mode == "remote":
        await _dispatch_http_routes(
            server, writer, method, path, headers, reader, query
        )
        return
    # Capture local HTTP currency too: a handoff cannot revive its delayed work.
    # A request naming a multi-client session acts as that session (MOR-3534).
    key = (headers or {}).get("x-rigplane-controller")
    try:
        ticket = controller.capture() if key is None else controller.credential(key)
    except ControllerError as exc:
        await server._send_json(writer, exc.status, "Forbidden", {"error": exc.code})
        return
    with controller.bind(ticket):
        await _dispatch_http_routes(
            server, writer, method, path, headers, reader, query
        )


async def _dispatch_http_routes(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    from . import server as _server_mod  # noqa: TID251

    _send_response = _server_mod._send_response
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
        if method not in ("GET", "HEAD", "POST", "PATCH", "DELETE"):
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        await server._handle_bridge(method, writer, headers, reader)
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
    elif path == "/api/v1/memories":
        await server._serve_memories(writer, query or {})
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
