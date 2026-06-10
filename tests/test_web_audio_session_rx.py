"""Web RX broadcaster routes through the radio-owned AudioSession (MOR-608).

Falsification-first (ADR §3.2 option a): browser-only listening (one WS
client, no bridge, no TX) used to subscribe straight on ``radio.audio_bus``,
bypassing the radio-owned :class:`AudioSession`. The session's ``rx_demand``
stayed 0, its state stayed IDLE, and the MOR-581 health watchdog never ran —
tenet T3 "no silent audio death" did not cover the most common usage, and
reconnects fell back to the legacy snapshot replay instead of
``AudioSession.reestablish()``.

The fix routes :meth:`AudioBroadcaster._start_relay` through
``radio.audio_session.subscribe_rx("web-audio")`` when the radio has a
session (mirroring the MOR-580 TX-lease pattern), keeping the legacy
``bus.subscribe`` path for radios without one. Session RX demand tracks the
RELAY lifetime: held while the relay runs (including tap-only operation),
released when the relay actually stops.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import AsyncMock

from _order_sensitive_radios import LanLikeRadio

from rigplane.audio import AudioPacket
from rigplane.audio.bus import STAGE_RX_POST_DSP
from rigplane.audio.session import (
    AudioSession,
    AudioSessionEvent,
    AudioSessionState,
)
from rigplane.core.types import AudioCodec
from rigplane.runtime._audio_recovery import AudioRecoveryRuntime
from rigplane.web.handlers import AudioBroadcaster
from rigplane.web.protocol import AUDIO_HEADER_SIZE, MSG_TYPE_AUDIO_RX

_PACKET = AudioPacket(ident=0x0080, send_seq=1, data=b"\x01\x00" * 160)

# Fast watchdog for deterministic RECOVERING tests (MOR-581 per-instance
# kwargs) — mirrors tests/test_audio_session_health.py.
_WD_INTERVAL = 0.02
_WD_TIMEOUT = 0.06


async def _wait_for(predicate: Callable[[], bool], deadline_s: float = 2.0) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


class _SessionLanRadio(LanLikeRadio):
    """LAN-like stub + radio-owned session singleton (MOR-579 shape)."""

    capabilities = {"audio"}
    audio_codec = AudioCodec.PCM_1CH_16BIT
    audio_sample_rate = 48000

    def __init__(self) -> None:
        super().__init__()
        self._audio_session: AudioSession | None = None

    @property
    def audio_session(self) -> AudioSession:
        if self._audio_session is None:
            self._audio_session = AudioSession(
                self,
                watchdog_interval=_WD_INTERVAL,
                rx_liveness_timeout=_WD_TIMEOUT,
            )
        return self._audio_session


class _NoSessionRadio(LanLikeRadio):
    """LAN-like stub WITHOUT ``audio_session`` (not-yet-migrated backend)."""

    capabilities = {"audio"}
    audio_codec = AudioCodec.PCM_1CH_16BIT
    audio_sample_rate = 48000


def _make_ws() -> Any:
    return SimpleNamespace(send_text=AsyncMock(), is_alive=lambda: True)


# ── 1. Session demand created by a browser-only listener ────────────────────


async def test_web_subscribe_creates_session_rx_demand() -> None:
    """One WS client, no bridge/TX → the session leaves IDLE (RX_ONLY)."""
    radio = _SessionLanRadio()
    broadcaster = AudioBroadcaster(radio)
    queue = await broadcaster.subscribe(ws=_make_ws())
    try:
        session = radio.audio_session
        assert session.rx_demand == 1, (
            "web RX must register demand on the radio-owned AudioSession "
            "(bus-direct subscribe bypasses the session — MOR-608)"
        )
        assert session.state is AudioSessionState.RX_ONLY

        # Frames still flow end-to-end through the session-routed handle.
        assert radio.rx_callback is not None
        radio.rx_callback(_PACKET)  # type: ignore[operator]
        frame = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert frame[0] == MSG_TYPE_AUDIO_RX
        assert frame[AUDIO_HEADER_SIZE:] == _PACKET.data
    finally:
        await broadcaster.unsubscribe(queue)


# ── 2. T3: the health watchdog covers browser-only listening ────────────────


async def test_watchdog_covers_web_only_listening() -> None:
    """Silent RX with only a web listener → RECOVERING event (MOR-581/T3)."""
    radio = _SessionLanRadio()
    events: list[AudioSessionEvent] = []
    radio.audio_session.add_listener(events.append)
    broadcaster = AudioBroadcaster(radio)
    queue = await broadcaster.subscribe(ws=_make_ws())
    try:
        # No frames are ever injected — RX is silent from the arm point.
        assert await _wait_for(
            lambda: radio.audio_session.state is AudioSessionState.RECOVERING
        ), "watchdog never ran for a browser-only listener (T3 gap — MOR-608)"
        assert events, "RECOVERING transition must publish an event"
        assert events[-1].state is AudioSessionState.RECOVERING
        assert events[-1].reason == "rx_silent"
    finally:
        await broadcaster.unsubscribe(queue)


# ── 3. Teardown releases demand ──────────────────────────────────────────────


async def test_unsubscribe_releases_session_demand() -> None:
    """Last WS client gone AND no PCM tap → relay stops, demand drops to 0."""
    radio = _SessionLanRadio()
    broadcaster = AudioBroadcaster(radio)
    queue = await broadcaster.subscribe(ws=_make_ws())
    session = radio.audio_session
    assert session.rx_demand == 1

    await broadcaster.unsubscribe(queue)

    assert broadcaster._relay_task is None
    assert broadcaster._subscription is None
    assert session.rx_demand == 0
    assert session.state is AudioSessionState.IDLE
    assert radio.state == "idle"  # radio RX leg actually disarmed


# ── 4. FFT-scope PCM tap keeps the relay (and session demand) alive ─────────


async def test_pcm_tap_keeps_relay_and_session_demand() -> None:
    """Tap active, no WS clients → relay stays up and demand stays held."""
    radio = _SessionLanRadio()
    broadcaster = AudioBroadcaster(radio)
    handle = broadcaster.taps(STAGE_RX_POST_DSP).register("fft-scope", lambda b: None)
    queue = await broadcaster.subscribe(ws=_make_ws())
    session = radio.audio_session

    await broadcaster.unsubscribe(queue)  # tap holds the relay open

    assert broadcaster._relay_task is not None
    assert not broadcaster._relay_task.done()
    assert session.rx_demand == 1, "session demand must track the relay lifetime"
    assert session.state is AudioSessionState.RX_ONLY

    broadcaster.taps(STAGE_RX_POST_DSP).unregister(handle)
    await broadcaster._stop_relay()
    assert session.rx_demand == 0
    assert session.state is AudioSessionState.IDLE


async def test_ensure_relay_creates_session_demand_without_clients() -> None:
    """Tap-only operation (``ensure_relay``) registers session RX demand."""
    radio = _SessionLanRadio()
    broadcaster = AudioBroadcaster(radio)
    await broadcaster.ensure_relay()
    session = radio.audio_session
    assert session.rx_demand == 1
    assert session.state is AudioSessionState.RX_ONLY
    await broadcaster._stop_relay()
    assert session.rx_demand == 0
    assert session.state is AudioSessionState.IDLE


# ── 5. No-session radios keep the legacy bus path ────────────────────────────


async def test_radio_without_session_keeps_legacy_bus_path() -> None:
    """Bare doubles / not-yet-migrated backends still relay via the bus."""
    radio = _NoSessionRadio()
    broadcaster = AudioBroadcaster(radio)
    queue = await broadcaster.subscribe(ws=_make_ws())
    try:
        assert radio.audio_bus.subscriber_count == 1
        assert radio.state == "receiving"
        assert radio.rx_callback is not None
        radio.rx_callback(_PACKET)  # type: ignore[operator]
        frame = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert frame[AUDIO_HEADER_SIZE:] == _PACKET.data
    finally:
        await broadcaster.unsubscribe(queue)
    # Legacy sync stop() schedules the bus removal — poll for completion.
    assert await _wait_for(lambda: radio.audio_bus.subscriber_count == 0)
    assert await _wait_for(lambda: radio.state == "idle")


# ── 6. Reconnect resolves the session path (not the legacy replay) ──────────


async def test_recovery_sees_web_only_relay_as_session_demand() -> None:
    """With only the web relay up, ``_session_with_demand()`` is non-None —
    the reconnect path goes through ``AudioSession.reestablish()``, not
    ``_replay_legacy``."""
    radio = _SessionLanRadio()
    broadcaster = AudioBroadcaster(radio)
    queue = await broadcaster.subscribe(ws=_make_ws())
    try:
        runtime = AudioRecoveryRuntime(radio)  # host shape: _audio_session slot
        assert runtime._session_with_demand() is not None, (
            "web-only RX demand must be visible to the recovery runtime "
            "(legacy replay would be used instead — MOR-608)"
        )
    finally:
        await broadcaster.unsubscribe(queue)
    assert AudioRecoveryRuntime(radio)._session_with_demand() is None
