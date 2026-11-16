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
from rigplane.web.api_channel import api_channel_golden_documents_from_json
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
# peers: a vanished peer's session expires TTL_MS after its last heartbeat,
# and the session watchdog (ControllerAuthority._watch_sessions) notices at
# most one HEARTBEAT_MS tick later — it sleeps HEARTBEAT_MS between
# _expire() passes, and _end_session aborts the registered channel so the
# handler sees EOF and the normal cleanup releases TX. The budget is
# therefore TTL_MS + HEARTBEAT_MS plus small scheduling slack. This
# deliberately changes no timeout: touching TTL_MS / HEARTBEAT_MS or the
# watchdog cadence would change WebSocket/LAN session behaviour, so the
# faster TX-session timeout stays a separate, later change.
_SILENT_LOSS_BUDGET_S = (
    ControllerAuthority.TTL_MS / 1000 + ControllerAuthority.HEARTBEAT_MS / 1000 + 0.5
)

_GATE_TIMEOUT_S = 20.0

_GOLDEN_JSON = (
    Path(__file__).resolve().parents[1]
    / "frontend/src/lib/transport/api-channel-golden.json"
)


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
        # Ordered here (production uses lossy): attach_audio sends the
        # attach frame and audio_start back to back, and the server reads
        # the FIRST frame as the attach — order must be deterministic.
        self._audio = self._pc.createDataChannel("audio", ordered=True)

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
            lambda: (
                self._control.readyState == "open" and self._audio.readyState == "open"
            ),
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

    async def heartbeat(self) -> None:
        """Refresh the session deadline (the TTL would otherwise expire)."""
        assert self._control is not None
        self._control.send(json.dumps({"type": "controller_heartbeat"}))
        await self._until(
            self._received,
            lambda m: m.get("type") == "controller_heartbeat",
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


async def _keepalive_while(peer: _DcAvPeer, done: asyncio.Event) -> None:
    """Beat a peer every 2 s until ``done`` (real-time pacing).

    Only for bridging a multi-second gap (a second peer's ICE negotiate)
    that would otherwise outlast the 6 s controller session TTL. Fire and
    forget: the server refreshes the deadline on receipt; replies just
    accumulate for the matchers to skip.
    """
    while not done.is_set():
        with contextlib.suppress(Exception):
            assert peer._control is not None
            peer._control.send(json.dumps({"type": "controller_heartbeat"}))
        await asyncio.sleep(2.0)


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
        b'{"type": "attach", "controller_key": 7}',
        # state_delta / role cases carry a valid key so they fail on
        # their own validator, not on the missing-key check.
        json.dumps(
            {"type": "attach", "controller_key": "k" * 64, "state_delta": 3}
        ).encode(),
        json.dumps(
            {"type": "attach", "controller_key": "k" * 64, "state_delta": "2"}
        ).encode(),
        json.dumps({"type": "attach", "controller_key": "k" * 64, "role": 7}).encode(),
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
    """Data-channel state deltas are v2 leaf deltas, like the WebSocket lane's."""
    stack = await _stand_up_stack(tmp_path)
    dc = _DcControlClient()
    # Two observers (data channel and WebSocket) and a separate driver lane,
    # so neither observer drops state updates while waiting for a response.
    ws = _WsControlClient(stack.web.port, None, query="?state_delta=2")
    driver = _WsControlClient(stack.web.port, None, query="?state_delta=2")
    try:
        await dc.connect(stack.web)
        # Data channels admit only keyed peers; the WS lanes stay
        # sessionless here, exactly as on origin/main.
        key = await _acquire_session(stack.web.port)
        hello = await dc.attach(key)
        assert hello["type"] == "hello"
        await ws.connect()
        await driver.connect()
        # Let both observers settle on the same baseline before driving.
        dc_baseline = await dc.state_updates(1)
        await ws.state_updates(1)
        for i in range(6):
            response = await driver.command(
                f"freq-{i}", "set_freq", {"freq": 14_074_000 + 1000 * (i % 2)}
            )
            assert response.get("ok") is True, response
        dc_frames = await dc.state_updates(10)
        ws_frames = await ws.state_updates(10)
    finally:
        await dc.close()
        await ws.close()
        await driver.close()
        await _tear_down_stack(stack)

    # What must hold: the data channel uses the SAME v2 leaf-delta encoder as
    # the WebSocket lane (MOR-4206), not the old top-level rendering (~49 KB
    # per delta before the unified attach). Sizes are not compared frame by
    # frame: each client's delta is relative to the last frame THAT client
    # received, so a lane whose queue coalesced two updates legitimately sends
    # a bigger delta for the same revision.
    dc_deltas = [f for f in dc_frames if f.get("type") == "delta"]
    ws_deltas = [f for f in ws_frames if f.get("type") == "delta"]
    assert dc_deltas and ws_deltas
    assert all(f.get("deltaVersion") == 2 for f in dc_deltas)
    assert all(f.get("deltaVersion") == 2 for f in ws_deltas)
    # The property that matters is relative, not a byte count: a v2 leaf delta
    # carries only the leaves that changed since THIS client's last frame, so
    # even when its queue coalesces several flushes the delta is bounded by the
    # union of changed leaves — a small part of the full state. The old
    # top-level rendering re-sent whole top-level sections on every change, so
    # each "delta" was about as large as a full render (measured ~49 KB vs a
    # 33–59 KB full state). Assert every data-channel delta stays below half of
    # this connection's own full-state render.
    full_frames = [f for f in dc_baseline + dc_frames if f.get("type") == "full"]
    assert full_frames, "the data-channel peer received no full state render"
    full_size = max(len(json.dumps(f)) for f in full_frames)
    for frame in dc_deltas:
        size = len(json.dumps(frame))
        assert size <= full_size // 2, (frame.get("revision"), size, full_size)


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
        opened_at = asyncio.get_running_loop().time()
        await dc.wait_closed(timeout=10.0)
        elapsed = asyncio.get_running_loop().time() - opened_at
        # Closed by the server's attach timeout (5 s), not by the client.
        assert elapsed <= 5.0 + 2.0, elapsed
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
        print(
            f"SILENT_LOSS_RELEASE_S elapsed={elapsed:.3f}"
            f" budget={_SILENT_LOSS_BUDGET_S:.3f}"
        )
        assert elapsed <= _SILENT_LOSS_BUDGET_S, elapsed
        state = (await stack.composition.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
    finally:
        await dc.close()
        await _tear_down_stack(stack)


@_webrtc
@pytest.mark.realtime(
    reason="holder keepalive beats are paced in real time across the second "
    "peer's multi-second ICE negotiate; the 6 s controller session TTL they "
    "refresh is real-time"
)
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
        # Attach each peer right after its own ICE negotiate: the server
        # closes a channel that sends no attach frame within 5 s, so a
        # second slow negotiate must not come between connect and attach.
        # Acquire just before attach for the same reason (6 s session TTL).
        await holder.connect(stack.web)
        holder_key = await _acquire_session(stack.web.port)
        await holder.attach_control(holder_key)
        await holder.attach_audio(holder_key)
        # The second negotiate takes seconds; keep the holder's session
        # alive across it or the supervisor aborts its channels on expiry.
        connected = asyncio.Event()
        keepalive = asyncio.create_task(_keepalive_while(holder, connected))
        try:
            await other.connect(stack.web)
            other_key = await _acquire_session(stack.web.port)
            await other.attach_control(other_key)
            await other.attach_audio(other_key)
        finally:
            connected.set()
            await keepalive
        # Refresh both deadlines: the keying below must meet live sessions,
        # and the other's frames must reach the pin (not die on expiry).
        await holder.heartbeat()
        await other.heartbeat()
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
        # Attach each peer right after its own ICE negotiate: the server
        # closes a channel with no attach frame within 5 s (which would
        # drop the peer before the assertion below), so the second slow
        # negotiate must not come between connect and attach.
        await first.connect(stack.web)
        await first.attach(await _acquire_session(stack.web.port))
        await second.connect(stack.web)
        await second.attach(await _acquire_session(stack.web.port))
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


class _LossyAckPeer:
    """A control+scope+audio peer that can lose its first attach ack:
    ``take(drop_first_ack=True)`` discards it, modelling the transit
    loss that makes a client resend its attach frame."""

    def __init__(self) -> None:
        self._pc: Any = None
        self._channels: dict[str, Any] = {}
        self.received: dict[str, list[Any]] = {}
        self._dropped: set[str] = set()
        # Every channel ever opened, in order, label included: a label can
        # appear more than once (re-created channels, same-label copies).
        self.every_channel: list[tuple[str, Any]] = []

    def open_channel(self, label: str, *, lossy: bool = False) -> Any:
        """Create (or re-create) the channel for ``label`` on this peer.

        ``lossy`` is the production scope/audio configuration (unordered,
        ``maxRetransmits=0``). A re-created label starts with an empty
        inbox, so nothing from the closed channel is mistaken for it.
        """
        kwargs: dict[str, Any] = (
            {"ordered": False, "maxRetransmits": 0} if lossy else {"ordered": True}
        )
        channel = self._pc.createDataChannel(label, **kwargs)
        self._channels[label] = channel
        self.received[label] = []
        self.every_channel.append((label, channel))

        @channel.on("message")  # type: ignore[misc, no-untyped-call]
        def _on_message(message: Any, _label: str = label) -> None:
            self.received[_label].append(message)

        return channel

    def close_channel(self, label: str) -> None:
        """Close only this label's channel (the peer connection stays)."""
        self._channels[label].close()

    async def connect(
        self,
        web: WebServer,
        labels: tuple[str, ...],
        *,
        lossy: tuple[str, ...] = (),
    ) -> None:
        """Open one data channel per label (``lossy`` ones unordered)."""
        from aiortc import RTCPeerConnection, RTCSessionDescription

        self._pc = RTCPeerConnection()
        for label in labels:
            self.open_channel(label, lossy=label in lossy)

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
        """Send one text frame on the label's channel."""
        self._channels[label].send(text)

    def take(
        self, label: str, *, drop_first_ack: bool = False
    ) -> dict[str, Any] | None:
        """The next text frame as parsed JSON (binary frames skipped)."""
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
        """Close the peer connection (idempotent, never raises)."""
        if self._pc is not None:
            with contextlib.suppress(Exception):
                await self._pc.close()


@_webrtc
async def test_attach_ack_on_lossy_channels_only(tmp_path: Path) -> None:
    """The ack exists only where the attach frame can be lost: the lossy
    scope/audio labels get one ``{"type":"attached","v":1,...}`` text
    frame as the FIRST frame (an ack-ignoring audio client is proven by
    ``test_only_holders_audio...``); control's first frame is its
    ``hello``, and api gets none — every ``test_webrtc_api_channel.py``
    pump asserts each inbound frame is bytes.
    """
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _LossyAckPeer()
    try:  # peer setup, negotiation and acquisition are in cleanup scope
        await peer.connect(stack.web, ("control", "scope", "audio"))
        key = await _acquire_session(stack.web.port)

        def _attach(label: str) -> None:
            peer.send_text(
                label,
                json.dumps(
                    {
                        "type": "attach",
                        "controller_key": key,
                        "state_delta": 2,
                        "role": "primary",
                    }
                ),
            )

        _attach("control")
        first = await await_until(
            lambda: peer.take("control"), timeout=10.0, description="control hello"
        )
        assert first is not None and first["type"] == "hello", first
        for label in ("scope", "audio"):  # the ack is the first frame
            _attach(label)
            assert await await_until(
                lambda bound=label: peer.take(bound), timeout=10.0, description=label
            ) == {"type": "attached", "v": 1, "channel": label}
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_resent_attach_after_lost_ack_is_reacknowledged_and_channel_works(
    tmp_path: Path,
) -> None:
    """The lossy-channel retry: the first ack is dropped in transit, the
    client resends its attach, and the ONE attach path re-acknowledges it
    (the retry never builds a new ticket or handler) — then both lossy
    channels keep working: ping/pong on scope, the audio format on audio.
    """
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _LossyAckPeer()
    try:  # peer setup, negotiation and acquisition are in cleanup scope
        await peer.connect(stack.web, ("control", "scope", "audio"))
        key = await _acquire_session(stack.web.port)

        def _attach(label: str) -> None:
            peer.send_text(
                label,
                json.dumps(
                    {
                        "type": "attach",
                        "controller_key": key,
                        "state_delta": 2,
                        "role": "primary",
                    }
                ),
            )

        _attach("control")
        hello = await await_until(
            lambda: peer.take("control"), timeout=10.0, description="control hello"
        )
        assert hello is not None and hello["type"] == "hello", hello
        for label in ("scope", "audio"):
            _attach(label)  # its ack is lost in transit (dropped below)
            _attach(label)  # the ackless client resends its attach
            ack = await await_until(
                lambda bound=label: peer.take(bound, drop_first_ack=True),
                timeout=5.0,
                description=f"re-acknowledged {label} attach",
            )
            assert ack == {"type": "attached", "v": 1, "channel": label}, ack
        # Both channels still work after the re-acknowledged retry.
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
    finally:
        await peer.close()
        await _tear_down_stack(stack)


async def test_websocket_clients_receive_no_attach_ack(tmp_path: Path) -> None:
    """The ack is lossy-data-channel only: the first WS control frame is hello."""
    stack = await _stand_up_stack(tmp_path)
    try:
        reader, writer, _ = await _ws_connect("127.0.0.1", stack.web.port, "/api/v1/ws")
        try:
            opcode, payload = await _ws_recv_frame(reader, timeout=10.0)
            assert opcode == 0x1
            assert json.loads(payload).get("type") == "hello"
        finally:
            with contextlib.suppress(Exception):
                await _close_ws(writer)
    finally:
        await _tear_down_stack(stack)


def _attach_text(label: str, key: str) -> str:
    """The golden ``attach_<label>`` document with a live controller key."""
    docs = api_channel_golden_documents_from_json(
        json.loads(_GOLDEN_JSON.read_text(encoding="utf-8"))
    ).documents
    return str(docs[f"attach_{label}"].text).replace("<SESSION-KEY>", key)


def _golden_ack(label: str) -> dict[str, Any]:
    docs = api_channel_golden_documents_from_json(
        json.loads(_GOLDEN_JSON.read_text(encoding="utf-8"))
    ).documents
    return dict(json.loads(str(docs[f"attach_ack_{label}"].text)))


def _registered(stack: _Stack, label: str) -> int:
    """Channels of this label the server still tracks (its connection
    registry: unregistered in ``serve_client_channel``'s finally, i.e.
    only after the handler has ended and its own cleanup ran)."""
    path = server_module._ATTACH_PATH_FOR_KIND[label]  # noqa: SLF001
    registry = stack.web._conn_manager._connections  # noqa: SLF001
    return sum(len(c) for (_, p), c in registry.items() if p == path)


async def _frame(peer: _LossyAckPeer, label: str, want: str) -> dict[str, Any]:
    """Next ``want``-typed text frame on ``label`` (others skipped)."""

    def _next() -> dict[str, Any] | None:
        while (frame := peer.take(label)) is not None:
            if frame.get("type") == want:
                return frame
        return None

    return await await_until(_next, timeout=10.0, description=f"{label} {want}")


async def _control_alive(peer: _LossyAckPeer) -> None:
    peer.send_text("control", json.dumps({"type": "controller_heartbeat"}))
    await _frame(peer, "control", "controller_heartbeat")


async def _attach_and_serve_label(peer: _LossyAckPeer, label: str, key: str) -> None:
    """Attach ``label`` with the golden frame; assert its ack and service."""
    peer.send_text(label, _attach_text(label, key))
    ack = await await_until(
        lambda: peer.take(label), timeout=10.0, description=f"{label} ack"
    )
    assert ack == _golden_ack(label) == {"type": "attached", "v": 1, "channel": label}
    if label == "scope":
        peer.send_text("scope", json.dumps({"type": "ping"}))
        assert await _frame(peer, "scope", "pong") == {"type": "pong"}
    else:
        peer.send_text("audio", json.dumps({"type": "audio_start", "direction": "tx"}))
        await _frame(peer, "audio", "audio_tx_format")


async def _channel_closed(channel: Any, timeout: float = 10.0) -> None:
    """Wait until ONE data channel reads ``closed`` client-side."""
    await await_until(
        lambda: channel.readyState == "closed" or None,
        timeout=timeout,
        description="channel closed",
    )


async def _release_session(port: int, key: str) -> int:
    """End one controller session server-side (``DELETE /api/v1/controller``).

    The endpoint calls ``ControllerAuthority.release`` → ``_end_session``,
    the same session-loss path the expiry watchdog and the WebRTC
    ``_fence_peer`` ICE fence use.
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        f"DELETE /api/v1/controller HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"X-RigPlane-Controller: {key}\r\n"
        "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
    )
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10.0)
    await asyncio.wait_for(reader.read(), timeout=10.0)
    writer.close()
    return int(head.split(b" ", 2)[1])


@_webrtc
@pytest.mark.parametrize("wait_for_cleanup", [True, False])
@pytest.mark.parametrize("label", ["scope", "audio"])
async def test_closed_channel_label_can_be_recreated_on_the_same_peer(
    tmp_path: Path, label: str, wait_for_cleanup: bool
) -> None:
    """Close the scope/audio channel from the client, re-create the SAME
    label on the SAME peer connection (production lossy config), attach
    it with the same key: a fresh ack and normal service, while control
    stays healthy. With ``wait_for_cleanup`` the first channel's server
    handler must have ended and unregistered (the connection registry
    drops it) before the second is created; without it the second is
    created straight after the close."""
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _LossyAckPeer()
    try:
        await peer.connect(stack.web, ("control", label), lossy=(label,))
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        await _attach_and_serve_label(peer, label, key)
        assert _registered(stack, label) == 1
        if label == "scope":
            assert len(stack.web._scope_handlers) == 1  # noqa: SLF001

        peer.close_channel(label)
        if wait_for_cleanup:
            await await_until(
                lambda: _registered(stack, label) == 0 or None,
                timeout=10.0,
                description=f"server {label} handler ended and unregistered",
            )
            if label == "scope":
                assert not stack.web._scope_handlers  # noqa: SLF001
            await _control_alive(peer)

        channel = peer.open_channel(label, lossy=True)
        await await_until(
            lambda: channel.readyState == "open" or None,
            timeout=15.0,
            description=f"re-created {label} channel open",
        )
        await _attach_and_serve_label(peer, label, key)
        await _control_alive(peer)
        await await_until(
            lambda: _registered(stack, label) == 1 or None,
            timeout=10.0,
            description=f"only the second {label} channel registered",
        )
        assert peer._pc.connectionState == "connected"  # noqa: SLF001
    finally:
        await peer.close()
        await _tear_down_stack(stack)


# ---------------------------------------------------------------------------
# MOR-4498: closing ONE data channel closes only that channel — never the
# peer connection, whose single owner is the WebRtcSessionManager.
# ---------------------------------------------------------------------------


@_webrtc
async def test_attach_timeout_closes_only_that_channel(tmp_path: Path) -> None:
    """A scope channel that never attaches is closed by the server's 5 s
    attach timeout — and ONLY that channel: control keeps answering on the
    same peer, which stays ``connected``."""
    stack = await _stand_up_stack(tmp_path)
    peer = _LossyAckPeer()
    try:
        await peer.connect(stack.web, ("control", "scope"), lossy=("scope",))
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        # scope stays silent: no attach frame is ever sent on it.
        opened_at = asyncio.get_running_loop().time()
        await _channel_closed(peer._channels["scope"], timeout=5.0 + 2.0)  # noqa: SLF001
        assert asyncio.get_running_loop().time() - opened_at <= 5.0 + 2.0
        await _control_alive(peer)
        assert peer._pc.connectionState == "connected"  # noqa: SLF001
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_refused_attach_closes_only_that_channel(tmp_path: Path) -> None:
    """A refused attach (a well-formed but never-granted key on audio)
    closes ONLY the refused channel: control keeps answering and the peer
    stays ``connected``."""
    stack = await _stand_up_stack(tmp_path)
    peer = _LossyAckPeer()
    try:
        await peer.connect(stack.web, ("control", "audio"), lossy=("audio",))
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        peer.send_text("audio", _attach_text("audio", "e" * 64))
        await _channel_closed(peer._channels["audio"])  # noqa: SLF001
        await _control_alive(peer)
        assert peer._pc.connectionState == "connected"  # noqa: SLF001
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_audio_channel_close_while_tx_running_matches_ws_socket_close(
    tmp_path: Path,
) -> None:
    """Closing the audio channel while TX audio runs ends exactly as the
    WebSocket audio socket closing: the audio handler stops its own TX
    audio, while the holder's PTT — keyed by control — stays keyed. That
    is MOR-3562's rule, pinned for the WS lane by
    ``test_path7_closing_an_auxiliary_control_channel_keeps_the_holders_tx``
    and the reconnect-storm audio-channel kills in
    tests/integration/test_multi_session_tx_lanes.py. Control stays up and
    unkey still releases."""
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _DcAvPeer()
    try:
        await peer.connect(stack.web)
        key = await _acquire_session(stack.web.port)
        await peer.attach_control(key)
        await peer.attach_audio(key)
        keyed = await peer.command("key-1", "ptt_on")
        assert keyed.get("ok") is True, keyed
        await await_until(
            lambda: stack.sim.ptt_on is True, timeout=10.0, description="sim keyed"
        )
        await _stream_audio(peer, 480)
        await _wait_air(stack)  # TX audio is really running
        assert peer._audio is not None  # noqa: SLF001
        peer._audio.close()  # noqa: SLF001 — ONLY the audio channel
        # The WS socket-close release: the audio handler stops its own TX
        # audio and unregisters. Once it has, the holder's PTT — keyed by
        # control, not by the audio channel — must still be keyed (MOR-3562).
        await await_until(
            lambda: _registered(stack, "audio") == 0 or None,
            timeout=10.0,
            description="server audio handler ended and unregistered",
        )
        assert stack.sim.ptt_on is True
        await peer.heartbeat()  # control still answers
        unkeyed = await peer.command("unkey-1", "ptt_off")
        assert unkeyed.get("ok") is True, unkeyed
        await await_until(
            lambda: stack.sim.ptt_on is False, timeout=5.0, description="sim unkeyed"
        )
        state = (await stack.composition.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_server_ended_session_closes_only_its_channels(tmp_path: Path) -> None:
    """The server ending a controller session closes every channel that
    session attached (control, scope, audio) and nothing else: the keyless
    ``api`` channel holds no session, so it stays open on the same peer
    (PR-B may build on it; nothing is built on it now), and the peer itself
    stays ``connected``."""
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _LossyAckPeer()
    try:
        await peer.connect(
            stack.web,
            ("control", "scope", "audio", "api"),
            lossy=("scope", "audio"),
        )
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        for label in ("scope", "audio"):
            await _attach_and_serve_label(peer, label, key)
        peer.send_text("api", _attach_text("api", key))  # keyless by design
        await _control_alive(peer)
        assert await _release_session(stack.web.port, key) == 200
        for label in ("control", "scope", "audio"):
            await _channel_closed(peer._channels[label])  # noqa: SLF001
            await await_until(
                lambda bound=label: _registered(stack, bound) == 0 or None,
                timeout=10.0,
                description=f"server {label} handler ended and unregistered",
            )
        # Every server-side step of the session end has run: the keyless
        # api channel holds no session, so nothing ended it — it stays
        # attached (PR-B may build on it; nothing is built on it now), and
        # the peer connection itself stays.
        assert peer._channels["api"].readyState == "open"  # noqa: SLF001
        assert _registered(stack, "api") == 1
        assert peer._pc.connectionState == "connected"  # noqa: SLF001
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_close_all_closes_every_peer(tmp_path: Path) -> None:
    """``WebRtcSessionManager.close_all`` tears down every live peer: both
    peers' channels close and the manager holds no session afterwards."""
    stack = await _stand_up_stack(tmp_path)
    first = _DcControlClient()
    second = _DcControlClient()
    try:
        await first.connect(stack.web)
        await first.attach(await _acquire_session(stack.web.port))
        await second.connect(stack.web)
        await second.attach(await _acquire_session(stack.web.port))
        manager = stack.web._webrtc_sessions  # noqa: SLF001
        assert manager is not None
        assert len(manager.active_session_ids) == 2
        await manager.close_all()
        assert manager.active_session_ids == []
        await first.wait_closed()
        await second.wait_closed()
    finally:
        await first.close()
        await second.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_server_initiated_channel_close_reaches_client_as_that_channels_close(
    tmp_path: Path,
) -> None:
    """A server-initiated close of ONE channel (the per-session connection
    limit evicting the oldest scope channel, ``1001 replaced by newer
    connection``) reaches the client as THAT channel's close event: the
    evicted channel closes, the other nine and control stay open, and the
    peer stays ``connected``."""
    stack = await _stand_up_stack(tmp_path)
    peer = _LossyAckPeer()
    try:
        # MAX_PER_IP_PER_CHANNEL is 9: the 10th scope channel's attach
        # evicts the first — all on one peer, one negotiation.
        await peer.connect(stack.web, ("control",) + ("scope",) * 10, lossy=("scope",))
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        scope_channels = [
            channel for label, channel in peer.every_channel if label == "scope"
        ]
        assert len(scope_channels) == 10
        # Sequential: each ack lands before the next attach, so the server
        # registers (and evicts) in this order.
        for channel in scope_channels:
            channel.send(_attach_text("scope", key))
            assert await await_until(
                lambda: peer.take("scope"), timeout=10.0, description="scope ack"
            ) == {"type": "attached", "v": 1, "channel": "scope"}
        await _channel_closed(scope_channels[0])
        for channel in scope_channels[1:]:
            assert channel.readyState == "open"
        await _control_alive(peer)
        assert peer._pc.connectionState == "connected"  # noqa: SLF001
        # The surviving scope channel still serves.
        scope_channels[-1].send(json.dumps({"type": "ping"}))
        assert await _frame(peer, "scope", "pong") == {"type": "pong"}
    finally:
        await peer.close()
        await _tear_down_stack(stack)
