"""TX-format single-source-of-truth contract guards.

These pin the boundaries where the negotiated RigPlane TX format (the
``AudioStreamContract`` resolved in ``IcomRadio.__init__``) must agree with the
PCM TX validator and the codec the radio actually puts on the wire. They guard
the *class* of the 3-day outage: a TX stage deriving its
codec/rate/channel/frame-size assumption independently from the negotiated one.

What each guard locks (all hermetic — no hardware, no sockets):

* **Codec single-source-of-truth** — every ``AudioCodec`` maps to exactly one
  channel count via ``route._CHANNELS_BY_CODEC``; a contract can never pair a
  1-channel codec with a 2-channel count (or vice versa), so the validator's
  ``frame_bytes`` can never silently diverge from the negotiated channel count.
* **Negotiated PCM contract -> raw PCM on the wire** — a PCM-negotiated radio
  resolves ``_audio_tx_codec == PCM_1CH_16BIT`` and
  ``_push_audio_tx_pcm_internal`` forwards the PCM frame *unchanged* (the
  "Opus-was-sent-when-PCM-expected" regression would flip this).
* **Negotiated Opus contract -> Opus on the wire** — the divergence is genuinely
  detectable: an Opus-negotiated radio transcodes instead of forwarding raw PCM.
* **Contract rate/channels feed the validator's frame size** — the bytes the
  validator requires are computed from the *same* negotiated rate/channels the
  contract carries, for every supported PCM rate.

The TX *frame-size* seam (capture output == validator required bytes) is locked
separately in ``test_audio_capture_tx_validator_seam.py``; this module locks the
codec/rate/channel agreement that sits next to it.
"""

from __future__ import annotations

import pytest

from rigplane.audio._transcoder import PcmAudioFormat
from rigplane.audio.route import (
    _CHANNELS_BY_CODEC,
    resolve_lan_audio_stream_request,
)
from rigplane.core.types import AudioCodec
from rigplane.profiles import get_radio_profile
from rigplane.radio import IcomRadio
from rigplane.runtime._audio_runtime_mixin import AudioRuntimeMixin


# ---------------------------------------------------------------------------
# Fakes — minimal, matching tests/test_audio_transcoder.py style.
# ---------------------------------------------------------------------------


class _RecordingTxAudioStream:
    """Records the exact bytes handed to the transport push (post codec branch)."""

    def __init__(self) -> None:
        self.pushed: list[bytes] = []

    async def push_tx(self, data: bytes) -> None:
        self.pushed.append(bytes(data))


class _ContractValidatorHarness(AudioRuntimeMixin):
    """Real ``AudioRuntimeMixin`` driven by a negotiated TX codec.

    Only the transport (``_audio_stream``) and ``_check_connected`` are stubbed.
    The codec branch in ``_push_audio_tx_pcm_internal`` and the size validation
    are unmodified production code.
    """

    def __init__(
        self,
        *,
        tx_codec: AudioCodec,
        sample_rate: int,
        channels: int,
        frame_ms: int,
    ) -> None:
        self._audio_stream = _RecordingTxAudioStream()  # type: ignore[assignment]
        self._audio_tx_codec = tx_codec
        self._pcm_tx_fmt = (sample_rate, channels, frame_ms)
        # Opus path needs a transcoder; provide a deterministic tagging fake so
        # the test can tell raw-PCM from transcoded output without libopus.
        self._pcm_transcoder = _TaggingTranscoder()  # type: ignore[assignment]
        self._pcm_transcoder_fmt = (sample_rate, channels, frame_ms)

    def _check_connected(self) -> None:  # type: ignore[override]
        return None


class _TaggingTranscoder:
    """Marks data as transcoded instead of encoding (no libopus dependency)."""

    def pcm_to_opus(self, pcm: bytes) -> bytes:
        return b"OPUS:" + bytes(pcm)


# Every PCM rate the transcoder/validator accepts, at the production 20 ms frame.
_PCM_RATES = [8000, 12000, 16000, 24000, 48000]


# ---------------------------------------------------------------------------
# Guard 1 — codec <-> channels single source of truth.
# ---------------------------------------------------------------------------


class TestCodecChannelsSingleSourceOfTruth:
    @pytest.mark.parametrize("codec", list(AudioCodec))
    def test_every_codec_has_exactly_one_channel_count(self, codec: AudioCodec) -> None:
        """``route._CHANNELS_BY_CODEC`` is the only place channels are derived.

        If a future edit adds a codec without a channel mapping, the
        ``resolve_lan_audio_stream_request`` ``rx_channels=_CHANNELS_BY_CODEC[...]``
        lookup would ``KeyError`` at runtime; this catches it at test time.
        """
        assert codec in _CHANNELS_BY_CODEC
        channels = _CHANNELS_BY_CODEC[codec]
        assert channels in (1, 2)
        # The channel count must match the "1CH"/"2CH" promise encoded in the
        # codec name, so a contract can never pair (e.g.) a 1ch codec with a
        # 2-sample-per-frame validator expectation.
        if "1CH" in codec.name:
            assert channels == 1, f"{codec.name} must be mono"
        if "2CH" in codec.name:
            assert channels == 2, f"{codec.name} must be stereo"

    def test_resolved_request_channels_track_resolved_codec(self) -> None:
        """The resolved request's tx/rx channels always equal the codec mapping.

        A drift here is exactly the channel-count divergence class: the contract
        would advertise a channel count the codec does not imply, and the
        validator's ``frame_bytes`` would silently expect the wrong size.
        """
        request = resolve_lan_audio_stream_request(
            profile=get_radio_profile("IC-7610"),
            requested_rx_codec=AudioCodec.PCM_2CH_16BIT,
            requested_sample_rate_hz=48000,
        )
        assert request.rx_channels == _CHANNELS_BY_CODEC[request.rx_codec]
        assert request.tx_channels == _CHANNELS_BY_CODEC[request.tx_codec]


# ---------------------------------------------------------------------------
# Guard 2 — negotiated PCM contract -> raw PCM on the wire (no Opus).
# ---------------------------------------------------------------------------


class TestNegotiatedContractDrivesWireCodec:
    def test_pcm_negotiated_radio_resolves_pcm_tx_codec(self) -> None:
        """A direct-LAN PCM radio negotiates ``_audio_tx_codec == PCM_1CH_16BIT``.

        This is the constructor-level source of truth: the same contract value
        the validator/codec branch reads. If conninfo resolution ever produced
        Opus for a PCM radio, the wire path below would transcode and the radio
        would reject (the false-suspect "Opus vs PCM" failure mode).
        """
        radio = IcomRadio("192.168.1.100", model="IC-7300")
        contract = radio.audio_stream_contract
        assert contract.tx_codec == AudioCodec.PCM_1CH_16BIT
        assert radio._audio_tx_codec == AudioCodec.PCM_1CH_16BIT
        # Channels/rate the validator will size frames from come from the same
        # contract object, not an independent default.
        assert contract.tx_channels == _CHANNELS_BY_CODEC[contract.tx_codec]

    @pytest.mark.asyncio
    async def test_pcm_tx_codec_forwards_raw_pcm_unchanged(self) -> None:
        """PCM-negotiated codec => frame reaches the transport byte-for-byte.

        Goes RED if ``_audio_tx_codec`` is perturbed to ``OPUS_1CH`` (the wire
        bytes would then be the ``OPUS:``-tagged transcode, not the raw frame) —
        see the companion test below that perturbs exactly that.
        """
        harness = _ContractValidatorHarness(
            tx_codec=AudioCodec.PCM_1CH_16BIT,
            sample_rate=48000,
            channels=1,
            frame_ms=20,
        )
        frame = b"\x11\x22" * 960  # 1920 bytes == fmt.frame_bytes for 48k/1/20
        await harness.push_audio_tx_pcm(frame)
        pushed = harness._audio_stream.pushed  # type: ignore[union-attr]
        assert pushed == [frame], "PCM contract must forward the frame unchanged"

    @pytest.mark.asyncio
    async def test_opus_tx_codec_transcodes_so_divergence_is_detectable(self) -> None:
        """Opus-negotiated codec => frame is transcoded, not forwarded raw.

        Proves the guard above is genuine: the same input under a different
        negotiated codec produces *different* wire bytes, so an
        Opus-when-PCM-expected (or vice versa) divergence is observable.
        """
        harness = _ContractValidatorHarness(
            tx_codec=AudioCodec.OPUS_1CH,
            sample_rate=48000,
            channels=1,
            frame_ms=20,
        )
        frame = b"\x11\x22" * 960
        await harness.push_audio_tx_pcm(frame)
        pushed = harness._audio_stream.pushed  # type: ignore[union-attr]
        assert pushed == [b"OPUS:" + frame]
        assert pushed != [frame], "Opus path must NOT forward raw PCM"


# ---------------------------------------------------------------------------
# Guard 3 — contract rate/channels feed the validator's frame size.
# ---------------------------------------------------------------------------


class TestContractRateChannelsFeedValidatorFrameSize:
    @pytest.mark.parametrize("sample_rate", _PCM_RATES)
    @pytest.mark.asyncio
    async def test_validator_accepts_exactly_contract_sized_frame(
        self, sample_rate: int
    ) -> None:
        """The validator's required bytes == bytes from the negotiated rate/ch.

        For each PCM rate, the frame size the validator enforces is computed from
        the *same* (sample_rate, channels, frame_ms) the TX format carries. A
        frame one sample short must be rejected; the exact-size frame accepted.
        Any future change that lets the validator size frames from a *different*
        rate/channel source than the contract breaks this.
        """
        channels, frame_ms = 1, 20
        expected = PcmAudioFormat(
            sample_rate=sample_rate, channels=channels, frame_ms=frame_ms
        ).frame_bytes

        harness = _ContractValidatorHarness(
            tx_codec=AudioCodec.PCM_1CH_16BIT,
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )

        good = b"\x00" * expected
        await harness.push_audio_tx_pcm(good)
        assert harness._audio_stream.pushed == [good]  # type: ignore[union-attr]

        # One sample short: the validator must reject, never forward.
        from rigplane.core.exceptions import AudioFormatError

        short = b"\x00" * (expected - 2)
        with pytest.raises(AudioFormatError, match="PCM frame size mismatch"):
            await harness.push_audio_tx_pcm(short)
        # The bad frame was not forwarded.
        assert harness._audio_stream.pushed == [good]  # type: ignore[union-attr]
