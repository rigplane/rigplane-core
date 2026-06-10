"""AudioSession health watchdog + liveness events (MOR-581, epic MOR-562 step 14).

ADR §3.4, tenet T3 — "no silent audio death". Falsification-first:

- (a) a session whose RX goes silent after start (no bus heartbeat
  advance — the -50-shaped silent capture death) must transition to
  RECOVERING and emit an event within ~threshold;
- (b) a healthy session (heartbeat advancing) must NEVER false-positive;
- (c) the web runtime payload exposes session state + last event
  (additive JSON next to ``audioBus``);
- (d) the watchdog task shuts down cleanly with the session — no leaked
  asyncio task (the MOR-567 conformance hygiene).

The watchdog only READS the AudioBus heartbeat (MOR-564) — it is fully
decoupled from the ~500 ms control / ~100 ms audio keep-alive loops.
Step 14 SURFACES the death; the recovery/retry loop is step 20.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any, Callable

from _order_sensitive_radios import LanLikeRadio

from rigplane.audio import AudioPacket
from rigplane.audio.session import (
    RX_LIVENESS_TIMEOUT_S,
    WATCHDOG_INTERVAL_S,
    AudioSession,
    AudioSessionEvent,
    AudioSessionState,
)
from rigplane.core._bounded_queue import BoundedQueue
from rigplane.web.server import WebConfig, WebServer

_PACKET = AudioPacket(ident=0x0080, send_seq=1, data=b"\x01\x00" * 160)

# Tight timings for deterministic tests: small threshold + generous polling
# deadlines instead of fixed real sleeps (per-check awaits stay ~5 ms).
_INTERVAL = 0.02
_TIMEOUT = 0.06


def _fast_session(radio: Any) -> AudioSession:
    return AudioSession(
        radio, watchdog_interval=_INTERVAL, rx_liveness_timeout=_TIMEOUT
    )


async def _wait_for(predicate: Callable[[], bool], deadline_s: float = 2.0) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


class _FakeWriter:
    """Minimal writer capturing the HTTP response bytes."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def _response_json(writer: _FakeWriter) -> tuple[int, dict]:
    text = writer.buffer.decode("ascii", errors="replace")
    status = int(text.split(" ", 2)[1])
    body_start = text.index("\r\n\r\n") + 4
    return status, json.loads(text[body_start:] or "{}")


# ---------------------------------------------------------------------------
# Shipping cadence/threshold: a watchdog, NOT another keep-alive loop
# ---------------------------------------------------------------------------


def test_shipping_constants_are_watchdog_grade() -> None:
    assert WATCHDOG_INTERVAL_S >= 1.0  # cadence ≥ 1 s — never a keep-alive
    assert RX_LIVENESS_TIMEOUT_S >= 3.0
    assert RX_LIVENESS_TIMEOUT_S > WATCHDOG_INTERVAL_S


# ---------------------------------------------------------------------------
# (a) silent RX after start → RECOVERING + event (T3)
# ---------------------------------------------------------------------------


async def test_silent_rx_after_start_surfaces_recovering_and_event() -> None:
    radio = LanLikeRadio()
    session = _fast_session(radio)
    events: list[AudioSessionEvent] = []
    session.add_listener(events.append)
    sub = await session.subscribe_rx("a")
    # RX armed but no frame EVER arrives — the silent -50 death shape.
    assert await _wait_for(lambda: session.state is AudioSessionState.RECOVERING)
    assert events, "RECOVERING transition must publish an event"
    event = events[-1]
    assert event.state is AudioSessionState.RECOVERING
    assert event.reason == "rx_silent"
    assert event.leg == "rx"
    assert isinstance(event.timestamp, float)
    assert session.last_event is event
    await sub.release()
    assert session.state is AudioSessionState.IDLE


async def test_frames_resuming_returns_to_demand_state() -> None:
    radio = LanLikeRadio()
    session = _fast_session(radio)
    events: list[AudioSessionEvent] = []
    session.add_listener(events.append)
    sub = await session.subscribe_rx("a")
    assert await _wait_for(lambda: session.state is AudioSessionState.RECOVERING)
    # Frames resume — keep the heartbeat advancing while we wait.
    deadline = time.monotonic() + 2.0
    while (
        session.state is not AudioSessionState.RX_ONLY and time.monotonic() < deadline
    ):
        radio.rx_callback(_PACKET)  # type: ignore[operator]
        await asyncio.sleep(0.005)
    assert session.state is AudioSessionState.RX_ONLY  # demand-derived state
    assert [e.reason for e in events] == ["rx_silent", "rx_resumed"]
    await sub.release()


# ---------------------------------------------------------------------------
# (b) healthy heartbeat must never false-positive
# ---------------------------------------------------------------------------


async def test_healthy_heartbeat_never_false_positives() -> None:
    radio = LanLikeRadio()
    session = _fast_session(radio)
    events: list[AudioSessionEvent] = []
    session.add_listener(events.append)
    sub = await session.subscribe_rx("a")
    deadline = time.monotonic() + max(0.3, 6 * _TIMEOUT)
    while time.monotonic() < deadline:
        radio.rx_callback(_PACKET)  # type: ignore[operator]
        assert session.state is AudioSessionState.RX_ONLY
        await asyncio.sleep(_INTERVAL / 2)
    assert events == []
    await sub.release()


# ---------------------------------------------------------------------------
# (c) runtime payload + WS event forwarding (additive, local-only)
# ---------------------------------------------------------------------------


async def test_runtime_payload_exposes_session_state_and_last_event() -> None:
    radio = LanLikeRadio()
    session = _fast_session(radio)
    sub = await session.subscribe_rx("a")
    assert await _wait_for(lambda: session.state is AudioSessionState.RECOVERING)

    web_radio = SimpleNamespace(
        model="IC-7610",
        backend_id="rigplane",
        connected=True,
        control_connected=True,
        radio_ready=True,
        capabilities=set(),
        _audio_session=session,
    )
    srv = WebServer(web_radio, WebConfig(host="127.0.0.1", port=0))
    writer = _FakeWriter()
    await srv._handle_http(writer, "GET", "/api/v1/runtime")  # noqa: SLF001
    status, data = _response_json(writer)
    assert status == 200
    payload = data["audioSession"]
    assert payload["enabled"] is True
    assert payload["state"] == "recovering"
    last_event = payload["lastEvent"]
    assert last_event["state"] == "recovering"
    assert last_event["reason"] == "rx_silent"
    assert last_event["leg"] == "rx"
    assert isinstance(last_event["timestamp"], float)
    await sub.release()


async def test_runtime_payload_session_disabled_without_session() -> None:
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0))
    writer = _FakeWriter()
    await srv._handle_http(writer, "GET", "/api/v1/runtime")  # noqa: SLF001
    status, data = _response_json(writer)
    assert status == 200
    assert data["audioSession"] == {"enabled": False}


async def test_session_events_forwarded_on_control_ws_queues() -> None:
    """Server forwards AudioSessionEvents on the EXISTING WS event surface."""
    radio = LanLikeRadio()
    session = _fast_session(radio)
    web_radio = SimpleNamespace(
        model="IC-7610",
        backend_id="rigplane",
        connected=True,
        control_connected=True,
        radio_ready=True,
        capabilities=set(),
        audio_session=session,
        _audio_session=session,
    )
    srv = WebServer(web_radio, WebConfig(host="127.0.0.1", port=0))
    srv._attach_audio_session_listener()  # noqa: SLF001 — start() wiring
    q: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=16)
    srv.register_control_event_queue(q)
    sub = await session.subscribe_rx("a")
    assert await _wait_for(lambda: not q.empty())
    msg = q.get_nowait()
    assert msg["type"] == "event"
    assert msg["name"] == "audio_session"
    assert msg["data"]["state"] == "recovering"
    assert msg["data"]["reason"] == "rx_silent"
    assert msg["data"]["leg"] == "rx"
    await sub.release()
    srv.unregister_control_event_queue(q)
    # stop() detaches — no listener piles up across start/stop cycles.
    srv._detach_audio_session_listener()  # noqa: SLF001
    assert session._listeners == []  # noqa: SLF001


# ---------------------------------------------------------------------------
# (d) watchdog task lifecycle — no leaked asyncio task
# ---------------------------------------------------------------------------


async def test_watchdog_stops_cleanly_on_session_teardown() -> None:
    radio = LanLikeRadio()
    session = _fast_session(radio)
    assert session._watchdog_task is None  # noqa: SLF001 — IDLE: no task
    sub = await session.subscribe_rx("a")
    task = session._watchdog_task  # noqa: SLF001
    assert task is not None and not task.done()
    await sub.release()
    assert session.state is AudioSessionState.IDLE
    assert session._watchdog_task is None  # noqa: SLF001
    assert task.done()  # cancelled AND awaited — nothing leaked
