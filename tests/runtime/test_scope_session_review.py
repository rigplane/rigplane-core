"""R5 review regressions: real Core RX, SDK and local WebSocket seams."""

import asyncio
from contextlib import asynccontextmanager

import aiohttp
import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.exceptions import CommandError, TimeoutError
from rigplane.core.types import ScopeFixedEdge, bcd_encode
from rigplane.radio import IcomRadio
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
    in FIX/SCROLL-F or center ± span preset in CENTER. A receiver in
    ``muted`` does not stream (e.g. a radio streaming only SUB at startup).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.windows: dict[int, tuple[int, int]] = {}
        self.muted: set[int] = set()

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
            else:
                # Every other mode carries edges (ScopeAssembler expands
                # only mode 0).
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
async def windowed_sdk():
    sim = _ScopeWindowSimulator(scope_stream=True, dual_receiver=True)
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
            # External FIX entry (front panel): the follow grant clips to the
            # fixed window, no radio write leaves Core.
            sim.windows[0] = (14_075_000, 14_175_000)
            sim.views[0] = (1, 4, 1)
            clipped = await _frame_with(ws, (14_075_000, 14_150_000))
            assert (clipped.coverage_start_hz, clipped.coverage_end_hz) == (
                14_075_000,
                14_150_000,
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


async def test_restore_skips_span_adopted_during_teardown(windowed_sdk, monkeypatch):
    """Pre-review round 1 (P2): an external span change landing BETWEEN two
    restore writes leaves the restore set in time — the copied restore
    loop must not write the stale span over the adopted value."""
    radio, sim = windowed_sdk
    sim.capture_civ_frames = True
    sim.set_frequency(14_100_000)
    radio._radio_state.main.freq = 14_100_000
    presets = radio._profile.scope_span_presets_hz
    # SCROLL-C start: the follow subscribe's set_center captures mode AND
    # span for restoration, so the restore loop's mode write (held below)
    # leaves a window for the external span change to land mid-restore.
    sim.views[0] = (2, 3, 1)
    async with web(radio) as (_, client, base, protocols, _):
        ws = await client.ws_connect(base + "/api/v1/scope", protocols=protocols)
        await _subscribe(
            ws, Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
        )
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
        await asyncio.sleep(0.05)
        sim.views[0] = (0, presets.index(100_000), 1)
        sim.push_scope_setting(b"\x15\x00" + bcd_encode(100_000))
        assert await await_until(lambda: not session.active)
        # The adopted span survives; restore wrote only mode (its owned
        # setting, back to the pre-subscribe SCROLL-C) — no stale 0x15.
        assert sim.views[0] == (2, presets.index(100_000), 1)
        assert (
            len([w for w in _viewport_writes(sim) if w.sub == 0x15]) == 1
        )  # only the subscribe's own span write


async def test_native_width_regrant_carries_native_coverage(windowed_sdk):
    """Gate round 1 finding 4: the initial follow grant and the first
    native-width regrant carry the truthful native coverage, which exceeds
    the cropped view — the document shape the browser's parseScopeGranted
    accepts (mirrored in scope-wire-v2.test.ts)."""
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
            # The sender emits the width-change regrant (text) right before
            # the first binary frame; capture both in wire order.
            regrant = None
            native_width = None
            async with asyncio.timeout(5):
                while True:
                    message = await ws.receive(timeout=5)
                    if message.type is aiohttp.WSMsgType.TEXT:
                        candidate = decode_scope_granted(message.data)
                        if candidate.width != initial.width:
                            regrant = candidate
                    elif message.type is aiohttp.WSMsgType.BINARY:
                        native_width = decode_scope_frame_v2(message.data).width
                        if regrant is not None:
                            break
            assert regrant is not None
            assert regrant.width == native_width
            assert (regrant.view.start_hz, regrant.view.end_hz) == (
                14_070_000,
                14_130_000,
            )
            assert regrant.coverage is not None
            assert (regrant.coverage.start_hz, regrant.coverage.end_hz) == (
                14_050_000,
                14_150_000,
            )
            # Native coverage sits strictly outside the cropped view.
            assert regrant.coverage.start_hz < regrant.view.start_hz
            assert regrant.coverage.end_hz > regrant.view.end_hz
