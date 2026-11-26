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
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from _perf_helpers import fast_connect
from test_web_server import (
    _close_ws,
    _http_get,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
    _ws_skip_handshake,
)
from test_webrtc_split_channel import _FakeChannel
from testkit.ic7610_fakes import ic7610_lan_fake

from rigplane.radio import IcomRadio
from rigplane.rigctld import protocol as rigctld_protocol
from rigplane.rigctld.contract import (
    HamlibError,
    RigctldCommand,
    RigctldConfig,
    RigctldResponse,
)
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.controller_authority import ControllerAuthority
from rigplane.runtime.managed_tx_authority import (
    ManagedTxAuthority,
    ManagedTxSubmission,
)
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore
from rigplane.runtime.managed_tx_state import (
    ManagedTxIntentKind,
    ManagedTxOutcome,
    ManagedTxState,
    ManagedTxTransition,
)
from rigplane.testing.timing import VirtualClock, await_until
from rigplane.web import server as server_module
from rigplane.web.api_channel import api_channel_golden_documents_from_json
from rigplane.web.channel_attach import (
    RIGCTL_MAX_FRAME_BYTES,
    ChannelAttachedAck,
    ClientChannelParams,
    RigctlAttachErrorV1,
    data_channels_for,
    decode_rigctl_attach_result,
)
from rigplane.web.handlers import rigctl
from rigplane.web.protocol import (
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_TX,
    encode_json,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.transport import webrtc, webrtc_session
from rigplane.web.transport.webrtc import webrtc_available
from rigplane.web.transport.webrtc_contract import (
    WebRtcAnswer,
    WebRtcClose,
    WebRtcCloseResult,
    WebRtcOffer,
    WebRtcRequestError,
    WebRtcTransportStatus,
)
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
        # The answerer's session id, set by ``connect_via_http``.
        self.session_id: str | None = None

    async def _prepare_pc(self, *, host_only: bool = False) -> None:
        from aiortc import RTCConfiguration, RTCPeerConnection

        configuration = RTCConfiguration(iceServers=[]) if host_only else None
        self._pc = RTCPeerConnection(configuration)
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

    async def connect_via_http(self, port: int, *, host_only: bool = False) -> None:
        """Signal the offer through the public HTTP endpoint (browser path).

        POSTs the SDP offer to ``/api/v1/transport/webrtc/offer`` instead
        of calling the session manager directly.
        """
        from aiortc import RTCSessionDescription

        await self._prepare_pc(host_only=host_only)
        assert self._pc is not None and self._channel is not None
        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        offer_body = WebRtcOffer(
            sdp=self._pc.localDescription.sdp, type=self._pc.localDescription.type
        )
        status, body = await _http_post(
            port, "/api/v1/transport/webrtc/offer", offer_body.to_json()
        )
        assert status == 200, body
        answer = WebRtcAnswer.from_json(body)
        self.session_id = answer.session_id
        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer.sdp, type=answer.type)
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
    """A control + audio peer over WebRTC data channels on one pc.

    Mirrors the production offerer: control is ordered and reliable;
    audio is the split pair — binary frames on the lossy ``audio``
    channel, text frames (attach, ``audio_start``) on the reliable
    ordered ``audio-text`` companion. Each kind carries its session's
    attach frame; control attaches first because the audio attach is an
    auxiliary one and needs the primary in place.
    """

    def __init__(self) -> None:
        self._pc: Any = None
        self._control: Any = None
        self._audio: Any = None
        self._audio_text: Any = None
        self._received: list[Any] = []
        self._audio_received: list[Any] = []

    async def connect(self, web: WebServer, *, via_http: bool = False) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription

        self._pc = RTCPeerConnection()
        self._control = self._pc.createDataChannel("control", ordered=True)
        # The production split pair: attach_audio sends the attach frame and
        # audio_start back to back on the ordered text half, so the server
        # reads the attach FIRST; TX frames ride the lossy binary half.
        self._audio = self._pc.createDataChannel(
            "audio", ordered=False, maxRetransmits=0
        )
        self._audio_text = self._pc.createDataChannel("audio-text", ordered=True)

        @self._control.on("message")  # type: ignore[misc, no-untyped-call]
        def _on_control(message: Any) -> None:
            self._received.append(message)

        for half in (self._audio, self._audio_text):

            @half.on("message")  # type: ignore[misc, no-untyped-call]
            def _on_audio(message: Any) -> None:
                self._audio_received.append(message)

        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        offer_body = WebRtcOffer(
            sdp=self._pc.localDescription.sdp, type=self._pc.localDescription.type
        )
        if via_http:
            status, response = await _http_post(
                web.port, "/api/v1/transport/webrtc/offer", offer_body.to_json()
            )
            assert status == 200, response
        else:
            manager = web._webrtc_session_manager()  # noqa: SLF001
            response = await manager.negotiate(offer_body.sdp, offer_body.type)
        answer = WebRtcAnswer.from_json(response)
        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer.sdp, type=answer.type)
        )
        await await_until(
            lambda: all(
                c.readyState == "open"
                for c in (self._control, self._audio, self._audio_text)
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
        assert self._audio_text is not None
        self._audio_text.send(
            json.dumps(
                {
                    "type": "attach",
                    "controller_key": key,
                    "state_delta": 2,
                    "role": "primary",
                }
            )
        )
        self._audio_text.send(json.dumps({"type": "audio_start", "direction": "tx"}))
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
async def test_control_ptt_release_and_link_drop_unkey_simulator(
    tmp_path: Path,
) -> None:
    """Momentary PTT keys and releases; a live hold ends on link drop."""
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
        released = await dc.command("release-1", "ptt_off")
        assert released.get("ok") is True, released
        await await_until(
            lambda: stack.sim.ptt_on is False, timeout=5.0, description="sim unkeyed"
        )
        keyed = await dc.command("key-2", "ptt_on")
        assert keyed.get("ok") is True, keyed
        await await_until(
            lambda: stack.sim.ptt_on is True, timeout=10.0, description="sim rekeyed"
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
    reason="stream microphone PCM in real time during a three-second PTT hold"
)
async def test_webrtc_ptt_three_second_hold_delivers_audio_and_link_loss_releases(
    tmp_path: Path,
) -> None:
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _DcAvPeer()
    clock = asyncio.get_running_loop().time
    try:
        await peer.connect(stack.web, via_http=True)
        key = await _acquire_session(stack.web.port)
        await peer.attach_control(key)
        await peer.attach_audio(key)
        await peer.heartbeat()

        assert stack.sim.ptt_on is False
        transition_start = len(stack.sim.ptt_transitions)
        pressed_at = clock()
        assert (await peer.command("key-1", "ptt_on"))["ok"] is True
        await await_until(lambda: stack.sim.ptt_on, timeout=5, description="TX")
        deadline = pressed_at + 3.0
        frame = _pcm_tx_frame(480)
        while clock() < deadline:
            assert stack.sim.ptt_on is True
            peer.send_audio(frame)
            await asyncio.sleep(0.020)
        assert stack.sim.ptt_on is True
        released_at = clock()
        assert (await peer.command("release-1", "ptt_off"))["ok"] is True
        await await_until(lambda: not stack.sim.ptt_on, timeout=5, description="RX")
        state = (await stack.composition.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX

        await peer.heartbeat()
        assert (await peer.command("key-2", "ptt_on"))["ok"] is True
        await await_until(lambda: stack.sim.ptt_on, timeout=5, description="second TX")
        dropped_at = clock()
        await peer.close()
        await await_until(
            lambda: not stack.sim.ptt_on,
            timeout=_SILENT_LOSS_BUDGET_S,
            description="RX after link loss",
        )
        state = (await stack.composition.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX

        transitions = stack.sim.ptt_transitions[transition_start:]
        assert [keyed for _, keyed in transitions] == [True, False, True, False]
        keyed_at, rx_at, rekeyed_at, drop_rx_at = (stamp for stamp, _ in transitions)
        frame_count = sum(
            1
            for received in stack.sim.audio_tx_frames
            if keyed_at <= received.received_at < released_at
            and received.ptt
            and any(received.payload)
        )
        print(
            f"WEBRTC_PTT frames={frame_count} hold_s={released_at - pressed_at:.3f}"
            f" keyed_s={keyed_at - pressed_at:.3f} release_s={released_at - pressed_at:.3f}"
            f" rx_s={rx_at - pressed_at:.3f} second_keyed_s={rekeyed_at - pressed_at:.3f}"
            f" drop_s={dropped_at - pressed_at:.3f} drop_rx_s={drop_rx_at - pressed_at:.3f}"
            f" drop_to_rx_s={drop_rx_at - dropped_at:.3f} budget_s={_SILENT_LOSS_BUDGET_S:.3f}"
        )
        assert frame_count > 0
        assert 0 <= drop_rx_at - dropped_at <= _SILENT_LOSS_BUDGET_S
    finally:
        await peer.close()
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


async def _post_close(port: int, close: WebRtcClose) -> tuple[int, dict[str, Any]]:
    """POST a typed close body through the real HTTP listener and router."""
    return await _http_post(port, "/api/v1/transport/webrtc/close", close.to_json())


@_webrtc
async def test_close_route_ends_one_peer_and_next_offer_is_answered(
    tmp_path: Path,
) -> None:
    """``POST .../webrtc/close`` with a session id ends that peer only.

    The peer sees its channel close, the other peer keeps being served, a
    second close of the same id is a typed ``close_error`` 404, and a later
    offer is still answered (the manager stays usable).
    """
    stack = await _stand_up_stack(tmp_path)
    first = _DcControlClient()
    second = _DcControlClient()
    later = _DcControlClient()
    try:
        # Attach right after each negotiate: an unattached channel is
        # closed after 5 s, which would look like the close under test.
        await first.connect_via_http(stack.web.port)
        await first.attach(await _acquire_session(stack.web.port))
        await second.connect_via_http(stack.web.port)
        await second.attach(await _acquire_session(stack.web.port))
        assert first.session_id is not None and second.session_id is not None
        manager = stack.web._webrtc_sessions  # noqa: SLF001
        assert manager is not None

        status, body = await _post_close(stack.web.port, WebRtcClose(first.session_id))
        assert status == 200, body
        assert WebRtcCloseResult.from_json(body).closed == (first.session_id,)
        assert manager.active_session_ids == [second.session_id]
        await first.wait_closed()
        await second.heartbeat()

        status, body = await _post_close(stack.web.port, WebRtcClose(first.session_id))
        assert status == 404, body
        assert WebRtcRequestError.from_json(body).code == "close_error"

        await later.connect_via_http(stack.web.port)
        assert later.session_id not in (first.session_id, second.session_id)
    finally:
        await first.close()
        await second.close()
        await later.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_close_route_without_session_id_ends_every_peer(tmp_path: Path) -> None:
    """A close with no session id ends every live peer through the route."""
    stack = await _stand_up_stack(tmp_path)
    peers = [_DcControlClient(), _DcControlClient()]
    try:
        for peer in peers:
            await peer.connect_via_http(stack.web.port)
            await peer.attach(await _acquire_session(stack.web.port))
        manager = stack.web._webrtc_sessions  # noqa: SLF001
        assert manager is not None
        ids = {peer.session_id for peer in peers}

        status, body = await _post_close(stack.web.port, WebRtcClose())
        assert status == 200, body
        assert set(WebRtcCloseResult.from_json(body).closed) == ids
        assert manager.active_session_ids == []
        for peer in peers:
            await peer.wait_closed()
    finally:
        for peer in peers:
            await peer.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_close_route_under_remote_control_ends_peer_and_releases_ptt(
    tmp_path: Path,
) -> None:
    """The remote-access switch-off while a remote peer holds control and TX.

    Remote mode, the peer keyed the radio over its data channel; a local
    close through the HTTP route is admitted, the peer sees its channel
    close, and the session-end path the channel teardown already runs
    releases the grant and unkeys the radio — well inside the controller
    TTL, so expiry is not what releases it.
    """
    stack = await _stand_up_stack(tmp_path)
    dc = _DcControlClient()
    try:
        await dc.connect_via_http(stack.web.port)
        status, _ = await _http_post(
            stack.web.port, "/api/v1/controller/mode", {"mode": "remote"}
        )
        assert status == 200, status
        key = await _acquire_session(stack.web.port)
        hello = await dc.attach(key)
        assert hello["type"] == "hello"
        keyed = await dc.command("key-1", "ptt_on")
        assert keyed.get("ok") is True, keyed
        await await_until(
            lambda: stack.sim.ptt_on is True, timeout=10.0, description="sim keyed"
        )
        assert dc.session_id is not None

        status, body = await _post_close(stack.web.port, WebRtcClose(dc.session_id))
        assert status == 200, body
        assert WebRtcCloseResult.from_json(body).closed == (dc.session_id,)
        await dc.wait_closed(timeout=3.0)
        await await_until(
            lambda: stack.sim.ptt_on is False,
            timeout=3.0,
            description="PTT released by the session end",
        )
        await await_until(
            lambda: stack.web._controller.status()["state"] == "idle",  # noqa: SLF001
            timeout=3.0,
            description="remote grant released",
        )
    finally:
        await dc.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_close_dispatches_no_channel_arriving_during_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A channel that arrives while its session tears down gets no handler.

    The attached handler's cleanup is held open after the close cancels
    it; meanwhile the peer opens a new channel and sends its attach frame.
    That channel must be closed without being dispatched (nothing reaches
    a handler on a peer being closed), and the close completes once the
    held cleanup finishes.
    """
    stack = await _stand_up_stack(tmp_path)
    dc = _DcControlClient()
    attached: list[str] = []
    cleanup_started = asyncio.Event()
    release = asyncio.Event()

    async def held_attach(
        conn: Any,
        kind: str,
        params: ClientChannelParams,
        peer: tuple[str, int],
        *,
        transport: str,
        track_owners: set[str] | None = None,
    ) -> None:
        attached.append(kind)
        try:
            await release.wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await release.wait()
            raise

    monkeypatch.setattr(stack.web, "attach_client_channel", held_attach)
    close_task: asyncio.Task[tuple[int, dict[str, Any]]] | None = None
    try:
        await dc.connect_via_http(stack.web.port)
        assert dc.session_id is not None
        key = await _acquire_session(stack.web.port)
        attach_frame = ClientChannelParams(controller_key=key).to_attach_frame()
        dc._channel.send(attach_frame)  # noqa: SLF001
        await await_until(
            lambda: attached == ["control"], timeout=10.0, description="attached"
        )

        close_task = asyncio.ensure_future(
            _post_close(stack.web.port, WebRtcClose(dc.session_id))
        )
        await await_until(
            cleanup_started.is_set, timeout=10.0, description="teardown in progress"
        )
        late = dc._pc.createDataChannel("api", ordered=True)  # noqa: SLF001

        @late.on("open")  # type: ignore[misc, no-untyped-call]
        def _on_open() -> None:
            late.send(attach_frame)

        await await_until(
            lambda: late.readyState == "closed",
            timeout=10.0,
            description="late channel closed undispatched",
        )
        assert attached == ["control"]
        assert not close_task.done()

        release.set()
        status, body = await asyncio.wait_for(close_task, timeout=10.0)
        assert status == 200, body
        assert WebRtcCloseResult.from_json(body).closed == (dc.session_id,)
    finally:
        release.set()
        if close_task is not None:
            if not close_task.done():
                close_task.cancel()
            with contextlib.suppress(BaseException):
                await close_task
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


class _ChannelSetPeer:
    """A peer that opens production data channels per KIND (K5 set).

    ``control``/``api`` are one ordered reliable channel; ``scope`` and
    ``audio`` are the split pair (lossy binary channel + reliable ordered
    ``*-text`` companion), exactly as ``DATA_CHANNELS`` configures them.
    Text goes out on the kind's text-carrying channel; frames from every
    channel of a kind land in ``received[kind]`` (and per label in
    ``received_by_label``).
    """

    def __init__(self) -> None:
        self._pc: Any = None
        self._channels: dict[str, Any] = {}
        self.received: dict[str, list[Any]] = {}
        self.received_by_label: dict[str, list[Any]] = {}
        # Every channel ever opened, in order, label included: a label can
        # appear more than once (re-created channels, same-kind copies).
        self.every_channel: list[tuple[str, Any]] = []

    def open_kind(self, kind: str) -> dict[str, Any]:
        """Create (or re-create) every channel of ``kind`` on this peer.

        A re-created kind starts with an empty inbox, so nothing from the
        closed channels is mistaken for it. ``_channels`` and the per-label
        inbox are keyed by label (the newest pair wins).
        """
        opened: dict[str, Any] = {}
        self.received[kind] = []
        for spec in data_channels_for(kind):
            kwargs: dict[str, Any] = {"ordered": spec.ordered}
            if spec.max_retransmits is not None:
                kwargs["maxRetransmits"] = spec.max_retransmits
            channel = self._pc.createDataChannel(spec.label, **kwargs)
            self._channels[spec.label] = channel
            self.received_by_label[spec.label] = []
            self.every_channel.append((spec.label, channel))
            opened[spec.label] = channel

            @channel.on("message")  # type: ignore[misc, no-untyped-call]
            def _on_message(
                message: Any, _kind: str = kind, _label: str = spec.label
            ) -> None:
                self.received[_kind].append(message)
                self.received_by_label[_label].append(message)

        return opened

    def text_channel(self, kind: str) -> Any:
        """The channel that carries ``kind``'s text frames."""
        spec = next(s for s in data_channels_for(kind) if s.carries != "binary")
        return self._channels[spec.label]

    def close_kind(self, kind: str) -> None:
        """Close every channel of ``kind`` (the peer connection stays)."""
        for spec in data_channels_for(kind):
            self._channels[spec.label].close()

    async def connect(
        self,
        web: WebServer,
        kinds: tuple[str, ...],
        *,
        lone_labels: tuple[str, ...] = (),
    ) -> None:
        """Open every production channel of each kind on one peer, plus
        ``lone_labels`` as single ordered channels (a half without its pair)."""
        from aiortc import RTCPeerConnection, RTCSessionDescription

        self._pc = RTCPeerConnection()
        for kind in kinds:
            self.open_kind(kind)
        for label in lone_labels:
            self._channels[label] = self._pc.createDataChannel(label, ordered=True)

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

    def send_text(self, kind: str, text: str) -> None:
        """Send one text frame on the kind's text-carrying channel."""
        self.text_channel(kind).send(text)

    def take(self, kind: str) -> dict[str, Any] | None:
        """The kind's next text frame as parsed JSON (binary skipped)."""
        while self.received[kind]:
            message = self.received[kind].pop(0)
            if isinstance(message, str):
                return dict(json.loads(message))
        return None

    async def close(self) -> None:
        """Close the peer connection (idempotent, never raises)."""
        if self._pc is not None:
            with contextlib.suppress(Exception):
                await self._pc.close()


@_webrtc
async def test_attach_ack_on_split_channels_only(tmp_path: Path) -> None:
    """The scope/audio kinds get one ``{"type":"attached","v":1,...}`` text
    frame as their FIRST frame (an ack-ignoring audio client is proven by
    ``test_only_holders_audio...``); control's first frame is its
    ``hello``, and api gets none — every ``test_webrtc_api_channel.py``
    pump asserts each inbound frame is bytes.
    """
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _ChannelSetPeer()
    try:  # peer setup, negotiation and acquisition are in cleanup scope
        await peer.connect(stack.web, ("control", "scope", "audio"))
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        first = await await_until(
            lambda: peer.take("control"), timeout=10.0, description="control hello"
        )
        assert first is not None and first["type"] == "hello", first
        for label in ("scope", "audio"):  # the ack is the first frame
            peer.send_text(label, _attach_text(label, key))
            assert await await_until(
                lambda bound=label: peer.take(bound), timeout=10.0, description=label
            ) == {"type": "attached", "v": 1, "channel": label}
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_split_kind_text_rides_the_reliable_half_and_bytes_the_lossy_half(
    tmp_path: Path,
) -> None:
    """K5 split (MOR-272): the attach and every text command of scope/audio
    travel on the reliable ``*-text`` channel and every server text reply
    (ack, pong, ``audio_tx_format``) comes back on it; the lossy binary
    channel carries no text at all. Both halves serve ONE handler: a text
    command on the reliable half arms TX, and the TX frames sent on the
    lossy half reach the air."""
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _ChannelSetPeer()
    try:
        await peer.connect(stack.web, ("control", "scope", "audio"))
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        for label in ("scope", "audio"):
            await _attach_and_serve_label(peer, label, key)
        peer.send_text(
            "control",
            json.dumps({"type": "cmd", "id": "k1", "name": "ptt_on", "params": {}}),
        )
        keyed = await _frame(peer, "control", "response")
        assert keyed.get("ok") is True, keyed
        for _ in range(10):
            peer._channels["audio"].send(_pcm_tx_frame(480))  # noqa: SLF001
        await _wait_air(stack)
        for label in ("scope", "audio"):
            replies = peer.received_by_label[label]
            assert not [m for m in replies if isinstance(m, str)], replies
        peer.send_text(
            "control",
            json.dumps({"type": "cmd", "id": "u1", "name": "ptt_off", "params": {}}),
        )
        assert (await _frame(peer, "control", "response")).get("ok") is True
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


async def _frame(peer: _ChannelSetPeer, label: str, want: str) -> dict[str, Any]:
    """Next ``want``-typed text frame on ``label`` (others skipped)."""

    def _next() -> dict[str, Any] | None:
        while (frame := peer.take(label)) is not None:
            if frame.get("type") == want:
                return frame
        return None

    return await await_until(_next, timeout=10.0, description=f"{label} {want}")


async def _control_alive(peer: _ChannelSetPeer) -> None:
    peer.send_text("control", json.dumps({"type": "controller_heartbeat"}))
    await _frame(peer, "control", "controller_heartbeat")


async def _attach_and_serve_label(peer: _ChannelSetPeer, label: str, key: str) -> None:
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
    # Every text reply of the kind came back on its reliable half.
    assert not [m for m in peer.received_by_label[label] if isinstance(m, str)], (
        f"text on the lossy {label} channel"
    )


async def _channel_closed(channel: Any, timeout: float = 10.0) -> None:
    """Wait until ONE data channel reads ``closed`` client-side."""
    await await_until(
        lambda: channel.readyState == "closed" or None,
        timeout=timeout,
        description="channel closed",
    )


@_webrtc
async def test_webrtc_rx_sender_cancelled_when_peer_connection_goes_away(
    tmp_path: Path,
) -> None:
    """MOR-4676: a clean peer teardown ends the RX sender and unsubscribes.

    A WebRTC session starts RX audio, then the whole peer connection
    goes away with no ``audio_stop``. The session's audio sender must be
    cancelled and its broadcaster subscription removed within a bound —
    not kept running in parallel with the next session's sender while
    the audio bus keeps a stale ``web-audio`` subscriber.
    """
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _ChannelSetPeer()
    try:
        await peer.connect(stack.web, ("control", "audio"))
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        await _attach_and_serve_audio_rx(peer, key)
        broadcaster = stack.web._audio_broadcaster  # noqa: SLF001
        assert await await_until(
            lambda: len(broadcaster._clients) == 1 or None,  # noqa: SLF001
            timeout=10.0,
            description="broadcaster subscribed the RX client",
        )
        assert broadcaster._subscription is not None  # noqa: SLF001
        assert _live_audio_senders(), "no audio sender task while RX is live"
        # The LAN-client drop: the whole peer session goes away with no
        # ``audio_stop`` — channels and peer connection together.
        await peer.close()

        def _no_sessions() -> bool | None:
            manager = stack.web._webrtc_session_manager()  # noqa: SLF001
            return True if manager is None or not manager.active_session_ids else None

        assert await await_until(
            _no_sessions, timeout=10.0, description="webrtc session dropped"
        ), "server still holds the dead peer session"
        assert await await_until(
            lambda: _registered(stack, "audio") == 0 or None,
            timeout=10.0,
            description="server audio handler ended and unregistered",
        ), "dead session's audio handler still registered"
        assert await await_until(
            lambda: len(broadcaster._clients) == 0 or None,  # noqa: SLF001
            timeout=10.0,
            description="broadcaster dropped the dead RX client",
        ), "dead session's broadcaster subscription outlives its channels"
        assert await await_until(
            lambda: broadcaster._subscription is None or None,  # noqa: SLF001
            timeout=10.0,
            description="relay subscription released",
        ), "audio bus keeps the dead session's web-audio subscriber"
        assert await await_until(
            lambda: not _live_audio_senders() or None,
            timeout=10.0,
            description="audio sender cancelled",
        ), "dead session's audio sender still runs"
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_webrtc_rx_sender_cancelled_when_peer_vanishes_without_close(
    tmp_path: Path,
) -> None:
    """MOR-4676: a peer that vanishes without closing still loses its sender.

    The session goes away with no channel close and no ``audio_stop``
    (the network path died the way the LAN client's did): the
    controller-session loss path must still cancel the session's audio
    sender and remove its subscriber within a bound, not run it in
    parallel with the next session's sender.
    """
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _ChannelSetPeer()
    try:
        await peer.connect(stack.web, ("control", "audio"))
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        await _attach_and_serve_audio_rx(peer, key)
        broadcaster = stack.web._audio_broadcaster  # noqa: SLF001
        assert await await_until(
            lambda: len(broadcaster._clients) == 1 or None,  # noqa: SLF001
            timeout=10.0,
            description="broadcaster subscribed the RX client",
        )
        assert broadcaster._subscription is not None  # noqa: SLF001
        assert _live_audio_senders(), "no audio sender task while RX is live"
        # The peer vanishes: nothing closes, no heartbeat refreshes the
        # controller session (6 s TTL), so the session-loss path ends it.
        assert await await_until(
            lambda: _registered(stack, "audio") == 0 or None,
            timeout=15.0,
            description="server audio handler ended and unregistered",
        ), "dead session's audio handler still registered"
        assert await await_until(
            lambda: len(broadcaster._clients) == 0 or None,  # noqa: SLF001
            timeout=15.0,
            description="broadcaster dropped the dead RX client",
        ), "dead session's broadcaster subscription outlives its session"
        assert await await_until(
            lambda: broadcaster._subscription is None or None,  # noqa: SLF001
            timeout=15.0,
            description="relay subscription released",
        ), "audio bus keeps the dead session's web-audio subscriber"
        assert await await_until(
            lambda: not _live_audio_senders() or None,
            timeout=15.0,
            description="audio sender cancelled",
        ), "dead session's audio sender still runs"
    finally:
        await peer.close()
        await _tear_down_stack(stack)


def _live_audio_senders() -> list[str]:
    """Names of live per-attachment audio sender tasks (``audio-sender``)."""
    return sorted(
        task.get_name()
        for task in asyncio.all_tasks()
        if task.get_name() == "audio-sender" and not task.done()
    )


async def _keep_peer_alive(peer: _ChannelSetPeer, done: asyncio.Event) -> None:
    """Heartbeat a peer's control channel until ``done`` (remote TTL guard).

    Remote mode keeps ONE controller grant for every peer on the key: while
    this beats, the grant (and its TTL revoke) can never fire, so only the
    peer teardown under test can end the attachments.
    """
    while not done.is_set():
        with contextlib.suppress(Exception):
            peer.send_text("control", json.dumps({"type": "controller_heartbeat"}))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(done.wait(), timeout=2.0)


@_webrtc
async def test_webrtc_audio_supersession_ends_stale_sender(
    tmp_path: Path,
) -> None:
    """MOR-4676: a re-attached audio pair supersedes the silently stale one.

    Remote mode mints tickets with no ``tx_owner``, so the per-attachment
    owner sets stay empty and a vanished session has nothing to end. The
    production leak: a remote peer whose lossy audio channels died without
    a close event kept its audio sender (50 fps) and its subscriber while
    its own healthy control channel kept the grant refreshed — the stale
    sender runs in parallel with the live session's sender.

    The server-observable event in that story is the client's re-attach:
    the peer opens a fresh audio pair and attaches it while control keeps
    heartbeating (no close and no ``audio_stop`` ever arrive on the stale
    pair). One peer holds one audio pair at a time, so serving the new
    attachment aborts the stale one through its normal exit path: the
    stale sender is cancelled and unsubscribed within a bound, while the
    shared relay keeps running for the live attachment.
    """
    stack = await _stand_up_stack(tmp_path, audio=True)
    ghost = _ChannelSetPeer()
    keepalive_done = asyncio.Event()
    keepalive: asyncio.Task[None] | None = None
    try:
        status, _ = await _http_post(
            stack.web.port, "/api/v1/controller/mode", {"mode": "remote"}
        )
        assert status == 200, status
        await ghost.connect(stack.web, ("control", "audio"))
        key = await _acquire_session(stack.web.port)
        ghost.send_text("control", _attach_text("control", key))
        assert (await _frame(ghost, "control", "hello"))["type"] == "hello"
        await _attach_and_serve_audio_rx(ghost, key)
        broadcaster = stack.web._audio_broadcaster  # noqa: SLF001
        assert await await_until(
            lambda: len(broadcaster._clients) == 1 or None,  # noqa: SLF001
            timeout=10.0,
            description="broadcaster subscribed the RX client",
        )
        assert broadcaster._subscription is not None  # noqa: SLF001
        assert len(_live_audio_senders()) == 1, "no audio sender task while RX is live"
        # The control channel keeps heartbeating, so the shared grant never
        # expires — only the supersession may end the stale sender.
        keepalive = asyncio.create_task(_keep_peer_alive(ghost, keepalive_done))
        # The stale pair never reported a close and sent no audio_stop; the
        # client re-opens its audio pair and attaches anew on it.
        pair = ghost.open_kind("audio")
        await await_until(
            lambda: all(c.readyState == "open" for c in pair.values()) or None,
            timeout=15.0,
            description="re-opened audio pair open",
        )
        ghost.send_text("audio", _attach_text("audio", key))
        assert await await_until(
            lambda: ghost.take("audio"), timeout=10.0, description="audio ack"
        ) == {"type": "attached", "v": 1, "channel": "audio"}
        ghost.send_text("audio", json.dumps({"type": "audio_start", "direction": "rx"}))
        assert (await _frame(ghost, "audio", "audio_format"))["type"] == "audio_format"
        # The stale sender is gone: exactly the live attachment's sender
        # remains, one subscriber, and the relay still runs for it.
        assert await await_until(
            lambda: len(_live_audio_senders()) == 1 or None,
            timeout=10.0,
            description="stale audio sender cancelled",
        ), "stale audio sender still runs in parallel with the live one"
        assert await await_until(
            lambda: len(broadcaster._clients) == 1 or None,  # noqa: SLF001
            timeout=10.0,
            description="broadcaster converged back to one RX client",
        ), "stale subscriber outlives its superseded attachment"
        assert broadcaster._subscription is not None  # noqa: SLF001
        await _control_alive(ghost)
    finally:
        keepalive_done.set()
        if keepalive is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(keepalive, timeout=5.0)
        await ghost.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_webrtc_audio_restart_without_stop_does_not_orphan_subscription(
    tmp_path: Path,
) -> None:
    """MOR-4676: re-arming RX on one channel keeps exactly one subscription.

    A second ``audio_start`` with no ``audio_stop`` replaces the
    attachment's subscription instead of orphaning the previous queue:
    otherwise the relay and its ``web-audio`` bus subscription never see
    an empty client set again, even after the session's own teardown
    unsubscribes the latest queue.
    """
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _ChannelSetPeer()
    try:
        await peer.connect(stack.web, ("control", "audio"))
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        await _attach_and_serve_audio_rx(peer, key)
        broadcaster = stack.web._audio_broadcaster  # noqa: SLF001
        assert await await_until(
            lambda: len(broadcaster._clients) == 1 or None,  # noqa: SLF001
            timeout=10.0,
            description="broadcaster subscribed the RX client",
        )
        # Re-arm RX on the SAME channel with no audio_stop in between.
        peer.send_text("audio", json.dumps({"type": "audio_start", "direction": "rx"}))
        assert (await _frame(peer, "audio", "audio_format"))["type"] == "audio_format"
        assert len(broadcaster._clients) == 1, (  # noqa: SLF001
            "re-armed RX orphaned its previous broadcaster queue"
        )
        peer.close_kind("audio")
        peer.close_kind("control")
        assert await await_until(
            lambda: _registered(stack, "audio") == 0 or None,
            timeout=10.0,
            description="server audio handler ended and unregistered",
        ), "dead session's audio handler still registered"
        assert await await_until(
            lambda: len(broadcaster._clients) == 0 or None,  # noqa: SLF001
            timeout=10.0,
            description="broadcaster dropped the RX client",
        ), "re-armed RX left a stale subscriber behind its session"
        assert await await_until(
            lambda: broadcaster._subscription is None or None,  # noqa: SLF001
            timeout=10.0,
            description="relay subscription released",
        ), "audio bus keeps a stale web-audio subscriber"
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_webrtc_rx_rearm_with_tx_active_preserves_relay(
    tmp_path: Path,
) -> None:
    """MOR-4676: re-arming RX with TX active never drops relay demand.

    With one RX client and an active TX lease the re-arm must replace the
    attachment's queue through the running relay — subscribe the new queue
    before unsubscribing the old one — so the client set never empties,
    the shared ``web-audio`` demand never drops, and the session never
    disarms and re-arms TX underneath an ongoing transmission.
    """
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
        await _wait_air(stack)
        assert peer._audio_text is not None  # noqa: SLF001
        # A stable client_id rides every (re)subscribe (MOR-924): the re-arm
        # below must not coalesce the attachment with itself.
        rx_start = json.dumps(
            {"type": "audio_start", "direction": "rx", "client_id": "tab-rearm"}
        )
        peer._audio_text.send(rx_start)  # noqa: SLF001
        await peer._until(  # noqa: SLF001
            peer._audio_received,  # noqa: SLF001
            lambda m: m.get("type") == "audio_format",
            timeout=10.0,
        )
        broadcaster = stack.web._audio_broadcaster  # noqa: SLF001
        assert await await_until(
            lambda: len(broadcaster._clients) == 1 or None,  # noqa: SLF001
            timeout=10.0,
            description="broadcaster subscribed the RX client",
        )
        relay_before = broadcaster._relay_task  # noqa: SLF001
        subscription_before = broadcaster._subscription  # noqa: SLF001
        assert relay_before is not None and subscription_before is not None
        aired_before = len(stack.sim.audio_tx_frames)
        # Re-arm RX on the SAME channel with no audio_stop in between.
        peer._audio_text.send(rx_start)  # noqa: SLF001
        await peer._until(  # noqa: SLF001
            peer._audio_received,  # noqa: SLF001
            lambda m: m.get("type") == "audio_format",
            timeout=10.0,
        )
        # The relay never dropped: same task, same RX demand, one client.
        assert len(broadcaster._clients) == 1  # noqa: SLF001
        assert broadcaster._relay_task is relay_before  # noqa: SLF001
        assert broadcaster._subscription is subscription_before  # noqa: SLF001
        # TX sailed through the re-arm: still keyed, still flowing.
        assert stack.sim.ptt_on is True
        await _stream_audio(peer, 480)
        await await_until(
            lambda: len(stack.sim.audio_tx_frames) > aired_before or None,
            timeout=_GATE_TIMEOUT_S,
            description="TX audio still reaches the air after RX re-arm",
        )
    finally:
        await peer.close()
        await _tear_down_stack(stack)


async def _attach_and_serve_audio_rx(peer: _ChannelSetPeer, key: str) -> None:
    """Attach the audio kind and arm RX; assert the ``audio_format`` ack."""
    peer.send_text("audio", _attach_text("audio", key))
    assert await await_until(
        lambda: peer.take("audio"), timeout=10.0, description="audio ack"
    ) == {"type": "attached", "v": 1, "channel": "audio"}
    peer.send_text("audio", json.dumps({"type": "audio_start", "direction": "rx"}))
    assert (await _frame(peer, "audio", "audio_format"))["type"] == "audio_format"


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
    """Close the scope/audio pair from the client, re-create the SAME kind
    on the SAME peer connection (production split config), attach it with
    the same key: a fresh ack and normal service, while control stays
    healthy. With ``wait_for_cleanup`` the first pair's server handler must
    have ended and unregistered (the connection registry drops it) before
    the second is created; without it the second is created straight
    after the close."""
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _ChannelSetPeer()
    try:
        await peer.connect(stack.web, ("control", label))
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        await _attach_and_serve_label(peer, label, key)
        assert _registered(stack, label) == 1
        if label == "scope":
            assert len(stack.web._scope_handlers) == 1  # noqa: SLF001

        peer.close_kind(label)
        if wait_for_cleanup:
            await await_until(
                lambda: _registered(stack, label) == 0 or None,
                timeout=10.0,
                description=f"server {label} handler ended and unregistered",
            )
            if label == "scope":
                assert not stack.web._scope_handlers  # noqa: SLF001
            await _control_alive(peer)

        pair = peer.open_kind(label)
        await await_until(
            lambda: all(c.readyState == "open" for c in pair.values()) or None,
            timeout=15.0,
            description=f"re-created {label} pair open",
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
async def test_rigctl_data_channel_reads_simulator_frequency(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A Tower-style peer should get a CAT reply on its own controller grant."""
    stack = await _stand_up_stack(tmp_path)
    peer = _DcControlClient()
    caplog.set_level("INFO", logger="rigplane.web.transport.webrtc_session")
    try:
        await peer.connect_via_http(stack.web.port, host_only=True)
        key = await _acquire_session(stack.web.port)
        await peer.attach(key)

        # Spell the label on the wire so this reaches today's answerer even
        # though the production offerer's channel table has no rigctl entry.
        channel = peer._pc.createDataChannel("rigctl", ordered=True)  # noqa: SLF001
        frames: list[str | bytes] = []
        channel.on("message", frames.append)

        def send_attach() -> None:
            channel.send(
                json.dumps(
                    {
                        "type": "attach",
                        "v": 1,
                        "controller_key": key,
                        "role": "auxiliary",
                    }
                )
            )

        channel.on("open", send_attach)

        async def next_frame() -> str | bytes | None:
            await await_until(
                lambda: bool(frames) or channel.readyState == "closed",
                timeout=5.0,
                description="rigctl frame or channel close",
            )
            return frames.pop(0) if frames else None

        ack = await next_frame()
        reply = None
        if isinstance(ack, str) and json.loads(ack) == {
            "type": "attached",
            "v": 1,
            "channel": "rigctl",
        }:
            channel.send("f\n")
            reply = await next_frame()

        await peer.heartbeat()
        assert not stack.sim.ptt_on and not stack.sim.ptt_transitions
        unknown_label = "WebRTC: closing unknown data channel 'rigctl'"
        reason = next(
            (message for message in caplog.messages if message == unknown_label),
            channel.readyState,
        )
        # This assertion flips from RED (unknown-label close, no reply) to
        # GREEN when the carrier attaches and returns the simulator's frequency.
        assert reply == "14074000\n", (
            f"rigctl attach={ack!r}, reply={reply!r}: {reason}"
        )
    finally:
        await peer.close()
        await _tear_down_stack(stack)


# MOR-4691 v1 wire fixtures, shared with the Pro offerer. JSON spacing and
# member order are incidental. The KEY is a fixture, never a live grant.
RIGCTL_ATTACH_V1 = '{"type":"attach","v":1,"controller_key":"KEY","role":"auxiliary"}'
RIGCTL_ATTACHED_V1 = '{"type":"attached","v":1,"channel":"rigctl"}'
RIGCTL_ATTACH_ERROR_V1 = (
    '{"type":"attach_error","v":1,"channel":"rigctl","code":"rigctl_busy"}'
)


def test_rigctl_attach_wire_v1() -> None:
    params = ClientChannelParams("KEY", "auxiliary", state_delta=2, rigctl_version=1)
    ack = ChannelAttachedAck("rigctl")
    refused = RigctlAttachErrorV1("rigctl_busy")
    assert (
        ClientChannelParams.from_rigctl_attach_frame(RIGCTL_ATTACH_V1.encode())
        == params
    )
    assert decode_rigctl_attach_result(RIGCTL_ATTACHED_V1) == ack
    assert decode_rigctl_attach_result(RIGCTL_ATTACH_ERROR_V1) == refused
    assert json.loads(params.to_attach_frame()) == json.loads(RIGCTL_ATTACH_V1)
    assert json.loads(ack.encode()) == json.loads(RIGCTL_ATTACHED_V1)
    assert json.loads(refused.encode()) == json.loads(RIGCTL_ATTACH_ERROR_V1)
    with pytest.raises(ValueError, match="int 'v'"):
        ClientChannelParams.from_rigctl_attach_frame(
            RIGCTL_ATTACH_V1.replace('"v":1', '"v":true').encode()
        )


def test_rigctl_wire_receivers_skip_unknown_kinds_and_keep_unknown_enum_values() -> (
    None
):
    future = '{"type":"future_notice","checked":false}'
    assert ClientChannelParams.from_rigctl_attach_frame(future.encode()) is None
    assert decode_rigctl_attach_result(future) is None
    params = ClientChannelParams.from_rigctl_attach_frame(
        RIGCTL_ATTACH_V1.replace("auxiliary", "future_role").encode()
    )
    assert params is not None and params.controller_role == "future_role"
    result = decode_rigctl_attach_result(
        RIGCTL_ATTACH_ERROR_V1.replace("rigctl_busy", "future_code")
    )
    assert isinstance(result, RigctlAttachErrorV1) and result.code == "future_code"
    with pytest.raises(ValueError, match="invalid outbound"):
        params.to_attach_frame()
    with pytest.raises(ValueError, match="unsupported rigctl attach error"):
        result.encode()
    status = WebRtcTransportStatus.from_json(
        {"available": True, "enabled": True, "rigctl_versions": [99, 1]}
    )
    assert status.rigctl_versions == (99, 1)
    assert status.select_rigctl_version() == 1


class _DcRigctlClient:
    def __init__(self, peer: _DcControlClient) -> None:
        self.channel = peer._pc.createDataChannel("rigctl", ordered=True)  # noqa: SLF001
        self.frames: asyncio.Queue[str] = asyncio.Queue()
        self.channel.on("message", self.frames.put_nowait)

    async def attach(self, key: str, *, version: int = 1) -> object:
        await await_until(
            lambda: self.channel.readyState == "open", timeout=5, description="CAT open"
        )
        self.channel.send('{"type":"future_notice"}')
        self.channel.send(
            json.dumps(
                {**json.loads(RIGCTL_ATTACH_V1), "controller_key": key, "v": version}
            )
        )
        return decode_rigctl_attach_result(await self.reply())

    async def reply(self) -> str:
        return await asyncio.wait_for(self.frames.get(), 5)

    async def key(self) -> None:
        self.channel.send("T 1\n")
        assert await self.reply() == "RPRT 0\n"


@_webrtc
async def test_rigctl_whole_peer_loss_uses_controller_owner_disconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = await _stand_up_stack(tmp_path)
    peer = _DcControlClient()
    retired: list[str] = []

    async def record_cat_retirement(owner: str) -> None:
        # Isolate controller-session release from the CAT-only handback.
        retired.append(owner)

    monkeypatch.setattr(stack.rigctld, "_release_session_tx", record_cat_retirement)
    try:
        await peer.connect_via_http(stack.web.port, host_only=True)
        key = await _acquire_session(stack.web.port)
        await peer.attach(key)
        cat = _DcRigctlClient(peer)
        assert await cat.attach(key) == ChannelAttachedAck("rigctl")
        await cat.key()
        await await_until(lambda: stack.sim.ptt_on, timeout=5, description="fake keyed")
        owner = stack.composition.authority.tx_holder_nowait()
        assert owner is not None
        await peer.heartbeat()
        await peer._pc.close()  # noqa: SLF001 — lose SCTP and all its channels
        await await_until(
            lambda: not stack.sim.ptt_on, timeout=3, description="peer-loss RX"
        )
        assert not stack.web._controller.is_session_owner(owner)  # noqa: SLF001
        await await_until(
            lambda: retired == [owner], timeout=3, description="CAT retired"
        )
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
@pytest.mark.parametrize("source", ["cat", "control", "pending_unkey"])
async def test_rigctl_only_close_releases_cat_hold_and_keeps_primary(
    tmp_path: Path, source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = await _stand_up_stack(tmp_path)
    peer = _DcControlClient()
    try:
        await peer.connect_via_http(stack.web.port, host_only=True)
        key = await _acquire_session(stack.web.port)
        await peer.attach(key)
        cat = _DcRigctlClient(peer)
        assert await cat.attach(key) == ChannelAttachedAck("rigctl")
        if source == "control":
            assert (await peer.command("key", "ptt_on"))["ok"] is True
            cat.channel.send("f\r\n")
            assert await cat.reply() == "14074000\n"
        else:
            await cat.key()
        await await_until(lambda: stack.sim.ptt_on, timeout=5, description="fake keyed")
        if source == "pending_unkey":
            started = asyncio.Event()

            def hold_submission(
                on: bool, *_args: Any, **_kwargs: Any
            ) -> asyncio.Task[bool]:
                assert on is False
                started.set()
                return asyncio.create_task(asyncio.Event().wait())

            monkeypatch.setattr(
                stack.composition.authority, "start_ptt_submission", hold_submission
            )
            cat.channel.send("T 0\n")
            await asyncio.wait_for(started.wait(), 3)
        cat.channel.close()
        await await_until(
            lambda: _registered(stack, "rigctl") == 0,
            timeout=3,
            description="CAT retired",
        )
        assert stack.sim.ptt_on is (source == "control")
        await peer.heartbeat()
        if source == "control":
            assert (await peer.command("unkey", "ptt_off"))["ok"] is True
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
@pytest.mark.parametrize("bad_frame", [b"f\n", "f"])
async def test_rigctl_framing_closes_only_cat_and_releases_its_hold(
    tmp_path: Path, bad_frame: str | bytes
) -> None:
    stack = await _stand_up_stack(tmp_path)
    peer = _DcControlClient()
    try:
        await peer.connect_via_http(stack.web.port, host_only=True)
        key = await _acquire_session(stack.web.port)
        await peer.attach(key)
        cat = _DcRigctlClient(peer)
        assert await cat.attach(key) == ChannelAttachedAck("rigctl")
        await cat.key()
        await await_until(lambda: stack.sim.ptt_on, timeout=5, description="fake keyed")
        cat.channel.send(bad_frame)
        await _channel_closed(cat.channel, timeout=3)
        await await_until(
            lambda: not stack.sim.ptt_on, timeout=3, description="CAT-loss RX"
        )
        await peer.heartbeat()
        replacement = _DcRigctlClient(peer)
        assert await replacement.attach(key) == ChannelAttachedAck("rigctl")
    finally:
        await peer.close()
        await _tear_down_stack(stack)


class _BufferedCatChannel(_FakeChannel):
    bufferedAmount = 0
    bufferedAmountLowThreshold = 0
    track_buffered_amount = False

    def send(self, data: str | bytes) -> None:
        super().send(data)
        if self.track_buffered_amount:
            self.bufferedAmount += len(data.encode() if isinstance(data, str) else data)

    def drain(self) -> None:
        self.bufferedAmount = 0
        for listener in self._listeners.get("bufferedamountlow", []):
            listener()


@dataclass
class _CatLimitPath:
    clock: VirtualClock
    cat: _BufferedCatChannel
    control: _FakeChannel
    attach: str
    tx: Any
    owner: str
    server: RigctldServer
    commands: asyncio.Event
    admit_unkey: asyncio.Event
    reply: str = "14074000\n"

    async def key_cat(self) -> None:
        self.cat.deliver(self.attach)
        await self.clock.elapse(0)
        assert len(self.cat.sent) == 1
        assert json.loads(self.cat.sent[0]) == json.loads(RIGCTL_ATTACHED_V1)
        self.cat.deliver("T 1\n")
        await self.clock.elapse(0)
        assert self.cat.sent[1:] == ["RPRT 0\n"]
        assert self.tx.tx_holder_nowait() == self.owner

    async def key_control(self) -> None:
        self.control.deliver('{"type":"cmd","id":"key","name":"ptt_on"}')
        await self.clock.elapse(0)
        responses = [json.loads(frame) for frame in self.control.sent]
        assert any(frame.get("id") == "key" and frame["ok"] for frame in responses)
        assert self.tx.tx_holder_nowait() == self.owner

    async def assert_control_works(self) -> None:
        self.control.sent.clear()
        self.control.deliver('{"type":"ping"}')
        await self.clock.elapse(0)
        assert self.control.readyState == "open"
        assert {"type": "pong"} in [json.loads(frame) for frame in self.control.sent]

    async def assert_cat_retired(self) -> None:
        await self.clock.elapse(0)
        assert self.cat.readyState == "closed"
        assert not self.server._client_tasks  # noqa: SLF001

    async def assert_cat_handed_back(self) -> None:
        await self.assert_cat_retired()
        self.tx.owner_disconnect.assert_awaited_once_with(self.owner)
        assert self.tx.tx_holder_nowait() is None
        await self.assert_control_works()


@pytest.fixture
async def cat_limit_path(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock
) -> AsyncIterator[_CatLimitPath]:
    web = WebServer(config=WebConfig(webrtc_enabled=True, radio_model="IC-7610"))
    controller = web._controller  # noqa: SLF001
    monkeypatch.setattr(controller, "_clock", virtual_clock)
    radio = SimpleNamespace(
        connected=True, radio_ready=True, _controller_authority=controller
    )
    monkeypatch.setattr(web, "_radio", radio)
    tx = Mock(spec=ManagedTxAuthority)
    tx.tx_holder_nowait.return_value = None
    monkeypatch.setattr(web, "_managed_tx_authority", lambda: tx)
    monkeypatch.setattr(
        web, "current_managed_tx_document", AsyncMock(return_value=None)
    )

    async def disconnect(owner: str) -> ManagedTxOutcome:
        if tx.tx_holder_nowait() == owner:
            tx.tx_holder_nowait.return_value = None
        return ManagedTxOutcome.ACCEPTED

    tx.owner_disconnect.side_effect = disconnect

    admit_unkey = asyncio.Event()
    admit_unkey.set()

    async def submit(on: bool, owner: str) -> ManagedTxSubmission:
        if not on:
            await admit_unkey.wait()
        if not on and tx.tx_holder_nowait() != owner:
            outcome = ManagedTxOutcome.REJECTED
        else:
            tx.tx_holder_nowait.return_value = owner if on else None
            outcome = ManagedTxOutcome.ACCEPTED
        transition = ManagedTxTransition(ManagedTxState(), outcome)

        async def settle() -> tuple[ManagedTxTransition, None]:
            return transition, None

        return ManagedTxSubmission(transition, asyncio.create_task(settle()))

    def start_submission(
        on: bool, owner: str, **_kwargs: Any
    ) -> asyncio.Task[ManagedTxSubmission]:
        return asyncio.create_task(submit(on, owner))

    async def enqueue_control_key(
        *, submission: asyncio.Task[ManagedTxSubmission], **_kwargs: Any
    ) -> ManagedTxSubmission:
        return await submission

    tx.start_ptt_submission.side_effect = start_submission
    monkeypatch.setattr(web, "enqueue_managed_positive_tx", enqueue_control_key)

    async def execute(
        cmd: RigctldCommand,
        *,
        ptt_submission: asyncio.Task[ManagedTxSubmission] | None = None,
        **_kwargs: Any,
    ) -> RigctldResponse:
        if cmd.long_cmd == "set_ptt":
            assert ptt_submission is not None
            receipt = await ptt_submission
            if receipt.outcome is ManagedTxOutcome.REJECTED:
                return RigctldResponse(error=HamlibError.ERJCTED)
        return RigctldResponse()

    def format_response(
        cmd: RigctldCommand, response: RigctldResponse, session: Any
    ) -> bytes:
        if cmd.long_cmd == "get_freq":
            return path.reply.encode()
        return rigctld_protocol.format_response(cmd, response, session)

    server = RigctldServer(
        Mock(spec=IcomRadio),
        _protocol=SimpleNamespace(
            parse_line=rigctld_protocol.parse_line,
            format_response=format_response,
            format_error=rigctld_protocol.format_error,
        ),
        _handler=SimpleNamespace(
            execute=execute, release_session_tx=tx.owner_disconnect
        ),
        clock=virtual_clock,
    )
    queue = web.command_queue
    generation = object()
    queue.bind_connection_generation(lambda: generation)
    monkeypatch.setattr(server, "_managed_tx_authority", tx)
    monkeypatch.setattr(server, "_command_queue", queue)
    monkeypatch.setattr(server, "_server", Mock(spec=asyncio.Server))
    read_line = server._readline  # noqa: SLF001

    async def gated_readline(reader: asyncio.StreamReader) -> bytes | None:
        await path.commands.wait()
        return await read_line(reader)

    monkeypatch.setattr(server, "_readline", gated_readline)
    web.attach_rigctl_channel(server.open_channel)
    manager = web._webrtc_session_manager()  # noqa: SLF001
    pc = Mock(connectionState="connected", close=AsyncMock())
    peer = webrtc_session._Session(pc)  # noqa: SLF001
    manager._sessions["peer"] = peer  # noqa: SLF001
    key = controller.acquire("pro")["controller_key"]
    assert isinstance(key, str)
    owner = controller.credential(key).tx_owner
    assert owner is not None
    cat, control = _BufferedCatChannel("rigctl"), _FakeChannel("control")
    attach = RIGCTL_ATTACH_V1.replace('"KEY"', json.dumps(key))
    path = _CatLimitPath(
        virtual_clock,
        cat,
        control,
        attach,
        tx,
        owner,
        server,
        asyncio.Event(),
        admit_unkey,
    )
    path.commands.set()

    try:
        manager._dispatch_channel("peer", peer, control)  # type: ignore[arg-type]  # noqa: SLF001
        control.deliver(_attach_text("control", key))
        await path.assert_control_works()
        manager._dispatch_channel("peer", peer, cat)  # type: ignore[arg-type]  # noqa: SLF001
        yield path
    finally:
        cat.close()
        control.close()
        path.commands.set()
        path.admit_unkey.set()
        await manager.close_all()
        controller.revoke()
        await controller.settle()
        monkeypatch.setattr(server, "_server", None)
        assert not server._client_tasks  # noqa: SLF001


async def test_rigctl_redundant_unkey_preserves_later_control_key(
    cat_limit_path: _CatLimitPath,
) -> None:
    path = cat_limit_path
    await path.key_cat()
    path.cat.deliver("T 0\n")
    await path.clock.elapse(0)
    assert path.cat.sent[-1] == "RPRT 0\n"
    assert path.tx.tx_holder_nowait() is None
    path.cat.deliver("T 0\n")
    await path.clock.elapse(0)
    assert path.cat.sent[-1] == "RPRT -9\n"

    await path.key_control()
    path.cat.close()
    await path.assert_cat_retired()
    path.tx.owner_disconnect.assert_not_awaited()
    assert path.tx.tx_holder_nowait() == path.owner
    await path.assert_control_works()


async def test_rigctl_quit_waits_for_unkey_admission(
    cat_limit_path: _CatLimitPath,
) -> None:
    path = cat_limit_path
    await path.key_cat()
    path.admit_unkey.clear()
    path.cat.deliver("T 0\nq\n")
    await path.clock.elapse(0)
    assert path.cat.readyState == "open"
    assert path.cat.sent[1:] == ["RPRT 0\n"]
    assert path.tx.tx_holder_nowait() == path.owner

    path.admit_unkey.set()
    await path.assert_cat_retired()
    assert path.cat.sent[1:] == ["RPRT 0\n", "RPRT 0\n"]
    assert path.tx.tx_holder_nowait() is None
    path.tx.owner_disconnect.assert_not_awaited()
    await path.assert_control_works()


async def test_rigctl_oversized_valid_batch_closes_cat(
    cat_limit_path: _CatLimitPath,
) -> None:
    path = cat_limit_path
    await path.key_cat()
    line = "f" + " " * 510 + "\n"
    lines_at_limit = RIGCTL_MAX_FRAME_BYTES // len(line)
    path.cat.deliver(line * lines_at_limit)
    await path.clock.elapse(0)
    assert path.cat.sent[2:] == [path.reply] * lines_at_limit
    path.cat.deliver(line * (lines_at_limit + 1))
    await path.assert_cat_handed_back()


@pytest.mark.parametrize("queued", [True, False], ids=["queued", "ack-blocked"])
async def test_rigctl_rejects_cat_before_attach_result(
    cat_limit_path: _CatLimitPath, queued: bool
) -> None:
    path = cat_limit_path
    await path.key_control()
    if not queued:
        path.cat.bufferedAmount = RIGCTL_MAX_FRAME_BYTES + 1
    path.cat.deliver(path.attach)
    if not queued:
        await path.clock.elapse(0)
        assert path.cat.sent == []
        assert path.cat.bufferedAmountLowThreshold == RIGCTL_MAX_FRAME_BYTES
    path.cat.deliver("T 0\n")
    await path.assert_cat_retired()
    assert [decode_rigctl_attach_result(frame) for frame in path.cat.sent] == (
        [RigctlAttachErrorV1("invalid_attach")] if queued else []
    )
    path.tx.owner_disconnect.assert_not_awaited()
    assert path.tx.tx_holder_nowait() == path.owner
    await path.assert_control_works()


async def test_rigctl_served_frame_queue_overflow(
    cat_limit_path: _CatLimitPath,
) -> None:
    path = cat_limit_path
    await path.key_cat()
    for _ in range(webrtc.MAX_FRAMES_BEFORE_SERVE):
        path.cat.deliver("f\n")
    assert path.cat.readyState == "open"
    path.cat.deliver("f\n")
    # The receive queue must close synchronously, before the command pump runs.
    assert path.cat.readyState == "closed"
    await path.assert_cat_handed_back()


async def test_rigctl_command_queue_waits_then_resumes(
    cat_limit_path: _CatLimitPath, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = cat_limit_path
    await path.key_cat()
    path.commands.clear()
    readline = path.server._readline  # noqa: SLF001

    async def read_one(reader: asyncio.StreamReader) -> bytes | None:
        raw = await readline(reader)
        path.commands.clear()
        return raw

    monkeypatch.setattr(path.server, "_readline", read_one)
    # Wake the already-parked read once, then park it before the test batch.
    path.cat.deliver("f\n")
    await path.clock.elapse(0)
    assert path.cat.sent[-1] == path.reply
    path.cat.deliver("f\n" * rigctl._MAX_QUEUED_LINES)  # noqa: SLF001
    await path.clock.elapse(0)
    assert path.cat.readyState == "open"
    path.cat.deliver("f\n" * rigctl._MAX_QUEUED_LINES)  # noqa: SLF001
    await path.clock.elapse(0)
    assert path.cat.readyState == "open"
    assert path.cat.sent[3:] == []
    assert path.tx.tx_holder_nowait() == path.owner
    # Retire each reply before admitting another line to the bounded scheduler.
    for received in range(1, 2 * rigctl._MAX_QUEUED_LINES + 1):  # noqa: SLF001
        path.commands.set()
        await path.clock.elapse(0)
        assert len(path.cat.sent[3:]) == received
    assert path.cat.sent[3:] == [path.reply] * (2 * rigctl._MAX_QUEUED_LINES)  # noqa: SLF001
    assert path.cat.readyState == "open"
    path.cat.close()
    await path.assert_cat_handed_back()


async def test_rigctl_reply_chunks_preserve_utf8(cat_limit_path: _CatLimitPath) -> None:
    path = cat_limit_path
    await path.key_cat()
    first_chunk = "x" * (RIGCTL_MAX_FRAME_BYTES - 1)
    path.reply = first_chunk + "é\n"
    path.cat.track_buffered_amount = True
    path.cat.bufferedAmount = RIGCTL_MAX_FRAME_BYTES
    path.cat.deliver("f\n")
    await path.clock.elapse(0)
    assert path.cat.readyState == "open"
    assert path.cat.sent[2:] == [first_chunk]
    path.cat.drain()
    await path.clock.elapse(0)
    assert path.cat.sent[2:] == [first_chunk, "é\n"]
    path.cat.close()
    await path.assert_cat_handed_back()


async def test_rigctl_reply_buffer_overflow(cat_limit_path: _CatLimitPath) -> None:
    path = cat_limit_path
    await path.key_cat()
    path.reply = "x" * (4 * RIGCTL_MAX_FRAME_BYTES) + "\n"
    path.cat.deliver("f\n")
    await path.assert_cat_handed_back()
    assert path.cat.sent[2:] == []


async def test_rigctl_stalled_write_times_out(cat_limit_path: _CatLimitPath) -> None:
    path = cat_limit_path
    await path.key_cat()
    path.cat.bufferedAmount = RIGCTL_MAX_FRAME_BYTES + 1
    path.cat.deliver("f\n")
    await path.clock.elapse(0.5)
    assert path.cat.readyState == "open"
    assert path.cat.sent[2:] == []
    assert path.tx.tx_holder_nowait() == path.owner
    await path.assert_control_works()
    await path.clock.elapse(0.5)
    await path.assert_cat_handed_back()
    assert path.cat.sent[2:] == []


@_webrtc
async def test_rigctl_capability_and_attach_refusals(tmp_path: Path) -> None:
    stack = await _stand_up_stack(tmp_path)
    peer, other = _DcControlClient(), _DcControlClient()
    try:
        status, _, body = await _http_get(
            "127.0.0.1", stack.web.port, "/api/v1/capabilities"
        )
        assert status == 200
        assert json.loads(body)["webrtc"]["rigctl_versions"] == [1]
        await peer.connect_via_http(stack.web.port, host_only=True)
        await other.connect_via_http(stack.web.port, host_only=True)
        key = await _acquire_session(stack.web.port)
        await peer.attach(key)
        assert await _DcRigctlClient(other).attach(key) == RigctlAttachErrorV1(
            "controller_primary_required"
        )
        assert await _DcRigctlClient(peer).attach(
            key, version=99
        ) == RigctlAttachErrorV1("unsupported_version")
        cat = _DcRigctlClient(peer)
        assert await cat.attach(key) == ChannelAttachedAck("rigctl")
        assert await _DcRigctlClient(peer).attach(key) == RigctlAttachErrorV1(
            "rigctl_busy"
        )
        cat.channel.send("f\n")
        assert await cat.reply() == "14074000\n"
        assert not stack.sim.ptt_transitions
    finally:
        await other.close()
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
async def test_attach_timeout_closes_only_that_channel(tmp_path: Path) -> None:
    """A scope channel that never attaches is closed by the server's 5 s
    attach timeout — and ONLY that channel: control keeps answering on the
    same peer, which stays ``connected``."""
    stack = await _stand_up_stack(tmp_path)
    peer = _ChannelSetPeer()
    try:
        await peer.connect(stack.web, ("control", "scope"))
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
    peer = _ChannelSetPeer()
    try:
        await peer.connect(stack.web, ("control", "audio"))
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
    peer = _ChannelSetPeer()
    try:
        await peer.connect(stack.web, ("control", "scope", "audio", "api"))
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
    peer = _ChannelSetPeer()
    try:
        # MAX_PER_IP_PER_CHANNEL is 9: the 10th scope pair's attach evicts
        # the first — all on one peer, one negotiation. Pairs open one at a
        # time (a client has one pair of a kind in flight): each pair opens,
        # attaches and is acked before the next is created, so the server
        # pairs, registers and evicts in this order.
        await peer.connect(stack.web, ("control",))
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        scope_channels: list[Any] = []
        text_channels: list[Any] = []
        for _ in range(10):
            pair = peer.open_kind("scope")
            await await_until(
                lambda: all(c.readyState == "open" for c in pair.values()) or None,
                timeout=15.0,
                description="scope pair open",
            )
            scope_channels.append(pair["scope"])
            text_channels.append(pair["scope-text"])
            pair["scope-text"].send(_attach_text("scope", key))
            assert await await_until(
                lambda: peer.take("scope"), timeout=10.0, description="scope ack"
            ) == {"type": "attached", "v": 1, "channel": "scope"}
        await _channel_closed(scope_channels[0])
        await _channel_closed(text_channels[0])
        for channel in scope_channels[1:] + text_channels[1:]:
            assert channel.readyState == "open"
        await _control_alive(peer)
        assert peer._pc.connectionState == "connected"  # noqa: SLF001
        # The surviving scope channel still serves.
        text_channels[-1].send(json.dumps({"type": "ping"}))
        assert await _frame(peer, "scope", "pong") == {"type": "pong"}
    finally:
        await peer.close()
        await _tear_down_stack(stack)


# ---------------------------------------------------------------------------
# K5: attach order (MOR-272). Control first; every attach rides a reliable
# channel and is never resent; a scope/audio attach that arrives before
# control is held (no ack, no close) until control attaches, within the
# attach bound.
# ---------------------------------------------------------------------------


@_webrtc
@pytest.mark.realtime(reason="lets a held attach sit on the server for real time")
async def test_split_attach_before_control_is_held_then_attaches(
    tmp_path: Path,
) -> None:
    """A scope/audio attach that outruns control gets no ack and no close;
    once control attaches, the SAME (single, never resent) attach lands:
    the ack arrives and the channel serves."""
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _ChannelSetPeer()
    try:
        await peer.connect(stack.web, ("control", "scope", "audio"))
        key = await _acquire_session(stack.web.port)
        for label in ("scope", "audio"):
            peer.send_text(label, _attach_text(label, key))  # once, before control
        await asyncio.sleep(0.5)  # ten re-check periods with no control
        for label in ("scope", "audio"):
            assert peer.take(label) is None, f"{label} acked before control"
            assert peer._channels[label].readyState == "open"  # noqa: SLF001
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        for label in ("scope", "audio"):
            ack = await await_until(
                lambda bound=label: peer.take(bound),
                timeout=10.0,
                description=f"held {label} attach acked",
            )
            assert ack == _golden_ack(label)
        peer.send_text("scope", json.dumps({"type": "ping"}))
        assert await _frame(peer, "scope", "pong") == {"type": "pong"}
        await _control_alive(peer)
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
@pytest.mark.realtime(reason="waits for the real (patched 1 s) attach bound")
async def test_split_attach_without_control_closes_at_the_attach_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A held attach never extends the attach bound: with no control attach
    the scope pair is closed once the bound (patched to 1 s) runs out."""
    from rigplane.web.transport import webrtc_session

    monkeypatch.setattr(webrtc_session, "_ATTACH_TIMEOUT_S", 1.0)
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _ChannelSetPeer()
    try:
        await peer.connect(stack.web, ("control", "scope"))
        key = await _acquire_session(stack.web.port)
        loop = asyncio.get_running_loop()
        started = loop.time()
        peer.send_text("scope", _attach_text("scope", key))  # once
        await _channel_closed(peer._channels["scope-text"])  # noqa: SLF001
        await _channel_closed(peer._channels["scope"])  # noqa: SLF001
        assert loop.time() - started <= 1.0 + 2.0
        assert peer.take("scope") is None  # never acked
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
@pytest.mark.realtime(reason="waits for the real (patched 1 s) attach bound")
async def test_split_half_without_its_pair_is_closed_at_the_attach_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lone ``scope-text`` channel (its lossy partner never opened) is
    closed by the attach bound and serves nothing; control stays up."""
    from rigplane.web.transport import webrtc_session

    monkeypatch.setattr(webrtc_session, "_ATTACH_TIMEOUT_S", 1.0)
    stack = await _stand_up_stack(tmp_path)
    peer = _ChannelSetPeer()
    try:
        await peer.connect(stack.web, ("control",), lone_labels=("scope-text",))
        lone = peer._channels["scope-text"]  # noqa: SLF001
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        await _channel_closed(lone, timeout=1.0 + 5.0)
        assert _registered(stack, "scope") == 0
        await _control_alive(peer)
    finally:
        await peer.close()
        await _tear_down_stack(stack)


@_webrtc
@pytest.mark.realtime(reason="leaves the attach waiting for its absent partner")
async def test_split_attach_on_the_text_half_before_its_lossy_partner_opens(
    tmp_path: Path,
) -> None:
    """The browser sends its single attach as soon as the ``*-text`` half
    opens, possibly before the lossy half exists: the answerer keeps that
    frame, pairs the halves when the partner arrives, and the pair is
    acknowledged and serves normally."""
    stack = await _stand_up_stack(tmp_path, audio=True)
    peer = _ChannelSetPeer()
    try:
        await peer.connect(stack.web, ("control",))
        key = await _acquire_session(stack.web.port)
        peer.send_text("control", _attach_text("control", key))
        assert (await _frame(peer, "control", "hello"))["type"] == "hello"
        peer.received["audio"] = []
        text = peer._pc.createDataChannel("audio-text", ordered=True)  # noqa: SLF001

        @text.on("message")  # type: ignore[misc, no-untyped-call]
        def _on_text(message: Any) -> None:
            peer.received["audio"].append(message)

        await await_until(
            lambda: text.readyState == "open" or None,
            timeout=15.0,
            description="text half open",
        )
        text.send(_attach_text("audio", key))  # the one attach, partner absent
        await asyncio.sleep(0.3)
        binary = peer._pc.createDataChannel(  # noqa: SLF001
            "audio", ordered=False, maxRetransmits=0
        )
        await await_until(
            lambda: binary.readyState == "open" or None,
            timeout=15.0,
            description="lossy half open",
        )
        ack = await await_until(
            lambda: peer.take("audio"), timeout=10.0, description="audio ack"
        )
        assert ack == _golden_ack("audio")
        text.send(json.dumps({"type": "audio_start", "direction": "tx"}))
        await _frame(peer, "audio", "audio_tx_format")
        await _control_alive(peer)
    finally:
        await peer.close()
        await _tear_down_stack(stack)
