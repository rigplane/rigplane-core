"""Live RX level probe for pairing-refusal capture candidates (MOR-3823).

When USB audio pairing refuses (:mod:`rigplane.audio._usb_pairing`), the
broadcaster latches the refusal with its candidate endpoints and
``GET /api/v1/info`` exposes them. This module measures a live capture
level for each latched ``capture`` candidate — a short listen through the
SAME :class:`~rigplane.audio.usb_driver.UsbAudioDriver` machinery a real RX
start uses (``_find_by_override`` stable-id resolution,
``_resolve_stream_contract`` format probe, and the bounded
``open_stream_bounded`` open) — so a device chooser can show which
candidate actually carries the radio's audio.

Safety bounds (any client of the web port can trigger this; it opens host
input devices):

- Only the latched candidates are opened — the caller hands the set in;
  nothing here enumerates or opens any other device.
- One candidate at a time, and one probe run at a time: a second run while
  one is in flight is refused with :class:`CandidateProbeBusyError` (the
  route answers 429).
- A finished result is reused for ``reuse_s`` seconds (hammering guard).
- The whole run is bounded by ``overall_timeout_s``; each candidate's open
  and close are bounded by the driver's capture-open timeout
  (``open_timeout_s``), which never blocks the event loop.

The probe is read-only: it never touches the refusal latch, the persisted
device selection, or the RX relay. Results carry no exception text, host
paths, or driver strings; failures are logged on the host at debug level.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ._usb_pairing import AudioCandidate
from .bridge import _INT16_MAX, _pcm16le_samples
from .usb_driver import UsbAudioDriver, _is_silent_frame

logger = logging.getLogger(__name__)

PROBE_CAPTURE_WINDOW_S = 0.4
"""How long one candidate is listened to."""

PROBE_OPEN_TIMEOUT_S = 1.0
"""Per-candidate bound on each bounded capture open/stop."""

PROBE_OVERALL_TIMEOUT_S = 3.0
"""Whole-call bound across every candidate."""

PROBE_RESULT_REUSE_S = 1.0
"""How long a finished result is reused before probing again."""

CandidateLevelState = Literal["ok", "silent", "busy", "error"]


@dataclass(frozen=True, slots=True)
class CandidateLevel:
    """Measured RX level for one capture candidate.

    ``peak_dbfs`` is the peak sample level over the capture window in dBFS;
    ``None`` unless the state is ``ok``.
    """

    stable_id: str
    state: CandidateLevelState
    peak_dbfs: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "stableId": self.stable_id,
            "state": self.state,
            "peakDbfs": self.peak_dbfs,
        }


class CandidateProbeBusyError(RuntimeError):
    """Raised when a probe run is already in flight (one at a time)."""


DriverFactory = Callable[[str, float], UsbAudioDriver]
"""Builds the per-candidate probe driver from ``(stable_id, open_timeout)``.

The default constructs a real :class:`UsbAudioDriver`; tests inject one
bound to a ``FakeAudioBackend``.
"""


def _default_driver_factory(stable_id: str, open_timeout: float) -> UsbAudioDriver:
    return UsbAudioDriver(rx_device=stable_id, capture_open_timeout=open_timeout)


def _classify_frames(stable_id: str, frames: list[bytes]) -> CandidateLevel:
    """Reduce one candidate's captured frames to a level state.

    ``silent`` reuses the bit-exact-zero semantics of
    ``usb_driver._is_silent_frame``; ``busy``/open failures are classified
    by the caller. A stream that opened but delivered nothing (or only
    bytes that cannot form a whole s16 sample) is an ``error``.
    """
    non_empty = [frame for frame in frames if frame]
    if not non_empty:
        return CandidateLevel(stable_id, "error")
    if all(_is_silent_frame(frame) for frame in non_empty):
        return CandidateLevel(stable_id, "silent")
    samples = _pcm16le_samples(b"".join(non_empty))
    if not samples:
        return CandidateLevel(stable_id, "error")
    peak = max(abs(sample) for sample in samples)
    peak_dbfs = min(0.0, 20.0 * math.log10(peak / _INT16_MAX))
    return CandidateLevel(stable_id, "ok", round(peak_dbfs, 1))


class CandidateLevelProbe:
    """Sequential, bounded, read-only level probe over latched candidates.

    One run at a time (a concurrent call gets
    :class:`CandidateProbeBusyError`); a finished run's result is reused for
    ``reuse_s`` seconds when the candidate set is unchanged. The run never
    starts the RX relay and never changes the refusal latch or the persisted
    device selection.
    """

    def __init__(
        self,
        *,
        driver_factory: DriverFactory = _default_driver_factory,
        clock: Callable[[], float] = time.monotonic,
        capture_window_s: float = PROBE_CAPTURE_WINDOW_S,
        open_timeout_s: float = PROBE_OPEN_TIMEOUT_S,
        overall_timeout_s: float = PROBE_OVERALL_TIMEOUT_S,
        reuse_s: float = PROBE_RESULT_REUSE_S,
    ) -> None:
        self._driver_factory = driver_factory
        self._clock = clock
        self._capture_window_s = capture_window_s
        self._open_timeout_s = open_timeout_s
        self._overall_timeout_s = overall_timeout_s
        self._reuse_s = reuse_s
        self._in_flight = False
        self._last: tuple[tuple[str, ...], float, tuple[CandidateLevel, ...]] | None = (
            None
        )

    @property
    def in_flight(self) -> bool:
        """True while a probe run is executing."""
        return self._in_flight

    async def probe(
        self, candidates: tuple[AudioCandidate, ...]
    ) -> tuple[CandidateLevel, ...]:
        """Measure each ``capture`` candidate once, in latch order.

        Only *candidates* (the latched refusal set) are opened. ``render``
        candidates are skipped: a playback endpoint carries no RX level.
        """
        capture = tuple(c for c in candidates if c.direction == "capture")
        key = tuple(c.stable_id for c in capture)
        last = self._last
        if (
            last is not None
            and last[0] == key
            and self._clock() - last[1] < self._reuse_s
        ):
            return last[2]
        if self._in_flight:
            raise CandidateProbeBusyError("a candidate level probe is already running")
        self._in_flight = True
        try:
            levels = await self._run(capture)
        finally:
            self._in_flight = False
        self._last = (key, self._clock(), levels)
        return levels

    async def _run(
        self, capture: tuple[AudioCandidate, ...]
    ) -> tuple[CandidateLevel, ...]:
        deadline = self._clock() + self._overall_timeout_s
        levels: list[CandidateLevel] = []
        for candidate in capture:
            remaining = deadline - self._clock()
            if remaining <= 0:
                logger.debug(
                    "candidate-levels: overall deadline exhausted before a candidate"
                )
                levels.append(CandidateLevel(candidate.stable_id, "error"))
                continue
            try:
                levels.append(
                    await asyncio.wait_for(
                        self._probe_one(candidate), timeout=remaining
                    )
                )
            except TimeoutError:
                logger.debug(
                    "candidate-levels: probe of a candidate exceeded the overall deadline"
                )
                levels.append(CandidateLevel(candidate.stable_id, "error"))
        return tuple(levels)

    async def _probe_one(self, candidate: AudioCandidate) -> CandidateLevel:
        """Open one candidate, listen for the window, close it, classify."""
        driver = self._driver_factory(candidate.stable_id, self._open_timeout_s)
        frames: list[bytes] = []
        try:
            await driver.start_rx(frames.append)
        except OSError:
            # The device is already open elsewhere (the AUHAL -50 class).
            logger.debug("candidate-levels: candidate device is busy", exc_info=True)
            return CandidateLevel(candidate.stable_id, "busy")
        except Exception:
            logger.debug(
                "candidate-levels: candidate device failed to open", exc_info=True
            )
            return CandidateLevel(candidate.stable_id, "error")
        try:
            await asyncio.sleep(self._capture_window_s)
        finally:
            try:
                await driver.stop_rx()
            except Exception:
                logger.debug(
                    "candidate-levels: probe stream failed to close", exc_info=True
                )
        return _classify_frames(candidate.stable_id, frames)


__all__ = [
    "CandidateLevel",
    "CandidateLevelProbe",
    "CandidateLevelState",
    "CandidateProbeBusyError",
    "PROBE_CAPTURE_WINDOW_S",
    "PROBE_OPEN_TIMEOUT_S",
    "PROBE_OVERALL_TIMEOUT_S",
    "PROBE_RESULT_REUSE_S",
]
