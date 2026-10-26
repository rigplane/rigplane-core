"""Tests for ``rigplane_testkit/audio_markers.py``: render -> analyze -> check.

A clean render must pass ``check_marker`` with no problems; each mutated
copy must fail with the problem the mutation planted. Pure functions, no
simulator, no sockets.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

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
