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
* a reconnect while the menu guard is active sends no EnableScope;
* a real link fault (link down, then reconnect) with the scope screen on
  re-enables once — bounded per viewing session, never every reconnect;
* elapsed times come from one monotonic clock and are never negative.

Every test runs on the suite's VirtualClock: one monitor window is one
clock jump, so "two minutes of fake time" costs no real time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from unittest.mock import MagicMock

import pytest

from rigplane.testing.timing import VirtualClock
from rigplane.web.server import WebServer
from test_web_server_coverage import (
    _elapse_scope_windows,
    _live_scope_frame,
    _scope_hidden_verdict,
    _virtual_scope_server,
    _zero_scope_frame,
)


def _fresh_civ(radio: object, clock: VirtualClock) -> None:
    """Model the CI-V poll traffic that keeps flowing while in the menu."""
    radio._last_civ_data_received = clock()  # type: ignore[attr-defined]  # noqa: SLF001


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
            between=lambda: srv._scope_health_check(_zero_scope_frame()),  # noqa: SLF001
        )

    await _run_monitor(srv, scenario())

    assert queued == []
    assert _scope_hidden_verdict(srv) is True


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
    power-cycle (which also closes the menu) — the reconnect enable fires."""
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
        srv._scope_last_frame = virtual_clock() + 5.0  # noqa: SLF001

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
            between=lambda: srv._scope_health_check(_zero_scope_frame()),  # noqa: SLF001
        )
        assert _scope_hidden_verdict(srv) is True
        await _elapse_scope_windows(
            virtual_clock,
            2,
            between=lambda: srv._scope_health_check(_live_scope_frame()),  # noqa: SLF001
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
    await srv.ensure_scope_enabled(MagicMock())
    user_enable = len(queued)  # the user-demand enable, if any, is not counted

    await srv._refetch_and_reenable()  # noqa: SLF001
    assert len(queued) == user_enable + 1
    await srv._refetch_and_reenable()  # noqa: SLF001
    assert len(queued) == user_enable + 1
