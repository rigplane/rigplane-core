
import pytest

from rigplane.core.types import ScopeFixedEdge
from rigplane.runtime.scope_session import ScopeSession, ScopeState, ScopeView
from rigplane.scope.models import ScopeDemand
from rigplane.scope import ScopeFrame


class Writer:
    def __init__(self):
        self.state = ScopeState(False, False)
        self.views = []
        self.writes = []
        self.fail = False

    async def _scope_read_state(self):
        return self.state

    async def _scope_write_state(self, previous, desired, *, policy, timeout):
        if previous.scope_on != desired.scope_on:
            self.writes.append((0x10, desired.scope_on))
        if previous.output_on != desired.output_on:
            self.writes.append((0x11, desired.output_on))
        if self.fail:
            raise RuntimeError("no acknowledgement")
        self.state = desired

    async def _scope_read_view(self):
        return ScopeView(0, 0, 2, 1)

    async def _scope_write_view(self, view):
        self.views.append(view)

    async def _scope_read_fixed(self, range_index, edge):
        return ScopeFixedEdge(range_index, edge, 14000000, 14350000)


async def test_one_enable_exact_restore_once():
    writer = Writer()
    session = ScopeSession(writer)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    await session.set_demand(ScopeDemand(2, 2))
    assert session.enabled
    assert writer.writes == [(0x10, True), (0x11, True)]
    await session.set_demand(ScopeDemand(1, 1))
    await session.set_demand(ScopeDemand(0, 0))
    await session.set_demand(ScopeDemand(0, 0))
    assert writer.writes[-2:] == [(0x10, False), (0x11, False)]
    assert len(writer.writes) == 4
    assert not session.enabled


async def test_deferred_enable_and_tx_have_no_writes():
    writer = Writer()
    session = ScopeSession(writer)
    await session.set_demand(ScopeDemand(1, 1))
    assert not session.enabled
    assert not writer.writes
    await session.set_inputs(ready=True, tx=True)
    assert not writer.writes
    await session.set_inputs(ready=True, tx=False)
    await session.set_inputs(ready=True, tx=False)
    assert writer.writes == [(0x10, True), (0x11, True)]


async def test_enabled_requires_acknowledgement():
    writer = Writer()
    writer.fail = True
    session = ScopeSession(writer)
    await session.set_inputs(ready=True, tx=False)
    with pytest.raises(RuntimeError, match="acknowledgement"):
        await session.set_demand(ScopeDemand(1, 1))
    assert not session.enabled


async def test_pause_only_output_resume_once_and_release_restores(virtual_clock):
    writer = Writer()
    writer.state = ScopeState(True, False)
    session = ScopeSession(writer, clock=virtual_clock)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(2, 2))
    await session.set_demand(ScopeDemand(2, 0))
    await session.set_demand(ScopeDemand(1, 0))
    assert writer.writes == [(0x11, True), (0x11, False)]
    started = virtual_clock()
    await session.set_demand(ScopeDemand(1, 1))
    assert virtual_clock() - started <= 0.1
    assert writer.writes[-1] == (0x11, True)
    await session.set_demand(ScopeDemand(0, 0))
    assert writer.state == ScopeState(True, False)
    assert writer.writes == [(0x11, True), (0x11, False), (0x11, True), (0x11, False)]


async def test_view_and_touched_fixed_slot_restored():
    writer = Writer()
    session = ScopeSession(writer)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    fixed = ScopeFixedEdge(7, 1, 14050000, 14150000)
    await session.set_fixed(fixed, 0)
    await session.set_fixed(fixed, 0)
    assert len(writer.views) == 1
    await session.set_demand(ScopeDemand(0, 0))
    assert writer.views[-1] == ScopeView(0, 0, 2, 1)
    assert writer.views[-2].fixed_edge == ScopeFixedEdge(7, 1, 14000000, 14350000)


async def test_oor_is_arrival_never_hidden(virtual_clock):
    session = ScopeSession(Writer(), clock=virtual_clock, silence_seconds=1)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    session.on_frame(ScopeFrame(0, 1, 14000000, 14350000, b"", True))
    assert session.health(civ_flowing=True).hidden is False


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
    from rigplane_testkit.icom import MockIcomRadio
    from test_web_server import _addr

    sim = MockIcomRadio(scope_stream=True)
    await sim.start()
    radio = IcomRadio("127.0.0.1", port=sim.control_port,
                      username="testuser", password="testpass", model="IC-7610")
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
                    await ws.send_str(encode_scope_subscription(
                        Subscription("radio", 0, View(14_090_000, 14_110_000), 100, fps)
                    ))
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
                await ws.send_json({"type": "unsubscribe", "sourceId": "radio", "receiver": 0})
                await await_until(lambda: not server._scope_enabled)
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

    radio = (IcomRadio("127.0.0.1", model="IC-7300") if transport == "lan"
             else Ic7300SerialRadio(device="/dev/fake-scope", baudrate=115200))
    radio._connected = True
    writes = []

    async def send(payload, **kwargs):
        frame = parse_civ_frame(payload)
        if frame.data:
            writes.append((frame.command, frame.sub, frame.data))
            radio._scope_activity_event.set()
        return parse_civ_frame(build_civ_frame(CONTROLLER_ADDR, radio._radio_addr, 0xFB))

    async def expect(payload, **kwargs):
        frame = parse_civ_frame(payload)
        return parse_civ_frame(build_civ_frame(CONTROLLER_ADDR, radio._radio_addr,
                                               frame.command, sub=frame.sub, data=b"\x00"))

    monkeypatch.setattr(radio, "_send_civ_raw", send)
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    monkeypatch.setattr(radio, "_send_civ_expect", expect)
    await radio.enable_scope(policy="verify")
    await radio.enable_scope(policy="verify")
    assert writes == [(0x27, 0x10, b"\x01"), (0x27, 0x11, b"\x01")]
    await radio.disable_scope()
    await radio.disable_scope()
    assert writes[-2:] == [(0x27, 0x10, b"\x00"), (0x27, 0x11, b"\x00")]


async def test_release_grace_cancelled_by_resume(virtual_clock):
    writer = Writer()
    session = ScopeSession(writer, clock=virtual_clock, disable_grace_seconds=0.25)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    await session.set_demand(ScopeDemand(0, 0))
    assert len(writer.writes) == 2
    virtual_clock.advance(0.1)
    await session.set_demand(ScopeDemand(1, 1))
    virtual_clock.advance(1)
    await session.reconcile()
    assert len(writer.writes) == 2
    await session.set_demand(ScopeDemand(0, 0))
    virtual_clock.advance(0.25)
    await session.reconcile()
    assert writer.writes[-2:] == [(0x10, False), (0x11, False)]
