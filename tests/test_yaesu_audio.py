"""Tests for Yaesu CAT radio audio integration layer."""

from __future__ import annotations

import asyncio
import sys
from typing import Callable
from unittest.mock import AsyncMock

import pytest

from rigplane.audio import AudioPacket
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.lan_stream import SYNTHETIC_RX_IDENT
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.exceptions import AudioFormatError
from rigplane.types import AudioCodec


# ---------------------------------------------------------------------------
# Fake audio driver — mimics UsbAudioDriver public API without hardware deps
# ---------------------------------------------------------------------------


class FakeAudioDriver:
    """In-memory stand-in for UsbAudioDriver."""

    def __init__(self) -> None:
        self._rx_task: asyncio.Task[None] | None = None
        self._tx_task: asyncio.Task[None] | None = None
        self._rx_callback: Callable[[bytes], None] | None = None
        self._tx_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)

    # -- properties expected by get_audio_stats ------------------------------

    @property
    def rx_running(self) -> bool:
        return self._rx_task is not None

    @property
    def tx_running(self) -> bool:
        return self._tx_task is not None

    # -- lifecycle -----------------------------------------------------------

    async def start_rx(
        self,
        callback: Callable[[bytes], None] | None = None,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None:
        self._rx_callback = callback
        self._rx_task = asyncio.ensure_future(asyncio.sleep(3600))

    async def stop_rx(self) -> None:
        if self._rx_task is not None:
            self._rx_task.cancel()
            try:
                await self._rx_task
            except asyncio.CancelledError:
                pass
            self._rx_task = None
        self._rx_callback = None

    async def start_tx(
        self,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None:
        self._tx_task = asyncio.ensure_future(asyncio.sleep(3600))

    async def stop_tx(self) -> None:
        if self._tx_task is not None:
            self._tx_task.cancel()
            try:
                await self._tx_task
            except asyncio.CancelledError:
                pass
            self._tx_task = None
        self._tx_queue = asyncio.Queue(maxsize=64)

    async def _push_tx_pcm(self, frame: bytes) -> None:
        if not self.tx_running:
            raise RuntimeError("Audio TX stream is not started.")
        await self._tx_queue.put(frame)

    def inject_rx_frame(self, data: bytes) -> None:
        """Simulate receiving a PCM frame from the hardware."""
        if self._rx_callback is not None:
            self._rx_callback(data)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_driver() -> FakeAudioDriver:
    return FakeAudioDriver()


@pytest.fixture
def radio(
    fake_driver: FakeAudioDriver, monkeypatch: pytest.MonkeyPatch
) -> YaesuCatRadio:
    """Create a YaesuCatRadio with faked transport + audio driver."""
    # Patch transport so connect() doesn't try to open a real serial port
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.YaesuCatTransport.connect",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.YaesuCatTransport.close",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.YaesuCatTransport.connected",
        True,
    )
    r = YaesuCatRadio(
        device="/dev/fake0",
        audio_driver=fake_driver,  # type: ignore[arg-type]
    )
    return r


# ---------------------------------------------------------------------------
# audio_codec property
# ---------------------------------------------------------------------------


class TestAudioCodecProperty:
    def test_returns_pcm_16bit_mono(self, radio: YaesuCatRadio) -> None:
        assert radio.audio_codec == AudioCodec.PCM_1CH_16BIT

    def test_is_not_opus(self, radio: YaesuCatRadio) -> None:
        assert radio.audio_codec not in (AudioCodec.OPUS_1CH, AudioCodec.OPUS_2CH)


# ---------------------------------------------------------------------------
# Audio descriptors — MOR-537 (AudioTransport epic step 4/12)
# ---------------------------------------------------------------------------


class TestAudioDescriptors:
    """MOR-532 descriptor surface: ``audio_tx_codec`` + ``audio_duplex_mode``."""

    def test_descriptors_present(self, radio: YaesuCatRadio) -> None:
        assert hasattr(radio, "audio_tx_codec")
        assert hasattr(radio, "audio_duplex_mode")

    def test_audio_tx_codec_is_raw_pcm(self, radio: YaesuCatRadio) -> None:
        assert radio.audio_tx_codec == AudioCodec.PCM_1CH_16BIT

    def test_duplex_mode_same_device_macos_is_exclusive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        backend = FakeAudioBackend(
            [
                AudioDeviceInfo(
                    id=AudioDeviceId(1),
                    name="USB Audio CODEC",
                    input_channels=2,
                    output_channels=2,
                ),
            ]
        )
        r = YaesuCatRadio(
            device="/dev/fake0",
            audio_driver=UsbAudioDriver(backend=backend),
        )
        assert r.audio_duplex_mode == "exclusive"

    def test_duplex_mode_falls_back_to_full_without_driver_support(
        self, radio: YaesuCatRadio
    ) -> None:
        # FakeAudioDriver has no ``duplex_mode`` — descriptor must not blow up.
        assert radio.audio_duplex_mode == "full"

    def test_duplex_mode_falls_back_to_full_when_driver_raises(self) -> None:
        class RaisingDriver(FakeAudioDriver):
            @property
            def duplex_mode(self) -> str:
                raise RuntimeError("no devices resolvable offline")

        r = YaesuCatRadio(
            device="/dev/fake0",
            audio_driver=RaisingDriver(),  # type: ignore[arg-type]
        )
        assert r.audio_duplex_mode == "full"


# ---------------------------------------------------------------------------
# audio_sample_rate property — regression guard for #1106 / P2-02
# ---------------------------------------------------------------------------


class TestAudioSampleRateProperty:
    """Regression guard for issue #1106 (P2-02).

    Before the fix the Yaesu backend stored ``_audio_sample_rate`` but did
    not expose it as a property, so ``getattr(radio, "audio_sample_rate", None)``
    returned ``None`` in :mod:`rigplane.web.handlers.audio` and the relay
    fell back to a default rate, mis-clocking FTX-1 audio.
    """

    def test_default_returns_48000(self, radio: YaesuCatRadio) -> None:
        assert radio.audio_sample_rate == 48000

    def test_custom_value_is_exposed(self) -> None:
        r = YaesuCatRadio(
            device="/dev/fake0",
            audio_driver=FakeAudioDriver(),  # type: ignore[arg-type]
            audio_sample_rate=44100,
        )
        assert r.audio_sample_rate == 44100

    def test_getattr_does_not_fallback(self, radio: YaesuCatRadio) -> None:
        # The web audio broadcaster relies on this exact pattern; ensure it
        # never returns ``None`` again (the bug under P2-02).
        sr = getattr(radio, "audio_sample_rate", None)
        assert sr is not None
        assert isinstance(sr, int)
        assert sr > 0


# ---------------------------------------------------------------------------
# AudioCapable protocol satisfaction
# ---------------------------------------------------------------------------


class TestAudioCapableProtocol:
    def test_yaesu_satisfies_audio_capable(self, radio: YaesuCatRadio) -> None:
        from rigplane.radio_protocol import AudioCapable

        assert isinstance(radio, AudioCapable)


# ---------------------------------------------------------------------------
# get_audio_stats
# ---------------------------------------------------------------------------


class TestGetAudioStats:
    @pytest.mark.asyncio
    async def test_both_idle(self, radio: YaesuCatRadio) -> None:
        stats = await radio.get_audio_stats()
        assert stats["rx_active"] is False
        assert stats["tx_active"] is False
        assert stats["sample_rate"] == 48000

    @pytest.mark.asyncio
    async def test_rx_active(self, radio: YaesuCatRadio) -> None:
        await radio.connect()
        await radio.start_audio_rx_opus(lambda pkt: None)
        stats = await radio.get_audio_stats()
        assert stats["rx_active"] is True
        assert stats["tx_active"] is False
        await radio.stop_audio_rx_opus()

    @pytest.mark.asyncio
    async def test_tx_active(self, radio: YaesuCatRadio) -> None:
        await radio.connect()
        await radio.start_audio_tx_pcm()
        stats = await radio.get_audio_stats()
        assert stats["tx_active"] is True
        assert stats["rx_active"] is False
        await radio.stop_audio_tx_pcm()

    @pytest.mark.asyncio
    async def test_both_active(self, radio: YaesuCatRadio) -> None:
        await radio.connect()
        await radio.start_audio_rx_opus(lambda pkt: None)
        await radio.start_audio_tx_pcm()
        stats = await radio.get_audio_stats()
        assert stats["rx_active"] is True
        assert stats["tx_active"] is True
        await radio.stop_audio_rx_opus()
        await radio.stop_audio_tx_pcm()


# ---------------------------------------------------------------------------
# RX audio flow
# ---------------------------------------------------------------------------


class TestRxAudio:
    @pytest.mark.asyncio
    async def test_start_rx_opus_wraps_pcm_in_audio_packet(
        self, radio: YaesuCatRadio, fake_driver: FakeAudioDriver
    ) -> None:
        received: list[AudioPacket] = []
        await radio.connect()
        await radio.start_audio_rx_opus(received.append)

        fake_driver.inject_rx_frame(b"\x00" * 960)
        assert len(received) == 1
        pkt = received[0]
        assert isinstance(pkt, AudioPacket)
        assert pkt.data == b"\x00" * 960
        assert pkt.ident == 0x9781
        await radio.stop_audio_rx_opus()

    @pytest.mark.asyncio
    async def test_rx_sequence_increments(
        self, radio: YaesuCatRadio, fake_driver: FakeAudioDriver
    ) -> None:
        received: list[AudioPacket] = []
        await radio.connect()
        await radio.start_audio_rx_opus(received.append)

        for _ in range(3):
            fake_driver.inject_rx_frame(b"\x01" * 960)

        assert [p.send_seq for p in received] == [0, 1, 2]
        await radio.stop_audio_rx_opus()

    @pytest.mark.asyncio
    async def test_start_pcm_rx_delivers_raw_bytes(
        self, radio: YaesuCatRadio, fake_driver: FakeAudioDriver
    ) -> None:
        received: list[bytes | None] = []
        await radio.connect()
        await radio.start_audio_rx_pcm(received.append)

        fake_driver.inject_rx_frame(b"\xab" * 960)
        assert received == [b"\xab" * 960]
        await radio.stop_audio_rx_pcm()

    @pytest.mark.asyncio
    async def test_stop_rx_opus_clears_state(
        self, radio: YaesuCatRadio, fake_driver: FakeAudioDriver
    ) -> None:
        await radio.connect()
        await radio.start_audio_rx_opus(lambda pkt: None)
        assert fake_driver.rx_running
        await radio.stop_audio_rx_opus()
        assert not fake_driver.rx_running

    @pytest.mark.asyncio
    async def test_stop_rx_pcm_clears_state(
        self, radio: YaesuCatRadio, fake_driver: FakeAudioDriver
    ) -> None:
        await radio.connect()
        await radio.start_audio_rx_pcm(lambda data: None)
        assert fake_driver.rx_running
        await radio.stop_audio_rx_pcm()
        assert not fake_driver.rx_running


# ---------------------------------------------------------------------------
# TX audio flow
# ---------------------------------------------------------------------------


class TestTxAudio:
    @pytest.mark.asyncio
    async def test_push_tx_delegates_to_driver(
        self, radio: YaesuCatRadio, fake_driver: FakeAudioDriver
    ) -> None:
        await radio.connect()
        await radio.start_audio_tx_pcm()
        frame = b"\x42" * 1920
        await radio._push_pcm_tx(frame)
        queued = await asyncio.wait_for(fake_driver._tx_queue.get(), timeout=1)
        assert queued == frame
        await radio.stop_audio_tx_pcm()

    @pytest.mark.asyncio
    async def test_stop_tx_clears_state(
        self, radio: YaesuCatRadio, fake_driver: FakeAudioDriver
    ) -> None:
        await radio.connect()
        await radio.start_audio_tx_pcm()
        assert fake_driver.tx_running
        await radio.stop_audio_tx_pcm()
        assert not fake_driver.tx_running

    @pytest.mark.asyncio
    async def test_push_tx_without_start_raises(
        self, radio: YaesuCatRadio, fake_driver: FakeAudioDriver
    ) -> None:
        await radio.connect()
        assert not fake_driver.tx_running
        with pytest.raises(RuntimeError, match="not started"):
            await fake_driver._push_tx_pcm(b"\x00" * 960)

    @pytest.mark.asyncio
    async def test_push_pcm_tx_rejects_non_bytes(self, radio: YaesuCatRadio) -> None:
        await radio.connect()
        with pytest.raises(TypeError, match="bytes"):
            await radio._push_pcm_tx("not bytes")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_push_pcm_tx_rejects_empty(self, radio: YaesuCatRadio) -> None:
        await radio.connect()
        with pytest.raises(ValueError, match="empty"):
            await radio._push_pcm_tx(b"")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.asyncio
    async def test_jitter_depth_negative_raises(self, radio: YaesuCatRadio) -> None:
        await radio.connect()
        with pytest.raises(ValueError, match="jitter_depth"):
            await radio.start_audio_rx_opus(lambda pkt: None, jitter_depth=-1)

    @pytest.mark.asyncio
    async def test_jitter_depth_bool_raises(self, radio: YaesuCatRadio) -> None:
        await radio.connect()
        with pytest.raises(TypeError, match="jitter_depth"):
            await radio.start_audio_rx_opus(lambda pkt: None, jitter_depth=True)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_pcm_rx_bad_frame_format(self, radio: YaesuCatRadio) -> None:
        await radio.connect()
        with pytest.raises(AudioFormatError):
            await radio.start_audio_rx_pcm(
                lambda data: None, sample_rate=44100, frame_ms=7
            )

    @pytest.mark.asyncio
    async def test_tx_bad_frame_format(self, radio: YaesuCatRadio) -> None:
        await radio.connect()
        with pytest.raises(AudioFormatError):
            await radio.start_audio_tx_pcm(sample_rate=44100, frame_ms=7)

    @pytest.mark.asyncio
    async def test_rx_opus_non_callable_raises(self, radio: YaesuCatRadio) -> None:
        await radio.connect()
        with pytest.raises(TypeError, match="callable"):
            await radio.start_audio_rx_opus("not_callable")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_rx_pcm_non_callable_raises(self, radio: YaesuCatRadio) -> None:
        await radio.connect()
        with pytest.raises(TypeError, match="callable"):
            await radio.start_audio_rx_pcm("not_callable")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AudioBus lazy init
# ---------------------------------------------------------------------------


class TestAudioBus:
    def test_audio_bus_lazy_init(self, radio: YaesuCatRadio) -> None:
        bus1 = radio.audio_bus
        bus2 = radio.audio_bus
        assert bus1 is bus2

    def test_audio_bus_is_not_none(self, radio: YaesuCatRadio) -> None:
        assert radio.audio_bus is not None


# ---------------------------------------------------------------------------
# Disconnect stops audio
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_stops_audio(
        self, radio: YaesuCatRadio, fake_driver: FakeAudioDriver
    ) -> None:
        await radio.connect()
        await radio.start_audio_rx_opus(lambda pkt: None)
        await radio.start_audio_tx_pcm()
        assert fake_driver.rx_running
        assert fake_driver.tx_running
        await radio.disconnect()
        assert not fake_driver.rx_running
        assert not fake_driver.tx_running


# ---------------------------------------------------------------------------
# MetersCapable protocol satisfaction (#1104)
# ---------------------------------------------------------------------------


class TestMetersCapableProtocol:
    """Yaesu backend satisfies the extended MetersCapable protocol (#1104)."""

    def test_yaesu_satisfies_meters_capable(self, radio: YaesuCatRadio) -> None:
        from rigplane.radio_protocol import MetersCapable

        assert isinstance(radio, MetersCapable)
        for name in ("get_power_meter", "get_alc_meter", "get_swr_meter"):
            assert callable(getattr(radio, name)), f"{name} missing"


# ---------------------------------------------------------------------------
# Neutral AudioTransport surface — MOR-541 (AudioTransport epic step 8/12)
# ---------------------------------------------------------------------------

_RX_FRAMES = [b"\x01\x02" * 480, b"\x03\x04" * 480]
_TX_FRAME = b"\x11\x22" * 480


class TracingAudioDriver(FakeAudioDriver):
    """FakeAudioDriver that records the driver-call trace.

    Used to prove the legacy ``*_opus`` family and the neutral
    AudioTransport methods drive the USB audio driver identically
    (delegates only — no divergent framing/format logic).
    """

    def __init__(self) -> None:
        super().__init__()
        self.rx_start_calls: list[dict[str, int | None]] = []
        self.tx_start_calls: list[dict[str, int | None]] = []
        self.tx_frames: list[bytes] = []

    async def start_rx(
        self,
        callback: Callable[[bytes], None] | None = None,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None:
        self.rx_start_calls.append(
            {"sample_rate": sample_rate, "channels": channels, "frame_ms": frame_ms}
        )
        await super().start_rx(
            callback, sample_rate=sample_rate, channels=channels, frame_ms=frame_ms
        )

    async def start_tx(
        self,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None:
        self.tx_start_calls.append(
            {"sample_rate": sample_rate, "channels": channels, "frame_ms": frame_ms}
        )
        await super().start_tx(
            sample_rate=sample_rate, channels=channels, frame_ms=frame_ms
        )

    async def _push_tx_pcm(self, frame: bytes) -> None:
        await super()._push_tx_pcm(frame)
        self.tx_frames.append(frame)


def _make_traced_radio(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[YaesuCatRadio, TracingAudioDriver]:
    """Build a YaesuCatRadio over a TracingAudioDriver (transport faked)."""
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.YaesuCatTransport.connect",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.YaesuCatTransport.close",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.YaesuCatTransport.connected",
        True,
    )
    driver = TracingAudioDriver()
    r = YaesuCatRadio(device="/dev/fake0", audio_driver=driver)  # type: ignore[arg-type]
    return r, driver


class TestAudioTransportNeutral:
    """YaesuCatRadio implements the neutral AudioTransport surface."""

    def test_satisfies_audio_transport_protocol(self, radio: YaesuCatRadio) -> None:
        from rigplane.core.radio_protocol import AudioTransport

        assert isinstance(radio, AudioTransport)

    @pytest.mark.asyncio
    async def test_start_rx_packets_carry_synthetic_ident(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Byte-compat lock: start_rx frames keep ident 0x9781 + wrapping seq."""
        assert SYNTHETIC_RX_IDENT == 0x9781

        r, driver = _make_traced_radio(monkeypatch)
        await r.connect()
        packets: list[AudioPacket] = []
        await r.start_rx(packets.append)
        for frame in _RX_FRAMES:
            driver.inject_rx_frame(frame)
        await r.stop_rx()

        assert [p.ident for p in packets] == [SYNTHETIC_RX_IDENT, SYNTHETIC_RX_IDENT]
        assert [p.send_seq for p in packets] == [0, 1]
        assert [p.data for p in packets] == _RX_FRAMES
        assert driver.rx_running is False

    async def _run_rx_session(
        self, monkeypatch: pytest.MonkeyPatch, *, neutral: bool
    ) -> tuple[list[AudioPacket], TracingAudioDriver]:
        r, driver = _make_traced_radio(monkeypatch)
        await r.connect()
        packets: list[AudioPacket] = []
        if neutral:
            await r.start_rx(packets.append)
        else:
            await r.start_audio_rx_opus(packets.append)
        for frame in _RX_FRAMES:
            driver.inject_rx_frame(frame)
        if neutral:
            await r.stop_rx()
        else:
            await r.stop_audio_rx_opus()
        return packets, driver

    @pytest.mark.asyncio
    async def test_neutral_rx_matches_legacy_packet_framing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Legacy and neutral RX produce identical AudioPackets + driver calls."""
        legacy_packets, legacy_driver = await self._run_rx_session(
            monkeypatch, neutral=False
        )
        neutral_packets, neutral_driver = await self._run_rx_session(
            monkeypatch, neutral=True
        )

        assert neutral_packets == legacy_packets
        assert legacy_packets, "expected RX packets in both sessions"
        assert neutral_driver.rx_start_calls == legacy_driver.rx_start_calls

    async def _run_tx_session(
        self, monkeypatch: pytest.MonkeyPatch, *, neutral: bool
    ) -> TracingAudioDriver:
        r, driver = _make_traced_radio(monkeypatch)
        await r.connect()
        if neutral:
            await r.start_tx()
            await r.push_tx(_TX_FRAME)
            await r.stop_tx()
        else:
            await r.start_audio_tx_opus()
            await r.push_audio_tx_opus(_TX_FRAME)
            await r.stop_audio_tx_opus()
        return driver

    @pytest.mark.asyncio
    async def test_neutral_tx_matches_legacy_driver_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Legacy opus family and neutral TX arm the driver identically."""
        legacy_driver = await self._run_tx_session(monkeypatch, neutral=False)
        neutral_driver = await self._run_tx_session(monkeypatch, neutral=True)

        assert neutral_driver.tx_start_calls == legacy_driver.tx_start_calls
        assert len(legacy_driver.tx_start_calls) == 1
        assert neutral_driver.tx_frames == legacy_driver.tx_frames == [_TX_FRAME]
        assert legacy_driver.tx_running is False
        assert neutral_driver.tx_running is False

    @pytest.mark.asyncio
    async def test_start_audio_tx_opus_arms_tx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression (MOR-541): start_audio_tx_opus was a silent no-op.

        The web handler's opus TX branch calls ``start_audio_tx_opus()``;
        before MOR-541 that silently did nothing on YaesuCatRadio, leaving
        the driver TX path unarmed. It must now delegate to ``start_tx()``.
        """
        r, driver = _make_traced_radio(monkeypatch)
        await r.connect()
        await r.start_audio_tx_opus()

        assert driver.tx_running is True
        assert len(driver.tx_start_calls) == 1

        await r.stop_audio_tx_opus()
        assert driver.tx_running is False
