"""Server-side TX frame accounting and the operator toast (MOR-4602).

The two silent server-side TX failure classes from the mic error-routing
audit:

* sustained dropping at the codec/push boundary — no decoder, decode
  failure, send-to-radio error: the browser keeps sending, the radio is
  keyed, and no modulation reaches the air while nothing on screen says
  so;
* a transmission that ends with zero frames received (the 44.1 kHz
  encoder class, MOR-4594): the ``TX stopped`` log line carried no frame
  count, so a support report showed the failure only by the absence of
  ``TX frame #1``.

One mechanism only: the existing ``send_notification_to_session`` toast
path, one toast per transmission per class, plus a frame count on the
stop log line. The toast targets the audio channel's session control
channel, resolved through the real ControllerAuthority (each channel
attaches with its own label; the control channel registers for targeted
sends under its own). No clocks anywhere: the sustained-drop streak is
counted in frames.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from _order_sensitive_radios import LanLikeRadio
from test_managed_tx_authority import authority
from test_web_controller import request

from rigplane.audio.session import AudioSession
from rigplane.core._bounded_queue import BoundedQueue
from rigplane.runtime.controller_authority import (
    ControllerAuthority,
    ControllerTicket,
)
from rigplane.web import server as server_module
from rigplane.web.handlers import AudioHandler
from rigplane.web.handlers.audio import TX_DROP_NOTIFY_AFTER
from rigplane.web.protocol import (
    AUDIO_CODEC_OPUS,
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_TX,
)
from rigplane.web.server import WebConfig, WebServer

_LOGGER = "rigplane.web.handlers.audio"


class _SessionLanRadio(LanLikeRadio):
    """LAN-like stub + radio-owned session singleton (MOR-579 shape)."""

    capabilities = {"audio", "tx"}
    backend_id = "rigplane"
    has_usb_audio = True
    audio_sample_rate = 48000

    def __init__(self) -> None:
        super().__init__()
        self.pushed: list[bytes] = []
        self._audio_session: AudioSession | None = None

    @property
    def audio_session(self) -> AudioSession:
        if self._audio_session is None:
            self._audio_session = AudioSession(self)
        return self._audio_session

    async def push_tx(self, audio_data: bytes) -> None:
        await super().push_tx(audio_data)  # raises unless TX is armed
        self.pushed.append(audio_data)


class _PushRefusingRadio(_SessionLanRadio):
    """Radio whose TX push path always fails (send-to-radio error class)."""

    async def push_tx(self, audio_data: bytes) -> None:
        raise RuntimeError("send-to-radio failure")


class _CapturingWs:
    """WS double capturing the JSON text frames the handler sends."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.recv = AsyncMock()
        self.send_binary = AsyncMock()

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


class _CapturingServer:
    """Server double capturing targeted session notifications."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_notification_to_session(
        self,
        session_id: str,
        level: str,
        message: str,
        category: str = "system",
        *,
        code: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> bool:
        self.sent.append(
            {
                "session_id": session_id,
                "level": level,
                "message": message,
                "category": category,
                "code": code,
                "params": params,
            }
        )
        return True


@dataclass
class _Channels:
    """A real authority with the native primary, the browser auxiliary
    control channel and the audio channel of one session."""

    ctrl: ControllerAuthority
    key: str
    control: ControllerTicket
    browser: ControllerTicket
    audio: ControllerTicket


async def _noop_cleanup() -> None:
    return None


@pytest.fixture
async def controller_station() -> Any:
    """A real WebServer with its controller authority installed and the
    managed-TX port wired (the ``test_web_controller.station`` shape), so
    the HTTP acquisition and the audio gate behave as in production."""
    import time as _time

    from rigplane.core.state_pipeline_contracts import (
        Observation,
        SourceMetadata,
    )
    from rigplane.core.tx_observation import OBSERVED_PTT_PATH, ObservedPtt
    from test_managed_tx_web_projection import _GatePort

    managed, *_ = authority()
    server = WebServer(None, WebConfig(radio_model="IC-7610"))
    server._production_managed_tx_port = _GatePort(managed)
    server._controller_installed = True
    server._controller_enforcement_ready = True
    server.command_state_store.apply_current(
        Observation(
            path=OBSERVED_PTT_PATH,
            value=ObservedPtt.OFF,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=_time.monotonic(),
            max_age=60,
        )
    )
    yield server, managed
    server._controller.revoke()
    await server._controller.settle()
    await managed.close()


@pytest.fixture
async def local_channels() -> Any:
    """Local-mode multi-client session: one key, native primary (the
    companion), browser auxiliary control, audio auxiliary — the shape
    the desktop-PTT case produces."""
    ctrl = ControllerAuthority(ready=lambda: True, cleanup=_noop_cleanup)
    grant = ctrl.acquire("browser")
    key = grant["controller_key"]
    control = ctrl.attach(key, "primary", "controller-test-native")
    browser = ctrl.attach(key, "auxiliary", "controller-test-browser")
    ctrl.add_channel(browser, lambda: None)
    audio = ctrl.attach(key, "auxiliary", "controller-test-audio")
    yield _Channels(ctrl, key, control, browser, audio)
    ctrl.revoke()
    await ctrl.settle()


@pytest.fixture
async def remote_channels() -> Any:
    """Remote mode: one exclusive key, global native control primary,
    browser auxiliary control, audio auxiliary."""
    ctrl = ControllerAuthority(ready=lambda: True, cleanup=_noop_cleanup)
    await ctrl.set_mode("remote")
    grant = ctrl.acquire("pro")
    key = grant["controller_key"]
    control = ctrl.attach(key, "primary", "controller-test-rnative")
    browser = ctrl.attach(key, "auxiliary", "controller-test-rbrowser")
    ctrl.add_channel(browser, lambda: None)
    audio = ctrl.attach(key, "auxiliary", "controller-test-raudio")
    yield _Channels(ctrl, key, control, browser, audio)
    ctrl.revoke()
    await ctrl.settle()


def _healthy_decoder(data: bytes) -> bytes:
    return b"pcm:" + data


def _raising_decoder(data: bytes) -> bytes:
    raise ValueError("decode failure")


class _FlippingGate:
    """TX gate that admits the first check and closes every later one."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> bool:
        self.calls += 1
        return self.calls == 1


def _make_handler(
    radio: _SessionLanRadio,
    *,
    opus_to_pcm: Any = None,
    channels: _Channels | None = None,
    server: Any = None,
    tx_gate: Any = None,
) -> tuple[AudioHandler, _CapturingWs]:
    """Handler with decoder availability injected, not discovered.

    ``opus_to_pcm=None`` means no transcoder exists; a callable is used
    as the decoder for every Opus frame. ``channels`` wires the real
    authority whose control channel the toast targets.
    """
    ws = _CapturingWs()
    handler = AudioHandler(
        ws,
        radio,
        None,
        tx_gate=tx_gate,
        controller=channels.ctrl if channels is not None else None,
        controller_ticket=channels.audio if channels is not None else None,
        server=server,
    )

    def _ensure() -> bool:
        if opus_to_pcm is None:
            handler._transcoder = None
            handler._transcoder_rate = 0
            return False
        handler._transcoder = SimpleNamespace(opus_to_pcm=opus_to_pcm)
        handler._transcoder_rate = radio.audio_sample_rate
        return True

    handler._ensure_tx_transcoder = _ensure  # type: ignore[method-assign]
    return handler, ws


def _tx_frame(codec: int, payload: bytes) -> bytes:
    return (
        bytes([MSG_TYPE_AUDIO_TX, codec]) + b"\x00" * (AUDIO_HEADER_SIZE - 2) + payload
    )


async def _start_tx(handler: AudioHandler) -> None:
    await handler._handle_control({"type": "audio_start", "direction": "tx"})


async def _stop_tx(handler: AudioHandler) -> None:
    await handler._handle_control({"type": "audio_stop", "direction": "tx"})


def _stop_lines(caplog) -> list[str]:
    return [
        record.message
        for record in caplog.records
        if record.levelno == logging.INFO and "TX stopped" in record.message
    ]


def test_every_emitted_tx_toast_code_has_a_catalog_entry_in_every_locale() -> None:
    """The server's toast codes are a contract with the frontend catalog
    (RP-ML-005): a code without ``core.toast.<code>`` renders the generic
    unknown fallback, hiding the cause the toast exists to name. This
    fails the day someone emits a new TX toast code without its entries."""
    import json as _json
    from pathlib import Path as _Path

    locales = _Path(__file__).resolve().parents[1] / "frontend/src/lib/i18n/locales"
    shipped = sorted(p.name for p in locales.glob("*.json"))
    assert shipped, "the locale catalog directory moved — update this test"
    for name in shipped:
        catalog = _json.loads((locales / name).read_text(encoding="utf-8"))
        for code in ("txAudioDropped", "txAudioNoFrames"):
            key = f"core.toast.{code}"
            assert catalog.get(key), f"{name} lacks {key}"


async def test_sustained_no_decoder_drops_send_one_operator_toast(
    local_channels, caplog
) -> None:
    """Every frame dropped with no decoder: exactly one toast to the
    session's CONTROL channel label, plus the dispatch trace in the log."""
    radio = _SessionLanRadio()
    server = _CapturingServer()
    handler, _ws = _make_handler(
        radio, opus_to_pcm=None, channels=local_channels, server=server
    )
    await _start_tx(handler)

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        for _ in range(TX_DROP_NOTIFY_AFTER + 5):
            await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_OPUS, b"opus"))

    assert radio.pushed == []
    assert len(server.sent) == 2, "one toast per control channel, per transmission"
    assert {note["session_id"] for note in server.sent} == {
        local_channels.control.session_id,
        local_channels.browser.session_id,
    }, "the browser auxiliary control channel is a target, not just the primary"
    note = server.sent[0]
    assert note["level"] == "error"
    assert note["code"] == "txAudioDropped"
    assert note["params"] == {"reason": "dropped_no_transcoder"}
    assert any(
        "TX operator toast" in record.message and "txAudioDropped" in record.message
        for record in caplog.records
    ), "the toast dispatch leaves a support-report trace"


async def test_sustained_decode_failures_send_one_operator_toast(
    local_channels,
) -> None:
    """A decoder that raises on every frame is the same operator-visible
    failure class, with its own reason token."""
    radio = _SessionLanRadio()
    server = _CapturingServer()
    handler, _ws = _make_handler(
        radio, opus_to_pcm=_raising_decoder, channels=local_channels, server=server
    )
    await _start_tx(handler)

    for _ in range(TX_DROP_NOTIFY_AFTER):
        await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_OPUS, b"opus"))

    assert radio.pushed == []
    assert [note["code"] for note in server.sent] == [
        "txAudioDropped",
        "txAudioDropped",
    ]
    assert server.sent[0]["params"] == {"reason": "dropped_transcode_failed"}


async def test_sustained_push_errors_send_one_operator_toast(
    local_channels,
) -> None:
    """A radio-side push failure on every frame is the third drop class."""
    radio = _PushRefusingRadio()
    server = _CapturingServer()
    handler, _ws = _make_handler(
        radio, opus_to_pcm=_healthy_decoder, channels=local_channels, server=server
    )
    await _start_tx(handler)

    for _ in range(TX_DROP_NOTIFY_AFTER):
        await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_OPUS, b"opus"))

    assert radio.pushed == []
    assert [note["code"] for note in server.sent] == [
        "txAudioDropped",
        "txAudioDropped",
    ]
    assert server.sent[0]["params"] == {"reason": "push_tx_error"}


async def test_broken_drop_streak_does_not_notify(local_channels) -> None:
    """Below-threshold streaks interrupted by a pushed frame never toast:
    the streak counts consecutive drops, not total drops."""
    radio = _SessionLanRadio()
    server = _CapturingServer()
    handler, _ws = _make_handler(
        radio, opus_to_pcm=None, channels=local_channels, server=server
    )
    await _start_tx(handler)

    for _ in range(TX_DROP_NOTIFY_AFTER - 1):
        await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_OPUS, b"opus"))
    await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_PCM16, b"pcm"))
    for _ in range(TX_DROP_NOTIFY_AFTER - 1):
        await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_OPUS, b"opus"))

    assert radio.pushed == [b"pcm"]
    assert server.sent == [], "intermittent drops with audio flowing are not sustained"


async def test_gate_withheld_frame_is_neither_pushed_nor_dropped(
    local_channels,
) -> None:
    """A frame the admission guard withholds between the outer gate check
    and submission counts as neither pushed nor dropped."""
    radio = _SessionLanRadio()
    server = _CapturingServer()
    gate = _FlippingGate()
    handler, _ws = _make_handler(
        radio,
        opus_to_pcm=_healthy_decoder,
        channels=local_channels,
        server=server,
        tx_gate=gate,
    )
    await _start_tx(handler)

    await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_PCM16, b"pcm"))

    assert gate.calls == 2, "outer check plus submission guard"
    assert radio.pushed == []
    assert handler._tx_frames_pushed == 0
    assert handler._tx_frames_received == 1
    assert server.sent == []


async def test_zero_frame_transmission_notifies_once_at_stop(
    local_channels, caplog
) -> None:
    """The 44.1 kHz class: TX opens and closes with no frame ever
    received — one toast at stop, and the stop log names the count."""
    radio = _SessionLanRadio()
    server = _CapturingServer()
    handler, _ws = _make_handler(
        radio, opus_to_pcm=_healthy_decoder, channels=local_channels, server=server
    )
    await _start_tx(handler)

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await _stop_tx(handler)
        await _stop_tx(handler)  # a duplicate stop must not toast again

    assert [note["code"] for note in server.sent] == [
        "txAudioNoFrames",
        "txAudioNoFrames",
    ]
    assert {note["session_id"] for note in server.sent} == {
        local_channels.control.session_id,
        local_channels.browser.session_id,
    }
    assert server.sent[0]["level"] == "error"
    assert _stop_lines(caplog) == [
        "audio: TX stopped (client request) frames=0 pushed=0"
    ]


async def test_healthy_transmission_logs_frame_count_without_toast(
    local_channels, caplog
) -> None:
    """A normal transmission keeps its toast silence and gains a count."""
    radio = _SessionLanRadio()
    server = _CapturingServer()
    handler, _ws = _make_handler(
        radio, opus_to_pcm=_healthy_decoder, channels=local_channels, server=server
    )
    await _start_tx(handler)
    for i in range(3):
        await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_PCM16, b"pcm-%d" % i))

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await _stop_tx(handler)

    assert server.sent == []
    assert _stop_lines(caplog) == [
        "audio: TX stopped (client request) frames=3 pushed=3"
    ]


async def test_duplicate_audio_start_keeps_the_transmission_totals(
    local_channels,
) -> None:
    """A duplicate ``audio_start`` on a live transmission is the same
    transmission: totals and the one-toast latch survive it."""
    radio = _SessionLanRadio()
    server = _CapturingServer()
    handler, _ws = _make_handler(
        radio, opus_to_pcm=_healthy_decoder, channels=local_channels, server=server
    )
    await _start_tx(handler)
    await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_PCM16, b"pcm"))

    await _start_tx(handler)  # duplicate start on the live transmission
    await _stop_tx(handler)

    assert server.sent == [], "one pushed frame means the transmission is not silent"
    assert handler._tx_frames_received == 1


async def test_accounting_resets_between_transmissions(local_channels, caplog) -> None:
    """Each transmission gets its own accounting: a second one is notified
    again even after the first already toasted."""
    radio = _SessionLanRadio()
    server = _CapturingServer()
    handler, _ws = _make_handler(
        radio, opus_to_pcm=None, channels=local_channels, server=server
    )

    await _start_tx(handler)
    for _ in range(TX_DROP_NOTIFY_AFTER):
        await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_OPUS, b"opus"))
    await _stop_tx(handler)

    await _start_tx(handler)  # fresh transmission, fresh counters
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await _stop_tx(handler)

    assert [note["code"] for note in server.sent] == [
        "txAudioDropped",
        "txAudioDropped",
        "txAudioNoFrames",
        "txAudioNoFrames",
    ]
    assert _stop_lines(caplog) == [
        "audio: TX stopped (client request) frames=0 pushed=0"
    ]


async def test_without_server_target_degrades_to_log_only(caplog) -> None:
    """A handler constructed without a server (direct construction, or a
    session with no live control channel) keeps today's log-only
    behavior: no crash, no second error channel."""
    radio = _SessionLanRadio()
    handler, _ws = _make_handler(radio, opus_to_pcm=None)
    await _start_tx(handler)

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        for _ in range(TX_DROP_NOTIFY_AFTER):
            await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_OPUS, b"opus"))
        await _stop_tx(handler)

    assert _stop_lines(caplog) == [
        "audio: TX stopped (client request) frames=%d pushed=0" % TX_DROP_NOTIFY_AFTER
    ]


async def test_toast_reaches_the_real_registered_control_queue(
    local_channels,
) -> None:
    """Local session end of the real seam: the toast lands as a wire
    notification on the queue registered under the BROWSER control
    channel's label — the auxiliary that holds the operator's toasts
    while a native controller holds the primary."""
    radio = _SessionLanRadio()
    server = WebServer(None, WebConfig(radio_model="IC-7300"))
    queue: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=100)
    initial = server.register_control_event_queue(
        queue, session_id=local_channels.browser.session_id
    )
    assert initial["type"] == "full", "registration returns the state baseline"

    handler, _ws = _make_handler(
        radio, opus_to_pcm=None, channels=local_channels, server=server
    )
    await _start_tx(handler)
    for _ in range(TX_DROP_NOTIFY_AFTER):
        await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_OPUS, b"opus"))

    frames = [queue.get_nowait() for _ in range(queue.qsize())]
    assert [f["type"] for f in frames] == ["notification"]
    assert frames[0] == {
        "type": "notification",
        "level": "error",
        "message": (
            "TX audio frames were dropped; microphone audio is not reaching the radio"
        ),
        "category": "audio",
        "code": "txAudioDropped",
        "params": {"reason": "dropped_no_transcoder"},
    }


async def test_remote_mode_toast_reaches_the_registered_control_channel(
    remote_channels,
) -> None:
    """Remote mode resolves the exclusive grant's control channels the
    same way."""
    radio = _SessionLanRadio()
    server = WebServer(None, WebConfig(radio_model="IC-7300"))
    queue: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=100)
    server.register_control_event_queue(
        queue, session_id=remote_channels.browser.session_id
    )

    handler, _ws = _make_handler(
        radio, opus_to_pcm=_healthy_decoder, channels=remote_channels, server=server
    )
    await _start_tx(handler)
    await _stop_tx(handler)

    notes = [
        f
        for f in [queue.get_nowait() for _ in range(queue.qsize())]
        if f["type"] == "notification"
    ]
    assert [n["code"] for n in notes] == ["txAudioNoFrames"]


class _HangingConn:
    """WS double that parks in recv() like an idle open connection."""

    async def recv(self) -> tuple[int, bytes]:
        await asyncio.Event().wait()

    async def send_text(self, text: str) -> None:
        pass

    async def send_binary(self, data: bytes) -> None:
        pass

    async def close(self, code: int = 1000, reason: str = "") -> None:
        pass

    def abort(self) -> None:
        pass

    async def keepalive_loop(self, interval: float) -> None:
        pass


class _CapturingConn(_HangingConn):
    """Idle connection that records the JSON frames it is sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def notifications(self, code: str) -> list[dict[str, Any]]:
        return [
            m
            for m in self.sent
            if m.get("type") == "notification" and m.get("code") == code
        ]


class _ScriptedConn(_HangingConn):
    """Audio connection that yields scripted frames, then parks."""

    def __init__(self, frames: list[tuple[int, bytes]]) -> None:
        self._frames = list(frames)

    async def recv(self) -> tuple[int, bytes]:
        if self._frames:
            return self._frames.pop(0)
        return await super().recv()


async def test_serve_client_channel_wires_the_audio_handler_to_the_server(
    monkeypatch,
) -> None:
    """The production attach path passes the server into the audio
    handler — removing that wiring fails here even though every handler
    level test still passes."""
    server = WebServer(None, WebConfig(radio_model="IC-7300"))
    grant = server._controller.acquire("browser")
    key = grant["controller_key"]
    control = server._controller.attach(key, "primary", "controller-test-wcontrol")
    constructed: dict[str, Any] = {}
    real_handler = server_module.AudioHandler

    class _CapturingHandler(real_handler):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructed.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(server_module, "AudioHandler", _CapturingHandler)

    from rigplane.web.channel_attach import ClientChannelParams

    task = asyncio.create_task(
        server.attach_client_channel(
            _HangingConn(),
            "audio",
            ClientChannelParams(controller_key=key),
            ("127.0.0.1", 1),
            transport="ws",
        )
    )
    try:
        for _ in range(4):
            await asyncio.sleep(0)  # let the attach construct and park in recv
        assert constructed["server"] is server
        assert constructed["controller_ticket"].session_id not in (
            None,
            control.session_id,
        ), "the audio channel attaches with its own label, not the control one"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    server._controller.revoke()
    await server._controller.settle()


@pytest.mark.realtime(
    reason="awaits real asyncio tasks (attach, control-channel registration "
    "and toast fan-out) through await_until"
)
async def test_sustained_drops_reach_the_browser_control_channel_end_to_end(
    controller_station, monkeypatch
) -> None:
    """The full production path, fake network only: a browser key
    acquired over the real HTTP route, a native primary, the browser's
    auxiliary control channel and its audio channel attached through
    ``attach_client_channel``, TX keyed through the managed authority,
    Opus frames with no decoder — and the toast arrives as a wire
    notification on the BROWSER control connection, not on an unrelated
    session's."""
    from rigplane.runtime.managed_tx_state import ManagedTxOutcome
    from rigplane.testing.timing import await_until
    from rigplane.web.channel_attach import ClientChannelParams
    from rigplane.ws import WS_OP_BINARY, WS_OP_TEXT

    server, managed = controller_station
    # Host independence (as in the codec-negotiation tests): force the
    # no-transcoder branch everywhere, so the drop reason is deterministic
    # on hosts that do ship a native Opus decoder.
    monkeypatch.setattr(
        server_module.AudioHandler,
        "_ensure_tx_transcoder",
        lambda self: False,
    )
    grant = await request(server, "POST", "/api/v1/controller", {"kind": "browser"})
    assert grant.status == 201
    key = grant.payload["controller_key"]
    other_grant = await request(
        server, "POST", "/api/v1/controller", {"kind": "browser"}
    )
    assert other_grant.status == 201
    other_key = other_grant.payload["controller_key"]

    native = server._controller.attach(key, "primary", "controller-e2e-native")
    # The unrelated session needs its own primary before its control attach.
    server._controller.attach(other_key, "primary", "other-e2e-native")

    browser_conn = _CapturingConn()
    other_conn = _CapturingConn()
    server._radio = _SessionLanRadio()

    def _attach(conn, kind, attach_key, role="auxiliary"):
        return asyncio.create_task(
            server.attach_client_channel(
                conn,
                kind,
                ClientChannelParams(controller_key=attach_key, controller_role=role),
                ("127.0.0.1", 1),
                transport="ws",
            )
        )

    tasks = [
        _attach(browser_conn, "control", key),
        _attach(other_conn, "control", other_key),
    ]
    try:
        await await_until(
            lambda: len(server._session_queues) >= 2,
            description="both control channels register their queues",
        )

        with server._controller.bind(native):
            owner = native.tx_owner
            assert owner is not None
            assert await managed.ptt_down(owner) is ManagedTxOutcome.ACCEPTED

        frames: list[tuple[int, bytes]] = [
            (
                WS_OP_TEXT,
                json.dumps({"type": "audio_start", "direction": "tx"}).encode(),
            )
        ]
        frames += [
            (WS_OP_BINARY, _tx_frame(AUDIO_CODEC_OPUS, b"opus"))
            for _ in range(TX_DROP_NOTIFY_AFTER)
        ]
        tasks.append(
            _attach(_ScriptedConn(frames), "audio", key),
        )

        notes = await await_until(
            lambda: browser_conn.notifications("txAudioDropped"),
            description="the browser control connection receives the toast",
        )
        assert notes[0]["level"] == "error"
        assert notes[0]["params"] == {"reason": "dropped_no_transcoder"}
        assert not [m for m in other_conn.sent if m.get("type") == "notification"], (
            "an unrelated session never sees the toast"
        )
        with server._controller.bind(native):
            await managed.ptt_up(owner)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
    server._controller.revoke()
    await server._controller.settle()
