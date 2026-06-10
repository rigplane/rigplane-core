"""Falsification tests for MOR-591 PR-1: PcmFrame + LAN decode-at-ingress.

ADR §3.5 (tenet T1 PCM spine): the LAN transport adapter decodes any
compressed wire codec (Icom LAN-negotiated uLaw or Opus) to PCM s16le
ONCE at ingress and DUAL-PUBLISHES — the legacy :class:`AudioPacket`
keeps flowing unchanged to the existing callback (Pro-pinned carrier),
while registered PCM taps receive the decoded :class:`PcmFrame`.

PR-1 is additive: no per-consumer decoder is removed here (that is the
PR-2 follow-up). These tests pin:

1. uLaw ingress → correctly decoded s16le PcmFrame (known mu-law
   values) + legacy AudioPacket untouched;
2. Opus ingress → decoded through the Opus transcoder at the
   negotiated (sample_rate, channels, 20 ms) format;
3. PCM16-native (IC-7610 LAN default PCM_2CH_16BIT) → zero-copy
   passthrough, byte-identical legacy path;
4. no PCM taps registered → no decode work at all (cost-identical);
5. monotonic PcmFrame.seq across uint16 send_seq wrap + gap (None)
   forwarding;
6. failure isolation: tap exceptions and a missing Opus backend never
   disturb the legacy carrier.
"""

from __future__ import annotations

import logging
import struct
from typing import Any
from unittest.mock import MagicMock

import pytest
from _audio_stream_fake import FakeAudioStream

import rigplane.audio
from rigplane.audio import AudioPacket, PcmFrame
from rigplane.audio._codecs import decode_ulaw_to_pcm16
from rigplane.core.types import AudioCodec
from rigplane.runtime.radio import IcomRadio

_MIXIN_MODULE = "rigplane.runtime._audio_runtime_mixin"


def _lan_radio(codec: AudioCodec, stream: FakeAudioStream) -> IcomRadio:
    """Connected LAN radio with a fake audio stream and a forced RX codec."""
    radio = IcomRadio("192.168.1.100")
    radio._connected = True
    radio._civ_transport = MagicMock()
    radio._audio_stream = stream  # type: ignore[assignment]
    radio._audio_codec = codec
    return radio


class _StubTranscoder:
    """Deterministic stand-in for PcmOpusTranscoder (no libopus needed)."""

    def opus_to_pcm(self, opus_data: bytes) -> bytes:
        return b"pcm:" + bytes(opus_data)


# ---------------------------------------------------------------------------
# Carrier export (additive — the Pro superset pin must stay green)
# ---------------------------------------------------------------------------


def test_pcm_frame_exported_additively() -> None:
    assert "PcmFrame" in rigplane.audio.__all__
    from rigplane.audio.pcm import PcmFrame as canonical

    assert rigplane.audio.PcmFrame is canonical


# ---------------------------------------------------------------------------
# uLaw ingress decode
# ---------------------------------------------------------------------------


async def test_ulaw_ingress_publishes_decoded_pcm_frame() -> None:
    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.ULAW_1CH, stream)
    legacy: list[AudioPacket | None] = []
    frames: list[PcmFrame | None] = []
    radio.add_pcm_rx_tap(frames.append)
    await radio.start_rx(legacy.append)

    ulaw = bytes([0x00, 0x80]) + bytes(range(64))
    pkt = AudioPacket(ident=0x9781, send_seq=7, data=ulaw)
    stream.emit_rx(pkt)

    # Dual-publish: the legacy carrier is the SAME object, untouched.
    assert legacy == [pkt]
    assert legacy[0] is pkt

    [frame] = frames
    assert isinstance(frame, PcmFrame)
    assert frame.sample_rate == radio.audio_sample_rate
    assert frame.channels == 1
    assert frame.seq == 0
    # One s16le sample per uLaw byte.
    assert len(frame.payload) == 2 * len(ulaw)
    # Known anchor: mu-law byte 0x00 decodes to -32124 (table maximum).
    assert frame.payload[:2] == struct.pack("<h", -32124)
    # Bit-exact match with the production decoder the broadcaster's
    # per-consumer branch would have applied (decode-once equivalence).
    assert frame.payload == decode_ulaw_to_pcm16(ulaw)


async def test_ulaw_stereo_ingress_reports_two_channels() -> None:
    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.ULAW_2CH, stream)
    frames: list[PcmFrame | None] = []
    radio.add_pcm_rx_tap(frames.append)
    await radio.start_rx(lambda _pkt: None)

    stream.emit_rx(AudioPacket(ident=0x9781, send_seq=0, data=bytes(8)))

    [frame] = frames
    assert frame is not None
    assert frame.channels == 2
    assert frame.payload == decode_ulaw_to_pcm16(bytes(8))


# ---------------------------------------------------------------------------
# Opus ingress decode
# ---------------------------------------------------------------------------


async def test_opus_ingress_decodes_via_negotiated_transcoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[tuple[int, int, int]] = []

    def _factory(*, sample_rate: int, channels: int, frame_ms: int) -> Any:
        created.append((sample_rate, channels, frame_ms))
        return _StubTranscoder()

    monkeypatch.setattr(f"{_MIXIN_MODULE}.create_pcm_opus_transcoder", _factory)

    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.OPUS_2CH, stream)
    legacy: list[AudioPacket | None] = []
    frames: list[PcmFrame | None] = []
    radio.add_pcm_rx_tap(frames.append)
    await radio.start_rx(legacy.append)

    opus_payload = b"\xde\xad\xbe\xef"
    pkt = AudioPacket(ident=0x9781, send_seq=1, data=opus_payload)
    stream.emit_rx(pkt)
    stream.emit_rx(AudioPacket(ident=0x9781, send_seq=2, data=b"\x01\x02"))

    # Decoder negotiated once at the radio's (sample_rate, channels, 20ms).
    assert created == [(radio.audio_sample_rate, 2, 20)]
    assert [f.payload for f in frames if f is not None] == [
        b"pcm:" + opus_payload,
        b"pcm:\x01\x02",
    ]
    assert all(f is not None and f.channels == 2 for f in frames)
    # Legacy carrier still ships the compressed wire payload (dual-publish).
    assert [p.data for p in legacy if p is not None] == [opus_payload, b"\x01\x02"]


async def test_opus_ingress_backend_failure_keeps_legacy_carrier(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[int] = []

    def _factory(**_kwargs: Any) -> Any:
        calls.append(1)
        raise RuntimeError("no opus backend")

    monkeypatch.setattr(f"{_MIXIN_MODULE}.create_pcm_opus_transcoder", _factory)

    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.OPUS_1CH, stream)
    legacy: list[AudioPacket | None] = []
    frames: list[PcmFrame | None] = []
    radio.add_pcm_rx_tap(frames.append)
    await radio.start_rx(legacy.append)

    with caplog.at_level(logging.WARNING, logger=_MIXIN_MODULE):
        stream.emit_rx(AudioPacket(ident=0x9781, send_seq=0, data=b"\x01"))
        stream.emit_rx(AudioPacket(ident=0x9781, send_seq=1, data=b"\x02"))

    # Dead-flagged after the first failure: factory not retried per frame.
    assert calls == [1]
    assert frames == []
    assert len(legacy) == 2  # legacy carrier unaffected
    assert any("Opus decode unavailable" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# PCM16-native passthrough (IC-7610 LAN default)
# ---------------------------------------------------------------------------


async def test_pcm16_native_ingress_is_zero_copy_passthrough() -> None:
    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.PCM_2CH_16BIT, stream)
    legacy: list[AudioPacket | None] = []
    frames: list[PcmFrame | None] = []
    radio.add_pcm_rx_tap(frames.append)
    await radio.start_rx(legacy.append)

    payload = struct.pack("<4h", 1, -2, 3, -4)
    pkt = AudioPacket(ident=0x9781, send_seq=3, data=payload)
    stream.emit_rx(pkt)

    assert legacy == [pkt]
    [frame] = frames
    assert frame is not None
    # The PcmFrame payload IS the wire payload — no decode, no copy.
    assert frame.payload is pkt.data
    assert frame.channels == 2
    assert frame.sample_rate == radio.audio_sample_rate


async def test_no_pcm_taps_means_no_decode_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default path (no PCM consumers): byte- and cost-identical."""

    def _boom(_data: bytes) -> bytes:
        raise AssertionError("decode must not run without PCM taps")

    monkeypatch.setattr(f"{_MIXIN_MODULE}.decode_ulaw_to_pcm16", _boom)

    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.ULAW_1CH, stream)
    legacy: list[AudioPacket | None] = []
    await radio.start_rx(legacy.append)

    pkt = AudioPacket(ident=0x9781, send_seq=0, data=bytes(4))
    stream.emit_rx(pkt)

    assert legacy == [pkt]  # legacy delivery, no decode attempted


async def test_callback_identity_preserved_for_legacy_consumers() -> None:
    """The bus callback handed to AudioStream.start_rx is NOT wrapped."""
    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.PCM_2CH_16BIT, stream)

    def _cb(_pkt: AudioPacket | None) -> None:
        pass

    await radio.start_rx(_cb)
    assert stream.last_start_rx_callback is _cb


# ---------------------------------------------------------------------------
# Sequence + gap semantics
# ---------------------------------------------------------------------------


async def test_pcm_seq_is_monotonic_and_gaps_forward_as_none() -> None:
    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.PCM_1CH_16BIT, stream)
    frames: list[PcmFrame | None] = []
    radio.add_pcm_rx_tap(frames.append)
    await radio.start_rx(lambda _pkt: None)

    stream.emit_rx(AudioPacket(ident=0x9781, send_seq=0xFFFF, data=b"\x01\x02"))
    stream.emit_rx(None)  # jitter-buffer gap placeholder
    stream.emit_rx(AudioPacket(ident=0x9781, send_seq=0x0000, data=b"\x03\x04"))

    assert frames[1] is None
    seqs = [f.seq for f in frames if f is not None]
    assert seqs == [0, 1]  # monotonic, no uint16 wrap


async def test_pcm_seq_resets_per_rx_session() -> None:
    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.PCM_1CH_16BIT, stream)
    frames: list[PcmFrame | None] = []
    radio.add_pcm_rx_tap(frames.append)

    await radio.start_rx(lambda _pkt: None)
    stream.emit_rx(AudioPacket(ident=0x9781, send_seq=1, data=b"\x01\x02"))
    await radio.stop_rx()

    await radio.start_rx(lambda _pkt: None)
    stream.emit_rx(AudioPacket(ident=0x9781, send_seq=2, data=b"\x03\x04"))

    assert [f.seq for f in frames if f is not None] == [0, 0]


# ---------------------------------------------------------------------------
# Tap management + failure isolation
# ---------------------------------------------------------------------------


async def test_remove_pcm_rx_tap_stops_delivery() -> None:
    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.PCM_1CH_16BIT, stream)
    frames: list[PcmFrame | None] = []
    radio.add_pcm_rx_tap(frames.append)
    radio.add_pcm_rx_tap(frames.append)  # idempotent registration
    await radio.start_rx(lambda _pkt: None)

    stream.emit_rx(AudioPacket(ident=0x9781, send_seq=0, data=b"\x01\x02"))
    assert len(frames) == 1

    radio.remove_pcm_rx_tap(frames.append)
    radio.remove_pcm_rx_tap(frames.append)  # double-remove is a no-op
    stream.emit_rx(AudioPacket(ident=0x9781, send_seq=1, data=b"\x03\x04"))
    assert len(frames) == 1


async def test_tap_exception_does_not_break_legacy_or_other_taps() -> None:
    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.PCM_1CH_16BIT, stream)
    legacy: list[AudioPacket | None] = []
    frames: list[PcmFrame | None] = []

    def _bad_tap(_frame: PcmFrame | None) -> None:
        raise RuntimeError("tap boom")

    radio.add_pcm_rx_tap(_bad_tap)
    radio.add_pcm_rx_tap(frames.append)
    await radio.start_rx(legacy.append)

    pkt = AudioPacket(ident=0x9781, send_seq=0, data=b"\x01\x02")
    stream.emit_rx(pkt)  # must not raise into the RX loop

    assert legacy == [pkt]
    assert len(frames) == 1


async def test_8bit_pcm_codec_publishes_no_frame_but_legacy_flows() -> None:
    """No s16le mapping for 8-bit wire codecs — legacy carrier only."""
    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.PCM_1CH_8BIT, stream)
    legacy: list[AudioPacket | None] = []
    frames: list[PcmFrame | None] = []
    radio.add_pcm_rx_tap(frames.append)
    await radio.start_rx(legacy.append)

    pkt = AudioPacket(ident=0x9781, send_seq=0, data=bytes(4))
    stream.emit_rx(pkt)

    assert legacy == [pkt]
    assert frames == []


# ---------------------------------------------------------------------------
# Real-codec round-trip (CI has libopus; macOS dev hosts may not)
# ---------------------------------------------------------------------------

try:  # opuslib raises a plain Exception (not ImportError) without libopus
    import opuslib  # noqa: F401

    _HAS_OPUS = True
except Exception:  # pragma: no cover - environment-dependent
    _HAS_OPUS = False


@pytest.mark.skipif(not _HAS_OPUS, reason="libopus not available")
async def test_opus_ingress_real_codec_roundtrip() -> None:
    import math

    from rigplane.audio._transcoder import create_pcm_opus_transcoder

    encoder = create_pcm_opus_transcoder(sample_rate=48000, channels=1, frame_ms=20)
    samples = [
        int(20000 * math.sin(2 * math.pi * 1000 * n / 48000)) for n in range(960)
    ]
    pcm_in = struct.pack("<960h", *samples)
    opus_frame = encoder.pcm_to_opus(pcm_in)

    stream = FakeAudioStream()
    radio = _lan_radio(AudioCodec.OPUS_1CH, stream)
    frames: list[PcmFrame | None] = []
    radio.add_pcm_rx_tap(frames.append)
    await radio.start_rx(lambda _pkt: None)

    stream.emit_rx(AudioPacket(ident=0x9781, send_seq=0, data=opus_frame))

    [frame] = frames
    assert frame is not None
    assert len(frame.payload) == 1920  # 20 ms mono s16le at 48 kHz
    decoded = struct.unpack("<960h", frame.payload)
    assert max(abs(s) for s in decoded) > 1000  # decoded audio, not silence
