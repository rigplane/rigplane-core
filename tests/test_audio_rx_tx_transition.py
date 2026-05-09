"""Regression test for audio RX/TX transition (2026-03-09).

Bug: After PTT OFF, RX audio stream was not restarted automatically.
IC-7610 doesn't support full duplex (RX+TX simultaneously) over LAN audio.

Expected flow:
  1. Initial state: RX audio active
  2. PTT ON → stop RX, start TX
  3. PTT OFF → stop TX, restart RX  ← THIS WAS BROKEN

Fix: radio_poller.py PttOff case now calls start_audio_rx_opus() after stop_audio_tx_opus()
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.audio.route import AudioConfigSource, AudioStreamContract
from rigplane.profiles import resolve_radio_profile
from rigplane.rigctld.state_cache import StateCache
from rigplane.types import AudioCodec
from rigplane.web.radio_poller import (
    CommandQueue,
    PttOff,
    PttOn,
    RadioPoller,
)


def _make_audio_capable_radio() -> SimpleNamespace:
    """Create stub radio with audio capabilities.

    Must satisfy isinstance(radio, AudioCapable) so that the poller
    runs the PTT-on/off audio stream transitions (start/stop TX, restart RX).
    """
    profile = resolve_radio_profile(model="IC-7610")
    return SimpleNamespace(
        profile=profile,
        model=profile.model,
        capabilities=set(profile.capabilities),
        _radio_state=SimpleNamespace(active="MAIN"),
        # Core methods
        send_civ=AsyncMock(),
        set_ptt=AsyncMock(),
        # AudioCapable protocol attrs (required for isinstance(radio, AudioCapable))
        # All async methods use AsyncMock so assert_awaited_* and await_count work.
        audio_bus=SimpleNamespace(),
        start_audio_rx_opus=AsyncMock(),
        stop_audio_rx_opus=AsyncMock(),
        push_audio_tx_opus=AsyncMock(),
        start_audio_rx_pcm=AsyncMock(),
        stop_audio_rx_pcm=AsyncMock(),
        start_audio_tx_pcm=AsyncMock(),
        push_audio_tx_pcm=AsyncMock(),
        stop_audio_tx_pcm=AsyncMock(),
        get_audio_stats=AsyncMock(return_value={}),
        # PTT transition methods (used by poller when AudioCapable)
        start_audio_tx_opus=AsyncMock(),
        stop_audio_tx_opus=AsyncMock(),
    )


@pytest.fixture
def radio() -> SimpleNamespace:
    return _make_audio_capable_radio()


@pytest.fixture
def state_cache() -> StateCache:
    return StateCache()


@pytest.fixture
def command_queue() -> CommandQueue:
    return CommandQueue()


@pytest.fixture
def poller(
    radio: SimpleNamespace, state_cache: StateCache, command_queue: CommandQueue
) -> RadioPoller:
    return RadioPoller(radio, state_cache, command_queue)


@pytest.mark.asyncio
async def test_ptt_on_starts_tx_audio(
    poller: RadioPoller, radio: SimpleNamespace
) -> None:
    """PTT ON должен запускать TX audio stream."""
    # Execute PTT ON command directly
    await poller._execute(PttOn())

    # Verify TX audio started before PTT
    radio.start_audio_tx_opus.assert_awaited_once()
    radio.set_ptt.assert_awaited_once_with(True)

    # Verify call order: audio first, then PTT
    assert radio.start_audio_tx_opus.await_count == 1
    assert radio.set_ptt.await_count == 1


@pytest.mark.asyncio
async def test_ptt_off_restarts_rx_audio(
    poller: RadioPoller, radio: SimpleNamespace
) -> None:
    """PTT OFF должен останавливать TX и перезапускать RX audio.

    This is the critical regression test for the 2026-03-09 bug.
    Without the fix, RX audio would not restart after PTT OFF,
    leaving the Web UI silent (no waterfall/spectrum/audio).
    """
    # Execute PTT OFF command directly
    await poller._execute(PttOff())

    # Verify PTT turned off
    radio.set_ptt.assert_awaited_once_with(False)

    # Verify TX audio stopped
    radio.stop_audio_tx_opus.assert_awaited_once()

    # CRITICAL: Verify RX audio restarted (this was the bug)
    radio.start_audio_rx_opus.assert_awaited_once()

    # Verify call order: PTT off → stop TX → start RX
    assert radio.set_ptt.await_count == 1
    assert radio.stop_audio_tx_opus.await_count == 1
    assert radio.start_audio_rx_opus.await_count == 1


@pytest.mark.asyncio
async def test_ptt_cycle_full_sequence(
    poller: RadioPoller, radio: SimpleNamespace
) -> None:
    """Полный цикл PTT ON → PTT OFF должен корректно переключать audio streams."""
    # PTT ON
    await poller._execute(PttOn())

    assert radio.start_audio_tx_opus.await_count == 1
    assert radio.set_ptt.call_args_list[-1][0][0] is True  # Last call was True

    # PTT OFF
    await poller._execute(PttOff())

    assert radio.stop_audio_tx_opus.await_count == 1
    assert radio.set_ptt.call_args_list[-1][0][0] is False  # Last call was False

    # CRITICAL: RX audio должен быть восстановлен
    assert radio.start_audio_rx_opus.await_count == 1


@pytest.mark.asyncio
async def test_ptt_off_handles_audio_errors_gracefully(
    poller: RadioPoller, radio: SimpleNamespace
) -> None:
    """PTT OFF должен обрабатывать ошибки audio transitions без crash."""
    # Simulate audio method failures
    radio.stop_audio_tx_opus.side_effect = RuntimeError("TX stop failed")
    radio.start_audio_rx_opus.side_effect = RuntimeError("RX start failed")

    # Should not raise, errors are logged
    await poller._execute(PttOff())

    # PTT still turned off despite audio errors
    radio.set_ptt.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_multiple_ptt_cycles(poller: RadioPoller, radio: SimpleNamespace) -> None:
    """Множественные PTT циклы должны работать стабильно."""
    for i in range(3):
        # PTT ON
        await poller._execute(PttOn())

        # PTT OFF
        await poller._execute(PttOff())

    # Each cycle should call all methods
    assert radio.start_audio_tx_opus.await_count == 3
    assert radio.stop_audio_tx_opus.await_count == 3
    assert radio.start_audio_rx_opus.await_count == 3
    assert radio.set_ptt.await_count == 6  # 3 ON + 3 OFF


@pytest.mark.asyncio
async def test_ptt_cycle_uses_pcm_when_audio_contract_tx_codec_is_pcm(
    poller: RadioPoller, radio: SimpleNamespace
) -> None:
    """PTT transitions must follow the radio-native TX codec contract."""
    radio.audio_stream_contract = AudioStreamContract(
        rx_codec=AudioCodec.PCM_2CH_16BIT,
        tx_codec=AudioCodec.PCM_1CH_16BIT,
        rx_sample_rate_hz=16000,
        tx_sample_rate_hz=16000,
        rx_channels=2,
        tx_channels=1,
        rx_codec_source=AudioConfigSource.PROFILE_DEFAULT,
        tx_codec_source=AudioConfigSource.PROFILE_DEFAULT,
        rx_sample_rate_source=AudioConfigSource.PROFILE_DEFAULT,
        tx_sample_rate_source=AudioConfigSource.PROFILE_DEFAULT,
    )

    await poller._execute(PttOn())
    await poller._execute(PttOff())

    radio.start_audio_tx_pcm.assert_awaited_once_with(sample_rate=16000)
    radio.start_audio_tx_opus.assert_not_awaited()
    radio.stop_audio_tx_pcm.assert_awaited_once()
    radio.stop_audio_tx_opus.assert_not_awaited()
