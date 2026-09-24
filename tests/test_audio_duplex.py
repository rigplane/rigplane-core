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

import asyncio
import struct
import threading
import types

import pytest

from rigplane.audio.backend import (
    AudioBackend,
    AudioDeviceId,
    AudioDeviceInfo,
    DuplexStream,
    FakeAudioBackend,
    FakeDuplexStream,
    PortAudioBackend,
    RxStreamHealth,
)
from rigplane.audio.usb_driver import AudioCaptureOpenTimeoutError, UsbAudioDriver
from rigplane.audio_bridge import AudioBridge

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


def test_bridge_metrics_prefer_active_duplex_capture_health() -> None:
    bridge = AudioBridge(types.SimpleNamespace())
    bridge._tx_stream = types.SimpleNamespace(
        running=True,
        capture_health=RxStreamHealth(input_overflow_events=9),
    )
    bridge._duplex_stream = types.SimpleNamespace(
        running=True,
        capture_health=RxStreamHealth(
            input_overflow_events=3,
            input_underflow_events=2,
            callback_status_flags={
                "input_overflow": 3,
                "input_underflow": 2,
            },
        ),
    )

    m = bridge.metrics

    assert m.capture_input_overflows == 3
    assert m.capture_input_underflows == 2
    assert m.capture_callback_status_flags == {
        "input_overflow": 3,
        "input_underflow": 2,
    }


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
        """start_rx/start_tx keep using two separate streams (additive).

        Genuinely separate RX/TX devices (distinct indices; BlackHole is a
        virtual loopback anyway), so the duplex policy is "full" on every
        platform — the exclusive same-device path is the MOR-546 handoff
        covered below.
        """
        from rigplane.audio.usb_driver import UsbAudioDriver

        backend = FakeAudioBackend(devices=[DUPLEX_DEVICE, SEPARATE_RX_DEVICE])
        driver = UsbAudioDriver(
            rx_device="USB Audio CODEC",
            tx_device="BlackHole 2ch",
            backend=backend,
        )
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

    @pytest.mark.asyncio()
    async def test_bridge_duplex_metrics_surface_capture_overflow_separately(
        self,
    ) -> None:
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
            capture = backend.duplex_streams[0]
            frame = b"\x10\x00" * 960
            capture.inject_frame(frame, input_overflow=True)

            for _ in range(5):
                await asyncio.sleep(0)

            assert bridge.metrics.capture_input_overflows == 1
            assert bridge.metrics.capture_input_underflows == 0
            assert bridge.metrics.tx_overruns == 0
        finally:
            await bridge.stop()


# ---------------------------------------------------------------------------
# MOR-546 — YaesuCatRadio exclusive duplex: TX arm over live RX = ONE stream
# ---------------------------------------------------------------------------


def _patch_yaesu_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """YaesuCatRadio without a serial port: the transport reads connected."""
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.YaesuCatTransport.connected", True
    )


def _forbid_cat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if TX-audio arm/disarm sends ANY CAT command (PTT stays separate)."""

    async def _no_cat(*args: object, **kwargs: object) -> None:
        raise AssertionError("TX audio arm/disarm must not send CAT commands")

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.YaesuCatTransport.write", _no_cat
    )
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.YaesuCatTransport.query", _no_cat
    )


class TestYaesuExclusiveDuplexTx:
    """MOR-546: on an exclusive radio (RX and TX on the SAME physical USB
    device, macOS) arming TX audio over a live RX must move BOTH legs to ONE
    duplex stream — a second OutputStream on that device kills the capture
    (AUHAL -50, the live FTX-1 defect)."""

    @pytest.mark.asyncio()
    async def test_tx_arm_uses_one_duplex_stream_and_rx_keeps_flowing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rigplane.audio.session import AudioSessionState
        from rigplane.audio.usb_driver import UsbAudioDriver
        from rigplane.backends.yaesu_cat.radio import YaesuCatRadio

        _patch_yaesu_offline(monkeypatch)
        _forbid_cat(monkeypatch)
        # Simulate the macOS same-device duplex policy on any test host.
        monkeypatch.setattr(
            "rigplane.audio.usb_driver.resolve_usb_duplex_mode",
            lambda _rx, _tx: "exclusive",
        )
        backend = FakeAudioBackend(
            devices=[DUPLEX_DEVICE], strict_device_exclusive=True
        )
        driver = UsbAudioDriver(
            rx_device="USB Audio CODEC",
            tx_device="USB Audio CODEC",
            backend=backend,
            rx_audio_channel="left",
        )
        radio = YaesuCatRadio(device="/dev/cu.fake", audio_driver=driver)
        assert radio.audio_duplex_mode == "exclusive"
        assert radio.audio_setup_order == "atomic"
        session = radio.audio_session

        sub = await session.subscribe_rx("web-audio")
        try:
            assert session.state is AudioSessionState.RX_ONLY
            assert len(backend.rx_streams) == 1
            assert backend.duplex_streams == [] and backend.tx_streams == []
            # RX frames reach the bus BEFORE the TX arm.
            backend.rx_streams[0].inject_frame(b"\x01\x02")
            pkt = await sub.get(timeout=1.0)
            assert pkt is not None and pkt.data == b"\x01\x02"

            lease = await session.acquire_tx("web-tx")
            try:
                assert session.state is AudioSessionState.RX_TX
                # ONE duplex stream; NO separate output stream on the device.
                assert len(backend.duplex_streams) == 1
                assert backend.tx_streams == []
                assert backend.duplex_streams[0].running
                # RX frames keep flowing DURING the TX arm — the bus
                # callback survived the RX → duplex handoff.
                backend.duplex_streams[0].inject_frame(b"\x03\x04")
                pkt = await sub.get(timeout=1.0)
                assert pkt is not None and pkt.data == b"\x03\x04"
                # TX frames ride the duplex stream's TX queue.
                await lease.push(b"\x05\x06")
                assert backend.duplex_streams[0].written_frames == [b"\x05\x06"]
            finally:
                await lease.release()

            # Disarm returns to plain RX with the bus callback preserved.
            assert session.state is AudioSessionState.RX_ONLY
            assert not backend.duplex_streams[0].running
            running_rx = [s for s in backend.rx_streams if s.running]
            assert len(running_rx) == 1
            running_rx[0].inject_frame(b"\x07\x08")
            pkt = await sub.get(timeout=1.0)
            assert pkt is not None and pkt.data == b"\x07\x08"
        finally:
            await sub.release()

        # Teardown closes the duplex stream — nothing left running.
        assert session.state is AudioSessionState.IDLE
        all_streams = backend.rx_streams + backend.tx_streams + backend.duplex_streams
        assert not any(s.running for s in all_streams)

    @pytest.mark.asyncio()
    async def test_separate_devices_keep_the_two_stream_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pin: full-duplex (separate RX/TX devices) behaviour is unchanged."""
        from rigplane.audio.session import AudioSessionState
        from rigplane.audio.usb_driver import UsbAudioDriver
        from rigplane.backends.yaesu_cat.radio import YaesuCatRadio

        _patch_yaesu_offline(monkeypatch)
        _forbid_cat(monkeypatch)
        backend = FakeAudioBackend(
            devices=[DUPLEX_DEVICE, SEPARATE_RX_DEVICE],
            strict_device_exclusive=True,
        )
        driver = UsbAudioDriver(
            rx_device="USB Audio CODEC",
            tx_device="BlackHole 2ch",
            backend=backend,
        )
        radio = YaesuCatRadio(device="/dev/cu.fake", audio_driver=driver)
        assert radio.audio_duplex_mode == "full"
        assert radio.audio_setup_order == "rx_first"
        session = radio.audio_session

        sub = await session.subscribe_rx("web-audio")
        try:
            assert len(backend.rx_streams) == 1
            lease = await session.acquire_tx("web-tx")
            try:
                assert session.state is AudioSessionState.RX_TX
                # Two separate streams, as before; no duplex stream; the RX
                # stream is never torn down for the TX arm.
                assert len(backend.tx_streams) == 1
                assert backend.duplex_streams == []
                assert len(backend.rx_streams) == 1
                assert backend.rx_streams[0].running
                backend.rx_streams[0].inject_frame(b"\x09\x0a")
                pkt = await sub.get(timeout=1.0)
                assert pkt is not None and pkt.data == b"\x09\x0a"
                await lease.push(b"\x0b\x0c")
                assert backend.tx_streams[0].written_frames == [b"\x0b\x0c"]
            finally:
                await lease.release()
            assert session.state is AudioSessionState.RX_ONLY
            # No RX re-open churn on the rx_first path (the redundant bus
            # re-arm is a typed no-op).
            assert len(backend.rx_streams) == 1
            assert backend.rx_streams[0].running
        finally:
            await sub.release()
        assert session.state is AudioSessionState.IDLE
        assert not backend.rx_streams[0].running
        assert not backend.tx_streams[0].running


# ---------------------------------------------------------------------------
# MOR-546 — driver-owned exclusive handoff (inside UsbAudioDriver)
# ---------------------------------------------------------------------------


def _patch_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate the macOS same-device duplex policy on any test host."""
    monkeypatch.setattr(
        "rigplane.audio.usb_driver.resolve_usb_duplex_mode",
        lambda _rx, _tx: "exclusive",
    )


def _exclusive_driver(
    monkeypatch: pytest.MonkeyPatch,
    *,
    capture_open_timeout: float | None = None,
) -> tuple[UsbAudioDriver, FakeAudioBackend]:
    """Shipping driver on a strict same-device fake, policy = exclusive."""
    _patch_exclusive(monkeypatch)
    backend = FakeAudioBackend(devices=[DUPLEX_DEVICE], strict_device_exclusive=True)
    extra: dict[str, float] = {}
    if capture_open_timeout is not None:
        extra["capture_open_timeout"] = capture_open_timeout
    driver = UsbAudioDriver(
        rx_device="USB Audio CODEC",
        tx_device="USB Audio CODEC",
        backend=backend,
        rx_audio_channel="left",
        **extra,
    )
    return driver, backend


class TestUsbDriverExclusiveHandoff:
    """MOR-546: the same-device handoff lives INSIDE UsbAudioDriver, reached
    through the plain start_rx / start_tx / stop_tx / stop_rx calls — so both
    USB backends (YaesuCatRadio, _IcomSerialRadioBase) get it unchanged. The
    strict fake raises the -50-shaped error on any second stream on the
    device, so these tests fail on origin/main where start_tx opens a
    separate OutputStream and start_rx on a running duplex raises."""

    @pytest.mark.asyncio()
    async def test_start_tx_over_live_rx_moves_to_one_duplex_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        driver, backend = _exclusive_driver(monkeypatch)
        received: list[bytes] = []
        await driver.start_rx(received.append)
        backend.rx_streams[0].inject_frame(b"\x01\x02")
        assert received == [b"\x01\x02"]

        await driver.start_tx()

        # ONE duplex stream; the plain RX stream yielded the device; NO
        # separate output stream was opened (strict backend would -50).
        assert len(backend.duplex_streams) == 1
        assert backend.tx_streams == []
        assert not backend.rx_streams[0].running
        assert backend.duplex_streams[0].running
        assert driver.rx_running and driver.tx_running
        # RX frames keep flowing through the SAME callback.
        backend.duplex_streams[0].inject_frame(b"\x03\x04")
        assert received == [b"\x01\x02", b"\x03\x04"]
        # TX frames ride the duplex stream's TX queue.
        await driver._push_tx_pcm(b"\x05\x06")
        assert backend.duplex_streams[0].written_frames == [b"\x05\x06"]

    @pytest.mark.asyncio()
    async def test_start_rx_joins_the_running_duplex_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rigplane.audio.usb_driver import AudioAlreadyStartedError

        driver, backend = _exclusive_driver(monkeypatch)
        await driver.start_tx()
        assert len(backend.duplex_streams) == 1
        # TX armed without RX demand: RX frames drain, nothing delivered.
        backend.duplex_streams[0].inject_frame(b"\x01\x02")

        received: list[bytes] = []
        await driver.start_rx(received.append)

        # No second stream on the device — RX joined the duplex stream.
        assert backend.rx_streams == []
        assert len(backend.duplex_streams) == 1
        backend.duplex_streams[0].inject_frame(b"\x03\x04")
        assert received == [b"\x03\x04"]
        # A second RX start while joined is the typed double-start error.
        with pytest.raises(AudioAlreadyStartedError):
            await driver.start_rx(received.append)

    @pytest.mark.asyncio()
    async def test_stop_tx_returns_to_plain_rx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        driver, backend = _exclusive_driver(monkeypatch)
        received: list[bytes] = []
        await driver.start_rx(received.append)
        await driver.start_tx()

        await driver.stop_tx()

        assert not backend.duplex_streams[0].running
        assert not driver.tx_running
        # Plain RX resumed on the SAME driver-owned callback.
        running_rx = [s for s in backend.rx_streams if s.running]
        assert len(running_rx) == 1
        assert driver.rx_running
        running_rx[0].inject_frame(b"\x07\x08")
        assert received == [b"\x07\x08"]

    @pytest.mark.asyncio()
    async def test_stop_rx_during_duplex_only_drops_the_callback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        driver, backend = _exclusive_driver(monkeypatch)
        received: list[bytes] = []
        await driver.start_rx(received.append)
        await driver.start_tx()

        await driver.stop_rx()

        # The duplex stream keeps running for the TX leg; RX frames drain.
        assert backend.duplex_streams[0].running
        assert driver.tx_running
        backend.duplex_streams[0].inject_frame(b"\x01\x02")
        assert received == []
        # RX demand can re-join the same duplex stream.
        await driver.start_rx(received.append)
        backend.duplex_streams[0].inject_frame(b"\x03\x04")
        assert received == [b"\x03\x04"]
        # Unwire RX again, then stop TX: no plain-RX re-open, all closed.
        await driver.stop_rx()
        await driver.stop_tx()
        assert not backend.duplex_streams[0].running
        assert not any(s.running for s in backend.rx_streams)
        assert not driver.rx_running and not driver.tx_running

    @pytest.mark.asyncio()
    async def test_teardown_order_closes_an_armed_duplex_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The backends' plain teardown (stop_rx + stop_tx) closes an armed
        duplex stream — no separate stop_duplex call is needed anywhere."""
        driver, backend = _exclusive_driver(monkeypatch)
        await driver.start_rx(lambda _pcm: None)
        await driver.start_tx()
        assert driver.rx_running and driver.tx_running

        await driver.stop_rx()  # drops the callback; duplex stays for TX
        assert backend.duplex_streams[0].running
        await driver.stop_tx()  # no RX demand left → closes, no re-open
        all_streams = backend.rx_streams + backend.tx_streams + backend.duplex_streams
        assert not any(s.running for s in all_streams)
        assert not driver.rx_running and not driver.tx_running
        assert len(backend.rx_streams) == 1  # no RX re-open churn


class TestIcomSerialExclusiveDuplexTx:
    """MOR-546: _IcomSerialRadioBase (IC-7300 here) is a plain pass-through
    to the SAME driver, so the exclusive handoff covers the Icom serial USB
    radios (and the X6200 via the IC-705 class) with no backend change."""

    @pytest.mark.asyncio()
    async def test_ic7300_exclusive_tx_arm_uses_one_duplex_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from test_icom7610_serial_radio import _FakeSerialCivLink, _wait_until

        from rigplane.audio.usb_driver import UsbAudioDriver
        from rigplane.backends.ic7300 import Ic7300SerialRadio
        from rigplane.types import AudioCodec

        _patch_exclusive(monkeypatch)
        backend = FakeAudioBackend(
            devices=[DUPLEX_DEVICE], strict_device_exclusive=True
        )
        driver = UsbAudioDriver(
            rx_device="USB Audio CODEC",
            tx_device="USB Audio CODEC",
            backend=backend,
            rx_audio_channel="left",
        )
        radio = Ic7300SerialRadio(
            device="/dev/ttyUSB-fake",
            civ_link=_FakeSerialCivLink(),
            audio_driver=driver,
            audio_codec=AudioCodec.PCM_1CH_16BIT,
        )
        await radio.connect()
        # RX delivery is marshalled onto the owner loop (MOR-2465), so
        # injected frames land after a loop turn.
        received: list[bytes] = []
        try:
            await radio.start_rx(
                lambda pkt: received.append(b"" if pkt is None else pkt.data)
            )
            assert len(backend.rx_streams) == 1
            backend.rx_streams[0].inject_frame(b"\x01\x02")
            assert await _wait_until(lambda: len(received) >= 1)
            assert received == [b"\x01\x02"]

            await radio.start_tx()
            # ONE duplex stream; NO separate output stream on the device.
            assert len(backend.duplex_streams) == 1
            assert backend.tx_streams == []
            # RX frames keep flowing DURING the TX arm.
            backend.duplex_streams[0].inject_frame(b"\x03\x04")
            assert await _wait_until(lambda: len(received) >= 2)
            assert received[-1] == b"\x03\x04"
            await radio.push_tx(b"\x05\x06")
            assert backend.duplex_streams[0].written_frames == [b"\x05\x06"]

            await radio.stop_tx()
            # Disarm returns to plain RX with delivery preserved.
            running_rx = [s for s in backend.rx_streams if s.running]
            assert len(running_rx) == 1
            running_rx[0].inject_frame(b"\x07\x08")
            assert await _wait_until(lambda: len(received) >= 3)
            assert received[-1] == b"\x07\x08"
        finally:
            await radio.disconnect()
        # Teardown closes everything — nothing left running.
        all_streams = backend.rx_streams + backend.tx_streams + backend.duplex_streams
        assert not any(s.running for s in all_streams)


# ---------------------------------------------------------------------------
# MOR-546 B1 — a FAILED exclusive duplex open must not leave RX dead
# ---------------------------------------------------------------------------


def _auhal_minus50() -> None:
    """The ordinary (non-timeout) error a real ``sd.Stream`` open raises."""
    raise OSError(-50, "PaMacCore (AUHAL) err='-50' injected open failure")


def _assert_rx_alive(
    driver: UsbAudioDriver,
    backend: FakeAudioBackend,
    received: list[bytes],
) -> None:
    """Exactly one RX stream is RUNNING and frames reach the callback."""
    assert driver.rx_running
    running = [s for s in backend.rx_streams if s.running]
    assert len(running) == 1, "no running RX stream after the failed TX arm"
    running[0].inject_frame(b"\xf0\x0f")
    assert received[-1] == b"\xf0\x0f"


class TestExclusiveFailedDuplexOpen:
    """MOR-546 B1 regression: base recovered RX after a failed exclusive
    TX arm; the duplex rework left it silently dead (stored dead stream,
    start_rx/stop_rx joining it, no plain-RX restore)."""

    @pytest.mark.asyncio()
    async def test_session_order_stop_rx_failed_tx_then_restart(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AudioSession atomic RX_ONLY→RX_TX: stop_rx, start_tx fails,
        start_rx — the sequence ``AudioBus.restart_rx`` drives."""
        driver, backend = _exclusive_driver(monkeypatch)
        received: list[bytes] = []
        await driver.start_rx(received.append)
        await driver.stop_rx()
        backend.block_duplex_open = _auhal_minus50
        with pytest.raises(OSError):
            await driver.start_tx()
        backend.block_duplex_open = None
        await driver.start_rx(received.append)
        _assert_rx_alive(driver, backend, received)

    @pytest.mark.asyncio()
    async def test_poller_order_failed_tx_over_live_rx_restores_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Poller/CLI order: start_tx over a LIVE plain RX stream fails —
        RX must come back on its own, and stop/start cycles keep healing."""
        driver, backend = _exclusive_driver(monkeypatch)
        received: list[bytes] = []
        await driver.start_rx(received.append)
        backend.block_duplex_open = _auhal_minus50
        with pytest.raises(OSError):
            await driver.start_tx()
        _assert_rx_alive(driver, backend, received)
        for _ in range(3):
            await driver.stop_rx()
            await driver.start_rx(received.append)
            _assert_rx_alive(driver, backend, received)

    @pytest.mark.asyncio()
    async def test_open_timeout_restores_rx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        driver, backend = _exclusive_driver(monkeypatch, capture_open_timeout=0.05)
        received: list[bytes] = []
        await driver.start_rx(received.append)
        gate = threading.Event()
        backend.block_duplex_open = gate.wait  # stuck duplex open
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await driver.start_tx()
        _assert_rx_alive(driver, backend, received)
        gate.set()  # release the abandoned background open
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio()
    async def test_cancelled_open_restores_rx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        driver, backend = _exclusive_driver(monkeypatch, capture_open_timeout=5.0)
        received: list[bytes] = []
        await driver.start_rx(received.append)
        gate = threading.Event()
        backend.block_duplex_open = gate.wait
        arm = asyncio.create_task(driver.start_tx())
        await asyncio.sleep(0.1)  # reach the stuck duplex open
        arm.cancel()
        with pytest.raises(asyncio.CancelledError):
            await arm
        _assert_rx_alive(driver, backend, received)
        gate.set()
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio()
    async def test_yaesu_acquire_tx_failure_keeps_session_rx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whole stack: YaesuCatRadio + AudioSession.acquire_tx raising —
        the bus's restart_rx must land on a RUNNING stream, not a dead one."""
        from rigplane.backends.yaesu_cat.radio import YaesuCatRadio

        _patch_yaesu_offline(monkeypatch)
        driver, backend = _exclusive_driver(monkeypatch)
        radio = YaesuCatRadio(device="/dev/cu.fake", audio_driver=driver)
        session = radio.audio_session
        sub = await session.subscribe_rx("web-audio")
        try:
            backend.block_duplex_open = _auhal_minus50
            with pytest.raises(OSError):
                await session.acquire_tx("web-tx")
            backend.block_duplex_open = None
            assert radio.audio_bus.rx_active
            running = [s for s in backend.rx_streams if s.running]
            assert len(running) == 1
            running[0].inject_frame(b"\x13\x14")
            pkt = await sub.get(timeout=1.0)
            assert pkt is not None and pkt.data == b"\x13\x14"
        finally:
            await sub.release()
