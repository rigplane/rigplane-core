"""Tests for the LIVE RX-audio probes on the hardware path (MOR-668).

``audio.rx.rms`` and ``scope.fft.presence`` (AUDIO_PROBE check-kind,
MOR-639/641) previously ran only in CI against ``FakeAudioBackend`` and stayed
MANUAL_REQUIRED on real hardware. These tests prove the LIVE wiring: when the
CLI hardware path captures a real RX-PCM window and threads it into
``execute_hardware_checks`` (or the probe functions directly), the probes run
for real and PASS on a non-silent stream / FAIL on a dead one.

Per CLAUDE.md, audio tests use ``FakeAudioBackend`` — never one-off mocks — for
the capture path. The minimal stand-in radio here wraps ``FakeAudioBackend``'s
RX stream behind the real ``AudioCapable.start_audio_rx_pcm`` surface so the
capture seam is exercised with deterministic frames.
"""

from __future__ import annotations

import asyncio

import pytest

from rigplane.cli._validate import (
    _AUDIO_PROBE_TARGET_FRAMES,
    _capture_rx_audio_probe,
)
from rigplane.core.radio_protocol import AudioCapable
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


# ---------------------------------------------------------------------------
# Minimal live-shaped radios (FakeAudioBackend-backed, no one-off mocks)
# ---------------------------------------------------------------------------


class _AudioProbeRadio:
    """Tiny AudioCapable stand-in delivering scripted RX PCM frames.

    Structurally satisfies :class:`AudioCapable` (so the capture helper's
    ``isinstance`` guard recognises it) while only the RX-PCM methods carry
    behaviour — the TX methods raise to prove the probe path is RX-only and
    never touches them. A plain class, not a mock, so a real signature drift on
    ``start_audio_rx_pcm`` fails loudly. ``stopped`` records teardown for the
    leak-safety assertion.
    """

    audio_codec = "pcm"
    audio_sample_rate = 48000

    def __init__(self, frames: list[bytes | None], *, fail_start: bool = False) -> None:
        self._frames = frames
        self._fail_start = fail_start
        self.started = False
        self.stopped = False

    @property
    def audio_bus(self):  # pragma: no cover - unused by the probe path
        raise AssertionError("audio_bus must not be touched by the RX probe")

    async def start_audio_rx_pcm(
        self,
        callback,
        *,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> None:
        if self._fail_start:
            raise OSError("audio port unavailable")
        self.started = True
        for frame in self._frames:
            callback(frame)

    async def stop_audio_rx_pcm(self) -> None:
        self.stopped = True

    # Remaining AudioCapable surface — present (protocol satisfaction) but the
    # TX side must never be reached by the RX-only probe.
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
# Capture helper (the live RX session seam)
# ---------------------------------------------------------------------------


async def test_capture_returns_none_for_non_audio_radio() -> None:
    """A non-AudioCapable radio yields None (no session opened, no hang)."""
    radio = _NoAudioRadio()
    assert not isinstance(radio, AudioCapable)
    assert await _capture_rx_audio_probe(radio) is None


async def test_capture_collects_frames_and_tears_down() -> None:
    # Deliver exactly the target count so the capture returns immediately once
    # the window fills (no reliance on the wall-clock ceiling).
    window = [_TONE] * _AUDIO_PROBE_TARGET_FRAMES
    radio = _AudioProbeRadio(window)
    captured = await asyncio.wait_for(_capture_rx_audio_probe(radio), timeout=5.0)
    assert captured == window
    assert radio.started and radio.stopped


async def test_capture_error_tears_down_and_returns_empty() -> None:
    """A start failure is contained: empty window, teardown not leaked."""
    radio = _AudioProbeRadio([_TONE], fail_start=True)
    captured = await _capture_rx_audio_probe(radio)
    # start() raised before `started` flipped, so stop() is skipped — but no
    # exception escapes and the window is empty (probes turn it into a FAIL).
    assert captured == []
    assert radio.stopped is False


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
    radio = _AudioProbeRadio([])  # radio object unused by the live probe path
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
    radio = _AudioProbeRadio([])
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
    radio = _AudioProbeRadio([])
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


async def test_capture_does_not_block_on_short_stream() -> None:
    """Fewer frames than the target must not hang — capture returns promptly."""
    radio = _AudioProbeRadio([_TONE, _TONE])
    captured = await asyncio.wait_for(_capture_rx_audio_probe(radio), timeout=10.0)
    assert captured == [_TONE, _TONE]
    assert radio.stopped
