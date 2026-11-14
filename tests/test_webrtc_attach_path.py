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
from rigplane.web.protocol import encode_json
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


async def _stand_up_stack(tmp_path: Path, *, tot_seconds: float = 30.0) -> _Stack:
    """Stand up web + rigctld + managed TX over the LAN fake (hold loss)."""
    config_path = tmp_path / "managed-tx.json"
    ManagedTxTotConfigStore(config_path).set_timeout_seconds(tot_seconds)
    sim = ic7610_lan_fake(
        single_owner=True,
        stateful_ptt=True,
        ptt_on_session_loss="hold",
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

    async def connect(self, web: WebServer) -> None:
        from aiortc import RTCPeerConnection, RTCSessionDescription

        self._pc = RTCPeerConnection()
        self._channel = self._pc.createDataChannel("control", ordered=True)

        @self._channel.on("message")  # type: ignore[misc, no-untyped-call]
        def _on_message(message: Any) -> None:
            self._received.append(message)

        @self._channel.on("close")  # type: ignore[misc, no-untyped-call]
        def _on_close() -> None:
            self._closed.set()

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


def test_attach_frame_parser_defaults_key_and_role() -> None:
    params = server_module.ClientChannelParams.from_attach_frame(
        json.dumps({"type": "attach", "state_delta": 1}).encode()
    )
    assert params.controller_key is None
    assert params.controller_role == "primary"
    assert params.state_delta == 1


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"[1, 2]",
        b'{"type": "hello"}',
        b'{"type": "attach", "state_delta": 3}',
        b'{"type": "attach", "state_delta": "2"}',
        b'{"type": "attach", "role": 7}',
        b'{"type": "attach", "controller_key": 7}',
    ],
)
def test_attach_frame_parser_rejects_bad_input(payload: bytes) -> None:
    with pytest.raises(ValueError):
        server_module.ClientChannelParams.from_attach_frame(payload)


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
        hello = await dc.attach(None)
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
async def test_remote_mode_admissible_after_webrtc_offer(tmp_path: Path) -> None:
    """Making a WebRTC offer must not bar remote controller mode."""
    stack = await _stand_up_stack(tmp_path)
    dc = _DcControlClient()
    try:
        await dc.connect(stack.web)
        assert stack.web._webrtc_sessions is not None  # noqa: SLF001
        await stack.web._controller.set_mode("remote")  # noqa: SLF001
        assert stack.web._controller.mode == "remote"  # noqa: SLF001
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
