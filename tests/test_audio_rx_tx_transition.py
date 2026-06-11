"""Regression test for audio RX/TX transition (2026-03-09).

Bug: After PTT OFF, RX audio stream was not restarted automatically.
IC-7610 doesn't support full duplex (RX+TX simultaneously) over LAN audio.

Expected flow:
  1. Initial state: RX audio active
  2. PTT ON → stop RX, start TX
  3. PTT OFF → stop TX, restart RX  ← THIS WAS BROKEN

Fix: radio_poller.py PttOff case restarts RX after stop_audio_tx_opus().

MOR-506: the restart must go through ``radio.audio_bus.restart_rx()`` so the
real subscriber callback is reinstated. The earlier fix passed a throwaway
``_noop_rx`` to ``start_audio_rx_opus``, which — because RX uses a single-slot
callback — clobbered the AudioBus consumer and silenced browser RX after the
first transmit.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import AsyncMock

import pytest

from rigplane.audio import AudioPacket
from rigplane.audio.route import AudioConfigSource, AudioStreamContract
from rigplane.audio_bus import AudioBus
from rigplane.core.radio_protocol import AudioCapable
from rigplane.profiles import resolve_radio_profile
from rigplane.rigctld.state_cache import StateCache
from rigplane.types import AudioCodec
from rigplane.web.radio_poller import (
    CommandQueue,
    PttOff,
    PttOn,
    RadioPoller,
    _should_restart_rx,
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
        # RX is re-armed after TX through the AudioBus (MOR-506), not via a
        # throwaway no-op passed to start_audio_rx_opus.
        audio_bus=SimpleNamespace(restart_rx=AsyncMock()),
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

    # CRITICAL: Verify RX audio restarted through the bus (MOR-506).
    # Must NOT clobber the single-slot RX callback with a no-op.
    radio.audio_bus.restart_rx.assert_awaited_once()
    radio.start_audio_rx_opus.assert_not_awaited()

    # Verify call order: PTT off → stop TX → restart RX via bus
    assert radio.set_ptt.await_count == 1
    assert radio.stop_audio_tx_opus.await_count == 1
    assert radio.audio_bus.restart_rx.await_count == 1


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

    # CRITICAL: RX audio должен быть восстановлен через bus (MOR-506)
    assert radio.audio_bus.restart_rx.await_count == 1
    radio.start_audio_rx_opus.assert_not_awaited()


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
    assert radio.audio_bus.restart_rx.await_count == 3  # RX re-armed via bus
    radio.start_audio_rx_opus.assert_not_awaited()
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


# ---------------------------------------------------------------------------
# MOR-543: PTT path on the neutral AudioTransport surface (no behavior flip)
# ---------------------------------------------------------------------------


def _make_neutral_radio(duplex_mode: str | None = "full") -> SimpleNamespace:
    """Stub radio exposing the neutral AudioTransport TX surface.

    Keeps the legacy per-codec mocks so tests can assert they are NOT
    used once ``start_tx``/``stop_tx`` are present. ``duplex_mode=None``
    models a radio without the ``audio_duplex_mode`` descriptor.
    """
    radio = _make_audio_capable_radio()
    radio.start_tx = AsyncMock()
    radio.stop_tx = AsyncMock()
    if duplex_mode is not None:
        radio.audio_duplex_mode = duplex_mode
    return radio


@pytest.mark.asyncio
async def test_ptt_on_neutral_radio_uses_start_tx() -> None:
    """PTT ON on a neutral radio calls start_tx() with no codec branching."""
    radio = _make_neutral_radio()
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    await poller._execute(PttOn())

    radio.start_tx.assert_awaited_once_with()
    radio.start_audio_tx_opus.assert_not_awaited()
    radio.start_audio_tx_pcm.assert_not_awaited()
    radio.set_ptt.assert_awaited_once_with(True)


@pytest.mark.asyncio
@pytest.mark.parametrize("duplex_mode", ["full", "exclusive", None])
async def test_ptt_off_neutral_radio_stops_tx_then_restarts_rx(
    duplex_mode: str | None,
) -> None:
    """PTT OFF calls stop_tx() then re-arms RX via the bus for EVERY mode.

    Pins the MOR-543 no-flip decision: even ``"full"`` (and a missing
    ``audio_duplex_mode`` attribute) must still re-arm RX, preserving
    the MOR-506 unconditional restart semantics exactly.
    """
    radio = _make_neutral_radio(duplex_mode)
    order: list[str] = []
    radio.stop_tx.side_effect = lambda: order.append("stop_tx")
    radio.audio_bus.restart_rx.side_effect = lambda: order.append("restart_rx")
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    await poller._execute(PttOff())

    radio.set_ptt.assert_awaited_once_with(False)
    radio.stop_tx.assert_awaited_once_with()
    radio.stop_audio_tx_opus.assert_not_awaited()
    radio.stop_audio_tx_pcm.assert_not_awaited()
    radio.audio_bus.restart_rx.assert_awaited_once()
    assert order == ["stop_tx", "restart_rx"]


@pytest.mark.asyncio
async def test_legacy_radio_without_neutral_methods_uses_codec_fallback(
    poller: RadioPoller, radio: SimpleNamespace
) -> None:
    """A radio lacking start_tx/stop_tx still uses the per-codec path."""
    assert not hasattr(radio, "start_tx")
    assert not hasattr(radio, "stop_tx")

    await poller._execute(PttOn())
    await poller._execute(PttOff())

    radio.start_audio_tx_opus.assert_awaited_once()
    radio.stop_audio_tx_opus.assert_awaited_once()
    radio.audio_bus.restart_rx.assert_awaited_once()


def test_should_restart_rx_returns_true_for_all_modes() -> None:
    """No behavior flip in MOR-543: RX re-arm fires for every duplex mode."""
    for mode in ("full", "half", "exclusive"):
        assert _should_restart_rx(mode) is True


# ---------------------------------------------------------------------------
# MOR-506: end-to-end clobber regression with a *real* AudioBus
# ---------------------------------------------------------------------------


class _SingleSlotAudioRadio:
    """Fake radio with realistic single-slot RX callback semantics.

    Mirrors ``_audio_runtime_mixin``: ``start_audio_rx_opus`` overwrites the
    single active RX callback, and TX start/stop never touch the RX callback.
    A test-only :meth:`deliver` invokes whatever RX callback is currently
    installed, modelling an incoming audio frame from the radio.
    """

    def __init__(self) -> None:
        profile = resolve_radio_profile(model="IC-7610")
        self.profile = profile
        self.model = profile.model
        self.capabilities = set(profile.capabilities)
        self._radio_state = SimpleNamespace(active="MAIN")
        self._rx_callback: Callable[[AudioPacket | None], None] | None = None
        self._audio_bus: AudioBus | None = None

    async def start_audio_rx_opus(
        self,
        callback: Callable[[AudioPacket | None], None],
        *,
        jitter_depth: int = 5,
    ) -> None:
        self._rx_callback = callback

    async def stop_audio_rx_opus(self) -> None:
        self._rx_callback = None

    async def start_audio_tx_opus(self) -> None:
        pass

    async def stop_audio_tx_opus(self) -> None:
        pass

    async def start_audio_tx_pcm(self, *, sample_rate: int) -> None:
        pass

    async def stop_audio_tx_pcm(self) -> None:
        pass

    async def push_audio_tx_opus(self, data: bytes) -> None:
        pass

    async def push_audio_tx_pcm(self, data: bytes) -> None:
        pass

    async def start_audio_rx_pcm(self, callback: Any, **kwargs: Any) -> None:
        pass

    async def stop_audio_rx_pcm(self) -> None:
        pass

    async def get_audio_stats(self) -> dict[str, Any]:
        return {}

    @property
    def audio_codec(self) -> Any:
        # NOTE: must not raise. ``RadioProfile`` has no ``audio_codec``
        # attribute, so returning ``self.profile.audio_codec`` raised
        # AttributeError — invisible on Python 3.12+ where the AudioCapable
        # isinstance() check uses inspect.getattr_static() (gh-102433), but
        # fatal on 3.11 where the hasattr()-based check invokes this getter
        # and the AttributeError makes the Protocol check fail.
        return AudioCodec[self.profile.codec_preference[0]]

    @property
    def audio_sample_rate(self) -> int:
        return 48000

    @property
    def audio_bus(self) -> AudioBus:
        if self._audio_bus is None:
            self._audio_bus = AudioBus(self)
        return self._audio_bus

    async def send_civ(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def set_ptt(self, on: bool) -> None:
        pass

    def deliver(self, packet: AudioPacket) -> None:
        """Simulate one incoming RX audio frame from the radio."""
        if self._rx_callback is not None:
            self._rx_callback(packet)


@pytest.mark.asyncio
async def test_ptt_off_does_not_clobber_audio_bus_subscriber() -> None:
    """A bus subscriber must keep receiving RX frames after a PTT TX cycle.

    Regression for MOR-506: drives a *real* :class:`AudioBus` plus a fake
    radio that faithfully models the single-slot RX callback, through the real
    poller ``PttOff`` transition, and asserts a subscriber still receives audio
    frames after TX. With the no-op bug the post-TX frame is swallowed.
    """
    radio = _SingleSlotAudioRadio()
    # The fake must structurally satisfy AudioCapable so the poller runs the
    # PTT audio transitions.
    assert isinstance(radio, AudioCapable)

    poller = RadioPoller(radio, StateCache(), CommandQueue())

    sub = radio.audio_bus.subscribe(name="browser")
    await sub.start()
    assert radio.audio_bus.rx_active

    pkt = AudioPacket(ident=0x01, send_seq=1, data=b"frame")
    radio.deliver(pkt)
    assert sub.get_nowait() is pkt

    await poller._execute(PttOn())
    await poller._execute(PttOff())

    pkt2 = AudioPacket(ident=0x01, send_seq=2, data=b"frame2")
    radio.deliver(pkt2)
    assert sub.get_nowait() is pkt2, (
        "RX frame after PTT-off did not reach the bus subscriber — "
        "the poller clobbered the AudioBus callback with a no-op"
    )
