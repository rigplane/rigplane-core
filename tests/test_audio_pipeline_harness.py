"""In-process audio pipeline harness for WSJT-X/LAN TX regressions."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.lan_stream import AudioStream, MAX_AUDIO_PAYLOAD, TX_IDENT
from rigplane.audio_bridge import AudioBridge, FRAME_BYTES, SAMPLES_PER_FRAME
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.types import AudioCodec

from _audio_pipeline_helpers import (
    PcmDiagnostics,
    assert_contiguous_sequences,
    collect_tx_audio_packets,
    pcm_rms,
    sine_pcm16_mono,
)


class _RecordingAudioTransport:
    my_id = 0xAABBCCDD
    remote_id = 0x11223344

    def __init__(self) -> None:
        self.send_tracked = AsyncMock()

    async def receive_packet(self, *, timeout: float = 1.0) -> bytes:
        await asyncio.sleep(0)
        raise TimeoutError


async def test_bridge_tx_pipeline_sends_raw_pcm_when_tx_codec_is_pcm() -> None:
    transport = _RecordingAudioTransport()
    radio = IcomRadio("192.0.2.10", username="u", password="p", timeout=0.05)
    radio._connected = True
    radio._civ_transport = object()
    radio._conn_state = RadioConnectionState.CONNECTED
    radio._audio_tx_codec = AudioCodec.PCM_1CH_16BIT
    radio._audio_stream = AudioStream(transport)  # type: ignore[arg-type]

    backend = FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="BlackHole Test",
                input_channels=2,
                output_channels=2,
            )
        ]
    )
    bridge = AudioBridge(
        radio,
        device_name="BlackHole Test",
        tx_enabled=True,
        backend=backend,
    )

    frame_count = 4
    tone_frame = sine_pcm16_mono(1000.0, samples=SAMPLES_PER_FRAME)
    assert len(tone_frame) == FRAME_BYTES
    expected_pcm = tone_frame * frame_count

    await bridge.start()
    try:
        for _ in range(frame_count):
            backend.rx_streams[0].inject_frame(tone_frame)
            await asyncio.sleep(0.01)
    finally:
        await bridge.stop()
        radio._connected = False

    packets = collect_tx_audio_packets(transport.send_tracked.await_args_list)
    payload = b"".join(packet.data for packet in packets)
    diagnostics = PcmDiagnostics.from_pcm(payload)

    assert payload == expected_pcm
    assert diagnostics.frame_count == frame_count
    assert diagnostics.rms == pcm_rms(expected_pcm)
    assert diagnostics.rms > 0.0
    assert [len(packet.data) for packet in packets] == [
        size
        for _ in range(frame_count)
        for size in (MAX_AUDIO_PAYLOAD, FRAME_BYTES - MAX_AUDIO_PAYLOAD)
    ]
    assert {packet.ident for packet in packets} == {TX_IDENT}
    assert_contiguous_sequences(packets)


async def test_bridge_tx_pipeline_encodes_pcm_only_when_tx_codec_is_opus() -> None:
    transport = _RecordingAudioTransport()
    radio = IcomRadio("192.0.2.10", username="u", password="p", timeout=0.05)
    radio._connected = True
    radio._civ_transport = object()
    radio._conn_state = RadioConnectionState.CONNECTED
    radio._audio_tx_codec = AudioCodec.OPUS_1CH
    radio._audio_stream = AudioStream(transport)  # type: ignore[arg-type]

    class _FakeTranscoder:
        def pcm_to_opus(self, frame: bytes) -> bytes:
            return b"OPUS" + frame[:24]

    radio._get_pcm_transcoder = MagicMock(return_value=_FakeTranscoder())  # type: ignore[method-assign]

    backend = FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="BlackHole Test",
                input_channels=2,
                output_channels=2,
            )
        ]
    )
    bridge = AudioBridge(
        radio,
        device_name="BlackHole Test",
        tx_enabled=True,
        backend=backend,
    )

    tone_frame = sine_pcm16_mono(1000.0, samples=SAMPLES_PER_FRAME)

    await bridge.start()
    try:
        backend.rx_streams[0].inject_frame(tone_frame)
        await asyncio.sleep(0.01)
    finally:
        await bridge.stop()
        radio._connected = False

    packets = collect_tx_audio_packets(transport.send_tracked.await_args_list)
    payload = b"".join(packet.data for packet in packets)

    assert payload == b"OPUS" + tone_frame[:24]
    assert payload != tone_frame
    assert [len(packet.data) for packet in packets] == [28]
    assert_contiguous_sequences(packets)
