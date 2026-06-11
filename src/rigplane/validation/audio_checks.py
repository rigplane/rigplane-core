"""Automated audio-pipeline probe checks (GH #1650; MOR-639/640/641).

The AUDIO_PROBE check family is the CI-automated counterpart of the MANUAL
``audio.rx`` / ``scope.capture`` operator checks. Each probe drives the audio
pipeline through the deterministic test fakes (:class:`FakeAudioBackend` and
its streams — no PortAudio, no hardware, no network) and emits a real
:class:`~rigplane.validation.schema.CheckResult`, so audio regressions flow
into the existing ``ValidationArtifact`` + golden-gate machinery exactly like
command checks do.

Integration contract:

* The probes' registry rows live in ``registry/_audio.py`` with
  ``CheckKind.AUDIO_PROBE``. Generated per-radio templates carry them as
  ``MANUAL_REQUIRED`` (never auto-run on a live radio); the pre-existing
  MANUAL entries are kept for real-hardware operator confirmation.
* ``pcm_transform`` on each probe is a fault-injection hook for the probes'
  own regression tests: it mutates the PCM *as injected into the pipeline*
  while the expected reference stays pristine, simulating a corrupted
  pipeline.

Import path: ``from rigplane.validation import audio_checks`` (not re-exported
by the ``rigplane.validation`` facade — this module imports ``rigplane.audio``
and is only needed by the CI harness).
"""

from __future__ import annotations

import datetime
import math
from collections.abc import Callable

from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.validation.registry import CheckSpec, get_spec
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CheckResult,
    CheckStatus,
)

__all__ = [
    "PROBE_AMPLITUDE",
    "PROBE_FRAME_BYTES",
    "PROBE_FRAME_MS",
    "PROBE_SAMPLE_RATE",
    "PROBE_SAMPLES_PER_FRAME",
    "PROBE_TONE_HZ",
    "RX_RMS_TOLERANCE",
    "PcmTransform",
    "run_rx_rms_check",
]

# Probe signal parameters. They mirror the audio-bridge PCM contract
# (48 kHz mono s16le, 20 ms frames = 960 samples = 1920 bytes) without
# importing the bridge: the probes exercise the fakes' fixed-frame contract,
# and the byte sizes must match what the managed PCM-TX validator accepts.
PROBE_SAMPLE_RATE = 48_000
PROBE_FRAME_MS = 20
PROBE_SAMPLES_PER_FRAME = PROBE_SAMPLE_RATE * PROBE_FRAME_MS // 1000  # 960
PROBE_FRAME_BYTES = PROBE_SAMPLES_PER_FRAME * 2  # mono s16le -> 1920
PROBE_TONE_HZ = 1_000.0
PROBE_AMPLITUDE = 12_000
PROBE_FRAME_COUNT = 8

# Relative RMS tolerance band for the RX probe: the fake pipeline delivers
# byte-identical PCM, so any deviation beyond rounding indicates corruption.
RX_RMS_TOLERANCE = 0.05

PcmTransform = Callable[[bytes], bytes]
"""Fault-injection hook: mutates PCM as injected; the reference stays pristine."""


# ---------------------------------------------------------------------------
# Deterministic PCM helpers (stdlib-only; mirror tests/_audio_pipeline_helpers)
# ---------------------------------------------------------------------------


def _sine_pcm16_mono(
    frequency_hz: float,
    *,
    samples: int,
    sample_rate: int = PROBE_SAMPLE_RATE,
    amplitude: int = PROBE_AMPLITUDE,
) -> bytes:
    """Generate deterministic mono s16le sine PCM."""
    pcm = bytearray()
    for index in range(samples):
        phase = 2.0 * math.pi * frequency_hz * (index / sample_rate)
        value = int(amplitude * math.sin(phase))
        pcm += value.to_bytes(2, "little", signed=True)
    return bytes(pcm)


def _pcm_rms(pcm: bytes) -> float:
    """Return RMS for mono s16le PCM bytes (0.0 for empty input)."""
    if not pcm:
        return 0.0
    if len(pcm) % 2:
        raise ValueError("PCM byte length must be even for s16le samples.")
    count = len(pcm) // 2
    total = 0.0
    for offset in range(0, len(pcm), 2):
        sample = int.from_bytes(pcm[offset : offset + 2], "little", signed=True)
        total += float(sample) * float(sample)
    return math.sqrt(total / count)


def _utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 millisecond ``...Z`` string."""
    return (
        datetime.datetime.now(datetime.UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# Backend / result plumbing
# ---------------------------------------------------------------------------


def _default_backend() -> FakeAudioBackend:
    """A fresh single-device fake backend (deterministic, dependency-free)."""
    return FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="Audio Probe Loopback",
                input_channels=2,
                output_channels=2,
            )
        ]
    )


def _first_device(backend: FakeAudioBackend) -> AudioDeviceId:
    devices = backend.list_devices()
    if not devices:
        raise ValueError("audio probe backend exposes no devices")
    return devices[0].id


def _probe_spec(check_id: str) -> CheckSpec:
    spec = get_spec(check_id)
    if spec is None:  # pragma: no cover — registry rows ship with this module
        raise LookupError(f"audio probe check {check_id!r} missing from REGISTRY")
    return spec


def _probe_result(
    check_id: str,
    *,
    passed: bool,
    evidence: dict[str, object],
    error: str | None,
    started_at: str,
) -> CheckResult:
    """Build a :class:`CheckResult` for a probe from its registry spec.

    The probe ran automatically, so the declaration is SUPPORTED; on failure
    the failure domain comes from the registry spec (``audio`` or
    ``scope_waterfall``).
    """
    spec = _probe_spec(check_id)
    status = CheckStatus.PASS if passed else CheckStatus.FAIL
    return CheckResult(
        check_id=spec.check_id,
        capability=spec.capability,
        level=spec.level,
        status=status,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary=spec.summary,
        failure_domain=None if passed else spec.failure_domain,
        evidence=evidence,
        error=None if passed else error,
        started_at=started_at,
        finished_at=_utcnow_iso(),
    )


# ---------------------------------------------------------------------------
# T4 / MOR-639 — RX-RMS probe
# ---------------------------------------------------------------------------


async def run_rx_rms_check(
    *,
    backend: FakeAudioBackend | None = None,
    frame_count: int = PROBE_FRAME_COUNT,
    pcm_transform: PcmTransform | None = None,
) -> CheckResult:
    """Inject a reference tone through the fake RX pipeline; verify the RMS.

    Opens an RX capture stream on the fake backend, injects ``frame_count``
    frames of a deterministic 1 kHz sine, and asserts the RMS of the delivered
    PCM is non-zero and within ``RX_RMS_TOLERANCE`` of the reference tone's
    RMS. A silent, attenuated, or distorted delivery fails with
    ``failure_domain="audio"``.
    """
    started_at = _utcnow_iso()
    fake = backend if backend is not None else _default_backend()
    stream = fake.open_rx(
        _first_device(fake),
        sample_rate=PROBE_SAMPLE_RATE,
        channels=1,
        frame_ms=PROBE_FRAME_MS,
    )

    reference = _sine_pcm16_mono(PROBE_TONE_HZ, samples=PROBE_SAMPLES_PER_FRAME)
    injected = reference if pcm_transform is None else pcm_transform(reference)

    received: list[bytes] = []
    await stream.start(received.append)
    try:
        for _ in range(frame_count):
            stream.inject_frame(injected)
    finally:
        await stream.stop()

    delivered = b"".join(received)
    expected_rms = _pcm_rms(reference)
    rms = _pcm_rms(delivered)
    in_band = (
        len(delivered) == frame_count * len(reference)
        and rms > 0.0
        and abs(rms - expected_rms) <= expected_rms * RX_RMS_TOLERANCE
    )

    evidence: dict[str, object] = {
        "tone_hz": PROBE_TONE_HZ,
        "frames_injected": frame_count,
        "bytes_delivered": len(delivered),
        "rms": round(rms, 6),
        "expected_rms": round(expected_rms, 6),
        "rms_tolerance": RX_RMS_TOLERANCE,
    }
    error = (
        None
        if in_band
        else (
            f"RX RMS {rms:.3f} outside tolerance band "
            f"{expected_rms:.3f} +/- {RX_RMS_TOLERANCE * 100:.0f}% "
            f"({len(delivered)} bytes delivered)"
        )
    )
    return _probe_result(
        "audio.rx.rms",
        passed=in_band,
        evidence=evidence,
        error=error,
        started_at=started_at,
    )
