"""MOR-4713: GET /api/v1/state scope span must track UI span steps at once.

After a span change driven the supported way (the scope subscription
view, the same path the UI +/- buttons use), the ``scopeControls.span``
leaf of ``GET /api/v1/state`` must carry the new preset index within one
update -- not stay one step behind until the next change.
"""

import asyncio
import time

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from pydantic import ValidationError

from rigplane.core.radio_state import RadioState
from rigplane.radio import IcomRadio
from rigplane.scope import ScopeFrame
from rigplane.scope.models import FollowView, Subscription
from rigplane.web.protocol import (
    decode_scope_granted,
    encode_scope_subscription,
)
from rigplane.web.runtime_helpers import build_public_state_payload
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.state_schema import ServerStatePublic
from rigplane_testkit.icom.scope_control import ScopeWindowSimulator

pytestmark = [
    pytest.mark.realtime(reason="real local simulator and WebSocket seams"),
    pytest.mark.timeout(60),
]


async def _state_span(client: aiohttp.ClientSession, base: str) -> int | None:
    async with client.get(base + "/api/v1/state") as response:
        assert response.status == 200
        payload = await response.json()
    return ServerStatePublic.model_validate(payload, strict=True).scopeControls.span


async def _wait_state_span(
    client: aiohttp.ClientSession, base: str, expected: int, timeout: float
) -> int | None:
    deadline = time.monotonic() + timeout
    last: int | None = None
    while time.monotonic() < deadline:
        last = await _state_span(client, base)
        if last == expected:
            return last
        await asyncio.sleep(0.05)
    return last


async def test_state_span_rejects_float_on_the_wire() -> None:
    payload = build_public_state_payload(
        RadioState(), radio=None, revision=1, receiver_count=2
    )
    payload["scopeControls"]["span"] = 4.0

    async def state(_request: web.Request) -> web.Response:
        return web.json_response(payload)

    app = web.Application()
    app.router.add_get("/api/v1/state", state)
    async with TestServer(app) as server, aiohttp.ClientSession() as client:
        with pytest.raises(ValidationError, match=r"scopeControls\.span"):
            await _state_span(client, str(server.make_url("/").origin()))


async def test_state_span_tracks_subscription_span_steps() -> None:
    expected_width_hz = 0
    applied_waveform = asyncio.Event()

    def observe_waveform(frame: ScopeFrame) -> None:
        if (
            frame.receiver == 0
            and frame.mode == 0
            and not frame.out_of_range
            and frame.end_freq_hz - frame.start_freq_hz == expected_width_hz
        ):
            applied_waveform.set()

    sim = ScopeWindowSimulator(scope_stream=True, dual_receiver=True)
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    await radio.connect()
    radio._scope_stream_callbacks.append(observe_waveform)
    sim.set_frequency(14286000)
    radio._radio_state.main.freq = 14286000
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    await server.start()
    try:
        port = server._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        async with aiohttp.ClientSession() as client:
            response = await client.post(
                base + "/api/v1/controller", json={"kind": "browser"}
            )
            key = (await response.json())["controller_key"]
            protocols = (f"rigplane-controller-v1.{key}",)
            async with client.ws_connect(
                base + "/api/v1/ws", protocols=protocols
            ) as control:
                await control.send_json({"type": "subscribe", "streams": ["state"]})
                async with client.ws_connect(
                    base + "/api/v1/scope", protocols=protocols
                ) as ws:
                    for expected_width_hz in (200000, 100000, 50000):
                        applied_waveform.clear()
                        await ws.send_str(
                            encode_scope_subscription(
                                Subscription(
                                    "radio",
                                    0,
                                    FollowView("vfo", expected_width_hz),
                                    100,
                                    20,
                                )
                            )
                        )
                        async with asyncio.timeout(10):
                            while True:
                                message = await ws.receive()
                                assert message.type is not aiohttp.WSMsgType.ERROR
                                if message.type is aiohttp.WSMsgType.TEXT:
                                    decode_scope_granted(message.data)
                                    break
                        async with asyncio.timeout(10):
                            while True:
                                message = await ws.receive()
                                if message.type is aiohttp.WSMsgType.BINARY:
                                    break
                            await applied_waveform.wait()
                        expected = sim.profile.scope_span_view_widths_hz.index(
                            expected_width_hz
                        )
                        assert sim.views[0][1] == expected, (
                            f"sim did not apply width {expected_width_hz}: {sim.views[0]}"
                        )
                        seen = await _wait_state_span(client, base, expected, 15)
                        assert seen == expected, (
                            f"state span stayed {seen}, want {expected} "
                            f"after subscribing width {expected_width_hz}"
                        )
    finally:
        await server.stop()
        await radio.disconnect()
        await sim.stop()
