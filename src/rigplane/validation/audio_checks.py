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
import statistics
from collections.abc import Callable, Sequence
from typing import cast

from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.lan_stream import (
    TX_IDENT,
    AudioPacket,
    AudioStream,
    parse_audio_packet,
)
from rigplane.core.transport import IcomTransport
from rigplane.scope import ScopeFrame
from rigplane.validation.registry import CheckSpec, get_spec
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CheckResult,
    CheckStatus,
    LevelResult,
    ValidationLevel,
)

__all__ = [
    "PROBE_AMPLITUDE",
    "PROBE_FRAME_BYTES",
    "PROBE_FRAME_MS",
    "PROBE_SAMPLE_RATE",
    "PROBE_SAMPLES_PER_FRAME",
    "PROBE_TONE_HZ",
    "RX_RMS_TOLERANCE",
    "SCOPE_MIN_RISE_PIXELS",
    "SCOPE_MIN_SIGNAL_PIXEL",
    "PcmTransform",
    "audio_probe_level_results",
    "run_audio_probe_checks",
    "run_rx_rms_check",
    "run_scope_presence_check",
    "run_tx_byte_perfect_check",
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

# Scope-presence probe parameters (regression guard for the MOR-512/528
# adaptive in-band auto-range). The synthetic VFO center is arbitrary —
# AudioFftScope only needs a positive center frequency for RF mapping.
SCOPE_CENTER_FREQ_HZ = 14_074_000
_SCOPE_FFT_SIZE = 2048
# In-band region inspected for the tone: the audio baseband the adaptive
# window estimates over (MOR-512's _INBAND_FALLBACK_HI_HZ).
_SCOPE_INBAND_HZ = 3_500.0
# Baseline region: bins beyond this audio offset, far outside the passband.
_SCOPE_BASELINE_MIN_HZ = 8_000.0
# A real in-band tone must reach this pixel level (0-160 ScopeFrame scale)
# and rise this far above the out-of-band baseline median.
SCOPE_MIN_SIGNAL_PIXEL = 80
SCOPE_MIN_RISE_PIXELS = 40

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


# ---------------------------------------------------------------------------
# T5 / MOR-640 — TX byte-perfect probe
# ---------------------------------------------------------------------------


class _RecordingAudioTransport:
    """Minimal in-memory audio transport: records every tracked send.

    Duck-types the slice of :class:`IcomTransport` that
    :meth:`AudioStream.push_tx` uses (``my_id``, ``remote_id``,
    ``send_tracked``). A plain class, not a mock — signature drift fails
    loudly instead of being absorbed.
    """

    my_id = 0xAABBCCDD
    remote_id = 0x11223344

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send_tracked(self, data: bytes) -> None:
        self.sent.append(bytes(data))


async def run_tx_byte_perfect_check(
    *,
    backend: FakeAudioBackend | None = None,
    frame_count: int = 4,
    pcm_transform: PcmTransform | None = None,
) -> CheckResult:
    """Verify captured TX PCM survives LAN packetization byte-perfect.

    Mirrors the in-process TX harness (``tests/test_audio_pipeline_harness``,
    real-hardware byte-perfectness proven in MOR-614): a reference tone is
    injected into the fake capture stream (the bridge's TX source), every
    delivered frame is pushed through the REAL :class:`AudioStream` TX
    packetization, and the reassembled packet payload must equal the
    reference PCM byte-for-byte — with TX idents and contiguous audio-level
    sequence numbers.
    """
    started_at = _utcnow_iso()
    fake = backend if backend is not None else _default_backend()
    capture = fake.open_rx(
        _first_device(fake),
        sample_rate=PROBE_SAMPLE_RATE,
        channels=1,
        frame_ms=PROBE_FRAME_MS,
    )

    reference = _sine_pcm16_mono(PROBE_TONE_HZ, samples=PROBE_SAMPLES_PER_FRAME)
    expected_pcm = reference * frame_count

    transport = _RecordingAudioTransport()
    stream = AudioStream(cast(IcomTransport, transport))
    await stream.start_tx()

    delivered: list[bytes] = []
    await capture.start(delivered.append)
    try:
        for _ in range(frame_count):
            frame = reference if pcm_transform is None else pcm_transform(reference)
            capture.inject_frame(frame)
        for frame in delivered:
            await stream.push_tx(frame)
    finally:
        await capture.stop()
        await stream.stop_tx()

    packets: list[AudioPacket] = []
    for raw in transport.sent:
        packet = parse_audio_packet(raw)
        if packet is not None:
            packets.append(packet)

    payload = b"".join(packet.data for packet in packets)
    payload_matches = payload == expected_pcm
    idents = sorted({packet.ident for packet in packets})
    sequences_contiguous = [packet.send_seq for packet in packets] == list(
        range(len(packets))
    )
    passed = (
        payload_matches
        and bool(packets)
        and idents == [TX_IDENT]
        and sequences_contiguous
    )

    evidence: dict[str, object] = {
        "tone_hz": PROBE_TONE_HZ,
        "frames_captured": len(delivered),
        "packets_sent": len(packets),
        "packet_sizes": [len(packet.data) for packet in packets],
        "payload_bytes": len(payload),
        "expected_bytes": len(expected_pcm),
        "payload_matches": payload_matches,
        "idents": idents,
        "sequences_contiguous": sequences_contiguous,
    }
    error = (
        None
        if passed
        else (
            "TX payload is not byte-perfect: "
            f"{len(payload)}/{len(expected_pcm)} bytes reassembled, "
            f"match={payload_matches}, idents={idents}, "
            f"contiguous={sequences_contiguous}"
        )
    )
    return _probe_result(
        "audio.tx.byte_perfect",
        passed=passed,
        evidence=evidence,
        error=error,
        started_at=started_at,
    )


# ---------------------------------------------------------------------------
# T6 / MOR-641 — scope-presence probe
# ---------------------------------------------------------------------------


async def run_scope_presence_check(
    *,
    backend: FakeAudioBackend | None = None,
    frame_count: int = PROBE_FRAME_COUNT,
    pcm_transform: PcmTransform | None = None,
) -> CheckResult:
    """Feed a reference tone to the audio FFT scope; verify in-band presence.

    Routes the tone through the fake RX capture stream into the REAL
    :class:`~rigplane.audio.fft_scope.AudioFftScope` (the MOR-512/528
    adaptive in-band auto-range path) and asserts the in-band pixel peak of
    the emitted :class:`ScopeFrame` rises above the out-of-band baseline. A
    silent or missing spectrum fails with ``failure_domain=scope_waterfall``.

    Returns a SKIP result when numpy (the FFT dependency) is unavailable.
    """
    started_at = _utcnow_iso()
    spec = _probe_spec("scope.fft.presence")
    try:
        from rigplane.audio.fft_scope import AudioFftScope

        scope = AudioFftScope(
            fft_size=_SCOPE_FFT_SIZE,
            fps=1_000,
            avg_count=1,
            sample_rate=PROBE_SAMPLE_RATE,
        )
    except ImportError as exc:
        return CheckResult(
            check_id=spec.check_id,
            capability=spec.capability,
            level=spec.level,
            status=CheckStatus.SKIP,
            declaration=CapabilityDeclaration.SUPPORTED,
            summary=spec.summary,
            evidence={"reason": f"FFT dependency unavailable: {exc}"},
            started_at=started_at,
            finished_at=_utcnow_iso(),
        )

    frames: list[ScopeFrame] = []
    scope.set_center_freq(SCOPE_CENTER_FREQ_HZ)
    scope.on_frame(frames.append)

    fake = backend if backend is not None else _default_backend()
    capture = fake.open_rx(
        _first_device(fake),
        sample_rate=PROBE_SAMPLE_RATE,
        channels=1,
        frame_ms=PROBE_FRAME_MS,
    )
    reference = _sine_pcm16_mono(PROBE_TONE_HZ, samples=PROBE_SAMPLES_PER_FRAME)
    injected = reference if pcm_transform is None else pcm_transform(reference)
    await capture.start(scope.feed_audio)
    try:
        for _ in range(frame_count):
            capture.inject_frame(injected)
    finally:
        await capture.stop()
        scope.stop()

    evidence: dict[str, object] = {
        "tone_hz": PROBE_TONE_HZ,
        "center_freq_hz": SCOPE_CENTER_FREQ_HZ,
        "fft_size": _SCOPE_FFT_SIZE,
        "frames_emitted": len(frames),
        "min_signal_pixel": SCOPE_MIN_SIGNAL_PIXEL,
        "min_rise_pixels": SCOPE_MIN_RISE_PIXELS,
    }
    if not frames:
        return _probe_result(
            "scope.fft.presence",
            passed=False,
            evidence=evidence,
            error=(
                f"no scope frame emitted from {frame_count} injected "
                f"frame(s) ({frame_count * PROBE_SAMPLES_PER_FRAME} samples)"
            ),
            started_at=started_at,
        )

    last = frames[-1]
    pixels = list(last.pixels)
    span_hz = last.end_freq_hz - last.start_freq_hz
    bin_hz = span_hz / max(1, len(pixels) - 1)
    center = len(pixels) // 2
    inband_half = int(_SCOPE_INBAND_HZ / bin_hz)
    baseline_from = int(_SCOPE_BASELINE_MIN_HZ / bin_hz)

    inband = pixels[max(0, center - inband_half) : center + inband_half + 1]
    baseline = (
        pixels[: max(0, center - baseline_from)] + pixels[center + baseline_from + 1 :]
    )
    signal_peak = max(inband) if inband else 0
    baseline_median = float(statistics.median(baseline)) if baseline else 0.0

    passed = (
        signal_peak >= SCOPE_MIN_SIGNAL_PIXEL
        and signal_peak - baseline_median >= SCOPE_MIN_RISE_PIXELS
    )
    evidence.update(
        {
            "signal_peak": int(signal_peak),
            "baseline_median": baseline_median,
            "pixel_bins": len(pixels),
        }
    )
    error = (
        None
        if passed
        else (
            f"in-band pixel peak {signal_peak} vs baseline median "
            f"{baseline_median:.1f} below presence thresholds "
            f"(peak >= {SCOPE_MIN_SIGNAL_PIXEL}, rise >= {SCOPE_MIN_RISE_PIXELS})"
        )
    )
    return _probe_result(
        "scope.fft.presence",
        passed=passed,
        evidence=evidence,
        error=error,
        started_at=started_at,
    )


# ---------------------------------------------------------------------------
# Aggregation — fold probe results into the artifact/golden-gate machinery
# ---------------------------------------------------------------------------


async def run_audio_probe_checks(
    *,
    backend_factory: Callable[[], FakeAudioBackend] = _default_backend,
) -> list[CheckResult]:
    """Run the full AUDIO_PROBE family, each probe on a fresh fake backend."""
    return [
        await run_rx_rms_check(backend=backend_factory()),
        await run_tx_byte_perfect_check(backend=backend_factory()),
        await run_scope_presence_check(backend=backend_factory()),
    ]


def audio_probe_level_results(checks: Sequence[CheckResult]) -> list[LevelResult]:
    """Group probe results by level for ``build_validation_artifact``.

    Mirrors the runner/hardware grouping: ascending level order, original
    order preserved within a level, empty levels omitted.
    """
    by_level: dict[ValidationLevel, list[CheckResult]] = {}
    for check in checks:
        by_level.setdefault(check.level, []).append(check)
    return [
        LevelResult(level=level, checks=by_level[level]) for level in sorted(by_level)
    ]
