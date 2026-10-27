"""Tests for ``rigplane_testkit/audio_markers.py``: render -> analyze -> check.

A clean render must pass ``check_marker`` with no problems; each mutated
copy must fail with the problem the mutation planted. Pure functions, no
simulator, no sockets.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from rigplane_testkit.audio_markers import (
    MarkerSpec,
    analyze_marker_pcm16,
    check_marker,
    render_marker_pcm16,
)

# Short spec keeps the pure-math tests fast; gaps stay 5 ms-aligned so the
# detector's 5 ms windows match them exactly.
_SPEC = MarkerSpec(duration_s=1.0, gaps=((0.20, 0.25), (0.60, 0.68)))


def _samples(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float64)


def _to_pcm(samples: np.ndarray) -> bytes:
    return np.round(np.clip(samples, -32768, 32767)).astype("<i2").tobytes()


def test_clean_render_passes() -> None:
    report = analyze_marker_pcm16(render_marker_pcm16(_SPEC), _SPEC)
    assert isinstance(report.freq_hz, float)
    assert abs(report.freq_hz - _SPEC.freq_hz) < 1.0
    assert abs(report.level_dbfs - _SPEC.level_dbfs) < 0.2
    assert report.duration_s == _SPEC.duration_s
    assert report.gaps == _SPEC.gaps
    assert report.gap_energy_dbfs == -np.inf
    assert report.residual_db < -60.0  # 16-bit quantization only
    assert check_marker(report, _SPEC) == []


def test_clean_short_low_frequency_tone_passes_at_default_limits() -> None:
    spec = MarkerSpec(freq_hz=100.0, duration_s=0.02, gaps=())
    report = analyze_marker_pcm16(render_marker_pcm16(spec), spec)
    assert report.residual_db < -60.0
    assert check_marker(report, spec) == []


def test_clean_non_aligned_gap_passes_at_default_limits() -> None:
    spec = replace(_SPEC, gaps=((0.201, 0.249),))
    report = analyze_marker_pcm16(render_marker_pcm16(spec), spec)
    assert report.residual_db < -60.0
    assert check_marker(report, spec) == []


@pytest.mark.parametrize("boundary_s", [0.201, 0.249 - 1 / 48000])
def test_contaminated_gap_boundary_is_reported(boundary_s: float) -> None:
    spec = replace(_SPEC, gaps=((0.201, 0.249),))
    samples = _samples(render_marker_pcm16(spec))
    samples[int(round(boundary_s * spec.sample_rate))] = 32767
    report = analyze_marker_pcm16(_to_pcm(samples), spec)
    assert report.residual_db < -60.0
    problems = check_marker(report, spec)
    assert len(problems) == 1 and "energy inside the planted gaps" in problems[0]


def test_wrong_frequency_is_reported() -> None:
    pcm = render_marker_pcm16(replace(_SPEC, freq_hz=_SPEC.freq_hz + 50.0))
    problems = check_marker(analyze_marker_pcm16(pcm, _SPEC), _SPEC)
    assert len(problems) == 1 and "frequency" in problems[0]


def test_wrong_level_is_reported() -> None:
    pcm = render_marker_pcm16(replace(_SPEC, level_dbfs=_SPEC.level_dbfs - 12.0))
    problems = check_marker(analyze_marker_pcm16(pcm, _SPEC), _SPEC)
    assert len(problems) == 1 and "level" in problems[0]


def test_unplanned_dropout_is_reported() -> None:
    samples = _samples(render_marker_pcm16(_SPEC))
    sr = _SPEC.sample_rate
    samples[int(0.40 * sr) : int(0.44 * sr)] = 0.0  # a 40 ms hole in the tone
    problems = check_marker(analyze_marker_pcm16(_to_pcm(samples), _SPEC), _SPEC)
    assert len(problems) == 1 and "dropout" in problems[0]


def test_second_tone_is_reported_as_residual() -> None:
    samples = _samples(render_marker_pcm16(_SPEC))
    t = np.arange(len(samples)) / _SPEC.sample_rate
    # -30 dB RELATIVE to the marker tone, and silent inside the planted
    # gaps, so the only problem the mix plants is the residual.
    leak = 10.0 ** ((_SPEC.level_dbfs - 30.0) / 20.0) * 32767.0
    second = leak * np.sin(2.0 * np.pi * 1500.0 * t)
    for start_s, end_s in _SPEC.gaps:
        second[
            int(round(start_s * _SPEC.sample_rate)) : int(
                round(end_s * _SPEC.sample_rate)
            )
        ] = 0.0
    samples += second
    problems = check_marker(analyze_marker_pcm16(_to_pcm(samples), _SPEC), _SPEC)
    assert len(problems) == 1 and "residual" in problems[0]


def test_noise_filled_gap_is_reported() -> None:
    samples = _samples(render_marker_pcm16(_SPEC))
    sr = _SPEC.sample_rate
    rng = np.random.default_rng(42)
    samples[int(0.20 * sr) : int(0.25 * sr)] = rng.normal(
        0.0, 10.0 ** (-40.0 / 20.0) * 32768.0, int(0.05 * sr)
    )
    problems = check_marker(analyze_marker_pcm16(_to_pcm(samples), _SPEC), _SPEC)
    assert any("gap" in problem for problem in problems)


def test_shifted_gap_is_reported() -> None:
    shifted = replace(_SPEC, gaps=tuple((a + 0.05, b + 0.05) for a, b in _SPEC.gaps))
    pcm = render_marker_pcm16(shifted)
    problems = check_marker(analyze_marker_pcm16(pcm, _SPEC), _SPEC)
    assert any("gap" in problem for problem in problems)


def test_wrong_duration_is_reported() -> None:
    pcm = render_marker_pcm16(_SPEC)[: int(0.8 * _SPEC.sample_rate) * 2]
    problems = check_marker(analyze_marker_pcm16(pcm, _SPEC), _SPEC)
    assert any("duration" in problem for problem in problems)


def test_partial_tail_corruption_is_reported() -> None:
    """A corrupt partial 5 ms tail window must reach the signal checks.

    Appending 239 full-scale samples to a clean render adds only 5 ms, so
    the duration check stays inside its tolerance — but the tail's energy
    is far above the marker tone's, so a residual problem must be reported.
    """
    pcm = render_marker_pcm16(_SPEC) + np.full(239, 32767, dtype="<i2").tobytes()
    problems = check_marker(analyze_marker_pcm16(pcm, _SPEC), _SPEC)
    assert problems and any("residual" in problem for problem in problems)


@pytest.mark.parametrize("freq_hz", [1000.25, 1499.75])
def test_clean_off_bin_tone_passes_at_default_limits(freq_hz: float) -> None:
    """A clean tone between FFT bins must pass with the default tolerances.

    At 1 s / 48 kHz the bin width is 1 Hz, so these frequencies sit a
    quarter-bin off; parabolic interpolation alone leaves the sine fit at
    a biased frequency whose accumulated phase error inflates the
    residual above the -30 dB limit despite an undamaged signal.
    """
    spec = MarkerSpec(freq_hz=freq_hz, duration_s=1.0, gaps=())
    report = analyze_marker_pcm16(render_marker_pcm16(spec), spec)
    assert check_marker(report, spec) == []
