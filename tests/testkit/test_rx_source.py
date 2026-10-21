"""Unit tests for ``rigplane_testkit.icom.rx_source.WavSource``.

WAVs are generated into ``tmp_path`` (nothing committed). Red mutation:
skip the 48 kHz stereo conversion in ``_load``, or ignore the slot wait
/ loop wrap in ``read_burst`` — all expectations go red.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from rigplane_testkit.icom.rx_source import RX_BURST_BYTES, WavSource

FRAMES_PER_BURST = RX_BURST_BYTES // 4


def _write_wav(
    path: Path, *, rate: int, channels: int, samples: bytes, width: int = 2
) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(samples)


def _mono_sine(rate: int, seconds: float) -> bytes:
    """A 1 kHz s16 sine — signal-shaped, unlike the simulator's ramp."""
    n = int(rate * seconds)
    return struct.pack(
        f"<{n}h",
        *[int(12000 * math.sin(2 * math.pi * 1000 * i / rate)) for i in range(n)],
    )


def _stereo_wav(path: Path, bursts: int) -> bytes:
    """A 48 kHz stereo s16 WAV, deterministic full-range; returns raw frames."""
    n = bursts * FRAMES_PER_BURST * 2
    raw = struct.pack(f"<{n}h", *(((i * 37) % 65536) - 32768 for i in range(n)))
    _write_wav(path, rate=48000, channels=2, samples=raw)
    return raw


def test_burst_size_and_48k_stereo_passthrough_bit_exact(tmp_path: Path) -> None:
    assert RX_BURST_BYTES == 3840
    raw = _stereo_wav(tmp_path / "rx.wav", bursts=3)
    src = WavSource(tmp_path / "rx.wav")
    # Past the end too (once mode): every read is still exactly one burst.
    assert {len(src.read_burst()) for _ in range(5)} == {RX_BURST_BYTES}
    src.reset()
    assert src.read_burst() == raw[:RX_BURST_BYTES]
    assert src.read_burst() == raw[RX_BURST_BYTES : 2 * RX_BURST_BYTES]


def test_12k_mono_resampled_to_48k_stereo(tmp_path: Path) -> None:
    wav = tmp_path / "m12.wav"
    _write_wav(wav, rate=12000, channels=1, samples=_mono_sine(12000, 1.0))
    src = WavSource(wav)
    served = b"".join(src.read_burst() for _ in range(50))  # 1 s at 48 kHz
    assert len(served) == 4 * 12000 * 4  # round(n * 48000 / 12000) stereo frames
    pairs = np.frombuffer(served, "<i2").reshape(-1, 2)
    assert (pairs[:, 0] == pairs[:, 1]).all()  # mono duplicated to both channels
    assert src.read_burst() == bytes(RX_BURST_BYTES)  # once: silence after the end


def test_loop_wraps_to_the_first_burst(tmp_path: Path) -> None:
    raw = _stereo_wav(tmp_path / "loop.wav", bursts=1)  # exactly 960 frames
    src = WavSource(tmp_path / "loop.wav", loop=True)
    first = src.read_burst()
    assert first == raw
    assert src.read_burst() == first  # wrapped to the file start
    short = raw[: RX_BURST_BYTES // 2]  # shorter than one burst: 480 frames
    _write_wav(tmp_path / "short.wav", rate=48000, channels=2, samples=short)
    tiny = WavSource(tmp_path / "short.wav", loop=True)
    assert tiny.read_burst() == short + short
    assert len(tiny.read_burst()) == RX_BURST_BYTES
    # Sub-burst loop keeps the wrapped offset: 300 frames resume at 60.
    sub = raw[: 300 * 4]
    _write_wav(tmp_path / "sub.wav", rate=48000, channels=2, samples=sub)
    wrapped = WavSource(tmp_path / "sub.wav", loop=True)
    assert len(wrapped.read_burst()) == RX_BURST_BYTES
    assert wrapped.read_burst() == (sub * 4)[240 : 240 + RX_BURST_BYTES]


def test_slot_start_waits_for_the_boundary(tmp_path: Path) -> None:
    raw = _stereo_wav(tmp_path / "slot.wav", bursts=2)
    src = WavSource(tmp_path / "slot.wav", start="slot", slot_s=15.0, utc=lambda: 3.0)
    silence = bytes(RX_BURST_BYTES)
    # Burst k of the mixer lands at reset + 20 ms·k; the burst AT the boundary (k=600) carries the first sample.
    assert [src.read_burst() for _ in range(599)] == [silence] * 599
    assert src.read_burst() == raw[:RX_BURST_BYTES]
    # Within 20 ms of a boundary (modulo): starts immediately.
    near = WavSource(
        tmp_path / "slot.wav", start="slot", slot_s=15.0, utc=lambda: 30.005
    )
    assert near.read_burst() == raw[:RX_BURST_BYTES]


def test_reset_restarts_the_file(tmp_path: Path) -> None:
    raw = _stereo_wav(tmp_path / "r.wav", bursts=3)
    src = WavSource(tmp_path / "r.wav")
    first = src.read_burst()
    src.read_burst()
    src.reset()
    assert src.read_burst() == first == raw[:RX_BURST_BYTES]


@pytest.mark.parametrize(
    ("rate", "channels", "width", "frames"),
    [
        (48000, 2, 1, 8),  # 8-bit samples
        (48000, 3, 2, 8),  # three channels
        (7000, 1, 2, 8),  # rate below the floor
        (100000, 1, 2, 8),  # rate above the ceiling
        (48000, 2, 2, 0),  # empty file
    ],
)
def test_rejects_unsupported_wav(
    tmp_path: Path, rate: int, channels: int, width: int, frames: int
) -> None:
    p = tmp_path / "bad.wav"
    _write_wav(
        p,
        rate=rate,
        channels=channels,
        samples=bytes(width * channels * frames),
        width=width,
    )
    with pytest.raises(ValueError):
        WavSource(p)


def test_rejects_missing_file_and_bad_arguments(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        WavSource(tmp_path / "does-not-exist.wav")
    _stereo_wav(tmp_path / "ok.wav", bursts=1)
    with pytest.raises(ValueError, match="start"):
        WavSource(tmp_path / "ok.wav", start="soon")
    with pytest.raises(ValueError, match="slot_s"):
        WavSource(tmp_path / "ok.wav", slot_s=0.0)
