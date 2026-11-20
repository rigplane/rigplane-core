"""The ``api`` channel chunking contract (MOR-272): frames of at most 16 KB
in both directions, at most 32 KB buffered before a sender waits.

The NAT bench showed a 1 MiB body on ``api`` at 1 % loss holding back the
``control`` channel's controller heartbeat past the 6 s session TTL:
aiortc moves data-channel messages into SCTP one at a time from ONE queue
shared by every channel and cannot interleave inside a message. These
tests pin the contract (encoder/parser, both chunkers, the receiver's
refusal of an oversize frame) and model that shared queue to prove a
control frame never waits behind more than the high-water mark plus two
frames, while the 1 MiB body still arrives intact.
"""

from __future__ import annotations

import asyncio
import collections
import json

import pytest

from rigplane.web import api_channel as api_mod
from rigplane.web.api_channel import (
    RESPONSE_CHUNK_BYTES,
    SEND_HIGH_WATER_BYTES,
    ApiChannelHandler,
    ApiFrame,
    ApiFrameError,
    ApiMessageType,
    ApiRequestHead,
    api_request_messages,
    api_response_messages,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.ws import WS_OP_BINARY

_MIB = 1024 * 1024
_HEADER = 7


def test_limits_are_16k_frames_and_32k_buffer() -> None:
    assert RESPONSE_CHUNK_BYTES == 16 * 1024
    assert SEND_HIGH_WATER_BYTES == 32 * 1024
    assert api_mod.SEND_LOW_WATER_BYTES == 16 * 1024


def test_frame_round_trip() -> None:
    frame = ApiFrame(ApiMessageType.REQ_BODY, 7, b"abc")
    wire = frame.encode()
    assert wire == bytes([1, 2, 0, 0, 0, 7, 0]) + b"abc"
    assert ApiFrame.parse(wire) == frame


def test_frame_encode_refuses_oversize_payload() -> None:
    ApiFrame(ApiMessageType.RES_BODY, 1, b"x" * RESPONSE_CHUNK_BYTES).encode()
    with pytest.raises(ValueError):
        ApiFrame(ApiMessageType.RES_BODY, 1, b"x" * (RESPONSE_CHUNK_BYTES + 1)).encode()


@pytest.mark.parametrize(
    ("wire", "code", "request_id"),
    [
        (b"\x01\x02", "malformed", 0),
        (bytes([2, 1, 0, 0, 0, 9, 0]), "bad_version", 9),
        (bytes([1, 2, 0, 0, 0, 5, 0]) + b"x" * (16 * 1024 + 1), "too_large", 5),
    ],
)
def test_frame_parse_refusals(wire: bytes, code: str, request_id: int) -> None:
    with pytest.raises(ApiFrameError) as info:
        ApiFrame.parse(wire)
    assert (info.value.code, info.value.request_id) == (code, request_id)


def test_request_messages_chunk_the_body() -> None:
    head = ApiRequestHead("POST", "/api/v1/x", {"a": ["1"]}, {"k": "v"})
    body = b"A" * (2 * RESPONSE_CHUNK_BYTES + 1)
    messages = api_request_messages(head, body)
    assert [t for t, _ in messages] == [
        ApiMessageType.REQ_HEAD,
        ApiMessageType.REQ_BODY,
        ApiMessageType.REQ_BODY,
        ApiMessageType.REQ_BODY,
        ApiMessageType.END,
    ]
    assert ApiRequestHead.parse(messages[0][1]) == head
    assert b"".join(p for t, p in messages if t == ApiMessageType.REQ_BODY) == body
    assert all(len(p) <= RESPONSE_CHUNK_BYTES for _, p in messages)
    assert all(
        len(p) <= RESPONSE_CHUNK_BYTES for _, p in api_response_messages(200, {}, body)
    )


class _Conn:
    """A connection that records frames and never drains (no aiortc)."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def recv(self) -> tuple[int, bytes]:
        raise EOFError

    async def send_text(self, text: str) -> None:
        raise AssertionError("api channel never sends text")

    async def send_binary(self, data: bytes) -> None:
        self.sent.append(bytes(data))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        pass

    def abort(self) -> None:
        pass

    def is_alive(self) -> bool:
        return True


def _errors(conn: _Conn) -> list[tuple[int, str]]:
    out = []
    for wire in conn.sent:
        frame = ApiFrame.parse(wire)
        if frame.type == ApiMessageType.ERROR:
            out.append((frame.request_id, json.loads(frame.payload)["code"]))
    return out


def _head(path: str = "/api/v1/info") -> bytes:
    return ApiRequestHead("POST", path, {}, {}).encode()


async def test_oversize_request_body_frame_fails_the_request() -> None:
    """A REQ_BODY over 16 KB is refused ``too_large``: chunking is the
    contract, a sender that relies on the peer to chunk is refused."""
    conn = _Conn()
    handler = ApiChannelHandler(conn, WebServer(None, WebConfig()), ("stub", 0))
    await handler._handle_message(WS_OP_BINARY, ApiFrame(1, 1, _head()).encode())
    oversize = ApiFrame(2, 1, b"").encode() + b"x" * (RESPONSE_CHUNK_BYTES + 1)
    assert await handler._handle_message(WS_OP_BINARY, oversize) is False
    await asyncio.gather(*handler._background)
    assert _errors(conn) == [(1, "too_large")]
    assert handler._in_flight == 0 and not handler._requests


async def test_oversize_head_is_refused_without_consuming_the_id() -> None:
    conn = _Conn()
    handler = ApiChannelHandler(conn, WebServer(None, WebConfig()), ("stub", 0))
    oversize = ApiFrame(1, 3, b"").encode() + b" " * (RESPONSE_CHUNK_BYTES + 1)
    await handler._handle_message(WS_OP_BINARY, oversize)
    assert _errors(conn) == [(3, "too_large")]
    await handler._handle_message(WS_OP_BINARY, ApiFrame(1, 3, _head()).encode())
    assert 3 in handler._requests  # the same id is accepted on retry
    handler._on_cancel(3)
    await asyncio.gather(*(s.task for s in handler._requests.values() if s.task))


class _SharedSctpQueue:
    """aiortc's send side, modelled: one FIFO for every channel's messages,
    moved one whole message at a time onto a link of fixed speed.
    ``bufferedAmount`` counts a channel's messages still in the FIFO (the
    message on the link is no longer counted, as in aiortc)."""

    def __init__(self, bytes_per_ms: int) -> None:
        self.fifo: collections.deque[tuple[str, bytes]] = collections.deque()
        self.bytes_per_ms = bytes_per_ms
        self.on_link = 0
        self.delivered: dict[str, list[bytes]] = collections.defaultdict(list)
        self.control_waits: list[int] = []

    def buffered(self, label: str) -> int:
        return sum(len(m) for lab, m in self.fifo if lab == label)

    def send(self, label: str, message: bytes) -> None:
        if label == "control":
            # Bytes that must leave before this control frame can.
            self.control_waits.append(self.on_link + sum(len(m) for _, m in self.fifo))
        self.fifo.append((label, message))

    async def run(self) -> None:
        while True:
            if not self.fifo:
                await asyncio.sleep(0.001)
                continue
            label, message = self.fifo.popleft()
            self.on_link = len(message)
            await asyncio.sleep(len(message) / self.bytes_per_ms / 1000)
            self.on_link = 0
            self.delivered[label].append(message)


class _ApiConnOnQueue(_Conn):
    def __init__(self, link: _SharedSctpQueue) -> None:
        super().__init__()
        self._link = link

    async def send_binary(self, data: bytes) -> None:
        self._link.send("api", bytes(data))

    def buffered_bytes(self) -> int:
        return self._link.buffered("api")

    def set_buffer_low_watermark(self, value: int) -> None:
        pass


@pytest.mark.realtime(
    reason="models the SCTP send queue draining in real time while the "
    "handler's backpressure waits on it"
)
async def test_1mib_response_never_holds_back_control_more_than_two_frames() -> None:
    """1 MiB over ``api`` while control heartbeats every 20 ms: no control
    frame ever waits behind more than the 32 KB high-water mark plus two
    16 KB frames, and the body arrives intact and in order. With the old
    256 KB mark (or one unchunked message) a heartbeat waited behind
    256 KB-1 MiB, which at the bench's 1 % loss is past the 6 s TTL."""
    link = _SharedSctpQueue(bytes_per_ms=4 * 1024)  # 4 MB/s: ~0.25 s for 1 MiB
    pump = asyncio.ensure_future(link.run())
    handler = ApiChannelHandler(
        _ApiConnOnQueue(link), WebServer(None, WebConfig()), ("s", 0)
    )
    body = bytes(range(256)) * (_MIB // 256)
    try:
        send = asyncio.ensure_future(handler._send_response(1, 200, {}, body))
        while not send.done():
            link.send("control", b'{"type":"controller_heartbeat"}')
            await asyncio.sleep(0.02)
        await send
        while link.fifo or link.on_link:
            await asyncio.sleep(0.005)
    finally:
        pump.cancel()
    frames = [ApiFrame.parse(m) for m in link.delivered["api"]]
    assert (
        b"".join(f.payload for f in frames if f.type == ApiMessageType.RES_BODY) == body
    )
    assert len(link.control_waits) >= 5
    # + 1 KB for heartbeats still queued ahead of the measured one.
    bound = SEND_HIGH_WATER_BYTES + 2 * (RESPONSE_CHUNK_BYTES + _HEADER) + 1024
    assert max(link.control_waits) <= bound, max(link.control_waits)
