"""MOR-3690 (reopened): scope-health never kicks the radio out of its menu.

Beta.13 evidence (IC-705 on USB serial): the operator opens the radio's own
menu and the radio stops sending scope frames ENTIRELY (no all-zero frames
like the LAN Icoms). The watchdog branch added by PR #4444 read that as a
transport fault and wrote a bare EnableScope — which kicks the operator out
of the menu — and the reconnect path re-sent EnableScope on every
soft-reconnect regardless of the menu state. Its "no scope frames for -1s"
log line also showed a negative elapsed time: ``now`` was sampled before the
``await`` of the EnableScope write, and a frame landing mid-await re-stamped
``_scope_last_frame`` newer than that sample.

The contract pinned here:

* no scope frames while CI-V traffic keeps flowing is a radio in its own
  menu — a valid state that publishes the scope-hidden verdict and NEVER
  triggers a scope-enable write, however long it lasts;
* the CI-V liveness evidence is the Radio contract's PUBLIC stamp
  (``last_valid_frame_monotonic``, ``core/radio_protocol.py``), never a
  backend-private field — a producer honoring only the public contract
  still gets the menu verdict;
* a reconnect while the menu guard is active sends no EnableScope, and the
  guard's evidence clock is refreshed by every definite observation, so a
  menu held open past the grace window still counts as fresh;
* a real link fault (link down, then reconnect) with the scope screen on
  re-enables once per connection epoch — bounded against repeated refetches
  within one epoch, re-armed by a genuinely new one, and serialised against
  overlapping refetch tasks;
* elapsed times come from one monotonic clock and are never negative.

The monitor tests run on the suite's VirtualClock: one monitor window is
one clock jump, so "two minutes of fake time" costs no real time. The
end-to-end tests enter through the real ``/api/v1/scope`` WebSocket with a
real WebServer and RadioPoller — only the hardware is faked.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.radio_protocol import Radio
from rigplane.rigctld.state_cache import StateCache
from rigplane.scope import ScopeAssembler, ScopeFrame
from rigplane.testing.timing import VirtualClock, await_until
from rigplane.web.protocol import encode_scope_subscription, decode_scope_frame_v2
from rigplane.scope.models import Subscription, View
from rigplane.web.radio_poller import EnableScope
from rigplane.web.server import WebConfig, WebServer
from rigplane.ws import WS_OP_BINARY
from test_web_server import (
    _add_scope_capable_attrs,
    _addr,
    _close_ws,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)
from test_web_server_coverage import (
    _elapse_scope_windows,
    _feed_scope_health,
    _live_scope_frame,
    _scope_hidden_verdict,
    _scope_radio,
    _virtual_scope_server,
    _zero_scope_frame,
)


def _fresh_civ(radio: object, clock: VirtualClock) -> None:
    """Model the CI-V poll traffic that keeps flowing while in the menu:
    the producer stamps the public Radio liveness member at its decode point."""
    radio.last_valid_frame_monotonic = clock()  # type: ignore[attr-defined]


async def _run_monitor(srv: WebServer, coro: Awaitable[None]) -> None:
    monitor = asyncio.create_task(srv._scope_health_monitor())  # noqa: SLF001
    try:
        await coro
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


@pytest.mark.timeout(30)
async def test_all_zero_frames_flowing_writes_nothing(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arriving-but-zero frames = menu on the radio, never a write (PR #4444)."""
    srv, radio, queued = _virtual_scope_server(monkeypatch, virtual_clock)

    async def scenario() -> None:
        await _elapse_scope_windows(
            virtual_clock,
            15,  # 150 fake seconds of menu-open blank frames
            between=lambda: _feed_scope_health(srv, _zero_scope_frame()),  # noqa: SLF001
        )

    await _run_monitor(srv, scenario())

    assert queued == []
    assert _scope_hidden_verdict(srv) is False


@pytest.mark.parametrize("model", ["IC-705", "IC-7610"])
async def test_captured_out_of_range_is_present_not_hidden(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/ic705/scope_out_of_range.json").read_text()
    )
    raw = bytes.fromhex(fixture["civHex"])
    assert raw[7:9] == b"\x01\x01"  # captured seq=1/max=1: no continuation
    frame = ScopeAssembler().feed(raw[7:-1], raw[6])
    assert isinstance(frame, ScopeFrame)
    assert frame.out_of_range and frame.pixels == b""
    assert (frame.start_freq_hz, frame.end_freq_hz) == (
        fixture["startHz"],
        fixture["endHz"],
    )
    radio = _scope_radio()
    radio.model = model
    srv, _, queued = _virtual_scope_server(monkeypatch, virtual_clock, radio=radio)
    caplog.set_level("INFO", logger="rigplane.web.server")
    published: list[bool] = []
    publish = srv._publish_scope_hidden  # noqa: SLF001

    def record(hidden: bool, **kwargs: Any) -> None:
        published.append(hidden)
        publish(hidden, **kwargs)

    monkeypatch.setattr(srv, "_publish_scope_hidden", record)

    async def scenario() -> None:
        await _elapse_scope_windows(
            virtual_clock,
            5,
            between=lambda: _feed_scope_health(srv, frame),  # noqa: SLF001
        )
        assert True not in published
        assert _scope_hidden_verdict(srv) is False
        assert "all-zero" not in caplog.text
        assert not srv._scope_menu_guard_active(now=virtual_clock())  # noqa: SLF001
        _feed_scope_health(srv, _live_scope_frame())  # noqa: SLF001
        await srv._refetch_and_reenable()  # noqa: SLF001
        assert len(queued) == 1

    await _run_monitor(srv, scenario())


@pytest.mark.timeout(30)
async def test_menu_silence_two_minutes_never_enables(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IC-705 evidence: NO frames at all while CI-V is healthy = menu.

    The radio sends nothing scope-related while the operator sits in its
    menu; CI-V polls keep answering. Two minutes of fake time must pass
    without a single EnableScope.
    """
    srv, radio, queued = _virtual_scope_server(monkeypatch, virtual_clock)

    async def scenario() -> None:
        await _elapse_scope_windows(
            virtual_clock,
            15,  # 150 fake seconds: past the 2-minute mark
            between=lambda: _fresh_civ(radio, virtual_clock),
        )

    await _run_monitor(srv, scenario())

    assert queued == []
    assert _scope_hidden_verdict(srv) is True


class _PublicLivenessOnlyRadio(MagicMock):
    """A producer honoring ONLY the public Radio liveness contract.

    It stamps ``last_valid_frame_monotonic`` (the Radio protocol member,
    ``core/radio_protocol.py``) and has no ``_last_civ_data_received`` at
    all — reading that private field raises AttributeError, so a consumer
    keying on a backend-private stamp can never produce the menu verdict
    for this producer.
    """

    def __getattr__(self, name: str) -> Any:
        if name == "_last_civ_data_received":
            raise AttributeError(name)
        return super().__getattr__(name)


@pytest.mark.timeout(30)
async def test_menu_silence_verdict_reads_the_public_liveness_stamp(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A contract-conforming producer without the private
    ``_last_civ_data_received`` field still gets the no-frame menu verdict —
    the monitor reads the Radio contract's public
    ``last_valid_frame_monotonic`` stamp, nothing private."""
    srv, radio, queued = _virtual_scope_server(
        monkeypatch,
        virtual_clock,
        radio=_scope_radio(cls=_PublicLivenessOnlyRadio),
    )

    async def scenario() -> None:
        await _elapse_scope_windows(
            virtual_clock,
            3,
            between=lambda: _fresh_civ(radio, virtual_clock),
        )

    await _run_monitor(srv, scenario())

    assert queued == []
    assert _scope_hidden_verdict(srv) is True


@pytest.mark.timeout(30)
async def test_continuing_menu_evidence_stays_fresh_past_the_grace_window(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A menu held open past the 300 s grace keeps re-confirming the hidden
    verdict every monitor window; the reconnect afterwards must still see
    the evidence as fresh and send no EnableScope.

    Without the refresh the verdict's timestamp stays frozen at its first
    publication: 300 s later the reconnect reads the still-open menu as
    stale evidence and writes EnableScope — ejecting the operator from the
    menu on a perfectly healthy link.
    """
    caplog.set_level("INFO", logger="rigplane.web.server")
    srv, radio, queued = _virtual_scope_server(monkeypatch, virtual_clock)
    windows = (
        int(srv._SCOPE_HIDDEN_RECONNECT_GRACE / srv._scope_health_interval) + 5  # noqa: SLF001
    )

    async def scenario() -> None:
        await _elapse_scope_windows(
            virtual_clock,
            windows,
            between=lambda: _fresh_civ(radio, virtual_clock),
        )

    await _run_monitor(srv, scenario())
    assert _scope_hidden_verdict(srv) is True

    await srv._refetch_and_reenable()  # noqa: SLF001

    assert queued == []
    assert "radio is in its own menu" in caplog.text


@pytest.mark.timeout(30)
async def test_reconnect_while_in_menu_sends_no_enable(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A soft-reconnect while the radio is known to be in its menu is quiet."""
    caplog.set_level("INFO", logger="rigplane.web.server")
    srv, _radio, queued = _virtual_scope_server(monkeypatch, virtual_clock)
    srv._publish_scope_hidden(True, now=virtual_clock())  # noqa: SLF001

    await srv._refetch_and_reenable()  # noqa: SLF001

    assert queued == []
    assert "radio is in its own menu" in caplog.text


@pytest.mark.timeout(30)
async def test_stale_menu_verdict_does_not_block_reconnect_enable(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hidden verdict older than the grace window may predate a radio
    power-cycle (which also closes the menu) — the reconnect enable fires.

    This is the genuinely-unobserved case: no monitor window ran to
    re-confirm the verdict, so its evidence clock rightly ages out.
    """
    srv, _radio, queued = _virtual_scope_server(monkeypatch, virtual_clock)
    srv._publish_scope_hidden(True, now=virtual_clock())  # noqa: SLF001
    virtual_clock.advance(srv._SCOPE_HIDDEN_RECONNECT_GRACE + 1.0)  # noqa: SLF001

    await srv._refetch_and_reenable()  # noqa: SLF001

    assert len(queued) == 1


@pytest.mark.timeout(30)
async def test_link_fault_with_scope_visible_reenables_once(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Real link fault with the scope screen on: the reconnect re-enables —
    at most once per viewing session, not on every reconnect cycle."""
    caplog.set_level("INFO", logger="rigplane.web.server")
    srv, _radio, queued = _virtual_scope_server(monkeypatch, virtual_clock)
    srv._publish_scope_hidden(False, now=virtual_clock())  # noqa: SLF001

    await srv._refetch_and_reenable()  # noqa: SLF001
    assert len(queued) == 1
    assert "re-enable confirmed after reconnect" in caplog.text

    await srv._refetch_and_reenable()  # noqa: SLF001
    await srv._refetch_and_reenable()  # noqa: SLF001
    assert len(queued) == 1  # bounded: once per viewing session


@pytest.mark.timeout(30)
async def test_new_connection_epoch_rearms_reconnect_enable(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two genuine recoveries with the same scope viewer each get one enable.

    The once-per-session bound suppresses repeated refetches inside ONE
    connection epoch; a genuine new connection — the backend's generation
    advance reaching the shared store's provider generation — re-arms it,
    so a second real outage (or a radio power cycle) while the viewer stays
    connected still gets its scope output restored.
    """
    srv, _radio, queued = _virtual_scope_server(monkeypatch, virtual_clock)
    srv._publish_scope_hidden(False, now=virtual_clock())  # noqa: SLF001

    await srv._refetch_and_reenable()  # noqa: SLF001
    assert len(queued) == 1
    await srv._refetch_and_reenable()  # noqa: SLF001 — same epoch: still bounded
    assert len(queued) == 1

    # A genuinely new connection epoch begins (the provider invalidation
    # edge the web seat already subscribes to).
    srv.command_state_store.begin_provider_generation()
    await srv._refetch_and_reenable()  # noqa: SLF001
    assert len(queued) == 2
    await srv._refetch_and_reenable()  # noqa: SLF001 — bounded again in this epoch
    assert len(queued) == 2


@pytest.mark.timeout(30)
async def test_overlapping_reconnect_refetches_send_one_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overlapping refetch tasks must not both pass the once-per-session
    check: the attempt check and the awaited enable are serialized through
    the existing scope-enable lock, so with the command completion held
    pending only one EnableScope ever reaches the queue."""
    radio = _scope_radio(ready=True, connected=True)
    radio._fetch_initial_state = AsyncMock()  # noqa: SLF001
    srv = WebServer(radio)
    srv._scope_handlers.add(MagicMock())  # noqa: SLF001
    srv._radio_scope_demand = True
    held: list[asyncio.Future[None]] = []

    put_ordered = srv.command_queue.put_ordered

    def observe_and_hold(command: object, **kwargs: object) -> object:
        entry = put_ordered(command, **kwargs)
        future = kwargs.get("future")
        if isinstance(command, EnableScope) and isinstance(future, asyncio.Future):
            held.append(future)
        return entry

    monkeypatch.setattr(srv.command_queue, "put_ordered", observe_and_hold)

    first = asyncio.create_task(srv._refetch_and_reenable())  # noqa: SLF001
    second = asyncio.create_task(srv._refetch_and_reenable())  # noqa: SLF001
    try:
        # Bare ``sleep(0)`` loop turns only: every await inside the refetch
        # resolves immediately, so a bounded spin gives the overlapping task
        # every chance to wrongly pass the check while the first enable is
        # still parked on its held completion.
        for _ in range(1000):
            if held:
                break
            await asyncio.sleep(0)
        else:
            raise AssertionError("no EnableScope reached the queue")
        for _ in range(1000):
            if len(held) >= 2 or second.done():
                break
            await asyncio.sleep(0)
        assert len(held) == 1
        for future in held:
            if not future.done():
                future.set_result(None)
        await asyncio.gather(first, second)
    finally:
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)

    enables = [c for c in srv.command_queue.drain() if isinstance(c, EnableScope)]
    assert len(enables) == 1


@pytest.mark.timeout(30)
async def test_elapsed_never_negative_across_reconnect(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The beta.13 "-1s" race: a frame stamped mid-write must never produce
    a negative elapsed time, before or after a reconnect."""
    caplog.set_level("INFO", logger="rigplane.web.server")

    def stamp_future_frame() -> None:
        # A frame landing while an EnableScope write is in flight re-stamps
        # newer than the window's `now` — the old log printed "for -1s".
        srv._scope_started_at = virtual_clock() + 5.0  # noqa: SLF001

    srv, radio, queued = _virtual_scope_server(
        monkeypatch, virtual_clock, on_enable=stamp_future_frame
    )

    async def scenario() -> None:
        await _elapse_scope_windows(
            virtual_clock, 3, between=lambda: _fresh_civ(radio, virtual_clock)
        )
        # A mid-window future stamp, as a racing frame leaves it.
        stamp_future_frame()
        # Link drops and recovers: the reconnect must not resurrect it.
        radio.radio_ready = False
        await _elapse_scope_windows(virtual_clock, 1)
        radio.radio_ready = True
        stamp_future_frame()
        await _elapse_scope_windows(
            virtual_clock, 3, between=lambda: _fresh_civ(radio, virtual_clock)
        )

    await _run_monitor(srv, scenario())

    assert queued == []
    assert "for -" not in caplog.text


@pytest.mark.timeout(30)
async def test_live_frames_clear_hidden_then_reconnect_enables_once(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator leaves the menu, frames resume, the verdict clears; the
    next reconnect re-enables (the guard no longer applies), still bounded."""
    srv, radio, queued = _virtual_scope_server(monkeypatch, virtual_clock)

    async def scenario() -> None:
        await _elapse_scope_windows(
            virtual_clock,
            3,
            between=lambda: _fresh_civ(radio, virtual_clock),
        )
        assert _scope_hidden_verdict(srv) is True
        await _elapse_scope_windows(
            virtual_clock,
            2,
            between=lambda: _feed_scope_health(srv, _live_scope_frame()),  # noqa: SLF001
        )

    await _run_monitor(srv, scenario())

    assert _scope_hidden_verdict(srv) is False
    assert queued == []

    await srv._refetch_and_reenable()  # noqa: SLF001
    assert len(queued) == 1


@pytest.mark.timeout(30)
async def test_new_viewing_session_allows_one_reconnect_enable(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The once-per-session bound is per viewing session: a fresh viewer
    generation earns one more reconnect re-enable, not unlimited ones."""
    srv, _radio, queued = _virtual_scope_server(monkeypatch, virtual_clock)

    await srv._refetch_and_reenable()  # noqa: SLF001
    await srv._refetch_and_reenable()  # noqa: SLF001
    assert len(queued) == 1  # bounded within the first session

    for handler in list(srv._scope_handlers):
        srv.unregister_scope_handler(handler)
    await srv._set_radio_scope_output(False)
    await srv._set_radio_scope_output(True)
    user_enable = len(queued)  # the user-demand enable, if any, is not counted

    await srv._refetch_and_reenable()  # noqa: SLF001
    assert len(queued) == user_enable + 1
    await srv._refetch_and_reenable()  # noqa: SLF001
    assert len(queued) == user_enable + 1


# ---------------------------------------------------------------------------
# API-to-outcome regression (review round 1): the menu guard must hold when
# the product sequence runs end to end — a real WebServer, a real RadioPoller,
# a real WebSocket client on /api/v1/scope; only the hardware is faked.
# ---------------------------------------------------------------------------


def _e2e_scope_radio() -> MagicMock:
    """A scope-capable fake radio for the real-server scope pipeline tests.

    Mirrors test_web_server.py's mock_radio fixture but spec'd to the
    producer's ``Radio`` protocol (the mock-spec meta gate): every
    ScopeCapable member is set in ``__dict__`` (Python 3.12+ runtime-checkable
    Protocol reads them via ``inspect.getattr_static``), and the spec itself
    keeps the ObservationPollable/StatePollable factory attrs off the fake —
    they are not ``Radio`` members — so the web stack takes the real
    RadioPoller branch, the same branch the Icom CI-V backends run in
    production. Only the hardware is faked; the server, poller, command
    queue and WebSocket transport are all real.
    """
    radio = MagicMock(name="radio", spec=Radio)
    radio.connected = True
    radio.radio_ready = True
    radio.control_connected = True
    radio.model = "IC-7610"
    from rigplane.radio_state import RadioState

    radio.radio_state = RadioState()
    radio.radio_state.main.freq = 14_074_000
    radio.capabilities = {
        "audio",
        "scope",
        "dual_rx",
        "meters",
        "tx",
        "attenuator",
        "preamp",
        "rf_gain",
        "af_level",
        "squelch",
        "nb",
        "nr",
    }
    _add_scope_capable_attrs(radio)
    # No managed TX runtime on this fake (same reasoning as the fixture).
    radio.managed_tx = None
    # The Radio contract's public liveness stamp — "never answered" until the
    # test stamps it, exactly like a fresh backend connection generation.
    radio.last_valid_frame_monotonic = None
    radio.get_freq = AsyncMock(return_value=14_074_000)
    radio.get_mode = AsyncMock(return_value=MagicMock(name="USB"))
    radio.get_mode.return_value.name = "USB"
    radio.get_mode_info = AsyncMock(return_value=(radio.get_mode.return_value, 1))
    radio.get_rf_power = AsyncMock(return_value=100)
    radio.get_filter = AsyncMock(return_value=1)
    radio.get_s_meter = AsyncMock(return_value=42)
    radio.get_swr = AsyncMock(return_value=10)
    radio.get_rf_gain = AsyncMock(return_value=200)
    radio.get_af_level = AsyncMock(return_value=180)
    radio.get_attenuator_level = AsyncMock(return_value=0)
    radio.get_preamp = AsyncMock(return_value=1)
    radio.get_data_mode = AsyncMock(return_value=False)
    radio.set_freq = AsyncMock()
    radio.set_mode = AsyncMock()
    radio.set_filter = AsyncMock()
    radio.set_rf_power = AsyncMock()
    radio.set_ptt = AsyncMock()
    radio.start_tx = AsyncMock()
    radio.stop_tx = AsyncMock()
    radio.set_rf_gain = AsyncMock()
    radio.set_af_level = AsyncMock()
    radio.set_attenuator_level = AsyncMock()
    radio.set_preamp = AsyncMock()
    radio.set_vfo = AsyncMock()
    radio.swap_main_sub = AsyncMock()
    radio.equalize_main_sub = AsyncMock()
    radio.set_main_sub_tracking = AsyncMock()
    radio.get_main_sub_tracking = AsyncMock(return_value=False)
    radio.audio_bus = MagicMock()
    radio.start_audio_rx_opus = AsyncMock()
    radio.stop_audio_rx_opus = AsyncMock()
    radio.push_audio_tx_opus = AsyncMock()
    radio.start_audio_rx_pcm = AsyncMock()
    radio.stop_audio_rx_pcm = AsyncMock()
    radio.start_audio_tx_pcm = AsyncMock()
    radio.push_audio_tx_pcm = AsyncMock()
    radio.stop_audio_tx_pcm = AsyncMock()
    radio.get_audio_stats = AsyncMock(return_value={})
    radio.start_audio_tx_opus = AsyncMock()
    radio.stop_audio_tx_opus = AsyncMock()
    radio.audio_codec = None
    radio.audio_sample_rate = 48000
    radio.state_cache = StateCache()
    radio.soft_disconnect = AsyncMock()
    radio.disconnect = AsyncMock()
    radio.send_civ = AsyncMock()
    # StateNotifyCapable members must exist in __dict__ for the 3.12+
    # getattr_static isinstance — the web startup wires the post-reconnect
    # recovery callback through them.
    radio.set_state_change_callback = MagicMock()
    radio.set_reconnect_callback = MagicMock()
    radio._fetch_initial_state = AsyncMock()  # noqa: SLF001
    return radio


def _scope_frame_sink(radio: MagicMock) -> Callable[[ScopeFrame], None]:
    """The server's hardware-scope sink, as wired through on_scope_data."""
    calls = [c for c in radio.on_scope_data.call_args_list if c.args[0] is not None]
    assert calls, "scope data callback not wired on the radio"
    return calls[-1].args[0]


def _producer_live_frame() -> ScopeFrame:
    """A producer-typed live frame, as the radio's scope callback delivers."""
    return ScopeFrame(
        receiver=0,
        mode=0,
        start_freq_hz=14_000_000,
        end_freq_hz=14_350_000,
        pixels=b"\x00" * 200 + b"\x05\x20" + b"\x00" * 277,
        out_of_range=False,
    )


async def _establish_menu_silence(srv: WebServer, radio: MagicMock) -> None:
    """Drive the radio into its menu: scope frames stop entirely while CI-V
    polls keep being answered (the producer keeps stamping the public
    liveness member at its decode point); wait for the monitor's verdict."""
    civ_fresh = asyncio.Event()

    async def keep_civ_fresh() -> None:
        while not civ_fresh.is_set():
            radio.last_valid_frame_monotonic = time.monotonic()
            await asyncio.sleep(0.01)

    civ_task = asyncio.create_task(keep_civ_fresh())
    try:
        await await_until(
            lambda: _scope_hidden_verdict(srv) is True,
            timeout=5.0,
            description="scope-hidden verdict",
        )
    finally:
        civ_fresh.set()
        await civ_task


def _track_refetch(srv: WebServer) -> asyncio.Event:
    """Wrap the refetch so the test can await the reconnect recovery pass."""
    refetch_done = asyncio.Event()
    real_refetch = srv._refetch_and_reenable  # noqa: SLF001

    async def tracked_refetch() -> None:
        try:
            await real_refetch()
        finally:
            refetch_done.set()

    srv._refetch_and_reenable = tracked_refetch  # type: ignore[method-assign]  # noqa: SLF001
    return refetch_done


@pytest.mark.realtime(reason="real WebSocket client and server sockets on real time")
@pytest.mark.timeout(30)
async def test_scope_ws_menu_preserved_through_reconnect() -> None:
    """Real /api/v1/scope entry: a viewer connects and frames flow; the
    radio then sits in its own menu (frames stop, CI-V keeps answering via
    the public liveness stamp); a soft reconnect must NOT write EnableScope
    — the radio's scope output stays untouched, the operator keeps the menu.
    """
    radio = _e2e_scope_radio()
    srv = WebServer(
        radio, WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
    )
    srv._scope_health_interval = 0.05  # noqa: SLF001 — real-time monitor windows
    await srv.start()
    try:
        host, port = _addr(srv)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/scope")
        await _ws_send_text(
            writer,
            encode_scope_subscription(
                Subscription("radio", 0, View(14_000_000, 14_350_000), 475, 30)
            ),
        )
        await _ws_recv_frame(reader)
        try:
            # The viewer's connect drives the real poller's EnableScope onto
            # the radio, and the radio's own callback delivers frames back.
            await await_until(
                lambda: radio.enable_scope.await_count == 1,
                timeout=5.0,
                description="scope enable on the radio",
            )
            live = _producer_live_frame()
            _scope_frame_sink(radio)(live)
            opcode, payload = await asyncio.wait_for(_ws_recv_frame(reader), 5.0)
            assert opcode == WS_OP_BINARY
            assert decode_scope_frame_v2(payload).source == "radio"
            assert decode_scope_frame_v2(payload).width == 475

            await _establish_menu_silence(srv, radio)

            # A soft reconnect while the menu is open must stay quiet: the
            # radio's registered post-reconnect callback fires the real
            # recovery pass.
            refetch_done = _track_refetch(srv)
            radio.set_reconnect_callback.call_args[0][0]()
            await asyncio.wait_for(refetch_done.wait(), timeout=5.0)

            assert radio.enable_scope.await_count == 1  # no ejecting write
            assert srv._scope_enabled is True  # noqa: SLF001
            assert _scope_hidden_verdict(srv) is True
        finally:
            await _close_ws(writer)
    finally:
        await srv.stop()


@pytest.mark.realtime(reason="real WebSocket client and server sockets on real time")
@pytest.mark.timeout(30)
async def test_scope_ws_deliberate_user_enable_during_menu() -> None:
    """The menu guard binds only AUTOMATIC writes: with the menu verdict
    fresh, a viewer who deliberately opens /api/v1/scope still gets the
    scope enabled — the operator's own request is the escape hatch for a
    stale verdict, and frames flow to the new client."""
    radio = _e2e_scope_radio()
    srv = WebServer(
        radio, WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
    )
    srv._scope_health_interval = 0.05  # noqa: SLF001
    srv._scope_disable_grace = 0.0  # noqa: SLF001 — the leave-teardown runs now
    await srv.start()
    try:
        host, port = _addr(srv)
        reader, writer, _ = await _ws_connect(host, port, "/api/v1/scope")
        await _ws_send_text(
            writer,
            encode_scope_subscription(
                Subscription("radio", 0, View(14_000_000, 14_350_000), 475, 30)
            ),
        )
        await _ws_recv_frame(reader)
        await await_until(
            lambda: radio.enable_scope.await_count == 1,
            timeout=5.0,
            description="scope enable on the radio",
        )
        _scope_frame_sink(radio)(_producer_live_frame())
        await _establish_menu_silence(srv, radio)

        # The first viewer leaves; the pre-session radio state is restored.
        await _close_ws(writer)
        await await_until(
            lambda: not srv._scope_handlers and not srv._scope_enabled,  # noqa: SLF001
            timeout=5.0,
            description="scope torn down after the last viewer",
        )
        assert _scope_hidden_verdict(srv) is True  # the menu is still open

        # A NEW viewer deliberately opens the scope view: user demand is not
        # gated by the menu guard, and frames reach the client again.
        reader2, writer2, _ = await _ws_connect(host, port, "/api/v1/scope")
        await _ws_send_text(
            writer2,
            encode_scope_subscription(
                Subscription("radio", 0, View(14_000_000, 14_350_000), 475, 30)
            ),
        )
        await _ws_recv_frame(reader2)
        try:
            await await_until(
                lambda: radio.enable_scope.await_count == 2,
                timeout=5.0,
                description="deliberate user scope enable",
            )
            live = _producer_live_frame()
            _scope_frame_sink(radio)(live)
            opcode, payload = await asyncio.wait_for(_ws_recv_frame(reader2), 5.0)
            assert opcode == WS_OP_BINARY
            assert decode_scope_frame_v2(payload).source == "radio"
            assert decode_scope_frame_v2(payload).width == 475
        finally:
            await _close_ws(writer2)
    finally:
        await srv.stop()
