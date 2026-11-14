"""One attach path for every transport (no second mechanism).

The WebSocket upgrade (``WebServer._handle_websocket``) and the WebRTC
data-channel dispatch (``WebRtcSessionManager._dispatch_channel``) must
attach a client channel through the SAME mechanism: controller ticket
capture/validation, handler construction with the session, leaf state
deltas (MOR-4206), caps, keepalive, and cleanup. The data-channel path
used to keep a hand-maintained copy that skipped all of it.

These tests drive REAL aiortc data channels against the IC-7610 LAN
fake (``stateful_ptt=True, ptt_on_session_loss="hold"`` where PTT
matters) and compare against the WebSocket lane on the same server.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from _perf_helpers import fast_connect
from test_web_server import (
    _close_ws,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
    _ws_skip_handshake,
)
from testkit.ic7610_fakes import ic7610_lan_fake

from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.controller_authority import ControllerAuthority
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind
from rigplane.testing.timing import await_until
from rigplane.web import server as server_module
from rigplane.web.protocol import (
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_TX,
    encode_json,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.transport.webrtc import webrtc_available
from rigplane.web.web_startup import (
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)

_webrtc = pytest.mark.skipif(
    not webrtc_available(), reason="aiortc extra not installed"
)

# The controller session TTL is the existing heartbeat for data-channel
# peers: a vanished peer's session expires TTL_MS after its last
# heartbeat, and the release must follow within one supervision beat.
_SILENT_LOSS_BUDGET_S = ControllerAuthority.TTL_MS / 1000 + 1.0

_GATE_TIMEOUT_S = 20.0


# ---------------------------------------------------------------------------
# Stack: production wiring over the IC-7610 LAN fake.
# ---------------------------------------------------------------------------


@dataclass
class _Stack:
    sim: Any
    radio: IcomRadio
    composition: ManagedTxComposition
    web: WebServer
    rigctld: RigctldServer
    rigctld_port: int


async def _stand_up_stack(
    tmp_path: Path, *, tot_seconds: float = 30.0, audio: bool = False
) -> _Stack:
    """Stand up web + rigctld + managed TX over the LAN fake (hold loss)."""
    config_path = tmp_path / "managed-tx.json"
    ManagedTxTotConfigStore(config_path).set_timeout_seconds(tot_seconds)
    sim_options: dict[str, Any] = (
        {"enable_audio": True, "capture_tx_audio": True} if audio else {}
    )
    sim = ic7610_lan_fake(
        single_owner=True,
        stateful_ptt=True,
        ptt_on_session_loss="hold",
        **sim_options,
    )
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
        model="IC-7610",
        auto_reconnect=True,
        reconnect_delay=0.2,
        reconnect_max_delay=0.5,
        watchdog_timeout=1.5,
    )
    composition = ManagedTxComposition(radio, config_path=config_path)
    install_managed_tx_composition(radio, composition)
    with fast_connect():
        await radio.connect()
    web = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, discovery=False, webrtc_enabled=True),
    )
    prepare_managed_tx_observation_generation(web)
    await composition.bind_state_store(web.command_state_store)
    attach_managed_tx_composition(web, composition)
    rigctld = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0),
        allow_not_ready=True,
        managed_tx_authority=composition.authority,
        command_queue=web.command_queue,
        command_service=web.command_service,
    )
    await rigctld.start()
    web.attach_rigctl_channel(rigctld.open_channel)
    await asyncio.wait_for(web.start(), timeout=_GATE_TIMEOUT_S)
    rigctld_port = rigctld._server.sockets[0].getsockname()[1]  # noqa: SLF001
    return _Stack(sim, radio, composition, web, rigctld, rigctld_port)


async def _tear_down_stack(stack: _Stack) -> None:
    with contextlib.suppress(Exception):
        await stack.rigctld.stop()
    with contextlib.suppress(Exception):
        await stack.web.stop()
    with contextlib.suppress(Exception):
        with fast_connect():
            await asyncio.wait_for(stack.radio.disconnect(), 10.0)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(
            stack.composition.shutdown(termination=asyncio.Event()), 5.0
        )
    with contextlib.suppress(Exception):
        await stack.sim.stop()


async def _http_post(
    port: int, path: str, body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Minimal HTTP POST helper (controller acquisition)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    raw = json.dumps(body).encode()
    writer.write(
        f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"Content-Length: {len(raw)}\r\nContent-Type: application/json\r\n"
        "Connection: close\r\n\r\n".encode()
        + raw
    )
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10.0)
    status = int(head.split(b" ", 2)[1])
    payload = json.loads(await asyncio.wait_for(reader.read(), timeout=10.0) or b"{}")
    writer.close()
    return status, payload


async def _acquire_session(port: int) -> str:
    status, grant = await _http_post(port, "/api/v1/controller", {"kind": "pro"})
    assert status == 201, grant
    return str(grant["controller_key"])


# ---------------------------------------------------------------------------
# Clients: a data-channel control peer and a raw WebSocket peer.
# ---------------------------------------------------------------------------


class _DcControlClient:
    """A control peer arriving over a WebRTC data channel.

    Speaks the attach protocol the shared attach path requires: the
    FIRST text frame is the attach frame, then ordinary control frames.
    """

    def __init__(self) -> None:
        self._pc: Any = None
        self._channel: Any = None
        self._received: list[Any] = []
        self._closed = asyncio.Event()

    async def _prepare_pc(self) -> None:
        from aiortc import RTCPeerConnection

        self._pc = RTCPeerConnection()
        self._channel = self._pc.createDataChannel("control", ordered=True)

        @self._channel.on("message")  # type: ignore[misc, no-untyped-call]
        def _on_message(message: Any) -> None:
            self._received.append(message)

        @self._channel.on("close")  # type: ignore[misc, no-untyped-call]
        def _on_close() -> None:
            self._closed.set()

    async def connect(self, web: WebServer) -> None:
        from aiortc import RTCSessionDescription

        await self._prepare_pc()
        assert self._pc is not None and self._channel is not None
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
            lambda: self._channel.readyState == "open",
            timeout=15.0,
            description="data channel open",
        )

    async def connect_via_http(self, port: int) -> None:
        """Signal the offer through the public HTTP endpoint (browser path).

        POSTs the SDP offer to ``/api/v1/transport/webrtc/offer`` instead
        of calling the session manager directly.
        """
        from aiortc import RTCSessionDescription

        await self._prepare_pc()
        assert self._pc is not None and self._channel is not None
        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        status, answer = await _http_post(
            port,
            "/api/v1/transport/webrtc/offer",
            {
                "sdp": self._pc.localDescription.sdp,
                "type": self._pc.localDescription.type,
            },
        )
        assert status == 200, answer
        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
        )
        await await_until(
            lambda: self._channel.readyState == "open",
            timeout=15.0,
            description="data channel open",
        )

    async def attach(
        self,
        key: str | None,
        *,
        state_delta: int = 2,
        role: str = "primary",
    ) -> dict[str, Any]:
        """Send the attach frame; return the hello that follows it."""
        assert self._channel is not None
        self._channel.send(
            json.dumps(
                {
                    "type": "attach",
                    "controller_key": key,
                    "state_delta": state_delta,
                    "role": role,
                }
            )
        )
        return await self._until(lambda m: m.get("type") == "hello", timeout=10.0)

    async def _until(self, matches: Any, timeout: float = 10.0) -> dict[str, Any]:
        def _next() -> dict[str, Any] | None:
            while self._received:
                message = self._received.pop(0)
                if isinstance(message, str):
                    parsed = json.loads(message)
                    if matches(parsed):
                        return parsed
            return None

        return await await_until(_next, timeout=timeout, description="frame")

    async def command(
        self, command_id: str, name: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        assert self._channel is not None
        self._channel.send(
            json.dumps(
                {"type": "cmd", "id": command_id, "name": name, "params": params or {}}
            )
        )
        return await self._until(
            lambda m: m.get("type") == "response" and m.get("id") == command_id,
            timeout=10.0,
        )

    async def heartbeat(self) -> None:
        assert self._channel is not None
        self._channel.send(json.dumps({"type": "controller_heartbeat"}))
        await self._until(
            lambda m: m.get("type") == "controller_heartbeat", timeout=10.0
        )

    async def state_updates(self, count: int, timeout: float = 30.0) -> list[Any]:
        """Collect the next ``count`` state_update data frames."""
        frames: list[Any] = []

        def _drain() -> list[Any] | None:
            while self._received:
                message = self._received.pop(0)
                if isinstance(message, str):
                    parsed = json.loads(message)
                    if parsed.get("type") == "state_update":
                        frames.append(parsed["data"])
                        if len(frames) >= count:
                            return frames
            return None

        await await_until(_drain, timeout=timeout, description="state updates")
        return frames

    async def wait_closed(self, timeout: float = 10.0) -> None:
        await await_until(
            lambda: (
                self._closed.is_set()
                or (self._channel is not None and self._channel.readyState == "closed")
            ),
            timeout=timeout,
            description="data channel closed",
        )

    def abandon(self) -> None:
        """Vanish without any close: stop interacting, keep refs alive."""

    async def close(self) -> None:
        if self._channel is not None:
            with contextlib.suppress(Exception):
                self._channel.close()
        if self._pc is not None:
            with contextlib.suppress(Exception):
                await self._pc.close()


class _WsControlClient:
    """A raw WebSocket control peer (the production WS lane)."""

    def __init__(self, port: int, key: str | None, *, query: str = "") -> None:
        self._port = port
        self._key = key
        self._query = query
        self._reader: Any = None
        self._writer: Any = None

    async def connect(self) -> None:
        if self._key is None:
            self._reader, self._writer, _ = await _ws_connect(
                "127.0.0.1", self._port, "/api/v1/ws" + self._query
            )
        else:
            self._reader, self._writer = await asyncio.open_connection(
                "127.0.0.1", self._port
            )
            self._writer.write(
                (
                    f"GET /api/v1/ws{self._query} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{self._port}\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    f"Sec-WebSocket-Protocol: rigplane-controller-v1.{self._key}"
                    "\r\n\r\n"
                ).encode()
            )
            await self._writer.drain()
            response = await asyncio.wait_for(
                self._reader.readuntil(b"\r\n\r\n"), timeout=5.0
            )
            assert response.startswith(b"HTTP/1.1 101 "), response[:200]
        await _ws_skip_handshake(self._reader)

    async def command(
        self, command_id: str, name: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        assert self._writer is not None
        await _ws_send_text(
            self._writer,
            json.dumps(
                {"type": "cmd", "id": command_id, "name": name, "params": params or {}}
            ),
        )
        while True:
            opcode, payload = await _ws_recv_frame(self._reader, timeout=10.0)
            if opcode != 0x1:
                continue
            message = json.loads(payload)
            if message.get("type") == "response" and message.get("id") == command_id:
                return message

    async def state_updates(self, count: int, timeout: float = 30.0) -> list[Any]:
        """Collect the next ``count`` state_update data frames."""
        frames: list[Any] = []
        async with asyncio.timeout(timeout):
            while len(frames) < count:
                opcode, payload = await _ws_recv_frame(self._reader, timeout=timeout)
                if opcode != 0x1:
                    continue
                message = json.loads(payload)
                if message.get("type") == "state_update":
                    frames.append(message["data"])
        return frames

    async def close(self) -> None:
        if self._writer is not None:
            with contextlib.suppress(Exception):
                await _close_ws(self._writer)


class _DcAvPeer:
    """A control + audio peer over two WebRTC data channels on one pc.

    Mirrors the production browser: the control channel is ordered and
    reliable, the audio channel is unordered. Each channel carries its
    session's attach frame; the control channel attaches first because
    the audio attach is an auxiliary one and needs the primary in place.
    """

    def __init__(self) -> None:
        self._pc: Any = None
        self._control: Any = None
        self._audio: Any = None
        self._received: list[Any] = []
        self._audio_received: list[Any] = []

    async def connect(self, web: WebServer) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription

        self._pc = RTCPeerConnection()
        self._control = self._pc.createDataChannel("control", ordered=True)
        self._audio = self._pc.createDataChannel("audio", ordered=False)

        @self._control.on("message")  # type: ignore[misc, no-untyped-call]
        def _on_control(message: Any) -> None:
            self._received.append(message)

        @self._audio.on("message")  # type: ignore[misc, no-untyped-call]
        def _on_audio(message: Any) -> None:
            self._audio_received.append(message)

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
            lambda: self._control.readyState == "open"
            and self._audio.readyState == "open",
            timeout=15.0,
            description="data channels open",
        )

    async def _until(
        self, messages: list[Any], matches: Any, timeout: float = 10.0
    ) -> dict[str, Any]:
        def _next() -> dict[str, Any] | None:
            while messages:
                message = messages.pop(0)
                if not isinstance(message, str):
                    continue  # binary RX audio on the audio channel
                parsed = json.loads(message)
                if matches(parsed):
                    return parsed
            return None

        return await await_until(_next, timeout=timeout, description="frame")

    async def attach_control(self, key: str) -> dict[str, Any]:
        """Attach the control channel as the session primary; return hello."""
        assert self._control is not None
        self._control.send(
            json.dumps(
                {
                    "type": "attach",
                    "controller_key": key,
                    "state_delta": 2,
                    "role": "primary",
                }
            )
        )
        return await self._until(
            self._received, lambda m: m.get("type") == "hello", timeout=10.0
        )

    async def attach_audio(self, key: str) -> dict[str, Any]:
        """Attach the audio channel, arm TX, return the audio_tx_format."""
        assert self._audio is not None
        self._audio.send(
            json.dumps(
                {
                    "type": "attach",
                    "controller_key": key,
                    "state_delta": 2,
                    "role": "primary",
                }
            )
        )
        self._audio.send(json.dumps({"type": "audio_start", "direction": "tx"}))
        return await self._until(
            self._audio_received,
            lambda m: m.get("type") == "audio_tx_format",
            timeout=10.0,
        )

    async def command(
        self, command_id: str, name: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        assert self._control is not None
        self._control.send(
            json.dumps(
                {"type": "cmd", "id": command_id, "name": name, "params": params or {}}
            )
        )
        return await self._until(
            self._received,
            lambda m: m.get("type") == "response" and m.get("id") == command_id,
            timeout=10.0,
        )

    def send_audio(self, frame: bytes) -> None:
        assert self._audio is not None
        self._audio.send(frame)

    async def close(self) -> None:
        if self._pc is not None:
            with contextlib.suppress(Exception):
                await self._pc.close()


def _pcm_tx_frame(samples: int) -> bytes:
    """One 20 ms PCM16 TX frame, production layout (cf. path4 in lanes)."""
    return (
        bytes([MSG_TYPE_AUDIO_TX, AUDIO_CODEC_PCM16])
        + b"\x00" * (AUDIO_HEADER_SIZE - 2)
        + b"\x10\x00" * samples
    )


async def _stream_audio(peer: _DcAvPeer, samples: int, frames: int = 10) -> None:
    """Stream a few TX frames from a peer (sends are synchronous)."""
    for _ in range(frames):
        peer.send_audio(_pcm_tx_frame(samples))
    # Pump the loop so queued datagrams are processed (asyncio.sleep(0) is
    # the realtime-guard-clean way to yield; real settling comes from the
    # await_until waits around every assertion).
    for _ in range(10):
        await asyncio.sleep(0)


async def _wait_air(stack: _Stack) -> None:
    """Wait until the holder's audio has reached the simulator TX sink."""
    await await_until(
        lambda: stack.sim.audio_tx_frames or None,
        timeout=_GATE_TIMEOUT_S,
        description="the holder's audio never reached the air",
    )
    for _ in range(50):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Attach-frame parser: typed, ValueError on bad input.
# ---------------------------------------------------------------------------


def test_attach_frame_parser_accepts_full_frame() -> None:
    params = server_module.ClientChannelParams.from_attach_frame(
        json.dumps(
            {
                "type": "attach",
                "controller_key": "k" * 64,
                "state_delta": 2,
                "role": "primary",
            }
        ).encode()
    )
    assert params.controller_key == "k" * 64
    assert params.controller_role == "primary"
    assert params.state_delta == 2


def test_attach_frame_parser_defaults_role_and_delta() -> None:
    """A keyed frame without role/delta still parses; the key is required."""
    params = server_module.ClientChannelParams.from_attach_frame(
        json.dumps({"type": "attach", "controller_key": "k" * 64}).encode()
    )
    assert params.controller_key == "k" * 64
    assert params.controller_role == "primary"
    assert params.state_delta == 2


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"[1, 2]",
        b'{"type": "hello"}',
        # A data channel admits only keyed peers: a missing or null key is
        # refused, so every data-channel peer is covered by its session TTL.
        b'{"type": "attach"}',
        b'{"type": "attach", "controller_key": null}',
        b'{"type": "attach", "state_delta": 3}',
        b'{"type": "attach", "state_delta": "2"}',
        b'{"type": "attach", "role": 7}',
        b'{"type": "attach", "controller_key": 7}',
    ],
)
def test_attach_frame_parser_rejects_bad_input(payload: bytes) -> None:
    with pytest.raises(ValueError):
        server_module.ClientChannelParams.from_attach_frame(payload)


def test_attach_frame_parser_rejects_keyless_frame() -> None:
    """Local mode stays open to keyless WebSockets, never to data channels."""
    with pytest.raises(ValueError):
        server_module.ClientChannelParams.from_attach_frame(
            json.dumps({"type": "attach", "controller_key": None}).encode()
        )


# ---------------------------------------------------------------------------
# One mechanism: data channels behave like WebSocket channels.
# ---------------------------------------------------------------------------


@_webrtc
async def test_data_channel_leaf_deltas_match_websocket_size(
    tmp_path: Path,
) -> None:
    """State deltas over a data channel match the WS leaf deltas (~2x)."""
    stack = await _stand_up_stack(tmp_path)
    dc = _DcControlClient()
    ws = _WsControlClient(stack.web.port, None, query="?state_delta=2")
    try:
        await dc.connect(stack.web)
        # Data channels admit only keyed peers; the WS lane stays
        # sessionless here, exactly as on origin/main.
        key = await _acquire_session(stack.web.port)
        hello = await dc.attach(key)
        assert hello["type"] == "hello"
        await ws.connect()
        # Drive N real state changes through the WS lane.
        for i in range(6):
            response = await ws.command(
                f"freq-{i}", "set_freq", {"freq": 14_074_000 + 1000 * (i % 2)}
            )
            assert response.get("ok") is True, response
        dc_frames = await dc.state_updates(6)
        ws_frames = await ws.state_updates(6)
    finally:
        await dc.close()
        await ws.close()
        await _tear_down_stack(stack)
    # The first state_update is the full baseline; the rest are deltas.
    dc_deltas, ws_deltas = dc_frames[1:], ws_frames[1:]
    assert dc_deltas and ws_deltas
    assert all(frame.get("deltaVersion") == 2 for frame in dc_deltas)
    dc_mean = sum(len(json.dumps(f)) for f in dc_deltas) / len(dc_deltas)
    ws_mean = sum(len(json.dumps(f)) for f in ws_deltas) / len(ws_deltas)
    assert dc_mean <= 2.0 * ws_mean, (dc_mean, ws_mean)


@_webrtc
async def test_data_channel_with_key_keys_tx_like_ws(tmp_path: Path) -> None:
    """A data-channel peer with a session key holds TX like a WS peer."""
    stack = await _stand_up_stack(tmp_path)
    dc = _DcControlClient()
    try:
        await dc.connect(stack.web)
        # Acquire after the slow ICE negotiate, just before attach: the
        # controller session TTL would otherwise expire in between.
        key = await _acquire_session(stack.web.port)
        await dc.attach(key)
        keyed = await dc.command("key-1", "ptt_on")
        assert keyed.get("ok") is True, keyed
        await await_until(
            lambda: stack.sim.ptt_on is True, timeout=10.0, description="sim keyed"
        )
        unkeyed = await dc.command("unkey-1", "ptt_off")
        assert unkeyed.get("ok") is True, unkeyed
        await await_until(
            lambda: stack.sim.ptt_on is False, timeout=10.0, description="sim unkeyed"
        )
    finally:
        await dc.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_data_channel_without_key_cannot_key_in_remote(
    tmp_path: Path,
) -> None:
    """Remote mode: a keyless data channel is closed, TX stays off."""
    stack = await _stand_up_stack(tmp_path)
    dc = _DcControlClient()
    try:
        await stack.web._controller.set_mode("remote")  # noqa: SLF001
        await dc.connect(stack.web)
        dc._channel.send(  # noqa: SLF001
            json.dumps({"type": "attach", "controller_key": None})
        )
        await dc.wait_closed(timeout=10.0)
        assert stack.sim.ptt_on is False
    finally:
        await dc.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_data_channel_without_key_refused_in_local(tmp_path: Path) -> None:
    """Local mode: a keyless data channel is closed before any handler.

    WebSocket keyless behaviour is unchanged (sessionless capture still
    works there); data channels admit only keyed peers, so a silent loss
    always has a controller session TTL to expire it.
    """
    stack = await _stand_up_stack(tmp_path)
    dc = _DcControlClient()
    try:
        await dc.connect(stack.web)
        dc._channel.send(  # noqa: SLF001
            json.dumps({"type": "attach", "controller_key": None})
        )
        await dc.wait_closed(timeout=10.0)
        assert stack.sim.ptt_on is False
    finally:
        await dc.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_missing_attach_closes_channel_within_timeout(
    tmp_path: Path,
) -> None:
    """A data channel that never sends attach is closed within 5 s."""
    stack = await _stand_up_stack(tmp_path)
    dc = _DcControlClient()
    try:
        await dc.connect(stack.web)
        await dc.wait_closed(timeout=10.0)
    finally:
        await dc.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_clean_close_while_keyed_releases_ptt_fast(
    tmp_path: Path,
) -> None:
    """Clean close of a keyed data channel unkeys within 0.5 s."""
    stack = await _stand_up_stack(tmp_path)
    dc = _DcControlClient()
    try:
        await dc.connect(stack.web)
        # Acquire after the slow ICE negotiate, just before attach: the
        # controller session TTL would otherwise expire in between.
        key = await _acquire_session(stack.web.port)
        await dc.attach(key)
        keyed = await dc.command("key-1", "ptt_on")
        assert keyed.get("ok") is True, keyed
        await await_until(
            lambda: stack.sim.ptt_on is True, timeout=10.0, description="sim keyed"
        )
        started = asyncio.get_running_loop().time()
        await dc.close()
        await await_until(
            lambda: stack.sim.ptt_on is False, timeout=5.0, description="sim unkeyed"
        )
        assert asyncio.get_running_loop().time() - started <= 0.5
    finally:
        await dc.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_silent_loss_releases_ptt_within_session_ttl(
    tmp_path: Path,
) -> None:
    """A peer that vanishes without closing must not hold TX past the TTL."""
    stack = await _stand_up_stack(tmp_path)
    dc = _DcControlClient()
    try:
        await dc.connect(stack.web)
        # Acquire after the slow ICE negotiate, just before attach: the
        # controller session TTL would otherwise expire in between.
        key = await _acquire_session(stack.web.port)
        await dc.attach(key)
        keyed = await dc.command("key-1", "ptt_on")
        assert keyed.get("ok") is True, keyed
        await await_until(
            lambda: stack.sim.ptt_on is True, timeout=10.0, description="sim keyed"
        )
        # Vanish: no close, no heartbeat, no traffic. The controller
        # session TTL is the only thing that can release TX now.
        vanished_at = asyncio.get_running_loop().time()
        dc.abandon()
        await await_until(
            lambda: stack.sim.ptt_on is False,
            timeout=_SILENT_LOSS_BUDGET_S + 5.0,
            description="sim released after silent loss",
        )
        elapsed = asyncio.get_running_loop().time() - vanished_at
        assert elapsed <= _SILENT_LOSS_BUDGET_S, elapsed
        state = (await stack.composition.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
    finally:
        await dc.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_only_holders_audio_reaches_radio_over_data_channels(
    tmp_path: Path,
) -> None:
    """Only the TX holder's audio frames reach the radio over data channels.

    Two controller sessions, each with real control + audio data channels
    attached through the public entrypoint with its own key; the holder
    keys, both stream distinguishable TX audio frames, and only the
    holder's frames land in the simulator TX sink. Teardown releases TX.
    """
    stack = await _stand_up_stack(tmp_path, audio=True)
    holder = _DcAvPeer()
    other = _DcAvPeer()
    try:
        await holder.connect(stack.web)
        await other.connect(stack.web)
        # Acquire after the slow ICE negotiate, just before attach: the
        # controller session TTL would otherwise expire in between.
        holder_key = await _acquire_session(stack.web.port)
        other_key = await _acquire_session(stack.web.port)
        await holder.attach_control(holder_key)
        await holder.attach_audio(holder_key)
        await other.attach_control(other_key)
        await other.attach_audio(other_key)
        # Nobody holds the transmitter: nothing reaches the air.
        await _stream_audio(holder, 480)
        await _stream_audio(other, 200)
        assert not stack.sim.audio_tx_frames
        # The holder keys; both stream again with the same chance.
        keyed = await holder.command("key-1", "ptt_on")
        assert keyed.get("ok") is True, keyed
        await await_until(
            lambda: stack.sim.ptt_on is True, timeout=10.0, description="sim keyed"
        )
        # The other peer streams first: when the holder's frames land, the
        # other's in-flight frames had strictly more chance to leak.
        await _stream_audio(other, 200)
        await _stream_audio(holder, 480)
        await _wait_air(stack)
        # Distinct frame sizes tell whose audio the sink recorded: only the
        # holder's 480-sample (960-byte) frames, never the other's 200.
        assert {frame.payload_len for frame in stack.sim.audio_tx_frames} == {960}
        assert len(stack.sim.audio_tx_frames) == 10
        # Teardown releases the transmitter without an explicit unkey.
        await holder.close()
        await other.close()
        await await_until(
            lambda: stack.sim.ptt_on is False,
            timeout=5.0,
            description="sim unkeyed on teardown",
        )
        state = (await stack.composition.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
    finally:
        await holder.close()
        await other.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_last_peer_close_resets_session_manager(tmp_path: Path) -> None:
    """Closing the last peer resets the manager; a new offer is served fresh."""
    stack = await _stand_up_stack(tmp_path)
    first = _DcControlClient()
    second = _DcControlClient()
    try:
        await first.connect(stack.web)
        await second.connect(stack.web)
        manager = stack.web._webrtc_sessions  # noqa: SLF001
        assert manager is not None
        session_ids = manager.active_session_ids
        assert len(session_ids) == 2
        # Closing one peer leaves the manager (and the other peer) alive.
        await manager._sessions[session_ids[0]].pc.close()  # noqa: SLF001
        await await_until(
            lambda: manager.active_session_ids == [session_ids[1]],
            timeout=10.0,
            description="first peer dropped",
        )
        assert stack.web._webrtc_sessions is manager  # noqa: SLF001
        # Closing the last peer resets the manager on the server.
        await manager._sessions[session_ids[1]].pc.close()  # noqa: SLF001
        await await_until(
            lambda: stack.web._webrtc_sessions is None,  # noqa: SLF001
            timeout=10.0,
            description="session manager reset",
        )
        # A new offer builds a fresh manager that serves it end to end.
        fresh = stack.web._webrtc_session_manager()  # noqa: SLF001
        assert fresh is not manager
        third = _DcControlClient()
        await third.connect(stack.web)
        try:
            key = await _acquire_session(stack.web.port)
            hello = await third.attach(key)
            assert hello["type"] == "hello"
            keyed = await third.command("key-1", "ptt_on")
            assert keyed.get("ok") is True, keyed
            await await_until(
                lambda: stack.sim.ptt_on is True,
                timeout=10.0,
                description="fresh manager keyed",
            )
            unkeyed = await third.command("unkey-1", "ptt_off")
            assert unkeyed.get("ok") is True, unkeyed
            await await_until(
                lambda: stack.sim.ptt_on is False,
                timeout=10.0,
                description="fresh manager unkeyed",
            )
        finally:
            await third.close()
    finally:
        await first.close()
        await second.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_remote_mode_admissible_after_webrtc_offer(tmp_path: Path) -> None:
    """Remote admission entirely through the public HTTP API + data channel.

    The browser path: POST the SDP offer to
    ``/api/v1/transport/webrtc/offer``, switch remote mode through the
    public ``POST /api/v1/controller/mode`` endpoint, acquire the grant
    through ``POST /api/v1/controller``, attach the peer, and run a
    control command verified on the fake radio.
    """
    stack = await _stand_up_stack(tmp_path)
    dc = _DcControlClient()
    try:
        await dc.connect_via_http(stack.web.port)
        assert stack.web._webrtc_sessions is not None  # noqa: SLF001
        status, _ = await _http_post(
            stack.web.port, "/api/v1/controller/mode", {"mode": "remote"}
        )
        assert status == 200, status
        assert stack.web._controller.mode == "remote"  # noqa: SLF001
        key = await _acquire_session(stack.web.port)
        hello = await dc.attach(key)
        assert hello["type"] == "hello"
        keyed = await dc.command("key-1", "ptt_on")
        assert keyed.get("ok") is True, keyed
        await await_until(
            lambda: stack.sim.ptt_on is True, timeout=10.0, description="sim keyed"
        )
        unkeyed = await dc.command("unkey-1", "ptt_off")
        assert unkeyed.get("ok") is True, unkeyed
        await await_until(
            lambda: stack.sim.ptt_on is False,
            timeout=10.0,
            description="sim unkeyed",
        )
    finally:
        await dc.close()
        await _tear_down_stack(stack)


async def test_websocket_scope_output_shape(tmp_path: Path) -> None:
    """WS scope output stays byte-identical: ping in, exact pong bytes out."""
    stack = await _stand_up_stack(tmp_path)
    try:
        reader, writer, _ = await _ws_connect(
            "127.0.0.1", stack.web.port, "/api/v1/scope"
        )
        try:
            await _ws_send_text(writer, json.dumps({"type": "ping"}))
            opcode, payload = await _ws_recv_frame(reader, timeout=10.0)
            assert opcode == 0x1
            assert payload == encode_json({"type": "pong"}).encode()
        finally:
            with contextlib.suppress(Exception):
                await _close_ws(writer)
    finally:
        await _tear_down_stack(stack)
