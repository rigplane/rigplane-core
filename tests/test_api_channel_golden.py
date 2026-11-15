"""Golden api-channel documents: producer-built, replayed for real.

``frontend/src/lib/transport/api-channel-golden.json`` pins the concrete
frames the browser must handle, generated from the server's own
encoders. This file fails when the committed JSON drifts from the
producer builder, and replays EVERY document through the real stack —
real aiortc data channels, the real attach path, the real request
handling — asserting the documented outcome, so the browser tests that
read the same file are pinned to what the server really does.
"""

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
from test_webrtc_attach_path import (
    _LossyAckPeer,
    _acquire_session,
    _stand_up_stack,
    _tear_down_stack,
)

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
    return (
        bytes.fromhex(frame["hex"])
        if "hex" in frame
        else base64.b64decode(frame["b64"])
    )


def _seq(doc: dict[str, Any]) -> list[tuple[int, int, bytes]]:
    """(type, request_id, payload) of every frame: with version 1 and the
    reserved flag 0 this pins every byte of the committed frames."""
    out = []
    for frame in doc["frames"]:
        raw = _raw(frame)
        assert raw[0] == 1 and raw[6] == 0, raw[:8]
        out.append((raw[1], int.from_bytes(raw[2:6], "big"), raw[7:]))
    return out


def test_committed_golden_json_matches_producer_documents() -> None:
    from rigplane.web.api_channel import api_channel_golden_documents

    assert _golden() == asyncio.run(api_channel_golden_documents()), (
        "api-channel-golden.json drifted; regenerate it"
    )


def test_golden_json_names_its_replayer() -> None:
    golden = _golden()
    assert golden["_tested_by"] == _REPLAYER
    assert len(golden["documents"]) == 21, sorted(golden["documents"])


def _with_id(frame: bytes, request_id: int) -> bytes:
    return frame[:2] + request_id.to_bytes(4, "big") + frame[6:]


@_webrtc
@pytest.mark.realtime(
    reason="the timeout document needs the (test-shortened) request "
    "deadline to pass in real time before the server answers ERROR"
)
async def test_golden_documents_replay_through_real_handlers(
    tmp_path: Path,
) -> None:
    golden = _golden()
    docs = golden["documents"]
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _LossyAckPeer(("control", "scope", "audio"))
    api = _ApiClient()
    try:
        # -- attach documents, over real lossy data channels --
        await peer.connect(stack.web)
        key = await _acquire_session(stack.web.port)
        for label in ("control", "scope", "audio"):
            peer.send_text(
                label, docs[f"attach_{label}"]["text"].replace("<SESSION-KEY>", key)
            )
        hello = await await_until(
            lambda: peer.take("control"), timeout=10.0, description="control hello"
        )
        assert hello is not None and hello["type"] == "hello", hello
        for label in ("scope", "audio"):
            ack = await await_until(
                lambda bound=label: peer.take(bound),
                timeout=10.0,
                description=f"{label} attach ack",
            )
            assert ack == json.loads(docs[f"attach_ack_{label}"]["text"]), ack

        # -- api documents on a real api channel, exact committed bytes --
        await api.connect(stack.web.port)
        assert api._channel is not None  # noqa: SLF001
        api._channel.send(docs["attach_api"]["text"])  # noqa: SLF001
        for _ in range(20):
            await asyncio.sleep(0)
        assert not api._closed.is_set(), "keyless api attach refused"
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
                (t, p) for t, _i, p in _seq(docs[name])
            ], (name, got)

        for request_name, response_name in (
            ("api_request_tx_refused", "api_response_tx_refused"),
            ("api_request_not_found", "api_response_not_found"),
        ):
            for frame in (_raw(f) for f in docs[request_name]["frames"]):
                api.send_raw(frame)
            request_id = _seq(docs[request_name])[0][1]
            _check(response_name, await _replies(request_id))

        from unittest.mock import patch

        from rigplane.web import server as server_module

        for request_name, response_name in (
            ("api_request_ok", "api_response_ok"),
            ("api_request_chunked", "api_response_chunked"),
        ):
            body = base64.b64decode(docs[response_name]["response_body_b64"])

            async def _route(writer: Any, _body: bytes = body) -> None:
                await server_module._send_response(
                    writer, 200, "OK", _body, {"Content-Type": "application/json"}
                )

            with patch.object(
                WebServer,
                "_serve_band_plan_layers",
                lambda self, writer: _route(writer),
            ):
                for frame in (_raw(f) for f in docs[request_name]["frames"]):
                    api.send_raw(frame)
                request_id = _seq(docs[request_name])[0][1]
                _check(response_name, await _replies(request_id))

        # -- failure shapes --
        async def _expect_error(name: str, request_id: int) -> None:
            want = json.loads(_seq(docs[name])[0][2])
            with pytest.raises(_ApiError) as caught:
                await api._next_for(request_id)  # noqa: SLF001
            assert (caught.value.code, caught.value.message) == (
                want["code"],
                want["message"],
            ), (name, caught.value)

        head_nf = _raw(docs["api_request_not_found"]["frames"][0])
        end_nf = _raw(docs["api_request_not_found"]["frames"][1])
        api.send_raw(_with_id(head_nf, 5))
        api.send_raw(_with_id(end_nf, 5))
        _check("api_response_not_found", await _replies(5))
        api.send_raw(_with_id(head_nf, 5))  # the resend: id 5 is stale now
        await _expect_error("api_error_stale_id", 5)

        from rigplane.web import api_channel as api_mod

        real_caps = WebServer._serve_capabilities

        async def _slow_caps(self: WebServer, writer: Any, headers: Any = None) -> None:
            await asyncio.sleep(30.0)
            await real_caps(self, writer, headers)

        with (
            patch.object(api_mod, "REQUEST_DEADLINE_S", 0.3),
            patch.object(WebServer, "_serve_capabilities", _slow_caps),
        ):
            api.send_raw(_frame(1, 6, _head("GET", "/api/v1/capabilities")))
            api.send_raw(_frame(5, 6, b""))
            await _expect_error("api_error_timeout", 6)

        api.send_raw(
            _frame(
                1,
                7,
                _head(
                    "POST", "/api/v1/eibi/fetch", {"content-type": "application/json"}
                ),
            )
        )
        big = b"x" * (1024 * 1024 + 1)
        for off in range(0, len(big), 16384):
            api.send_raw(_frame(2, 7, big[off : off + 16384]))
        await _expect_error("api_error_too_large", 7)

        for request_id in range(8, 41):  # 33 heads, no END
            api.send_raw(_frame(1, request_id, _head("GET", "/api/v1/info")))
        await _expect_error("api_error_busy", 40)
        for request_id in range(8, 40):
            api.send_raw(_frame(7, request_id, b""))  # CANCEL the in-flight

        api.send_raw(b"\x01\x02\x00\x00")  # shorter than the 7-byte header
        await _expect_error("api_error_malformed", 0)

        for frame in (_raw(f) for f in docs["api_cancel"]["frames"]):
            api.send_raw(frame)
        for frame in (_raw(f) for f in docs["api_unknown_id_ignored"]["frames"]):
            api.send_raw(frame)
        for _ in range(20):
            await asyncio.sleep(0)
        for request_id in (41, 9999):
            assert api._queues.get(request_id, asyncio.Queue()).empty()  # noqa: SLF001
        # The channel still serves after every failure shape.
        assert (await api.request(42, "GET", "/api/v1/info")).status == 200
    finally:
        with contextlib.suppress(Exception):
            await api.close()
        await peer.close()
        await _tear_down_stack(stack)
