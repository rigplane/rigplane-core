"""Unit tests for ``rigplane_testkit.icom.rx_source.WavSource``."""

from __future__ import annotations

import struct
import wave
from pathlib import Path

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
    _write_wav(wav, rate=12000, channels=1, samples=struct.pack("<3h", 1, 7, -3))
    src = WavSource(wav)
    # Input positions 0, 1/4, ...; halfway values round to even.
    samples = (1, 2, 4, 6, 7, 4, 2, 0, -3, -3, -3, -3)
    expected = struct.pack("<24h", *(v for v in samples for _ in range(2)))
    assert src.read_burst() == expected + bytes(RX_BURST_BYTES - len(expected))
    assert src.read_burst() == bytes(RX_BURST_BYTES)


def test_64k_stereo_downsample_preserves_channels_and_eof(tmp_path: Path) -> None:
    wav = tmp_path / "s64.wav"
    _write_wav(
        wav,
        rate=64000,
        channels=2,
        samples=struct.pack("<8h", 1, -11, 8, 3, -5, 12, 11, -7),
    )
    # Three output frames at input positions 0, 4/3 and 8/3.
    expected = struct.pack("<6h", 1, -11, 4, 6, 6, -1)
    src = WavSource(wav)
    assert src.read_burst() == expected + bytes(RX_BURST_BYTES - len(expected))
    assert src.read_burst() == bytes(RX_BURST_BYTES)


@pytest.mark.parametrize("rate", [12000, 64000])
def test_resampled_duration_crosses_burst_boundary(tmp_path: Path, rate: int) -> None:
    wav = tmp_path / "duration.wav"
    # 321 frames at 12 kHz or 1712 at 64 kHz both become 1284 frames.
    frames = 321 if rate == 12000 else 1712
    _write_wav(wav, rate=rate, channels=1, samples=struct.pack("<h", 1234) * frames)
    src = WavSource(wav)
    frame = struct.pack("<2h", 1234, 1234)
    assert src.read_burst() == frame * 960
    assert src.read_burst() == frame * 324 + bytes(636 * 4)
    assert src.read_burst() == bytes(RX_BURST_BYTES)


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


def test_rejects_float_format_wav(tmp_path: Path) -> None:
    """IEEE-float WAV (format tag 3) raises ValueError, not wave.Error."""
    p = tmp_path / "float.wav"
    frames = 8
    data = bytes(4 * frames)  # 32-bit float silence
    p.write_bytes(
        struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + len(data),
            b"WAVE",
            b"fmt ",
            16,
            3,  # IEEE float, not PCM
            1,
            8000,
            8000 * 4,
            4,
            32,
            b"data",
            len(data),
        )
        + data
    )
    with pytest.raises(ValueError):
        WavSource(p)


def test_rejects_non_wav_bytes(tmp_path: Path) -> None:
    """A file that is not a WAV at all raises ValueError, not wave.Error."""
    p = tmp_path / "nope.wav"
    p.write_bytes(b"not a wav")
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
