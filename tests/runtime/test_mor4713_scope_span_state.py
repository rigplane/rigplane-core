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

from rigplane.radio import IcomRadio
from rigplane.scope.models import FollowView, Subscription
from rigplane.web.protocol import (
    decode_scope_granted,
    encode_scope_subscription,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom.scope_control import ScopeControlSimulator

pytestmark = [
    pytest.mark.realtime(reason="real local simulator and WebSocket seams"),
    pytest.mark.timeout(60),
]

# IC-7610 half-width presets: [2500, 5000, 10000, 25000, 50000, 100000,
# 250000, 500000]. A follow width of 2x the preset drives that preset.
WIDTH_TO_INDEX = {200000: 5, 100000: 4, 50000: 3}


async def _state_span(client: aiohttp.ClientSession, base: str) -> int | None:
    async with client.get(base + "/api/v1/state") as response:
        assert response.status == 200
        payload = await response.json()
    return payload.get("scopeControls", {}).get("span")


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


async def test_state_span_tracks_subscription_span_steps() -> None:
    sim = ScopeControlSimulator(scope_stream=True, dual_receiver=True)
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    await radio.connect()
    # The scope_stream sim replays a fixed IC-7610 recording around
    # 14286000; keep the VFO there so polls keep reporting it (same
    # recipe as test_control_ws_refuses_legacy_span_preserving_subscription).
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
                    for width_hz in (200000, 100000, 50000):
                        await ws.send_str(
                            encode_scope_subscription(
                                Subscription(
                                    "radio", 0, FollowView("vfo", width_hz), 100, 20
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
                        expected = WIDTH_TO_INDEX[width_hz]
                        assert sim.views[0][1] == expected, (
                            f"sim did not apply width {width_hz}: {sim.views[0]}"
                        )
                        seen = await _wait_state_span(client, base, expected, 15)
                        assert seen == expected, (
                            f"state span stayed {seen}, want {expected} "
                            f"after subscribing width {width_hz}"
                        )
    finally:
        await server.stop()
        await radio.disconnect()
        await sim.stop()
