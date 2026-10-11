"""Injectable-clock tests for the web/rigctld/audio replay path (MOR-3215).

The replay-path timing decisions in ``web``, ``rigctld`` and ``audio``
follow the runtime clock-injection pattern (``runtime/
managed_tx_authority.py``: a ``clock: Callable[[], float]`` constructor
parameter defaulting to ``time.monotonic``). ``AudioBus`` uses the
parameter name ``monotonic`` to match its sibling ``AudioSession``,
whose liveness watchdog compares against the bus heartbeat. Each test
advances a fake clock and observes the freshness/TTL/timeout decision
flip; reverting any single converted call site back to a direct
``time.monotonic()`` read makes its class's test fail.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from rigplane.audio_bus import AudioBus
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import StateStore
from rigplane.profiles import resolve_radio_profile
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.web.handlers import ControlHandler
from rigplane.web.radio_poller import CommandQueue, RadioPoller
from rigplane.web.websocket import WebSocketConnection


class FakeClock:
    """Deterministic stand-in for ``time.monotonic``."""

    def __init__(self, start: float) -> None:
        self._now = float(start)

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        self._now += float(seconds)
        return self._now


# ---------------------------------------------------------------------------
# RadioPoller — tx_target half-TTL republish decision
# ---------------------------------------------------------------------------


async def test_radio_poller_tx_target_ttl_uses_injected_clock() -> None:
    """The half-TTL skip/republish of the derived tx_target follows the
    injected clock: an unchanged value under half its TTL is skipped, and
    crossing the half-TTL mark republishes stamped at the clock's now."""
    profile = resolve_radio_profile(model="IC-7300")
    assert profile.vfo_readback == "selected_unselected"
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio._radio_state = SimpleNamespace(active="MAIN")
    radio.managed_tx = None
    clock = FakeClock(1000.0)
    store = StateStore()
    poller = RadioPoller(radio, CommandQueue(), state_store=store, clock=clock)

    path = FieldPath.global_("tx_state", "tx_target")
    poller._publish_tx_target()  # noqa: SLF001
    first = store.snapshot().field(path)
    assert first.last_observed_monotonic == 1000.0

    poller._publish_tx_target()  # noqa: SLF001
    assert store.snapshot().field(path).last_observed_monotonic == 1000.0

    clock.advance(poller._tx_target_max_age() * 0.5 + 0.1)  # noqa: SLF001
    poller._publish_tx_target()  # noqa: SLF001
    assert store.snapshot().field(path).last_observed_monotonic == clock()


# ---------------------------------------------------------------------------
# RigctldServer — per-client sliding rate-limit window
# ---------------------------------------------------------------------------


async def test_rigctld_rate_limit_window_uses_injected_clock() -> None:
    """The 1-second sliding rate-limit window slides with the injected
    clock: a full window empties only once the clock passes its stamps."""
    clock = FakeClock(5000.0)
    server = RigctldServer(
        MagicMock(),
        RigctldConfig(command_rate_limit=2),
        clock=clock,
    )
    assert server._check_rate_limit(7) is True  # noqa: SLF001
    assert server._check_rate_limit(7) is True  # noqa: SLF001
    assert server._check_rate_limit(7) is False  # noqa: SLF001
    clock.advance(1.0)
    assert server._check_rate_limit(7) is True  # noqa: SLF001


# ---------------------------------------------------------------------------
# WebSocketConnection — pong freshness
# ---------------------------------------------------------------------------


async def test_websocket_pong_freshness_uses_injected_clock() -> None:
    """is_alive() expires exactly when the injected clock passes the pong
    timeout, because the pong anchor is stamped from that same clock."""
    clock = FakeClock(3000.0)
    ws = WebSocketConnection(MagicMock(), MagicMock(), clock=clock)
    assert ws.is_alive() is True
    clock.advance(60.1)
    assert ws.is_alive() is False


# ---------------------------------------------------------------------------
# ControlHandler — per-command pacing window
# ---------------------------------------------------------------------------


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def put(self, item: Any) -> None:
        self.items.append(item)


async def _await_flush(handler: ControlHandler, key: str) -> None:
    task = handler._cmd_flush_tasks.get(key)  # noqa: SLF001
    if task is not None:
        await task


async def test_control_handler_pacing_window_uses_injected_clock() -> None:
    """A same-key SET command inside the 50ms pacing window is coalesced,
    and the paced flush reopens the window stamped from the injected
    clock (gate read and flush stamp both follow it)."""
    clock = FakeClock(2000.0)
    handler = ControlHandler(
        SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock()),
        SimpleNamespace(connected=True),
        "9.9.9",
        "IC-7610",
        server=SimpleNamespace(command_queue=_QueueRecorder()),
        clock=clock,
    )
    queue = handler._server.command_queue  # noqa: SLF001
    key = "set_freq:0"

    await handler._handle_command(  # noqa: SLF001
        {"id": "0", "name": "set_freq", "params": {"freq": 14_000_000}}
    )
    assert len(queue.items) == 1
    assert handler._cmd_last[key] == 2000.0  # noqa: SLF001

    await handler._handle_command(  # noqa: SLF001
        {"id": "1", "name": "set_freq", "params": {"freq": 14_000_001}}
    )
    assert len(queue.items) == 1
    assert key in handler._cmd_pending  # noqa: SLF001

    clock.advance(0.06)
    await _await_flush(handler, key)
    assert len(queue.items) == 2
    assert handler._cmd_last[key] == 2000.06  # noqa: SLF001


# ---------------------------------------------------------------------------
# AudioBus — RX liveness heartbeat
# ---------------------------------------------------------------------------


async def test_audio_bus_heartbeat_uses_injected_clock() -> None:
    """The RX liveness heartbeat stamp (read by AudioSession's watchdog)
    comes from the injected clock, so both can share one replay clock."""
    clock = FakeClock(4000.0)
    radio = SimpleNamespace(
        start_audio_rx_opus=AsyncMock(), stop_audio_rx_opus=AsyncMock()
    )
    bus = AudioBus(radio, monotonic=clock)
    assert bus.last_rx_frame_monotonic is None
    bus._on_opus_packet(None)  # noqa: SLF001
    assert bus.last_rx_frame_monotonic == 4000.0
    clock.advance(5.0)
    bus._on_opus_packet(None)  # noqa: SLF001
    assert bus.last_rx_frame_monotonic == 4005.0
