"""MOR-4227 equality pins for the vectorized audio hot paths.

The numpy downmix (:func:`rigplane.audio.backend.
_downmix_stereo_to_mono_s16le`) and RMS meter (:func:`rigplane.audio.bridge.
_rms_dbfs`) are pure speedups: the old stdlib implementations are reproduced
here verbatim as references, and every test pins the vectorized output to
them — byte-for-byte for the downmix, exactly for realistic RMS frames
(both sums are exact integers in float64) with a 1e-9 dB bound besides.
"""

from __future__ import annotations

import array
import math
import sys
from random import Random

import pytest

from rigplane.audio.backend import _downmix_stereo_to_mono_s16le
from rigplane.audio.bridge import _INT16_MAX, _rms_dbfs


# ---------------------------------------------------------------------------
# References: the pre-MOR-4227 stdlib implementations, verbatim.
# ---------------------------------------------------------------------------


def _reference_downmix(
    pcm: bytes,
    *,
    channel: str = "mix",
    open_channels: int = 2,
    deliver_channels: int = 1,
) -> bytes:
    frame_bytes = 2 * open_channels
    usable = len(pcm) - (len(pcm) % frame_bytes)
    if usable <= 0:
        return b""
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    if sys.byteorder != "little":
        samples.byteswap()
    left = samples[0::open_channels]
    right = samples[1::open_channels]
    if deliver_channels == 2:
        stereo = array.array("h", bytes(4 * len(left)))
        stereo[0::2] = left
        stereo[1::2] = right
        if sys.byteorder != "little":
            stereo.byteswap()
        return stereo.tobytes()
    if channel == "left":
        mono = left
    elif channel == "right":
        mono = right
    else:  # "mix"
        mono = array.array("h", [(lch + rch) // 2 for lch, rch in zip(left, right)])
    if sys.byteorder != "little":
        mono.byteswap()
    return mono.tobytes()


def _reference_rms_dbfs(pcm: bytes) -> float:
    even_length = len(pcm) - (len(pcm) % 2)
    samples: array.array[int] = array.array("h")
    if even_length > 0:
        samples.frombytes(pcm[:even_length])
        if sys.byteorder != "little":
            samples.byteswap()
    if not samples:
        return -96.0
    rms = math.sqrt(sum(float(s) * float(s) for s in samples) / len(samples))
    if rms < 1.0:
        return -96.0
    return 20.0 * math.log10(rms / _INT16_MAX)


def _pack(samples: list[int]) -> bytes:
    packed = array.array("h", samples)
    if sys.byteorder != "little":
        packed.byteswap()
    return packed.tobytes()


def _s16_values(rng: Random, count: int) -> list[int]:
    return [
        rng.choice(
            (
                rng.randint(-32768, 32767),
                rng.choice((-32768, -1, 0, 1, 32767)),
            )
        )
        for _ in range(count)
    ]


# ---------------------------------------------------------------------------
# Downmix equality pins.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "deliver_channels"),
    [("mix", 1), ("left", 1), ("right", 1), ("mix", 2)],
)
def test_downmix_matches_reference_exhaustive_small_range(
    channel: str, deliver_channels: int
) -> None:
    """Every L/R pair in a small signed range, exact-match against stdlib."""
    for left in range(-5, 6):
        for right in range(-5, 6):
            pcm = _pack([left, right, left, right, -32768, 32767])
            assert _downmix_stereo_to_mono_s16le(
                pcm, channel=channel, deliver_channels=deliver_channels
            ) == _reference_downmix(
                pcm, channel=channel, deliver_channels=deliver_channels
            ), (left, right, channel, deliver_channels)


def test_downmix_negative_sums_floor_like_python() -> None:
    """Python ``//`` floors negative sums: (-3 + 0) // 2 == -2, not -1."""
    pcm = _pack([-3, 0, -1, -2, -32768, -32768])
    mono = _downmix_stereo_to_mono_s16le(pcm)
    assert mono == _pack([-2, -2, -32768])
    # -32768 + -32768 = -65536; // 2 = -32768 — no s16 wraparound either way.
    assert mono == _reference_downmix(pcm)


@pytest.mark.parametrize("open_channels", [2, 3, 4, 6, 8, 16, 32])
def test_downmix_matches_reference_random_full_s16(open_channels: int) -> None:
    rng = Random(4227 + open_channels)
    for channel, deliver_channels in (
        ("mix", 1),
        ("left", 1),
        ("right", 1),
        ("mix", 2),
    ):
        frames = rng.randint(1, 9)
        pcm = _pack(_s16_values(rng, frames * open_channels))
        assert _downmix_stereo_to_mono_s16le(
            pcm,
            channel=channel,
            open_channels=open_channels,
            deliver_channels=deliver_channels,
        ) == _reference_downmix(
            pcm,
            channel=channel,
            open_channels=open_channels,
            deliver_channels=deliver_channels,
        )


@pytest.mark.parametrize("open_channels", [2, 4, 32])
@pytest.mark.parametrize("tail_bytes", [1, 2, 3])
def test_downmix_drops_trailing_partial_frame(
    open_channels: int, tail_bytes: int
) -> None:
    """A tail that is not a whole N-channel frame is dropped (both impls)."""
    rng = Random(open_channels * 10 + tail_bytes)
    pcm = _pack(_s16_values(rng, 3 * open_channels)) + bytes(tail_bytes)
    assert _downmix_stereo_to_mono_s16le(
        pcm, open_channels=open_channels
    ) == _reference_downmix(pcm, open_channels=open_channels)


def test_downmix_empty_and_subframe_inputs() -> None:
    for pcm in (b"", b"\x01", b"\x00\x01", b"\xff" * 3):
        assert _downmix_stereo_to_mono_s16le(pcm) == b""
        assert _downmix_stereo_to_mono_s16le(pcm, open_channels=4) == b""
        assert _downmix_stereo_to_mono_s16le(pcm, deliver_channels=2) == b""


# ---------------------------------------------------------------------------
# RMS equality pins.
# ---------------------------------------------------------------------------


def test_rms_dbfs_edge_cases_match_reference() -> None:
    assert _rms_dbfs(b"") == -96.0
    assert _rms_dbfs(b"\x01") == -96.0  # odd byte count truncates to nothing
    assert _rms_dbfs(b"\x00\x00") == -96.0  # digital silence
    # One LSB sample (0x0001 LE): rms == 1.0 is NOT under the floor.
    assert _rms_dbfs(b"\x01\x00") == _reference_rms_dbfs(b"\x01\x00")
    # Odd byte count with one whole sample present: the lone sample wins.
    assert _rms_dbfs(b"\x00\x01\x02") == _reference_rms_dbfs(b"\x00\x01\x02")
    for pcm in (b"", b"\x01", b"\x00\x00", b"\x01\x00", b"\x00\x01\x02"):
        assert _rms_dbfs(pcm) == _reference_rms_dbfs(pcm)


def test_rms_dbfs_full_scale_and_corner_samples() -> None:
    # A single -32768 sample: rms = 32768 >= 1 → +5.3e-5 dBFS, not the floor.
    assert _rms_dbfs(_pack([-32768])) == _reference_rms_dbfs(_pack([-32768]))
    assert _rms_dbfs(_pack([32767] * 960)) == _reference_rms_dbfs(_pack([32767] * 960))
    assert _rms_dbfs(_pack([0] * 959 + [32767])) == _reference_rms_dbfs(
        _pack([0] * 959 + [32767])
    )


def test_rms_dbfs_matches_reference_exactly_on_realistic_frames() -> None:
    """s16 squares and their sums stay exact integers in float64, so the
    vectorized sum equals the reference's left-to-right sum bit for bit."""
    rng = Random(4227)
    for _ in range(25):
        pcm = _pack(_s16_values(rng, rng.choice((960, 480, 1920, 13))))
        new = _rms_dbfs(pcm)
        old = _reference_rms_dbfs(pcm)
        assert new == old or abs(new - old) < 1e-9


def test_rms_dbfs_matches_reference_with_odd_trailing_byte() -> None:
    rng = Random(99)
    pcm = _pack(_s16_values(rng, 97)) + b"\x03"
    assert abs(_rms_dbfs(pcm) - _reference_rms_dbfs(pcm)) < 1e-9
