"""R5 review regressions: real Core RX, SDK and local WebSocket seams."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

import aiohttp
import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.exceptions import CommandError, TimeoutError
from rigplane.core.types import ScopeFixedEdge, bcd_encode
from rigplane.radio import IcomRadio
from rigplane.profiles import resolve_radio_profile
from rigplane.scope.hub import HubSubscription
from rigplane.scope.models import FollowView, Subscription, View
from rigplane.testing.timing import await_until
from rigplane.web.protocol import (
    decode_scope_frame_v2,
    decode_scope_granted,
    encode_scope_subscription,
)
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


async def test_mixed_journey_server_grants_follow_before_refusing_pinned_view(
    monkeypatch, tmp_path
):
    import argparse
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "e2e"))
    import journey_web_server
    from rigplane_testkit.icom.simulator import MockIcomRadio

    exchanges = []
    dispatch = MockIcomRadio._dispatch_civ

    def record(self, command, payload, from_addr):
        reply = dispatch(self, command, payload, from_addr)
        if command == 0x27 and reply is not None:
            exchanges.append((payload.hex(), reply.hex()))
        return reply

    monkeypatch.setattr(MockIcomRadio, "_dispatch_civ", record)
    monkeypatch.setattr(
        journey_web_server,
        "parse_args",
        lambda: argparse.Namespace(
            host="127.0.0.1",
            port=0,
            static_dir=tmp_path,
            panorama_audio=True,
            freq=14_286_000,
            read_only=False,
        ),
    )

    async def exercise(server, *, on_started):
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as client:
                async with client.ws_connect(
                    f"http://127.0.0.1:{port}/api/v1/scope"
                ) as ws:
                    for receiver in (0, 1):
                        await ws.send_str(
                            encode_scope_subscription(
                                Subscription(
                                    "radio",
                                    receiver,
                                    FollowView("vfo", 100000),
                                    1206,
                                    20,
                                )
                            )
                        )
                    frames = {0: [], 1: []}
                    grants = set()
                    while not (grants == {0, 1} and all(frames.values())):
                        message = await ws.receive(timeout=5)
                        if message.type is aiohttp.WSMsgType.TEXT:
                            assert '"error"' not in message.data, (
                                message.data,
                                exchanges,
                            )
                            grants.add(decode_scope_granted(message.data).receiver)
                        else:
                            assert message.type is aiohttp.WSMsgType.BINARY
                            frame = decode_scope_frame_v2(message.data)
                            assert frame.pixels and not frame.out_of_range
                            frames[frame.receiver].append(frame)
                    await ws.send_str(
                        encode_scope_subscription(
                            Subscription(
                                "radio", 0, View(14_260_000, 14_310_000), 1206, 20
                            )
                        )
                    )
                    while True:
                        message = await ws.receive(timeout=5)
                        if message.type is aiohttp.WSMsgType.TEXT:
                            assert '"error"' in message.data
                            break
                    for receiver in frames:
                        previous = frames[receiver][-1].seq
                        while True:
                            message = await ws.receive(timeout=5)
                            if message.type is aiohttp.WSMsgType.BINARY:
                                frame = decode_scope_frame_v2(message.data)
                                if frame.receiver == receiver and frame.seq > previous:
                                    break
        finally:
            await server.stop()

    monkeypatch.setattr(WebServer, "serve_forever", exercise)
    await journey_web_server.run()


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
            # MOR-4385 ±half-width presets: the 100 kHz VFO-centred
            # sub view needs preset 50000 (index 4, center ±50000),
            # not preset 100000 (index 5) as under full-width matching.
            assert sim.views[1][1] == 4
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


async def test_control_ws_refuses_legacy_span_preserving_subscription(sdk):
    radio, sim = sdk
    # The scope_stream sim replays a fixed IC-7610 recording whose MAIN
    # window is 14286000 ±100000 (span preset 100000). Under MOR-4385
    # adoption the follow grant tracks that streamed window, so the
    # request must be that same window — a real driven radio would
    # stream what Core wrote, but the recording never changes. Both
    # sides: the sim's VFO (so polls keep reporting it) and Core's
    # cached copy (so the first grant resolves before the next poll).
    # A narrower request would drive a smaller preset than the
    # recording's span metadata, and the adoption would rewrite it on
    # resubscribe; the 200 kHz request drives preset 100000 (index 5),
    # which the streamed metadata confirms.
    sim.set_frequency(14286000)
    radio._radio_state.main.freq = 14286000
    sim.capture_civ_frames = True

    def scope_writes():
        lengths = dict.fromkeys((0x10, 0x11, 0x12, 0x13), 1)
        lengths.update({0x14: 2, 0x15: 6, 0x16: 2, 0x1E: 12})
        return [
            frame
            for raw in sim.civ_frames_received
            if (frame := parse_civ_frame(raw)).command == 0x27
            and len(frame.data) == lengths.get(frame.sub)
        ]

    async with web(radio) as (_, client, base, protocols, control):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            subscription = encode_scope_subscription(
                Subscription("radio", 0, FollowView("vfo", 200000), 100, 20)
            )
            await ws.send_str(subscription)
            granted = decode_scope_granted(await ws.receive_str(timeout=5))
            await ws.receive_bytes(timeout=5)
            before = scope_writes()
            assert before
            views = dict(sim.views)
            assert views[0][1] != 3
            await control.send_json(
                {
                    "type": "cmd",
                    "id": "legacy-span",
                    "name": "set_scope_span",
                    "params": {"span": 3},
                }
            )
            async with asyncio.timeout(5):
                while True:
                    response = await control.receive_json()
                    if (
                        response.get("type") == "response"
                        and response.get("id") == "legacy-span"
                    ):
                        break
            assert response["ok"] is False
            assert response["error"] == "unsupported_command"
            assert "scope subscription view" in response["message"]
            await ws.send_str(subscription)
            async with asyncio.timeout(5):
                while True:
                    message = await ws.receive()
                    if message.type is aiohttp.WSMsgType.TEXT:
                        assert decode_scope_granted(message.data) == granted
                        break
            frame = decode_scope_frame_v2(await ws.receive_bytes(timeout=5))
            assert (frame.view_start_hz, frame.view_end_hz) == (
                granted.view.start_hz,
                granted.view.end_hz,
            )
            assert sim.views == views
            assert scope_writes() == before


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


# ── MOR-4385 + MOR-4392 gate round 1: real /api/v1/scope regressions ──
# Every scenario below drives a REAL ScopeControlSimulator over TCP: radio
# observations arrive through the production RX path (CI-V waveform
# assembly + 0x27 response routing into ScopeSession.observe) and
# subscriptions through the real scope WebSocket handler — never through
# direct session.observe()/hub.subscribe() calls.


class _ScopeWindowSimulator(ScopeControlSimulator):
    """Scope simulator whose streamed window follows its CI-V scope state.

    The base class replays a fixed recording; here every pump reshapes the
    templates the way the panorama journey simulator does: the mode byte
    tracks ``views[receiver][0]``, and the window is ``windows[receiver]``
    in FIX/SCROLL-F, a radio-owned scroll window (VFO ± span preset,
    rebuilt on VFO exit or span change) in SCROLL-C, or center ± span
    preset in CENTER. A receiver in ``muted`` does not stream (e.g. a
    radio streaming only SUB at startup).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.windows: dict[int, tuple[int, int]] = {}
        self.muted: set[int] = set()
        # SCROLL-C windows the "radio" owns, keyed by receiver, plus the
        # span preset each window was built at (#4565 pre-review item 5).
        self._scroll: dict[int, tuple[int, int]] = {}
        self._scroll_span: dict[int, int] = {}
        # 27 15 GET reads the "radio" does not answer: the waveform edges
        # are then the only evidence of an external span change.
        self.withhold_span_reads = False

    def _dispatch_scope(self, payload: bytes, from_addr: int) -> bytes | None:
        if self.withhold_span_reads and payload[0] == 0x15 and len(payload) == 2:
            return None
        return super()._dispatch_scope(payload, from_addr)

    def _scope_streaming_receivers(self) -> list[int]:
        return [
            receiver
            for receiver in super()._scope_streaming_receivers()
            if receiver not in self.muted
        ]

    def pump_scope_stream(self) -> int:
        for receiver, templates in self._scope_templates.items():
            mode, span, _edge = self.views[receiver]
            if mode == 0:
                # Center mode carries (center, span); the assembler expands.
                first = self._frequency
                last = self.profile.scope_span_presets_hz[span]
            elif mode == 2:
                # SCROLL-C: the radio owns the window, keeps the VFO
                # inside it and sizes it to the SPAN preset — so an
                # acknowledged span change must show in the following
                # waveform edges (#4565 pre-review item 5). The window
                # rebuilds when it is absent, when the VFO leaves it, or
                # when the span preset changed; the same model the
                # panorama journey simulator uses.
                preset = self.profile.scope_span_presets_hz[span]
                tuned = self._frequency if receiver == 0 else self._unselected_frequency
                window = self._scroll.get(receiver)
                if (
                    window is None
                    or self._scroll_span.get(receiver) != span
                    or not window[0] <= tuned < window[1]
                ):
                    window = (tuned - preset, tuned + preset)
                    self._scroll[receiver] = window
                    self._scroll_span[receiver] = span
                first, last = window
            else:
                # FIX/SCROLL-F carry configured edges; the radio never
                # moves them on its own (ScopeAssembler expands only
                # mode 0).
                first, last = self.windows.get(receiver, (14_000_000, 14_350_000))
            self._scope_templates[receiver] = [
                template[:9]
                + bytes([mode])
                + bcd_encode(first)
                + bcd_encode(last)
                + b"\x00"
                + bytes(15 + index % 30 for index in range(len(template) - 22))
                + template[-1:]
                for template in templates
            ]
        return super().pump_scope_stream()

    def push_scope_setting(self, data: bytes) -> None:
        """An unsolicited 0x27 report, like a front-panel scope change."""
        if self._civ_proto is None or self._civ_owner_addr is None:
            return
        frame = self._civ_frame(CONTROLLER_ADDR, self._radio_addr, 0x27, data=data)
        self._send(
            self._civ_proto,
            self._wrap_civ(frame, self._civ_owner_id),
            self._civ_owner_addr,
            "civ",
        )


@pytest.fixture
async def windowed_sdk(request):
    model = getattr(request, "param", "IC-7610")
    profile = resolve_radio_profile(model=model)
    sim = _ScopeWindowSimulator(
        scope_stream=True,
        dual_receiver=profile.receiver_count > 1,
        radio_addr=profile.civ_addr,
    )
    sim.profile = profile
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model=model,
    )
    await radio.connect()
    try:
        yield radio, sim
    finally:
        await radio.disconnect()
        await sim.stop()


def _viewport_writes(sim) -> list:
    """CI-V scope viewport SETs the radio received (mode/span/edge/fixed)."""
    lengths = {0x14: 2, 0x15: 6, 0x16: 2, 0x1E: 12}
    return [
        frame
        for raw in sim.civ_frames_received
        if (frame := parse_civ_frame(raw)).command == 0x27
        and len(frame.data) == lengths.get(frame.sub)
    ]


async def _subscribe(ws, subscription):
    await ws.send_str(encode_scope_subscription(subscription))
    while True:
        message = await ws.receive(timeout=5)
        if message.type is aiohttp.WSMsgType.TEXT:
            assert '"error"' not in message.data
            return decode_scope_granted(message.data)


async def _frame_with(ws, view, timeout=5):
    """The next binary wire frame whose view matches ``view`` exactly."""
    async with asyncio.timeout(timeout):
        while True:
            message = await ws.receive(timeout=5)
            if message.type is not aiohttp.WSMsgType.BINARY:
                continue
            frame = decode_scope_frame_v2(message.data)
            if (frame.view_start_hz, frame.view_end_hz) == view:
                return frame


@pytest.mark.parametrize(
    "model,receiver", [("IC-7610", 0), ("IC-7610", 1), ("IC-705", 0)]
)
@pytest.mark.parametrize("sub,key", [(0x14, "mode"), (0x15, "span"), (0x16, "edge")])
@pytest.mark.parametrize("failure", ["nak", "timeout"])
@pytest.mark.parametrize("refuse_span_write", [False, True])
async def test_capture_read_failure_degrades_through_scope_ws(
    model, receiver, sub, key, failure, refuse_span_write, monkeypatch, caplog
):
    profile = resolve_radio_profile(model=model)
    sim = _ScopeWindowSimulator(
        scope_stream=True,
        dual_receiver=profile.receiver_count > 1,
        radio_addr=profile.civ_addr,
        capture_civ_frames=True,
    )
    sim.profile = profile
    sim.set_frequency(14_100_000)
    sim._unselected_frequency = 14_100_000
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model=model,
    )
    await radio.connect()
    dispatch = sim._dispatch_civ
    failed_reads = 0
    refused_writes = 0

    def one_shot(cmd, payload, from_addr):
        nonlocal failed_reads, refused_writes
        if cmd == 0x27 and payload == bytes([sub, receiver]) and not failed_reads:
            failed_reads += 1
            if failure == "timeout":
                return None
            sim.refused_civ_commands.add((cmd, sub))
            try:
                return dispatch(cmd, payload, from_addr)
            finally:
                sim.refused_civ_commands.remove((cmd, sub))
        if (
            cmd == 0x27
            and payload[:1] == b"\x15"
            and len(payload) > 2
            and refuse_span_write
            and not refused_writes
        ):
            refused_writes += 1
            return sim._civ_nak(from_addr, sim._radio_addr)
        return dispatch(cmd, payload, from_addr)

    monkeypatch.setattr(sim, "_dispatch_civ", one_shot)
    radio._radio_state.main.freq = radio._radio_state.sub.freq = 14_100_000
    caplog.set_level(logging.WARNING)
    try:
        async with web(radio) as (_, client, base, protocols, _):
            async with client.ws_connect(
                base + "/api/v1/scope", protocols=protocols
            ) as ws:
                initial = await _subscribe(
                    ws,
                    Subscription(
                        "radio", receiver, FollowView("vfo", 100_000), 475, 20
                    ),
                )
                assert initial.receiver == receiver
                half = profile.scope_span_presets_hz[3] if refuse_span_write else 50_000
                view = (14_100_000 - half, 14_100_000 + half)
                if initial.coverage is not None:
                    assert (initial.coverage.start_hz, initial.coverage.end_hz) == view
                first = await _frame_with(ws, view)
                second = await _frame_with(ws, view)
                assert first.pixels and second.pixels and second.seq > first.seq
                assert (second.coverage_start_hz, second.coverage_end_hz) == view
                if refuse_span_write:
                    assert refused_writes == 1
                    assert len([f for f in _viewport_writes(sim) if f.sub == 0x15]) == 1
                before_close = len(_viewport_writes(sim))
            assert await await_until(lambda: not radio.scope_session().active)
            restores = _viewport_writes(sim)[before_close:]
            assert not [f for f in restores if f.sub == sub and f.data[0] == receiver]
            assert failed_reads == 1
            warnings = [
                record
                for record in caplog.records
                if record.levelno == logging.WARNING
                and "scope capture failed" in record.message
            ]
            assert len(warnings) == 1
            assert f"receiver={receiver}" in warnings[0].message
            assert f"key={key}" in warnings[0].message
            assert (
                "timed out" in warnings[0].message.lower()
                if failure == "timeout"
                else "NAK" in warnings[0].message
            )
    finally:
        await radio.disconnect()
        await sim.stop()


@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-9700"], indirect=True)
async def test_sub_follow_refuses_failed_dual_acquisition(windowed_sdk, monkeypatch):
    radio, sim = windowed_sdk
    sim._scope_13 = 0
    radio._radio_state.main.freq = radio._radio_state.sub.freq = 14_100_000
    dispatch = sim._dispatch_civ

    def refuse_dual(cmd, payload, from_addr):
        if cmd == 0x27 and payload == b"\x13\x01":
            return sim._civ_nak(from_addr, sim._radio_addr)
        return dispatch(cmd, payload, from_addr)

    monkeypatch.setattr(sim, "_dispatch_civ", refuse_dual)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await ws.send_str(
                encode_scope_subscription(
                    Subscription("radio", 1, FollowView("vfo", 100_000), 475, 20)
                )
            )
            message = await ws.receive(timeout=5)
            assert message.type is aiohttp.WSMsgType.TEXT
            assert '"type":"error"' in message.data
        assert await await_until(lambda: not radio.scope_session().active)
        assert sim._scope_13 == 0


@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-705"], indirect=True)
@pytest.mark.parametrize("mode", [1, 3])
async def test_timed_out_mode_capture_preserves_streamed_fixed_mode(
    windowed_sdk, mode, monkeypatch
):
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    sim.views[0] = (mode, 3, 1)
    sim.windows[0] = (14_090_000, 14_110_000)
    dispatch = sim._dispatch_civ
    missed = False

    def timeout_mode(cmd, payload, from_addr):
        nonlocal missed
        if cmd == 0x27 and payload == b"\x14\x00" and not missed:
            missed = True
            return None
        return dispatch(cmd, payload, from_addr)

    monkeypatch.setattr(sim, "_dispatch_civ", timeout_mode)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            assert _viewport_writes(sim) == []
            first = await _frame_with(ws, sim.windows[0])
            second = await _frame_with(ws, sim.windows[0])
            assert second.seq > first.seq and second.pixels
        assert await await_until(lambda: not radio.scope_session().active)
        assert missed and sim.views[0][0] == mode
        assert _viewport_writes(sim) == []


@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-705"], indirect=True)
@pytest.mark.parametrize("failure", ["nak", "nak_during", "timeout"])
async def test_failed_follow_span_write_keeps_restore_ownership_truthful(
    windowed_sdk, failure, monkeypatch
):
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    dispatch = sim._dispatch_civ
    failed = False

    def fail_span(cmd, payload, from_addr):
        nonlocal failed
        if cmd == 0x27 and payload[:1] == b"\x15" and len(payload) > 2 and not failed:
            failed = True
            if failure != "timeout":
                if failure == "nak_during":
                    sim.views[0] = (0, 5, 1)
                    sim.push_scope_setting(
                        b"\x15\x00" + bcd_encode(sim.profile.scope_span_presets_hz[5])
                    )
                return sim._civ_nak(from_addr, sim._radio_addr)
            dispatch(cmd, payload, from_addr)
            return None
        return dispatch(cmd, payload, from_addr)

    monkeypatch.setattr(sim, "_dispatch_civ", fail_span)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            if failure == "nak":
                sim.views[0] = (0, 5, 1)
                sim.push_scope_setting(
                    b"\x15\x00" + bcd_encode(sim.profile.scope_span_presets_hz[5])
                )
            frame = await _frame_with(ws, (14_050_000, 14_150_000))
            assert frame.pixels and failed
            before_close = len(_viewport_writes(sim))
        assert await await_until(lambda: not radio.scope_session().active)
        restores = [f for f in _viewport_writes(sim)[before_close:] if f.sub == 0x15]
        if failure != "timeout":
            assert sim.views[0][1] == 5
            assert restores == []
        else:
            assert sim.views[0][1] == 3
            assert len(restores) == 1


@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-705"], indirect=True)
async def test_unread_span_stays_unowned_after_external_adoption_and_new_gesture(
    windowed_sdk, monkeypatch
):
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    dispatch = sim._dispatch_civ
    failed = False

    def refuse_read(cmd, payload, from_addr):
        nonlocal failed
        if cmd == 0x27 and payload == b"\x15\x00" and not failed:
            failed = True
            return sim._civ_nak(from_addr, sim._radio_addr)
        return dispatch(cmd, payload, from_addr)

    monkeypatch.setattr(sim, "_dispatch_civ", refuse_read)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            await _frame_with(ws, (14_050_000, 14_150_000))
            sim.views[0] = (0, 5, 1)
            sim.push_scope_setting(
                b"\x15\x00" + bcd_encode(sim.profile.scope_span_presets_hz[5])
            )
            assert await await_until(
                lambda: radio.scope_session()._applied.get((0, "span")) == 5
            )
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 500_000), 475, 20)
            )
            assert (await _frame_with(ws, (13_850_000, 14_350_000))).pixels
            before_close = len(_viewport_writes(sim))
        assert await await_until(lambda: not radio.scope_session().active)
        assert sim.views[0][1] == 6 and failed
        assert not [f for f in _viewport_writes(sim)[before_close:] if f.sub == 0x15]


async def test_ctr_fix_ctr_transitions_keep_one_follow_subscription_quiet(
    windowed_sdk,
):
    """Gate round 1 finding 6: external CTR→FIX→CTR on ONE unchanged follow
    subscription — no resubscribe, the wire geometry follows the radio, and
    no unsolicited viewport write leaves Core in either transition."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            granted = await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            assert (granted.view.start_hz, granted.view.end_hz) == (
                14_050_000,
                14_150_000,
            )
            await _frame_with(ws, (14_050_000, 14_150_000))
            assert sim.views[0][1] == presets.index(50_000)
            baseline = len(_viewport_writes(sim))
            assert baseline > 0
            # External FIX entry (front panel): the follow grant IS the
            # fixed window (MOR-4451: the panorama stands still), no
            # radio write leaves Core.
            sim.windows[0] = (14_075_000, 14_175_000)
            sim.views[0] = (1, 4, 1)
            clipped = await _frame_with(ws, (14_075_000, 14_175_000))
            assert (clipped.coverage_start_hz, clipped.coverage_end_hz) == (
                14_075_000,
                14_175_000,
            )
            assert len(_viewport_writes(sim)) == baseline
            # External return to CENTER: the same subscription follows.
            sim.views[0] = (0, 4, 1)
            back = await _frame_with(ws, (14_050_000, 14_150_000))
            assert (back.coverage_start_hz, back.coverage_end_hz) == (
                14_050_000,
                14_150_000,
            )
            assert len(_viewport_writes(sim)) == baseline
        # The owned restore at close returns only what Core still owns:
        # the span it drove (mode left the restore set at FIX adoption).
        assert await await_until(lambda: not radio.scope_session().active)
        assert sim.views[0] == (0, 3, 1)


async def test_sub_fix_follow_gesture_writes_nothing(windowed_sdk):
    """Gate round 1 finding 1: an IC-7610 SUB in FIX — a SUB follow
    subscription writes no mode/span/edge/fixed-edge command; the grant
    and frames clip to the SUB fixed window."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    radio._radio_state.sub.freq = 14_100_000
    sim.views[1] = (1, 3, 1)
    sim.windows[1] = (14_090_000, 14_110_000)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 1, FollowView("vfo", 50_000), 475, 20)
            )
            frame = await _frame_with(ws, (14_090_000, 14_110_000))
            assert (frame.coverage_start_hz, frame.coverage_end_hz) == (
                14_090_000,
                14_110_000,
            )
            assert _viewport_writes(sim) == []
            assert sim.views[1] == (1, 3, 1)


async def test_main_scroll_f_follow_gesture_writes_nothing(windowed_sdk):
    """Gate round 1 finding 1: MAIN in SCROLL-F (mode 3, fixed-family) —
    a MAIN follow subscription writes nothing and clips to the radio's
    window instead of flipping the radio to CENTER."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    sim.views[0] = (3, 3, 1)
    sim.windows[0] = (14_060_000, 14_140_000)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            frame = await _frame_with(ws, (14_060_000, 14_140_000))
            assert (frame.coverage_start_hz, frame.coverage_end_hz) == (
                14_060_000,
                14_140_000,
            )
            assert _viewport_writes(sim) == []
            assert sim.views[0] == (3, 3, 1)


async def test_startup_main_fix_sub_selected_first_follow_preserves_fix(
    windowed_sdk,
):
    """Gate round 1 finding 2: MAIN in FIX, SUB selected (the session
    snapshot then reads the SELECTED receiver's CENTER cache), MAIN frames
    withheld — the first MAIN follow subscription must acquire MAIN's own
    mode and preserve FIX with zero viewport writes."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    sim.views[0] = (1, 3, 1)
    sim.windows[0] = (14_060_000, 14_140_000)
    sim.muted.add(0)
    await radio.set_scope_receiver(1)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            granted = await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            # No MAIN frame has streamed: the grant is the requested window
            # and the radio kept its FIX mode (no 0x14 flip to CENTER).
            assert (granted.view.start_hz, granted.view.end_hz) == (
                14_050_000,
                14_150_000,
            )
            assert _viewport_writes(sim) == []
            assert sim.views[0] == (1, 3, 1)
            sim.muted.discard(0)
            frame = await _frame_with(ws, (14_060_000, 14_140_000))
            assert (frame.coverage_start_hz, frame.coverage_end_hz) == (
                14_060_000,
                14_140_000,
            )
            assert _viewport_writes(sim) == []
            assert sim.views[0] == (1, 3, 1)


async def test_last_unsubscribe_does_not_restore_over_external_span(windowed_sdk):
    """Gate round 1 finding 3: an external span change is adopted; closing
    the last real scope WebSocket must NOT restore Core's stale span over
    it (owner rule «следовать за рацией»)."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            await _frame_with(ws, (14_050_000, 14_150_000))
            assert sim.views[0][1] == presets.index(50_000)
            # External ±100k (front panel / RigDeck): adopted via the stream.
            sim.views[0] = (0, presets.index(100_000), 1)
            session = radio.scope_session()
            assert await await_until(
                lambda: (
                    session.observed is not None
                    and session.observed.span == presets.index(100_000)
                )
            )
            span_sets_before_close = len(
                [w for w in _viewport_writes(sim) if w.sub == 0x15]
            )
        assert await await_until(lambda: not radio.scope_session().active)
        # The externally set span survives the close; Core wrote no 0x15
        # beyond the one its own subscribe drove.
        assert sim.views[0][1] == presets.index(100_000)
        assert (
            len([w for w in _viewport_writes(sim) if w.sub == 0x15])
            == span_sets_before_close
        )


async def test_last_unsubscribe_does_not_restore_over_external_fixed_edge(
    windowed_sdk,
):
    """Gate round 1 finding 3: an external rewrite of the fixed-edge slot
    Core configured leaves the restore set; closing the last real scope
    WebSocket writes no fixed edge back over the external value."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    external = ScopeFixedEdge(6, 1, 14_060_000, 14_160_000)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, View(14_050_000, 14_150_000), 475, 20)
            )
            assert sim.fixed[b"\x06\x01"] == ScopeFixedEdge(
                6, 1, 14_050_000, 14_150_000
            )
            # External rewrite of the same slot, reported like a front-panel
            # change (0x1E report through the production RX path).
            sim.fixed[b"\x06\x01"] = external
            sim.push_scope_setting(
                b"\x1e\x06\x01"
                + bcd_encode(external.start_hz)
                + bcd_encode(external.end_hz)
            )
            session = radio.scope_session()
            assert await await_until(
                lambda: session._applied.get((0, "fixed_edge")) == external
            )
            fixed_sets_before_close = len(
                [w for w in _viewport_writes(sim) if w.sub == 0x1E]
            )
        assert await await_until(lambda: not radio.scope_session().active)
        assert sim.fixed[b"\x06\x01"] == external
        assert (
            len([w for w in _viewport_writes(sim) if w.sub == 0x1E])
            == fixed_sets_before_close
        )


async def test_follow_fix_external_center_leaves_no_stale_span_restore(
    windowed_sdk,
):
    """Gate round 2 (fix-4565): a follow subscription drives span 3→4
    (capturing 3); a pinned FIX subscription retires desired span; the
    operator returns to CENTER with span 5; closing the last real scope
    WebSocket must NOT restore 3 over 5 (owner rule «следовать за рацией»)."""
    from rigplane.scope.models import View

    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    assert (presets.index(50_000), presets.index(100_000)) == (4, 5)
    sim.views[0] = (0, 3, 1)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            await _frame_with(ws, (14_050_000, 14_150_000))
            assert sim.views[0][1] == 4
            await _subscribe(
                ws, Subscription("radio", 0, View(14_050_000, 14_150_000), 475, 20)
            )
            session = radio.scope_session()
            assert await await_until(lambda: sim.views[0][0] == 1)
            assert (0, "span") not in session._desired
            # External return to CENTER with span index 5 (front panel):
            # the streamed window follows the radio through the production
            # RX path.
            sim.views[0] = (0, 5, 1)
            assert await await_until(
                lambda: (
                    session.observed is not None
                    and session.observed.span == presets.index(100_000)
                )
            )
            span_sets_before_close = len(
                [w for w in _viewport_writes(sim) if w.sub == 0x15]
            )
            assert span_sets_before_close >= 1
        assert await await_until(lambda: not radio.scope_session().active)
        assert sim.views[0] == (0, 5, 1)
        assert (
            len([w for w in _viewport_writes(sim) if w.sub == 0x15])
            == span_sets_before_close
        )


async def test_retired_fixed_slot_external_rewrite_leaves_no_stale_restore(
    windowed_sdk,
):
    """Gate round 2 (fix-4565): a FIX subscription writes slot (6, 1); the
    operator returns the radio to CENTER, then a follow resubscribe runs
    configure(mode=0, ...) which retires desired fixed_edge/edge; the
    operator rewrites that slot's edges; closing the last real scope
    WebSocket writes no fixed edge back over the external value."""
    from rigplane.scope.models import View

    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    requested = ScopeFixedEdge(6, 1, 14_050_000, 14_150_000)
    external = ScopeFixedEdge(6, 1, 14_060_000, 14_160_000)
    sim.views[0] = (0, 3, 1)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, View(14_050_000, 14_150_000), 475, 20)
            )
            assert sim.fixed[b"\x06\x01"] == requested
            # External return to CENTER (front panel): the mode report
            # arrives through the production RX path.
            sim.views[0] = (0, 3, 1)
            sim.push_scope_setting(b"\x14\x00\x00")
            session = radio.scope_session()
            assert await await_until(lambda: session._applied.get((0, "mode")) == 0)
            # A follow subscription now runs configure(mode=0, span) on a
            # CENTER radio, retiring desired fixed_edge/edge.
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            assert await await_until(lambda: sim.views[0][1] == 4)
            assert (0, "fixed_edge") not in session._desired
            assert (0, "edge") not in session._desired
            # External rewrite of the same slot, reported like a front-panel
            # change (0x1E report through the production RX path).
            sim.fixed[b"\x06\x01"] = external
            sim.push_scope_setting(
                b"\x1e\x06\x01"
                + bcd_encode(external.start_hz)
                + bcd_encode(external.end_hz)
            )
            assert await await_until(
                lambda: session._applied.get((0, "fixed_edge")) == external
            )
            fixed_sets_before_close = len(
                [w for w in _viewport_writes(sim) if w.sub == 0x1E]
            )
        assert await await_until(lambda: not radio.scope_session().active)
        assert sim.fixed[b"\x06\x01"] == external
        assert (
            len([w for w in _viewport_writes(sim) if w.sub == 0x1E])
            == fixed_sets_before_close
        )


async def test_single_scope_sub_fix_follow_acquires_dual_and_streams(windowed_sdk):
    """Pre-review round 1: single-scope output (dual off), MAIN selected,
    SUB in FIX — a SUB follow subscription still acquires dual output so
    SUB streams (the CENTER path configures it via set_center/set_fixed),
    while mode/span/edge/fixed writes stay at zero; teardown restores
    dual off."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    radio._radio_state.sub.freq = 14_100_000
    sim._scope_13 = 0
    sim.views[1] = (1, 3, 1)
    sim.windows[1] = (14_090_000, 14_110_000)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 1, FollowView("vfo", 50_000), 475, 20)
            )
            frame = await _frame_with(ws, (14_090_000, 14_110_000))
            assert (frame.coverage_start_hz, frame.coverage_end_hz) == (
                14_090_000,
                14_110_000,
            )
            assert sim._scope_13 == 1
            assert _viewport_writes(sim) == []
            assert sim.views[1] == (1, 3, 1)
        assert await await_until(lambda: not radio.scope_session().active)
        assert sim._scope_13 == 0


async def test_fix_follow_reopen_forgets_the_stopped_stream_window(windowed_sdk):
    """Pre-review round 1 (P2): the remembered native coverage belongs to
    the stream that produced it — close a FIX subscription, change the
    window externally while stopped, reopen with frames withheld: the new
    grant is the requested window, not a clip against the stale one."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    sim.views[0] = (1, 3, 1)
    sim.windows[0] = (14_060_000, 14_140_000)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            await _frame_with(ws, (14_060_000, 14_140_000))
        assert await await_until(lambda: not radio.scope_session().active)
        # The stream is stopped; the fixed window moves externally.
        sim.windows[0] = (14_080_000, 14_120_000)
        sim.muted.add(0)
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            granted = await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            assert (granted.view.start_hz, granted.view.end_hz) == (
                14_050_000,
                14_150_000,
            )
            sim.muted.discard(0)
            await _frame_with(ws, (14_080_000, 14_120_000))


@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-705"], indirect=True)
async def test_scroll_c_span_step_writes_only_span_preset_over_ws(windowed_sdk):
    """#4565 journey: the MORE-menu S-C button's set_scope_mode(2), then a
    follow SPAN+ resubscribe — exactly one 27 15 receipt with the covering
    preset, zero 0x14 receipts after the S-C switch. The radio stays in
    SCROLL-C, and the acknowledged span change shows in the following
    waveform edges: the visible window widens to the granted preset
    (#4565 pre-review item 5) instead of silently keeping the old one.
    """
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            # The S-C button: an explicit mode command (its own 0x14 is
            # expected) — the session owns mode 2 from here on. The
            # radio-owned window starts at the subscription's preset.
            await radio.set_scope_mode(2)
            assert sim.views[0][0] == 2
            await _frame_with(ws, (14_050_000, 14_150_000))
            sim.civ_frames_received.clear()
            # SPAN+: a wider follow resubscribe must write only the
            # covering span preset, never a mode write.
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 500_000), 475, 20)
            )
            receipts = _viewport_writes(sim)
            assert len([w for w in receipts if w.sub == 0x15]) == 1
            assert [w for w in receipts if w.sub == 0x14] == []
            assert sim.views[0] == (2, presets.index(250_000), 1)
            # The user-visible SPAN outcome: later frames carry the changed
            # visible width (VFO ± the written preset) while the mode stays
            # SCROLL-C — the old window is no longer accepted.
            widened = await _frame_with(
                ws, (14_100_000 - 250_000, 14_100_000 + 250_000)
            )
            assert widened.pixels
            assert (widened.coverage_start_hz, widened.coverage_end_hz) == (
                14_100_000 - 250_000,
                14_100_000 + 250_000,
            )
            assert sim.views[0][0] == 2


@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-705"], indirect=True)
async def test_scroll_c_external_span_change_via_waveform_then_cached_preset_writes(
    windowed_sdk,
):
    """#4565 pre-review item 3, over the real scope WebSocket on two radio
    profiles: a front-panel span change in SCROLL-C must reach the session
    through the waveform edges (the reported window width maps back to the
    span preset), so a later request for the PREVIOUSLY CACHED preset is
    not suppressed by the same-value skip — the 27 15 SET goes out and the
    streamed window changes while the mode stays SCROLL-C.
    """
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            await radio.set_scope_mode(2)
            assert sim.views[0][0] == 2
            # The subscription cached the covering preset (50k): the
            # session's span is observed from the streamed width.
            await _frame_with(ws, (14_050_000, 14_150_000))
            session = radio.scope_session()
            assert await await_until(
                lambda: session._applied.get((0, "span")) == presets.index(50_000)
            )
            # External span change (front panel) while SCROLL-C: no write
            # leaves Core and no readback answers, so the following
            # waveform edges are the only evidence — the session adopts
            # the new preset from them.
            sim.withhold_span_reads = True
            sim.views[0] = (2, presets.index(100_000), 1)
            await _frame_with(ws, (14_000_000, 14_200_000))
            assert await await_until(
                lambda: session._applied.get((0, "span")) == presets.index(100_000)
            )
            sim.civ_frames_received.clear()
            # Requesting the PREVIOUSLY CACHED preset must reach the radio:
            # the stale cache must not suppress the SET.
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            span_sets = [w for w in _viewport_writes(sim) if w.sub == 0x15]
            assert len(span_sets) == 1 and span_sets[0].data[0] == 0
            assert sim.views[0] == (2, presets.index(50_000), 1)
            assert [w for w in _viewport_writes(sim) if w.sub == 0x14] == []
            # The window changes back to the requested preset's width.
            narrowed = await _frame_with(ws, (14_050_000, 14_150_000))
            assert narrowed.pixels
            assert sim.views[0][0] == 2


async def test_held_span_ack_survives_new_report_then_late_older_waveform(
    windowed_sdk, monkeypatch
):
    """MOR-4507 over the real scope WebSocket: a follow resubscribe's 27 15
    SET reaches the radio but its ACK is held. While it is held the radio
    streams the new width (a report of the new span), then an OLDER
    waveform at the old width finishes assembling. That late frame must
    not be adopted as an external change: once the ACK lands the stream
    keeps the requested width, reconciliation writes no reversing SET, and
    the last unsubscribe still restores the captured original span."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    original, old, new = (
        presets.index(25_000),
        presets.index(50_000),
        presets.index(250_000),
    )
    sim.views[0] = (2, original, 1)
    async with web(radio) as (_, client, base, protocols, _):
        ws = await client.ws_connect(base + "/api/v1/scope", protocols=protocols)
        await _subscribe(
            ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
        )
        await _frame_with(ws, (14_050_000, 14_150_000))
        session = radio.scope_session()
        assert await await_until(lambda: session._applied.get((0, "span")) == old)
        assert session._previous.get((0, "span")) == original
        # Hold the answer to the new span SET after the radio applied it.
        send = radio._send_civ_raw
        held = asyncio.Event()
        release = asyncio.Event()

        async def held_span_ack(payload, **kwargs):
            response = await send(payload, **kwargs)
            frame = parse_civ_frame(payload)
            if (
                frame.command == 0x27
                and frame.sub == 0x15
                and frame.data == b"\x00" + bcd_encode(250_000)
            ):
                held.set()
                await release.wait()
            return response

        monkeypatch.setattr(radio, "_send_civ_raw", held_span_ack)
        widen = asyncio.create_task(
            _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 500_000), 475, 20)
            )
        )
        await asyncio.wait_for(held.wait(), 5)
        assert sim.views[0] == (2, new, 1)
        # The radio already streams the new width: a report of the new span.
        assert await await_until(lambda: session._applied.get((0, "span")) == new)
        # An older waveform at the old width finishes assembling late.
        sim.views[0] = (2, old, 1)
        assert await await_until(lambda: session._applied.get((0, "span")) == old)
        sim.views[0] = (2, new, 1)
        assert await await_until(lambda: session._applied.get((0, "span")) == new)
        release.set()
        await asyncio.wait_for(widen, 5)
        assert session._desired.get((0, "span")) == new
        assert session._previous.get((0, "span")) == original
        sim.civ_frames_received.clear()
        await session.reconcile()
        assert [w for w in _viewport_writes(sim) if w.sub == 0x15] == []
        widened = await _frame_with(ws, (14_100_000 - 250_000, 14_100_000 + 250_000))
        assert (widened.coverage_start_hz, widened.coverage_end_hz) == (
            14_100_000 - 250_000,
            14_100_000 + 250_000,
        )
        assert [w for w in _viewport_writes(sim) if w.sub == 0x15] == []
        # The last unsubscribe restores the captured original span.
        await ws.close()
        assert await await_until(lambda: not session.active)
        assert sim.views[0] == (2, original, 1)


async def test_restore_keeps_span_adopted_after_span_rewrite(windowed_sdk, monkeypatch):
    """Pre-review round 1 (P2): an external span change landing BETWEEN two
    restore writes (after the span rewrite, before the held mode write)
    survives — the restore loop must not write the stale span over the
    adopted value once the adoption has landed."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    # SCROLL-C start: the follow subscribe writes ONLY the covering span
    # preset (#4565 — no 0x14 mode flip, the radio stays in SCROLL-C and
    # the grant is the radio-reported window), so an explicit CENTER
    # switch captures mode AND span for restoration, and the restore
    # loop's mode write (held below) leaves a window for the external
    # span change to land mid-restore.
    sim.views[0] = (2, 3, 1)
    async with web(radio) as (_, client, base, protocols, _):
        ws = await client.ws_connect(base + "/api/v1/scope", protocols=protocols)
        await _subscribe(
            ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
        )
        # The window simulator sizes the SCROLL-C window to the written
        # preset (#4565 pre-review item 5): VFO ± 50k, no longer the
        # arbitrary default edge pair the span-ignoring pump streamed.
        await _frame_with(ws, (14_050_000, 14_150_000))
        assert sim.views[0] == (2, presets.index(50_000), 1)
        assert [w.sub for w in _viewport_writes(sim)] == [0x15]
        await radio.set_scope_mode(0)
        await radio.set_scope_span(presets.index(50_000))
        await _frame_with(ws, (14_050_000, 14_150_000))
        assert sim.views[0] == (0, presets.index(50_000), 1)
        # Hold the restore's first write (mode) in flight so the external
        # span change lands while the restore loop is between iterations.
        send = radio._send_civ_raw

        async def slow_mode_ack(payload, **kwargs):
            frame = parse_civ_frame(payload)
            if (
                frame.command == 0x27
                and frame.sub == 0x14
                and frame.data == b"\x00\x02"
            ):
                await asyncio.sleep(0.3)
            return await send(payload, **kwargs)

        monkeypatch.setattr(radio, "_send_civ_raw", slow_mode_ack)
        await ws.close()
        session = radio.scope_session()
        assert await await_until(lambda: session.restoring)
        # The span rewrite (pre-subscribe snapshot) lands first — wait
        # for it, so the external change deterministically lands BETWEEN
        # the two restore writes.
        assert await await_until(lambda: sim.views[0] == (0, presets.index(25_000), 1))
        n15_before_push = len([w for w in _viewport_writes(sim) if w.sub == 0x15])
        sim.views[0] = (0, presets.index(100_000), 1)
        sim.push_scope_setting(b"\x15\x00" + bcd_encode(100_000))
        assert await await_until(lambda: not session.active)
        # The adopted span survives; restore wrote span (its pre-subscribe
        # value, before the adoption) then mode (back to SCROLL-C) — and
        # no stale 0x15 lands after the adoption point.
        assert sim.views[0] == (2, presets.index(100_000), 1)
        writes = [w for w in _viewport_writes(sim) if w.sub == 0x15]
        assert len(writes) == 2  # the S-C subscribe's + the early restore's
        assert writes[n15_before_push:] == []


def _dual_writes(sim) -> list:
    """CI-V single/dual SETs (0x27 0x13, one data byte) Core sent."""
    return [
        frame
        for raw in sim.civ_frames_received
        if (frame := parse_civ_frame(raw)).command == 0x27
        and frame.sub == 0x13
        and len(frame.data) == 1
    ]


async def test_external_dual_flip_flop_leaves_the_restore_set(windowed_sdk):
    """MOR-4392 last round: a SUB subscription acquires dual output (radio
    started single); an external single→dual flip-flop on the radio during
    the subscription is adopted through the production RX path, so closing
    the last real scope WebSocket writes NO 0x27 0x13 — never Core's stale
    single snapshot over the radio's own dual (owner rule «следовать за
    рацией», applied to the radio-global dual key)."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    radio._radio_state.sub.freq = 14_100_000
    sim._scope_13 = 0  # the radio starts in single-scope output
    sim.views[1] = (1, 3, 1)
    sim.windows[1] = (14_090_000, 14_110_000)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 1, FollowView("vfo", 50_000), 475, 20)
            )
            await _frame_with(ws, (14_090_000, 14_110_000))
            assert sim._scope_13 == 1  # the subscription acquired dual
            session = radio.scope_session()
            # External flip to single (front panel): adopted, dual leaves
            # the restore set.
            sim._scope_13 = 0
            sim.push_scope_setting(b"\x13\x00")
            assert await await_until(lambda: session._applied.get((0, "dual")) is False)
            # External flop back to dual: adopted too — the radio's choice.
            sim._scope_13 = 1
            sim.push_scope_setting(b"\x13\x01")
            assert await await_until(lambda: session._applied.get((0, "dual")) is True)
            writes_before_close = len(_dual_writes(sim))
        assert await await_until(lambda: not radio.scope_session().active)
        # The radio's own dual survives the close: teardown wrote no
        # 0x27 0x13 at all (in particular no 0x13 00 single over it).
        assert sim._scope_13 == 1
        assert len(_dual_writes(sim)) == writes_before_close


async def test_external_single_is_adopted_no_reassert_no_restore(windowed_sdk):
    """MOR-4392 last round: an external switch to single during a SUB
    subscription is adopted — no later reconcile re-asserts dual over it
    (a real MAIN follow request drives one) and teardown writes nothing
    for dual."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    radio._radio_state.sub.freq = 14_100_000
    sim._scope_13 = 0
    sim.views[1] = (1, 3, 1)
    sim.windows[1] = (14_090_000, 14_110_000)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 1, FollowView("vfo", 50_000), 475, 20)
            )
            await _frame_with(ws, (14_090_000, 14_110_000))
            assert sim._scope_13 == 1
            session = radio.scope_session()
            # External switch to single: adopted under the radio-global key.
            sim._scope_13 = 0
            sim.push_scope_setting(b"\x13\x00")
            assert await await_until(lambda: session._applied.get((0, "dual")) is False)
            acquisition_writes = len(_dual_writes(sim))
            # A MAIN follow request drives a real reconcile (span/mode for
            # MAIN): dual must NOT be re-asserted over the adopted single.
            async with client.ws_connect(
                base + "/api/v1/scope", protocols=protocols
            ) as main:
                await _subscribe(
                    main,
                    Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20),
                )
                await _frame_with(main, (14_050_000, 14_150_000))
                assert sim._scope_13 == 0
                assert len(_dual_writes(sim)) == acquisition_writes
        assert await await_until(lambda: not radio.scope_session().active)
        assert sim._scope_13 == 0
        assert len(_dual_writes(sim)) == acquisition_writes


async def test_native_width_regrant_carries_native_coverage(windowed_sdk):
    """Gate round 1 finding 4: the granted document at native width — the
    initial grant when the source learned the native width before granting
    (RadioScopeSource.feed_frame records it during the subscribe's
    set_view await), otherwise the width-change regrant the sender emits
    right before the first binary frame — carries the truthful native
    coverage, which exceeds the cropped view: the document shape the
    browser's parseScopeGranted accepts (mirrored in
    scope-wire-v2.test.ts). Which of the two orderings happens is a race,
    so the test accepts either."""
    radio, sim = windowed_sdk
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            # A ±30k request drives the ±50k preset: native coverage (±50k)
            # exceeds the granted view (±30k) from the initial grant on.
            initial = await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 60_000), 1024, 20)
            )
            assert (initial.view.start_hz, initial.view.end_hz) == (
                14_070_000,
                14_130_000,
            )
            assert initial.coverage is not None
            assert (initial.coverage.start_hz, initial.coverage.end_hz) == (
                14_050_000,
                14_150_000,
            )
            # Capture every granted document in wire order up to the first
            # binary frame.
            granted = [initial]
            native_width = None
            async with asyncio.timeout(5):
                while native_width is None:
                    message = await ws.receive(timeout=5)
                    if message.type is aiohttp.WSMsgType.TEXT:
                        granted.append(decode_scope_granted(message.data))
                    elif message.type is aiohttp.WSMsgType.BINARY:
                        native_width = decode_scope_frame_v2(message.data).width
            # The contract: the granted document whose width equals the
            # first binary frame's width — the initial grant if it already
            # matches, otherwise the regrant that precedes the frame —
            # carries the native coverage.
            native_grant = next(
                document for document in granted if document.width == native_width
            )
            assert (native_grant.view.start_hz, native_grant.view.end_hz) == (
                14_070_000,
                14_130_000,
            )
            assert native_grant.coverage is not None
            assert (
                native_grant.coverage.start_hz,
                native_grant.coverage.end_hz,
            ) == (
                14_050_000,
                14_150_000,
            )
            # Native coverage sits strictly outside the cropped view.
            assert native_grant.coverage.start_hz < native_grant.view.start_hz
            assert native_grant.coverage.end_hz > native_grant.view.end_hz


async def test_reconnect_without_external_change_keeps_span_restore(windowed_sdk):
    """Gate round 2 P1 (fix-4565): a follow subscription drives span 3→4
    (capturing 3); a connection loss invalidates the session; the
    reconnect reports the unchanged span 4 through the production RX
    path — closing the last real scope WebSocket must still restore
    the original span 3 (an unchanged report is not a superseding
    external change)."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    assert (presets.index(50_000), presets.index(100_000)) == (4, 5)
    sim.views[0] = (0, 3, 1)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            await _frame_with(ws, (14_050_000, 14_150_000))
            assert sim.views[0][1] == 4
            session = radio.scope_session()
            assert session._previous.get((0, "span")) == 3
            # Connection loss: the server invalidates the session, which
            # clears the last-applied comparison values.
            await session.invalidate()
            assert session._applied.get((0, "span")) is None
            # Reconnect with NO external change: the initial-state read
            # (0x27 0x15 report) and the streamed waveform observations
            # both report the unchanged span 4 through the production
            # RX path.
            sim.push_scope_setting(b"\x15\x00" + bcd_encode(50_000))
            assert await await_until(lambda: session._applied.get((0, "span")) == 4)
            await _frame_with(ws, (14_050_000, 14_150_000))
            assert session._previous.get((0, "span")) == 3
            # Reconnect completion re-arms readiness (the server scope
            # re-enable); the unchanged span must not drive a rewrite.
            await session.set_ready(True)
            span_sets_before_close = len(
                [w for w in _viewport_writes(sim) if w.sub == 0x15]
            )
        assert await await_until(lambda: not radio.scope_session().active)
        assert sim.views[0][1] == 3
        restores = [
            w for w in _viewport_writes(sim)[span_sets_before_close:] if w.sub == 0x15
        ]
        assert len(restores) == 1


async def test_reconnect_external_change_releases_span_restore(windowed_sdk):
    """Gate round 2 P1 mirror (fix-4565): same connection loss, but the
    operator moves the radio to span 5 during the outage — the
    reconnect report differs from Core's last applied span, so the
    snapshot is released and closing the last real scope WebSocket
    writes no stale span 3 over the radio's own span."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    assert (presets.index(50_000), presets.index(100_000)) == (4, 5)
    sim.views[0] = (0, 3, 1)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
            )
            await _frame_with(ws, (14_050_000, 14_150_000))
            assert sim.views[0][1] == 4
            session = radio.scope_session()
            assert session._previous.get((0, "span")) == 3
            await session.invalidate()
            # External change DURING the outage (front panel): the radio
            # now carries span 5.
            sim.views[0] = (0, 5, 1)
            sim.push_scope_setting(b"\x15\x00" + bcd_encode(100_000))
            assert await await_until(lambda: session._applied.get((0, "span")) == 5)
            await session.set_ready(True)
            span_sets_before_close = len(
                [w for w in _viewport_writes(sim) if w.sub == 0x15]
            )
        assert await await_until(lambda: not radio.scope_session().active)
        assert sim.views[0][1] == 5
        assert (
            len([w for w in _viewport_writes(sim) if w.sub == 0x15])
            == span_sets_before_close
        )


async def test_native_width_known_at_subscribe_needs_no_regrant(windowed_sdk):
    """The other ordering, deterministically: a receiver-0 frame reaches
    RadioScopeSource.feed_frame BEFORE the subscription grant is computed
    (a first subscription streams one frame, then closes — the learned
    native width survives the stream stop, unlike the remembered
    coverage). The second subscription's initial grant is then ALREADY at
    native width and ScopeHandler._sender, which only re-sends the granted
    document on a width change, emits no regrant: the first post-grant
    wire message is the binary frame itself."""
    radio, sim = windowed_sdk
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(
            base + "/api/v1/scope", protocols=protocols
        ) as first:
            await _subscribe(
                first, Subscription("radio", 0, FollowView("vfo", 60_000), 1024, 20)
            )
            native_width = (await _frame_with(first, (14_070_000, 14_130_000))).width
        assert await await_until(lambda: not radio.scope_session().active)
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            initial = await _subscribe(
                ws, Subscription("radio", 0, FollowView("vfo", 60_000), 1024, 20)
            )
            # The grant is computed with the native width already known:
            # native width and native coverage from the start.
            assert initial.width == native_width
            assert (initial.view.start_hz, initial.view.end_hz) == (
                14_070_000,
                14_130_000,
            )
            assert initial.coverage is not None
            assert (initial.coverage.start_hz, initial.coverage.end_hz) == (
                14_050_000,
                14_150_000,
            )
            assert initial.coverage.start_hz < initial.view.start_hz
            assert initial.coverage.end_hz > initial.view.end_hz
            # No width-change regrant is required: the first wire message
            # after the grant is the binary frame, at the same width.
            message = await ws.receive(timeout=5)
            assert message.type is aiohttp.WSMsgType.BINARY
            assert decode_scope_frame_v2(message.data).width == native_width


# ── MOR-4532 PR-A: the grant echoes requestSeq and carries requestState ──
# The client's requestSeq is echoed and ordered, never interpreted; the
# grant (and only the grant) says when that request is settled. Settling
# reads ScopeSession's existing facts: the span write's outcome and the
# first frame after its answer window closed (a muted receiver streams no
# frame, which makes "no frame yet" deterministic).


def _request(request_seq, span_hz):
    return Subscription(
        "radio", 0, FollowView("vfo", span_hz), 475, 20, request_seq=request_seq
    )


async def _grants(ws, until, timeout=10):
    """Every granted document, in arrival order, until ``until(grant)``."""
    grants = []
    async with asyncio.timeout(timeout):
        while True:
            message = await ws.receive(timeout=5)
            if message.type is not aiohttp.WSMsgType.TEXT:
                continue
            assert '"error"' not in message.data, message.data
            grants.append(decode_scope_granted(message.data))
            if until(grants[-1]):
                return grants


def _settled(request_seq):
    return lambda grant: (
        (grant.request_seq, grant.request_state)
        == (
            request_seq,
            "settled",
        )
    )


async def _settle_first_request(ws):
    await ws.send_str(encode_scope_subscription(_request(1, 100_000)))
    await _grants(ws, _settled(1))
    await _frame_with(ws, (14_050_000, 14_150_000))


async def _mute(sim, receiver=0):
    sim.muted.add(receiver)
    # Let a frame already on the wire land before the request goes out.
    await asyncio.sleep(0.3)


def _span_sets(sim):
    return [w for w in _viewport_writes(sim) if w.sub == 0x15]


async def _texts_until_frames(ws, view, count, timeout=10):
    """Every text document received until ``count`` frames show ``view``."""
    documents = []
    async with asyncio.timeout(timeout):
        while count:
            message = await ws.receive(timeout=5)
            if message.type is aiohttp.WSMsgType.TEXT:
                documents.append(json.loads(message.data))
            elif message.type is aiohttp.WSMsgType.BINARY:
                frame = decode_scope_frame_v2(message.data)
                if (frame.view_start_hz, frame.view_end_hz) == view:
                    count -= 1
    return documents


@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-705"], indirect=True)
async def test_request_seq_acked_span_write_settles_on_the_next_frame(windowed_sdk):
    """Change C, ACK: the acknowledged write alone does not settle the
    request; the first frame after the answer does, and the grant that says
    so precedes the frames at the requested width."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _settle_first_request(ws)
            await _mute(sim)
            sim.civ_frames_received.clear()
            await ws.send_str(encode_scope_subscription(_request(2, 500_000)))
            (first,) = await _grants(ws, lambda grant: True)
            assert (first.request_seq, first.request_state) == (2, "pending")
            assert sim.views[0][1] == presets.index(250_000)
            sim.muted.discard(0)
            grants = await _grants(ws, _settled(2))
            assert {grant.request_seq for grant in grants} == {2}
            await _frame_with(ws, (14_100_000 - 250_000, 14_100_000 + 250_000))
            assert len(_span_sets(sim)) == 1


@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-705"], indirect=True)
async def test_request_seq_lost_ack_settles_on_the_first_frame_after_the_window(
    windowed_sdk, monkeypatch
):
    """Change C, uncertain: the span SET ends without an answer and the
    radio stays on the old span. The first frame after that write's answer
    window settles the request, and the geometry shows the old span."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    send = radio._send_civ_raw

    async def lost_span_ack(payload, **kwargs):
        frame = parse_civ_frame(payload)
        if frame.command == 0x27 and frame.sub == 0x15 and len(frame.data) > 1:
            raise TimeoutError("lost ACK")
        return await send(payload, **kwargs)

    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _settle_first_request(ws)
            await _mute(sim)
            monkeypatch.setattr(radio, "_send_civ_raw", lost_span_ack)
            await ws.send_str(encode_scope_subscription(_request(2, 500_000)))
            (first,) = await _grants(ws, lambda grant: True)
            assert (first.request_seq, first.request_state) == (2, "pending")
            sim.muted.discard(0)
            await _grants(ws, _settled(2))
            await _frame_with(ws, (14_050_000, 14_150_000))


@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-705"], indirect=True)
async def test_request_seq_refused_span_write_is_settled_in_its_grant(
    windowed_sdk, monkeypatch
):
    """Change C, refusal: the radio refuses the span SET, so the grant is
    settled at once (no frame needed) and nothing retries the write."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    dispatch = sim._dispatch_civ

    def refuse_span(cmd, payload, from_addr):
        if cmd == 0x27 and payload[:1] == b"\x15" and len(payload) > 2:
            return sim._civ_nak(from_addr, sim._radio_addr)
        return dispatch(cmd, payload, from_addr)

    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _settle_first_request(ws)
            await _mute(sim)
            monkeypatch.setattr(sim, "_dispatch_civ", refuse_span)
            sim.civ_frames_received.clear()
            await ws.send_str(encode_scope_subscription(_request(2, 500_000)))
            (first,) = await _grants(ws, lambda grant: True)
            assert (first.request_seq, first.request_state) == (2, "settled")
            sim.muted.discard(0)
            await _frame_with(ws, (14_050_000, 14_150_000))
            assert len(_span_sets(sim)) == 1


async def test_request_seq_fast_requests_are_answered_in_order(windowed_sdk):
    """Change B: requests 5 and 6 sent before any grant. Grants never go
    back to an older request, and request 6 ends settled at its width."""
    radio, sim = windowed_sdk
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await ws.send_str(encode_scope_subscription(_request(5, 200_000)))
            await ws.send_str(encode_scope_subscription(_request(6, 500_000)))
            grants = await _grants(ws, _settled(6))
            sequence = [grant.request_seq for grant in grants]
            assert sequence == sorted(sequence) and set(sequence) <= {5, 6}
            await _frame_with(ws, (14_100_000 - 250_000, 14_100_000 + 250_000))


async def test_request_seq_another_clients_span_change_leaves_this_grant_settled(
    windowed_sdk, monkeypatch
):
    """Another client's span request (its ACK held, frames flowing) changes
    this client's geometry but never its grant's requestSeq or state."""
    radio, sim = windowed_sdk
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    send = radio._send_civ_raw
    held = asyncio.Event()
    release = asyncio.Event()

    async def held_span_ack(payload, **kwargs):
        response = await send(payload, **kwargs)
        frame = parse_civ_frame(payload)
        if (
            frame.command == 0x27
            and frame.sub == 0x15
            and frame.data == b"\x00" + bcd_encode(25_000)
        ):
            held.set()
            await release.wait()
        return response

    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as a:
            await a.send_str(encode_scope_subscription(_request(1, 200_000)))
            await _grants(a, _settled(1))
            await _frame_with(a, (14_000_000, 14_200_000))
            monkeypatch.setattr(radio, "_send_civ_raw", held_span_ack)
            async with client.ws_connect(
                base + "/api/v1/scope", protocols=protocols
            ) as b:
                narrow = asyncio.create_task(_subscribe(b, _request(9, 50_000)))
                await asyncio.wait_for(held.wait(), 5)
                # A's frames keep flowing while B's write awaits its answer.
                seen = await _texts_until_frames(a, (14_075_000, 14_125_000), 3)
                release.set()
                await asyncio.wait_for(narrow, 5)
                seen += await _texts_until_frames(a, (14_075_000, 14_125_000), 3)
    # Any grant A received meanwhile is still request 1, settled.
    for document in seen:
        grant = decode_scope_granted(json.dumps(document))
        assert (grant.request_seq, grant.request_state) == (1, "settled")


async def test_request_seq_tx_deferred_span_stays_pending_until_applied(
    windowed_sdk,
):
    """Change C, TX: a span request during transmit is deferred, so its
    grant stays pending while frames flow; after TX the write goes out and
    the next frame settles it."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _settle_first_request(ws)
            # The radio's PTT polls must keep answering ON while transmitting.
            sim._ptt_state = True
            ptt_reply(radio, True)
            sim.civ_frames_received.clear()
            await ws.send_str(encode_scope_subscription(_request(2, 500_000)))
            (first,) = await _grants(ws, lambda grant: True)
            assert (first.request_seq, first.request_state) == (2, "pending")
            # Frames at the old width flow and settle nothing.
            during = await _texts_until_frames(ws, (14_050_000, 14_150_000), 3)
            assert all(
                (d["requestSeq"], d["requestState"]) == (2, "pending") for d in during
            )
            assert _span_sets(sim) == []
            sim._ptt_state = False
            ptt_reply(radio, False)
            await _grants(ws, _settled(2))
            await _frame_with(ws, (14_100_000 - 250_000, 14_100_000 + 250_000))
            assert len(_span_sets(sim)) == 1


async def test_request_seq_absent_leaves_the_grant_document_unchanged(windowed_sdk):
    """Change D: a subscription without requestSeq gets the grant document
    exactly as before — no requestSeq, no requestState."""
    radio, sim = windowed_sdk
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await ws.send_str(
                encode_scope_subscription(
                    Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
                )
            )
            documents = await _texts_until_frames(ws, (14_050_000, 14_150_000), 1)
            assert documents
            for document in documents:
                assert set(document) == {
                    "type",
                    "sourceId",
                    "receiver",
                    "view",
                    "coverage",
                    "width",
                    "fps",
                }


@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-705"], indirect=True)
async def test_request_seq_paused_request_is_settled_without_frames(windowed_sdk):
    """A tagged maxFps 0 (passive) request replaces an outstanding span
    request before its settling frame. A paused subscription receives no
    frames and the passive request performs no view action, so its grant
    is settled at once; it must never wait for a frame that cannot come."""
    radio, sim = windowed_sdk
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _settle_first_request(ws)
            await _mute(sim)
            await ws.send_str(encode_scope_subscription(_request(2, 500_000)))
            (outstanding,) = await _grants(ws, lambda grant: True)
            assert (outstanding.request_seq, outstanding.request_state) == (
                2,
                "pending",
            )
            paused = Subscription(
                "radio", 0, FollowView("vfo", 500_000), 475, 0, request_seq=3
            )
            await ws.send_str(encode_scope_subscription(paused))
            (final,) = await _grants(ws, lambda grant: True)
            assert (final.request_seq, final.request_state) == (3, "settled")


async def test_request_seq_buffered_delivery_never_reverses_a_settled_grant(
    windowed_sdk, monkeypatch
):
    """A TX-deferred replacement at a low frame rate (0.5 fps): while the old
    subscription's release is held, the new one buffers a pending delivery,
    then TX ends and the request settles. The initial grant goes out
    settled; the older buffered delivery must not send pending after it."""
    radio, sim = windowed_sdk
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    entered = asyncio.Event()
    release = asyncio.Event()
    close = HubSubscription.close

    async def held_release(self):
        if self._request.request_seq == 1:
            entered.set()
            await release.wait()
        await close(self)

    async with web(radio) as (server, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _settle_first_request(ws)
            sim._ptt_state = True
            ptt_reply(radio, True)
            monkeypatch.setattr(HubSubscription, "close", held_release)
            await ws.send_str(
                encode_scope_subscription(
                    Subscription(
                        "radio", 0, FollowView("vfo", 500_000), 475, 0.5, request_seq=2
                    )
                )
            )
            await asyncio.wait_for(entered.wait(), 5)
            source = server.scope_hub._sources["radio"]
            (new,) = [c for c in source.clients if c._request.request_seq == 2]
            # The first frame during TX is buffered as a pending delivery.
            assert await await_until(
                lambda: (
                    new._latest is not None
                    and new._latest[0].request_state == "pending"
                )
            )
            sim._ptt_state = False
            ptt_reply(radio, False)
            assert await await_until(lambda: new.granted.request_state == "settled")
            release.set()
            seen = []
            async with asyncio.timeout(10):
                while True:
                    message = await ws.receive(timeout=5)
                    if message.type is aiohttp.WSMsgType.TEXT:
                        seen.append(json.loads(message.data))
                    elif message.type is aiohttp.WSMsgType.BINARY:
                        first_frame = decode_scope_frame_v2(message.data)
                        break
            # The buffered pre-settle delivery is retired, not relabelled:
            # the first frame after the settled grant has the settled width.
            assert (first_frame.view_start_hz, first_frame.view_end_hz) == (
                14_100_000 - 250_000,
                14_100_000 + 250_000,
            )
            states = [d["requestState"] for d in seen if d.get("requestSeq") == 2]
            assert states and states[0] == "settled"
            assert "pending" not in states[states.index("settled") :]


# ── MOR-4532 write-back rule: only a NEW requestSeq applies a follow width ──
# A re-sent subscription with the same requestSeq (resize, visibility, fps)
# carries its follow width as context; requestSeq 0 (a fresh connection) is
# never a request. Neither may write a span the radio did not get asked for.


def _resize(request_seq, span_hz, width):
    return Subscription(
        "radio", 0, FollowView("vfo", span_hz), width, 20, request_seq=request_seq
    )


@pytest.mark.parametrize("mode", [0, 2], ids=["center", "scroll-c"])
@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-705"], indirect=True)
async def test_request_seq_resize_after_external_narrow_writes_no_span(
    windowed_sdk, mode
):
    """External narrow to 50k, then the panel resizes: the same requestSeq
    re-sent with another pixel width writes no span; the view stays 50k.
    In CENTER and SCROLL-C (the two modes where a follow width writes)."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await ws.send_str(encode_scope_subscription(_request(1, 200_000)))
            await _grants(ws, _settled(1))
            await _frame_with(ws, (14_000_000, 14_200_000))
            if mode == 2:
                await radio.set_scope_mode(2)
                assert sim.views[0][0] == 2
            sim.views[0] = (mode, presets.index(25_000), 1)
            await _frame_with(ws, (14_075_000, 14_125_000))
            sim.civ_frames_received.clear()
            await ws.send_str(encode_scope_subscription(_resize(1, 200_000, 300)))
            await _grants(ws, lambda grant: grant.request_seq == 1)
            assert _span_sets(sim) == []
            await _frame_with(ws, (14_075_000, 14_125_000))
            assert _span_sets(sim) == []
            assert sim.views[0][1] == presets.index(25_000)


@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-705"], indirect=True)
async def test_request_seq_zero_on_reconnect_writes_no_span(windowed_sdk):
    """A fresh connection starts at requestSeq 0, which is never a request:
    reconnecting while the radio sits on an externally set span writes no
    span, and the view follows the radio."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await ws.send_str(encode_scope_subscription(_request(1, 200_000)))
            await _grants(ws, _settled(1))
            sim.views[0] = (0, presets.index(25_000), 1)
            await _frame_with(ws, (14_075_000, 14_125_000))
        assert await await_until(lambda: not radio.scope_session().active)
        sim.civ_frames_received.clear()
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await ws.send_str(encode_scope_subscription(_request(0, 200_000)))
            (first,) = await _grants(ws, lambda grant: True)
            assert (first.request_seq, first.request_state) == (0, "settled")
            await _frame_with(ws, (14_075_000, 14_125_000))
            assert _span_sets(sim) == []
            assert sim.views[0][1] == presets.index(25_000)


@pytest.mark.parametrize("windowed_sdk", ["IC-7610", "IC-705"], indirect=True)
async def test_request_seq_stale_frame_after_ack_never_writes_back(windowed_sdk):
    """MOR-4507 residual as display-only: one old-width frame lands right
    after the span ACK (and nothing newer follows yet). Re-sending the same
    requestSeq (a resize) must not write the requested span back."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    old, new = presets.index(50_000), presets.index(250_000)
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as ws:
            await _settle_first_request(ws)
            await ws.send_str(encode_scope_subscription(_request(2, 500_000)))
            await _grants(ws, _settled(2))
            assert sim.views[0][1] == new
            session = radio.scope_session()
            # One stale frame at the old width, then the stream pauses.
            sim.views[0] = (0, old, 1)
            assert await await_until(lambda: session._applied.get((0, "span")) == old)
            await _mute(sim)
            sim.views[0] = (0, new, 1)
            sim.civ_frames_received.clear()
            await ws.send_str(encode_scope_subscription(_resize(2, 500_000, 300)))
            await _grants(ws, lambda grant: grant.request_seq == 2)
            assert _span_sets(sim) == []
            sim.muted.discard(0)
            await _frame_with(ws, (14_100_000 - 250_000, 14_100_000 + 250_000))
            assert _span_sets(sim) == []


async def test_request_seq_resent_and_zero_grants_stay_settled_while_another_writes(
    windowed_sdk,
):
    """While another client's span request is still pending (its frame not
    yet arrived), a re-sent settled requestSeq and a fresh connection's
    requestSeq 0 are both granted settled: settled is final per requestSeq,
    and 0 is never a pending request."""
    radio, sim = windowed_sdk
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    async with web(radio) as (_, client, base, protocols, _):
        async with client.ws_connect(base + "/api/v1/scope", protocols=protocols) as a:
            await _settle_first_request(a)
            await _mute(sim)
            async with client.ws_connect(
                base + "/api/v1/scope", protocols=protocols
            ) as b:
                (other,) = await _grants_after(b, _request(9, 500_000))
                assert (other.request_seq, other.request_state) == (9, "pending")
                (resent,) = await _grants_after(a, _resize(1, 100_000, 300))
                assert (resent.request_seq, resent.request_state) == (1, "settled")
                async with client.ws_connect(
                    base + "/api/v1/scope", protocols=protocols
                ) as c:
                    (fresh,) = await _grants_after(c, _request(0, 100_000))
                    assert (fresh.request_seq, fresh.request_state) == (0, "settled")


async def _grants_after(ws, subscription):
    await ws.send_str(encode_scope_subscription(subscription))
    return await _grants(ws, lambda grant: True)
