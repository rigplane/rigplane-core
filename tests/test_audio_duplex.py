"""Tests for the full-duplex USB audio path (single ``sd.Stream``) — MOR-531.

A USB-CODEC radio (FTX-1, X6200) must be able to do digital-mode TX (computer
audio → radio via USB MOD) WHILE RX capture (browser audio + FFT scope) keeps
running. On macOS CoreAudio, opening two separate streams (``sd.InputStream`` +
``sd.OutputStream``) on one C-Media device fails with AUHAL ``-50``. Opening ONE
``sd.Stream(device=(idx, idx), channels=(2, 2), ...)`` with a single duplex
callback avoids that.

These tests exercise the additive ``open_duplex`` path on the backend, the
``UsbAudioDriver.start_duplex`` same-device path, and the bridge's duplex
selection — radio-free, ``FakeAudioBackend`` only (no one-off mocks). The
PortAudio-level callback semantics are exercised against a tiny in-test fake
``sd`` module exactly as the existing RX/TX stream tests do.
"""

from __future__ import annotations

import struct

import pytest

from rigplane.audio.backend import (
    AudioBackend,
    AudioDeviceId,
    AudioDeviceInfo,
    DuplexStream,
    FakeAudioBackend,
    FakeDuplexStream,
    PortAudioBackend,
)

DUPLEX_DEVICE = AudioDeviceInfo(
    id=AudioDeviceId(0),
    name="USB Audio CODEC",
    input_channels=2,
    output_channels=2,
    default_samplerate=48_000,
    is_default_input=True,
    is_default_output=True,
)

SEPARATE_RX_DEVICE = AudioDeviceInfo(
    id=AudioDeviceId(1),
    name="BlackHole 2ch",
    input_channels=2,
    output_channels=2,
)


@pytest.fixture()
def fake_backend() -> FakeAudioBackend:
    return FakeAudioBackend(devices=[DUPLEX_DEVICE, SEPARATE_RX_DEVICE])


# ---------------------------------------------------------------------------
# Protocol / Fake conformance
# ---------------------------------------------------------------------------


class TestDuplexProtocol:
    def test_fake_backend_exposes_open_duplex(
        self, fake_backend: FakeAudioBackend
    ) -> None:
        stream = fake_backend.open_duplex(AudioDeviceId(0))
        assert isinstance(stream, DuplexStream)
        assert isinstance(stream, FakeDuplexStream)

    def test_fake_duplex_stream_is_duplex_stream(self) -> None:
        assert isinstance(FakeDuplexStream(), DuplexStream)

    def test_portaudio_backend_has_open_duplex(self) -> None:
        backend = PortAudioBackend(dependency_loader=lambda: (None, None))
        assert isinstance(backend, AudioBackend)
        assert hasattr(backend, "open_duplex")


# ---------------------------------------------------------------------------
# FakeDuplexStream lifecycle (RX fan + TX queue, radio-free)
# ---------------------------------------------------------------------------


class TestFakeDuplexStream:
    @pytest.mark.asyncio()
    async def test_lifecycle_and_rx_fan(self, fake_backend: FakeAudioBackend) -> None:
        stream = fake_backend.open_duplex(AudioDeviceId(0))
        assert not stream.running
        received: list[bytes] = []
        await stream.start(received.append)
        assert stream.running
        # RX fan: an injected capture frame reaches the registered RX callback.
        stream.inject_frame(b"\x01\x02")
        assert received == [b"\x01\x02"]
        # TX queue: a written frame is captured for assertions.
        await stream.write(b"\x03\x04")
        assert stream.written_frames == [b"\x03\x04"]
        await stream.stop()
        assert not stream.running


# ---------------------------------------------------------------------------
# PortAudio single-callback: RX-fan AND TX-pull in ONE callback
# ---------------------------------------------------------------------------


def _make_fake_sd() -> tuple[type, dict[str, object]]:
    """A fake ``sd`` exposing a ``Stream`` that captures its duplex callback."""
    captured: dict[str, object] = {}

    class FakeSd:
        class Stream:
            def __init__(self, **kw: object) -> None:
                captured["kwargs"] = kw
                captured["callback"] = kw["callback"]

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def close(self) -> None:
                pass

    return FakeSd, captured


class TestPortAudioDuplexCallback:
    @pytest.mark.asyncio()
    async def test_open_duplex_opens_single_stream_with_pair_args(self) -> None:
        FakeSd, captured = _make_fake_sd()
        backend = PortAudioBackend(dependency_loader=lambda: (FakeSd(), object()))
        stream = backend.open_duplex(
            AudioDeviceId(3),
            sample_rate=48_000,
            channels=2,
            frame_ms=20,
            deliver_channels=1,
            rx_audio_channel="left",
        )
        await stream.start(lambda _pcm: None)
        kwargs = captured["kwargs"]
        # ONE stream, both directions targeting the SAME device index, opened at
        # the native channel count for both legs.
        assert kwargs["device"] == (3, 3)
        assert kwargs["channels"] == (2, 2)
        assert kwargs["samplerate"] == 48_000
        assert kwargs["dtype"] == "int16"
        assert callable(kwargs["callback"])
        await stream.stop()

    @pytest.mark.asyncio()
    async def test_single_callback_fans_rx_and_pulls_tx(self) -> None:
        """ONE callback must (a) deliver indata to the RX consumer AND
        (b) fill outdata from the TX queue — both directions in one call."""
        FakeSd, captured = _make_fake_sd()
        backend = PortAudioBackend(dependency_loader=lambda: (FakeSd(), object()))
        # Mono deliver on a stereo-native device (FTX-1 RX on LEFT channel),
        # mono TX leg (the radio's USB MOD consumes one channel).
        stream = backend.open_duplex(
            AudioDeviceId(0),
            sample_rate=48_000,
            channels=2,
            frame_ms=20,
            deliver_channels=1,
            rx_audio_channel="left",
            tx_channels=1,
        )
        received: list[bytes] = []
        await stream.start(received.append)
        cb = captured["callback"]
        assert callable(cb)

        # Queue one mono 20 ms TX frame (960 samples) to be pulled into outdata.
        tx_frame = b"".join(struct.pack("<h", (v % 200) - 100) for v in range(960))
        await stream.write(tx_frame)

        # Build one duplex callback: indata = 960 stereo frames (L = signal,
        # R = silence), outdata = 960 mono frames to fill.
        frames = 960
        indata = _stereo_left_signal(frames)
        outdata = bytearray(frames * 1 * 2)  # mono out

        cb(indata, outdata, frames, None, None)  # type: ignore[operator]

        # (a) RX fan: one 20 ms mono frame delivered, LEFT channel at full level
        # (the silent R was NOT mixed in — left-only downmix).
        assert len(received) == 1
        assert received[0] == _expected_left_mono(frames)
        # (b) TX pull: outdata filled from the queued TX frame.
        assert bytes(outdata) == tx_frame

        await stream.stop()

    @pytest.mark.asyncio()
    async def test_callback_fills_silence_when_tx_queue_empty(self) -> None:
        FakeSd, captured = _make_fake_sd()
        backend = PortAudioBackend(dependency_loader=lambda: (FakeSd(), object()))
        stream = backend.open_duplex(
            AudioDeviceId(0),
            sample_rate=48_000,
            channels=2,
            frame_ms=20,
            deliver_channels=2,
        )
        received: list[bytes] = []
        await stream.start(received.append)
        cb = captured["callback"]

        frames = 960
        indata = _stereo_left_signal(frames)
        outdata = bytearray(frames * 2 * 2)  # stereo out
        outdata[:] = b"\xaa" * len(outdata)  # poison: must be overwritten

        cb(indata, outdata, frames, None, None)  # type: ignore[operator]

        # TX queue empty → outdata zero-filled (silence), never the poison.
        assert bytes(outdata) == b"\x00" * len(outdata)
        # RX still fanned (stereo passthrough — deliver == open).
        assert len(received) == 1
        await stream.stop()


# ---------------------------------------------------------------------------
# Helpers — synthesize a stereo capture buffer with LEFT signal, RIGHT silence
# ---------------------------------------------------------------------------


class _FakeIndata:
    """Mimics the sounddevice numpy buffer's ``tobytes()`` contract."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def tobytes(self) -> bytes:
        return self._payload


def _stereo_left_signal(frames: int) -> _FakeIndata:
    out = bytearray()
    for i in range(frames):
        left = ((i % 200) - 100) * 3
        out += struct.pack("<hh", left, 0)  # L = signal, R = silence
    return _FakeIndata(bytes(out))


def _expected_left_mono(frames: int) -> bytes:
    out = bytearray()
    for i in range(frames):
        left = ((i % 200) - 100) * 3
        out += struct.pack("<h", left)
    return bytes(out)


# ---------------------------------------------------------------------------
# UsbAudioDriver.start_duplex — same-device RX+TX uses open_duplex
# ---------------------------------------------------------------------------


class TestUsbDriverDuplex:
    @pytest.mark.asyncio()
    async def test_start_duplex_uses_open_duplex_for_same_device(self) -> None:
        from rigplane.audio.usb_driver import UsbAudioDriver

        backend = FakeAudioBackend(devices=[DUPLEX_DEVICE])
        driver = UsbAudioDriver(
            rx_device="USB Audio CODEC",
            tx_device="USB Audio CODEC",
            backend=backend,
            rx_audio_channel="left",
        )
        received: list[bytes] = []
        await driver.start_duplex(received.append)

        # ONE duplex stream opened; NO separate InputStream/OutputStream.
        assert len(backend.duplex_streams) == 1
        assert backend.rx_streams == []
        assert backend.tx_streams == []
        assert driver.rx_running is True
        assert driver.tx_running is True

        # RX fan reaches the supplied callback.
        backend.duplex_streams[0].inject_frame(b"\x05\x06")
        assert received == [b"\x05\x06"]

        # TX push routes through the duplex stream's TX queue.
        await driver._push_tx_pcm(b"\x07\x08")
        assert backend.duplex_streams[0].written_frames == [b"\x07\x08"]

        await driver.stop_duplex()
        assert driver.rx_running is False
        assert driver.tx_running is False

    @pytest.mark.asyncio()
    async def test_two_stream_path_unchanged_for_separate_devices(self) -> None:
        """start_rx/start_tx keep using two separate streams (additive)."""
        from rigplane.audio.usb_driver import UsbAudioDriver

        backend = FakeAudioBackend(devices=[DUPLEX_DEVICE])
        driver = UsbAudioDriver(backend=backend)
        await driver.start_rx(lambda _pcm: None)
        await driver.start_tx()
        # Legacy two-stream path: NO duplex stream is opened.
        assert len(backend.rx_streams) == 1
        assert len(backend.tx_streams) == 1
        assert backend.duplex_streams == []
        await driver.stop_rx()
        await driver.stop_tx()


# ---------------------------------------------------------------------------
# Bridge — duplex selection when RX device == TX device
# ---------------------------------------------------------------------------


class _FakeRadio:
    """Minimal AudioCapable double for the bridge (no one-off mocks of streams)."""

    model = "ftx1"
    audio_codec = None

    def __init__(self) -> None:
        self.tx_started = False
        self.pushed: list[bytes] = []
        self.audio_bus = _FakeBus()

    async def start_audio_tx_pcm(self, **_kw: object) -> None:
        self.tx_started = True

    async def push_audio_tx_pcm(self, frame: bytes) -> None:
        self.pushed.append(frame)

    async def stop_audio_tx_pcm(self) -> None:
        self.tx_started = False


class _FakeSubscription:
    def __init__(self) -> None:
        self.active = False  # mirrors AudioSubscription.active (MOR-577)

    async def start(self) -> None:
        self.active = True

    async def aclose(self) -> None:
        self.active = False

    def __aiter__(self) -> "_FakeSubscription":
        return self

    async def __anext__(self) -> object:
        import asyncio

        await asyncio.sleep(3600)
        raise StopAsyncIteration


class _FakeBus:
    rx_active = True  # mirrors AudioBus.rx_active (read by the session)

    def subscribe(self, *, name: str) -> _FakeSubscription:
        return _FakeSubscription()


class TestBridgeDuplexSelection:
    @pytest.mark.asyncio()
    async def test_bridge_uses_duplex_when_rx_device_equals_tx_device(self) -> None:
        from rigplane.audio.bridge import AudioBridge

        backend = FakeAudioBackend(devices=[DUPLEX_DEVICE])
        radio = _FakeRadio()
        bridge = AudioBridge(
            radio,  # type: ignore[arg-type]
            device_name="USB Audio CODEC",
            backend=backend,
            tx_enabled=True,
        )
        await bridge.start()
        try:
            # Same device for RX leg and TX leg → ONE duplex stream, no separate
            # InputStream + OutputStream pair (which would -50 on the C-Media).
            assert len(backend.duplex_streams) == 1
            assert backend.rx_streams == []
            assert backend.tx_streams == []
        finally:
            await bridge.stop()

    @pytest.mark.asyncio()
    async def test_bridge_two_stream_path_for_separate_devices(self) -> None:
        from rigplane.audio.bridge import AudioBridge

        backend = FakeAudioBackend(devices=[DUPLEX_DEVICE, SEPARATE_RX_DEVICE])
        radio = _FakeRadio()
        bridge = AudioBridge(
            radio,  # type: ignore[arg-type]
            device_name="USB Audio CODEC",
            tx_device_name="BlackHole 2ch",
            backend=backend,
            tx_enabled=True,
        )
        await bridge.start()
        try:
            # Distinct RX/TX devices (BlackHole capture, CODEC playback) stay on
            # the two-stream path: one OutputStream (rx leg) + one InputStream.
            assert backend.duplex_streams == []
            assert len(backend.tx_streams) == 1  # radio→device playback (open_tx)
            assert len(backend.rx_streams) == 1  # device→radio capture (open_rx)
        finally:
            await bridge.stop()

    @pytest.mark.asyncio()
    async def test_bridge_rx_only_degrade_preserved_on_duplex(self) -> None:
        """If the radio rejects TX start, the duplex bridge degrades to RX-only
        playback (open_tx), not a duplex stream (MOR-242 preserved)."""
        from rigplane.audio.bridge import AudioBridge

        class _RejectsTxRadio(_FakeRadio):
            async def start_audio_tx_pcm(self, **_kw: object) -> None:
                raise RuntimeError("TX path not armed")

        backend = FakeAudioBackend(devices=[DUPLEX_DEVICE])
        radio = _RejectsTxRadio()
        bridge = AudioBridge(
            radio,  # type: ignore[arg-type]
            device_name="USB Audio CODEC",
            backend=backend,
            tx_enabled=True,
        )
        await bridge.start()
        try:
            # No duplex stream — RX-only playback uses the plain output (open_tx).
            assert backend.duplex_streams == []
            assert len(backend.tx_streams) == 1  # radio→device playback only
            assert backend.rx_streams == []  # no capture leg
        finally:
            await bridge.stop()
