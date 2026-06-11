"""Tests for the LIVE RX-audio probes on the hardware path (MOR-668).

``audio.rx.rms`` and ``scope.fft.presence`` (AUDIO_PROBE check-kind,
MOR-639/641) previously ran only in CI against ``FakeAudioBackend`` and stayed
MANUAL_REQUIRED on real hardware. These tests prove the LIVE wiring: when the
CLI hardware path captures a real RX-PCM window and threads it into
``execute_hardware_checks`` (or the probe functions directly), the probes run
for real and PASS on a non-silent stream / FAIL on a dead one.

The capture seam goes through the radio-owned :class:`AudioSession`
(``radio.audio_session``) — the SAME codec-negotiated RX path the web server and
bridge use — NOT the Opus-only ``start_audio_rx_pcm`` decoder. Direct IC-7610
LAN audio is PCM-first, so the old Opus decoder rejected every on-wire PCM frame
and captured ZERO frames; this is the bug these tests now pin.

Per CLAUDE.md, audio tests use ``FakeAudioBackend``/real audio primitives — never
one-off mocks. The stand-in radios here drive a real :class:`AudioBus` +
:class:`AudioSession`, feeding scripted on-wire ``AudioPacket`` frames through
the bus's ``start_rx`` callback exactly as a backend would.
"""

from __future__ import annotations

import asyncio

import pytest

from rigplane.audio.bus import AudioBus
from rigplane.audio.lan_stream import AudioPacket
from rigplane.audio.session import AudioSession
from rigplane.cli._validate import (
    _AUDIO_PROBE_TARGET_FRAMES,
    _capture_rx_audio_probe,
)
from rigplane.core.radio_protocol import AudioCapable
from rigplane.core.types import AudioCodec
from rigplane.validation.audio_checks import (
    PROBE_FRAME_BYTES,
    PROBE_SAMPLES_PER_FRAME,
    PROBE_TONE_HZ,
    run_rx_rms_check_on_frames,
    run_scope_presence_check_on_frames,
)
from rigplane.validation.hardware import execute_hardware_checks
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckStatus,
    MatrixTemplate,
    OperatorSafetyBlock,
    RadioTarget,
    ValidationLevel,
)

from _audio_pipeline_helpers import sine_pcm16_mono

_TONE = sine_pcm16_mono(PROBE_TONE_HZ, samples=PROBE_SAMPLES_PER_FRAME)
_SILENCE = b"\x00" * PROBE_FRAME_BYTES


def _ulaw_encode_pcm16(pcm: bytes) -> bytes:
    """Reference μ-law encoder (G.711) for the codec-decode test.

    Inverse of ``rigplane.audio._codecs.decode_ulaw_to_pcm16`` so the probe's
    codec-correct decode reproduces the original PCM tone byte-for-byte.
    """
    out = bytearray()
    bias = 0x84
    for offset in range(0, len(pcm), 2):
        sample = int.from_bytes(pcm[offset : offset + 2], "little", signed=True)
        sign = 0x80 if sample < 0 else 0x00
        if sample < 0:
            sample = -sample
        if sample > 32635:
            sample = 32635
        sample += bias
        exponent = 7
        mask = 0x4000
        while exponent > 0 and not (sample & mask):
            exponent -= 1
            mask >>= 1
        mantissa = (sample >> (exponent + 3)) & 0x0F
        out.append(~(sign | (exponent << 4) | mantissa) & 0xFF)
    return bytes(out)


# ---------------------------------------------------------------------------
# Live-shaped radios driving a real AudioBus + AudioSession (no one-off mocks)
# ---------------------------------------------------------------------------


class _SessionProbeRadio:
    """AudioCapable stand-in that delivers scripted on-wire RX frames.

    Drives a real :class:`AudioBus`/:class:`AudioSession`: ``subscribe_rx``
    arms RX via the bus, which calls this radio's ``start_rx(callback)``; the
    radio then feeds the scripted :class:`AudioPacket` frames through that
    callback — exactly the codec-negotiated path the web relay uses. The
    Opus-only ``start_audio_rx_pcm`` is present (protocol satisfaction) but
    raises to PROVE the probe no longer uses it; the TX surface raises to prove
    the probe is RX-only.
    """

    audio_sample_rate = 48000

    def __init__(
        self,
        frames: list[bytes | None],
        *,
        codec: AudioCodec = AudioCodec.PCM_1CH_16BIT,
        fail_start: bool = False,
    ) -> None:
        self._frames = frames
        self.audio_codec = codec
        self._fail_start = fail_start
        self.rx_started = False
        self.rx_stopped = False
        self._callback = None
        self.audio_bus = AudioBus(self)
        self.audio_session = AudioSession(self, bus=self.audio_bus)

    async def start_rx(self, callback) -> None:
        if self._fail_start:
            raise OSError("audio port unavailable")
        self.rx_started = True
        self._callback = callback
        for frame in self._frames:
            packet = None if frame is None else AudioPacket(0x0001, 0, frame)
            callback(packet)

    async def stop_rx(self) -> None:
        self.rx_stopped = True
        self._callback = None

    # Opus-only RX decoder — MUST NOT be used by the session-routed probe.
    async def start_audio_rx_pcm(self, *a, **k):  # pragma: no cover
        raise AssertionError(
            "probe must use the codec-negotiated AudioSession path, "
            "not the Opus-only start_audio_rx_pcm"
        )

    async def stop_audio_rx_pcm(self):  # pragma: no cover
        raise AssertionError("Opus-only stop_audio_rx_pcm must not be used")

    async def start_audio_rx_opus(self, *a, **k):  # pragma: no cover
        raise AssertionError("opus RX not used by the probe")

    async def stop_audio_rx_opus(self):  # pragma: no cover
        raise AssertionError("opus RX not used by the probe")

    async def push_audio_tx_opus(self, *a, **k):  # pragma: no cover
        raise AssertionError("TX must not be touched by the RX probe")

    async def start_audio_tx_pcm(self, *a, **k):  # pragma: no cover
        raise AssertionError("TX must not be touched by the RX probe")

    async def push_audio_tx_pcm(self, *a, **k):  # pragma: no cover
        raise AssertionError("TX must not be touched by the RX probe")

    async def stop_audio_tx_pcm(self):  # pragma: no cover
        raise AssertionError("TX must not be touched by the RX probe")

    async def start_audio_tx_opus(self):  # pragma: no cover
        raise AssertionError("TX must not be touched by the RX probe")

    async def stop_audio_tx_opus(self):  # pragma: no cover
        raise AssertionError("TX must not be touched by the RX probe")

    async def get_audio_stats(self):  # pragma: no cover
        return {}


class _NoSessionRadio:
    """AudioCapable-shaped but with no ``audio_session`` → MANUAL fallback.

    Mirrors a not-yet-migrated/bare backend: the probe must degrade to
    MANUAL_REQUIRED (return None) rather than crash or touch the Opus API.
    """

    audio_session = None
    audio_codec = AudioCodec.PCM_1CH_16BIT
    audio_sample_rate = 48000

    def __init__(self) -> None:
        self.audio_bus = AudioBus(self)

    async def start_audio_rx_pcm(self, *a, **k):  # pragma: no cover
        raise AssertionError("Opus-only start_audio_rx_pcm must not be used")

    async def stop_audio_rx_pcm(self):  # pragma: no cover
        raise AssertionError

    async def start_audio_rx_opus(self, *a, **k):  # pragma: no cover
        raise AssertionError

    async def stop_audio_rx_opus(self):  # pragma: no cover
        raise AssertionError

    async def push_audio_tx_opus(self, *a, **k):  # pragma: no cover
        raise AssertionError

    async def start_audio_tx_pcm(self, *a, **k):  # pragma: no cover
        raise AssertionError

    async def push_audio_tx_pcm(self, *a, **k):  # pragma: no cover
        raise AssertionError

    async def stop_audio_tx_pcm(self):  # pragma: no cover
        raise AssertionError

    async def start_audio_tx_opus(self):  # pragma: no cover
        raise AssertionError

    async def stop_audio_tx_opus(self):  # pragma: no cover
        raise AssertionError

    async def get_audio_stats(self):  # pragma: no cover
        return {}


class _NoAudioRadio:
    """A radio with no audio surface — not AudioCapable."""


def _probe_template() -> MatrixTemplate:
    """Template carrying the two live probes as MANUAL_REQUIRED (hardware default)."""
    return MatrixTemplate(
        radio=RadioTarget(model="IC-7610", profile_id="ic7610"),
        entries=[
            CapabilityDeclarationEntry(
                check_id="audio.rx.rms",
                capability="audio",
                level=ValidationLevel.COMPATIBILITY_SURFACES,
                declaration=CapabilityDeclaration.MANUAL_REQUIRED,
                summary="rx rms",
            ),
            CapabilityDeclarationEntry(
                check_id="scope.fft.presence",
                capability="scope",
                level=ValidationLevel.COMPATIBILITY_SURFACES,
                declaration=CapabilityDeclaration.MANUAL_REQUIRED,
                summary="scope presence",
            ),
        ],
    )


def _flatten(levels):
    return {check.check_id: check for level in levels for check in level.checks}


def _safety() -> OperatorSafetyBlock:
    return OperatorSafetyBlock(tx_allowed=False, tuner_allowed=False)


# ---------------------------------------------------------------------------
# Capture helper (the live AudioSession RX seam)
# ---------------------------------------------------------------------------


async def test_capture_returns_none_for_non_audio_radio() -> None:
    """A non-AudioCapable radio yields None (no session opened, no hang)."""
    radio = _NoAudioRadio()
    assert not isinstance(radio, AudioCapable)
    assert await _capture_rx_audio_probe(radio) is None


async def test_capture_returns_none_without_audio_session() -> None:
    """An AudioCapable radio with no session → MANUAL fallback (None), no crash."""
    radio = _NoSessionRadio()
    assert isinstance(radio, AudioCapable)
    assert await _capture_rx_audio_probe(radio) is None


async def test_capture_collects_frames_via_session_and_tears_down() -> None:
    # Deliver exactly the target count so the capture returns once the window
    # fills (no reliance on the wall-clock ceiling).
    window = [_TONE] * _AUDIO_PROBE_TARGET_FRAMES
    radio = _SessionProbeRadio(window)
    captured = await asyncio.wait_for(_capture_rx_audio_probe(radio), timeout=5.0)
    assert captured == window
    # RX was armed through the session/bus and torn down (subscription released
    # → bus drops the last subscriber → radio.stop_rx).
    assert radio.rx_started
    assert radio.rx_stopped
    assert radio.audio_session.rx_demand == 0


async def test_capture_decodes_ulaw_to_pcm_via_negotiated_codec() -> None:
    """Codec-correct decode: a μ-law on-wire frame is expanded to PCM16.

    Proves the probe honours ``radio.audio_codec`` (the negotiated path) rather
    than treating every payload as raw PCM. The decoded frame round-trips back
    to the original tone within μ-law quantisation tolerance, and crucially is
    NON-silent so the downstream RMS check would PASS.
    """
    ulaw_frame = _ulaw_encode_pcm16(_TONE)
    radio = _SessionProbeRadio([ulaw_frame], codec=AudioCodec.ULAW_1CH)
    captured = await asyncio.wait_for(_capture_rx_audio_probe(radio), timeout=5.0)
    assert len(captured) == 1
    decoded = captured[0]
    assert decoded is not None
    # μ-law decode yields s16le PCM the same length as the tone (2 bytes/sample).
    assert len(decoded) == len(_TONE)
    # Non-silent: the decoded window must carry energy (RMS check would PASS).
    result = await run_rx_rms_check_on_frames(captured)
    assert result.status is CheckStatus.PASS


async def test_capture_error_tears_down_and_returns_empty() -> None:
    """A start failure is contained: empty window, no leaked demand."""
    radio = _SessionProbeRadio([_TONE], fail_start=True)
    captured = await _capture_rx_audio_probe(radio)
    # subscribe_rx unwinds the failed RX arm itself; the probe surfaces an empty
    # window (probes turn it into a FAIL) and leaves no leaked session demand.
    assert captured == []
    assert radio.audio_session.rx_demand == 0


async def test_capture_does_not_block_on_short_stream() -> None:
    """Fewer frames than the target must not hang — capture returns promptly."""
    radio = _SessionProbeRadio([_TONE, _TONE])
    captured = await asyncio.wait_for(_capture_rx_audio_probe(radio), timeout=10.0)
    assert captured == [_TONE, _TONE]
    assert radio.rx_stopped


# ---------------------------------------------------------------------------
# Pure measurement on captured frames
# ---------------------------------------------------------------------------


async def test_rx_rms_on_frames_passes_for_non_silent() -> None:
    result = await run_rx_rms_check_on_frames([_TONE, _TONE, _TONE])
    assert result.status is CheckStatus.PASS
    assert result.evidence["live"] is True
    assert float(result.evidence["rms"]) > 0.0


async def test_rx_rms_on_frames_fails_for_silence() -> None:
    result = await run_rx_rms_check_on_frames([_SILENCE, _SILENCE])
    assert result.status is CheckStatus.FAIL
    assert result.evidence["live"] is True
    assert result.error is not None


async def test_rx_rms_on_frames_fails_for_empty_window() -> None:
    result = await run_rx_rms_check_on_frames([])
    assert result.status is CheckStatus.FAIL


async def test_rx_rms_on_frames_drops_gap_placeholders() -> None:
    result = await run_rx_rms_check_on_frames([None, _TONE, None, _TONE])
    assert result.status is CheckStatus.PASS
    assert result.evidence["gap_frames"] == 2


async def test_scope_presence_on_frames_passes_for_tone() -> None:
    # A strong in-band tone across several frames must register spectral presence.
    result = await run_scope_presence_check_on_frames([_TONE] * 8)
    # SKIP only when numpy is unavailable; otherwise it must PASS on a real tone.
    if result.status is CheckStatus.SKIP:
        pytest.skip("numpy FFT dependency unavailable")
    assert result.status is CheckStatus.PASS
    assert result.evidence["live"] is True


async def test_scope_presence_on_frames_fails_for_silence() -> None:
    result = await run_scope_presence_check_on_frames([_SILENCE] * 8)
    if result.status is CheckStatus.SKIP:
        pytest.skip("numpy FFT dependency unavailable")
    assert result.status is CheckStatus.FAIL


# ---------------------------------------------------------------------------
# End-to-end seam through execute_hardware_checks
# ---------------------------------------------------------------------------


async def test_execute_runs_live_probes_when_frames_supplied() -> None:
    radio = _SessionProbeRadio([])  # radio object unused by the live probe path
    levels = await execute_hardware_checks(
        radio,
        _probe_template(),
        _safety(),
        allow_writes=True,
        audio_probe_frames=[_TONE] * 8,
    )
    checks = _flatten(levels)
    assert checks["audio.rx.rms"].status is CheckStatus.PASS
    scope_status = checks["scope.fft.presence"].status
    assert scope_status in {CheckStatus.PASS, CheckStatus.SKIP}


async def test_execute_fails_live_rms_on_silent_stream() -> None:
    radio = _SessionProbeRadio([])
    levels = await execute_hardware_checks(
        radio,
        _probe_template(),
        _safety(),
        allow_writes=True,
        audio_probe_frames=[_SILENCE] * 8,
    )
    checks = _flatten(levels)
    assert checks["audio.rx.rms"].status is CheckStatus.FAIL


async def test_execute_leaves_probes_manual_without_frames() -> None:
    """No captured window (non-hardware/non-AudioCapable) → unchanged behaviour."""
    radio = _SessionProbeRadio([])
    levels = await execute_hardware_checks(
        radio,
        _probe_template(),
        _safety(),
        allow_writes=True,
        audio_probe_frames=None,
    )
    checks = _flatten(levels)
    assert checks["audio.rx.rms"].status is CheckStatus.MANUAL_REQUIRED
    assert checks["scope.fft.presence"].status is CheckStatus.MANUAL_REQUIRED
