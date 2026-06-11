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
from rigplane.audio.lan_stream import MAX_AUDIO_PAYLOAD, TX_IDENT
from rigplane.validation.audio_checks import (
    PROBE_FRAME_BYTES,
    PROBE_SAMPLES_PER_FRAME,
    PROBE_TONE_HZ,
    audio_probe_level_results,
    run_audio_probe_checks,
    run_rx_rms_check,
    run_scope_presence_check,
    run_tx_byte_perfect_check,
)
from rigplane.validation.gating import gate_artifacts
from rigplane.validation.runner import build_validation_artifact
from rigplane.validation.schema import validate_artifact_dict
from rigplane.validation.registry import (
    REGISTRY_BY_ID,
    CheckKind,
    build_template_from_capabilities,
)
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CheckStatus,
    FailureDomain,
    OperatorSafetyBlock,
    TransportInfo,
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


# ---------------------------------------------------------------------------
# T5 / MOR-640 — TX byte-perfect probe
# ---------------------------------------------------------------------------


def test_audio_tx_byte_perfect_spec_registered() -> None:
    spec = REGISTRY_BY_ID["audio.tx.byte_perfect"]
    assert spec.kind is CheckKind.AUDIO_PROBE
    assert spec.capability == "audio"
    assert spec.level == ValidationLevel.COMPATIBILITY_SURFACES
    assert spec.failure_domain is FailureDomain.AUDIO
    assert spec.set_op is None
    # GH #1650: TX audio stays behind explicit operator safety enablement
    # on real hardware, exactly like tuner.tune / tx.ptt.
    assert spec.tx_adjacent is True


async def test_tx_byte_perfect_check_passes_on_clean_pipeline() -> None:
    backend = _backend()
    frame_count = 4
    result = await run_tx_byte_perfect_check(backend=backend, frame_count=frame_count)

    assert result.check_id == "audio.tx.byte_perfect"
    assert result.status is CheckStatus.PASS
    assert result.failure_domain is None
    assert result.error is None

    # Mirrors the byte-perfect TX harness contract: each 1920-byte PCM frame
    # splits into a MAX_AUDIO_PAYLOAD chunk plus the remainder, all packets
    # carry TX_IDENT, and sequence numbers are contiguous.
    assert result.evidence["payload_matches"] is True
    assert result.evidence["payload_bytes"] == frame_count * PROBE_FRAME_BYTES
    assert result.evidence["packet_sizes"] == [
        size
        for _ in range(frame_count)
        for size in (MAX_AUDIO_PAYLOAD, PROBE_FRAME_BYTES - MAX_AUDIO_PAYLOAD)
    ]
    assert result.evidence["idents"] == [TX_IDENT]
    assert result.evidence["sequences_contiguous"] is True

    # The fake capture stream was started and stopped exactly once.
    assert len(backend.rx_streams) == 1
    assert backend.rx_streams[0].stopped_count == 1


async def test_tx_byte_perfect_check_fails_on_corrupted_payload() -> None:
    def corrupt(pcm: bytes) -> bytes:
        # Flip one byte mid-frame: same length, different payload.
        mutated = bytearray(pcm)
        mutated[len(mutated) // 2] ^= 0xFF
        return bytes(mutated)

    result = await run_tx_byte_perfect_check(backend=_backend(), pcm_transform=corrupt)
    assert result.status is CheckStatus.FAIL
    assert result.failure_domain is FailureDomain.AUDIO
    assert result.error is not None
    assert result.evidence["payload_matches"] is False


async def test_tx_byte_perfect_check_fails_on_dropped_frames() -> None:
    dropped: dict[str, int] = {"count": 0}

    def drop_every_other(pcm: bytes) -> bytes:
        dropped["count"] += 1
        return pcm if dropped["count"] % 2 else b""

    result = await run_tx_byte_perfect_check(
        backend=_backend(), pcm_transform=drop_every_other
    )
    assert result.status is CheckStatus.FAIL
    assert result.failure_domain is FailureDomain.AUDIO
    assert result.evidence["payload_matches"] is False


# ---------------------------------------------------------------------------
# T6 / MOR-641 — scope-presence probe
# ---------------------------------------------------------------------------


def test_scope_fft_presence_spec_registered() -> None:
    spec = REGISTRY_BY_ID["scope.fft.presence"]
    assert spec.kind is CheckKind.AUDIO_PROBE
    assert spec.capability == "scope"
    assert spec.level == ValidationLevel.COMPATIBILITY_SURFACES
    assert spec.failure_domain is FailureDomain.SCOPE_WATERFALL
    assert spec.tx_adjacent is False


def test_manual_scope_capture_check_kept_for_real_hardware() -> None:
    spec = REGISTRY_BY_ID["scope.capture"]
    assert spec.kind is CheckKind.MANUAL


async def test_scope_presence_check_passes_on_tone() -> None:
    """A 1 kHz tone raises in-band FFT bins well above the baseline.

    Regression guard for the MOR-512/528 adaptive in-band auto-range: a
    clean in-band signal must stand out of the (clipped-low) baseline.
    """
    result = await run_scope_presence_check(backend=_backend())

    assert result.check_id == "scope.fft.presence"
    assert result.status is CheckStatus.PASS
    assert result.failure_domain is None
    assert result.error is None

    frames = result.evidence["frames_emitted"]
    assert isinstance(frames, int) and frames >= 1
    signal_peak = result.evidence["signal_peak"]
    baseline = result.evidence["baseline_median"]
    assert isinstance(signal_peak, int) and isinstance(baseline, (int, float))
    assert signal_peak > baseline


async def test_scope_presence_check_fails_on_silence() -> None:
    result = await run_scope_presence_check(backend=_backend(), pcm_transform=_silence)
    assert result.status is CheckStatus.FAIL
    assert result.failure_domain is FailureDomain.SCOPE_WATERFALL
    assert result.error is not None


async def test_scope_presence_check_fails_when_no_frames_emitted() -> None:
    """Starving the scope below one FFT window emits no frame at all."""
    result = await run_scope_presence_check(
        backend=_backend(),
        frame_count=1,  # 960 samples < 2048-sample FFT window
    )
    assert result.status is CheckStatus.FAIL
    assert result.failure_domain is FailureDomain.SCOPE_WATERFALL
    assert result.evidence["frames_emitted"] == 0


# ---------------------------------------------------------------------------
# Aggregation: artifact + golden-gate integration
# ---------------------------------------------------------------------------


async def test_run_audio_probe_checks_runs_all_three() -> None:
    checks = await run_audio_probe_checks()
    assert [check.check_id for check in checks] == [
        "audio.rx.rms",
        "audio.tx.byte_perfect",
        "scope.fft.presence",
    ]
    assert all(check.status is CheckStatus.PASS for check in checks)


async def test_probe_results_flow_into_artifact_and_golden_gate() -> None:
    """Probe CheckResults assemble into a schema-valid ValidationArtifact and
    regress through gate_artifacts exactly like command checks."""
    checks = await run_audio_probe_checks()
    levels = audio_probe_level_results(checks)
    assert len(levels) == 1
    assert levels[0].level == ValidationLevel.COMPATIBILITY_SURFACES

    template = build_template_from_capabilities(
        frozenset({"audio", "scope"}),
        model="CI Audio Harness",
        profile_id="ci_audio_harness",
    )
    artifact = build_validation_artifact(
        template=template,
        levels=levels,
        transport=TransportInfo(backend="fake-audio"),
        safety=OperatorSafetyBlock(),
        core_version="0.0.0-test",
        mode="ci-audio-probe",
    )
    payload = artifact.to_dict()
    # Round-trips through the strict schema validator.
    validate_artifact_dict(payload)

    # An all-PASS run gates clean against itself (the golden).
    report = gate_artifacts(payload, payload)
    assert report.ok
    assert report.matched == 3

    # A regressed probe (silent RX pipeline) is a BLOCKING regression.
    regressed = await run_rx_rms_check(pcm_transform=_silence)
    regressed_levels = audio_probe_level_results([regressed, *checks[1:]])
    regressed_artifact = build_validation_artifact(
        template=template,
        levels=regressed_levels,
        transport=TransportInfo(backend="fake-audio"),
        safety=OperatorSafetyBlock(),
        core_version="0.0.0-test",
        mode="ci-audio-probe",
    )
    report = gate_artifacts(regressed_artifact.to_dict(), payload)
    assert not report.ok
    assert any("audio.rx.rms" in line for line in report.regressions)
