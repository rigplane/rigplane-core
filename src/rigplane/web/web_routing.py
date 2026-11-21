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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final
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

__all__ = [
    "API_ROUTE_TABLE",
    "ApiRoute",
    "api_channel_tx_refusal",
    "api_route_for_path",
    "dispatch_http_request",
    "dispatch_http_with_controller_gate",
]

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

    Anything malformed returns False and keeps today's route refusal.
    """
    frame = _batch_transaction_step_frame(raw_step)
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


def _batch_transaction_step_frame(raw_step: Any) -> bytes | None:
    """Frame a raw batch transaction step would send, or None when malformed.

    The same parsing :meth:`WebServer._prepare_http_batch_transaction_step`
    applies; anything malformed (extra keys, bad bytes, bad hex) returns
    None and keeps today's handler error.
    """
    from . import server as _server_mod  # noqa: TID251

    if not isinstance(raw_step, dict):
        return None
    if set(raw_step) - _server_mod._CIV_TRANSACTION_BATCH_STEP_KEYS:
        return None
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
        return None
    if command is None:
        return None
    # Annotated local, as above.
    frame: bytes | None = send_civ_frame(command, sub, data)
    return frame


def _typed_command_starts_transmission(payload: object) -> bool | None:
    """Tri-state TX verdict for one typed command body (single or batch step).

    True refuses (the command can key the transmitter), False passes (it
    only ends a transmission), None passes too (unclassifiable: not an
    object, no name, unknown name, non-object params, or a rejected level
    unit — the shared dispatch answers those with its own error). Mirrors
    :meth:`WebServer._handle_http_single_command` parsing — the same name
    lookup, params-object rule, and level-unit normalization — so no body
    refuses here and executes as keying in the handler, or vice versa.
    ``transmit_on`` classifies like the control seat even though no command
    registry entry executes it.
    """
    from .handlers.control import (  # noqa: TID251
        ControlHandler,
        _consume_normalized_level_unit,
        starts_transmission,
    )

    if not isinstance(payload, dict):
        return None
    raw_name = payload.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        return None
    if raw_name not in ControlHandler._COMMANDS and raw_name != "transmit_on":  # noqa: SLF001
        return None
    raw_params = payload.get("params", {})
    if not isinstance(raw_params, dict):
        return None
    try:
        params = _consume_normalized_level_unit(raw_name, raw_params)
    except ValueError:
        return None
    return starts_transmission(raw_name, params)


def _batch_step_starts_transmission(raw_step: Any) -> bool | None:
    """Tri-state TX verdict for one batch step: True refuses the batch.

    Raw transaction steps classify by the frame they would send (only an
    exact transmission-end frame passes, like the control seat); typed
    steps classify like a single command. Unknown, non-executing, or
    malformed steps return None and keep today's handler error. A
    queue-bypass TX name (``send_cw_text``) refuses even though the batch
    would reject it too — both refuse, so the radio stays untouched either
    way.
    """
    from . import server as _server_mod  # noqa: TID251

    if _server_mod.WebServer._is_http_batch_transaction_step(raw_step):  # noqa: SLF001
        frame = _batch_transaction_step_frame(raw_step)
        if frame is None:
            return None
        # Annotated local, as above.
        ends: bool = is_exact_raw_transmission_end_frame(frame)
        return not ends
    if not isinstance(raw_step, dict):
        return None
    if "type" in raw_step and "name" not in raw_step:
        # The handler answers unknown_step_type and executes nothing.
        return None
    return _typed_command_starts_transmission(raw_step)


def api_channel_tx_refusal(method: str, path: str, payload: object) -> str | None:
    """Controller code refusing TX-capable ``api``-channel REST, or None.

    The ``api`` data channel carries no controller fence of its own —
    every request runs through the shared HTTP gate with whatever key it
    carries — so transmit-capable bodies are refused here, before
    dispatch, with the same ``controller_tx_unsupported`` code the control
    seat raises (answered as HTTP 403 ``{"error": code}``, the existing
    controller error style). Transmission ends pass (None), like the
    control and remote HTTP route gates; anything unclassifiable (a wrong
    method, a neutral route, a malformed body) passes through to the
    shared dispatch, which answers with its own error. Covers single
    commands, every batch step (typed and raw), CW send, raw CI-V
    transactions, and managed ``transmit_on``; ``force_off`` and the exact
    transmission-end frames pass.
    """
    if method != "POST":
        return None
    if path == "/api/v1/radio/cw/send":
        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
            return "controller_tx_unsupported"
        return None
    if path == "/api/v1/managed-transmit/command":
        if isinstance(payload, dict) and payload.get("operation") == "transmit_on":
            return "controller_tx_unsupported"
        return None
    if path == "/api/v1/civ/transaction":
        if not isinstance(payload, dict):
            return None
        frame = _raw_transaction_frame_from_payload(payload)
        if frame is None:
            return None
        # Annotated local: the predicate resolves to Any under the
        # web-scoped mypy run while the value is a real bool.
        ends: bool = is_exact_raw_transmission_end_frame(frame)
        return None if ends else "controller_tx_unsupported"
    if path == "/api/v1/commands":
        if _typed_command_starts_transmission(payload):
            return "controller_tx_unsupported"
        return None
    if path == "/api/v1/commands/batch":
        if not isinstance(payload, dict):
            return None
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list):
            return None
        for raw_step in raw_steps:
            if _batch_step_starts_transmission(raw_step):
                return "controller_tx_unsupported"
        return None
    return None


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


# ---------------------------------------------------------------------------
# The /api/v1/* route table: the single place a route is added.
#
# Every /api/v1/* path the HTTP router serves is one entry here —
# ``_dispatch_http_routes`` looks the path up in this table (exact match
# first, then the one trailing-id prefix) and enforces the entry's method
# set with the same 405 the old per-branch checks answered. Adding a route
# means adding one entry; no other list needs a matching edit.
#
# The ``api`` data channel (rigplane.web.api_channel) serves from this same
# table: every ``/api/v1/*`` entry is reachable, with the entry's method
# set enforced. The router has no notion of "routes the UI needs", so
# there is no per-route api flag — reachability follows routing. TX safety
# is per-body, not per-route: before dispatch the channel refuses
# transmit-capable bodies (single commands, every batch step, CW send, raw
# transactions, managed transmit_on) with 403 ``controller_tx_unsupported``
# via :func:`api_channel_tx_refusal`, while transmission ends pass through
# to the shared dispatch. A routed path under the wrong method answers 404
# on that transport (it never reaches dispatch); over HTTP the same request
# answers 405.
# ---------------------------------------------------------------------------

#: One table route's handler: the branch body the old if-chain ran.
RouteHandler = Callable[..., Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ApiRoute:
    """One ``/api/v1/*`` route: methods served, path, and handler.

    ``methods`` is None when the route predates a method check and answers
    any method (its handler decides — the EiBi query branches and the
    controller acquisition branch); otherwise only the listed methods reach
    the handler and anything else answers 405. ``prefix`` marks the one
    trailing-id route (``DELETE /api/v1/diagnose/preview/<id>``); every
    other route matches its path exactly.
    """

    methods: tuple[str, ...] | None
    path: str
    handler: RouteHandler
    prefix: bool = False


async def _handle_route_controller(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Acquire/grant controller keys (any method; the handler decides)."""
    await server._handle_http_controller(method, path, writer, headers, reader)


async def _handle_route_managed_tx(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Serve the three managed-transmit paths (methods enforced by the table)."""
    await server._handle_http_managed_tx(path, writer, headers, reader)


async def _handle_route_bridge(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Serve the audio bridge endpoint for any of its methods."""
    await server._handle_bridge(method, writer, headers, reader)


async def _handle_route_radio_post(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Serve the POST-only radio/command/transaction group by path."""
    if path == "/api/v1/civ/transaction":
        await server._handle_http_civ_transaction(writer, headers, reader)
        return
    if path.startswith("/api/v1/commands"):
        await server._handle_http_commands(path, writer, headers, reader)
    else:
        await server._handle_radio_control(path, writer, headers, reader)


async def _handle_route_band_plan_config(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Serve the band-plan config (the table restricts this to GET/POST)."""
    if method == "GET":
        await server._serve_band_plan_config(writer)
    else:
        await server._handle_band_plan_config(writer, headers, reader)


async def _handle_route_eibi_fetch(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Run an EiBi fetch."""
    await server._handle_eibi_fetch(writer, headers, reader)


async def _handle_route_eibi_status(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Answer the EiBi status snapshot (any method, as routed before)."""
    from . import server as _server_mod  # noqa: TID251

    body = json.dumps(
        server._eibi.status(),
        separators=(",", ":"),
    ).encode()
    await _server_mod._send_response(
        writer,
        200,
        "OK",
        body,
        {"Content-Type": "application/json"},
    )


async def _handle_route_eibi_stations(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Answer the EiBi station query (any method, as routed before)."""
    await server._serve_eibi_stations(writer, query or {})


async def _handle_route_eibi_segments(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Answer the EiBi segment query (any method, as routed before)."""
    await server._serve_eibi_segments(writer, query or {})


async def _handle_route_eibi_identify(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Answer the EiBi identify query, with the FCC fallback for US AM."""
    from . import server as _server_mod  # noqa: TID251

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
    await _server_mod._send_response(
        writer,
        200,
        "OK",
        body,
        {"Content-Type": "application/json"},
    )


async def _handle_route_eibi_bands(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Answer the EiBi band list (any method, as routed before)."""
    from . import server as _server_mod  # noqa: TID251

    bands = server._eibi.get_bands()
    body = json.dumps(
        {"bands": bands},
        separators=(",", ":"),
    ).encode()
    await _server_mod._send_response(
        writer,
        200,
        "OK",
        body,
        {"Content-Type": "application/json"},
    )


async def _handle_route_webrtc_offer(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Run the gated WebRTC SDP exchange."""
    await server._handle_webrtc_offer(writer, headers, reader)


async def _handle_route_webrtc_ice(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Run the gated WebRTC ICE trickle."""
    await server._handle_webrtc_ice(writer, headers, reader)


async def _handle_route_webrtc_close(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Run the gated WebRTC session close."""
    await server._handle_webrtc_close(writer, headers, reader)


async def _handle_route_diagnose_preview(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Stage a diagnostic bundle preview."""
    await server._handle_diagnose_preview(writer, headers, reader)


async def _handle_route_diagnose_send(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Send a staged diagnostic bundle."""
    await server._handle_diagnose_send(writer, headers, reader)


async def _handle_route_diagnose_save(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Save a staged diagnostic bundle."""
    await server._handle_diagnose_save(writer, headers, reader)


async def _handle_route_diagnose_delete(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Delete a staged diagnostic preview by its trailing id."""
    preview_id = path.removeprefix("/api/v1/diagnose/preview/")
    await server._handle_diagnose_delete(writer, headers, preview_id)


async def _handle_route_tail_get(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None,
    reader: asyncio.StreamReader | None,
    query: dict[str, list[str]] | None,
) -> None:
    """Serve the GET/HEAD status surface (the table restricts the methods)."""
    from . import server as _server_mod  # noqa: TID251

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
    elif path == "/api/v1/audio/candidates":
        await server._serve_audio_candidates(writer, headers)
    else:  # Unreachable through the table; totality if table and code drift.
        await _server_mod._send_response(writer, 404, "Not Found", b"404 Not Found", {})


#: Every ``/api/v1/*`` path the HTTP router serves. ``_dispatch_http_routes``
#: matches path here and enforces the entry's method set; the ``api`` data
#: channel serves every entry too, refusing transmit-capable bodies before
#: dispatch via :func:`api_channel_tx_refusal`.
API_ROUTE_TABLE: Final[tuple[ApiRoute, ...]] = (
    ApiRoute(None, "/api/v1/controller", _handle_route_controller),
    ApiRoute(None, "/api/v1/controller/mode", _handle_route_controller),
    ApiRoute(("GET",), "/api/v1/managed-transmit", _handle_route_managed_tx),
    ApiRoute(("POST",), "/api/v1/managed-transmit/command", _handle_route_managed_tx),
    ApiRoute(("PUT",), "/api/v1/managed-transmit/tot", _handle_route_managed_tx),
    ApiRoute(
        ("GET", "HEAD", "POST", "PATCH", "DELETE"),
        "/api/v1/bridge",
        _handle_route_bridge,
    ),
    ApiRoute(("POST",), "/api/v1/radio/disconnect", _handle_route_radio_post),
    ApiRoute(("POST",), "/api/v1/radio/connect", _handle_route_radio_post),
    ApiRoute(("POST",), "/api/v1/radio/power", _handle_route_radio_post),
    ApiRoute(("POST",), "/api/v1/radio/cw/send", _handle_route_radio_post),
    ApiRoute(("POST",), "/api/v1/radio/cw/stop", _handle_route_radio_post),
    ApiRoute(("POST",), "/api/v1/commands", _handle_route_radio_post),
    ApiRoute(("POST",), "/api/v1/commands/batch", _handle_route_radio_post),
    ApiRoute(("POST",), "/api/v1/civ/transaction", _handle_route_radio_post),
    ApiRoute(
        ("GET", "POST"), "/api/v1/band-plan/config", _handle_route_band_plan_config
    ),
    ApiRoute(("POST",), "/api/v1/eibi/fetch", _handle_route_eibi_fetch),
    ApiRoute(None, "/api/v1/eibi/status", _handle_route_eibi_status),
    ApiRoute(None, "/api/v1/eibi/stations", _handle_route_eibi_stations),
    ApiRoute(None, "/api/v1/eibi/segments", _handle_route_eibi_segments),
    ApiRoute(None, "/api/v1/eibi/identify", _handle_route_eibi_identify),
    ApiRoute(None, "/api/v1/eibi/bands", _handle_route_eibi_bands),
    ApiRoute(("POST",), "/api/v1/transport/webrtc/offer", _handle_route_webrtc_offer),
    ApiRoute(
        ("POST",), "/api/v1/transport/webrtc/ice-candidate", _handle_route_webrtc_ice
    ),
    ApiRoute(
        ("POST",), "/api/v1/transport/webrtc/close", _handle_route_webrtc_close
    ),
    ApiRoute(("POST",), "/api/v1/diagnose/preview", _handle_route_diagnose_preview),
    ApiRoute(("POST",), "/api/v1/diagnose/send", _handle_route_diagnose_send),
    ApiRoute(("POST",), "/api/v1/diagnose/save", _handle_route_diagnose_save),
    ApiRoute(
        ("DELETE",),
        "/api/v1/diagnose/preview/",
        _handle_route_diagnose_delete,
        prefix=True,
    ),
    ApiRoute(("GET", "HEAD"), "/api/v1/info", _handle_route_tail_get),
    ApiRoute(("GET", "HEAD"), "/api/v1/station", _handle_route_tail_get),
    ApiRoute(("GET", "HEAD"), "/api/v1/runtime", _handle_route_tail_get),
    ApiRoute(("GET", "HEAD"), "/api/v1/state", _handle_route_tail_get),
    ApiRoute(("GET", "HEAD"), "/api/v1/capabilities", _handle_route_tail_get),
    ApiRoute(("GET", "HEAD"), "/api/v1/dx/spots", _handle_route_tail_get),
    ApiRoute(("GET", "HEAD"), "/api/v1/band-plan/segments", _handle_route_tail_get),
    ApiRoute(("GET", "HEAD"), "/api/v1/band-plan/layers", _handle_route_tail_get),
    ApiRoute(("GET", "HEAD"), "/api/v1/memories", _handle_route_tail_get),
    ApiRoute(("GET", "HEAD"), "/api/v1/audio/analysis", _handle_route_tail_get),
    ApiRoute(("GET", "HEAD"), "/api/v1/audio/candidates", _handle_route_tail_get),
)


def api_route_for_path(path: str) -> ApiRoute | None:
    """Match *path* against the route table: exact entries first, then prefix.

    Returns the entry or None. Method checks stay with the callers (HTTP
    answers 405, the ``api`` channel answers 404 — see the table comment).
    """
    for route in API_ROUTE_TABLE:
        if not route.prefix and path == route.path:
            return route
    for route in API_ROUTE_TABLE:
        if route.prefix and path.startswith(route.path):
            return route
    return None


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
    """Host/Origin transport gates, then the controller gate plus the table."""
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

    await dispatch_http_with_controller_gate(
        server, writer, method, path, headers, reader, query
    )


async def dispatch_http_with_controller_gate(
    server: WebServer,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    reader: asyncio.StreamReader | None = None,
    query: dict[str, list[str]] | None = None,
) -> None:
    """Controller currency gate + route table, shared by HTTP and the api channel.

    This is the second half of :func:`_dispatch_http_request` (everything
    after the Host/Origin transport gates): controller acquisition,
    remote-mode credential rules, transmission-end exemptions, and the
    call into :func:`_dispatch_http_routes`. The ``api`` data channel
    runs each request through exactly this function with a synthetic
    request/response adapter, so every route applies its existing HTTP
    controller rules per request; the Host/Origin gates stay HTTP-only
    (a data channel has neither header).
    """
    route = api_route_for_path(path)
    if route is not None and route.handler is _handle_route_controller:
        await route.handler(server, writer, method, path, headers, reader, query)
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

    # Every /api/v1/* route lives in API_ROUTE_TABLE: match path here and
    # enforce the entry's method set with the same 405 the old per-branch
    # checks answered.
    route = api_route_for_path(path)
    if route is not None:
        if route.methods is not None and method not in route.methods:
            await _send_response(writer, 405, "Method Not Allowed", b"", {})
            return
        await route.handler(server, writer, method, path, headers, reader, query)
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
    elif path == "/api/v1/audio/candidates":
        await server._serve_audio_candidates(writer, headers)
    elif path == "/" or path == "/index.html":
        await server._serve_static(writer, "index.html")
    elif path.startswith("/"):
        # Try to serve as static file
        rel = path.lstrip("/") or "index.html"
        await server._serve_static(writer, rel)
    else:
        await _send_response(writer, 404, "Not Found", b"404 Not Found", {})
