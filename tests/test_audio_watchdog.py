"""Tests for audio transport EPIPE-storm watchdog (GH#942).

Verifies:
- Audio error count exceeding the threshold triggers audio-only recovery.
- The error count does not grow unbounded after recovery (new transport starts at 0).
- The watchdog exits cleanly when the radio disconnects.
- The watchdog skips checks when no audio transport is active.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from icom_lan.runtime import radio_reconnect


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------


class _FakeTransport:
    """Minimal IcomTransport stand-in."""

    def __init__(self, error_count: int = 0) -> None:
        self._udp_error_count = error_count
        self.rx_packet_count = 0
        self.disconnected = False

    async def disconnect(self) -> None:
        self.disconnected = True


class _FakeAudioStream:
    def __init__(self) -> None:
        self.stopped_rx = False
        self.stopped_tx = False

    async def stop_rx(self) -> None:
        self.stopped_rx = True

    async def stop_tx(self) -> None:
        self.stopped_tx = True


class _FakeAudioRuntime:
    def __init__(self) -> None:
        self.snapshot = MagicMock(name="snapshot")
        self.recovered = False

    def capture_snapshot(self):
        return self.snapshot

    async def recover(self, snapshot) -> None:
        self.recovered = True


class _FakeRadio:
    """Minimal IcomRadio stand-in for watchdog tests."""

    def __init__(self, *, connected: bool = True) -> None:
        self._connected = connected
        self._audio_transport: _FakeTransport | None = None
        self._audio_stream: _FakeAudioStream | None = None
        self._auto_recover_audio = True
        self._audio_runtime = _FakeAudioRuntime()
        self._ensure_audio_calls: int = 0
        self._ensure_audio_exception: Exception | None = None

    async def _ensure_audio_transport(self) -> None:
        self._ensure_audio_calls += 1
        if self._ensure_audio_exception is not None:
            raise self._ensure_audio_exception
        # Simulate fresh transport (error_count starts at 0)
        self._audio_transport = _FakeTransport(error_count=0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_THRESHOLD = radio_reconnect._AUDIO_ERROR_THRESHOLD


async def _run_watchdog_n_ticks(
    radio: _FakeRadio,
    n: int,
    *,
    sleep_side_effect=None,
) -> None:
    """Run audio_error_watchdog_loop for exactly n sleep ticks then cancel."""
    tick = 0

    async def fake_sleep(_interval: float) -> None:
        nonlocal tick
        tick += 1
        if tick > n:
            raise asyncio.CancelledError
        if sleep_side_effect:
            await sleep_side_effect(tick)

    with patch("icom_lan.runtime.radio_reconnect.asyncio.sleep", side_effect=fake_sleep):
        try:
            await radio_reconnect.audio_error_watchdog_loop(radio)  # type: ignore[arg-type]
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAudioWatchdogNoTransport:
    """Watchdog with no audio transport active."""

    @pytest.mark.asyncio
    async def test_skips_when_no_audio_transport(self) -> None:
        radio = _FakeRadio()
        radio._audio_transport = None

        await _run_watchdog_n_ticks(radio, 3)

        # No recovery attempted
        assert radio._ensure_audio_calls == 0

    @pytest.mark.asyncio
    async def test_exits_cleanly_on_disconnect(self) -> None:
        radio = _FakeRadio(connected=False)
        radio._audio_transport = _FakeTransport(error_count=0)

        # Should exit without recovery
        await _run_watchdog_n_ticks(radio, 1)
        assert radio._ensure_audio_calls == 0


class TestAudioWatchdogBelowThreshold:
    """Error counts below threshold should not trigger recovery."""

    @pytest.mark.asyncio
    async def test_no_recovery_below_threshold(self) -> None:
        radio = _FakeRadio()
        radio._audio_transport = _FakeTransport(error_count=_THRESHOLD - 1)

        await _run_watchdog_n_ticks(radio, 2)

        assert radio._ensure_audio_calls == 0

    @pytest.mark.asyncio
    async def test_exactly_at_threshold_triggers_recovery(self) -> None:
        radio = _FakeRadio()
        radio._audio_transport = _FakeTransport(error_count=_THRESHOLD)
        radio._audio_stream = _FakeAudioStream()
        old_stream = radio._audio_stream

        await _run_watchdog_n_ticks(radio, 2)

        assert radio._ensure_audio_calls == 1
        assert old_stream.stopped_rx
        assert old_stream.stopped_tx


class TestAudioWatchdogRecovery:
    """EPIPE storm triggers teardown and reconnect of audio transport."""

    @pytest.mark.asyncio
    async def test_tears_down_old_transport(self) -> None:
        radio = _FakeRadio()
        old_transport = _FakeTransport(error_count=_THRESHOLD + 100)
        radio._audio_transport = old_transport

        await _run_watchdog_n_ticks(radio, 2)

        assert old_transport.disconnected

    @pytest.mark.asyncio
    async def test_reconnects_audio_transport(self) -> None:
        radio = _FakeRadio()
        radio._audio_transport = _FakeTransport(error_count=_THRESHOLD + 10)

        await _run_watchdog_n_ticks(radio, 2)

        assert radio._ensure_audio_calls == 1

    @pytest.mark.asyncio
    async def test_new_transport_starts_at_zero_errors(self) -> None:
        radio = _FakeRadio()
        radio._audio_transport = _FakeTransport(error_count=_THRESHOLD + 50)

        await _run_watchdog_n_ticks(radio, 2)

        # _ensure_audio_transport installs a fresh transport with error_count=0
        assert radio._audio_transport is not None
        assert radio._audio_transport._udp_error_count == 0

    @pytest.mark.asyncio
    async def test_stream_stopped_before_reconnect(self) -> None:
        radio = _FakeRadio()
        radio._audio_transport = _FakeTransport(error_count=_THRESHOLD)
        stream = _FakeAudioStream()
        radio._audio_stream = stream

        await _run_watchdog_n_ticks(radio, 2)

        assert stream.stopped_rx
        assert stream.stopped_tx

    @pytest.mark.asyncio
    async def test_restores_streams_via_audio_runtime(self) -> None:
        radio = _FakeRadio()
        radio._audio_transport = _FakeTransport(error_count=_THRESHOLD)

        await _run_watchdog_n_ticks(radio, 2)

        assert radio._audio_runtime.recovered

    @pytest.mark.asyncio
    async def test_no_restore_when_auto_recover_disabled(self) -> None:
        radio = _FakeRadio()
        radio._auto_recover_audio = False
        radio._audio_transport = _FakeTransport(error_count=_THRESHOLD)

        await _run_watchdog_n_ticks(radio, 2)

        assert not radio._audio_runtime.recovered

    @pytest.mark.asyncio
    async def test_watchdog_continues_after_recovery(self) -> None:
        """After recovery, watchdog keeps monitoring (doesn't exit)."""
        radio = _FakeRadio()
        radio._audio_transport = _FakeTransport(error_count=_THRESHOLD)

        # Run 4 ticks — should recover on tick 1 and keep going
        await _run_watchdog_n_ticks(radio, 4)

        # Should have reconnected once (on tick 1), then kept checking
        assert radio._ensure_audio_calls == 1

    @pytest.mark.asyncio
    async def test_recovery_failure_does_not_crash_watchdog(self) -> None:
        """A failing _ensure_audio_transport is logged but doesn't crash the loop."""
        radio = _FakeRadio()
        radio._audio_transport = _FakeTransport(error_count=_THRESHOLD)
        radio._ensure_audio_exception = OSError("connect failed")

        await _run_watchdog_n_ticks(radio, 2)
        # Should not raise


class TestAudioWatchdogDisconnectDuringRecovery:
    """Radio disconnects while recovery is in flight."""

    @pytest.mark.asyncio
    async def test_aborts_recovery_on_disconnect(self) -> None:
        radio = _FakeRadio()
        radio._audio_transport = _FakeTransport(error_count=_THRESHOLD)

        original_ensure = radio._ensure_audio_transport

        async def disconnect_then_ensure() -> None:
            radio._connected = False
            await original_ensure()

        radio._ensure_audio_transport = disconnect_then_ensure  # type: ignore[method-assign]

        await _run_watchdog_n_ticks(radio, 2)
        # Should not recover (disconnected before reconnect)
        assert not radio._audio_runtime.recovered
