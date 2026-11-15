"""Golden api-channel documents: producer-built, replayed for real.

The committed JSON pins the frames the browser must handle, from the
server's own encoders; this file fails when it drifts and replays EVERY
document through the real stack — real aiortc data channels, the real
attach path (lost-ack retry included), the api channel's real request
handling — asserting the documented outcome, so browser tests reading
the same file are pinned to what the server really does."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from pathlib import Path
from typing import Any

import pytest

from rigplane.testing.timing import await_until
from rigplane.web.server import WebServer
from rigplane.web.transport.webrtc import webrtc_available

from test_webrtc_api_channel import _ApiClient, _ApiError, _frame, _head
from test_webrtc_attach_path import _acquire_session, _stand_up_stack, _tear_down_stack

_webrtc = pytest.mark.skipif(
    not webrtc_available(), reason="aiortc extra not installed"
)

_GOLDEN_JSON = (
    Path(__file__).resolve().parents[1]
    / "frontend/src/lib/transport/api-channel-golden.json"
)
_REPLAYER = "tests/test_api_channel_golden.py::test_golden_documents_replay_through_real_handlers"


def _golden() -> dict[str, Any]:
    return json.loads(_GOLDEN_JSON.read_text(encoding="utf-8"))


def _raw(frame: dict[str, Any]) -> bytes:
    hex_ = frame.get("hex")
    return bytes.fromhex(hex_) if hex_ else base64.b64decode(frame["b64"])


def _seq(frames: list[dict[str, Any]]) -> list[tuple[int, int, bytes]]:
    """(type, request_id, payload): with version 1 and flag 0 this pins
    every byte of the committed frames."""
    out = []
    for frame in frames:
        raw = _raw(frame)
        assert raw[0] == 1 and raw[6] == 0, raw[:8]
        out.append((raw[1], int.from_bytes(raw[2:6], "big"), raw[7:]))
    return out


class _LossyAckPeer:
    """A control+scope+audio peer that can lose its first attach ack:
    ``take(drop_first_ack=True)`` discards it, modelling the transit
    loss that makes a client resend its attach frame."""

    def __init__(self) -> None:
        self._pc: Any = None
        self._channels: dict[str, Any] = {}
        self.received: dict[str, list[Any]] = {}
        self._dropped: set[str] = set()

    async def connect(self, web: WebServer, labels: tuple[str, ...]) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription

        self._pc = RTCPeerConnection()
        for label in labels:
            channel = self._pc.createDataChannel(label, ordered=True)
            self._channels[label] = channel
            self.received[label] = []

            @channel.on("message")  # type: ignore[misc, no-untyped-call]
            def _on_message(message: Any, _label: str = label) -> None:
                self.received[_label].append(message)

        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        manager = web._webrtc_session_manager()  # noqa: SLF001
        answer = await manager.negotiate(
            self._pc.localDescription.sdp, self._pc.localDescription.type
        )
        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
        )
        await await_until(
            lambda: all(c.readyState == "open" for c in self._channels.values()),
            timeout=15.0,
            description="data channels open",
        )

    def send_text(self, label: str, text: str) -> None:
        self._channels[label].send(text)

    def take(
        self, label: str, *, drop_first_ack: bool = False
    ) -> dict[str, Any] | None:
        while self.received[label]:
            message = self.received[label].pop(0)
            if not isinstance(message, str):
                continue
            parsed = json.loads(message)
            first_ack = parsed.get("type") == "attached" and label not in self._dropped
            if drop_first_ack and first_ack:
                self._dropped.add(label)  # lost: the client never saw it
                continue
            return parsed
        return None

    async def close(self) -> None:
        if self._pc is not None:
            with contextlib.suppress(Exception):
                await self._pc.close()


def test_committed_golden_json_is_one_typed_contract() -> None:
    """The committed file is the producer's serialised typed contract:
    one binary-frame encoding (whole-frame hex, never a hex/b64 guess),
    a variant discriminator on every document, an envelope ``shape``
    map for the browser side, and a producer-owned loader that
    round-trips build -> serialise -> load."""
    import dataclasses

    from rigplane.web.api_channel import (  # noqa: TID251
        api_channel_golden_documents,
        api_channel_golden_documents_from_json,
    )

    golden = _golden()
    assert set(golden) == {"version", "tested_by", "formats", "shape", "documents"}
    assert golden["tested_by"] == _REPLAYER
    assert len(golden["documents"]) == 13, sorted(golden["documents"])
    frame_keys = {"hex", "type", "request_id", "payload", "payload_bytes", "variant"}
    for doc in golden["documents"].values():
        assert doc["variant"] in golden["shape"], doc.get("variant")
        for frame in (
            doc.get("request", []) + doc.get("reply", []) + doc.get("frames", [])
        ):
            assert set(frame) == frame_keys, sorted(frame)
    built = asyncio.run(api_channel_golden_documents())
    assert golden == json.loads(json.dumps(dataclasses.asdict(built))), (
        "api-channel-golden.json drifted; regenerate it"
    )
    assert api_channel_golden_documents_from_json(golden) == built


@_webrtc
@pytest.mark.realtime(
    reason="the timeout document needs the shortened request deadline "
    "to pass in real time before the server answers ERROR"
)
async def test_golden_documents_replay_through_real_handlers(
    tmp_path: Path,
) -> None:
    docs = _golden()["documents"]
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _LossyAckPeer()
    api = _ApiClient()
    try:
        # -- attach documents on real lossy channels, ack lost and retried --
        await peer.connect(stack.web, ("control", "scope", "audio"))
        key = await _acquire_session(stack.web.port)

        def _attach_text(label: str) -> str:
            return docs[f"attach_{label}"]["text"].replace("<SESSION-KEY>", key)

        for label in ("control", "scope", "audio"):
            peer.send_text(label, _attach_text(label))
        hello = await await_until(
            lambda: peer.take("control"), timeout=10.0, description="control hello"
        )
        assert hello is not None and hello["type"] == "hello", hello
        for label in ("scope", "audio"):
            # The lossy-channel retry: the interceptor drops the first
            # ack, the client resends the attach, and the ONE attach
            # path re-acknowledges it — then the channel keeps working.
            peer.send_text(label, _attach_text(label))
            ack = await await_until(
                lambda bound=label: peer.take(bound, drop_first_ack=True),
                timeout=5.0,
                description=f"re-acknowledged {label} attach",
            )
            assert ack == json.loads(docs[f"attach_ack_{label}"]["text"]), ack
        peer.send_text("scope", json.dumps({"type": "ping"}))
        pong = await await_until(
            lambda: peer.take("scope"), timeout=10.0, description="scope pong"
        )
        assert pong == {"type": "pong"}, pong
        peer.send_text("audio", json.dumps({"type": "audio_start", "direction": "tx"}))
        fmt = await await_until(
            lambda: peer.take("audio"), timeout=10.0, description="audio format"
        )
        assert fmt is not None and fmt["type"] == "audio_tx_format", fmt

        # -- api documents on a real api channel, exact committed bytes --
        await api.connect(stack.web.port)
        api._channel.send(docs["attach_api"]["text"])  # noqa: SLF001
        await api.start_pump()

        async def _replies(request_id: int) -> list[tuple[int, bytes]]:
            frames = []
            while True:
                msg_type, payload = await api._next_for(request_id)  # noqa: SLF001
                frames.append((int(msg_type), payload))
                if int(msg_type) in (5, 6):  # END / ERROR terminate a request
                    return frames

        def _check(name: str, got: list[tuple[int, bytes]]) -> None:
            assert [(t, p) for t, p in got] == [
                (t, p) for t, _i, p in _seq(docs[name]["reply"])
            ], (name, got)

        from unittest.mock import patch

        from rigplane.web import server as server_module

        for name in ("api_tx_refused", "api_not_found"):
            doc = docs[name]
            for frame in (_raw(f) for f in doc["request"]):
                api.send_raw(frame)
            _check(name, await _replies(_seq(doc["request"])[0][1]))
        # The replayed exchange IS observed processing: the keyless api
        # attach was consumed and the channel stayed open.
        assert not api._closed.is_set(), "keyless api attach refused"  # noqa: SLF001
        for name in ("api_ok", "api_chunked"):
            doc = docs[name]
            body = base64.b64decode(doc["response_body_b64"])

            async def _route(writer: Any, _body: bytes = body) -> None:
                await server_module._send_response(
                    writer, 200, "OK", _body, {"Content-Type": "application/json"}
                )

            with patch.object(
                WebServer,
                "_serve_band_plan_layers",
                lambda self, writer: _route(writer),
            ):
                for frame in (_raw(f) for f in doc["request"]):
                    api.send_raw(frame)
                _check(name, await _replies(_seq(doc["request"])[0][1]))

        # -- failure shapes --
        async def _expect_error(code: str) -> None:
            want = next(
                f
                for f in _seq(docs["api_errors"]["frames"])
                if f[2] and json.loads(f[2])["code"] == code
            )
            with pytest.raises(_ApiError) as caught:
                await api._next_for(want[1])  # noqa: SLF001
            error = json.loads(want[2])
            got = caught.value
            assert (got.code, got.message) == (code, error["message"]), (code, got)

        head_nf = _head("GET", "/api/v1/nonexistent")
        api.send_raw(_frame(1, 5, head_nf))
        api.send_raw(_frame(5, 5, b""))
        _check("api_not_found", await _replies(5))
        api.send_raw(_frame(1, 5, head_nf))  # the resend: id 5 is stale now
        await _expect_error("stale_id")

        from rigplane.web import api_channel as api_mod

        with patch.object(api_mod, "REQUEST_DEADLINE_S", 0.3):
            # A request that never gets its END: reassembly outlasts the
            # (shortened) deadline and answers ERROR timeout.
            api.send_raw(_frame(1, 6, _head("GET", "/api/v1/info")))
            await _expect_error("timeout")

        api.send_raw(
            _frame(
                1,
                7,
                _head(
                    "POST",
                    "/api/v1/eibi/fetch",
                    headers={"content-type": "application/json"},
                ),
            )
        )
        big = b"x" * (1024 * 1024 + 1)
        for off in range(0, len(big), 16384):
            api.send_raw(_frame(2, 7, big[off : off + 16384]))
        await _expect_error("too_large")

        for request_id in range(8, 41):  # 33 heads, no END
            api.send_raw(_frame(1, request_id, _head("GET", "/api/v1/info")))
        await _expect_error("busy")
        for request_id in range(8, 40):
            api.send_raw(_frame(7, request_id, b""))  # CANCEL the in-flight

        api.send_raw(b"\x01\x02\x00\x00")  # shorter than the 7-byte header
        await _expect_error("malformed")

        # Cancelled and unknown ids stay silent while the channel keeps
        # serving. The checked ids are derived from the committed frames
        # themselves; a failed cancel answers the trailing END (an
        # observable response) and a frame-answering handler answers the
        # unknown id.
        silent_ids = {
            _seq([frame])[0][1]
            for name in ("api_cancel", "api_unknown_id_ignored")
            for frame in docs[name]["request"]
        }
        assert silent_ids == {41, 9999}, silent_ids
        assert [t for t, _r, _p in _seq(docs["api_cancel"]["request"])] == [
            1,
            7,
            5,
        ], "api_cancel must send HEAD, CANCEL, then END"
        for name in ("api_cancel", "api_unknown_id_ignored"):
            for frame in (_raw(f) for f in docs[name]["request"]):
                api.send_raw(frame)
        # Request 42's completed response IS observed processing: the
        # ordered channel served every earlier frame before it.
        assert (await api.request(42, "GET", "/api/v1/info")).status == 200
        for request_id in silent_ids:
            assert request_id not in api._queues, request_id  # noqa: SLF001
    finally:
        await api.close()
        await peer.close()
        await _tear_down_stack(stack)
