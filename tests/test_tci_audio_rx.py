"""A1 RED: TCI adapter RX audio path (MOR-3821, spec 8.1/8.2/10.2)."""

from __future__ import annotations

import asyncio
import struct

import pytest

from rigplane.audio.lan_stream import AudioPacket
from rigplane.core.state_pipeline_contracts import FieldPath

from tci_adapter_stub import StubIcomRadio, StubProfile, build_store, make_handler


def _loaded_store():  # type: ignore[no-untyped-def]
    return build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "USB",
            FieldPath.active("main", "freq_mode", "data_mode"): 0,
            FieldPath.global_("tx_state", "split"): False,
            FieldPath.global_("operator_controls", "rit_freq"): 0,
            FieldPath.global_("tx_state", "rit_on"): False,
            FieldPath.global_("tx_state", "rit_tx"): False,
            FieldPath.global_("operator_controls", "power_level"): 128,
        }
    )


class TestPacketToPcm:
    def test_pcm_mono_passthrough(self) -> None:
        from rigplane.audio.pcm import packet_to_pcm

        from rigplane.core.types import AudioCodec

        payload = struct.pack("<4h", 0, 1000, -1000, 32767)
        frame = packet_to_pcm(
            AudioPacket(ident=0x9781, send_seq=7, data=payload),
            codec=AudioCodec.PCM_1CH_16BIT,
            sample_rate=48000,
            seq=3,
        )
        assert frame is not None
        assert frame.payload == payload
        assert frame.sample_rate == 48000
        assert frame.channels == 1
        assert frame.seq == 3

    def test_ulaw_decodes(self) -> None:
        from rigplane.audio._codecs import decode_ulaw_to_pcm16
        from rigplane.audio.pcm import packet_to_pcm

        from rigplane.core.types import AudioCodec

        raw = bytes([0x00, 0xFF, 0x80, 0x7F])
        frame = packet_to_pcm(
            AudioPacket(ident=0x9781, send_seq=1, data=raw),
            codec=AudioCodec.ULAW_1CH,
            sample_rate=8000,
            seq=0,
        )
        assert frame is not None
        assert frame.payload == decode_ulaw_to_pcm16(raw)
        assert frame.channels == 1

    def test_stereo_pcm_channels(self) -> None:
        from rigplane.audio.pcm import packet_to_pcm

        from rigplane.core.types import AudioCodec

        payload = struct.pack("<2h", 100, -100)
        frame = packet_to_pcm(
            AudioPacket(ident=0x9781, send_seq=1, data=payload),
            codec=AudioCodec.PCM_2CH_16BIT,
            sample_rate=48000,
            seq=0,
        )
        assert frame is not None
        assert frame.channels == 2
        assert frame.payload == payload

    def test_opus_without_decoder_is_none(self) -> None:
        from rigplane.audio.pcm import packet_to_pcm

        from rigplane.core.types import AudioCodec

        frame = packet_to_pcm(
            AudioPacket(ident=0x9781, send_seq=1, data=b"\x00" * 8),
            codec=AudioCodec.OPUS_1CH,
            sample_rate=48000,
            seq=0,
        )
        assert frame is None

    def test_opus_with_decoder(self) -> None:
        from rigplane.audio.pcm import packet_to_pcm

        from rigplane.core.types import AudioCodec

        expected = struct.pack("<2h", 11, 22)
        frame = packet_to_pcm(
            AudioPacket(ident=0x9781, send_seq=1, data=b"\x99" * 8),
            codec=AudioCodec.OPUS_1CH,
            sample_rate=48000,
            seq=9,
            opus_decoder=lambda data, channels: expected,
        )
        assert frame is not None
        assert frame.payload == expected
        assert frame.channels == 1


class TestAudioSettings:
    def test_defaults_per_rate(self) -> None:
        from rigplane.tci_adapter.audio import TciAudioSettings

        assert TciAudioSettings().samples_for(48000) == 2048
        assert TciAudioSettings().samples_for(24000) == 1024
        assert TciAudioSettings().samples_for(12000) == 512
        assert TciAudioSettings().samples_for(8000) == 256

    def test_samplerate_unsupported_keeps_current(self) -> None:
        from rigplane.tci_adapter.audio import TciAudioSettings

        settings = TciAudioSettings(samplerate=48000)
        assert settings.set_samplerate(44100) == 48000
        assert settings.set_samplerate(8000) == 8000

    def test_sample_type_reply_is_lowercase(self) -> None:
        from rigplane.tci_adapter.audio import TciAudioSettings

        settings = TciAudioSettings()
        assert settings.set_sample_type("FLOAT32") == "float32"
        assert settings.set_sample_type("int16") == "int16"

    def test_channels_request_two_keeps_one(self) -> None:
        from rigplane.tci_adapter.audio import TciAudioSettings

        settings = TciAudioSettings()
        assert settings.set_channels(2) == 1

    def test_samples_clamped(self) -> None:
        from rigplane.tci_adapter.audio import TciAudioSettings

        settings = TciAudioSettings()
        assert settings.set_samples(50) == 100
        assert settings.set_samples(4096) == 2048

    def test_buffering_clamped(self) -> None:
        from rigplane.tci_adapter.audio import TciAudioSettings

        settings = TciAudioSettings()
        assert settings.set_buffering(10) == 50
        assert settings.set_buffering(900) == 500


def _audio_radio(rate: int = 48000):  # type: ignore[no-untyped-def]
    """StubIcomRadio with a servable RX audio surface (AudioSession + bus)."""
    from rigplane.audio.bus import AudioBus
    from rigplane.audio.session import AudioSession

    from rigplane.core.types import AudioCodec

    radio = StubIcomRadio()
    radio.audio_codec = AudioCodec.PCM_1CH_16BIT
    radio.audio_sample_rate = rate
    bus = AudioBus(radio)

    async def _start_rx(callback, jitter_depth=5):  # type: ignore[no-untyped-def]
        radio.rx_callback = callback  # type: ignore[attr-defined]

    async def _stop_rx() -> None:
        radio.rx_callback = None  # type: ignore[attr-defined]

    radio.start_rx = _start_rx  # type: ignore[attr-defined]
    radio.stop_rx = _stop_rx  # type: ignore[attr-defined]
    radio.audio_session = AudioSession(radio, bus=bus)
    return radio


class TestHandlerAudioCommands:
    async def test_burst_has_stream_settings(self) -> None:
        handler = make_handler(radio=_audio_radio(), store=_loaded_store())
        burst = await handler.on_connect()
        text = "\n".join(burst)
        assert "audio_samplerate:48000;" in text
        assert "audio_stream_sample_type:float32;" in text
        assert "audio_stream_channels:1;" in text
        assert "audio_stream_samples:2048;" in text
        assert burst[-1] == "ready;"

    async def test_audio_start_stop_roundtrip(self) -> None:
        radio = _audio_radio()
        handler = make_handler(radio=radio, store=_loaded_store())
        outcome = await handler.on_command("audio_start", ("0",))
        assert outcome.to_sender == ("audio_start:0;",)
        assert handler.audio_active is True
        outcome = await handler.on_command("audio_stop", ("0",))
        assert outcome.to_sender == ("audio_stop:0;",)
        assert handler.audio_active is False

    async def test_audio_ignored_without_route(self) -> None:
        handler = make_handler(radio=StubIcomRadio(), store=_loaded_store())
        assert (await handler.on_command("audio_start", ("0",))) == __import__(
            "rigplane.tci.handler", fromlist=["CommandOutcome"]
        ).CommandOutcome()
        assert (await handler.on_command("audio_samplerate", ("0", "48000"))) == (
            __import__("rigplane.tci.handler", fromlist=["CommandOutcome"])
        ).CommandOutcome()


class TestRxPump:
    async def test_pump_delivers_resampled_reblocked_frames(self) -> None:
        from rigplane.audio.lan_stream import AudioPacket
        from rigplane.tci_adapter.audio import TciAudioSettings, TciRxPump

        radio = _audio_radio()
        pump = TciRxPump(
            radio=radio,
            owner="tci-1",
            settings=TciAudioSettings(samplerate=8000, samples=256),
        )
        await pump.start()
        try:
            payload = struct.pack("<960h", *([1000] * 960))
            for seq in range(2):
                radio.rx_callback(AudioPacket(ident=0x9781, send_seq=seq, data=payload))  # type: ignore[attr-defined]
            frame = await asyncio.wait_for(pump.get_frame(), timeout=5.0)
            assert frame is not None
            assert len(frame) > 64
        finally:
            await pump.stop()
        assert radio.audio_session._bus.subscriber_count == 0  # noqa: SLF001

    async def test_slow_client_does_not_stall_other(self) -> None:
        from rigplane.audio.lan_stream import AudioPacket
        from rigplane.tci_adapter.audio import TciAudioSettings, TciRxPump

        radio = _audio_radio(rate=8000)
        slow = TciRxPump(
            radio=radio,
            owner="tci-slow",
            settings=TciAudioSettings(samplerate=8000, samples=256),
        )
        fast = TciRxPump(
            radio=radio,
            owner="tci-fast",
            settings=TciAudioSettings(samplerate=8000, samples=256),
        )
        await slow.start()
        await fast.start()
        try:
            payload = struct.pack("<256h", *([500] * 256))
            for seq in range(40):
                radio.rx_callback(AudioPacket(ident=0x9781, send_seq=seq, data=payload))  # type: ignore[attr-defined]
            await asyncio.sleep(0.2)
            got = await asyncio.wait_for(fast.get_frame(), timeout=5.0)
            assert got is not None
            assert slow.dropped > 0
        finally:
            await slow.stop()
            await fast.stop()

    async def test_eesdr_receives_typed_rx_packets(self) -> None:
        pytest.importorskip("eesdr_tci", reason="mini-only dev dependency")
        import importlib

        from rigplane.audio.lan_stream import AudioPacket as LanPacket
        from rigplane.tci_adapter.server import build_tci_server

        listener_mod = importlib.import_module("eesdr_tci.listener")
        radio = _audio_radio(rate=8000)

        server = build_tci_server(radio=radio, state_store=_loaded_store(), port=0)
        await server.start()
        try:
            listener = listener_mod.Listener(
                f"ws://127.0.0.1:{server.bound_port}"
            )
            packets: list[object] = []

            async def _on_data(packet: object) -> None:
                packets.append(packet)

            listener.add_data_listener("*", _on_data)
            await listener.start()
            try:
                await listener.ready()
                await listener.send("audio_start:0;")
                async with asyncio.timeout(10.0):
                    while getattr(radio, "rx_callback", None) is None:
                        await asyncio.sleep(0.02)
                payload = struct.pack("<256h", *([700] * 256))
                for seq in range(4):
                    radio.rx_callback(LanPacket(ident=0x9781, send_seq=seq, data=payload))  # type: ignore[attr-defined]
                    await asyncio.sleep(0.05)
                async with asyncio.timeout(10.0):
                    while not packets:
                        await asyncio.sleep(0.02)
                assert len(packets) > 0
            finally:
                listener.shutdown()
                try:
                    async with asyncio.timeout(5.0):
                        await listener.wait()
                except Exception:
                    pass
        finally:
            await server.stop()
