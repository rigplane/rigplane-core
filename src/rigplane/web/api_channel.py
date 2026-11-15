"""REST over a reliable WebRTC data channel: the ``api`` channel label.

A browser that reaches Core only over WebRTC data channels has no HTTP.
The ``api`` label carries the web UI's REST surface (``POST
/api/v1/controller`` to acquire a key, then ``GET /api/v1/info`` and
friends with that key in the ``X-RigPlane-Controller`` header) through
one ordered/reliable data channel. The channel attaches through the SAME
``attach_client_channel`` / ``ClientChannelParams`` path as
control/scope/audio; the first frame is the attach frame, and a keyless
attach is admitted (it is how a browser acquires its first key).

Requests are served by Core's EXISTING HTTP routing
(:mod:`rigplane.web.web_routing`, via the shared controller gate the
HTTP lane also uses), in-process, with a synthetic request/response
adapter. There is no second router and no URL is ever proxied: the
adapter builds a ``StreamReader`` over the request body plus a capturing
writer, runs the shared dispatch, and splits the captured HTTP bytes
back into status/headers/body. What this transport serves is derived
from the router's own ``API_ROUTE_TABLE`` — every ``/api/v1/*`` entry is
reachable — so adding a route to the router is the only place to
change. The router has no notion of "routes the UI needs", so there is
no per-route api flag; reachability follows routing. Anything else (an
unknown path or a routed path under the wrong method) answers 404
without touching controller state.

TX-capable REST bodies are refused even where HTTP serves them: they key
the transmitter, and over a data channel the UI keys PTT through the
control channel command instead, which is fenced by the controller
session. A data-channel REST request must not become a second, unfenced
TX keying mechanism beside it, so before dispatch the channel refuses
transmit-capable bodies — single commands, every batch step, CW send,
raw CI-V transactions, managed transmit_on — with 403
``controller_tx_unsupported`` (see
:func:`rigplane.web.web_routing.api_channel_tx_refusal` for the
body-by-body rule, shared with the control seat's own classifier).
Transmission ends pass through to the shared dispatch, like the control
and remote HTTP route gates.

Wire format (typed, versioned): every message is a binary header
``[u8 version=1][u8 type][u32 request_id BE][u8 flags]`` plus payload.
The flags byte is reserved (always sent as 0, ignored on receipt).
Message types: REQ_HEAD carries UTF-8 JSON
``{method, path, query, headers}`` with ``query`` as
``{name: [values]}``; REQ_BODY carries raw bytes; a request is one
REQ_HEAD, zero or more REQ_BODY, then END. The server answers RES_HEAD
(JSON ``{status, headers}``), zero or more RES_BODY (each at most 16
KB), then END; or one terminal ERROR (JSON ``{code, message}``).
CANCEL asks the server to drop a request before it answers. Request ids
are client-chosen and strictly increasing per channel: a REQ_HEAD with
an id at or below the highest accepted one answers ERROR ``stale_id``
(a rejected head never consumes its id, so it may be retried). A
version mismatch answers ERROR ``bad_version`` and then closes the
channel; other malformed or short frames answer ERROR ``malformed`` and
never raise out of the handler.

Prior art (behaviour reference only): peerfetch (Apache-2.0) multiplexes
by positional FIFO matching (head-of-line blocking), sends whole bodies
unchunked, proxies arbitrary URLs, and fails silently; go-webrtc/http
(MIT) multiplexes by ``[u32 id][raw HTTP]`` with chunking at the SCTP
max message size but no backpressure (a blocking receive stalls every
request), no timeouts, and silent drops. This design keeps the
id-multiplexing plus chunking idea and adds backpressure (wait while the
data channel reports more than 256 KB buffered, resume on drain),
per-request deadlines, explicit errors, and an allow-list instead of URL
proxying.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import struct
from collections.abc import Coroutine
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Final, Protocol, cast, runtime_checkable

from ..ws import WS_OP_BINARY  # noqa: TID251

if TYPE_CHECKING:
    from .server import WebServer  # noqa: TID251
    from .transport.connection import Connection  # noqa: TID251

__all__ = [
    "API_CHANNEL_VERSION",
    "MAX_IN_FLIGHT_REQUESTS",
    "MAX_REQUEST_BODY_BYTES",
    "REQUEST_DEADLINE_S",
    "RESPONSE_CHUNK_BYTES",
    "SEND_HIGH_WATER_BYTES",
    "SEND_LOW_WATER_BYTES",
    "ApiChannelHandler",
    "ApiMessageType",
    "ApiRequestHead",
    "ApiWireDefinition",
    "ApiWireHeader",
    "ApiWireHeaderField",
    "ApiWireLimits",
    "ApiWireMessageTypes",
    "ApiWirePayload",
    "api_channel_golden_documents",
    "api_channel_wire_definition",
    "api_path_allowed",
    "api_response_messages",
]

logger = logging.getLogger(__name__)

#: Wire version; a mismatch answers ERROR ``bad_version`` and closes.
API_CHANNEL_VERSION: Final = 1


class ApiMessageType(IntEnum):
    """The one definition of every message type on the wire."""

    REQ_HEAD = 0x01
    REQ_BODY = 0x02
    RES_HEAD = 0x03
    RES_BODY = 0x04
    END = 0x05
    ERROR = 0x06
    CANCEL = 0x07


#: Binary header ``[u8 version][u8 type][u32 request_id BE][u8 flags]``.
_FRAME_FORMAT: Final = ">BBIB"
_FRAME_HEADER: Final = struct.Struct(_FRAME_FORMAT)

#: Biggest RES_BODY payload the server ever emits in one frame.
RESPONSE_CHUNK_BYTES: Final = 16 * 1024

#: Biggest assembled request body the server accepts; past it ERROR ``too_large``.
MAX_REQUEST_BODY_BYTES: Final = 1024 * 1024

#: Most requests one channel may hold at once; past it ERROR ``busy``.
MAX_IN_FLIGHT_REQUESTS: Final = 32

#: Per-request server deadline (HEAD acceptance to terminal frame); past it
#: ERROR ``timeout``. Read at request time so tests may patch it.
REQUEST_DEADLINE_S: Final = 15.0

#: While the data channel reports more than this buffered, sends wait.
SEND_HIGH_WATER_BYTES: Final = 256 * 1024

#: Resume threshold after a high-water wait (also the requested channel mark).
SEND_LOW_WATER_BYTES: Final = 64 * 1024

#: Poll step for the drain wait (a short poll is the portable resume the
#: wire contract requires; the threshold above is still requested).
_DRAIN_POLL_S: Final = 0.01

#: Error codes the server may emit (``timeout``/``too_large``/``busy`` are
#: the contract; the rest name the framing failures).
_BAD_VERSION: Final = "bad_version"
_MALFORMED: Final = "malformed"
_STALE_ID: Final = "stale_id"
_TOO_LARGE: Final = "too_large"
_BUSY: Final = "busy"
_TIMEOUT: Final = "timeout"
_INTERNAL: Final = "internal"


@dataclass(frozen=True, slots=True)
class ApiWireHeaderField:
    """One binary header field: wire name and width."""

    name: str
    type: str


@dataclass(frozen=True, slots=True)
class ApiWireHeader:
    """The binary frame header layout the wire contract pins."""

    layout: str
    fields: tuple[ApiWireHeaderField, ...]
    size_bytes: int
    flags: str


@dataclass(frozen=True, slots=True)
class ApiWireMessageTypes:
    """Every message type on the wire, by name."""

    REQ_HEAD: int
    REQ_BODY: int
    RES_HEAD: int
    RES_BODY: int
    END: int
    ERROR: int
    CANCEL: int


@dataclass(frozen=True, slots=True)
class ApiWireLimits:
    """The numeric limits the wire contract pins."""

    response_chunk_bytes: int
    max_request_body_bytes: int
    max_in_flight_requests: int
    request_deadline_s: float
    send_high_water_bytes: int
    send_low_water_bytes: int


@dataclass(frozen=True, slots=True)
class ApiWirePayload:
    """One JSON payload contract from its producer: ``carried_by`` names
    the carrying frame, each field row is ``(name, type, required,
    note)``, ``codes`` lists the values a string field may take. Field
    order is the stable key order of the committed JSON.
    """

    version: int
    carried_by: str
    fields: tuple[tuple[str, str, bool, str], ...]
    codes: tuple[str, ...] = ()


def _api_channel_payload_contracts() -> dict[str, ApiWirePayload]:
    """The payload contracts, owned by their producer types: every field
    mirrors what the producer accepts or emits; the golden replay test
    pins the bytes against the real handlers.
    """
    from .channel_attach import ATTACH_ACK_VERSION  # noqa: TID251

    return {
        "request_head": ApiWirePayload(
            1,
            "REQ_HEAD frame payload (UTF-8 JSON object)",
            (
                ("method", "string", True, "non-empty HTTP method"),
                ("path", "string", True, "origin-form, no query"),
                ("query", "object: name -> [values]", False, "absent means none"),
                ("headers", "object: name -> value", False, "absent means none"),
            ),
        ),
        "response_head": ApiWirePayload(
            1,
            "RES_HEAD frame payload (UTF-8 JSON object)",
            (
                ("status", "integer", True, "HTTP status code"),
                ("headers", "object: name -> value", True, "answer headers"),
            ),
        ),
        "error": ApiWirePayload(
            1,
            "ERROR frame payload (UTF-8 JSON object)",
            (
                ("code", "string", True, "one of the codes below"),
                ("message", "string", True, "human-readable detail"),
            ),
            (
                _BAD_VERSION,
                _MALFORMED,
                _STALE_ID,
                _TOO_LARGE,
                _BUSY,
                _TIMEOUT,
                _INTERNAL,
            ),
        ),
        "attach_frame": ApiWirePayload(
            1,
            "first text frame on a client data channel",
            (
                ("type", '"attach"', True, "the attach discriminator"),
                ("controller_key", "string", True, 'session key; null only on "api"'),
                ("state_delta", "integer 1 | 2", False, "2 (leaf deltas) when absent"),
                ("role", "string", False, '"primary" when absent'),
            ),
        ),
        "attach_ack": ApiWirePayload(
            1,
            "first server text frame on a scope/audio channel",
            (
                ("type", '"attached"', True, "the ack discriminator"),
                ("v", "integer", True, f"ack version, currently {ATTACH_ACK_VERSION}"),
                ("channel", '"scope" | "audio"', True, "the acked label"),
            ),
        ),
    }


@dataclass(frozen=True, slots=True)
class ApiWireDefinition:
    """The wire contract as a typed, JSON-serialisable record.

    The one machine-readable copy of the header layout, message types,
    version, chunk size, limits, and the JSON payload contracts. Use
    :func:`dataclasses.asdict` for the plain-JSON form; every value
    round-trips through ``json`` unchanged.
    """

    version: int
    header: ApiWireHeader
    message_types: ApiWireMessageTypes
    limits: ApiWireLimits
    payloads: dict[str, ApiWirePayload]


def api_channel_wire_definition() -> ApiWireDefinition:
    """Return the wire contract as a typed record.

    Built from the module constants above for the browser side (the
    browser transport will consume and check this instead of re-listing
    values).
    """
    return ApiWireDefinition(
        version=API_CHANNEL_VERSION,
        header=ApiWireHeader(
            layout=_FRAME_FORMAT,
            fields=(
                ApiWireHeaderField("version", "u8"),
                ApiWireHeaderField("type", "u8"),
                ApiWireHeaderField("request_id", "u32be"),
                ApiWireHeaderField("flags", "u8"),
            ),
            size_bytes=_FRAME_HEADER.size,
            flags="reserved, always sent as 0 and ignored on receipt",
        ),
        message_types=ApiWireMessageTypes(
            **{entry.name: entry.value for entry in ApiMessageType}
        ),
        limits=ApiWireLimits(
            response_chunk_bytes=RESPONSE_CHUNK_BYTES,
            max_request_body_bytes=MAX_REQUEST_BODY_BYTES,
            max_in_flight_requests=MAX_IN_FLIGHT_REQUESTS,
            request_deadline_s=REQUEST_DEADLINE_S,
            send_high_water_bytes=SEND_HIGH_WATER_BYTES,
            send_low_water_bytes=SEND_LOW_WATER_BYTES,
        ),
        payloads=_api_channel_payload_contracts(),
    )


@runtime_checkable
class _BackpressureSource(Protocol):
    """A connection that reports its unsent send buffer (aiortc exposes it)."""

    def buffered_bytes(self) -> int:
        """Unsent bytes currently buffered by the transport."""
        ...

    def set_buffer_low_watermark(self, value: int) -> None:
        """Ask the transport to mark the drain-resume level (best effort)."""
        ...


@dataclass(frozen=True, slots=True)
class ApiRequestHead:
    """A parsed REQ_HEAD: the HTTP request line the channel carries."""

    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]

    @classmethod
    def parse(cls, payload: bytes) -> ApiRequestHead:
        """Parse a REQ_HEAD payload with the producer's own rules.

        Raises :class:`ValueError` on anything malformed: undecodable or
        non-object JSON, a missing or mistyped ``method``/``path``, a
        ``path`` that is not an origin-form path (it must start with
        ``/`` and carries no query — the query travels in ``query``), or
        mistyped ``query``/``headers`` entries. Unknown fields are
        ignored (forward compatibility).
        """
        try:
            obj = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            # A deeply nested head raises RecursionError (not ValueError);
            # normalise it into the same malformed ERROR instead of letting
            # it escape and kill the receive loop.
            raise ValueError(f"api request head is not JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError("api request head must be a JSON object")
        method = obj.get("method")
        if type(method) is not str or not method:
            raise ValueError("api request head 'method' must be a string")
        path = obj.get("path")
        if type(path) is not str or not path.startswith("/") or "?" in path:
            raise ValueError("api request head 'path' must be an origin-form path")
        query_raw = obj.get("query", {})
        if not isinstance(query_raw, dict):
            raise ValueError("api request head 'query' must be an object")
        query: dict[str, list[str]] = {}
        for key, values in query_raw.items():
            if type(key) is not str or not isinstance(values, list):
                raise ValueError("api request head 'query' maps names to lists")
            for value in values:
                if type(value) is not str:
                    raise ValueError("api request head 'query' values are strings")
            query[key] = list(values)
        headers_raw = obj.get("headers", {})
        if not isinstance(headers_raw, dict):
            raise ValueError("api request head 'headers' must be an object")
        headers: dict[str, str] = {}
        for key, value in headers_raw.items():
            if type(key) is not str or type(value) is not str:
                raise ValueError("api request head 'headers' maps strings to strings")
            headers[key] = value
        return cls(method=method, path=path, query=query, headers=headers)


@dataclass(frozen=True, slots=True)
class ApiResponseHead:
    """A RES_HEAD: the HTTP status line the channel answers with."""

    status: int
    headers: dict[str, str]

    def encode(self) -> bytes:
        """Encode as the JSON payload of a RES_HEAD frame."""
        return json.dumps({"status": self.status, "headers": self.headers}).encode(
            "utf-8"
        )


@dataclass(frozen=True, slots=True)
class ApiErrorFrame:
    """A terminal ERROR: the machine-readable failure for one request."""

    code: str
    message: str

    def encode(self) -> bytes:
        """Encode as the JSON payload of an ERROR frame."""
        return json.dumps({"code": self.code, "message": self.message}).encode("utf-8")


def _encode_frame(msg_type: int, request_id: int, payload: bytes) -> bytes:
    """Encode one wire message (always version 1, reserved flags 0)."""
    return _FRAME_HEADER.pack(API_CHANNEL_VERSION, msg_type, request_id, 0) + payload


def api_path_allowed(method: str, path: str) -> bool:
    """True when the api channel routes ``(method, path)``.

    Derived from the router's own ``API_ROUTE_TABLE``: every ``/api/v1/*``
    entry is reachable. Anything else (an unknown path or a routed path
    under the wrong method) answers 404 without touching controller state.
    Transmit-capable *bodies* on reachable routes are refused later, before
    dispatch, with 403 (see :func:`_dispatch`).
    """
    from .web_routing import api_route_for_path  # noqa: TID251

    route = api_route_for_path(path)
    if route is None:
        return False
    return route.methods is None or method in route.methods


class _ChannelHttpWriter:
    """An ``asyncio.StreamWriter`` stand-in capturing one HTTP response.

    Handlers only ``write`` response bytes, ``drain`` them, read
    ``peername`` (controller acquisition records the client), and
    ``close`` on oversize bodies — exactly the surface captured here.
    """

    def __init__(self, peer: tuple[str, int]) -> None:
        self._buf = bytearray()
        self._peer = peer
        self.closed = False

    def write(self, data: bytes) -> None:
        """Append response bytes to the capture buffer."""
        self._buf += bytes(data)

    async def drain(self) -> None:
        """Capture needs no drain; present for the writer surface."""

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        """Answer ``peername`` with the data-channel peer, else the default."""
        if name == "peername":
            return self._peer
        return default

    def close(self) -> None:
        """Mark closed (an oversize body already answered its 413)."""
        self.closed = True


async def _serve_one_http(
    server: WebServer,
    method: str,
    path: str,
    query: dict[str, list[str]],
    headers: dict[str, str],
    body: bytes,
    peer: tuple[str, int],
) -> tuple[int, dict[str, str], bytes]:
    """Run one request through the shared HTTP dispatch, in-process.

    Builds the synthetic request (a ``StreamReader`` over the body, header
    names lowercased exactly as the TCP lane delivers them, plus a
    ``content-length`` that always matches the bytes carried — a client
    value is never trusted) and runs the SAME controller gate + route
    table the HTTP lane uses, so every route applies exactly its existing
    HTTP rules, including the per-request controller key. Splits the one
    captured HTTP response back into status/headers/body. ``Connection``
    is a transport lie on a data channel and is filtered out of the
    answered headers.
    """
    from .web_routing import dispatch_http_with_controller_gate  # noqa: TID251

    lowered = {key.lower(): value for key, value in headers.items()}
    lowered["content-length"] = str(len(body))
    reader = asyncio.StreamReader()
    if body:
        reader.feed_data(body)
    reader.feed_eof()
    writer = _ChannelHttpWriter(peer)
    try:
        await dispatch_http_with_controller_gate(
            server,
            cast("asyncio.StreamWriter", writer),
            method,
            path,
            lowered,
            reader,
            query,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("api: dispatch failed for %s %s", method, path)
        if not writer._buf:  # noqa: SLF001 — our own capture, nothing written
            return (
                500,
                {"Content-Type": "application/json"},
                b'{"error":"internal server error"}',
            )
    return _split_response(bytes(writer._buf))


def _split_response(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    """Split captured HTTP response bytes into status/headers/body.

    Raises :class:`ValueError` when no complete response was captured.
    """
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        raise ValueError("api adapter captured no HTTP response")
    lines = head.split(b"\r\n")
    try:
        status = int(lines[0].split(b" ", 2)[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"api adapter captured a bad status line: {exc}") from exc
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, colon, value = line.partition(b":")
        if not colon:
            continue
        text = name.decode("latin-1").strip()
        if text.lower() == "connection":
            continue
        headers[text] = value.decode("latin-1").strip()
    return status, headers, body


class _RequestState:
    """One request from an accepted REQ_HEAD to its terminal frame.

    The retained body is one bytearray bounded by bytes: every chunk —
    empty or 1 byte — is accounted by its length, so no sequence of
    chunks can retain more than ``MAX_REQUEST_BODY_BYTES``.
    """

    __slots__ = (
        "buf",
        "cancelled",
        "dispatched",
        "end_event",
        "head",
        "task",
    )

    head: ApiRequestHead
    buf: bytearray
    end_event: asyncio.Event
    cancelled: bool
    dispatched: bool
    task: asyncio.Task[None] | None

    def __init__(self, head: ApiRequestHead) -> None:
        self.head = head
        self.buf = bytearray()
        self.end_event = asyncio.Event()
        self.cancelled = False
        self.dispatched = False
        self.task: asyncio.Task[None] | None = None


class ApiChannelHandler:
    """Serve allow-listed REST over one reliable data channel.

    The receive loop only routes frames into per-request state and spawns
    one task per request, so one slow request never blocks the others
    (no head-of-line blocking) and the data-channel receive callback is
    never blocked. Each request runs the shared HTTP dispatch with its
    own 15 s deadline; every outcome — response, ERROR, or channel
    death — releases the request's in-flight slot exactly once.
    """

    def __init__(
        self,
        conn: Connection,
        server: WebServer,
        peer: tuple[str, int],
    ) -> None:
        self._conn = conn
        self._server = server
        self._peer = peer
        self._requests: dict[int, _RequestState] = {}
        self._background: set[asyncio.Task[None]] = set()
        self._highest_id = 0
        self._in_flight = 0
        self._done = False
        source = conn if isinstance(conn, _BackpressureSource) else None
        if source is not None:
            with contextlib.suppress(Exception):
                source.set_buffer_low_watermark(SEND_LOW_WATER_BYTES)

    def _spawn_send(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """Track a fire-and-forget send for teardown (never untracked).

        Overflow ERRORs cannot join a request task (its task is already
        cancelled), so they run here; teardown cancels and gathers them.
        """
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    @property
    def done(self) -> bool:
        """True once the handler left its receive loop (channel over)."""
        return self._done

    async def run(self) -> None:
        """Route frames until the channel closes, then release everything.

        Teardown cancels and gathers every request task and every tracked
        background send, so no task outlives the channel.
        """
        try:
            while True:
                try:
                    opcode, payload = await self._conn.recv()
                except EOFError:
                    break
                except Exception:
                    logger.debug("api: receive failed", exc_info=True)
                    break
                if await self._handle_message(opcode, payload):
                    break
        finally:
            self._done = True
            tasks = [
                state.task
                for state in list(self._requests.values())
                if state.task is not None
            ]
            tasks.extend(self._background)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_message(self, opcode: int, payload: bytes) -> bool:
        """Route one frame; True asks the receive loop to stop (closing)."""
        if opcode != WS_OP_BINARY or len(payload) < _FRAME_HEADER.size:
            await self._send_error(0, _MALFORMED, "frame too short")
            return False
        version, msg_type, request_id, _flags = _FRAME_HEADER.unpack(
            payload[: _FRAME_HEADER.size]
        )
        body = payload[_FRAME_HEADER.size :]
        if version != API_CHANNEL_VERSION:
            await self._send_error(request_id, _BAD_VERSION, "unsupported version")
            with contextlib.suppress(Exception):
                await self._conn.close(1000, "bad version")
            return True
        if msg_type == ApiMessageType.REQ_HEAD:
            await self._on_head(request_id, body)
        elif msg_type == ApiMessageType.REQ_BODY:
            self._on_body(request_id, body)
        elif msg_type == ApiMessageType.END:
            self._on_end(request_id)
        elif msg_type == ApiMessageType.CANCEL:
            self._on_cancel(request_id)
        else:
            await self._send_error(request_id, _MALFORMED, "unknown message type")
        return False

    async def _on_head(self, request_id: int, payload: bytes) -> None:
        """Accept a request (spawning its task) or refuse it with an ERROR."""
        if request_id <= self._highest_id:
            await self._send_error(request_id, _STALE_ID, "request id reused")
            return
        try:
            head = ApiRequestHead.parse(payload)
        except ValueError as exc:
            await self._send_error(request_id, _MALFORMED, str(exc))
            return
        if self._in_flight >= MAX_IN_FLIGHT_REQUESTS:
            await self._send_error(request_id, _BUSY, "too many requests")
            return
        self._highest_id = request_id
        state = _RequestState(head)
        self._requests[request_id] = state
        self._in_flight += 1
        state.task = asyncio.ensure_future(self._serve_request(request_id, state))

    def _on_body(self, request_id: int, payload: bytes) -> None:
        """Append one body chunk; past 1 MB the request fails ``too_large``.

        Accounting is by bytes on the one buffer, so empty chunks retain
        nothing and 1-byte chunks trip the same cap as whole bodies.
        """
        state = self._requests.get(request_id)
        if state is None or state.dispatched:
            return
        if len(state.buf) + len(payload) > MAX_REQUEST_BODY_BYTES:
            state.dispatched = True
            assert state.task is not None
            state.task.cancel()
            self._finish(request_id)
            self._spawn_send(
                self._send_error(request_id, _TOO_LARGE, "request body too large")
            )
            return
        state.buf += payload

    def _on_end(self, request_id: int) -> None:
        """Mark a request complete: its task now runs the HTTP dispatch."""
        state = self._requests.get(request_id)
        if state is None or state.dispatched:
            return
        state.dispatched = True
        state.end_event.set()

    def _on_cancel(self, request_id: int) -> None:
        """Drop a request before it answers; unknown ids are a no-op.

        A CANCEL after END still honours the cancellation: the dispatched
        task is cancelled even before it starts, so a racing completion
        cannot answer, and the slot is released through the existing
        cleanup (the task's own ``finally`` is a no-op when it already
        ran, and never runs when the task never started).
        """
        state = self._requests.get(request_id)
        if state is None:
            return
        if state.dispatched:
            state.cancelled = True
            if state.task is not None:
                state.task.cancel()
            self._finish(request_id)
            return
        state.dispatched = True
        state.cancelled = True
        state.end_event.set()

    async def _serve_request(self, request_id: int, state: _RequestState) -> None:
        """Wait for END, run the HTTP dispatch, stream the response.

        One 15 s deadline covers reassembly plus dispatch plus the first
        send attempt; past it the request answers ERROR ``timeout`` and a
        late dispatch result is dropped. A handler exception answers
        ERROR ``internal``: a request always gets its terminal frame. The
        terminal ERROR itself is bounded by the same deadline — past it
        the unwritable channel is closed instead of wedging the task.
        """
        try:
            async with asyncio.timeout(REQUEST_DEADLINE_S):
                await state.end_event.wait()
                if state.cancelled:
                    return
                body = bytes(state.buf)
                status, headers, resp_body = await self._dispatch(state.head, body)
                if state.cancelled:
                    return
                await self._send_response(request_id, status, headers, resp_body)
        except TimeoutError:
            await self._send_error(request_id, _TIMEOUT, "request timed out")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("api: request %d failed", request_id)
            await self._send_error(request_id, _INTERNAL, "internal error")
        finally:
            self._finish(request_id)

    def _finish(self, request_id: int) -> None:
        """Release one in-flight slot exactly once."""
        if self._requests.pop(request_id, None) is not None:
            self._in_flight -= 1

    async def _dispatch(
        self, head: ApiRequestHead, body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        """Answer one assembled request: 404 off-list, else shared HTTP.

        Transmit-capable bodies on reachable routes are refused here, before
        the shared dispatch runs, with 403 ``controller_tx_unsupported``
        (the same code and ``{"error": code}`` shape the control seat
        raises); transmission ends pass through to the shared dispatch.
        """
        if not api_path_allowed(head.method, head.path):
            return 404, {}, b"404 Not Found"
        from .web_routing import api_channel_tx_refusal  # noqa: TID251

        try:
            payload = json.loads(body) if body else None
        except (ValueError, RecursionError):
            payload = None
        refusal = api_channel_tx_refusal(head.method, head.path, payload)
        if refusal is not None:
            return (
                403,
                {"Content-Type": "application/json"},
                json.dumps({"error": refusal}, separators=(",", ":")).encode(),
            )
        return await _serve_one_http(
            self._server,
            head.method,
            head.path,
            head.query,
            head.headers,
            body,
            self._peer,
        )

    async def _send_response(
        self, request_id: int, status: int, headers: dict[str, str], body: bytes
    ) -> None:
        """Stream RES_HEAD + <=16 KB RES_BODY chunks + END, with backpressure."""
        for msg_type, payload in api_response_messages(status, headers, body):
            await self._send_frame(msg_type, request_id, payload)

    async def _send_error(self, request_id: int, code: str, message: str) -> None:
        """Send one terminal ERROR, never raising (the channel may be dead).

        The send is bounded by the existing request deadline: past it the
        channel is unwritable, so it is closed (ending the receive loop)
        instead of wedging the sender.
        """
        with contextlib.suppress(Exception):
            await self._send_frame(
                ApiMessageType.ERROR,
                request_id,
                ApiErrorFrame(code, message).encode(),
                timeout=REQUEST_DEADLINE_S,
            )

    async def _abort_unwritable(self, reason: str) -> None:
        """Close a channel that stayed unwritable past its send bound.

        Closing wakes the receive loop (its ``recv`` raises ``EOFError``),
        so a stuck transport cannot wedge requests past their deadline.
        Bare ``close()`` fits every ``Connection`` (the protocol defaults
        the code and reason, and the data-channel connection takes none).
        """
        with contextlib.suppress(Exception):
            await self._conn.close()

    async def _send_frame(
        self,
        msg_type: int,
        request_id: int,
        payload: bytes,
        *,
        timeout: float | None = None,
    ) -> None:
        """Send one frame, waiting while the transport buffer is full.

        While the data channel reports more than 256 KB still buffered,
        the send waits (short poll for the drain); the receive loop and
        every other request keep running meanwhile. With *timeout* the
        drain wait is bounded by the existing request deadline: past it
        the channel is closed as unwritable and the send raises
        :class:`TimeoutError`.
        """
        source = self._conn if isinstance(self._conn, _BackpressureSource) else None
        if timeout is None:
            while (
                source is not None and source.buffered_bytes() > SEND_HIGH_WATER_BYTES
            ):
                await asyncio.sleep(_DRAIN_POLL_S)
        else:
            try:
                async with asyncio.timeout(timeout):
                    while (
                        source is not None
                        and source.buffered_bytes() > SEND_HIGH_WATER_BYTES
                    ):
                        await asyncio.sleep(_DRAIN_POLL_S)
            except TimeoutError:
                await self._abort_unwritable("send buffer stayed full")
                raise
        await self._conn.send_binary(_encode_frame(msg_type, request_id, payload))


# Golden documents: the concrete frames the browser must handle,
# generated from the server's own encoders.

#: The test that replays every committed golden document for real.
_API_GOLDEN_TESTED_BY: Final = "tests/test_api_channel_golden.py::test_golden_documents_replay_through_real_handlers"

#: Where a live session key goes when replaying a committed attach frame.
_GOLDEN_KEY_PLACEHOLDER: Final = "<SESSION-KEY>"


def api_response_messages(
    status: int, headers: dict[str, str], body: bytes
) -> list[tuple[ApiMessageType, bytes]]:
    """RES_HEAD, <=16 KB RES_BODY chunks, END: the one chunking rule
    shared by the handler's streaming and the golden documents."""
    messages = [(ApiMessageType.RES_HEAD, ApiResponseHead(status, headers).encode())]
    messages.extend(
        (ApiMessageType.RES_BODY, bytes(body[off : off + RESPONSE_CHUNK_BYTES]))
        for off in range(0, len(body), RESPONSE_CHUNK_BYTES)
    )
    messages.append((ApiMessageType.END, b""))
    return messages


def _golden_frame(
    msg_type: ApiMessageType, request_id: int, payload: bytes
) -> dict[str, Any]:
    """One wire frame as a committed golden entry: bytes plus meaning."""
    frame = _encode_frame(int(msg_type), request_id, payload)
    decoded: dict[str, Any] = {"type": msg_type.name, "request_id": request_id}
    if payload:
        try:
            decoded["payload"] = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            decoded["payload_bytes"] = len(payload)
    if len(frame) <= 512:
        return {"hex": frame.hex(), "decoded": decoded}
    return {"b64": base64.b64encode(frame).decode("ascii"), "decoded": decoded}


def _golden_attach_document(label: str) -> dict[str, Any]:
    """The attach frame for one data-channel label (client -> server)."""
    keyless = label == "api"
    text = json.dumps(
        {
            "type": "attach",
            "controller_key": None if keyless else _GOLDEN_KEY_PLACEHOLDER,
            "state_delta": 2,
            "role": "primary",
        }
    )
    outcome = (
        "silent until the first request (the keyless acquire path)"
        if keyless
        else "answered by the attach ack; a resent frame is re-acknowledged"
    )
    return {
        "direction": "client->server",
        "summary": f'the "{label}" attach frame (the first text frame)',
        "text": text,
        "decoded": json.loads(text),
        "template": f"substitute a live controller_key for {_GOLDEN_KEY_PLACEHOLDER}",
        "outcome": outcome,
    }


def _golden_ack_document(kind: str) -> dict[str, Any]:
    """The attach ack for one lossy label (server -> client)."""
    from .channel_attach import ChannelAttachedAck  # noqa: TID251

    text = ChannelAttachedAck(kind).encode()
    return {
        "direction": "server->client",
        "summary": f"the {kind} attach ack (the first server text frame)",
        "text": text,
        "decoded": json.loads(text),
        "outcome": "the client stops resending its attach",
    }


def _request_head(
    method: str, path: str, request_id: int, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    payload = json.dumps(
        {"method": method, "path": path, "query": {}, "headers": headers or {}}
    ).encode()
    return _golden_frame(ApiMessageType.REQ_HEAD, request_id, payload)


async def api_channel_golden_documents() -> dict[str, Any]:
    """Every concrete frame sequence the browser must handle, from the
    server's own encoders; committed to ``api-channel-golden.json`` and
    replayed for real by the test named in ``_tested_by``. Regenerate:

        uv run python -c "import asyncio, json; from rigplane.web.api_channel import api_channel_golden_documents as d; print(json.dumps(asyncio.run(d())))" > frontend/src/lib/transport/api-channel-golden.json
    """
    from .server import _send_response  # noqa: TID251

    gf = _golden_frame

    async def _route_frames(body: bytes) -> tuple[dict[str, Any], list[Any]]:
        writer: Any = _ChannelHttpWriter(("golden", 0))
        await _send_response(
            writer, 200, "OK", body, {"Content-Type": "application/json"}
        )
        status, headers, captured = _split_response(bytes(writer._buf))  # noqa: SLF001
        return {
            "route": "GET /api/v1/band-plan/layers",
            "response_body_b64": base64.b64encode(body).decode("ascii"),
        }, api_response_messages(status, headers, captured)

    refusal_body = json.dumps(
        {"error": "controller_tx_unsupported"}, separators=(",", ":")
    ).encode()
    ok_fixture, ok_reply = await _route_frames(b'{"golden": true}')
    chunked_fixture, chunked_reply = await _route_frames(
        b"A" * RESPONSE_CHUNK_BYTES + b"B"
    )
    end = (ApiMessageType.END, b"")
    Head = tuple[str, str, int, dict[str, str] | None]
    Msgs = list[tuple[ApiMessageType, bytes]]
    exchanges: dict[str, tuple[str, str, Head | None, Msgs, Msgs]] = {
        "api_tx_refused": (
            "a request with a body and its 403, id 1",
            "a TX-capable body is refused before the shared dispatch",
            (
                "POST",
                "/api/v1/civ/transaction",
                1,
                {"content-type": "application/json"},
            ),
            [
                (
                    ApiMessageType.REQ_BODY,
                    json.dumps({"command": 3, "expect": "data"}).encode(),
                ),
                end,
            ],
            api_response_messages(
                403, {"Content-Type": "application/json"}, refusal_body
            ),
        ),
        "api_not_found": (
            "a request for an unknown path and its 404, id 2",
            "only /api/v1/* table entries are routed",
            ("GET", "/api/v1/nonexistent", 2, None),
            [end],
            api_response_messages(404, {}, b"404 Not Found"),
        ),
        "api_ok": (
            "a plain GET and its normal 200 response, id 3",
            "RES_HEAD, one RES_BODY per <= 16 KB of body, then END",
            ("GET", "/api/v1/band-plan/layers", 3, None),
            [end],
            ok_reply,
        ),
        "api_chunked": (
            "a chunked multi-message body (> 16 KB), id 4",
            "RES_HEAD, 16384-byte + 1-byte RES_BODYs, END; concatenated body",
            ("GET", "/api/v1/band-plan/layers", 4, None),
            [end],
            chunked_reply,
        ),
        "api_cancel": (
            "CANCEL drops request 41 before it answers",
            "no frame is ever sent for id 41; the channel keeps serving",
            ("GET", "/api/v1/info", 41, None),
            [(ApiMessageType.CANCEL, b"")],
            [],
        ),
        "api_unknown_id_ignored": (
            "frames for an unknown id are a no-op, id 9999",
            "no ERROR, no answer; the channel keeps serving",
            None,
            [(ApiMessageType.REQ_BODY, b"x"), end, (ApiMessageType.CANCEL, b"")],
            [],
        ),
    }
    documents: dict[str, Any] = {
        f"attach_{label}": _golden_attach_document(label)
        for label in ("control", "scope", "audio", "api")
    }
    documents.update(
        {
            f"attach_ack_{kind}": _golden_ack_document(kind)
            for kind in ("scope", "audio")
        }
    )
    for name, (summary, outcome, head, trailing, reply) in exchanges.items():
        rid = (head or (None, None, 0))[2]
        documents[name] = {
            "direction": "client->server then server->client",
            "summary": summary,
            "request": [
                *([] if head is None else [_request_head(*head)]),
                *(gf(t, rid, p) for t, p in trailing),
            ],
            "reply": [gf(m, rid, p) for m, p in reply],
            "outcome": outcome,
        }
    documents["api_ok"].update(ok_fixture)
    documents["api_chunked"].update(chunked_fixture)
    errors = {  # code: (request_id, message, trigger)
        "stale_id": (
            5,
            "request id reused",
            "resend the api_not_found head with id 5: the resend answers "
            "stale_id (a rejected head never consumes its id)",
        ),
        "timeout": (
            6,
            "request timed out",
            "reassembly or dispatch outlasting request_deadline_s answers timeout",
        ),
        "too_large": (
            7,
            "request body too large",
            "REQ_HEAD id 7 then 16384-byte REQ_BODYs of 'x': past "
            "max_request_body_bytes",
        ),
        "busy": (
            40,
            "too many requests",
            "33 REQ_HEADs with no END: ids 8..39 fill the 32 slots; the "
            "33rd answers busy",
        ),
        "malformed": (
            0,
            "frame too short",
            "any frame shorter than the 7-byte header (the bytes 01 02 00 00)",
        ),
    }
    documents["api_errors"] = {
        "direction": "server->client",
        "summary": "every terminal ERROR frame, with its trigger",
        "triggers": {code: spec[2] for code, spec in errors.items()},
        "frames": [
            gf(ApiMessageType.ERROR, rid, ApiErrorFrame(code, message).encode())
            for code, (rid, message, _trigger) in errors.items()
        ],
    }
    return {
        "version": 1,
        "_tested_by": _API_GOLDEN_TESTED_BY,
        "formats": "text frames carry the exact UTF-8 text; binary frames "
        "carry whole-frame hex (base64 when > 512 bytes), decoded alongside",
        "documents": documents,
    }
