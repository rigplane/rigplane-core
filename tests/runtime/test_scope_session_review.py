"""R5 review regressions: real Core RX, SDK and local WebSocket seams."""

import asyncio
from contextlib import asynccontextmanager

import aiohttp
import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.exceptions import CommandError, TimeoutError
from rigplane.radio import IcomRadio
from rigplane.scope.models import FollowView, Subscription
from rigplane.testing.timing import await_until
from rigplane.web.protocol import encode_scope_subscription
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom.scope_control import ScopeControlSimulator

pytestmark = [
    pytest.mark.realtime(reason="real local simulator and WebSocket seams"),
    pytest.mark.timeout(30),
]


@pytest.fixture
async def sdk():
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
    try:
        yield radio, sim
    finally:
        await radio.disconnect()
        await sim.stop()


@asynccontextmanager
async def web(radio):
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    await server.start()
    base = f"http://127.0.0.1:{server._server.sockets[0].getsockname()[1]}"
    try:
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
                yield server, client, base, protocols, control
    finally:
        await server.stop()


async def grant(ws, receiver=0, fps=20):
    await ws.send_str(
        encode_scope_subscription(
            Subscription("radio", receiver, FollowView("vfo", 100000), 100, fps)
        )
    )
    while True:
        message = await ws.receive(timeout=5)
        if message.type is aiohttp.WSMsgType.TEXT:
            assert '"error"' not in message.data
            return


def ptt_reply(radio, on):
    radio._update_state_cache_from_frame(
        parse_civ_frame(
            build_civ_frame(
                CONTROLLER_ADDR, radio._radio_addr, 0x1C, sub=0, data=bytes([on])
            )
        )
    )


async def test_sdk_only_tx_off_drives_deferred_demand(sdk):
    radio, sim = sdk
    ptt_reply(radio, True)
    await radio.enable_scope(policy="fast")
    assert sim._scope_11 == 0
    ptt_reply(radio, False)
    assert await await_until(lambda: sim._scope_11 == 1)


async def test_sdk_only_reconnect_reapplies_demand(sdk):
    radio, sim = sdk
    await radio.enable_scope(policy="fast")
    await radio.disconnect()
    await radio.connect()
    assert await await_until(lambda: sim._scope_11 == 1)


async def test_sub_view_real_dispatch_preserves_main_sdk_selection(sdk):
    radio, sim = sdk
    sim._scope_12 = sim._scope_13 = 0
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await grant(ws, receiver=1)
            assert (await ws.receive(timeout=5)).type is aiohttp.WSMsgType.BINARY
            async with client.ws_connect(
                base + "/api/v1/scope", protocols=protocols
            ) as main:
                await grant(main)
                assert (await main.receive(timeout=5)).type is aiohttp.WSMsgType.BINARY
                assert (await ws.receive(timeout=5)).type is aiohttp.WSMsgType.BINARY
            assert radio._scope_controls().receiver == 0
            await radio.set_scope_span(4)
            assert sim.views[0][1] == 4
            assert sim.views[1][1] == 5
    assert sim._scope_12 == sim._scope_13 == 0


async def test_sdk_frames_stay_visible_when_web_pauses(sdk):
    radio, _ = sdk
    await radio.enable_scope(policy="verify")
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await grant(ws)
            await grant(ws, fps=0)
            arrivals = asyncio.Queue()
            radio._scope_stream_callbacks.append(arrivals.put_nowait)
            try:
                for _ in range(3):
                    await asyncio.wait_for(arrivals.get(), 5)
                assert (
                    radio.scope_session()
                    .health(civ_flowing=True, silence_seconds=0.05)
                    .hidden
                    is False
                )
            finally:
                radio._scope_stream_callbacks.remove(arrivals.put_nowait)


async def test_control_ws_reapplies_span_after_real_front_panel_readback(sdk):
    radio, sim = sdk
    await radio.enable_scope(policy="fast")
    await radio.set_scope_span(3)
    sim.views[0] = (0, 4, 1)
    assert await radio.get_scope_span() == 4
    async with web(radio) as (_, _, _, _, ws):
        await ws.send_json(
            {
                "type": "cmd",
                "id": "span",
                "name": "set_scope_span",
                "params": {"span": 3},
            }
        )
        assert await await_until(lambda: sim.views[0][1] == 3)


async def test_uncertain_write_cannot_suppress_the_previous_acknowledged_value(
    sdk, monkeypatch
):
    radio, sim = sdk
    await radio.set_scope_mode(1)
    send = radio._send_civ_raw

    async def lost_ack(payload, **kwargs):
        response = await send(payload, **kwargs)
        frame = parse_civ_frame(payload)
        if frame.command == 0x27 and frame.sub == 0x14 and frame.data == b"\x00\x02":
            raise CommandError("lost ACK")
        return response

    monkeypatch.setattr(radio, "_send_civ_raw", lost_ack)
    with pytest.raises(CommandError, match="lost ACK"):
        await radio.set_scope_mode(2)
    await radio.set_scope_mode(1)
    assert sim.views[0][0] == 1


async def test_verify_requires_new_activity_after_a_previous_stream(sdk):
    radio, sim = sdk
    await radio.enable_scope(policy="verify")
    sim._scope_stream = False
    with pytest.raises(TimeoutError, match="verification"):
        await radio.enable_scope(policy="verify", timeout=0.1)


async def test_sub_fixed_slot_capture_preserves_main_selected_edge(sdk):
    from rigplane.scope.models import View

    radio, sim = sdk
    radio._radio_state.sub.freq = 14100000
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await ws.send_str(
                encode_scope_subscription(
                    Subscription("radio", 1, View(14100000, 14120000), 100, 20)
                )
            )
            message = await ws.receive(timeout=5)
            assert message.type is aiohttp.WSMsgType.TEXT
            assert '"error"' not in message.data
            assert sim.views[1][2] == 2
            assert radio._scope_controls().receiver == 0
            assert radio._scope_controls().edge == sim.views[0][2] == 1


async def test_standalone_sdk_span_setter_retires_after_ack(sdk, monkeypatch):
    radio, sim = sdk
    async with web(radio) as (server, _, _, _, ws):
        server._scope_health_interval = 0.05
        await radio.set_scope_span(2)
        assert await await_until(lambda: sim.views[0][1] == 2)
        sim.views[0] = (0, 4, 1)
        assert await radio.get_scope_span() == 4
        reconciled = asyncio.Event()
        reconcile = radio.scope_session().reconcile

        async def tick():
            await reconcile()
            reconciled.set()

        monkeypatch.setattr(radio.scope_session(), "reconcile", tick)
        monitor = asyncio.create_task(server._scope_health_monitor())
        try:
            await asyncio.wait_for(reconciled.wait(), 5)
            assert sim.views[0][1] == 4
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)


@pytest.mark.parametrize("defer", ["readiness", "tx"])
@pytest.mark.parametrize("setting", ["mode", "span", "edge"])
async def test_invalid_deferred_setting_cannot_poison_later_work(sdk, defer, setting):
    radio, sim = sdk
    session = radio.scope_session()
    await session.set_inputs(ready=defer != "readiness", tx=defer == "tx")
    with pytest.raises(ValueError):
        await session.configure(**{setting: 99})
    await session.configure(span=2)
    await session.set_inputs(ready=True, tx=False)
    assert sim.views[0][1] == 2


async def test_panel_output_readbacks_allow_sdk_reenable(sdk):
    radio, sim = sdk
    await radio.enable_scope(policy="verify")
    sim._scope_10 = sim._scope_11 = 0
    assert await radio.get_scope_session_state() == (False, False)
    assert not radio.scope_session().enabled
    await radio.enable_scope(policy="verify")
    assert sim._scope_10 == sim._scope_11 == 1


async def test_successive_fixed_sessions_capture_fresh_panel_slot(sdk):
    from rigplane.core.types import ScopeFixedEdge
    from rigplane.scope.models import View

    radio, sim = sdk
    radio._radio_state.main.freq = 14100000
    requested = ScopeFixedEdge(6, 1, 14100000, 14120000)
    changed = ScopeFixedEdge(6, 1, 14000000, 14300000)
    sim.fixed[b"\x06\x01"] = requested
    async with web(radio) as (_, client, base, protocols, _):
        for original in (requested, changed):
            sim.fixed[b"\x06\x01"] = original
            async with client.ws_connect(
                base + "/api/v1/scope", protocols=protocols
            ) as ws:
                await ws.send_str(
                    encode_scope_subscription(
                        Subscription("radio", 0, View(14100000, 14120000), 100, 20)
                    )
                )
                message = await ws.receive(timeout=5)
                assert message.type is aiohttp.WSMsgType.TEXT
                assert '"error"' not in message.data
                assert sim.fixed[b"\x06\x01"] == requested
            assert await await_until(lambda: not radio.scope_session().active)
            assert sim.fixed[b"\x06\x01"] == original
