"""Regression tests for MOR-508 — per-rig RX downmix channel selection.

The FTX-1 presents its USB RX audio on the LEFT channel only (right channel is
near-silence / noise floor). The MOR-504 stereo→mono reconciliation downmix
collapses interleaved L/R via the per-pair average ``(L + R) // 2``. For a
mono-on-one-channel source that averages the live L with a silent R, halving the
delivered level (−6 dB) → quiet audio + low FFT scope.

These tests pin a per-rig channel selector threaded into the downmix:

- ``"left"``  → take L at FULL level (the FTX-1 fix),
- ``"right"`` → take R at FULL level,
- ``"mix"``   → the legacy ``(L + R) // 2`` average (default for every rig).

The default (``"mix"``) path MUST stay byte-identical so IC-7610/X6200 and any
unspecified rig see no behavior change.
"""

from __future__ import annotations

import struct

import pytest

from rigplane.audio.backend import (
    AudioDeviceId,
    PortAudioBackend,
    _downmix_stereo_to_mono_s16le,
)


def _stereo(pairs: list[tuple[int, int]]) -> bytes:
    return b"".join(struct.pack("<hh", left, right) for left, right in pairs)


def _mono(samples: list[int]) -> bytes:
    return b"".join(struct.pack("<h", s) for s in samples)


# ---------------------------------------------------------------------------
# Downmix function: channel selection
# ---------------------------------------------------------------------------


def test_left_only_source_downmixes_to_full_left_level() -> None:
    """A left-only stereo buffer (R == 0) → L at FULL level under ``left``.

    This is the MOR-508 bug: ``mix`` would deliver L/2 (−6 dB). ``left`` must
    deliver the live L sample unchanged.
    """
    pcm = _stereo([(1000, 0), (-2000, 0), (32000, 0)])
    out = _downmix_stereo_to_mono_s16le(pcm, channel="left")
    assert out == _mono([1000, -2000, 32000])


def test_right_only_source_downmixes_to_full_right_level() -> None:
    """A right-only stereo buffer (L == 0) → R at FULL level under ``right``."""
    pcm = _stereo([(0, 1500), (0, -2500), (0, 31000)])
    out = _downmix_stereo_to_mono_s16le(pcm, channel="right")
    assert out == _mono([1500, -2500, 31000])


def test_mix_default_is_byte_identical_to_legacy_average() -> None:
    """Default ``mix`` reproduces the legacy per-pair average exactly.

    No-arg call and explicit ``channel="mix"`` must both equal ``(L + R) // 2``
    so unspecified rigs (IC-7610/X6200) see zero behavior change.
    """
    pairs = [(1000, 2000), (-20, -40), (32000, 30000), (-32768, -32768)]
    pcm = _stereo(pairs)
    expected = _mono([(left + right) // 2 for left, right in pairs])
    assert _downmix_stereo_to_mono_s16le(pcm) == expected
    assert _downmix_stereo_to_mono_s16le(pcm, channel="mix") == expected


def test_dual_mono_left_select_equals_mix() -> None:
    """For a dual-mono source (L == R), ``left`` and ``mix`` agree (no doubling)."""
    pairs = [(1234, 1234), (-5000, -5000), (32767, 32767)]
    pcm = _stereo(pairs)
    expected = _mono([left for left, _ in pairs])
    assert _downmix_stereo_to_mono_s16le(pcm, channel="left") == expected
    assert _downmix_stereo_to_mono_s16le(pcm, channel="mix") == expected


def test_full_scale_left_select_does_not_clip_or_overflow() -> None:
    """Selecting a full-scale L (R silent) stays in s16 range — no overflow."""
    pcm = _stereo([(32767, 0), (-32768, 0)])
    out = _downmix_stereo_to_mono_s16le(pcm, channel="left")
    assert out == _mono([32767, -32768])
    # Re-decode to confirm valid s16 (struct.unpack would raise on overflow).
    decoded = list(struct.unpack(f"<{len(out) // 2}h", out))
    assert decoded == [32767, -32768]


# ---------------------------------------------------------------------------
# Threading: _PortAudioRxStream honors the channel selector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_portaudio_rx_stream_left_select_delivers_full_left() -> None:
    """open_rx(rx_audio_channel="left") → callback receives full-level L mono.

    Pins the end-to-end backend path: a left-only stereo block opened at 2 ch /
    deliver 1 ch with the ``left`` selector delivers the L sample at full level
    (not L/2) at the mono fixed-frame byte length.
    """

    class FakeIndata:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def tobytes(self) -> bytes:
            return self._payload

    captured_cb: dict[str, object] = {}

    class FakeSd:
        class InputStream:
            def __init__(self, **kw: object) -> None:
                captured_cb["cb"] = kw["callback"]

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def close(self) -> None:
                pass

    backend = PortAudioBackend(dependency_loader=lambda: (FakeSd(), object()))
    stream = backend.open_rx(
        AudioDeviceId(0),
        sample_rate=48_000,
        channels=2,
        frame_ms=20,
        deliver_channels=1,
        rx_audio_channel="left",
    )

    received: list[bytes] = []
    await stream.start(received.append)
    cb = captured_cb["cb"]
    assert callable(cb)

    # 960 left-only L/R pairs → one 20 ms mono frame after channel select.
    pairs = 960
    block = _stereo([(1000, 0)] * pairs)
    cb(FakeIndata(block), pairs, None, None)  # type: ignore[operator]

    expected = _mono([1000] * pairs)
    assert len(expected) == 1920  # mono fixed-frame, not 3840 (stereo)
    assert received == [expected]

    await stream.stop()
