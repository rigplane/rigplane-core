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
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Literal,
    NamedTuple,
    NewType,
    Protocol,
    runtime_checkable,
)

from ._portaudio_enum import WASAPI_HOST_API
from ._usb_holder import (
    HOLDER_LOOKUP_TIMEOUT_S,
    HolderLookup,
    find_holder_name,
    holder_exe_name,
)

logger = logging.getLogger(__name__)

_TX_BUFFER_MS = 1_000
# Playback policy per writable stream (MOR-4013), selected via ``playout=``.
# ``"adaptive"`` (the default) is the fill-target policy every RX playback
# sink uses — the bridges' radio → speaker/virtual-cable leg, including
# callers outside this repository that open a playback stream without a
# policy: a ring held at a small fill target tolerates bursty delivery
# without pinning at the 1 s capacity. ``"immediate"`` is passed explicitly
# by every host-to-radio TX caller (usb_driver): the free-running ring so a
# freshly written frame plays on the very next callback (PTT-release
# promptness), byte-identical to the pre-MOR-4013 behaviour.
# Adaptive playback stays silent until ``_TX_FILL_TARGET_MS`` is buffered
# (at least two ``frame_ms`` blocks), then holds that fill. A slow filtered
# proportional controller skews the read rate (clamped to ±3%, field drift
# is 1-2%) so a producer at 50 Hz feeding a 49.25 Hz device clock neither
# pins the ring at capacity nor drains it. Headroom on top of the target
# forms the hard enqueue cap: one burst above it trims once back to the
# target with a short fade at the splice instead of dropping a frame per
# write.
_TX_FILL_TARGET_MS = 150
_TX_OVERRUN_HEADROOM_MS = 100
_TX_DRIFT_MAX_RATIO = 0.03
_TX_DRIFT_KP = 1.0e-5
_TX_DRIFT_FILTER_S = 2.0
_TX_DRIFT_SLEW_PER_S = 0.01
_TX_DRIFT_DEADBAND_MS = 20
_TX_TRIM_FADE_MS = 5
_LINUX_ALSA_INPUT_LATENCY_S = 0.050

# ---------------------------------------------------------------------------
# Identifiers & descriptors
# ---------------------------------------------------------------------------

AudioDeviceId = NewType("AudioDeviceId", int)
"""Opaque device identifier (maps to a host-API device index)."""

TxPlayoutPolicy = Literal["adaptive", "immediate"]
"""Playback policy of a writable stream (MOR-4013).

``"adaptive"`` (the default) holds a small fill target with drift
compensation — the RX playback sinks (radio → speakers / virtual cable).
``"immediate"`` keeps the free-running ring so a freshly written frame
plays on the very next callback — the host-to-radio TX paths.
"""

CaptureMode = Literal["shared", "exclusive-preferred"]
"""How a USB radio codec is captured and played on Windows (MOR-4030).

``"shared"`` (the default) uses the host API pairing picks first (MME) and the
Windows shared audio engine, so other programs, WSJT-X pointed at the codec
included, can use the codec at the same time. The Windows engine applies the
input's effects there, and its default AI noise suppression gates non-speech
audio such as a radio's receive audio into digital silence.

``"exclusive-preferred"`` prefers the WASAPI host API for both directions and
opens the codec in WASAPI exclusive mode, which bypasses the engine and its
effects, and falls back to shared WASAPI when the exclusive open is refused.
Other platforms ignore it.
"""

CAPTURE_MODES: tuple[CaptureMode, ...] = ("shared", "exclusive-preferred")

_ALSA_HW_RE = re.compile(r"(?:plug)?(hw:\d+,\d+)")


def _platform_uid_from_device_name(name: str) -> str:
    match = _ALSA_HW_RE.search(name)
    if match is None:
        return ""
    return match.group(1)


@dataclass(frozen=True, slots=True)
class AudioDeviceConfig:
    """Per-device audio configuration carrier (MOR-578).

    Bundles the per-device audio knobs that historically travelled as six
    separate keyword parameters across every layer (``rigs/*.toml [audio]`` →
    rig loader → backend ctor → :class:`UsbAudioDriver` → ``open_rx`` →
    ``_RxFramer`` → downmix) into ONE frozen object, so adding a knob no
    longer means threading a new keyword through each hop. Fields and
    defaults mirror the historical per-keyword plumbing 1:1.

    ``channels`` keeps the exact per-layer meaning the ``channels=`` keyword
    had: the requested capture/playback channel count at the driver level,
    and the OS-open channel count for the per-stream copy the backend hands
    to its internal streams (the delivered count stays a separate
    ``deliver_channels`` value, as before — it is a derived downmix
    instruction, not device config). ``rx_device``/``tx_device`` are the
    driver-level selection overrides; they are not consulted below the
    driver, exactly like the old keywords. ``capture_mode`` (MOR-4030) is the
    Windows capture mode of a USB radio codec, see :data:`CaptureMode`; the
    driver reads it for pairing and builds its backend from it.
    """

    rx_device: str | None = None
    tx_device: str | None = None
    sample_rate: int = 48_000
    channels: int = 1
    frame_ms: int = 20
    rx_audio_channel: str = "mix"
    capture_mode: CaptureMode = "shared"


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
    enqueued_audio_ms: float = 0.0
    callback_consumed_audio_ms: float = 0.0
    callback_output_audio_ms: float = 0.0
    enqueue_overrun_audio_ms: float = 0.0
    enqueue_overrun_events: int = 0
    callback_underrun_audio_ms: float = 0.0
    callback_underrun_events: int = 0
    callback_calls_per_sec_ewma: float | None = None
    discard_events: int = 0
    discarded_audio_ms: float = 0.0
    # MOR-4013 fill-target playback state.
    refill_events: int = 0
    playback_read_ratio: float = 1.0
    fill_target_ms: float = 0.0

    def __post_init__(self) -> None:
        self._sync_alias_pair("enqueued_audio_ms", "queued_audio_ms", 0.0)
        self._sync_alias_pair("callback_consumed_audio_ms", "consumed_audio_ms", 0.0)
        self._sync_alias_pair("callback_output_audio_ms", "written_audio_ms", 0.0)
        self._sync_alias_pair("enqueue_overrun_audio_ms", "overrun_audio_ms", 0.0)
        self._sync_alias_pair("enqueue_overrun_events", "overrun_events", 0)
        self._sync_alias_pair("callback_underrun_audio_ms", "underrun_audio_ms", 0.0)
        self._sync_alias_pair("callback_underrun_events", "underrun_events", 0)
        self._sync_alias_pair(
            "callback_calls_per_sec_ewma",
            "write_calls_per_sec_ewma",
            None,
        )

    def _sync_alias_pair(
        self,
        canonical_name: str,
        legacy_name: str,
        default: float | int | None,
    ) -> None:
        canonical_value = getattr(self, canonical_name)
        legacy_value = getattr(self, legacy_name)
        if (
            canonical_value != default
            and legacy_value != default
            and canonical_value != legacy_value
        ):
            raise ValueError(
                "Conflicting TxStreamHealth alias values for "
                f"{canonical_name} and {legacy_name}: "
                f"{canonical_value!r} != {legacy_value!r}"
            )
        if canonical_value == default and legacy_value != default:
            object.__setattr__(self, canonical_name, legacy_value)
        elif legacy_value == default and canonical_value != default:
            object.__setattr__(self, legacy_name, canonical_value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "queued_frames": self.queued_frames,
            "frames_queued": self.frames_queued,
            "frames_dropped": self.frames_dropped,
            "write_attempts": self.write_attempts,
            "writes_completed": self.writes_completed,
            "write_failures": self.write_failures,
            "enqueued_audio_ms": self.enqueued_audio_ms,
            "queued_audio_ms": self.queued_audio_ms,
            "buffered_audio_ms": self.buffered_audio_ms,
            "callback_consumed_audio_ms": self.callback_consumed_audio_ms,
            "consumed_audio_ms": self.consumed_audio_ms,
            "callback_output_audio_ms": self.callback_output_audio_ms,
            "written_audio_ms": self.written_audio_ms,
            "dropped_audio_ms": self.dropped_audio_ms,
            "enqueue_overrun_audio_ms": self.enqueue_overrun_audio_ms,
            "overrun_audio_ms": self.overrun_audio_ms,
            "enqueue_overrun_events": self.enqueue_overrun_events,
            "overrun_events": self.overrun_events,
            "callback_underrun_audio_ms": self.callback_underrun_audio_ms,
            "underrun_audio_ms": self.underrun_audio_ms,
            "callback_underrun_events": self.callback_underrun_events,
            "underrun_events": self.underrun_events,
            "callback_errors": self.callback_errors,
            "callback_status_flags": dict(self.callback_status_flags),
            "callback_calls_per_sec_ewma": self.callback_calls_per_sec_ewma,
            "write_calls_per_sec_ewma": self.write_calls_per_sec_ewma,
            "last_error": self.last_error,
            "discard_events": self.discard_events,
            "discarded_audio_ms": self.discarded_audio_ms,
            "refill_events": self.refill_events,
            "playback_read_ratio": self.playback_read_ratio,
            "fill_target_ms": self.fill_target_ms,
        }


@dataclass(frozen=True, slots=True)
class RxStreamHealth:
    """Snapshot of capture callback delivery and input-side health."""

    frames_delivered: int = 0
    input_overflow_events: int = 0
    input_underflow_events: int = 0
    callback_errors: int = 0
    callback_status_flags: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames_delivered": self.frames_delivered,
            "input_overflow_events": self.input_overflow_events,
            "input_underflow_events": self.input_underflow_events,
            "callback_errors": self.callback_errors,
            "callback_status_flags": dict(self.callback_status_flags),
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

    async def discard_queued(self) -> int:
        """Drop every playback frame queued but not yet handed to the device.

        Returns how many queued writes were dropped. The stream keeps
        running; later writes queue normally. Audio already handed to the
        device/OS is not recalled.
        """
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

    async def discard_queued(self) -> int:
        """Drop every playback frame queued but not yet handed to the device.

        Returns how many queued writes were dropped. The stream keeps
        running; later writes queue normally. Audio already handed to the
        device/OS is not recalled.
        """
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
        playout: TxPlayoutPolicy = "adaptive",
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
        tx_playout: TxPlayoutPolicy = "adaptive",
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


def _downmix_stereo_to_mono_s16le(
    pcm: bytes,
    *,
    channel: str = "mix",
    open_channels: int = 2,
    deliver_channels: int = 1,
) -> bytes:
    """Collapse the first two channels of N-channel interleaved s16le PCM.

    Generalizes the MOR-504/508 stereo-to-mono downmix to a capture device
    opened at MORE channels than the consumer receives (MOR-4029: PipeWire's
    ``pulse`` = 32 ch, BlackHole = 16 ch, any 4/6/8-channel interface). The
    device's FIRST TWO channels are L/R — the extra virtual-cable/interface
    channels carry no radio audio, so averaging all N open channels would only
    attenuate the signal; channels 2..N-1 are dropped. ``open_channels`` is the
    OS-open count (N >= 2); ``deliver_channels`` is the delivered count:

    - ``1`` — mono via ``channel`` (MOR-508): ``"mix"`` (default) = per-frame
      average ``(L + R) // 2`` of channels 0 and 1 (correct for a dual-mono
      source, the historical behavior preserved verbatim); ``"left"`` = ch0 at
      FULL level (the FTX-1 presents its USB RX audio on the LEFT channel only;
      ``mix`` averaging the live L with a silent R halves the level);
      ``"right"`` = ch1 at FULL level (the mirror case). ``open_channels == 2``
      is byte-identical to the historical stereo-to-mono behavior, so
      IC-7610/X6200 etc. are unchanged.
    - ``2`` — keep channels 0 and 1 interleaved, drop the rest.

    Mirrors ``rigplane.audio.bridge._downmix_stereo_to_mono`` and is
    vectorized with numpy (a core dependency since #1090; the PortAudio
    backend already imports it at open time via ``_ensure_portaudio_deps``).
    The mix path is an int32 widening plus a numpy floor division, which
    rounds toward negative infinity exactly like the historical Python
    ``//`` loop — the output is byte-identical (MOR-4227 equality pins in
    ``tests/test_audio_cpu_hotspots_mor4227.py``). Used by
    :class:`_RxFramer` whenever a device opens at more channels than it
    delivers: passing the interleaved PCM straight through would misread the
    delivered layout (wrong effective sample-rate, garbage audio — the same
    class of bug the bridge downmix guards, issue #1381). Trailing bytes
    that do not form a whole N-channel frame are dropped (an incomplete
    frame cannot satisfy the fixed contract).

    Single-channel selection cannot clip: it copies one existing s16 sample
    per frame, so the output stays in range with no widening/averaging.
    """
    import numpy as np

    frame_bytes = 2 * open_channels  # one N-channel frame of s16 samples
    usable = len(pcm) - (len(pcm) % frame_bytes)
    if usable <= 0:
        return b""
    # '<i2' fixes little-endian on any host, replacing the stdlib byteswap
    # dance; this runs on the audio thread with up to 32 open channels.
    frames = np.frombuffer(pcm, dtype="<i2", count=usable // 2).reshape(
        -1, open_channels
    )
    if deliver_channels == 2:
        stereo = frames[:, :2]
        return np.ascontiguousarray(stereo).tobytes()
    if channel == "left":
        mono: np.ndarray = frames[:, 0]
    elif channel == "right":
        mono = frames[:, 1]
    else:  # "mix" — per-frame average (default; legacy behavior)
        # Average in int32 so (L + R) cannot overflow s16 before the floor
        # division truncates toward -inf like Python's ``//``.
        total = frames[:, 0].astype(np.int32) + frames[:, 1].astype(np.int32)
        mono = total // 2
    return mono.astype("<i2").tobytes()


def _callback_status_flag_names(status: Any) -> tuple[str, ...]:
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


@dataclass(slots=True)
class _RxFeedResult:
    frames_delivered: int = 0
    callback_errors: int = 0


class _CaptureHealthTracker:
    """Mutable capture-side counters with snapshot export."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames_delivered = 0
        self._input_overflow_events = 0
        self._input_underflow_events = 0
        self._callback_errors = 0
        self._callback_status_flags: dict[str, int] = {}

    def record_status(self, status: Any, *, input_only: bool) -> None:
        flags = _callback_status_flag_names(status)
        if input_only:
            flags = tuple(
                flag
                for flag in flags
                if flag.startswith("input_")
                or flag not in {"output_underflow", "output_overflow", "priming_output"}
            )
        if not flags:
            return
        with self._lock:
            for flag in flags:
                self._callback_status_flags[flag] = (
                    self._callback_status_flags.get(flag, 0) + 1
                )
                if flag == "input_overflow":
                    self._input_overflow_events += 1
                elif flag == "input_underflow":
                    self._input_underflow_events += 1

    def record_frames_delivered(self, count: int) -> None:
        if count <= 0:
            return
        with self._lock:
            self._frames_delivered += count

    def record_callback_errors(self, count: int) -> None:
        if count <= 0:
            return
        with self._lock:
            self._callback_errors += count

    def snapshot(self) -> RxStreamHealth:
        with self._lock:
            return RxStreamHealth(
                frames_delivered=self._frames_delivered,
                input_overflow_events=self._input_overflow_events,
                input_underflow_events=self._input_underflow_events,
                callback_errors=self._callback_errors,
                callback_status_flags=dict(self._callback_status_flags),
            )


class _RxFramer:
    """Shared RX data-path: downmix → accumulate → re-chunk into fixed frames.

    Factored out of :class:`_PortAudioRxStream` so the duplex stream
    (:class:`_PortAudioDuplexStream`) can reuse the *exact* same capture
    semantics — the N-channel ``rx_audio_channel`` downmix (MOR-504/508/4029)
    plus the lossless re-chunking of engine-native variable-size blocks into
    fixed ``frame_ms`` frames (the downstream PCM-TX validator rejects any
    other size). The owning stream is the single audio-thread writer, so no
    lock is needed; the consumer ``callback`` is contractually cheap and
    thread-safe.
    """

    def __init__(
        self,
        *,
        config: AudioDeviceConfig,
        deliver_channels: int,
    ) -> None:
        self._rx_audio_channel = config.rx_audio_channel
        # ``config.channels`` is the OS-open count in this per-stream scope.
        # Software downmix fires whenever the device opens at MORE channels
        # than the consumer receives (N >= 2 open -> 1 or 2 delivered,
        # MOR-4029); N == 2 -> 1 is the historical stereo-to-mono case
        # (MOR-504/508). Any other deliver/open relationship passes
        # interleaved PCM through unchanged (over-request already clamps
        # open == deliver upstream).
        self._open_channels = config.channels
        self._deliver_channels = deliver_channels
        self._downmix = config.channels > deliver_channels
        frame_samples = (config.sample_rate * config.frame_ms) // 1000
        self._frame_bytes = max(1, frame_samples * deliver_channels * 2)
        self._accumulator = bytearray()

    @property
    def frame_bytes(self) -> int:
        return self._frame_bytes

    def reset(self) -> None:
        self._accumulator = bytearray()

    def feed(self, indata: Any, callback: Callable[[bytes], None]) -> _RxFeedResult:
        """Downmix + re-chunk *indata*; deliver each whole frame to *callback*.

        Runs on PortAudio's audio thread: keep it non-blocking. Copies the PCM
        out of the (reused, variable-size) input buffer, optionally downmixes
        N open channels to the delivered layout, accumulates, then slices whole
        fixed-size frames out and hands each to the consumer. Re-chunking a
        continuous callback stream is lossless and adds no scheduling seam.
        """
        result = _RxFeedResult()
        try:
            pcm = (
                bytes(indata.tobytes()) if hasattr(indata, "tobytes") else bytes(indata)
            )
        except Exception:
            logger.warning("portaudio-rx: capture copy failed", exc_info=True)
            result.callback_errors += 1
            return result
        if not pcm:
            return result
        if self._downmix:
            # Device opened at N >= 2 ch (native) but the consumer receives
            # fewer (MOR-4029; N == 2 -> mono is the MOR-504 case): collapse
            # the first two device channels to the delivered layout BEFORE
            # chunking, so the accumulator/fixed-frame slicing operates on
            # delivered-layout bytes and the FFT scope + broadcaster receive
            # the layout they expect.
            try:
                pcm = _downmix_stereo_to_mono_s16le(
                    pcm,
                    channel=self._rx_audio_channel,
                    open_channels=self._open_channels,
                    deliver_channels=self._deliver_channels,
                )
            except Exception:
                logger.warning("portaudio-rx: channel downmix failed", exc_info=True)
                result.callback_errors += 1
                return result
            if not pcm:
                return result
        acc = self._accumulator
        acc.extend(pcm)
        frame_bytes = self._frame_bytes
        try:
            while len(acc) >= frame_bytes:
                frame = bytes(acc[:frame_bytes])
                del acc[:frame_bytes]
                callback(frame)
                result.frames_delivered += 1
        except Exception:
            logger.warning("portaudio-rx: consumer callback failed", exc_info=True)
            result.callback_errors += 1
        return result


class _WindowsMtaLease:
    """Keep the process MTA alive while a native stream owns COM interfaces.

    Start and close may run on different bounded workers. A COM count scoped
    only to start would let its apartment disappear while capture is running.
    Windows explicitly permits this usage cookie to be released on another
    thread. Worker-local CoInitializeEx counts remain separately balanced.
    """

    def __init__(self) -> None:
        self._cookie: Any = None
        self._ole32: Any = None

    def acquire(self) -> None:
        if sys.platform != "win32":
            return
        if self._cookie is not None:
            raise RuntimeError("Windows audio stream still owns MTA resources.")
        import ctypes

        ole32 = ctypes.WinDLL("ole32")
        ole32.CoIncrementMTAUsage.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        ole32.CoIncrementMTAUsage.restype = ctypes.c_int32
        ole32.CoDecrementMTAUsage.argtypes = [ctypes.c_void_p]
        ole32.CoDecrementMTAUsage.restype = ctypes.c_int32
        cookie = ctypes.c_void_p()
        hresult = int(ole32.CoIncrementMTAUsage(ctypes.byref(cookie))) & 0xFFFFFFFF
        if hresult != 0:
            raise RuntimeError(f"Windows audio MTA retain failed: 0x{hresult:08x}")
        if not cookie.value:
            raise RuntimeError("Windows audio MTA retain returned no cookie.")
        self._cookie, self._ole32 = cookie, ole32

    def release(self) -> None:
        if self._cookie is None:
            return
        hresult = int(self._ole32.CoDecrementMTAUsage(self._cookie)) & 0xFFFFFFFF
        if hresult != 0:
            raise RuntimeError(f"Windows audio MTA release failed: 0x{hresult:08x}")
        self._cookie, self._ole32 = None, None


class AudioDeviceBusyError(RuntimeError):
    """Another program holds the codec: the exclusive open was refused as busy
    and the shared fallback failed too (MOR-4030).

    Windows refuses a shared open while an exclusive holder exists, so RX
    cannot start until the holder lets go. ``holder`` is the holding
    program's executable name when it could be found, never a path.
    """

    def __init__(
        self,
        message: str,
        *,
        device_index: int | None = None,
        holder: str | None = None,
    ) -> None:
        super().__init__(message)
        self.device_index = device_index
        self.holder = holder


# How PortAudio reports a codec another program holds exclusively. The real
# Windows run (MOR-4030) saw paInvalidDevice (-9996) from BOTH the exclusive open
# and the shared fallback; paDeviceUnavailable (-9985) and the WASAPI host error
# AUDCLNT_E_DEVICE_IN_USE (0x8889000A, signed and unsigned) are the same fact.
_IN_USE_CODES = frozenset({-9985, -9996, -2004287478, 0x8889000A})
_IN_USE_TEXT = (
    "device unavailable",
    "invalid device",
    "device is in use",
    "device_in_use",
    "0x8889000a",
)
# E_ACCESSDENIED: a privacy or permission refusal, never "in use".
_ACCESS_DENIED_CODES = frozenset({-2147024891, 0x80070005})
_ACCESS_DENIED_TEXT = ("0x80070005", "access is denied", "e_accessdenied")


def _error_values(error: BaseException) -> tuple[list[int], list[str]]:
    """The integer codes and lower-cased texts in a PortAudio error's args."""
    codes: list[int] = []
    texts: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, int):
            codes.append(value)
        elif isinstance(value, str):
            texts.append(value.lower())
        elif isinstance(value, (tuple, list)):
            for item in value:
                walk(item)

    walk(error.args)
    return codes, texts


def _is_in_use_refusal(error: BaseException) -> bool:
    """Whether an open error says the device is held, not denied or malformed."""
    codes, texts = _error_values(error)
    if any(code in _ACCESS_DENIED_CODES for code in codes) or any(
        marker in text for text in texts for marker in _ACCESS_DENIED_TEXT
    ):
        return False
    return any(code in _IN_USE_CODES for code in codes) or any(
        marker in text for text in texts for marker in _IN_USE_TEXT
    )


class _OpenAttempt(NamedTuple):
    """One way to open a stream: its PortAudio ``extra_settings`` and whether it
    is a WASAPI exclusive open."""

    extra_settings: Any
    exclusive: bool


class _CapturePolicy:
    """WASAPI capture-mode policy shared by the streams one backend opens (MOR-4030).

    In ``"exclusive-preferred"`` mode a stream on a WASAPI device is opened in
    exclusive mode first and, when PortAudio refuses that, once more in shared
    mode with sample-rate auto-convert (shared WASAPI otherwise accepts only
    the engine's mix format, which MME converted for free). Every other device
    and the ``"shared"`` mode open exactly as before, with no host-API lookup.
    The fallback is logged once per device, not once per open.
    """

    def __init__(self, mode: str = "shared") -> None:
        if mode not in CAPTURE_MODES:
            raise ValueError(
                f"capture_mode must be one of {', '.join(CAPTURE_MODES)}; got {mode!r}."
            )
        self.mode: CaptureMode = mode  # type: ignore[assignment]
        self._logged: set[int] = set()
        self._logged_lock = threading.Lock()
        self.holder_lookup: HolderLookup = find_holder_name
        self.holder_timeout_s = HOLDER_LOOKUP_TIMEOUT_S

    def _is_wasapi(self, sd: Any, device_index: int) -> bool:
        if self.mode != "exclusive-preferred":
            return False
        try:
            host_api = sd.query_devices(device_index)["hostapi"]
            return bool(
                type(host_api) is int
                and host_api >= 0
                and sd.query_hostapis(host_api)["name"] == WASAPI_HOST_API
            )
        except Exception:
            logger.debug(
                "portaudio: host API metadata unavailable; opening in shared mode",
                exc_info=True,
            )
            return False

    def attempts(self, sd: Any, device_index: int) -> tuple[_OpenAttempt, ...]:
        """The opens to try, in order: exclusive then shared WASAPI, or one plain."""
        if self._is_wasapi(sd, device_index):
            try:
                return (
                    _OpenAttempt(sd.WasapiSettings(exclusive=True), True),
                    _OpenAttempt(sd.WasapiSettings(auto_convert=True), False),
                )
            except Exception:
                logger.debug(
                    "portaudio: WASAPI settings unavailable; opening in shared mode",
                    exc_info=True,
                )
        return (_OpenAttempt(None, False),)

    def probe_settings(self, sd: Any, device_index: int) -> Any:
        """``extra_settings`` for a format probe: shared WASAPI may convert the rate."""
        if self._is_wasapi(sd, device_index):
            try:
                return sd.WasapiSettings(auto_convert=True)
            except Exception:
                logger.debug("portaudio: WASAPI settings unavailable", exc_info=True)
        return None

    def note_fallback(self, device_index: int, error: BaseException) -> None:
        """Log that the exclusive open was refused, once for this device."""
        with self._logged_lock:
            if device_index in self._logged:
                return
            self._logged.add(device_index)
        logger.warning(
            "portaudio: WASAPI exclusive open of device [%d] was refused (%s); "
            "using shared mode, where Windows audio effects can apply",
            device_index,
            error,
        )

    def busy_error(
        self,
        sd: Any,
        device_index: int,
        exclusive_error: BaseException | None,
        shared_error: BaseException,
        *,
        direction: str,
    ) -> AudioDeviceBusyError | None:
        """The in-use refusal when another program holds the device, else None.

        Called only after the shared fallback failed too. It is the case when
        the exclusive open was refused, the shared failure says the device is
        held (see ``_is_in_use_refusal``), and the device still enumerates with
        channels for this direction (so it is not unplugged or missing). Looks
        up the holder's name (bounded, never raising) and logs its executable
        name only.
        """
        if exclusive_error is None or not _is_in_use_refusal(shared_error):
            return None
        try:
            info = sd.query_devices(device_index)
            channels = info[
                "max_input_channels" if direction == "rx" else "max_output_channels"
            ]
            if not channels > 0:
                return None
            device_name = str(info["name"])
        except Exception:
            return None
        # The holder is only looked up for capture: its sessions are those of the
        # capture endpoint, and the message that names it is about the audio input.
        holder = (
            holder_exe_name(self.holder_lookup, device_name, self.holder_timeout_s)
            if direction == "rx"
            else None
        )
        logger.warning(
            "portaudio: device [%d] is in use by %s; audio cannot start until it lets go",
            device_index,
            holder or "another program",
        )
        return AudioDeviceBusyError(
            f"Audio device [{device_index}] is in use by another program.",
            device_index=device_index,
            holder=holder,
        )


def _stream_kwargs(attempt: _OpenAttempt, kwargs: dict[str, Any]) -> dict[str, Any]:
    if attempt.extra_settings is None:
        return kwargs
    return {**kwargs, "extra_settings": attempt.extra_settings}


class _PortAudioRxStream:
    """RxStream backed by a callback-driven sounddevice InputStream.

    Capture is clocked by PortAudio's own audio thread via an ``InputStream``
    callback, mirroring the callback design of :class:`_PortAudioTxStream`.
    This replaces the previous fixed-``blocksize=960`` blocking-read loop
    (``stream.read(960)`` on a worker thread), whose 20 ms read straddled the
    WASAPI shared-mode engine period (~10 ms) and dropped/duplicated a sample
    run once per block, producing a ~50 Hz spectral comb on captured TX audio
    on Windows.

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
        *,
        config: AudioDeviceConfig,
        blocksize: int,
        deliver_channels: int | None = None,
        capture_policy: _CapturePolicy | None = None,
    ) -> None:
        self._sd = sd
        self._device_index = device_index
        self._capture_policy = capture_policy or _CapturePolicy()
        # True once the stream opened in WASAPI exclusive mode (MOR-4030).
        self.exclusive = False
        self._sample_rate = config.sample_rate
        # ``config.channels`` is the OS open count; ``_deliver_channels`` is
        # what the consumer receives. When deliver < open (1- or 2-channel
        # request on an N-channel device, MOR-504/4029) the framer downmixes
        # interleaved s16le to the deliver count before chunking. Default:
        # deliver == open.
        self._channels = config.channels
        self._deliver_channels = (
            config.channels if deliver_channels is None else deliver_channels
        )
        self._blocksize = blocksize
        # The framer owns the downmix + fixed-frame re-chunking (shared with the
        # duplex stream). It carries the sub-frame remainder between audio-thread
        # callbacks; the audio thread is the only writer, so no lock is needed.
        self._framer = _RxFramer(
            config=config,
            deliver_channels=self._deliver_channels,
        )
        self._stream: Any = None
        self._mta = _WindowsMtaLease()
        self._running = False
        self._callback: Callable[[bytes], None] | None = None
        self._capture_health = _CaptureHealthTracker()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def capture_health(self) -> RxStreamHealth:
        return self._capture_health.snapshot()

    async def start(self, callback: Callable[[bytes], None]) -> None:
        if self.running or self._stream is not None:
            raise RuntimeError("RX stream already running.")
        self._callback = callback
        self._framer.reset()
        stage = "open"
        try:
            self._mta.acquire()
            latency: str | float = "low"
            if sys.platform == "linux":
                try:
                    host_api = self._sd.query_devices(self._device_index)["hostapi"]
                    if type(host_api) is int and host_api >= 0:
                        if self._sd.query_hostapis(host_api)["name"] == "ALSA":
                            latency = _LINUX_ALSA_INPUT_LATENCY_S
                except Exception:
                    logger.debug(
                        "portaudio-rx: host API metadata unavailable; using low latency",
                        exc_info=True,
                    )
            attempts = self._capture_policy.attempts(self._sd, self._device_index)
            exclusive_error: Exception | None = None
            for number, attempt in enumerate(attempts, start=1):
                stage = "open"
                try:
                    self._stream = self._sd.InputStream(
                        **_stream_kwargs(
                            attempt,
                            {
                                "samplerate": self._sample_rate,
                                "channels": self._channels,
                                "dtype": "int16",
                                "device": self._device_index,
                                "blocksize": self._blocksize,
                                "latency": latency,
                                "callback": self._input_callback,
                            },
                        )
                    )
                    stage = "start"
                    self._stream.start()
                except Exception as exc:
                    if not attempt.exclusive or number == len(attempts):
                        busy = self._capture_policy.busy_error(
                            self._sd,
                            self._device_index,
                            exclusive_error,
                            exc,
                            direction="rx",
                        )
                        if busy is not None:
                            raise busy from exc
                        raise
                    exclusive_error = exc
                    self._capture_policy.note_fallback(self._device_index, exc)
                    self._close_failed_attempt()
                    continue
                self.exclusive = attempt.exclusive
                break
        except BaseException:
            logger.warning(
                "portaudio-rx: %s failed (device=%d, sample_rate=%d, "
                "open_channels=%d, deliver_channels=%d, blocksize=%d)",
                stage,
                self._device_index,
                self._sample_rate,
                self._channels,
                self._deliver_channels,
                self._blocksize,
            )
            # Construction may succeed before Pa_StartStream refuses. Close
            # that owned handle even though running was never set to True;
            # the driver's normal stop path skips non-running streams.
            try:
                await self.stop()
            except Exception:
                logger.warning(
                    "portaudio-rx: failed-start cleanup failed", exc_info=True
                )
            raise
        self._running = True

    def _close_failed_attempt(self) -> None:
        """Close the stream a refused exclusive open left behind, if any."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                logger.debug("portaudio-rx: failed-attempt close failed", exc_info=True)

    async def stop(self) -> None:
        stream = self._stream
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
            stream.close()
        # A refused close retains the handle and its MTA cookie for retry.
        self._stream = None
        self._mta.release()

    def _input_callback(
        self,
        indata: Any,
        _frames: int,
        _time_info: Any,
        _status: Any,
    ) -> None:
        # Runs on PortAudio's audio thread: keep it non-blocking. The shared
        # framer copies the captured PCM out of the (reused, variable-size) input
        # buffer, optionally downmixes N open channels, accumulates, and delivers
        # whole fixed-size frames to the consumer. Re-chunking a continuous
        # callback stream is lossless and adds no scheduling seam, so it keeps
        # the fixed-frame contract without re-introducing the comb. The consumer
        # is contractually cheap/thread-safe.
        cb = self._callback
        if cb is None:
            return
        self._capture_health.record_status(_status, input_only=True)
        result = self._framer.feed(indata, cb)
        self._capture_health.record_frames_delivered(result.frames_delivered)
        self._capture_health.record_callback_errors(result.callback_errors)


class _PortAudioTxStream:
    """TxStream backed by a sounddevice OutputStream callback.

    The bounded callback ring decouples async producers from PortAudio's
    hardware-clocked consumer: ``write()`` enqueues interleaved PCM and returns
    quickly, and the callback drains the ring at the playback cadence.

    Two playback policies share this one ring, selected per stream via
    ``playout`` (MOR-4013):

    - ``"adaptive"`` (the default; every RX playback sink): the ring is
      held at a small fill target — playback starts only once the target is
      buffered, a drift-compensated read ratio absorbs producer/device clock
      mismatch (±3% clamp), a dry ring drops to unprimed for ONE refill
      event instead of per-block zero-fill underruns, and an enqueue above
      the target-plus-headroom cap trims once back to the target with a
      short fade at the splice.
    - ``"immediate"`` (passed explicitly by every host-to-radio TX caller,
      usb_driver): the free-running 1 s ring, byte-identical to the
      pre-MOR-4013 behaviour — the callback consumes in real time (partial
      copy + silence on a dry ring, one underrun per starved block) and an
      overflowing enqueue drops the oldest queued frames one at a time. A
      freshly written frame plays on the next callback, which TX paths
      (PTT release promptness) require.
    """

    def __init__(
        self,
        sd: Any,
        _np: Any,
        device_index: int,
        sample_rate: int,
        channels: int,
        blocksize: int,
        *,
        playout: TxPlayoutPolicy = "adaptive",
        capture_policy: _CapturePolicy | None = None,
    ) -> None:
        self._sd = sd
        self._capture_policy = capture_policy or _CapturePolicy()
        # True once the stream opened in WASAPI exclusive mode (MOR-4030).
        self.exclusive = False
        # numpy handle for the adaptive drift-compensated read (MOR-4013);
        # the immediate TX policy never touches it.
        self._np = _np
        self._device_index = device_index
        self._sample_rate = sample_rate
        self._channels = channels
        self._blocksize = blocksize
        self._bytes_per_audio_frame = max(1, channels * 2)
        self._capacity_bytes = self._buffer_capacity_bytes()
        self._playout = playout
        if playout == "adaptive":
            self._fill_target_ms = float(_TX_FILL_TARGET_MS)
            self._fill_target_frames = self._audio_frames_for_ms(_TX_FILL_TARGET_MS)
            self._fill_target_bytes = (
                self._fill_target_frames * self._bytes_per_audio_frame
            )
            self._tx_cap_bytes = (
                self._fill_target_frames
                + self._audio_frames_for_ms(_TX_OVERRUN_HEADROOM_MS)
            ) * self._bytes_per_audio_frame
        else:
            self._fill_target_ms = 0.0
            self._fill_target_frames = 0
            self._fill_target_bytes = 0
            self._tx_cap_bytes = self._capacity_bytes
        self._drift_deadband_frames = self._audio_frames_for_ms(_TX_DRIFT_DEADBAND_MS)
        self._trim_fade_frames = self._audio_frames_for_ms(_TX_TRIM_FADE_MS)
        self._stream: Any = None
        self._mta = _WindowsMtaLease()
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
        self._discard_events: int = 0
        self._discarded_audio_ms: float = 0.0
        # Fill-target playback state (MOR-4013). An immediate (TX-to-radio)
        # stream starts primed: the priming gate only ever gates adaptive RX
        # playback.
        self._primed = playout == "immediate"
        self._refill_events: int = 0
        self._drift_ratio: float = 1.0
        self._drift_phase: float = 0.0
        self._drift_filtered_error: float = 0.0

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
                enqueued_audio_ms=round(self._queued_audio_ms, 3),
                buffered_audio_ms=round(buffered_audio_ms, 3),
                callback_consumed_audio_ms=round(self._consumed_audio_ms, 3),
                callback_output_audio_ms=round(self._written_audio_ms, 3),
                dropped_audio_ms=round(self._dropped_audio_ms, 3),
                enqueue_overrun_audio_ms=round(self._overrun_audio_ms, 3),
                enqueue_overrun_events=self._overrun_events,
                callback_underrun_audio_ms=round(self._underrun_audio_ms, 3),
                callback_underrun_events=self._underrun_events,
                callback_errors=self._callback_errors,
                callback_status_flags=dict(self._callback_status_flags),
                callback_calls_per_sec_ewma=(
                    round(self._write_calls_per_sec_ewma, 3)
                    if self._write_calls_per_sec_ewma is not None
                    else None
                ),
                last_error=self._last_error,
                discard_events=self._discard_events,
                discarded_audio_ms=round(self._discarded_audio_ms, 3),
                refill_events=self._refill_events,
                playback_read_ratio=round(self._drift_ratio, 5),
                fill_target_ms=self._fill_target_ms,
            )

    async def start(self) -> None:
        if self.running or self._stream is not None:
            raise RuntimeError("TX stream already running.")
        try:
            self._mta.acquire()
            attempts = self._capture_policy.attempts(self._sd, self._device_index)
            exclusive_error: Exception | None = None
            for number, attempt in enumerate(attempts, start=1):
                try:
                    self._stream = self._sd.OutputStream(
                        **_stream_kwargs(
                            attempt,
                            {
                                "samplerate": self._sample_rate,
                                "channels": self._channels,
                                "dtype": "int16",
                                "device": self._device_index,
                                "blocksize": self._blocksize,
                                "latency": "low",
                                "callback": self._output_callback,
                            },
                        )
                    )
                    self._stream.start()
                except Exception as exc:
                    if not attempt.exclusive or number == len(attempts):
                        busy = self._capture_policy.busy_error(
                            self._sd,
                            self._device_index,
                            exclusive_error,
                            exc,
                            direction="tx",
                        )
                        if busy is not None:
                            raise busy from exc
                        raise
                    exclusive_error = exc
                    self._capture_policy.note_fallback(self._device_index, exc)
                    failed, self._stream = self._stream, None
                    try:
                        self._close_stream(failed)
                    except Exception:
                        logger.debug(
                            "portaudio-tx: failed-attempt close failed", exc_info=True
                        )
                    continue
                self.exclusive = attempt.exclusive
                break
        except BaseException:
            try:
                await self.stop()
            except Exception:
                logger.warning(
                    "portaudio-tx: failed-start cleanup failed", exc_info=True
                )
            raise
        self._running = True

    async def stop(self) -> None:
        stream = self._stream
        self._running = False
        try:
            self._close_stream(stream)
        finally:
            self._clear_buffer()
        self._stream = None
        self._mta.release()

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
        # let the callback clock playback. Overflow handling follows the
        # stream's policy: an adaptive stream trims the stalest audio ONCE
        # back to the target (with a short fade at the splice, MOR-4013) so
        # catch-up bursts stop dropping a frame per write; an immediate
        # TX-to-radio stream keeps the historical behaviour — drop oldest
        # queued frames one at a time until the write fits, so latency
        # cannot grow without bound.
        incoming_drop_bytes = 0
        if len(data) > self._capacity_bytes:
            incoming_drop_bytes = self._align_byte_count(
                len(data) - self._capacity_bytes,
                round_up=True,
            )
            data = data[incoming_drop_bytes:]

        dropped_bytes = incoming_drop_bytes
        with self._lock:
            if self._playout == "adaptive":
                overflowed = self._buffered_bytes + len(data) > self._tx_cap_bytes
                if overflowed:
                    excess_bytes = (
                        self._buffered_bytes + len(data) - self._fill_target_bytes
                    )
                    trim_bytes = min(
                        self._align_byte_count(excess_bytes, round_up=True),
                        self._buffered_bytes,
                    )
                    dropped_bytes += self._drop_oldest_locked(trim_bytes)
                    self._apply_trim_fade_locked()
            else:
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
                    drop_bytes = self._align_byte_count(
                        remaining_overflow, round_up=True
                    )
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

    async def discard_queued(self) -> int:
        """Drop every playback frame queued but not yet handed to the device.

        Returns how many queued writes were dropped. The stream keeps
        running; later writes queue normally. Audio already handed to the
        device/OS is not recalled. A stream that is not running returns 0
        and changes nothing.
        """
        if not self.running:
            return 0
        with self._lock:
            dropped_writes = len(self._frame_lengths)
            dropped_bytes = self._buffered_bytes
            if not dropped_writes and not dropped_bytes:
                return 0
            self._discard_locked()
            self._discard_events += 1
            self._discarded_audio_ms += self._audio_ms_for_bytes(dropped_bytes)
            return dropped_writes

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
            # Callback hot path: no logging or I/O. Fill the block from the
            # ring at the drift-compensated read rate; a dry or unprimed ring
            # outputs silence and counts a single refill event (MOR-4013).
            dest = memoryview(outdata).cast("B")
            if len(dest) != byte_count:
                raise ValueError(
                    f"output buffer is {len(dest)} bytes, expected {byte_count}"
                )
            with self._lock:
                consumed_bytes = self._playback_block_locked(dest, frames)
                if consumed_bytes < byte_count:
                    dest[consumed_bytes:] = b"\x00" * (byte_count - consumed_bytes)
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

    def _playback_block_locked(self, dest: memoryview, frames: int) -> int:
        """Fill one playback block from the ring; caller holds ``self._lock``.

        Returns the input bytes consumed (0 for a silent block). An
        immediate stream takes the legacy path: copy what is buffered,
        zero-fill the remainder, count one underrun per starved block. On
        an adaptive stream: while unprimed the callback outputs silence
        until the fill target is buffered again, and a primed ring that
        cannot supply the block goes dry ONCE: one underrun plus one
        refill event, then back to unprimed — a stall no longer produces a
        partial-block zero-fill underrun per callback (MOR-4013).
        """
        if self._playout == "immediate":
            return self._read_ring_partial_locked(dest)
        if not self._primed:
            if self._buffered_bytes < self._fill_target_bytes:
                return 0
            self._primed = True
        ratio = self._drift_ratio_for_block_locked(frames)
        if ratio == 1.0:
            # Exact copy path: bit-identical passthrough whenever the fill
            # error sits inside the deadband (matched clocks).
            self._drift_phase = 0.0
            need_frames = frames
        else:
            need_frames = int(math.floor(self._drift_phase + (frames - 1) * ratio)) + 2
        available_frames = self._buffered_bytes // self._bytes_per_audio_frame
        if available_frames < need_frames:
            shortfall_bytes = (need_frames - available_frames) * (
                self._bytes_per_audio_frame
            )
            self._primed = False
            self._refill_events += 1
            self._underrun_events += 1
            self._underrun_audio_ms += self._audio_ms_for_bytes(shortfall_bytes)
            self._reset_drift_locked()
            return 0
        if ratio == 1.0:
            return self._copy_head_into_locked(
                dest, frames * self._bytes_per_audio_frame
            )
        return self._read_interpolated_locked(dest, frames, ratio)

    def _read_ring_partial_locked(self, dest: memoryview) -> int:
        """Immediate-playout consume (no fill target); caller holds the lock.

        Pre-MOR-4013 TX behaviour, unchanged: copy what is buffered, leave
        the remainder to be zero-filled by the caller, and count one
        underrun per starved block.
        """
        requested = len(dest)
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
        if copied < requested:
            self._underrun_events += 1
            self._underrun_audio_ms += self._audio_ms_for_bytes(requested - copied)
        return copied

    def _drift_ratio_for_block_locked(self, frames: int) -> float:
        """Read ratio for this block from the fill error; caller holds the lock.

        Slow filtered proportional controller: inside the deadband the exact
        copy path runs (ratio 1.0); outside it the ratio is slew-limited and
        clamped to ±3% so a 1-2% producer/device clock mismatch is absorbed
        without letting the ring pin at the cap or drain (MOR-4013).
        """
        dt = frames / self._sample_rate
        error_frames = (
            self._buffered_bytes // self._bytes_per_audio_frame
        ) - self._fill_target_frames
        alpha = 1.0 - math.exp(-dt / _TX_DRIFT_FILTER_S)
        self._drift_filtered_error += alpha * (
            error_frames - self._drift_filtered_error
        )
        if abs(error_frames) <= self._drift_deadband_frames:
            self._drift_ratio = 1.0
            return 1.0
        target_ratio = 1.0 + _TX_DRIFT_KP * self._drift_filtered_error
        target_ratio = min(
            1.0 + _TX_DRIFT_MAX_RATIO,
            max(1.0 - _TX_DRIFT_MAX_RATIO, target_ratio),
        )
        max_step = _TX_DRIFT_SLEW_PER_S * dt
        if target_ratio > self._drift_ratio + max_step:
            target_ratio = self._drift_ratio + max_step
        elif target_ratio < self._drift_ratio - max_step:
            target_ratio = self._drift_ratio - max_step
        self._drift_ratio = target_ratio
        return target_ratio

    def _read_interpolated_locked(
        self,
        dest: memoryview,
        frames: int,
        ratio: float,
    ) -> int:
        """Consume ``frames * ratio`` input frames as one output block via
        linear interpolation, keeping the fractional read position between
        blocks; caller holds ``self._lock``.

        Interpolation runs per audio frame over ALL channels: position ``i``
        of the output blends input frames ``floor(pos)`` and ``floor(pos)+1``
        channel by channel, so interleaved stereo (and any channel count)
        stays interleaved (MOR-4013).
        """
        advance_exact = self._drift_phase + frames * ratio
        advance_frames = int(math.floor(advance_exact))
        need_frames = int(math.floor(self._drift_phase + (frames - 1) * ratio)) + 2
        span = self._peek_head_bytes_locked(need_frames * self._bytes_per_audio_frame)
        np = self._np
        block = np.frombuffer(span, dtype=np.int16).reshape(-1, self._channels)
        positions = self._drift_phase + np.arange(frames, dtype=np.float64) * ratio
        base = positions.astype(np.int64)
        frac = (positions - base).reshape(-1, 1)
        values = np.rint(block[base] * (1.0 - frac) + block[base + 1] * frac)
        payload = values.astype(np.int16).tobytes()
        dest[: len(payload)] = payload
        self._consume_head_locked(advance_frames * self._bytes_per_audio_frame)
        self._drift_phase = advance_exact - advance_frames
        return advance_frames * self._bytes_per_audio_frame

    def _copy_head_into_locked(self, dest: memoryview, byte_count: int) -> int:
        """Bit-exact copy of ``byte_count`` bytes out of the ring head;
        caller holds ``self._lock`` and guarantees they are buffered."""
        first = min(byte_count, self._capacity_bytes - self._read_pos)
        if first:
            dest[:first] = self._buffer[self._read_pos : self._read_pos + first]
        second = byte_count - first
        if second:
            dest[first : first + second] = self._buffer[:second]
        self._read_pos = (self._read_pos + byte_count) % self._capacity_bytes
        self._buffered_bytes -= byte_count
        self._consume_frame_lengths_locked(byte_count, count_dropped=False)
        if self._buffered_bytes == 0:
            self._read_pos = self._write_pos
        return byte_count

    def _peek_head_bytes_locked(self, byte_count: int) -> bytes:
        """Copy ``byte_count`` bytes from the ring head without consuming."""
        first = min(byte_count, self._capacity_bytes - self._read_pos)
        segment = bytes(self._buffer[self._read_pos : self._read_pos + first])
        second = byte_count - first
        if second:
            segment += bytes(self._buffer[:second])
        return segment

    def _consume_head_locked(self, byte_count: int) -> None:
        self._drop_oldest_locked(byte_count, count_dropped=False)

    def _reset_drift_locked(self) -> None:
        self._drift_ratio = 1.0
        self._drift_phase = 0.0
        self._drift_filtered_error = 0.0

    def _apply_trim_fade_locked(self) -> None:
        """Short fade-in over the ring head after a trim so the splice where
        stale audio was dropped does not click; caller holds the lock."""
        buffered_frames = self._buffered_bytes // self._bytes_per_audio_frame
        fade_frames = min(self._trim_fade_frames, buffered_frames // 2)
        if fade_frames < 2:
            return
        byte_count = fade_frames * self._bytes_per_audio_frame
        segment = bytearray(self._peek_head_bytes_locked(byte_count))
        channels = self._channels
        for i in range(len(segment) // 2):
            scale = ((i // channels) + 1) / fade_frames
            value = int.from_bytes(segment[2 * i : 2 * i + 2], "little", signed=True)
            segment[2 * i : 2 * i + 2] = int(value * scale).to_bytes(
                2, "little", signed=True
            )
        first = min(byte_count, self._capacity_bytes - self._read_pos)
        self._buffer[self._read_pos : self._read_pos + first] = segment[:first]
        if first < byte_count:
            self._buffer[: byte_count - first] = segment[first:]

    def _write_ring_locked(self, data: bytes) -> None:
        first = min(len(data), self._capacity_bytes - self._write_pos)
        if first:
            self._buffer[self._write_pos : self._write_pos + first] = data[:first]
        second = len(data) - first
        if second:
            self._buffer[:second] = data[first:]
        self._write_pos = (self._write_pos + len(data)) % self._capacity_bytes
        self._buffered_bytes += len(data)

    def _drop_oldest_locked(
        self,
        byte_count: int,
        *,
        count_dropped: bool = True,
    ) -> int:
        dropped = min(byte_count, self._buffered_bytes)
        if dropped <= 0:
            return 0
        self._read_pos = (self._read_pos + dropped) % self._capacity_bytes
        self._buffered_bytes -= dropped
        self._consume_frame_lengths_locked(dropped, count_dropped=count_dropped)
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

    def _audio_frames_for_ms(self, ms: int) -> int:
        return max(1, (self._sample_rate * ms) // 1000)

    def _clear_buffer(self) -> None:
        with self._lock:
            self._discard_locked()

    def _discard_locked(self) -> None:
        """Reset the playback ring to empty; caller must hold ``self._lock``."""
        self._buffer[:] = b"\x00" * self._capacity_bytes
        self._read_pos = 0
        self._write_pos = 0
        self._buffered_bytes = 0
        self._frame_lengths.clear()
        # An adaptive stream re-primes after its ring is dropped; an
        # immediate TX-to-radio stream never gates on priming.
        self._primed = self._playout == "immediate"
        self._reset_drift_locked()

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
        return _callback_status_flag_names(status)

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
        stream.close()


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
      ``rx_audio_channel`` downmix (MOR-504/508/4029) and lossless re-chunking into
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
        *,
        config: AudioDeviceConfig,
        blocksize: int,
        deliver_channels: int | None = None,
        tx_channels: int | None = None,
        tx_playout: TxPlayoutPolicy = "adaptive",
    ) -> None:
        self._sd = sd
        self._device_index = device_index
        self._sample_rate = config.sample_rate
        # RX leg opens at the native ``config.channels`` count and delivers
        # ``deliver_channels`` (downmix when fewer, MOR-504/4029). The TX leg opens at
        # its own ``tx_channels`` count (the mono playback the radio's USB MOD
        # consumes); ``sd.Stream`` accepts a ``(in, out)`` channel pair so the
        # two legs need not match.
        self._channels = config.channels
        self._deliver_channels = (
            config.channels if deliver_channels is None else deliver_channels
        )
        self._tx_channels = config.channels if tx_channels is None else tx_channels
        self._blocksize = blocksize
        self._framer = _RxFramer(
            config=config,
            deliver_channels=self._deliver_channels,
        )
        # Embed a TX stream purely for its ring + write()/output-callback; its
        # own ``sd`` stream is never opened (we drive its ``_output_callback``
        # from the duplex callback). It opens at ``tx_channels`` so the ring's
        # frame accounting and ``outdata`` interleaving match the TX leg.
        # ``tx_playout`` carries the same playback policy as
        # ``open_tx(playout=...)``: the bridge opens the duplex stream for RX
        # playback (adaptive default), usb_driver's TX duplex passes
        # ``"immediate"`` (MOR-4013).
        self._tx = _PortAudioTxStream(
            sd,
            np,
            device_index=device_index,
            sample_rate=config.sample_rate,
            channels=self._tx_channels,
            blocksize=blocksize,
            playout=tx_playout,
        )
        self._stream: Any = None
        self._mta = _WindowsMtaLease()
        self._running = False
        self._callback: Callable[[bytes], None] | None = None
        self._capture_health = _CaptureHealthTracker()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def write_health(self) -> TxStreamHealth:
        return self._tx.write_health

    @property
    def capture_health(self) -> RxStreamHealth:
        return self._capture_health.snapshot()

    async def start(self, callback: Callable[[bytes], None]) -> None:
        if self.running or self._stream is not None:
            raise RuntimeError("Duplex stream already running.")
        self._callback = callback
        self._framer.reset()
        # The embedded TX stream's ring must be marked running so ``write()``
        # accepts frames; its OWN sounddevice stream stays unopened — the duplex
        # callback drives playback.
        self._tx._running = True
        # ONE full-duplex stream: device=(idx, idx), channels=(open, open).
        # blocksize=0 (engine-native period) mirrors a clean sd.rec/playback.
        try:
            self._mta.acquire()
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
        except BaseException:
            try:
                await self.stop()
            except Exception:
                logger.warning(
                    "portaudio-duplex: failed-start cleanup failed", exc_info=True
                )
            raise
        self._running = True

    async def stop(self) -> None:
        stream = self._stream
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
            stream.close()
        self._stream = None
        self._mta.release()

    async def write(self, frame: bytes) -> None:
        """Queue one PCM s16le playback frame (TX leg, via the shared ring)."""
        if not self.running:
            raise RuntimeError("Duplex stream is not running.")
        await self._tx.write(frame)

    async def discard_queued(self) -> int:
        """Drop queued-but-unplayed TX playback frames (delegates to the TX leg)."""
        return await self._tx.discard_queued()

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
            self._capture_health.record_status(status, input_only=True)
            result = self._framer.feed(indata, cb)
            self._capture_health.record_frames_delivered(result.frames_delivered)
            self._capture_health.record_callback_errors(result.callback_errors)
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
        capture_mode: CaptureMode = "shared",
    ) -> None:
        self._sd: Any = None
        self._np: Any = None
        self._dependency_loader = dependency_loader
        # MOR-4030: ``"exclusive-preferred"`` opens WASAPI devices in
        # exclusive mode (shared fallback); see :data:`CaptureMode`. It
        # applies to the RX and TX streams opened here, never to duplex.
        self._capture_policy = _CapturePolicy(capture_mode)

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
        extra = self._capture_policy.probe_settings(sd, idx)
        kwargs: dict[str, Any] = {"device": idx, "samplerate": sample_rate}
        if extra is not None:
            kwargs["extra_settings"] = extra
        try:
            if direction == "rx":
                sd.check_input_settings(**kwargs)
            else:
                sd.check_output_settings(**kwargs)
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
        # ``channels`` is the OS open count; ``deliver_channels`` is what the
        # consumer receives. When ``deliver_channels`` < ``channels`` (1- or
        # 2-channel request on an N-channel device, MOR-504/4029) the stream
        # software-downmixes before chunking, so the consumer still sees the
        # delivered fixed-frame contract while the OS opens the device natively.
        if sys.platform == "darwin":
            blocksize = (sample_rate * frame_ms) // 1000
        else:
            blocksize = 0
        return _PortAudioRxStream(
            sd,
            device_index=int(device),
            config=AudioDeviceConfig(
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
                rx_audio_channel=rx_audio_channel,
            ),
            blocksize=blocksize,
            deliver_channels=deliver_channels,
            capture_policy=self._capture_policy,
        )

    def open_tx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        playout: TxPlayoutPolicy = "adaptive",
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
            playout=playout,
            capture_policy=self._capture_policy,
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
        tx_playout: TxPlayoutPolicy = "adaptive",
    ) -> DuplexStream:
        sd, np = self._ensure_deps()
        # TX uses a fixed blocksize (frame_ms) so the duplex callback's
        # ``outdata`` window matches a whole playback frame, mirroring open_tx.
        blocksize = (sample_rate * frame_ms) // 1000
        return _PortAudioDuplexStream(
            sd,
            np,
            device_index=int(device),
            config=AudioDeviceConfig(
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
                rx_audio_channel=rx_audio_channel,
            ),
            blocksize=blocksize,
            deliver_channels=deliver_channels,
            tx_channels=tx_channels,
            tx_playout=tx_playout,
        )


# ---------------------------------------------------------------------------
# FakeAudioBackend (for tests)
# ---------------------------------------------------------------------------


def _exclusive_device_busy(device: int) -> OSError:
    """The -50-shaped error strict mode raises (MOR-566).

    Models the macOS CoreAudio AUHAL ``paramErr -50`` kill observed live when
    a second PortAudio stream opens a device that already drives one
    (MOR-531/559): an :class:`OSError` with ``errno == -50`` and the PaMacCore
    marker in the message, mirroring the ``||PaMacCore (AUHAL)|| err='-50'``
    line PortAudio logs on the real failure.
    """
    return OSError(
        -50,
        f"PaMacCore (AUHAL) err='-50' (paramErr): device {device} already "
        "has an open stream (strict_device_exclusive)",
    )


def _claim_exclusive_device(
    registry: dict[int, object] | None, device: int | None, stream: object
) -> None:
    """Claim *device* for *stream*; raise the -50 error if another holds it."""
    if registry is None or device is None:
        return
    holder = registry.get(device)
    if holder is not None and holder is not stream:
        raise _exclusive_device_busy(device)
    registry[device] = stream


def _release_exclusive_device(
    registry: dict[int, object] | None, device: int | None, stream: object
) -> None:
    """Free *device* if *stream* holds it — closing frees the device."""
    if registry is not None and device is not None and registry.get(device) is stream:
        del registry[device]


class FakeRxStream:
    """Test double: records lifecycle and delivers injected frames."""

    def __init__(
        self,
        *,
        device: int | None = None,
        exclusive: dict[int, object] | None = None,
        block_open: Callable[[], None] | None = None,
    ) -> None:
        self._running = False
        self._callback: Callable[[bytes], None] | None = None
        self._device = device
        self._exclusive = exclusive
        self.started_count = 0
        self.stopped_count = 0
        self.heartbeat_count = 0
        self.fail_on_inject: Exception | None = None
        self._capture_health = _CaptureHealthTracker()
        # MOR-1438: a synchronous hook invoked (on whatever thread calls
        # ``start()``) before the stream is marked running. Lets tests model
        # a genuinely blocking OS-level open (e.g. ``threading.Event.wait``)
        # without touching PortAudio, so ``UsbAudioDriver``'s off-loop
        # timeout handling can be exercised deterministically.
        self.block_open = block_open
        # MOR-1438 (F2 coverage): records which thread called ``stop()``, so
        # tests can assert a late/abandoned handle was closed off the event
        # loop rather than by a synchronous await on the loop thread.
        self.stop_thread_ident: int | None = None
        # MOR-2892: same purpose as ``block_open`` for ``stop()`` — lets
        # tests model a genuinely blocking Pa_StopStream/Pa_CloseStream so
        # the driver's bounded off-loop stop handling can be exercised.
        self.block_stop: Callable[[], None] | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def capture_health(self) -> RxStreamHealth:
        return self._capture_health.snapshot()

    async def start(self, callback: Callable[[bytes], None]) -> None:
        if self._running:
            raise RuntimeError("FakeRxStream already running.")
        if self.block_open is not None:
            self.block_open()
        _claim_exclusive_device(self._exclusive, self._device, self)
        self._callback = callback
        self._running = True
        self.started_count += 1

    async def stop(self) -> None:
        self.stop_thread_ident = threading.get_ident()
        # Recorded before the hook so the off-loop-thread assertion stays
        # meaningful even when the block makes the stop time out.
        if self.block_stop is not None:
            self.block_stop()
        _release_exclusive_device(self._exclusive, self._device, self)
        self._running = False
        self._callback = None
        self.stopped_count += 1

    def inject_frame(
        self,
        frame: bytes,
        *,
        input_overflow: bool = False,
        input_underflow: bool = False,
    ) -> None:
        """Push a frame to the registered callback (test helper).

        If *fail_on_inject* is set, raises it once and clears the flag.
        """
        exc = self.fail_on_inject
        if exc is not None:
            self.fail_on_inject = None
            raise exc
        if input_overflow or input_underflow:
            status = type("_Status", (), {})()
            if input_overflow:
                status.input_overflow = True
            if input_underflow:
                status.input_underflow = True
            self._capture_health.record_status(status, input_only=True)
        if self._callback is not None:
            try:
                self._callback(frame)
            except Exception:
                self._capture_health.record_callback_errors(1)
                raise
            self._capture_health.record_frames_delivered(1)

    def inject_heartbeat(self, frame: bytes = b"\x00\x00") -> None:
        """Advance RX liveness by one synthetic capture tick (MOR-566).

        Opt-in test helper: increments :attr:`heartbeat_count` and, when a
        callback is registered, delivers *frame* (default: one s16le zero
        sample) exactly like a live capture callback would. Tests that never
        call it see no behavior change.
        """
        self.heartbeat_count += 1
        if self._callback is not None:
            try:
                self._callback(frame)
            except Exception:
                self._capture_health.record_callback_errors(1)
                raise
            self._capture_health.record_frames_delivered(1)


class FakeTxStream:
    """Test double: records lifecycle and captures written frames.

    ``written_frames`` keeps every frame ever written, in order. The fake
    also models a real playback queue: ``pending_frames`` holds written
    frames not yet played, :meth:`play` is the fake's "device clock" and
    moves the oldest pending frames to ``played_frames``, and
    :meth:`discard_queued` drops the pending ones (MOR-3760). Like the real
    PortAudio streams (``_PortAudioTxStream.stop`` → ``_clear_buffer``),
    :meth:`stop` clears the pending ring so a restart does not replay
    frames queued before the stop.
    """

    def __init__(
        self,
        *,
        device: int | None = None,
        exclusive: dict[int, object] | None = None,
        block_open: Callable[[], None] | None = None,
        playout: TxPlayoutPolicy = "adaptive",
    ) -> None:
        self._running = False
        self._device = device
        self._exclusive = exclusive
        self.started_count = 0
        self.stopped_count = 0
        self.written_frames: list[bytes] = []
        self.pending_frames: list[bytes] = []
        self.played_frames: list[bytes] = []
        self.discard_events = 0
        self.fail_on_write: OSError | None = None
        self.write_failures = 0
        self.last_error: str | None = None
        # MOR-4013: the playback policy this stream was opened with,
        # recorded for open-call assertions.
        self.playout = playout
        # MOR-1438: see FakeRxStream.block_open.
        self.block_open = block_open
        # MOR-1438 (F2 coverage): see FakeRxStream.stop_thread_ident.
        self.stop_thread_ident: int | None = None

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
            discard_events=self.discard_events,
        )

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("FakeTxStream already running.")
        if self.block_open is not None:
            self.block_open()
        _claim_exclusive_device(self._exclusive, self._device, self)
        self._running = True
        self.started_count += 1

    async def stop(self) -> None:
        self.stop_thread_ident = threading.get_ident()
        _release_exclusive_device(self._exclusive, self._device, self)
        self._running = False
        self.stopped_count += 1
        # Match _PortAudioTxStream.stop (→ _clear_buffer): stop drops the
        # pending playback ring; written_frames/played_frames/discard_events
        # are cumulative records and stay untouched.
        self.pending_frames.clear()

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
        self.pending_frames.append(frame)

    def play(self, count: int | None = None) -> list[bytes]:
        """Move the oldest *count* pending frames (all when None) to played.

        Test helper modelling the device clock: returns the frames the fake
        "played", oldest first.
        """
        moved = (
            len(self.pending_frames)
            if count is None
            else min(count, len(self.pending_frames))
        )
        played = self.pending_frames[:moved]
        del self.pending_frames[:moved]
        self.played_frames.extend(played)
        return played

    async def discard_queued(self) -> int:
        """Drop pending (written, not yet played) frames; return the count."""
        if not self._running:
            return 0
        dropped = len(self.pending_frames)
        if dropped:
            self.pending_frames.clear()
            self.discard_events += 1
        return dropped


class FakeDuplexStream:
    """Test double: one full-duplex stream — RX fan + TX queue (MOR-531).

    Combines :class:`FakeRxStream` (delivers injected capture frames to the
    registered callback) and :class:`FakeTxStream` (captures written playback
    frames) so the same-device duplex path can be exercised without PortAudio.
    """

    def __init__(
        self,
        *,
        device: int | None = None,
        exclusive: dict[int, object] | None = None,
        block_open: Callable[[], None] | None = None,
        tx_playout: TxPlayoutPolicy = "adaptive",
    ) -> None:
        self._running = False
        self._callback: Callable[[bytes], None] | None = None
        self._device = device
        self._exclusive = exclusive
        self.started_count = 0
        self.stopped_count = 0
        self.written_frames: list[bytes] = []
        self.pending_frames: list[bytes] = []
        self.played_frames: list[bytes] = []
        self.discard_events = 0
        self._capture_health = _CaptureHealthTracker()
        # MOR-4013: the TX-leg playback policy this stream was opened
        # with, recorded for open-call assertions.
        self.tx_playout = tx_playout
        # MOR-1573: see FakeRxStream.block_open — lets tests model a
        # genuinely blocking duplex open so UsbAudioDriver.start_duplex's
        # off-loop timeout handling can be exercised deterministically.
        self.block_open = block_open
        # MOR-1573: see FakeRxStream.stop_thread_ident.
        self.stop_thread_ident: int | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def capture_health(self) -> RxStreamHealth:
        return self._capture_health.snapshot()

    @property
    def write_health(self) -> TxStreamHealth:
        return TxStreamHealth(
            frames_queued=len(self.written_frames),
            writes_completed=len(self.written_frames),
            discard_events=self.discard_events,
        )

    async def start(self, callback: Callable[[bytes], None]) -> None:
        if self._running:
            raise RuntimeError("FakeDuplexStream already running.")
        if self.block_open is not None:
            self.block_open()
        _claim_exclusive_device(self._exclusive, self._device, self)
        self._callback = callback
        self._running = True
        self.started_count += 1

    async def stop(self) -> None:
        self.stop_thread_ident = threading.get_ident()
        _release_exclusive_device(self._exclusive, self._device, self)
        self._running = False
        self._callback = None
        self.stopped_count += 1
        # Match _PortAudioDuplexStream.stop (→ _tx._clear_buffer): stop drops
        # the pending TX playback ring; written_frames/played_frames and
        # discard_events are cumulative records and stay untouched.
        self.pending_frames.clear()

    async def write(self, frame: bytes) -> None:
        if not self._running:
            raise RuntimeError("FakeDuplexStream is not running.")
        self.written_frames.append(frame)
        self.pending_frames.append(frame)

    def play(self, count: int | None = None) -> list[bytes]:
        """Move the oldest *count* pending TX frames (all when None) to played.

        Test helper modelling the device clock, exactly like
        :meth:`FakeTxStream.play`.
        """
        moved = (
            len(self.pending_frames)
            if count is None
            else min(count, len(self.pending_frames))
        )
        played = self.pending_frames[:moved]
        del self.pending_frames[:moved]
        self.played_frames.extend(played)
        return played

    async def discard_queued(self) -> int:
        """Drop pending (written, not yet played) TX frames; return the count."""
        if not self._running:
            return 0
        dropped = len(self.pending_frames)
        if dropped:
            self.pending_frames.clear()
            self.discard_events += 1
        return dropped

    def inject_frame(
        self,
        frame: bytes,
        *,
        input_overflow: bool = False,
        input_underflow: bool = False,
    ) -> None:
        """Push an RX capture frame to the registered callback (test helper)."""
        if input_overflow or input_underflow:
            status = type("_Status", (), {})()
            if input_overflow:
                status.input_overflow = True
            if input_underflow:
                status.input_underflow = True
            self._capture_health.record_status(status, input_only=True)
        if self._callback is not None:
            try:
                self._callback(frame)
            except Exception:
                self._capture_health.record_callback_errors(1)
                raise
            self._capture_health.record_frames_delivered(1)


class FakeAudioBackend:
    """Deterministic AudioBackend for tests — no real audio hardware.

    **Tier 2 — Best-effort.** Import path:
    ``from rigplane.audio.backend import FakeAudioBackend``. Implements the
    :class:`AudioBackend` protocol with in-memory :class:`FakeRxStream` /
    :class:`FakeTxStream` doubles so consumer code can be exercised without
    PortAudio or any optional extras.

    ``strict_device_exclusive`` (MOR-566, default OFF — behavior identical to
    before): when ON, opening or starting a second stream (rx/tx/duplex) on a
    device id that already has an open (started, not yet stopped) stream
    raises the -50-shaped :class:`OSError` modeling the macOS CoreAudio AUHAL
    ``paramErr -50`` kill (MOR-531/559). Stopping the holding stream frees
    the device.

    Stability: breaking changes require a CHANGELOG note plus a minor version
    bump per ``docs/api/public-api-surface.md``. No strict semver guarantee.
    """

    def __init__(
        self,
        devices: list[AudioDeviceInfo] | None = None,
        *,
        supported_sample_rates: set[int] | None = None,
        strict_device_exclusive: bool = False,
    ) -> None:
        self._devices: list[AudioDeviceInfo] = devices or []
        self._supported_rates: set[int] = supported_sample_rates or {
            8_000,
            16_000,
            44_100,
            48_000,
            96_000,
        }
        self._strict_device_exclusive = strict_device_exclusive
        self._exclusive_streams: dict[int, object] = {}
        self.rx_streams: list[FakeRxStream] = []
        self.tx_streams: list[FakeTxStream] = []
        self.duplex_streams: list[FakeDuplexStream] = []
        # MOR-1438: applied to every stream opened from here on — lets tests
        # model a genuinely blocking RX/TX open (e.g. a macOS TCC
        # microphone-consent prompt that never renders) without a real
        # backend. ``None`` (default) behaves exactly as before.
        self.block_rx_open: Callable[[], None] | None = None
        self.block_tx_open: Callable[[], None] | None = None
        # MOR-1573: same purpose as block_rx_open/block_tx_open, for the
        # same-device full-duplex open path (UsbAudioDriver.start_duplex).
        self.block_duplex_open: Callable[[], None] | None = None
        # MOR-2892: invoked inside check_sample_rate — lets tests model a
        # genuinely blocking ``Pa_IsFormatSupported`` format probe (the
        # stand-freeze incident path) without a real backend. ``None``
        # (default) behaves exactly as before.
        self.block_probe: Callable[[], None] | None = None

    def _strict_stream_kwargs(self, device: AudioDeviceId) -> dict[str, Any]:
        """Strict-mode plumbing for a new fake stream (MOR-566).

        With ``strict_device_exclusive`` ON, opening a stream on a device
        that already has an open (started, not stopped) stream raises the
        -50-shaped error immediately; otherwise the returned kwargs let the
        stream enforce exclusivity at ``start()``/``stop()`` time. Default
        mode returns no kwargs — streams behave exactly as before.
        """
        if not self._strict_device_exclusive:
            return {}
        if int(device) in self._exclusive_streams:
            raise _exclusive_device_busy(int(device))
        return {"device": int(device), "exclusive": self._exclusive_streams}

    def list_devices(self) -> list[AudioDeviceInfo]:
        return list(self._devices)

    def check_sample_rate(
        self,
        device: AudioDeviceId,
        sample_rate: int,
        *,
        direction: str = "rx",
    ) -> bool:
        if self.block_probe is not None:
            self.block_probe()
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
        stream = FakeRxStream(
            block_open=self.block_rx_open, **self._strict_stream_kwargs(device)
        )
        self.rx_streams.append(stream)
        return stream

    def open_tx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        playout: TxPlayoutPolicy = "adaptive",
    ) -> FakeTxStream:
        if not any(d.id == device for d in self._devices):
            raise ValueError(f"Unknown device id {device}")
        stream = FakeTxStream(
            block_open=self.block_tx_open,
            playout=playout,
            **self._strict_stream_kwargs(device),
        )
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
        tx_playout: TxPlayoutPolicy = "adaptive",
    ) -> FakeDuplexStream:
        if not any(d.id == device for d in self._devices):
            raise ValueError(f"Unknown device id {device}")
        stream = FakeDuplexStream(
            block_open=self.block_duplex_open,
            tx_playout=tx_playout,
            **self._strict_stream_kwargs(device),
        )
        self.duplex_streams.append(stream)
        return stream


__all__ = [
    "AudioBackend",
    "AudioDeviceBusyError",
    "AudioDeviceConfig",
    "AudioDeviceId",
    "AudioDeviceInfo",
    "CAPTURE_MODES",
    "CaptureMode",
    "DuplexStream",
    "FakeAudioBackend",
    "FakeDuplexStream",
    "FakeRxStream",
    "FakeTxStream",
    "PortAudioBackend",
    "RxStreamHealth",
    "RxStream",
    "TxPlayoutPolicy",
    "TxStream",
    "TxStreamHealth",
]
