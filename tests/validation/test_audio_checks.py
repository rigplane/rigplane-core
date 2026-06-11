"""Tests for the automated audio-pipeline probe checks (MOR-639/640/641).

The AUDIO_PROBE check family is the CI-automated counterpart of the MANUAL
``audio.rx`` / ``scope.capture`` operator checks (GH #1650): each probe
executes against the real audio fakes (``FakeAudioBackend`` and friends) and
produces a real :class:`CheckResult` that folds into the existing
``ValidationArtifact`` + golden-gate machinery.
"""

from __future__ import annotations

from array import array

from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.validation.audio_checks import (
    PROBE_SAMPLES_PER_FRAME,
    PROBE_TONE_HZ,
    run_rx_rms_check,
)
from rigplane.validation.registry import (
    REGISTRY_BY_ID,
    CheckKind,
    build_template_from_capabilities,
)
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CheckStatus,
    FailureDomain,
    ValidationLevel,
)

from _audio_pipeline_helpers import pcm_rms, sine_pcm16_mono


def _backend() -> FakeAudioBackend:
    return FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="Probe Loopback",
                input_channels=2,
                output_channels=2,
            )
        ]
    )


def _silence(pcm: bytes) -> bytes:
    return b"\x00" * len(pcm)


def _attenuate_6db(pcm: bytes) -> bytes:
    samples = array("h")
    samples.frombytes(pcm)
    return array("h", (s // 2 for s in samples)).tobytes()


# ---------------------------------------------------------------------------
# Registry integration (T4 — audio.rx.rms)
# ---------------------------------------------------------------------------


def test_audio_rx_rms_spec_registered() -> None:
    spec = REGISTRY_BY_ID["audio.rx.rms"]
    assert spec.kind is CheckKind.AUDIO_PROBE
    assert spec.capability == "audio"
    assert spec.level == ValidationLevel.COMPATIBILITY_SURFACES
    assert spec.failure_domain is FailureDomain.AUDIO
    assert spec.get_op is None
    assert spec.set_op is None
    assert spec.tx_adjacent is False


def test_manual_audio_rx_check_kept_for_real_hardware() -> None:
    """The MANUAL audio.rx operator check is kept, not superseded."""
    spec = REGISTRY_BY_ID["audio.rx"]
    assert spec.kind is CheckKind.MANUAL


def test_audio_probe_declares_manual_required_in_hardware_templates() -> None:
    """AUDIO_PROBE checks slot into generated templates as MANUAL_REQUIRED.

    On real hardware the probes are never auto-run (they execute in CI via
    ``rigplane.validation.audio_checks``), so per-radio templates carry them
    with the same operator-confirmation posture as the MANUAL checks.
    """
    template = build_template_from_capabilities(
        frozenset({"audio"}),
        model="IC-0000",
        profile_id="probe_test",
    )
    entries = {entry.check_id: entry for entry in template.entries}
    entry = entries["audio.rx.rms"]
    assert entry.declaration == CapabilityDeclaration.MANUAL_REQUIRED
    assert entry.level == ValidationLevel.COMPATIBILITY_SURFACES


# ---------------------------------------------------------------------------
# T4 / MOR-639 — RX-RMS probe
# ---------------------------------------------------------------------------


async def test_rx_rms_check_passes_on_clean_pipeline() -> None:
    backend = _backend()
    result = await run_rx_rms_check(backend=backend)

    assert result.check_id == "audio.rx.rms"
    assert result.capability == "audio"
    assert result.level == ValidationLevel.COMPATIBILITY_SURFACES
    assert result.status is CheckStatus.PASS
    assert result.declaration is CapabilityDeclaration.SUPPORTED
    assert result.failure_domain is None
    assert result.error is None
    assert result.started_at and result.finished_at

    # The delivered RMS matches the reference tone exactly (byte-identical
    # delivery through FakeRxStream), cross-checked against the shared
    # test helpers rather than the probe's own math.
    expected_rms = pcm_rms(
        sine_pcm16_mono(PROBE_TONE_HZ, samples=PROBE_SAMPLES_PER_FRAME)
    )
    rms = result.evidence["rms"]
    assert isinstance(rms, float)
    assert rms > 0.0
    assert abs(rms - expected_rms) < 1e-6

    # Stream lifecycle is clean: started once, stopped once.
    assert len(backend.rx_streams) == 1
    assert backend.rx_streams[0].started_count == 1
    assert backend.rx_streams[0].stopped_count == 1


async def test_rx_rms_check_builds_default_backend() -> None:
    result = await run_rx_rms_check()
    assert result.status is CheckStatus.PASS


async def test_rx_rms_check_fails_on_silent_pipeline() -> None:
    result = await run_rx_rms_check(backend=_backend(), pcm_transform=_silence)
    assert result.status is CheckStatus.FAIL
    assert result.failure_domain is FailureDomain.AUDIO
    assert result.error is not None
    assert result.evidence["rms"] == 0.0


async def test_rx_rms_check_fails_on_attenuated_pipeline() -> None:
    """A -6 dB level drop lands outside the RMS tolerance band."""
    result = await run_rx_rms_check(backend=_backend(), pcm_transform=_attenuate_6db)
    assert result.status is CheckStatus.FAIL
    assert result.failure_domain is FailureDomain.AUDIO
    rms = result.evidence["rms"]
    expected_rms = result.evidence["expected_rms"]
    assert isinstance(rms, float) and isinstance(expected_rms, float)
    assert 0.0 < rms < expected_rms
