"""AudioBackend protocol and implementations.

Defines the abstract ``AudioBackend`` interface for discovering devices and
opening RX/TX audio streams, plus two concrete implementations:

- **PortAudioBackend** — wraps *sounddevice* + *numpy* (requires ``[bridge]``
  extras).
- **FakeAudioBackend** — deterministic, dependency-free backend for tests.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, NewType, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_TX_BUFFER_MS = 1_000

# ---------------------------------------------------------------------------
# Identifiers & descriptors
# ---------------------------------------------------------------------------

AudioDeviceId = NewType("AudioDeviceId", int)
"""Opaque device identifier (maps to a host-API device index)."""

_ALSA_HW_RE = re.compile(r"(?:plug)?(hw:\d+,\d+)")


def _platform_uid_from_device_name(name: str) -> str:
    match = _ALSA_HW_RE.search(name)
    if match is None:
        return ""
    return match.group(1)


@dataclass(frozen=True, slots=True)
class AudioDeviceInfo:
    """Normalized audio device descriptor returned by a backend."""

    id: AudioDeviceId
    name: str
    input_channels: int = 0
    output_channels: int = 0
    default_samplerate: int = 48_000
    is_default_input: bool = False
    is_default_output: bool = False
    platform_uid: str = ""

    @property
    def supports_rx(self) -> bool:
        return self.input_channels > 0

    @property
    def supports_tx(self) -> bool:
        return self.output_channels > 0

    @property
    def duplex(self) -> bool:
        return self.supports_rx and self.supports_tx


@dataclass(frozen=True, slots=True)
class TxStreamHealth:
    """Snapshot of writable stream queue and backend write health."""

    queued_frames: int = 0
    frames_queued: int = 0
    frames_dropped: int = 0
    write_attempts: int = 0
    writes_completed: int = 0
    write_failures: int = 0
    queued_audio_ms: float = 0.0
    buffered_audio_ms: float = 0.0
    consumed_audio_ms: float = 0.0
    written_audio_ms: float = 0.0
    dropped_audio_ms: float = 0.0
    overrun_audio_ms: float = 0.0
    overrun_events: int = 0
    underrun_audio_ms: float = 0.0
    underrun_events: int = 0
    callback_errors: int = 0
    callback_status_flags: dict[str, int] = field(default_factory=dict)
    write_calls_per_sec_ewma: float | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "queued_frames": self.queued_frames,
            "frames_queued": self.frames_queued,
            "frames_dropped": self.frames_dropped,
            "write_attempts": self.write_attempts,
            "writes_completed": self.writes_completed,
            "write_failures": self.write_failures,
            "queued_audio_ms": self.queued_audio_ms,
            "buffered_audio_ms": self.buffered_audio_ms,
            "consumed_audio_ms": self.consumed_audio_ms,
            "written_audio_ms": self.written_audio_ms,
            "dropped_audio_ms": self.dropped_audio_ms,
            "overrun_audio_ms": self.overrun_audio_ms,
            "overrun_events": self.overrun_events,
            "underrun_audio_ms": self.underrun_audio_ms,
            "underrun_events": self.underrun_events,
            "callback_errors": self.callback_errors,
            "callback_status_flags": dict(self.callback_status_flags),
            "write_calls_per_sec_ewma": self.write_calls_per_sec_ewma,
            "last_error": self.last_error,
        }


# ---------------------------------------------------------------------------
# Stream protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class RxStream(Protocol):
    """Readable audio capture stream."""

    @property
    def running(self) -> bool: ...

    async def start(self, callback: Callable[[bytes], None]) -> None:
        """Begin capture; deliver PCM s16le frames to *callback*."""
        ...

    async def stop(self) -> None: ...


@runtime_checkable
class TxStream(Protocol):
    """Writable audio playback stream."""

    @property
    def running(self) -> bool: ...

    @property
    def write_health(self) -> TxStreamHealth: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def write(self, frame: bytes) -> None:
        """Queue one PCM s16le frame for playback."""
        ...


@runtime_checkable
class DuplexStream(Protocol):
    """Bidirectional audio stream — RX capture and TX playback on ONE device.

    Backed by a single PortAudio full-duplex stream so a USB-CODEC radio can
    transmit (computer audio → radio) while RX capture keeps running on the
    same device, without the two-separate-stream macOS CoreAudio AUHAL ``-50``
    (MOR-531). Combines the :class:`RxStream` consumer contract (``start``
    registers a PCM s16le frame callback) with the :class:`TxStream` producer
    contract (``write`` queues a playback frame).
    """

    @property
    def running(self) -> bool: ...

    @property
    def write_health(self) -> TxStreamHealth: ...

    async def start(self, callback: Callable[[bytes], None]) -> None:
        """Begin duplex I/O; deliver captured PCM frames to *callback*."""
        ...

    async def stop(self) -> None: ...

    async def write(self, frame: bytes) -> None:
        """Queue one PCM s16le frame for playback."""
        ...


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AudioBackend(Protocol):
    """Abstract audio backend capable of listing devices and opening streams.

    **Tier 2 — Best-effort.** Import path:
    ``from rigplane.audio.backend import AudioBackend`` (also lazily exposed
    on the top-level ``rigplane`` package via PEP 562 ``__getattr__``).

    The contract is the four methods declared below: :meth:`list_devices`,
    :meth:`check_sample_rate`, :meth:`open_rx`, :meth:`open_tx`. Streams
    returned by ``open_rx`` / ``open_tx`` follow the :class:`RxStream` and
    :class:`TxStream` protocols.

    Stability: breaking changes require a CHANGELOG note plus a minor version
    bump per ``docs/api/public-api-surface.md``. No strict semver guarantee.
    """

    def list_devices(self) -> list[AudioDeviceInfo]: ...

    def check_sample_rate(
        self,
        device: AudioDeviceId,
        sample_rate: int,
        *,
        direction: str = "rx",
    ) -> bool:
        """Check whether *device* supports *sample_rate* for the given direction.

        Args:
            device: Target device id.
            sample_rate: Desired rate in Hz.
            direction: ``"rx"`` (capture) or ``"tx"`` (playback).

        Returns:
            ``True`` if the rate is supported, ``False`` otherwise.
        """
        ...

    def open_rx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        deliver_channels: int | None = None,
        rx_audio_channel: str = "mix",
    ) -> RxStream: ...

    def open_tx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> TxStream: ...

    def open_duplex(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        deliver_channels: int | None = None,
        rx_audio_channel: str = "mix",
        tx_channels: int | None = None,
    ) -> DuplexStream:
        """Open a single full-duplex RX+TX stream on one *device* (MOR-531).

        Additive: ``open_rx`` / ``open_tx`` are unchanged. ``channels`` is the
        RX-leg native open count; ``deliver_channels`` is the RX-delivered count
        (the duplex stream software-downmixes when delivering fewer than it
        opens, identically to ``open_rx``). ``tx_channels`` is the TX-leg channel
        count (defaults to ``channels``) — a ``sd.Stream`` accepts an
        ``(in, out)`` channel pair, so RX and TX legs need not match.
        """
        ...


# ---------------------------------------------------------------------------
# PortAudioBackend (sounddevice)
# ---------------------------------------------------------------------------

_DEPENDENCY_HINT = (
    "PortAudioBackend requires optional dependencies sounddevice and numpy. "
    "Install with: pip install rigplane[bridge]"
)


def _ensure_portaudio_deps() -> tuple[Any, Any]:
    """Return ``(sounddevice, numpy)`` or raise :class:`ImportError`."""
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise ImportError(_DEPENDENCY_HINT) from exc
    try:
        import numpy as np
    except ImportError as exc:
        raise ImportError(_DEPENDENCY_HINT) from exc
    return sd, np


def _downmix_stereo_to_mono_s16le(pcm: bytes, *, channel: str = "mix") -> bytes:
    """Collapse L+R interleaved s16le → mono s16le via the selected ``channel``.

    Mirrors ``rigplane.audio.bridge._downmix_stereo_to_mono`` but is dependency-
    free (stdlib ``array``) so it can run on the PortAudio audio thread without
    importing numpy. Used by :class:`_PortAudioRxStream` when a stereo-native
    device is opened at 2 channels but the consumer wants mono (MOR-504):
    passing interleaved stereo straight through would halve the effective
    sample-rate and compress the spectrum 2x — the same bug the bridge downmix
    guards (issue #1381). Trailing bytes that do not form a whole L/R sample
    pair are dropped (an incomplete frame cannot satisfy the fixed contract).

    ``channel`` picks how each L/R pair collapses to one mono sample (MOR-508):

    - ``"mix"`` (default) — the per-pair average ``(L + R) // 2``. Correct for a
      dual-mono source (``L == R``); the historical behavior preserved verbatim
      for every rig that does not opt out, so IC-7610/X6200 etc. are unchanged.
    - ``"left"`` — take L at FULL level. The FTX-1 presents its USB RX audio on
      the LEFT channel only (R is the silent noise floor); ``mix`` averaging the
      live L with a silent R halves the level (−6 dB → quiet audio + low FFT
      scope). Selecting L delivers it undivided.
    - ``"right"`` — take R at FULL level (the mirror case).

    Single-channel selection cannot clip: it copies one existing s16 sample
    per pair, so the output stays in range with no widening/averaging.
    """
    from array import array

    usable = len(pcm) - (len(pcm) % 4)  # whole L/R pairs (2 ch × 2 bytes)
    if usable <= 0:
        return b""
    samples = array("h")
    samples.frombytes(pcm[:usable])
    import sys

    if sys.byteorder != "little":
        samples.byteswap()
    mono = array("h", bytes(len(samples)))  # len(samples)//2 mono samples
    if channel == "left":
        for i in range(0, len(samples), 2):
            mono[i // 2] = samples[i]
    elif channel == "right":
        for i in range(0, len(samples), 2):
            mono[i // 2] = samples[i + 1]
    else:  # "mix" — per-pair average (default; legacy behavior)
        for i in range(0, len(samples), 2):
            # Average in a wider int to avoid s16 overflow before truncation.
            mono[i // 2] = (samples[i] + samples[i + 1]) // 2
    if sys.byteorder != "little":
        mono.byteswap()
    return mono.tobytes()


class _RxFramer:
    """Shared RX data-path: downmix → accumulate → re-chunk into fixed frames.

    Factored out of :class:`_PortAudioRxStream` so the duplex stream
    (:class:`_PortAudioDuplexStream`) can reuse the *exact* same capture
    semantics — the stereo→mono ``rx_audio_channel`` downmix (MOR-504/508) plus
    the lossless re-chunking of engine-native variable-size blocks into fixed
    ``frame_ms`` frames (the downstream PCM-TX validator rejects any other size).
    The owning stream is the single audio-thread writer, so no lock is needed;
    the consumer ``callback`` is contractually cheap and thread-safe.
    """

    def __init__(
        self,
        *,
        channels: int,
        deliver_channels: int,
        sample_rate: int,
        frame_ms: int,
        rx_audio_channel: str,
    ) -> None:
        self._rx_audio_channel = rx_audio_channel
        # Software downmix only fires for the stereo-native → mono case. Any
        # other deliver/open relationship passes interleaved PCM through
        # unchanged (over-request already clamps open == deliver upstream).
        self._downmix_stereo_to_mono = channels == 2 and deliver_channels == 1
        frame_samples = (sample_rate * frame_ms) // 1000
        self._frame_bytes = max(1, frame_samples * deliver_channels * 2)
        self._accumulator = bytearray()

    @property
    def frame_bytes(self) -> int:
        return self._frame_bytes

    def reset(self) -> None:
        self._accumulator = bytearray()

    def feed(self, indata: Any, callback: Callable[[bytes], None]) -> None:
        """Downmix + re-chunk *indata*; deliver each whole frame to *callback*.

        Runs on PortAudio's audio thread: keep it non-blocking. Copies the PCM
        out of the (reused, variable-size) input buffer, optionally downmixes
        stereo→mono, accumulates, then slices whole fixed-size frames out and
        hands each to the consumer. Re-chunking a continuous callback stream is
        lossless and adds no scheduling seam.
        """
        try:
            pcm = (
                bytes(indata.tobytes()) if hasattr(indata, "tobytes") else bytes(indata)
            )
        except Exception:
            logger.warning("portaudio-rx: capture copy failed", exc_info=True)
            return
        if not pcm:
            return
        if self._downmix_stereo_to_mono:
            # Device opened at 2 ch (native) but consumer wants mono (MOR-504):
            # collapse interleaved L/R → mono BEFORE chunking, so the
            # accumulator/fixed-frame slicing operates on mono bytes and the
            # FFT scope + broadcaster receive the mono contract they expect.
            try:
                pcm = _downmix_stereo_to_mono_s16le(pcm, channel=self._rx_audio_channel)
            except Exception:
                logger.warning(
                    "portaudio-rx: stereo→mono downmix failed", exc_info=True
                )
                return
            if not pcm:
                return
        acc = self._accumulator
        acc.extend(pcm)
        frame_bytes = self._frame_bytes
        try:
            while len(acc) >= frame_bytes:
                frame = bytes(acc[:frame_bytes])
                del acc[:frame_bytes]
                callback(frame)
        except Exception:
            logger.warning("portaudio-rx: consumer callback failed", exc_info=True)


class _PortAudioRxStream:
    """RxStream backed by a callback-driven sounddevice InputStream.

    Capture is clocked by PortAudio's own audio thread via an ``InputStream``
    callback, mirroring the callback design of :class:`_PortAudioTxStream`.
    This replaces the previous fixed-``blocksize=960`` blocking-read loop
    (``stream.read(960)`` on a worker thread), whose 20 ms read straddled the
    WASAPI shared-mode engine period (~10 ms) and dropped/duplicated a sample
    run once per block, producing a ~50 Hz spectral comb on captured TX audio
    on Windows. The stream is opened with ``blocksize=0`` (engine-native
    period), mirroring a known-clean ``sd.rec`` capture. The earlier WDM-KS
    capture face rejected ``blocksize=0`` (PortAudioError -9999), but companion
    device selection now forces the WASAPI host-API face, on which
    ``blocksize=0`` opens cleanly.

    The PortAudio callback runs on the audio thread and must not block. It
    copies the captured PCM (s16le) out of the engine-native, variable-size
    block and *re-chunks* it into fixed ``frame_ms``-sized frames before
    handing them to the consumer *callback*. The downstream PCM-TX contract
    requires fixed-size frames (e.g. 20 ms = 960 samples = 1920 bytes s16le
    mono @ 48 kHz); a managed core validator rejects any frame whose byte
    length differs. Re-chunking a *continuous* callback stream is lossless and
    introduces no scheduling seam (the ~50 Hz comb came from the old
    blocking-read inter-read gap, not from re-chunking), so this preserves the
    fixed-frame contract without re-introducing the comb. Sub-frame remainders
    are buffered across callbacks; a trailing partial frame at stop is dropped.
    The consumer contract (see :class:`RxStream`) is that the callback is cheap
    and thread-safe to invoke from the audio thread; the companion bridge
    marshals onto its event loop via ``call_soon_threadsafe``.
    """

    def __init__(
        self,
        sd: Any,
        device_index: int,
        sample_rate: int,
        channels: int,
        blocksize: int,
        frame_ms: int,
        deliver_channels: int | None = None,
        rx_audio_channel: str = "mix",
    ) -> None:
        self._sd = sd
        self._device_index = device_index
        self._sample_rate = sample_rate
        # ``channels`` is the OS open count; ``_deliver_channels`` is what the
        # consumer receives. When deliver < open (mono request on a stereo-
        # native device, MOR-504) the framer downmixes interleaved s16le to
        # the deliver count before chunking. Default: deliver == open.
        self._channels = channels
        self._deliver_channels = (
            channels if deliver_channels is None else deliver_channels
        )
        # Capture is callback-driven with blocksize=0 (engine-native period),
        # mirroring a clean sd.rec. blocksize=0 was previously rejected by the
        # WDM-KS capture face (PortAudioError -9999), but companion device
        # selection now forces the WASAPI face, on which blocksize=0 opens.
        self._blocksize = blocksize
        # The framer owns the downmix + fixed-frame re-chunking (shared with the
        # duplex stream). It carries the sub-frame remainder between audio-thread
        # callbacks; the audio thread is the only writer, so no lock is needed.
        self._framer = _RxFramer(
            channels=self._channels,
            deliver_channels=self._deliver_channels,
            sample_rate=sample_rate,
            frame_ms=frame_ms,
            rx_audio_channel=rx_audio_channel,
        )
        self._stream: Any = None
        self._running = False
        self._callback: Callable[[bytes], None] | None = None

    @property
    def running(self) -> bool:
        return self._running

    async def start(self, callback: Callable[[bytes], None]) -> None:
        if self.running:
            raise RuntimeError("RX stream already running.")
        self._callback = callback
        self._framer.reset()
        # blocksize=0 (engine-native period) mirrors a clean sd.rec on the
        # WASAPI face. latency="low" matches the (clean) output stream.
        self._stream = self._sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            device=self._device_index,
            blocksize=self._blocksize,
            latency="low",
            callback=self._input_callback,
        )
        self._stream.start()
        self._running = True

    async def stop(self) -> None:
        stream = self._stream
        self._stream = None
        self._running = False
        self._callback = None
        # Drop any sub-frame remainder: an incomplete trailing frame cannot
        # satisfy the fixed-size contract and is discarded at teardown.
        self._framer.reset()
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                logger.debug("portaudio-rx: stream stop failed", exc_info=True)
            try:
                stream.close()
            except Exception:
                logger.debug("portaudio-rx: stream close failed", exc_info=True)

    def _input_callback(
        self,
        indata: Any,
        _frames: int,
        _time_info: Any,
        _status: Any,
    ) -> None:
        # Runs on PortAudio's audio thread: keep it non-blocking. The shared
        # framer copies the captured PCM out of the (reused, variable-size) input
        # buffer, optionally downmixes stereo→mono, accumulates, and delivers
        # whole fixed-size frames to the consumer. Re-chunking a continuous
        # callback stream is lossless and adds no scheduling seam, so it keeps
        # the fixed-frame contract without re-introducing the comb. The consumer
        # is contractually cheap/thread-safe.
        cb = self._callback
        if cb is None:
            return
        self._framer.feed(indata, cb)


class _PortAudioTxStream:
    """TxStream backed by a sounddevice OutputStream callback.

    The bounded callback ring decouples async producers from PortAudio's
    hardware-clocked consumer: ``write()`` enqueues interleaved PCM and returns
    quickly, and the callback drains the ring at the playback cadence.
    """

    def __init__(
        self,
        sd: Any,
        _np: Any,
        device_index: int,
        sample_rate: int,
        channels: int,
        blocksize: int,
    ) -> None:
        self._sd = sd
        self._device_index = device_index
        self._sample_rate = sample_rate
        self._channels = channels
        self._blocksize = blocksize
        self._bytes_per_audio_frame = max(1, channels * 2)
        self._capacity_bytes = self._buffer_capacity_bytes()
        self._stream: Any = None
        self._running = False
        self._lock = threading.Lock()
        self._buffer = bytearray(self._capacity_bytes)
        self._read_pos = 0
        self._write_pos = 0
        self._buffered_bytes: int = 0
        self._frame_lengths: deque[int] = deque()
        self._dropped_frames: int = 0
        self._frames_queued: int = 0
        self._write_attempts: int = 0
        self._writes_completed: int = 0
        self._write_failures: int = 0
        self._queued_audio_ms: float = 0.0
        self._consumed_audio_ms: float = 0.0
        self._written_audio_ms: float = 0.0
        self._dropped_audio_ms: float = 0.0
        self._overrun_audio_ms: float = 0.0
        self._overrun_events: int = 0
        self._underrun_audio_ms: float = 0.0
        self._underrun_events: int = 0
        self._callback_errors: int = 0
        self._callback_status_flags: dict[str, int] = {}
        self._write_calls_per_sec_ewma: float | None = None
        self._last_write_rate_at: float | None = None
        self._last_write_rate_total: int = 0
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def write_health(self) -> TxStreamHealth:
        with self._lock:
            buffered_audio_ms = self._audio_ms_for_bytes(self._buffered_bytes)
            return TxStreamHealth(
                queued_frames=len(self._frame_lengths),
                frames_queued=self._frames_queued,
                frames_dropped=self._dropped_frames,
                write_attempts=self._write_attempts,
                writes_completed=self._writes_completed,
                write_failures=self._write_failures,
                queued_audio_ms=round(self._queued_audio_ms, 3),
                buffered_audio_ms=round(buffered_audio_ms, 3),
                consumed_audio_ms=round(self._consumed_audio_ms, 3),
                written_audio_ms=round(self._written_audio_ms, 3),
                dropped_audio_ms=round(self._dropped_audio_ms, 3),
                overrun_audio_ms=round(self._overrun_audio_ms, 3),
                overrun_events=self._overrun_events,
                underrun_audio_ms=round(self._underrun_audio_ms, 3),
                underrun_events=self._underrun_events,
                callback_errors=self._callback_errors,
                callback_status_flags=dict(self._callback_status_flags),
                write_calls_per_sec_ewma=(
                    round(self._write_calls_per_sec_ewma, 3)
                    if self._write_calls_per_sec_ewma is not None
                    else None
                ),
                last_error=self._last_error,
            )

    async def start(self) -> None:
        if self.running:
            raise RuntimeError("TX stream already running.")
        self._stream = self._sd.OutputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            device=self._device_index,
            blocksize=self._blocksize,
            latency="low",
            callback=self._output_callback,
        )
        self._stream.start()
        self._running = True

    async def stop(self) -> None:
        stream = self._stream
        self._stream = None
        self._running = False
        self._close_stream(stream)
        self._clear_buffer()

    async def write(self, frame: bytes) -> None:
        if not self.running:
            detail = (
                f" Last writer error: {self._last_error}."
                if self._last_error is not None
                else ""
            )
            raise RuntimeError(f"TX stream is not running.{detail}")
        if not frame:
            return

        data = bytes(frame)
        if not data:
            return

        # Keep producer writes cheap: enqueue bytes into the bounded ring and
        # let the callback clock playback. On overflow, discard oldest queued
        # audio first so latency cannot grow without bound.
        incoming_drop_bytes = 0
        if len(data) > self._capacity_bytes:
            incoming_drop_bytes = self._align_byte_count(
                len(data) - self._capacity_bytes,
                round_up=True,
            )
            data = data[incoming_drop_bytes:]

        dropped_bytes = incoming_drop_bytes
        with self._lock:
            overflowed = self._buffered_bytes + len(data) > self._capacity_bytes
            while (
                self._buffered_bytes + len(data) > self._capacity_bytes
                and self._frame_lengths
            ):
                dropped_bytes += self._drop_oldest_locked(self._frame_lengths[0])
            remaining_overflow = max(
                0,
                self._buffered_bytes + len(data) - self._capacity_bytes,
            )
            if remaining_overflow:
                drop_bytes = self._align_byte_count(remaining_overflow, round_up=True)
                dropped_bytes += self._drop_oldest_locked(drop_bytes)
            if incoming_drop_bytes or overflowed:
                self._overrun_events += 1
            if dropped_bytes:
                dropped_audio_ms = self._audio_ms_for_bytes(dropped_bytes)
                self._dropped_audio_ms += dropped_audio_ms
                self._overrun_audio_ms += dropped_audio_ms

            self._write_ring_locked(data)
            self._frame_lengths.append(len(data))
            self._frames_queued += 1
            self._queued_audio_ms += self._audio_ms_for_bytes(len(data))

            log_drops = dropped_bytes and (
                self._dropped_frames <= 3 or self._dropped_frames % 500 == 0
            )
            total_dropped = self._dropped_frames

        if log_drops:
            logger.warning(
                "portaudio-tx: dropped stale playback frame (buffer full, total=%d)",
                total_dropped,
            )

    def _output_callback(
        self,
        outdata: Any,
        frames: int,
        _time_info: Any,
        status: Any,
    ) -> None:
        byte_count = frames * self._bytes_per_audio_frame
        self._record_callback_attempt(status)
        try:
            # Callback hot path: no logging or I/O. Copy from the ring; if the
            # producer falls behind, fill the remainder with silence and record
            # an underrun.
            dest = memoryview(outdata).cast("B")
            if len(dest) != byte_count:
                raise ValueError(
                    f"output buffer is {len(dest)} bytes, expected {byte_count}"
                )
            consumed_bytes = self._read_ring_into_locked(dest)
            if consumed_bytes < byte_count:
                underrun_bytes = byte_count - consumed_bytes
                dest[consumed_bytes:] = b"\x00" * underrun_bytes
                self._record_underrun(underrun_bytes)
            self._record_callback_complete(
                consumed_bytes=consumed_bytes,
                output_bytes=byte_count,
            )
        except Exception as exc:
            self._record_callback_failure(exc)
            self._fill_output_silence(outdata, byte_count)

    def _record_callback_attempt(self, status: Any) -> None:
        flags = self._status_flag_names(status)
        with self._lock:
            self._write_attempts += 1
            for flag in flags:
                self._callback_status_flags[flag] = (
                    self._callback_status_flags.get(flag, 0) + 1
                )

    def _record_callback_complete(
        self,
        *,
        consumed_bytes: int,
        output_bytes: int,
    ) -> None:
        with self._lock:
            self._writes_completed += 1
            self._consumed_audio_ms += self._audio_ms_for_bytes(consumed_bytes)
            self._written_audio_ms += self._audio_ms_for_bytes(output_bytes)
            self._track_write_rate_locked()

    def _record_callback_failure(self, exc: Exception) -> None:
        with self._lock:
            self._callback_errors += 1
            self._write_failures += 1
            self._last_error = f"{type(exc).__name__}: {exc}"

    def _record_underrun(self, byte_count: int) -> None:
        with self._lock:
            self._underrun_audio_ms += self._audio_ms_for_bytes(byte_count)
            self._underrun_events += 1

    def _read_ring_into_locked(self, dest: memoryview) -> int:
        requested = len(dest)
        with self._lock:
            copied = min(requested, self._buffered_bytes)
            first = min(copied, self._capacity_bytes - self._read_pos)
            if first:
                dest[:first] = self._buffer[self._read_pos : self._read_pos + first]
            second = copied - first
            if second:
                dest[first : first + second] = self._buffer[:second]
            self._read_pos = (self._read_pos + copied) % self._capacity_bytes
            self._buffered_bytes -= copied
            self._consume_frame_lengths_locked(copied, count_dropped=False)
            if self._buffered_bytes == 0:
                self._read_pos = self._write_pos
        return copied

    def _write_ring_locked(self, data: bytes) -> None:
        first = min(len(data), self._capacity_bytes - self._write_pos)
        if first:
            self._buffer[self._write_pos : self._write_pos + first] = data[:first]
        second = len(data) - first
        if second:
            self._buffer[:second] = data[first:]
        self._write_pos = (self._write_pos + len(data)) % self._capacity_bytes
        self._buffered_bytes += len(data)

    def _drop_oldest_locked(self, byte_count: int) -> int:
        dropped = min(byte_count, self._buffered_bytes)
        if dropped <= 0:
            return 0
        self._read_pos = (self._read_pos + dropped) % self._capacity_bytes
        self._buffered_bytes -= dropped
        self._consume_frame_lengths_locked(dropped, count_dropped=True)
        if self._buffered_bytes == 0:
            self._read_pos = self._write_pos
        return dropped

    def _consume_frame_lengths_locked(
        self,
        byte_count: int,
        *,
        count_dropped: bool,
    ) -> None:
        remaining = byte_count
        while remaining > 0 and self._frame_lengths:
            frame_len = self._frame_lengths[0]
            if frame_len <= remaining:
                remaining -= frame_len
                self._frame_lengths.popleft()
                if count_dropped:
                    self._dropped_frames += 1
            else:
                self._frame_lengths[0] = frame_len - remaining
                remaining = 0

    def _buffer_capacity_bytes(self) -> int:
        frames = (self._sample_rate * _TX_BUFFER_MS) // 1000
        return max(1, frames) * self._bytes_per_audio_frame

    def _clear_buffer(self) -> None:
        with self._lock:
            self._buffer[:] = b"\x00" * self._capacity_bytes
            self._read_pos = 0
            self._write_pos = 0
            self._buffered_bytes = 0
            self._frame_lengths.clear()

    def _audio_ms_for_bytes(self, byte_count: int) -> float:
        frames = byte_count // self._bytes_per_audio_frame
        return (frames / self._sample_rate) * 1000.0

    def _align_byte_count(self, byte_count: int, *, round_up: bool) -> int:
        remainder = byte_count % self._bytes_per_audio_frame
        if remainder == 0:
            return byte_count
        if round_up:
            return byte_count + self._bytes_per_audio_frame - remainder
        return byte_count - remainder

    def _fill_output_silence(self, outdata: Any, byte_count: int) -> None:
        try:
            dest = memoryview(outdata).cast("B")
            dest[: min(byte_count, len(dest))] = b"\x00" * min(byte_count, len(dest))
            return
        except Exception:
            pass
        try:
            outdata.fill(0)
        except Exception:
            pass

    def _status_flag_names(self, status: Any) -> tuple[str, ...]:
        if status is None:
            return ()
        try:
            if not bool(status):
                return ()
        except Exception:
            pass
        flags: list[str] = []
        for name in (
            "input_underflow",
            "input_overflow",
            "output_underflow",
            "output_overflow",
            "priming_output",
        ):
            try:
                if bool(getattr(status, name, False)):
                    flags.append(name)
            except Exception:
                continue
        if flags:
            return tuple(flags)
        try:
            label = str(status).strip()
        except Exception:
            label = type(status).__name__
        return (label or type(status).__name__,)

    def _track_write_rate_locked(self) -> None:
        observed_at = time.monotonic()
        if self._last_write_rate_at is None:
            self._last_write_rate_at = observed_at
            self._last_write_rate_total = self._writes_completed
            self._write_calls_per_sec_ewma = 0.0
            return

        dt = observed_at - self._last_write_rate_at
        if dt <= 0:
            return
        delta = self._writes_completed - self._last_write_rate_total
        instant_rate = delta / dt
        alpha = 1.0 - math.exp(-dt / 5.0)
        previous = self._write_calls_per_sec_ewma or 0.0
        self._write_calls_per_sec_ewma = previous + alpha * (instant_rate - previous)
        self._last_write_rate_at = observed_at
        self._last_write_rate_total = self._writes_completed

    def _close_stream(self, stream: Any) -> None:
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            logger.debug("portaudio-tx: stream stop failed", exc_info=True)
        try:
            stream.close()
        except Exception:
            logger.debug("portaudio-tx: stream close failed", exc_info=True)


class _PortAudioDuplexStream:
    """Full-duplex stream backed by ONE callback-driven sounddevice ``Stream``.

    Wraps a single ``sd.Stream(device=(idx, idx), channels=(open, open), ...)``
    so a USB-CODEC radio can do digital-mode TX (computer audio → radio via USB
    MOD) WHILE RX capture (browser audio + FFT scope) keeps running — without
    the macOS CoreAudio AUHAL ``-50`` that two separate streams
    (``sd.InputStream`` + ``sd.OutputStream``) cause on one C-Media device
    (MOR-531).

    Reuses the existing data paths rather than duplicating them:

    - **RX leg** delegates to the shared :class:`_RxFramer` — the *same*
      ``rx_audio_channel`` downmix (MOR-504/508) and lossless re-chunking into
      fixed ``frame_ms`` frames that :class:`_PortAudioRxStream` uses. Delivered
      frames are marshalled to the consumer exactly as the RX stream is (the
      consumer is contractually cheap/thread-safe; the bridge marshals onto its
      event loop via ``call_soon_threadsafe``).
    - **TX leg** delegates to an embedded :class:`_PortAudioTxStream` — the
      *same* bounded ring + ``write()`` producer and ring-drain consumer. The
      duplex callback fills ``outdata`` by invoking the TX stream's output
      callback (silence when the queue is empty), and ``write_health`` proxies
      the embedded TX stream so diagnostics are identical to the split path.
    """

    def __init__(
        self,
        sd: Any,
        np: Any,
        device_index: int,
        sample_rate: int,
        channels: int,
        blocksize: int,
        frame_ms: int,
        deliver_channels: int | None = None,
        rx_audio_channel: str = "mix",
        tx_channels: int | None = None,
    ) -> None:
        self._sd = sd
        self._device_index = device_index
        self._sample_rate = sample_rate
        # RX leg opens at the native ``channels`` count and delivers
        # ``deliver_channels`` (downmix when fewer, MOR-504). The TX leg opens at
        # its own ``tx_channels`` count (the mono playback the radio's USB MOD
        # consumes); ``sd.Stream`` accepts a ``(in, out)`` channel pair so the
        # two legs need not match.
        self._channels = channels
        self._deliver_channels = (
            channels if deliver_channels is None else deliver_channels
        )
        self._tx_channels = channels if tx_channels is None else tx_channels
        self._blocksize = blocksize
        self._framer = _RxFramer(
            channels=self._channels,
            deliver_channels=self._deliver_channels,
            sample_rate=sample_rate,
            frame_ms=frame_ms,
            rx_audio_channel=rx_audio_channel,
        )
        # Embed a TX stream purely for its ring + write()/output-callback; its
        # own ``sd`` stream is never opened (we drive its ``_output_callback``
        # from the duplex callback). It opens at ``tx_channels`` so the ring's
        # frame accounting and ``outdata`` interleaving match the TX leg.
        self._tx = _PortAudioTxStream(
            sd,
            np,
            device_index=device_index,
            sample_rate=sample_rate,
            channels=self._tx_channels,
            blocksize=blocksize,
        )
        self._stream: Any = None
        self._running = False
        self._callback: Callable[[bytes], None] | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def write_health(self) -> TxStreamHealth:
        return self._tx.write_health

    async def start(self, callback: Callable[[bytes], None]) -> None:
        if self.running:
            raise RuntimeError("Duplex stream already running.")
        self._callback = callback
        self._framer.reset()
        # The embedded TX stream's ring must be marked running so ``write()``
        # accepts frames; its OWN sounddevice stream stays unopened — the duplex
        # callback drives playback.
        self._tx._running = True
        # ONE full-duplex stream: device=(idx, idx), channels=(open, open).
        # blocksize=0 (engine-native period) mirrors a clean sd.rec/playback.
        self._stream = self._sd.Stream(
            samplerate=self._sample_rate,
            channels=(self._channels, self._tx_channels),
            dtype="int16",
            device=(self._device_index, self._device_index),
            blocksize=self._blocksize,
            latency="low",
            callback=self._duplex_callback,
        )
        self._stream.start()
        self._running = True

    async def stop(self) -> None:
        stream = self._stream
        self._stream = None
        self._running = False
        self._callback = None
        self._framer.reset()
        self._tx._running = False
        self._tx._clear_buffer()
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                logger.debug("portaudio-duplex: stream stop failed", exc_info=True)
            try:
                stream.close()
            except Exception:
                logger.debug("portaudio-duplex: stream close failed", exc_info=True)

    async def write(self, frame: bytes) -> None:
        """Queue one PCM s16le playback frame (TX leg, via the shared ring)."""
        if not self.running:
            raise RuntimeError("Duplex stream is not running.")
        await self._tx.write(frame)

    def _duplex_callback(
        self,
        indata: Any,
        outdata: Any,
        frames: int,
        _time_info: Any,
        status: Any,
    ) -> None:
        # ONE audio-thread callback handles BOTH directions:
        #  (a) RX fan — hand captured ``indata`` to the shared framer, which
        #      downmixes + re-chunks and delivers fixed frames to the consumer;
        #  (b) TX pull — fill ``outdata`` from the embedded TX ring (silence when
        #      the producer is behind), reusing the TX stream's output callback.
        cb = self._callback
        if cb is not None:
            self._framer.feed(indata, cb)
        self._tx._output_callback(outdata, frames, _time_info, status)


class PortAudioBackend:
    """AudioBackend backed by PortAudio via *sounddevice*.

    **Tier 2 — Best-effort.** Import path:
    ``from rigplane.audio.backend import PortAudioBackend``. Implements the
    :class:`AudioBackend` protocol on top of the optional ``[bridge]`` extras
    (``sounddevice`` + ``numpy``); dependencies are loaded lazily on first
    method call so importing this class does not require PortAudio at import
    time.

    Stability: breaking changes require a CHANGELOG note plus a minor version
    bump per ``docs/api/public-api-surface.md``. No strict semver guarantee.
    """

    def __init__(
        self,
        *,
        dependency_loader: Callable[[], tuple[Any, Any]] | None = None,
    ) -> None:
        self._sd: Any = None
        self._np: Any = None
        self._dependency_loader = dependency_loader

    @property
    def sounddevice_module(self) -> Any | None:
        """Return the underlying sounddevice module, or None if not loaded."""
        try:
            sd, _ = self._ensure_deps()
            return sd
        except ImportError:
            return None

    def _ensure_deps(self) -> tuple[Any, Any]:
        if self._sd is not None and self._np is not None:
            return self._sd, self._np
        if self._dependency_loader is not None:
            try:
                self._sd, self._np = self._dependency_loader()
            except ImportError as exc:
                raise ImportError(_DEPENDENCY_HINT) from exc
        else:
            self._sd, self._np = _ensure_portaudio_deps()
        return self._sd, self._np

    def list_devices(self) -> list[AudioDeviceInfo]:
        sd, _ = self._ensure_deps()
        raw_devices = list(sd.query_devices())
        default_raw = getattr(getattr(sd, "default", None), "device", None)
        default_in: int | None = None
        default_out: int | None = None
        if isinstance(default_raw, (list, tuple)) and len(default_raw) >= 2:
            default_in = int(default_raw[0]) if default_raw[0] is not None else None
            default_out = int(default_raw[1]) if default_raw[1] is not None else None

        result: list[AudioDeviceInfo] = []
        for idx, raw in enumerate(raw_devices):
            dev_idx = int(raw.get("index", idx))
            result.append(
                AudioDeviceInfo(
                    id=AudioDeviceId(dev_idx),
                    name=str(raw.get("name", f"device-{dev_idx}")),
                    input_channels=int(raw.get("max_input_channels", 0)),
                    output_channels=int(raw.get("max_output_channels", 0)),
                    default_samplerate=int(raw.get("default_samplerate", 48_000)),
                    is_default_input=(default_in is not None and dev_idx == default_in),
                    is_default_output=(
                        default_out is not None and dev_idx == default_out
                    ),
                    platform_uid=_platform_uid_from_device_name(
                        str(raw.get("name", f"device-{dev_idx}"))
                    ),
                )
            )
        return result

    def check_sample_rate(
        self,
        device: AudioDeviceId,
        sample_rate: int,
        *,
        direction: str = "rx",
    ) -> bool:
        sd, _ = self._ensure_deps()
        idx = int(device)
        try:
            if direction == "rx":
                sd.check_input_settings(device=idx, samplerate=sample_rate)
            else:
                sd.check_output_settings(device=idx, samplerate=sample_rate)
            return True
        except Exception:
            return False

    def open_rx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        deliver_channels: int | None = None,
        rx_audio_channel: str = "mix",
    ) -> RxStream:
        sd, _ = self._ensure_deps()
        # blocksize=0 lets PortAudio use the engine-native period, mirroring a
        # clean ``sd.rec`` capture. This is safe now that companion device
        # selection forces the WASAPI host-API face (the WDM-KS face that
        # rejected blocksize=0 with PortAudioError -9999 is no longer chosen).
        # The PortAudio capture period stays engine-native; frame_ms only sizes
        # the fixed frames re-chunked out to the consumer callback.
        #
        # ``channels`` is the OS open count; ``deliver_channels`` is what the
        # consumer receives. When ``deliver_channels`` < ``channels`` (mono
        # request on a stereo-native device, MOR-504) the stream software-
        # downmixes before chunking, so the consumer still sees the mono
        # fixed-frame contract while CoreAudio/AUHAL opens the device natively.
        return _PortAudioRxStream(
            sd,
            device_index=int(device),
            sample_rate=sample_rate,
            channels=channels,
            blocksize=0,
            frame_ms=frame_ms,
            deliver_channels=deliver_channels,
            rx_audio_channel=rx_audio_channel,
        )

    def open_tx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> TxStream:
        sd, np = self._ensure_deps()
        blocksize = (sample_rate * frame_ms) // 1000
        return _PortAudioTxStream(
            sd,
            np,
            device_index=int(device),
            sample_rate=sample_rate,
            channels=channels,
            blocksize=blocksize,
        )

    def open_duplex(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        deliver_channels: int | None = None,
        rx_audio_channel: str = "mix",
        tx_channels: int | None = None,
    ) -> DuplexStream:
        sd, np = self._ensure_deps()
        # TX uses a fixed blocksize (frame_ms) so the duplex callback's
        # ``outdata`` window matches a whole playback frame, mirroring open_tx.
        blocksize = (sample_rate * frame_ms) // 1000
        return _PortAudioDuplexStream(
            sd,
            np,
            device_index=int(device),
            sample_rate=sample_rate,
            channels=channels,
            blocksize=blocksize,
            frame_ms=frame_ms,
            deliver_channels=deliver_channels,
            rx_audio_channel=rx_audio_channel,
            tx_channels=tx_channels,
        )


# ---------------------------------------------------------------------------
# FakeAudioBackend (for tests)
# ---------------------------------------------------------------------------


class FakeRxStream:
    """Test double: records lifecycle and delivers injected frames."""

    def __init__(self) -> None:
        self._running = False
        self._callback: Callable[[bytes], None] | None = None
        self.started_count = 0
        self.stopped_count = 0
        self.fail_on_inject: Exception | None = None

    @property
    def running(self) -> bool:
        return self._running

    async def start(self, callback: Callable[[bytes], None]) -> None:
        if self._running:
            raise RuntimeError("FakeRxStream already running.")
        self._callback = callback
        self._running = True
        self.started_count += 1

    async def stop(self) -> None:
        self._running = False
        self._callback = None
        self.stopped_count += 1

    def inject_frame(self, frame: bytes) -> None:
        """Push a frame to the registered callback (test helper).

        If *fail_on_inject* is set, raises it once and clears the flag.
        """
        exc = self.fail_on_inject
        if exc is not None:
            self.fail_on_inject = None
            raise exc
        if self._callback is not None:
            self._callback(frame)


class FakeTxStream:
    """Test double: records lifecycle and captures written frames."""

    def __init__(self) -> None:
        self._running = False
        self.started_count = 0
        self.stopped_count = 0
        self.written_frames: list[bytes] = []
        self.fail_on_write: OSError | None = None
        self.write_failures = 0
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def write_health(self) -> TxStreamHealth:
        return TxStreamHealth(
            queued_frames=0,
            frames_queued=len(self.written_frames),
            frames_dropped=0,
            write_attempts=len(self.written_frames) + self.write_failures,
            writes_completed=len(self.written_frames),
            write_failures=self.write_failures,
            last_error=self.last_error,
        )

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("FakeTxStream already running.")
        self._running = True
        self.started_count += 1

    async def stop(self) -> None:
        self._running = False
        self.stopped_count += 1

    async def write(self, frame: bytes) -> None:
        if not self._running:
            raise RuntimeError("FakeTxStream is not running.")
        exc = self.fail_on_write
        if exc is not None:
            self.fail_on_write = None
            self.write_failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise exc
        self.written_frames.append(frame)


class FakeDuplexStream:
    """Test double: one full-duplex stream — RX fan + TX queue (MOR-531).

    Combines :class:`FakeRxStream` (delivers injected capture frames to the
    registered callback) and :class:`FakeTxStream` (captures written playback
    frames) so the same-device duplex path can be exercised without PortAudio.
    """

    def __init__(self) -> None:
        self._running = False
        self._callback: Callable[[bytes], None] | None = None
        self.started_count = 0
        self.stopped_count = 0
        self.written_frames: list[bytes] = []

    @property
    def running(self) -> bool:
        return self._running

    @property
    def write_health(self) -> TxStreamHealth:
        return TxStreamHealth(
            frames_queued=len(self.written_frames),
            writes_completed=len(self.written_frames),
        )

    async def start(self, callback: Callable[[bytes], None]) -> None:
        if self._running:
            raise RuntimeError("FakeDuplexStream already running.")
        self._callback = callback
        self._running = True
        self.started_count += 1

    async def stop(self) -> None:
        self._running = False
        self._callback = None
        self.stopped_count += 1

    async def write(self, frame: bytes) -> None:
        if not self._running:
            raise RuntimeError("FakeDuplexStream is not running.")
        self.written_frames.append(frame)

    def inject_frame(self, frame: bytes) -> None:
        """Push an RX capture frame to the registered callback (test helper)."""
        if self._callback is not None:
            self._callback(frame)


class FakeAudioBackend:
    """Deterministic AudioBackend for tests — no real audio hardware.

    **Tier 2 — Best-effort.** Import path:
    ``from rigplane.audio.backend import FakeAudioBackend``. Implements the
    :class:`AudioBackend` protocol with in-memory :class:`FakeRxStream` /
    :class:`FakeTxStream` doubles so consumer code can be exercised without
    PortAudio or any optional extras.

    Stability: breaking changes require a CHANGELOG note plus a minor version
    bump per ``docs/api/public-api-surface.md``. No strict semver guarantee.
    """

    def __init__(
        self,
        devices: list[AudioDeviceInfo] | None = None,
        *,
        supported_sample_rates: set[int] | None = None,
    ) -> None:
        self._devices: list[AudioDeviceInfo] = devices or []
        self._supported_rates: set[int] = supported_sample_rates or {
            8_000,
            16_000,
            44_100,
            48_000,
            96_000,
        }
        self.rx_streams: list[FakeRxStream] = []
        self.tx_streams: list[FakeTxStream] = []
        self.duplex_streams: list[FakeDuplexStream] = []

    def list_devices(self) -> list[AudioDeviceInfo]:
        return list(self._devices)

    def check_sample_rate(
        self,
        device: AudioDeviceId,
        sample_rate: int,
        *,
        direction: str = "rx",
    ) -> bool:
        return sample_rate in self._supported_rates

    def add_device(self, device: AudioDeviceInfo) -> None:
        """Add a device (test helper — simulates hotplug)."""
        self._devices.append(device)

    def remove_devices(self) -> None:
        """Remove all devices (test helper — simulates device loss)."""
        self._devices.clear()

    def open_rx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        deliver_channels: int | None = None,
        rx_audio_channel: str = "mix",
    ) -> FakeRxStream:
        if not any(d.id == device for d in self._devices):
            raise ValueError(f"Unknown device id {device}")
        # ``deliver_channels`` selects the software downmix in the real
        # PortAudio stream; FakeRxStream is a verbatim pass-through, so it only
        # records what it was opened at for assertions.
        stream = FakeRxStream()
        self.rx_streams.append(stream)
        return stream

    def open_tx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> FakeTxStream:
        if not any(d.id == device for d in self._devices):
            raise ValueError(f"Unknown device id {device}")
        stream = FakeTxStream()
        self.tx_streams.append(stream)
        return stream

    def open_duplex(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        deliver_channels: int | None = None,
        rx_audio_channel: str = "mix",
        tx_channels: int | None = None,
    ) -> FakeDuplexStream:
        if not any(d.id == device for d in self._devices):
            raise ValueError(f"Unknown device id {device}")
        stream = FakeDuplexStream()
        self.duplex_streams.append(stream)
        return stream


__all__ = [
    "AudioBackend",
    "AudioDeviceId",
    "AudioDeviceInfo",
    "DuplexStream",
    "FakeAudioBackend",
    "FakeDuplexStream",
    "FakeRxStream",
    "FakeTxStream",
    "PortAudioBackend",
    "RxStream",
    "TxStream",
    "TxStreamHealth",
]
