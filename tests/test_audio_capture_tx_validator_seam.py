"""Cross-seam regression: capture output is ALWAYS accepted by core PCM TX validator.

This locks the seam between two sides that previously disagreed on frame size:

* **Capture (producer)** — :class:`rigplane.audio.backend._PortAudioRxStream`,
  driven via :meth:`PortAudioBackend.open_rx`. Its PortAudio callback receives
  *engine-native, variable-size* device blocks (``blocksize=0``) and re-chunks
  them into fixed ``frame_ms`` frames before handing them to the consumer.
* **Validator (consumer)** — the managed core PCM TX path
  :meth:`AudioRuntimeMixin._push_audio_tx_pcm_internal` (reached through the
  public :meth:`push_audio_tx_pcm`). It raises
  :class:`AudioFormatError` unless ``len(frame) == fmt.frame_bytes``.

The bug these tests guard: ``_PortAudioRxStream`` used to forward the device-native
variable-size blocks verbatim (~960 bytes / 10 ms on a WASAPI shared-mode engine),
while the validator hard-requires exactly ``fmt.frame_bytes`` (1920 bytes for
48000/1ch/s16le/20 ms). Every TX frame was rejected -> radio Po=0 W. No test
previously spanned the capture *output* and the validator *required* size, so the
960-vs-1920 mismatch slipped through.

Both sides use the REAL production code (no re-implementation of the size check):
the capture is the real ``_PortAudioRxStream`` (only ``sounddevice`` is faked via
``open_rx``'s ``dependency_loader``), and the validator is the real
``AudioRuntimeMixin._push_audio_tx_pcm_internal`` (only the transport
``_audio_stream`` is faked so no socket is needed).
"""

from __future__ import annotations

import struct

import pytest

from rigplane.audio.backend import AudioDeviceId, PortAudioBackend
from rigplane.core.exceptions import AudioFormatError
from rigplane.core.types import AudioCodec
from rigplane.runtime._audio_runtime_mixin import AudioRuntimeMixin

# ---------------------------------------------------------------------------
# Fakes — minimal, matching tests/test_audio_backend.py style.
# ---------------------------------------------------------------------------


class _FakeIndata:
    """sounddevice indata stand-in: an ``int16`` buffer exposing ``tobytes()``."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def tobytes(self) -> bytes:
        return self._payload


def _make_fake_sd(captured_cb: dict[str, object]) -> type:
    """Build a fake ``sounddevice`` module whose InputStream records the callback.

    The capture path registers ``callback=`` and never calls ``read()``; the test
    drives the recorded callback directly with adversarial variable-size blocks.
    """

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

    return FakeSd


class _FakeTxAudioStream:
    """Records frames that survive the validator and reach the transport push."""

    def __init__(self) -> None:
        self.pushed: list[bytes] = []

    async def push_tx(self, data: bytes) -> None:
        self.pushed.append(bytes(data))


class _ValidatorHarness(AudioRuntimeMixin):
    """Minimal real ``AudioRuntimeMixin`` host that exercises the PCM TX validator.

    Only the two collaborators the validator touches are stubbed: ``_check_connected``
    (no socket) and ``_audio_stream`` (records pushed frames). The size check itself
    (``len(frame) != fmt.frame_bytes``) is the unmodified production code.
    """

    def __init__(self, *, sample_rate: int, channels: int, frame_ms: int) -> None:
        self._audio_stream = _FakeTxAudioStream()  # type: ignore[assignment]
        self._audio_tx_codec = AudioCodec.PCM_1CH_16BIT
        self._pcm_tx_fmt = (sample_rate, channels, frame_ms)

    def _check_connected(self) -> None:  # type: ignore[override]
        return None


# ---------------------------------------------------------------------------
# Supported (sample_rate, frame_ms, channels) combos exercised on both sides.
# ---------------------------------------------------------------------------

# Each entry is a format the capture path and the validator must agree on. The
# 20 ms / 48 kHz / mono case is the production RigPlane PCM TX contract; 10 ms is
# the smaller frame, and 2-channel checks the channel multiplier on both sides.
_SUPPORTED_FORMATS = [
    (48_000, 20, 1),
    (48_000, 10, 1),
    (24_000, 20, 1),
    (48_000, 20, 2),
]


def _expected_frame_bytes(sample_rate: int, frame_ms: int, channels: int) -> int:
    frame_samples = sample_rate * frame_ms // 1000
    return frame_samples * channels * 2  # s16le


def _open_capture(
    sample_rate: int,
    frame_ms: int,
    channels: int,
) -> tuple[object, dict[str, object]]:
    """Open the REAL capture stream against a fake sounddevice; return (stream, cb)."""
    captured_cb: dict[str, object] = {}
    fake_sd = _make_fake_sd(captured_cb)
    backend = PortAudioBackend(dependency_loader=lambda: (fake_sd(), object()))
    stream = backend.open_rx(
        AudioDeviceId(0),
        sample_rate=sample_rate,
        channels=channels,
        frame_ms=frame_ms,
    )
    return stream, captured_cb


def _tone_pcm(total_samples: int, channels: int) -> bytes:
    """A deterministic interleaved s16le buffer (a ramp doubles as a tone here).

    Distinct per-sample values let the concatenation assert catch any
    reorder/dup/loss, not just a length match.
    """
    out = bytearray()
    for n in range(total_samples * channels):
        out += struct.pack("<h", (n * 7 + 1) % 32768)
    return bytes(out)


# ---------------------------------------------------------------------------
# Test 1 — Cross-seam INTEGRATION: every captured frame is validator-accepted.
# ---------------------------------------------------------------------------


class TestCaptureFramesAlwaysAcceptedByTxValidator:
    @pytest.mark.asyncio()
    async def test_adversarial_blocks_emit_only_validator_accepted_frames(self) -> None:
        """Drive the real capture with adversarial variable-size blocks; feed every
        emitted frame into the real PCM TX validator. Assert ZERO ``AudioFormatError``
        and that accepted frames reconstruct the captured samples (no loss/dup/reorder).

        This is the regression guard for the 960-vs-1920 mismatch. If the capture
        reverted to forwarding device-native variable-size blocks verbatim, the
        validator would raise ``AudioFormatError`` on the first non-1920-byte block
        and this test would go RED (verified by monkeypatching the capture to
        forward verbatim — see test below).
        """
        sample_rate, frame_ms, channels = 48_000, 20, 1
        frame_bytes = _expected_frame_bytes(sample_rate, frame_ms, channels)

        stream, captured_cb = _open_capture(sample_rate, frame_ms, channels)

        # Collect every frame the capture emits to the consumer callback.
        emitted: list[bytes] = []
        await stream.start(emitted.append)
        cb = captured_cb["cb"]
        assert callable(cb)

        # Adversarial engine-native block sizes (in samples). Deliberately none is
        # a whole 960-sample frame and many straddle frame boundaries: 480, 200,
        # 760, 333, 480, 480, 187, ... The pattern repeats to span several seconds.
        block_pattern = (480, 200, 760, 333, 480, 480, 187, 911, 49, 600)
        # Several seconds of audio so the seam is hammered, not just touched once.
        target_samples = sample_rate * 4  # ~4 s mono
        reference = _tone_pcm(target_samples, channels)

        offset = 0
        i = 0
        total_bytes = len(reference)
        while offset < total_bytes:
            n_samples = block_pattern[i % len(block_pattern)]
            i += 1
            nbytes = n_samples * channels * 2
            chunk = reference[offset : offset + nbytes]
            if not chunk:
                break
            offset += len(chunk)
            cb(_FakeIndata(chunk), len(chunk) // (channels * 2), None, None)

        # --- The key assertion: every emitted frame is validator-accepted. ---
        validator = _ValidatorHarness(
            sample_rate=sample_rate, channels=channels, frame_ms=frame_ms
        )
        for frame in emitted:
            # Real production validator path; raises AudioFormatError on size
            # mismatch. The test asserts it NEVER does for capture output.
            await validator.push_audio_tx_pcm(frame)

        # No frame was rejected: every emitted frame reached the transport push.
        assert len(validator._audio_stream.pushed) == len(emitted)  # type: ignore[union-attr]
        # And every emitted frame is exactly one validator frame.
        assert emitted, "capture must emit at least one frame for a multi-second tone"
        assert all(len(f) == frame_bytes for f in emitted)

        # Sanity: accepted frames reconstruct the captured samples truncated to
        # whole frames (no loss, no dup, no reorder).
        whole_frames = total_bytes // frame_bytes
        assert b"".join(emitted) == reference[: whole_frames * frame_bytes]

        await stream.stop()

    @pytest.mark.asyncio()
    async def test_verbatim_forwarding_would_be_rejected(self) -> None:
        """Document/verify the guard: pre-fix verbatim forwarding -> validator rejects.

        Simulates the OLD behavior (forward each device-native variable-size block
        straight to the consumer, no re-chunking) and feeds those blocks into the
        SAME real validator. Asserts the validator DOES raise ``AudioFormatError``,
        proving the integration test above is a genuine regression guard rather than
        a tautology: the seam only passes because the capture re-chunks.
        """
        sample_rate, frame_ms, channels = 48_000, 20, 1
        validator = _ValidatorHarness(
            sample_rate=sample_rate, channels=channels, frame_ms=frame_ms
        )
        # A device-native block that is NOT a whole frame (480 samples = 960 bytes,
        # the ~10 ms WASAPI engine period that triggered the original 0 W bug).
        verbatim_block = _tone_pcm(480, channels)
        assert len(verbatim_block) == 960  # != 1920 = fmt.frame_bytes

        with pytest.raises(AudioFormatError, match="PCM frame size mismatch"):
            await validator.push_audio_tx_pcm(verbatim_block)


# ---------------------------------------------------------------------------
# Test 2 — CONTRACT: capture emitted-frame size == validator required frame_bytes.
# ---------------------------------------------------------------------------


class TestCaptureValidatorFrameSizeContract:
    @pytest.mark.parametrize(
        ("sample_rate", "frame_ms", "channels"), _SUPPORTED_FORMATS
    )
    @pytest.mark.asyncio()
    async def test_emitted_frame_size_equals_validator_required_bytes(
        self,
        sample_rate: int,
        frame_ms: int,
        channels: int,
    ) -> None:
        """Pin both sides to one truth for each supported format.

        The capture emits frames of ``frame_samples * channels * 2`` bytes; the
        validator requires exactly ``PcmAudioFormat.frame_bytes`` for the matching
        PCM format. They must be equal, so a future change to blocksize / rate /
        frame_ms / channels / codec / sample width on EITHER side breaks this test.
        """
        expected = _expected_frame_bytes(sample_rate, frame_ms, channels)

        # --- Validator side: the size the core PCM TX validator REQUIRES. ---
        # Construct the real format object the validator builds internally and read
        # its required frame_bytes (the exact value compared in the size check).
        from rigplane.audio._transcoder import PcmAudioFormat

        validator_required = PcmAudioFormat(
            sample_rate=sample_rate, channels=channels, frame_ms=frame_ms
        ).frame_bytes
        assert validator_required == expected

        # --- Capture side: the size the real capture path EMITS. ---
        stream, captured_cb = _open_capture(sample_rate, frame_ms, channels)
        emitted: list[bytes] = []
        await stream.start(emitted.append)
        cb = captured_cb["cb"]

        # Feed exactly two whole frames' worth of audio in oddly-sized blocks so the
        # emitted frame size (not the input block size) is what is asserted.
        two_frames_samples = (sample_rate * frame_ms // 1000) * 2
        reference = _tone_pcm(two_frames_samples, channels)
        # Split into three uneven blocks that cross the frame boundary.
        third = len(reference) // 3
        for chunk in (
            reference[:third],
            reference[third : 2 * third],
            reference[2 * third :],
        ):
            if chunk:
                cb(_FakeIndata(chunk), len(chunk) // (channels * 2), None, None)

        await stream.stop()

        assert emitted, "capture must emit whole frames for two-frame input"
        emitted_frame_size = len(emitted[0])
        assert all(len(f) == emitted_frame_size for f in emitted)

        # The load-bearing contract: capture output size == validator required size.
        assert emitted_frame_size == validator_required == expected
