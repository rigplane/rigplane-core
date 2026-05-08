"""Safe audio capability probe runner helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping

from rigplane.audio.probe import (
    AudioProbeArtifact,
    AudioProbeCandidate,
    AudioProbeResult,
    AudioProbeStatus,
    classify_stock_radio_lan_probe_error,
)

AudioProbeAttempt = Callable[[AudioProbeCandidate], Awaitable[AudioProbeResult]]


async def run_audio_probe(
    candidates: Iterable[AudioProbeCandidate],
    attempt: AudioProbeAttempt,
    *,
    candidate_cooldown_s: float = 0.0,
    retry_rejected: int = 0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[AudioProbeResult]:
    """Run probe candidates sequentially and normalize attempt failures."""

    results: list[AudioProbeResult] = []
    candidate_list = list(candidates)
    for index, candidate in enumerate(candidate_list):
        result = await _attempt_with_retries(
            candidate,
            attempt,
            candidate_cooldown_s=candidate_cooldown_s,
            retry_rejected=retry_rejected,
            sleep=sleep,
        )
        results.append(result)
        if candidate_cooldown_s > 0 and index < len(candidate_list) - 1:
            await sleep(candidate_cooldown_s)
    return results


async def _attempt_with_retries(
    candidate: AudioProbeCandidate,
    attempt: AudioProbeAttempt,
    *,
    candidate_cooldown_s: float,
    retry_rejected: int,
    sleep: Callable[[float], Awaitable[None]],
) -> AudioProbeResult:
    tries = 0
    while True:
        result = await _attempt_once(candidate, attempt)
        if result.status is not AudioProbeStatus.REJECTED or tries >= retry_rejected:
            return result
        tries += 1
        if candidate_cooldown_s > 0:
            await sleep(candidate_cooldown_s)


async def _attempt_once(
    candidate: AudioProbeCandidate,
    attempt: AudioProbeAttempt,
) -> AudioProbeResult:
    try:
        return await attempt(candidate)
    except Exception as exc:
        status, reason = classify_stock_radio_lan_probe_error(exc)
        return AudioProbeResult(
            candidate=candidate,
            status=status,
            phase="conninfo" if status is AudioProbeStatus.REJECTED else "runtime",
            reason=reason,
            error=str(exc),
        )


def dry_run_probe_results(
    candidates: Iterable[AudioProbeCandidate],
) -> list[AudioProbeResult]:
    """Return skipped results for operators validating the probe plan."""

    return [
        AudioProbeResult(
            candidate=candidate,
            status=AudioProbeStatus.SKIPPED,
            phase="dry-run",
            reason="dry-run",
        )
        for candidate in candidates
    ]


def summarize_probe_results(
    results: Iterable[AudioProbeResult],
) -> dict[str, int]:
    """Count probe results by normalized status."""

    summary = {status.value: 0 for status in AudioProbeStatus}
    for result in results:
        summary[result.status.value] += 1
    return summary


def build_probe_artifact(
    *,
    model: str,
    profile_id: str,
    transport: str,
    results: list[AudioProbeResult],
    metadata: Mapping[str, object] | None = None,
) -> AudioProbeArtifact:
    """Build a machine-readable probe artifact with a stable summary."""

    merged_metadata: dict[str, object] = dict(metadata or {})
    merged_metadata["summary"] = summarize_probe_results(results)
    return AudioProbeArtifact(
        model=model,
        profile_id=profile_id,
        transport=transport,
        results=results,
        metadata=merged_metadata,
    )


__all__ = [
    "AudioProbeAttempt",
    "build_probe_artifact",
    "dry_run_probe_results",
    "run_audio_probe",
    "summarize_probe_results",
]
