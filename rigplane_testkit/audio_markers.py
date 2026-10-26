"""Tone-with-silence markers for TX-audio pipeline tests.

A marker is a mono 16-bit PCM tone at a known frequency and level with
planted windows of exact digital silence. ``render_marker_pcm16``
synthesizes one; ``analyze_marker_pcm16`` measures a captured buffer
(frequency by Hann-windowed FFT with parabolic interpolation, level and
residual by a least-squares sine fit on the tone-on samples, gaps as runs
of all-zero 5 ms windows — the final partial window counts as one shorter
window); ``check_marker`` turns the measurement into a list of readable
problems (empty = pass). Stdlib + numpy only. Used by the Core simulated-TX
end-to-end test and by the Pro nightly scenario.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Gap detection resolution: runs of all-zero windows of this length.
_GAP_WINDOW_S = 0.005

_FULL_SCALE = 32768.0  # int16 full scale; samples are analyzed in +-1


@dataclass(frozen=True)
class MarkerSpec:
    """What a marker is: the tone and the planted silence windows.

    ``gaps`` are (start_s, end_s) windows of exact digital silence inside
    the tone; ``level_dbfs`` is the tone's peak level.
    """

    freq_hz: float = 1000.0
    level_dbfs: float = -20.0
    sample_rate: int = 48000
    duration_s: float = 3.0
    gaps: tuple[tuple[float, float], ...] = ((0.80, 0.90), (1.60, 1.75))


def render_marker_pcm16(spec: MarkerSpec) -> bytes:
    """Mono s16le: the tone at ``spec`` level, hard zero inside the gaps."""
    n = int(round(spec.duration_s * spec.sample_rate))
    t = np.arange(n) / spec.sample_rate
    tone = np.sin(2.0 * math.pi * spec.freq_hz * t) * 10.0 ** (spec.level_dbfs / 20.0)
    for start_s, end_s in spec.gaps:
        tone[_sample_range(spec, start_s, end_s, n)] = 0.0
    return np.round(tone * 32767.0).astype("<i2").tobytes()


def _sample_range(spec: MarkerSpec, start_s: float, end_s: float, n: int) -> slice:
    """The sample slice of a (start_s, end_s) window, clipped to n."""
    return slice(
        min(n, max(0, int(round(start_s * spec.sample_rate)))),
        min(n, max(0, int(round(end_s * spec.sample_rate)))),
    )


@dataclass(frozen=True)
class MarkerReport:
    """What a captured buffer measured against a spec looks like."""

    freq_hz: float  # FFT peak, parabolic interpolation, tone-on windows
    level_dbfs: float  # peak-equivalent level of the tone (sine fit)
    gaps: tuple[tuple[float, float], ...]  # silence windows (5 ms windows; a
    # trailing run ends at the buffer end when it reaches into the tail)
    residual_db: float  # non-tone energy vs the tone (more negative = cleaner)
    duration_s: float
    gap_energy_dbfs: float  # RMS inside the PLANTED gaps (-inf = silence)


def analyze_marker_pcm16(pcm: bytes, spec: MarkerSpec) -> MarkerReport:
    """Measure a captured mono s16le buffer against the spec it should be."""
    sr = spec.sample_rate
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / _FULL_SCALE
    n = len(x)
    win = max(1, int(round(sr * _GAP_WINDOW_S)))
    nwin, tail = divmod(n, win)
    silent = (
        ~np.any(x[: nwin * win].reshape(nwin, win) != 0.0, axis=1)
        if nwin
        else np.zeros(0, dtype=bool)
    )
    if tail:
        # The partial tail window counts as one shorter window: all-zero it
        # extends a gap run (which then ends at n/sr), otherwise it is
        # tone-on and enters every signal measurement below.
        silent = np.append(silent, not np.any(x[nwin * win :] != 0.0))
    nwindows = len(silent)
    # A window's end offset: full windows end on win boundaries; the tail
    # window (when present) ends at n.
    ends = np.minimum((np.arange(nwindows) + 1) * win, n)
    gaps: list[tuple[float, float]] = []
    tone_on = np.ones(nwindows, dtype=bool)
    i = 0
    while i < nwindows:
        if silent[i]:
            j = i
            while j + 1 < nwindows and silent[j + 1]:
                j += 1
            gaps.append((i * win / sr, ends[j] / sr))
            tone_on[i : j + 1] = False
            i = j + 1
        else:
            i += 1
    gap_energy = _gap_energy_dbfs(x, spec)
    if not tone_on.any():
        # No tone at all: frequency/level fail their checks on their own.
        return MarkerReport(0.0, -math.inf, tuple(gaps), 0.0, n / sr, gap_energy)
    mask = np.repeat(tone_on[:nwin], win) if nwin else np.zeros(0, dtype=bool)
    if tail:
        mask = np.concatenate([mask, np.full(tail, tone_on[nwin])])
    idx = np.nonzero(mask)[0]
    freq_hz = _measure_freq(x, sr)
    level_dbfs, residual_db = _fit_tone(x, idx, sr, freq_hz)
    return MarkerReport(
        freq_hz, level_dbfs, tuple(gaps), residual_db, n / sr, gap_energy
    )


def _measure_freq(x: np.ndarray, sr: int) -> float:
    """FFT peak frequency with parabolic interpolation.

    The planted gaps are zeros, so the whole buffer transforms as the
    windowed tone; the gap modulation only smears the skirts.
    """
    windowed = x * np.hanning(len(x))
    magnitude = np.abs(np.fft.rfft(windowed))
    if len(magnitude) < 3:
        return 0.0
    k = int(np.argmax(magnitude[1:])) + 1  # never DC
    if not 0 < k < len(magnitude) - 1:
        return k * sr / len(x)
    left, center, right = magnitude[k - 1], magnitude[k], magnitude[k + 1]
    denominator = left - 2.0 * center + right
    if denominator == 0.0:
        return k * sr / len(x)
    delta = 0.5 * (left - right) / denominator
    return (k + delta) * sr / len(x)


def _fit_tone(
    x: np.ndarray, idx: np.ndarray, sr: int, freq_hz: float
) -> tuple[float, float]:
    """Least-squares sin/cos fit at ``freq_hz`` over the tone-on samples.

    ``idx`` holds the tone-on SAMPLE indices (the fit skips the planted
    gaps); the samples' true time indices keep the gaps from breaking
    phase. Returns (peak level dBFS, residual energy relative to the fit
    in dB).
    """
    t = idx / sr
    basis = np.column_stack(
        [np.cos(2.0 * math.pi * freq_hz * t), np.sin(2.0 * math.pi * freq_hz * t)]
    )
    coef, *_ = np.linalg.lstsq(basis, x[idx], rcond=None)
    fit = basis @ coef
    amplitude = float(np.hypot(coef[0], coef[1]))
    level_dbfs = -math.inf if amplitude <= 0 else 20.0 * math.log10(amplitude)
    fit_energy = float(fit @ fit)
    if fit_energy <= 0:
        return level_dbfs, 0.0
    residual = x[idx] - fit
    return level_dbfs, 10.0 * math.log10(float(residual @ residual) / fit_energy)


def _gap_energy_dbfs(x: np.ndarray, spec: MarkerSpec) -> float:
    """RMS level inside the planted gaps: -inf for exact digital silence."""
    parts = [
        x[_sample_range(spec, start_s, end_s, len(x))] for start_s, end_s in spec.gaps
    ]
    parts = [part for part in parts if len(part)]
    if not parts:
        return -math.inf
    inside = np.concatenate(parts)
    energy = float(inside @ inside)
    if energy <= 0:
        return -math.inf
    return 10.0 * math.log10(energy / len(inside))


def check_marker(
    report: MarkerReport,
    spec: MarkerSpec,
    *,
    freq_tol_hz: float = 5.0,
    level_tol_db: float = 1.5,
    gap_tol_s: float = 0.015,
    max_residual_db: float = -30.0,
    max_gap_energy_dbfs: float = -70.0,
    duration_tol_s: float = 0.1,
) -> list[str]:
    """The report against the spec: a list of readable problems ([] = pass)."""
    problems: list[str] = []
    if abs(report.freq_hz - spec.freq_hz) > freq_tol_hz:
        problems.append(
            f"tone frequency {report.freq_hz:.1f} Hz is outside "
            f"{spec.freq_hz:.1f} +/- {freq_tol_hz} Hz"
        )
    if abs(report.level_dbfs - spec.level_dbfs) > level_tol_db:
        problems.append(
            f"tone level {report.level_dbfs:.1f} dBFS is outside "
            f"{spec.level_dbfs:.1f} +/- {level_tol_db} dB"
        )
    if abs(report.duration_s - spec.duration_s) > duration_tol_s:
        problems.append(
            f"duration {report.duration_s:.3f}s is outside "
            f"{spec.duration_s:.3f} +/- {duration_tol_s}s"
        )
    unmatched = list(report.gaps)
    for start_s, end_s in spec.gaps:
        match = _match_gap(unmatched, start_s, end_s, gap_tol_s)
        if match is None:
            problems.append(
                f"planted gap {start_s:.3f}s..{end_s:.3f}s is missing or "
                f"shifted by more than {gap_tol_s}s"
            )
        else:
            unmatched.remove(match)
    for start_s, end_s in unmatched:
        problems.append(
            f"unplanned dropout {start_s:.3f}s..{end_s:.3f}s (no planted gap there)"
        )
    if report.residual_db > max_residual_db:
        problems.append(
            f"residual {report.residual_db:.1f} dB is above the "
            f"{max_residual_db:.1f} dB limit"
        )
    if report.gap_energy_dbfs > max_gap_energy_dbfs:
        problems.append(
            f"energy inside the planted gaps {report.gap_energy_dbfs:.1f} dBFS "
            f"is above the {max_gap_energy_dbfs:.1f} dBFS limit"
        )
    return problems


def _match_gap(
    detected: list[tuple[float, float]], start_s: float, end_s: float, tol_s: float
) -> tuple[float, float] | None:
    """The detected gap covering the planted one within ``tol_s``, if any."""
    best: tuple[float, float] | None = None
    best_error = tol_s
    for gap in detected:
        error = max(abs(gap[0] - start_s), abs(gap[1] - end_s))
        if error <= best_error:
            best, best_error = gap, error
    return best
