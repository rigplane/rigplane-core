"""A WAV file as the IC-7610 simulator's RX audio (testkit, dev-only).

``WavSource`` (MOR-3603) loads a WAV file at construction, converts it
to the audio port's fixed output format — 48 kHz stereo s16le — and
serves :class:`rigplane_testkit.icom.simulator_audio.AudioPortMixin`
one 3840-byte burst per 20 ms, so a downstream decoder (WSJT-X on the
dogfood bench) hears the recording instead of the synthetic byte ramp.

Resampling is linear interpolation (``numpy.interp``) over the whole
file at load time, rounded to int16 with clipping: test quality, not
mastering quality. Mono input is duplicated to both channels; 48 kHz
stereo passes through bit-exactly. Anything else — non-16-bit PCM, more
than two channels, a rate outside 8000..96000, an empty file — raises
``ValueError`` (no silent conversion); a missing path raises
``FileNotFoundError``.

Alignment: ``start="slot"`` holds the file's first sample for the next
UTC slot boundary (``slot_s`` seconds, default 15 — FT8's slot) counted
from :meth:`WavSource.reset`, which the simulator calls when the audio
stream (re)starts; bursts before the boundary are silence, and a clock
within 20 ms of a boundary starts immediately. With ``loop=True`` the
file wraps at its end — for FT8 keep the recording a whole number of
slots long so every wrap stays aligned.

Do NOT commit recordings containing third-party callsigns: test WAVs
are generated into ``tmp_path`` at runtime.
"""

from __future__ import annotations

import math
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np

from rigplane_testkit.icom.simulator_audio import (
    _AUDIO_BURST_PERIOD_S,
    _AUDIO_BURST_PAYLOADS,
)

# One 20 ms RX burst: 960 frames of 48 kHz stereo s16le (1364+1364+1112).
RX_BURST_BYTES = sum(_AUDIO_BURST_PAYLOADS)

_TARGET_RATE = 48000
_FRAMES_PER_BURST = RX_BURST_BYTES // 4
_SILENCE = bytes(RX_BURST_BYTES)
# Within 20 ms of a slot boundary (modulo): start immediately.
_SLOT_START_EPS_S = 0.020


class RxSource(Protocol):
    """Anything that can feed the simulator's RX bursts (see ``WavSource``)."""

    def reset(self) -> None: ...

    def read_burst(self) -> bytes: ...


class WavSource:
    """A WAV file as the simulator's RX audio (pure data, no sockets/asyncio).

    The audio mixin pulls one burst per 20 ms slot via :meth:`read_burst`;
    :meth:`reset` restarts the file and recomputes the slot wait. See the
    module docstring for the conversion and alignment rules.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        loop: bool = False,
        start: str = "now",
        slot_s: float = 15.0,
        utc: Callable[[], float] = time.time,
    ) -> None:
        if start not in ("now", "slot"):
            raise ValueError(f"start must be 'now' or 'slot', got {start!r}")
        if slot_s <= 0:
            raise ValueError(f"slot_s must be positive, got {slot_s}")
        self._loop = loop
        self._start = start
        self._slot_s = slot_s
        self._utc = utc
        self._pcm: bytes = self._load(path)  # 48 kHz stereo s16le
        self._frames = len(self._pcm) // 4
        self.reset()

    @staticmethod
    def _load(path: str | Path) -> bytes:
        """Read the WAV and convert it to 48 kHz stereo s16le bytes."""
        with wave.open(str(path), "rb") as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            frames = w.getnframes()
            if w.getcomptype() != "NONE":
                raise ValueError(f"{path}: compressed WAV (need PCM)")
            if width != 2:
                raise ValueError(f"{path}: {width * 8}-bit samples (need 16-bit PCM)")
            if channels not in (1, 2):
                raise ValueError(f"{path}: {channels} channels (need 1 or 2)")
            if not 8000 <= rate <= 96000:
                raise ValueError(f"{path}: sample rate {rate} (need 8000..96000)")
            if frames < 1:
                raise ValueError(f"{path}: no frames (empty file)")
            raw = w.readframes(frames)

        src = np.frombuffer(raw, dtype="<i2").astype(np.float64)
        n_out = int(round(frames * _TARGET_RATE / rate))
        x_src = np.arange(frames, dtype=np.float64)
        x_out = np.arange(n_out, dtype=np.float64) * (rate / _TARGET_RATE)
        if channels == 1:
            left = right = np.interp(x_out, x_src, src)
        else:
            left = np.interp(x_out, x_src, src[0::2])
            right = np.interp(x_out, x_src, src[1::2])
        stereo = np.empty(n_out * 2, dtype=np.float64)
        stereo[0::2] = left
        stereo[1::2] = right
        return np.clip(np.rint(stereo), -32768, 32767).astype("<i2").tobytes()

    def reset(self) -> None:
        """Restart from the file head and recompute the slot wait."""
        self._pos = 0  # frames already served
        self._silent_bursts = 0
        if self._start == "slot":
            phase = self._utc() % self._slot_s
            wait_s = 0.0 if phase < _SLOT_START_EPS_S else self._slot_s - phase
            self._silent_bursts = math.ceil(wait_s / _AUDIO_BURST_PERIOD_S)

    def read_burst(self) -> bytes:
        """The next 20 ms: exactly ``RX_BURST_BYTES`` bytes — silence
        before the slot start and, unless ``loop``, after the file's end
        (a truncated tail is padded with silence)."""
        if self._silent_bursts > 0:
            self._silent_bursts -= 1
            return _SILENCE
        pos = self._pos
        if pos >= self._frames:
            if not self._loop:
                return _SILENCE
            pos = 0  # loop: wrap to the file start
        chunk = self._pcm[pos * 4 : (pos + _FRAMES_PER_BURST) * 4]
        self._pos = pos + _FRAMES_PER_BURST
        if len(chunk) == RX_BURST_BYTES:
            return chunk
        if not self._loop:
            return chunk.ljust(RX_BURST_BYTES, b"\x00")
        # Loop wrap mid-burst: the truncated tail is followed by the
        # file's head, and playback continues from there.
        head = self._pcm[: RX_BURST_BYTES - len(chunk)]
        self._pos = len(head) // 4
        return chunk + head
