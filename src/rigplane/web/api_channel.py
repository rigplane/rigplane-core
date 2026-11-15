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
back into status/headers/body. An explicit allow-list below is the
routing for this transport; anything else answers 404.

TX-capable REST is excluded from the allow-list even where HTTP serves
it: ``POST /api/v1/managed-transmit/command`` and ``POST
/api/v1/civ/transaction`` both key the transmitter. Over a data channel
the UI keys PTT through the control channel command instead, which is
fenced by the controller session. A data-channel REST path must not
become a second, unfenced TX keying mechanism beside it, so excluded
paths read exactly like unknown ones (404, indistinguishable by design)
even with a valid controller key.

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
import contextlib
import json
import logging
import struct
from dataclasses import dataclass
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
    "ApiRequestHead",
    "api_path_allowed",
]

logger = logging.getLogger(__name__)

#: Wire version; a mismatch answers ERROR ``bad_version`` and closes.
API_CHANNEL_VERSION: Final = 1

#: Message types on the wire.
_REQ_HEAD: Final = 0x01
_REQ_BODY: Final = 0x02
_RES_HEAD: Final = 0x03
_RES_BODY: Final = 0x04
_END: Final = 0x05
_ERROR: Final = 0x06
_CANCEL: Final = 0x07

#: Binary header ``[u8 version][u8 type][u32 request_id BE][u8 flags]``.
_FRAME_HEADER: Final = struct.Struct(">BBIB")

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
        except (UnicodeDecodeError, ValueError) as exc:
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
        return json.dumps(
            {"status": self.status, "headers": self.headers}
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ApiErrorFrame:
    """A terminal ERROR: the machine-readable failure for one request."""

    code: str
    message: str

    def encode(self) -> bytes:
        """Encode as the JSON payload of an ERROR frame."""
        return json.dumps(
            {"code": self.code, "message": self.message}
        ).encode("utf-8")


def _encode_frame(msg_type: int, request_id: int, payload: bytes) -> bytes:
    """Encode one wire message (always version 1, reserved flags 0)."""
    return _FRAME_HEADER.pack(API_CHANNEL_VERSION, msg_type, request_id, 0) + payload


# (method, path) pairs the api channel serves; anything else answers 404.
# Mirrors the frontend's fetch surface (frontend/src `fetch(` + `/api/v1/`
# literals), minus the TX-capable REST the module docstring excludes.
_API_ALLOW_LIST: Final = frozenset(
    {
        ("GET", "/api/v1/controller"),
        ("POST", "/api/v1/controller"),
        ("DELETE", "/api/v1/controller"),
        ("GET", "/api/v1/info"),
        ("GET", "/api/v1/capabilities"),
        ("GET", "/api/v1/band-plan/segments"),
        ("GET", "/api/v1/band-plan/layers"),
        ("GET", "/api/v1/band-plan/config"),
        ("POST", "/api/v1/band-plan/config"),
        ("POST", "/api/v1/eibi/fetch"),
        ("GET", "/api/v1/eibi/status"),
        ("GET", "/api/v1/eibi/stations"),
        ("GET", "/api/v1/eibi/segments"),
        ("GET", "/api/v1/eibi/identify"),
        ("GET", "/api/v1/eibi/bands"),
        ("GET", "/api/v1/memories"),
        ("POST", "/api/v1/radio/power"),
        ("GET", "/api/v1/managed-transmit"),
        ("PUT", "/api/v1/managed-transmit/tot"),
        ("POST", "/api/v1/diagnose/preview"),
        ("POST", "/api/v1/diagnose/send"),
        ("POST", "/api/v1/diagnose/save"),
    }
)

#: Prefix whose DELETE carries a preview id (``/preview/<id>``).
_DIAGNOSE_DELETE_PREFIX: Final = "/api/v1/diagnose/preview/"


def api_path_allowed(method: str, path: str) -> bool:
    """True when the api channel routes ``(method, path)``.

    The allow-list is the routing for this transport: anything else (an
    unknown path, a TX-capable path, or a listed path under the wrong
    method) answers 404 without touching controller state.
    """
    if (method, path) in _API_ALLOW_LIST:
        return True
    return method == "DELETE" and path.startswith(_DIAGNOSE_DELETE_PREFIX)


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
    """One request from an accepted REQ_HEAD to its terminal frame."""

    __slots__ = (
        "cancelled",
        "chunks",
        "dispatched",
        "end_event",
        "head",
        "size",
        "task",
    )

    head: ApiRequestHead
    chunks: list[bytes]
    size: int
    end_event: asyncio.Event
    cancelled: bool
    dispatched: bool
    task: asyncio.Task[None] | None

    def __init__(self, head: ApiRequestHead) -> None:
        self.head = head
        self.chunks: list[bytes] = []
        self.size = 0
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
        self._highest_id = 0
        self._in_flight = 0
        self._done = False
        source = conn if isinstance(conn, _BackpressureSource) else None
        if source is not None:
            with contextlib.suppress(Exception):
                source.set_buffer_low_watermark(SEND_LOW_WATER_BYTES)

    @property
    def done(self) -> bool:
        """True once the handler left its receive loop (channel over)."""
        return self._done

    async def run(self) -> None:
        """Route frames until the channel closes, then release everything."""
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
        if msg_type == _REQ_HEAD:
            await self._on_head(request_id, body)
        elif msg_type == _REQ_BODY:
            self._on_body(request_id, body)
        elif msg_type == _END:
            self._on_end(request_id)
        elif msg_type == _CANCEL:
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
        """Append one body chunk; past 1 MB the request fails ``too_large``."""
        state = self._requests.get(request_id)
        if state is None or state.dispatched:
            return
        state.chunks.append(payload)
        state.size += len(payload)
        if state.size > MAX_REQUEST_BODY_BYTES:
            state.dispatched = True
            assert state.task is not None
            state.task.cancel()
            self._finish(request_id)
            asyncio.ensure_future(self._send_error(
                request_id, _TOO_LARGE, "request body too large"))

    def _on_end(self, request_id: int) -> None:
        """Mark a request complete: its task now runs the HTTP dispatch."""
        state = self._requests.get(request_id)
        if state is None or state.dispatched:
            return
        state.dispatched = True
        state.end_event.set()

    def _on_cancel(self, request_id: int) -> None:
        """Drop a request before it answers; unknown ids are a no-op."""
        state = self._requests.get(request_id)
        if state is None or state.dispatched:
            return
        state.dispatched = True
        state.cancelled = True
        state.end_event.set()

    async def _serve_request(self, request_id: int, state: _RequestState) -> None:
        """Wait for END, run the HTTP dispatch, stream the response.

        One 15 s deadline covers reassembly plus dispatch plus the first
        send attempt; past it the request answers ERROR ``timeout`` and a
        late dispatch result is dropped. A handler exception answers
        ERROR ``internal``: a request always gets its terminal frame.
        """
        try:
            async with asyncio.timeout(REQUEST_DEADLINE_S):
                await state.end_event.wait()
                if state.cancelled:
                    return
                body = b"".join(state.chunks)
                state.chunks.clear()
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
        """Answer one assembled request: 404 off-list, else shared HTTP."""
        if not api_path_allowed(head.method, head.path):
            return 404, {}, b"404 Not Found"
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
        await self._send_frame(
            _RES_HEAD, request_id, ApiResponseHead(status, headers).encode()
        )
        for offset in range(0, len(body), RESPONSE_CHUNK_BYTES):
            await self._send_frame(
                _RES_BODY, request_id, body[offset : offset + RESPONSE_CHUNK_BYTES]
            )
        await self._send_frame(_END, request_id, b"")

    async def _send_error(self, request_id: int, code: str, message: str) -> None:
        """Send one terminal ERROR, never raising (the channel may be dead)."""
        with contextlib.suppress(Exception):
            await self._send_frame(
                _ERROR, request_id, ApiErrorFrame(code, message).encode()
            )

    async def _send_frame(self, msg_type: int, request_id: int, payload: bytes) -> None:
        """Send one frame, waiting while the transport buffer is full.

        While the data channel reports more than 256 KB still buffered,
        the send waits (short poll for the drain); the receive loop and
        every other request keep running meanwhile.
        """
        source = self._conn if isinstance(self._conn, _BackpressureSource) else None
        while source is not None and source.buffered_bytes() > SEND_HIGH_WATER_BYTES:
            await asyncio.sleep(_DRAIN_POLL_S)
        await self._conn.send_binary(_encode_frame(msg_type, request_id, payload))
