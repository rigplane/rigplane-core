import pytest


@pytest.mark.realtime(reason="local simulator and real server websocket integration")
@pytest.mark.timeout(30)
async def test_server_simulator_pause_resume_release_counts_scope_writes(monkeypatch):
    import aiohttp
    from rigplane.radio import IcomRadio
    from rigplane.commands import parse_civ_frame
    from rigplane.scope.models import Subscription, View
    from rigplane.testing.timing import await_until
    from rigplane.web.protocol import encode_scope_subscription
    from rigplane.web.server import WebConfig, WebServer
    from _scope_simulator import ScopeControlSimulator
    from test_web_server import _addr

    sim = ScopeControlSimulator(scope_stream=True)
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    await radio.connect()
    writes = []
    send = radio._send_civ_raw

    async def record(payload, **kwargs):
        frame = parse_civ_frame(payload)
        if frame.command == 0x27 and frame.sub in (0x10, 0x11) and frame.data:
            writes.append((frame.sub, frame.data))
        return await send(payload, **kwargs)

    monkeypatch.setattr(radio, "_send_civ_raw", record)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    await server.start()
    host, port = _addr(server)
    try:
        async with aiohttp.ClientSession() as client:
            async with client.ws_connect(f"http://{host}:{port}/api/v1/scope") as ws:

                async def subscribe(fps):
                    await ws.send_str(
                        encode_scope_subscription(
                            Subscription(
                                "radio", 0, View(14_090_000, 14_110_000), 100, fps
                            )
                        )
                    )
                    while True:
                        message = await ws.receive(timeout=5)
                        if message.type is aiohttp.WSMsgType.TEXT:
                            assert '"type":"error"' not in message.data
                            if '"granted"' in message.data:
                                return

                await subscribe(20)
                await await_until(lambda: writes.count((0x11, b"\x01")) == 1)
                initial = list(writes)
                await subscribe(0)
                assert writes == initial + [(0x11, b"\x00")]
                await subscribe(0)
                assert writes == initial + [(0x11, b"\x00")]
                await subscribe(20)
                assert writes == initial + [(0x11, b"\x00"), (0x11, b"\x01")]
                await ws.send_json(
                    {"type": "unsubscribe", "sourceId": "radio", "receiver": 0}
                )
                assert await await_until(lambda: not radio.scope_session().active)
                assert writes.count((0x10, b"\x01")) == 1
                assert writes.count((0x10, b"\x00")) == 1
    finally:
        await server.stop()
        await radio.disconnect()
        await sim.stop()


@pytest.mark.parametrize("transport", ["lan", "serial"])
async def test_real_scope_writer_emits_one_enable_per_transport(transport, monkeypatch):
    from rigplane.radio import IcomRadio
    from rigplane.backends.ic7300 import Ic7300SerialRadio
    from rigplane.commands import parse_civ_frame, build_civ_frame, CONTROLLER_ADDR

    radio = (
        IcomRadio("127.0.0.1", model="IC-7300")
        if transport == "lan"
        else Ic7300SerialRadio(device="/dev/fake-scope", baudrate=115200)
    )
    radio._connected = True
    writes = []

    async def send(payload, **kwargs):
        frame = parse_civ_frame(payload)
        if frame.data:
            writes.append((frame.command, frame.sub, frame.data))
            radio._scope_activity_event.set()
        return parse_civ_frame(
            build_civ_frame(CONTROLLER_ADDR, radio._radio_addr, 0xFB)
        )

    async def expect(payload, **kwargs):
        frame = parse_civ_frame(payload)
        return parse_civ_frame(
            build_civ_frame(
                CONTROLLER_ADDR,
                radio._radio_addr,
                frame.command,
                sub=frame.sub,
                data=b"\x00",
            )
        )

    monkeypatch.setattr(radio, "_send_civ_raw", send)
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    monkeypatch.setattr(radio, "_send_civ_expect", expect)
    await radio.enable_scope(policy="verify")
    await radio.enable_scope(policy="verify")
    assert writes == [(0x27, 0x10, b"\x01"), (0x27, 0x11, b"\x01")]
    await radio.disable_scope()
    await radio.disable_scope()
    assert writes[-2:] == [(0x27, 0x10, b"\x00"), (0x27, 0x11, b"\x00")]
