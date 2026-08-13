"""Tests for cw_auto_tune command wiring in ControlHandler."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.dsp.tap_registry import TapRegistry
from rigplane.radio_state import RadioState, ReceiverState
from rigplane.web.handlers.control import ControlHandler

_DEFAULT_RADIO = object()


def _make_handler(
    *,
    radio_state: RadioState | None = None,
    radio: Any = _DEFAULT_RADIO,
    audio_fft_available: bool = True,
) -> ControlHandler:
    """Build a ControlHandler with a fake server and WebSocket."""
    ws = MagicMock()
    ws.send_text = AsyncMock()

    tap_registry = TapRegistry()
    broadcaster = MagicMock()
    broadcaster._tap_registry = tap_registry
    broadcaster.ensure_relay = AsyncMock()

    command_queue = MagicMock()

    if radio_state is None:
        radio_state = RadioState(
            main=ReceiverState(freq=14_074_000),
            cw_pitch=600,
        )

    server = SimpleNamespace(
        _audio_broadcaster=broadcaster,
        _radio_state=radio_state,
        _audio_fft_scope=object() if audio_fft_available else None,
        command_queue=command_queue,
    )

    if radio is _DEFAULT_RADIO:
        radio = SimpleNamespace(capabilities={"cw", "audio"}, has_usb_audio=True)

    handler = ControlHandler(
        ws=ws,
        radio=radio,
        server_version="test",
        radio_model="IC-7610",
        server=server,
    )
    return handler


class TestCwAutoTuneWiring:
    """Test cw_auto_tune command dispatch in ControlHandler."""

    async def test_cw_auto_tune_in_commands_set(self) -> None:
        """cw_auto_tune is an RX correction, not a TX-class command."""
        assert "cw_auto_tune" in ControlHandler._COMMANDS
        assert "cw_auto_tune" not in ControlHandler._TX_COMMANDS

    @pytest.mark.parametrize(
        ("radio", "audio_fft_available"),
        [
            (None, True),
            (SimpleNamespace(capabilities={"audio"}, has_usb_audio=True), True),
            (SimpleNamespace(capabilities={"cw"}, has_usb_audio=True), True),
            (SimpleNamespace(capabilities={"cw", "audio"}, has_usb_audio=True), False),
        ],
        ids=("missing-radio", "missing-cw", "missing-audio", "missing-fft"),
    )
    async def test_missing_rx_analysis_prerequisite_rejects_before_side_effects(
        self, radio: Any, audio_fft_available: bool
    ) -> None:
        """CW auto-tune needs CW, RX audio, and an active server FFT source."""
        handler = _make_handler(
            radio=radio,
            audio_fft_available=audio_fft_available,
        )
        registry = MagicMock()
        handler._server._audio_broadcaster._tap_registry = registry

        with (
            patch("rigplane.cw_auto_tuner.CwAutoTuner") as tuner,
            pytest.raises(RuntimeError, match="CW auto-tune requires RX audio FFT"),
        ):
            await handler._cw_auto_tune()

        tuner.return_value.start_collection.assert_not_called()
        registry.register.assert_not_called()
        handler._server._audio_broadcaster.ensure_relay.assert_not_awaited()
        handler._server.command_queue.put.assert_not_called()

    async def test_timeout_returns_not_detected(self) -> None:
        """If no audio arrives within 3s, return detected=None."""
        handler = _make_handler()

        async def _raise_timeout(coro: Any, timeout: float) -> None:
            # Close the Event.wait() coroutine so it isn't left un-awaited.
            coro.close()
            raise asyncio.TimeoutError

        # No audio fed → tuner never fires → timeout
        with patch(
            "rigplane.web.handlers.control.asyncio.wait_for",
            side_effect=_raise_timeout,
        ):
            result = await handler._cw_auto_tune()

        assert result == {"detected": None, "applied": False}

    async def test_successful_detection(self) -> None:
        """Detected tone returns Hz, delta, and applied flag."""
        handler = _make_handler(
            radio_state=RadioState(
                main=ReceiverState(freq=14_074_000),
                cw_pitch=600,
            ),
        )
        # Patch CwAutoTuner to immediately call the callback with 650 Hz
        with patch("rigplane.cw_auto_tuner.CwAutoTuner") as MockTuner:
            instance = MockTuner.return_value
            instance.feed_audio = MagicMock()
            instance.cancel = MagicMock()

            def fake_start(callback):
                # Immediately call callback to simulate detection
                callback(650)

            instance.start_collection = MagicMock(side_effect=fake_start)

            result = await handler._cw_auto_tune()

        assert result["detected"] == 650
        assert result["cw_pitch"] == 600
        assert result["delta"] == 50
        assert result["applied"] is True

        # Verify frequency was shifted
        q = handler._server.command_queue
        q.put.assert_called_once()
        cmd = q.put.call_args[0][0]
        assert cmd.freq == 14_074_000 + 50

    async def test_detection_none_returns_not_applied(self) -> None:
        """Detected=None (silence) returns applied=False."""
        handler = _make_handler()

        with patch("rigplane.cw_auto_tuner.CwAutoTuner") as MockTuner:
            instance = MockTuner.return_value
            instance.feed_audio = MagicMock()
            instance.cancel = MagicMock()

            def fake_start(callback):
                callback(None)

            instance.start_collection = MagicMock(side_effect=fake_start)

            result = await handler._cw_auto_tune()

        assert result == {"detected": None, "applied": False}

    async def test_small_delta_not_applied(self) -> None:
        """Delta <= 5 Hz does not shift frequency."""
        handler = _make_handler(
            radio_state=RadioState(
                main=ReceiverState(freq=7_030_000),
                cw_pitch=600,
            ),
        )

        with patch("rigplane.cw_auto_tuner.CwAutoTuner") as MockTuner:
            instance = MockTuner.return_value
            instance.feed_audio = MagicMock()
            instance.cancel = MagicMock()

            def fake_start(callback):
                callback(603)  # delta = 3 Hz, below threshold

            instance.start_collection = MagicMock(side_effect=fake_start)

            result = await handler._cw_auto_tune()

        assert result["detected"] == 603
        assert result["delta"] == 3
        assert result["applied"] is False
        handler._server.command_queue.put.assert_not_called()

    async def test_no_server_raises(self) -> None:
        """Raises RuntimeError when server is None."""
        ws = MagicMock()
        ws.send_text = AsyncMock()
        handler = ControlHandler(
            ws=ws,
            radio=MagicMock(),
            server_version="test",
            radio_model="IC-7610",
            server=None,
        )
        with pytest.raises(RuntimeError, match="server not available"):
            await handler._cw_auto_tune()

    async def test_tap_is_unregistered_on_success(self) -> None:
        """Tap handle is removed from registry after detection."""
        handler = _make_handler()
        registry = handler._server._audio_broadcaster._tap_registry

        with patch("rigplane.cw_auto_tuner.CwAutoTuner") as MockTuner:
            instance = MockTuner.return_value
            instance.feed_audio = MagicMock()
            instance.cancel = MagicMock()

            def fake_start(callback):
                callback(700)

            instance.start_collection = MagicMock(side_effect=fake_start)

            await handler._cw_auto_tune()

        # Registry should have no active taps after completion
        assert not registry.active

    async def test_tap_is_unregistered_on_timeout(self) -> None:
        """Tap handle is removed from registry even on timeout."""
        handler = _make_handler()
        registry = handler._server._audio_broadcaster._tap_registry

        async def _raise_timeout(coro: Any, timeout: float) -> None:
            # Close the Event.wait() coroutine so it isn't left un-awaited.
            coro.close()
            raise asyncio.TimeoutError

        with patch(
            "rigplane.web.handlers.control.asyncio.wait_for",
            side_effect=_raise_timeout,
        ):
            await handler._cw_auto_tune()

        assert not registry.active

    async def test_ensure_relay_called(self) -> None:
        """ensure_relay is called so audio flows to the tap."""
        handler = _make_handler()

        with patch("rigplane.cw_auto_tuner.CwAutoTuner") as MockTuner:
            instance = MockTuner.return_value
            instance.feed_audio = MagicMock()
            instance.cancel = MagicMock()

            def fake_start(callback):
                callback(700)

            instance.start_collection = MagicMock(side_effect=fake_start)

            await handler._cw_auto_tune()

        handler._server._audio_broadcaster.ensure_relay.assert_awaited_once()
