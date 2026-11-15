"""The ``api`` data channel: REST over a reliable WebRTC data channel.

A browser that reaches Core only over WebRTC data channels has no HTTP.
The ``api`` label carries the web UI's REST surface (``GET /api/v1/info``,
``POST /api/v1/controller``, ...) through one ordered/reliable data
channel, attached through the SAME ``attach_client_channel`` /
``ClientChannelParams`` path as control/scope/audio. Every request is
served by Core's existing HTTP routing in-process; the channel only
adds an allow-listed, multiplexed framing.

Wire format under test (binary header ``[u8 version][u8 type][u32 id][u8 flags]``):
types 1=REQ_HEAD ``{method,path,query,headers}`` 2=REQ_BODY 3=RES_HEAD
``{status,headers}`` 4=RES_BODY 5=END 6=ERROR ``{code,message}`` 7=CANCEL.

These tests drive REAL aiortc channels (like
``tests/test_webrtc_attach_path.py``) and hand-roll the frames, so the
file is an independent oracle of the wire contract, not a re-read of the
implementation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from rigplane.testing.timing import await_until
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.transport.webrtc import webrtc_available

_webrtc = pytest.mark.skipif(
    not webrtc_available(), reason="aiortc extra not installed"
)

# Wire constants (mirrored from the contract, not imported: this file is
# the independent check).
_API_VERSION = 1
_REQ_HEAD = 1
_REQ_BODY = 2
_RES_HEAD = 3
_RES_BODY = 4
_END = 5
_ERROR = 6
_CANCEL = 7
_HEADER = struct.Struct(">BBIB")
_CHUNK = 16 * 1024


class _ApiError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _frame(msg_type: int, req_id: int, payload: bytes, version: int = 1) -> bytes:
    return _HEADER.pack(version, msg_type, req_id, 0) + payload


def _head(method: str, path: str, query: dict[str, list[str]] | None = None,
          headers: dict[str, str] | None = None) -> bytes:
    return json.dumps(
        {"method": method, "path": path,
         "query": query or {}, "headers": headers or {}}
    ).encode()


@dataclass
class _Response:
    status: int
    headers: dict[str, Any]
    body: bytes


async def _http_raw(port: int, method: str, path: str,
                    body: bytes = b"") -> tuple[int, bytes]:
    """Minimal raw HTTP helper: returns (status, raw body bytes)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        head = (
            f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode() + body
        writer.write(head)
        await writer.drain()
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10.0)
        status = int(raw.split(b" ", 2)[1])
        rest = await asyncio.wait_for(reader.read(), timeout=10.0)
        return status, rest
    finally:
        writer.close()


class _ApiClient:
    """A browser-side ``api`` peer over a real aiortc RTCDataChannel."""

    def __init__(self) -> None:
        self._pc: Any = None
        self._channel: Any = None
        self._received: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = asyncio.Event()
        self._queues: dict[int, asyncio.Queue[Any]] = {}
        self._pump_task: asyncio.Task[None] | None = None

    async def connect(self, port: int) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription

        self._pc = RTCPeerConnection()
        self._channel = self._pc.createDataChannel("api", ordered=True)
        queue = self._received
        closed = self._closed

        @self._channel.on("message")  # type: ignore[misc, no-untyped-call]
        def _on_message(message: Any) -> None:
            queue.put_nowait(message)

        @self._channel.on("close")  # type: ignore[misc, no-untyped-call]
        def _on_close() -> None:
            closed.set()

        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            payload = json.dumps(
                {"sdp": self._pc.localDescription.sdp,
                 "type": self._pc.localDescription.type}
            ).encode()
            writer.write(
                f"POST /api/v1/transport/webrtc/offer HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Content-Length: {len(payload)}\r\n"
                "Content-Type: application/json\r\n"
                "Connection: close\r\n\r\n".encode() + payload
            )
            await writer.drain()
            head = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=10.0)
            assert head.split(b" ", 2)[1] == b"200", head[:200]
            answer = json.loads(await asyncio.wait_for(reader.read(), timeout=10.0))
        finally:
            writer.close()
        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
        )
        await await_until(
            lambda: self._channel.readyState == "open",
            timeout=15.0,
            description="api data channel open",
        )

    async def attach(self, key: str | None) -> None:
        """Send the attach frame; the api channel answers with no hello."""
        assert self._channel is not None
        self._channel.send(json.dumps(
            {"type": "attach", "controller_key": key,
             "state_delta": 2, "role": "primary"}))
        # No hello is defined for the api channel; give the server a
        # moment to refuse (a refusal closes the channel).
        await asyncio.sleep(0.5)
        assert not self._closed.is_set(), "api attach refused"
        await self.start_pump()

    def send_raw(self, data: bytes) -> None:
        assert self._channel is not None
        self._channel.send(data)

    async def start_pump(self) -> None:
        """Demultiplex inbound frames by request id (one shared reader)."""
        self._queues = {}
        self._pump_task = asyncio.ensure_future(self._pump())

    async def _pump(self) -> None:
        while True:
            message = await self._received.get()
            assert isinstance(message, bytes), message
            assert len(message) >= _HEADER.size, message
            version, msg_type, req_id, _flags = _HEADER.unpack(
                message[: _HEADER.size])
            assert version == _API_VERSION, (version, message[:8])
            payload = message[_HEADER.size:]
            queue = self._queues.setdefault(req_id, asyncio.Queue())
            if msg_type == _ERROR:
                obj = json.loads(payload.decode())
                queue.put_nowait(_ApiError(obj["code"], obj.get("message", "")))
            else:
                queue.put_nowait((msg_type, payload))

    async def _next_for(self, req_id: int,
                        timeout: float = 10.0) -> tuple[int, bytes]:
        """Next frame for one request id; a server ERROR raises _ApiError."""
        item = await asyncio.wait_for(
            self._queues.setdefault(req_id, asyncio.Queue()).get(), timeout)
        if isinstance(item, _ApiError):
            raise item
        return item

    async def request(self, req_id: int, method: str, path: str,
                      query: dict[str, list[str]] | None =None,
                      headers: dict[str, str] | None = None,
                      body: bytes = b"") -> _Response:
        """One full request: HEAD [+BODY chunks] + END, collect to END."""
        assert self._pump_task is not None, "start_pump first"
        self.send_raw(_frame(_REQ_HEAD, req_id, _head(method, path, query, headers)))
        for off in range(0, len(body), _CHUNK):
            self.send_raw(_frame(_REQ_BODY, req_id, body[off: off + _CHUNK]))
        self.send_raw(_frame(_END, req_id, b""))
        status = 0
        resp_headers: dict[str, Any] = {}
        chunks: list[bytes] = []
        while True:
            msg_type, payload = await self._next_for(req_id)
            if msg_type == _RES_HEAD:
                obj = json.loads(payload.decode())
                status, resp_headers = obj["status"], obj["headers"]
            elif msg_type == _RES_BODY:
                assert 0 < len(payload) <= _CHUNK, len(payload)
                chunks.append(payload)
            elif msg_type == _END:
                return _Response(status, resp_headers, b"".join(chunks))
            else:  # pragma: no cover - server must not emit anything else here
                raise AssertionError(f"unexpected frame type {msg_type}")

    async def expect_error(self, req_id: int, code: str,
                           timeout: float = 10.0) -> str:
        """The next frame for req_id must be ERROR with this code."""
        try:
            await self._next_for(req_id, timeout)
        except _ApiError as exc:
            assert exc.code == code, (exc.code, exc.message)
            return exc.message
        raise AssertionError(f"expected ERROR {code} for request {req_id}")

    async def close(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            with contextlib.suppress(Exception):
                await self._pump_task
        if self._channel is not None:
            with contextlib.suppress(Exception):
                self._channel.close()
        if self._pc is not None:
            with contextlib.suppress(Exception):
                await self._pc.close()


async def _stand_up_light() -> WebServer:
    server = WebServer(
        None,
        WebConfig(host="127.0.0.1", port=0, discovery=False, webrtc_enabled=True),
    )
    await server.start()
    return server


async def _tear_down(server: WebServer, *clients: _ApiClient) -> None:
    for client in clients:
        with contextlib.suppress(Exception):
            await client.close()
    with contextlib.suppress(Exception):
        await server._webrtc_sessions.close_all()  # noqa: SLF001
    with contextlib.suppress(Exception):
        await server.stop()


# ---------------------------------------------------------------------------
# Happy paths: keyless attach, byte-identical bodies, acquire -> control.
# ---------------------------------------------------------------------------


@_webrtc
async def test_api_info_and_capabilities_match_http() -> None:
    """GET /info and /capabilities over api return byte-identical bodies."""
    server = await _stand_up_light()
    client = _ApiClient()
    try:
        await client.connect(server.port)
        await client.attach(None)
        for req_id, path in ((1, "/api/v1/info"), (2, "/api/v1/capabilities")):
            resp = await client.request(req_id, "GET", path)
            assert resp.status == 200, (path, resp.status)
            http_status, http_body = await _http_raw(server.port, "GET", path)
            assert http_status == 200, (path, http_status)
            assert resp.body == http_body, path
    finally:
        await _tear_down(server, client)


@_webrtc
async def test_api_acquire_grant_keys_control_channel(tmp_path: Path) -> None:
    """End-to-end: keyless api -> POST /controller grant -> control hello."""
    from test_webrtc_attach_path import (  # noqa: E402
        _DcControlClient,
        _stand_up_stack,
        _tear_down_stack,
    )

    stack = await _stand_up_stack(tmp_path)
    api = _ApiClient()
    dc = _DcControlClient()
    try:
        await api.connect(stack.web.port)
        await api.attach(None)
        resp = await api.request(
            1, "POST", "/api/v1/controller",
            headers={"content-type": "application/json"},
            body=json.dumps({"kind": "pro"}).encode(),
        )
        assert resp.status == 201, (resp.status, resp.body[:200])
        key = json.loads(resp.body.decode())["controller_key"]
        await dc.connect(stack.web)
        hello = await dc.attach(key)
        assert hello["type"] == "hello"
    finally:
        await api.close()
        await dc.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_api_large_response_arrives_in_chunks() -> None:
    """A ~300 KB response arrives intact in <=16 KB RES_BODY frames."""
    from unittest.mock import patch

    from rigplane.web import server as server_module

    big = {"layers": [{"id": i, "name": "x" * 300} for i in range(900)]}
    raw = json.dumps(big, separators=(",", ":")).encode()
    assert len(raw) > 300_000, len(raw)

    async def _big_layers(writer: Any) -> None:
        await server_module._send_response(
            writer, 200, "OK", raw, {"Content-Type": "application/json"})

    server = await _stand_up_light()
    client = _ApiClient()
    try:
        await client.connect(server.port)
        await client.attach(None)
        with patch.object(
            WebServer, "_serve_band_plan_layers",
            lambda self, writer: _big_layers(writer),
        ):
            resp = await client.request(1, "GET", "/api/v1/band-plan/layers")
        assert resp.status == 200, (resp.status, resp.body[:200])
        assert resp.body == raw
        assert len(resp.body) > 300_000
    finally:
        await _tear_down(server, client)


@_webrtc
async def test_api_concurrent_requests_do_not_block() -> None:
    """10 parallel requests: 9 fast finish before 1 deliberately slow one."""
    from unittest.mock import patch

    server = await _stand_up_light()
    client = _ApiClient()
    try:
        await client.connect(server.port)
        await client.attach(None)

        real_caps = WebServer._serve_capabilities

        async def _slow_caps(self: WebServer, writer: Any,
                             headers: Any = None) -> None:
            await asyncio.sleep(3.0)
            await real_caps(self, writer, headers)

        async def _one(req_id: int, path: str) -> _Response:
            return await client.request(req_id, "GET", path)

        with patch.object(WebServer, "_serve_capabilities", _slow_caps):
            tasks = [asyncio.ensure_future(_one(i, "/api/v1/info"))
                     for i in range(1, 10)]
            tasks.append(asyncio.ensure_future(_one(10, "/api/v1/capabilities")))
            done_order: list[int] = []
            pending = set(tasks)
            while pending:
                finished, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED)
                for task in finished:
                    done_order.append(tasks.index(task) + 1)
            results = [t.result() for t in tasks]
        assert all(r.status == 200 for r in results)
        # The slow capabilities request (id 10) finishes last: no HOL block.
        assert done_order[-1] == 10, done_order
    finally:
        await _tear_down(server, client)


# ---------------------------------------------------------------------------
# Errors: routing, framing, limits, cancellation, deadline.
# ---------------------------------------------------------------------------


@_webrtc
async def test_api_unknown_path_is_404() -> None:
    """A path outside the allow-list answers 404, like HTTP would."""
    server = await _stand_up_light()
    client = _ApiClient()
    try:
        await client.connect(server.port)
        await client.attach(None)
        resp = await client.request(1, "GET", "/api/v1/nonexistent")
        assert resp.status == 404, (resp.status, resp.body[:200])
    finally:
        await _tear_down(server, client)


@_webrtc
async def test_api_excluded_tx_paths_are_404() -> None:
    """TX-capable REST stays off the channel: 404 even though HTTP serves it.

    POST /managed-transmit/command and POST /civ/transaction key the
    transmitter; over a data channel the UI keys PTT through the control
    channel command instead, fenced by the controller session. The
    allow-list is the routing for this transport, so excluded paths read
    exactly like unknown ones: 404, indistinguishable by design.
    """
    server = await _stand_up_light()
    client = _ApiClient()
    try:
        await client.connect(server.port)
        await client.attach(None)
        for req_id, method, path in (
            (1, "POST", "/api/v1/managed-transmit/command"),
            (2, "POST", "/api/v1/civ/transaction"),
        ):
            resp = await client.request(
                req_id, method, path,
                headers={"content-type": "application/json"},
                body=b"{}",
            )
            assert resp.status == 404, (path, resp.status, resp.body[:200])
        # And the channel still serves afterwards.
        resp = await client.request(3, "GET", "/api/v1/info")
        assert resp.status == 200
    finally:
        await _tear_down(server, client)


@_webrtc
async def test_api_bad_version_errors_and_closes() -> None:
    """A version mismatch answers ERROR and then closes the channel."""
    server = await _stand_up_light()
    client = _ApiClient()
    try:
        await client.connect(server.port)
        await client.attach(None)
        client.send_raw(_frame(_REQ_HEAD, 1, _head("GET", "/api/v1/info"),
                               version=99))
        try:
            await client._next_for(1)
        except _ApiError as exc:
            assert exc.code == "bad_version", exc.code
        else:  # pragma: no cover
            raise AssertionError("expected ERROR bad_version")
        await await_until(
            lambda: client._closed.is_set()
            or (client._channel is not None
                and client._channel.readyState == "closed"),
            timeout=10.0,
            description="api channel closed after bad version",
        )
    finally:
        await _tear_down(server, client)


@_webrtc
async def test_api_malformed_frames_error_without_closing() -> None:
    """Short frames and bad JSON answer ERROR; the channel stays usable."""
    server = await _stand_up_light()
    client = _ApiClient()
    try:
        await client.connect(server.port)
        await client.attach(None)
        # A runt that cannot even carry the 7-byte header (id 0: no request).
        client.send_raw(b"\x01\x02")
        await client.expect_error(0, "malformed")
        # A well-formed header with a non-JSON head payload.
        client.send_raw(_frame(_REQ_HEAD, 1, b"{nope"))
        await client.expect_error(1, "malformed")
        # A head naming a wrong-typed field.
        client.send_raw(_frame(
            _REQ_HEAD, 2, json.dumps({"method": "GET", "path": 42}).encode()))
        await client.expect_error(2, "malformed")
        # The channel still serves afterwards, with fresh ids.
        resp = await client.request(3, "GET", "/api/v1/info")
        assert resp.status == 200
    finally:
        await _tear_down(server, client)


@_webrtc
async def test_api_reused_id_is_rejected() -> None:
    """Request ids are strictly increasing: reuse answers ERROR stale."""
    server = await _stand_up_light()
    client = _ApiClient()
    try:
        await client.connect(server.port)
        await client.attach(None)
        resp = await client.request(5, "GET", "/api/v1/info")
        assert resp.status == 200
        client.send_raw(_frame(_REQ_HEAD, 5, _head("GET", "/api/v1/info")))
        client.send_raw(_frame(_END, 5, b""))
        await client.expect_error(5, "stale_id")
        client.send_raw(_frame(_REQ_HEAD, 4, _head("GET", "/api/v1/info")))
        client.send_raw(_frame(_END, 4, b""))
        await client.expect_error(4, "stale_id")
        resp = await client.request(6, "GET", "/api/v1/info")
        assert resp.status == 200
    finally:
        await _tear_down(server, client)


@_webrtc
async def test_api_body_over_1mb_is_rejected() -> None:
    """An assembled body past 1 MB answers ERROR too_large, no dispatch."""
    server = await _stand_up_light()
    client = _ApiClient()
    try:
        await client.connect(server.port)
        await client.attach(None)
        client.send_raw(_frame(_REQ_HEAD, 1, _head(
            "POST", "/api/v1/eibi/fetch",
            headers={"content-type": "application/json"})))
        big = b"x" * (1024 * 1024 + 1)
        for off in range(0, len(big), _CHUNK):
            client.send_raw(_frame(_REQ_BODY, 1, big[off: off + _CHUNK]))
        await client.expect_error(1, "too_large")
        # The next id is still accepted: the rejected request holds no slot.
        resp = await client.request(2, "GET", "/api/v1/info")
        assert resp.status == 200
    finally:
        await _tear_down(server, client)


@_webrtc
async def test_api_33rd_in_flight_is_busy() -> None:
    """32 requests in flight is the cap: the 33rd HEAD answers ERROR busy."""
    from unittest.mock import patch

    server = await _stand_up_light()
    client = _ApiClient()
    gate = asyncio.Event()
    started = 0
    real_caps = WebServer._serve_capabilities

    async def _gated_caps(self: WebServer, writer: Any,
                          headers: Any = None) -> None:
        nonlocal started
        started += 1
        await gate.wait()
        await real_caps(self, writer, headers)

    try:
        await client.connect(server.port)
        await client.attach(None)
        with patch.object(WebServer, "_serve_capabilities", _gated_caps):
            for req_id in range(1, 33):
                client.send_raw(_frame(
                    _REQ_HEAD, req_id, _head("GET", "/api/v1/capabilities")))
                client.send_raw(_frame(_END, req_id, b""))
            # Every dispatched request parks on the gate holding its slot;
            # only then is the 33rd HEAD guaranteed to see a full house.
            await await_until(
                lambda: started == 32,
                timeout=10.0,
                description="32 api requests in flight",
            )
            client.send_raw(_frame(
                _REQ_HEAD, 33, _head("GET", "/api/v1/capabilities")))
            client.send_raw(_frame(_END, 33, b""))
            await client.expect_error(33, "busy")
            gate.set()
            for req_id in range(1, 33):
                msg_type, payload = await client._next_for(req_id)
                assert msg_type == _RES_HEAD, (req_id, msg_type)
                obj = json.loads(payload.decode())
                assert obj["status"] == 200, (req_id, obj)
    finally:
        gate.set()
        await _tear_down(server, client)


@_webrtc
async def test_api_cancel_drops_request() -> None:
    """CANCEL before END: no response frames, the slot and id are handled."""
    server = await _stand_up_light()
    client = _ApiClient()
    try:
        await client.connect(server.port)
        await client.attach(None)
        client.send_raw(_frame(_REQ_HEAD, 1, _head("GET", "/api/v1/info")))
        client.send_raw(_frame(_CANCEL, 1, b""))
        client.send_raw(_frame(_END, 1, b""))
        # No RES_HEAD/RES_BODY/END must arrive for the cancelled request.
        with pytest.raises(asyncio.TimeoutError):
            await client._next_for(1, timeout=1.0)
        # A fresh id still works.
        resp = await client.request(2, "GET", "/api/v1/info")
        assert resp.status == 200
    finally:
        await _tear_down(server, client)


@_webrtc
async def test_api_deadline_errors_slow_request() -> None:
    """Past the per-request deadline the server answers ERROR timeout."""
    from unittest.mock import patch

    from rigplane.web import api_channel as api_mod

    server = await _stand_up_light()
    real_caps = WebServer._serve_capabilities

    async def _slow_caps(self: WebServer, writer: Any,
                         headers: Any = None) -> None:
        await asyncio.sleep(30.0)
        await real_caps(self, writer, headers)

    with patch.object(api_mod, "REQUEST_DEADLINE_S", 0.3):
        client = _ApiClient()
        try:
            await client.connect(server.port)
            await client.attach(None)
            with patch.object(WebServer, "_serve_capabilities", _slow_caps):
                client.send_raw(_frame(
                    _REQ_HEAD, 1, _head("GET", "/api/v1/capabilities")))
                client.send_raw(_frame(_END, 1, b""))
                await client.expect_error(1, "timeout", timeout=10.0)
            # The channel stays usable after a timeout.
            resp = await client.request(2, "GET", "/api/v1/info")
            assert resp.status == 200
        finally:
            await _tear_down(server, client)
