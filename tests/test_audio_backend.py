"""Tests for AudioBackend protocol, FakeAudioBackend, and PortAudioBackend."""

from __future__ import annotations


import asyncio

import pytest

from rigplane.audio.backend import (
    AudioBackend,
    AudioDeviceId,
    AudioDeviceInfo,
    FakeAudioBackend,
    FakeRxStream,
    FakeTxStream,
    PortAudioBackend,
    RxStream,
    TxStream,
    TxStreamHealth,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DUPLEX_DEVICE = AudioDeviceInfo(
    id=AudioDeviceId(0),
    name="USB Audio CODEC",
    input_channels=1,
    output_channels=1,
    default_samplerate=48_000,
    is_default_input=True,
    is_default_output=True,
)

INPUT_ONLY_DEVICE = AudioDeviceInfo(
    id=AudioDeviceId(1),
    name="Microphone",
    input_channels=2,
    output_channels=0,
)

OUTPUT_ONLY_DEVICE = AudioDeviceInfo(
    id=AudioDeviceId(2),
    name="Speakers",
    input_channels=0,
    output_channels=2,
)


@pytest.fixture()
def fake_backend() -> FakeAudioBackend:
    return FakeAudioBackend(
        devices=[DUPLEX_DEVICE, INPUT_ONLY_DEVICE, OUTPUT_ONLY_DEVICE]
    )


# ---------------------------------------------------------------------------
# AudioDeviceInfo
# ---------------------------------------------------------------------------


class TestAudioDeviceInfo:
    def test_duplex(self) -> None:
        assert DUPLEX_DEVICE.supports_rx is True
        assert DUPLEX_DEVICE.supports_tx is True
        assert DUPLEX_DEVICE.duplex is True

    def test_input_only(self) -> None:
        assert INPUT_ONLY_DEVICE.supports_rx is True
        assert INPUT_ONLY_DEVICE.supports_tx is False
        assert INPUT_ONLY_DEVICE.duplex is False

    def test_output_only(self) -> None:
        assert OUTPUT_ONLY_DEVICE.supports_rx is False
        assert OUTPUT_ONLY_DEVICE.supports_tx is True
        assert OUTPUT_ONLY_DEVICE.duplex is False

    def test_frozen(self) -> None:
        with pytest.raises(AttributeError):
            DUPLEX_DEVICE.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Protocol conformance (structural typing checks)
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_fake_backend_is_audio_backend(
        self, fake_backend: FakeAudioBackend
    ) -> None:
        assert isinstance(fake_backend, AudioBackend)

    def test_fake_rx_stream_is_rx_stream(self) -> None:
        assert isinstance(FakeRxStream(), RxStream)

    def test_fake_tx_stream_is_tx_stream(self) -> None:
        assert isinstance(FakeTxStream(), TxStream)

    def test_tx_stream_health_is_exported(self) -> None:
        health = TxStreamHealth().to_dict()
        assert health["write_failures"] == 0
        assert health["buffered_audio_ms"] == 0.0
        assert health["written_audio_ms"] == 0.0
        assert health["overrun_events"] == 0
        assert health["underrun_events"] == 0

    def test_portaudio_backend_is_audio_backend(self) -> None:
        # PortAudioBackend satisfies the protocol structurally (deps not needed here)
        backend = PortAudioBackend(dependency_loader=lambda: (None, None))
        assert isinstance(backend, AudioBackend)


# ---------------------------------------------------------------------------
# FakeAudioBackend — device listing
# ---------------------------------------------------------------------------


class TestFakeBackendDevices:
    def test_list_devices(self, fake_backend: FakeAudioBackend) -> None:
        devices = fake_backend.list_devices()
        assert len(devices) == 3
        assert devices[0].name == "USB Audio CODEC"

    def test_list_devices_returns_copy(self, fake_backend: FakeAudioBackend) -> None:
        d1 = fake_backend.list_devices()
        d2 = fake_backend.list_devices()
        assert d1 is not d2

    def test_empty_backend(self) -> None:
        backend = FakeAudioBackend()
        assert backend.list_devices() == []

    def test_open_rx_unknown_device(self, fake_backend: FakeAudioBackend) -> None:
        with pytest.raises(ValueError, match="Unknown device"):
            fake_backend.open_rx(AudioDeviceId(99))

    def test_open_tx_unknown_device(self, fake_backend: FakeAudioBackend) -> None:
        with pytest.raises(ValueError, match="Unknown device"):
            fake_backend.open_tx(AudioDeviceId(99))


# ---------------------------------------------------------------------------
# FakeRxStream lifecycle
# ---------------------------------------------------------------------------


class TestFakeRxStreamLifecycle:
    @pytest.mark.asyncio()
    async def test_start_stop(self, fake_backend: FakeAudioBackend) -> None:
        stream = fake_backend.open_rx(DUPLEX_DEVICE.id)
        assert not stream.running

        received: list[bytes] = []
        await stream.start(received.append)
        assert stream.running
        assert stream.started_count == 1

        await stream.stop()
        assert not stream.running
        assert stream.stopped_count == 1

    @pytest.mark.asyncio()
    async def test_double_start_raises(self, fake_backend: FakeAudioBackend) -> None:
        stream = fake_backend.open_rx(DUPLEX_DEVICE.id)
        await stream.start(lambda _: None)
        with pytest.raises(RuntimeError, match="already running"):
            await stream.start(lambda _: None)
        await stream.stop()

    @pytest.mark.asyncio()
    async def test_inject_frame(self, fake_backend: FakeAudioBackend) -> None:
        stream = fake_backend.open_rx(DUPLEX_DEVICE.id)
        received: list[bytes] = []
        await stream.start(received.append)

        stream.inject_frame(b"\x00\x01\x02\x03")
        assert received == [b"\x00\x01\x02\x03"]

        stream.inject_frame(b"\xff")
        assert len(received) == 2

        await stream.stop()

    @pytest.mark.asyncio()
    async def test_inject_after_stop_is_noop(
        self, fake_backend: FakeAudioBackend
    ) -> None:
        stream = fake_backend.open_rx(DUPLEX_DEVICE.id)
        received: list[bytes] = []
        await stream.start(received.append)
        await stream.stop()

        stream.inject_frame(b"\x00")
        assert received == []  # callback cleared

    @pytest.mark.asyncio()
    async def test_tracks_opened_streams(self, fake_backend: FakeAudioBackend) -> None:
        s1 = fake_backend.open_rx(DUPLEX_DEVICE.id)
        s2 = fake_backend.open_rx(INPUT_ONLY_DEVICE.id)
        assert fake_backend.rx_streams == [s1, s2]


# ---------------------------------------------------------------------------
# FakeTxStream lifecycle
# ---------------------------------------------------------------------------


class TestFakeTxStreamLifecycle:
    @pytest.mark.asyncio()
    async def test_start_stop(self, fake_backend: FakeAudioBackend) -> None:
        stream = fake_backend.open_tx(DUPLEX_DEVICE.id)
        assert not stream.running

        await stream.start()
        assert stream.running
        assert stream.started_count == 1

        await stream.stop()
        assert not stream.running
        assert stream.stopped_count == 1

    @pytest.mark.asyncio()
    async def test_double_start_raises(self, fake_backend: FakeAudioBackend) -> None:
        stream = fake_backend.open_tx(DUPLEX_DEVICE.id)
        await stream.start()
        with pytest.raises(RuntimeError, match="already running"):
            await stream.start()
        await stream.stop()

    @pytest.mark.asyncio()
    async def test_write(self, fake_backend: FakeAudioBackend) -> None:
        stream = fake_backend.open_tx(DUPLEX_DEVICE.id)
        await stream.start()

        await stream.write(b"\x00\x01")
        await stream.write(b"\x02\x03")
        assert stream.written_frames == [b"\x00\x01", b"\x02\x03"]
        assert stream.write_health.frames_queued == 2
        assert stream.write_health.write_attempts == 2
        assert stream.write_health.writes_completed == 2

        await stream.stop()

    @pytest.mark.asyncio()
    async def test_write_health_tracks_fake_write_failure(
        self, fake_backend: FakeAudioBackend
    ) -> None:
        stream = fake_backend.open_tx(DUPLEX_DEVICE.id)
        await stream.start()
        stream.fail_on_write = OSError("backend write failed")

        with pytest.raises(OSError, match="backend write failed"):
            await stream.write(b"\x00\x01")

        health = stream.write_health
        assert health.write_attempts == 1
        assert health.writes_completed == 0
        assert health.write_failures == 1
        assert health.last_error == "OSError: backend write failed"

        await stream.stop()

    @pytest.mark.asyncio()
    async def test_write_when_stopped_raises(
        self, fake_backend: FakeAudioBackend
    ) -> None:
        stream = fake_backend.open_tx(DUPLEX_DEVICE.id)
        with pytest.raises(RuntimeError, match="not running"):
            await stream.write(b"\x00")

    @pytest.mark.asyncio()
    async def test_tracks_opened_streams(self, fake_backend: FakeAudioBackend) -> None:
        s1 = fake_backend.open_tx(DUPLEX_DEVICE.id)
        s2 = fake_backend.open_tx(OUTPUT_ONLY_DEVICE.id)
        assert fake_backend.tx_streams == [s1, s2]


# ---------------------------------------------------------------------------
# PortAudioBackend — dependency loading
# ---------------------------------------------------------------------------


class TestPortAudioBackendDeps:
    def test_missing_deps_raises(self) -> None:
        def _fail() -> tuple:
            raise ImportError("no sounddevice")

        backend = PortAudioBackend(dependency_loader=_fail)
        with pytest.raises(ImportError, match="PortAudioBackend requires"):
            backend.list_devices()

    def test_dependency_loader_called_once(self) -> None:
        call_count = 0

        class FakeSd:
            class default:
                device = [0, 0]

            @staticmethod
            def query_devices() -> list[dict]:
                return [
                    {
                        "index": 0,
                        "name": "Test",
                        "max_input_channels": 1,
                        "max_output_channels": 1,
                    }
                ]

        class FakeNp:
            pass

        def loader() -> tuple:
            nonlocal call_count
            call_count += 1
            return FakeSd(), FakeNp()

        backend = PortAudioBackend(dependency_loader=loader)
        backend.list_devices()
        backend.list_devices()
        assert call_count == 1  # cached

    def test_list_devices_via_loader(self) -> None:
        class FakeSd:
            class default:
                device = [0, 1]

            @staticmethod
            def query_devices() -> list[dict]:
                return [
                    {
                        "index": 0,
                        "name": "Mic",
                        "max_input_channels": 2,
                        "max_output_channels": 0,
                        "default_samplerate": 44100,
                    },
                    {
                        "index": 1,
                        "name": "Speaker",
                        "max_input_channels": 0,
                        "max_output_channels": 2,
                        "default_samplerate": 48000,
                    },
                ]

        backend = PortAudioBackend(dependency_loader=lambda: (FakeSd(), object()))
        devices = backend.list_devices()
        assert len(devices) == 2
        assert devices[0].name == "Mic"
        assert devices[0].is_default_input is True
        assert devices[0].is_default_output is False
        assert devices[0].input_channels == 2
        assert devices[1].name == "Speaker"
        assert devices[1].is_default_output is True
        assert devices[1].output_channels == 2

    def test_open_rx_returns_rx_stream(self) -> None:
        class FakeSd:
            class InputStream:
                def __init__(self, **kw: object) -> None:
                    pass

        backend = PortAudioBackend(dependency_loader=lambda: (FakeSd(), object()))
        stream = backend.open_rx(AudioDeviceId(0))
        assert isinstance(stream, RxStream)

    def test_open_tx_returns_tx_stream(self) -> None:
        class FakeSd:
            class OutputStream:
                def __init__(self, **kw: object) -> None:
                    pass

        class FakeNp:
            pass

        backend = PortAudioBackend(dependency_loader=lambda: (FakeSd(), FakeNp()))
        stream = backend.open_tx(AudioDeviceId(0))
        assert isinstance(stream, TxStream)

    @pytest.mark.asyncio()
    async def test_portaudio_tx_write_drops_oldest_when_buffer_full(self) -> None:
        class FakeSd:
            class OutputStream:
                def __init__(self, **kw: object) -> None:
                    pass

                def start(self) -> None:
                    pass

                def stop(self) -> None:
                    pass

                def close(self) -> None:
                    pass

        class FakeNp:
            pass

        backend = PortAudioBackend(dependency_loader=lambda: (FakeSd(), FakeNp()))
        stream = backend.open_tx(
            AudioDeviceId(0), sample_rate=48_000, channels=16, frame_ms=20
        )
        await stream.start()

        frame = b"x" * (320 * 16 * 2)
        for _ in range(180):
            await stream.write(frame)

        health = stream.write_health
        assert health.frames_queued == 180
        assert health.frames_dropped == 30
        assert health.overrun_events == 30
        assert health.buffered_audio_ms == pytest.approx(1000.0)
        assert health.dropped_audio_ms == pytest.approx(200.0)
        assert health.overrun_audio_ms == pytest.approx(200.0)

        await stream.stop()

    @pytest.mark.asyncio()
    async def test_portaudio_tx_callback_consumes_realtime_without_blocking_write(
        self,
    ) -> None:
        created: list[object] = []

        class FakeSd:
            class OutputStream:
                def __init__(self, **kw: object) -> None:
                    self.callback = kw["callback"]
                    created.append(self)

                def start(self) -> None:
                    pass

                def stop(self) -> None:
                    pass

                def close(self) -> None:
                    pass

        def frame(idx: int) -> bytes:
            out = bytearray()
            base = idx * 320 * 16
            for sample in range(320):
                for channel in range(16):
                    value = (base + sample * 16 + channel) % 32768
                    out.extend(value.to_bytes(2, "little", signed=True))
            return bytes(out)

        backend = PortAudioBackend(dependency_loader=lambda: (FakeSd(), object()))
        stream = backend.open_tx(
            AudioDeviceId(0), sample_rate=48_000, channels=16, frame_ms=20
        )
        await stream.start()
        output = created[0]

        input_frames = [frame(idx) for idx in range(150)]
        output_frames: list[bytes] = []

        async def enqueue_one_second() -> None:
            for pcm in input_frames:
                await stream.write(pcm)

        await asyncio.wait_for(enqueue_one_second(), timeout=0.25)
        assert stream.write_health.buffered_audio_ms == pytest.approx(1000.0)

        for _ in range(50):
            outdata = bytearray(960 * 16 * 2)
            output.callback(outdata, 960, None, None)  # type: ignore[attr-defined]
            output_frames.append(bytes(outdata))

        assert b"".join(output_frames) == b"".join(input_frames)
        assert len(output_frames) == 50
        assert [len(write) for write in output_frames] == [960 * 16 * 2] * 50

        health = stream.write_health
        assert health.frames_queued == 150
        assert health.frames_dropped == 0
        assert health.write_attempts == 50
        assert health.writes_completed == 50
        assert health.queued_audio_ms == pytest.approx(1000.0)
        assert health.buffered_audio_ms == 0.0
        assert health.consumed_audio_ms == pytest.approx(1000.0)
        assert health.written_audio_ms == pytest.approx(1000.0)
        assert health.dropped_audio_ms == 0.0
        assert health.underrun_audio_ms == 0.0
        assert health.write_calls_per_sec_ewma is not None

        await stream.stop()

    @pytest.mark.asyncio()
    async def test_portaudio_tx_callback_reports_underrun_with_silence(
        self,
    ) -> None:
        backend = PortAudioBackend(dependency_loader=lambda: (object(), object()))
        stream = backend.open_tx(AudioDeviceId(0), sample_rate=48_000, channels=2)
        outdata = bytearray(960 * 2 * 2)

        stream._output_callback(outdata, 960, None, None)  # type: ignore[attr-defined]

        assert bytes(outdata) == b"\x00" * (960 * 2 * 2)
        health = stream.write_health
        assert health.write_attempts == 1
        assert health.writes_completed == 1
        assert health.consumed_audio_ms == 0.0
        assert health.written_audio_ms == pytest.approx(20.0)
        assert health.underrun_events == 1
        assert health.underrun_audio_ms == pytest.approx(20.0)

    @pytest.mark.asyncio()
    async def test_portaudio_tx_callback_handles_output_failure(self) -> None:
        class FakeSd:
            class OutputStream:
                def __init__(self, **kw: object) -> None:
                    pass

                def start(self) -> None:
                    pass

                def stop(self) -> None:
                    pass

                def close(self) -> None:
                    pass

        backend = PortAudioBackend(dependency_loader=lambda: (FakeSd(), object()))
        stream = backend.open_tx(AudioDeviceId(0))

        await stream.start()
        await stream.write(b"\x00\x01")
        outdata = bytearray(1)
        stream._output_callback(outdata, 1, None, None)  # type: ignore[attr-defined]

        health = stream.write_health
        assert health.frames_queued == 1
        assert health.write_attempts == 1
        assert health.writes_completed == 0
        assert health.write_failures == 1
        assert health.callback_errors == 1
        assert health.last_error is not None
        assert health.last_error.startswith("ValueError: output buffer is 1 bytes")
        assert outdata == bytearray(1)

        await stream.stop()

    @pytest.mark.asyncio()
    async def test_portaudio_tx_callback_preserves_2ch_interleaving(self) -> None:
        backend = PortAudioBackend(dependency_loader=lambda: (object(), object()))
        stream = backend.open_tx(AudioDeviceId(0), sample_rate=48_000, channels=2)
        pcm = b"".join(
            value.to_bytes(2, "little", signed=True)
            for value in (100, -100, 200, -200, 300, -300)
        )

        stream._running = True  # type: ignore[attr-defined]
        await stream.write(pcm)
        outdata = bytearray(len(pcm))
        stream._output_callback(outdata, 3, None, None)  # type: ignore[attr-defined]

        assert bytes(outdata) == pcm
        assert stream.write_health.underrun_events == 0

    @pytest.mark.asyncio()
    async def test_portaudio_tx_callback_records_status_flags(self) -> None:
        class Status:
            output_underflow = True

            def __bool__(self) -> bool:
                return True

        backend = PortAudioBackend(dependency_loader=lambda: (object(), object()))
        stream = backend.open_tx(AudioDeviceId(0), sample_rate=48_000, channels=2)
        outdata = bytearray(960 * 2 * 2)

        stream._output_callback(outdata, 960, None, Status())  # type: ignore[attr-defined]

        health = stream.write_health
        assert health.callback_status_flags == {"output_underflow": 1}
        assert health.underrun_events == 1

    @pytest.mark.asyncio()
    async def test_portaudio_tx_stop_closes_without_drain_wait(self) -> None:
        created: list[object] = []

        class FakeSd:
            class OutputStream:
                def __init__(self, **kw: object) -> None:
                    self.stopped = False
                    self.closed = False
                    created.append(self)

                def start(self) -> None:
                    pass

                def stop(self) -> None:
                    self.stopped = True

                def close(self) -> None:
                    self.closed = True

        backend = PortAudioBackend(dependency_loader=lambda: (FakeSd(), object()))
        stream = backend.open_tx(AudioDeviceId(0), sample_rate=48_000, channels=16)
        await stream.start()
        await stream.write(b"x" * (320 * 16 * 2))

        await asyncio.wait_for(stream.stop(), timeout=0.05)

        output = created[0]
        assert output.stopped is True  # type: ignore[attr-defined]
        assert output.closed is True  # type: ignore[attr-defined]
        assert stream.write_health.buffered_audio_ms == 0.0
