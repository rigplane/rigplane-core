"""Coalesced thread→event-loop handoff for per-frame audio callbacks.

A plain per-frame ``loop.call_soon_threadsafe(deliver, frame)`` wakes the
event loop once per frame — each call writes the loop's self-pipe
(``_write_to_self`` in py-spy profiles, MOR-4227). :class:`CoalescedLoopHandoff`
keeps that handoff's latency when the loop is idle but collapses bursts: while
a drain is already scheduled or draining, arriving frames only append to a
buffer, so one wake-up delivers every frame that arrived before it started
(later arrivals ride the next wake-up).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable

__all__ = ["CoalescedLoopHandoff"]

logger = logging.getLogger(__name__)


class CoalescedLoopHandoff:
    """Deliver audio frames from producer threads to one event loop.

    Ordering and delivery guarantees (pinned in
    ``tests/test_audio_loop_handoff_mor4227.py``):

    - No frame is lost or duplicated: every append is followed either by
      this call's own drain scheduling or lands while a drain is still
      pending/draining, and only the loop thread pops the buffer.
    - FIFO order is preserved: one buffer, popped front to back.
    - Idle traffic keeps the plain handoff's latency: the first frame after
      a quiet period schedules exactly one drain and is delivered by that
      first scheduled callback — no extra scheduling round, so latency
      never grows by a frame.
    - Every drain is bounded to the frames present when it started
      (round-1 P1): frames appended while it delivers ride the next
      scheduled drain, so a producer keeping pace with delivery cannot
      hold the drain in one callback — the event loop runs timers,
      cancellation and other callbacks between drains.
    - A raising deliverable does not lose the remaining frames: each frame
      is isolated like its own ``call_soon_threadsafe`` callback would be
      (logged, not propagated into the loop's exception handler).
    - A closed loop is terminal (round-1 P2): the first submit that
      cannot schedule raises ``RuntimeError``, every frame buffered so
      far is released (it could never be delivered), and later submits
      are dropped silently — a capture feeding a dead loop cannot grow
      the buffer without bound.

    Thread-safe for any number of producer threads. Producer code already
    running on the loop's thread should call its deliverable directly
    instead — that path needs no wake-up at all.
    """

    __slots__ = (
        "_closed",
        "_deliver",
        "_drain_scheduled",
        "_loop",
        "_lock",
        "_pending",
    )

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        deliver: Callable[[bytes], None],
    ) -> None:
        self._loop = loop
        self._deliver = deliver
        self._pending: deque[bytes] = deque()
        self._lock = threading.Lock()
        self._drain_scheduled = False
        self._closed = False

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The loop frames are drained on."""
        return self._loop

    def submit(self, frame: bytes) -> None:
        """Buffer *frame* and schedule one drain if none is pending.

        Raises ``RuntimeError`` when the loop is closed; that closure is
        terminal for an asyncio loop (it never reopens): the handoff then
        releases its buffered frames and drops every later submit.
        """
        with self._lock:
            if self._closed:
                return
            self._pending.append(frame)
            if self._drain_scheduled:
                return
            self._drain_scheduled = True
        try:
            self._loop.call_soon_threadsafe(self._drain)
        except RuntimeError:
            # The loop is closed and stays closed: no drain can ever run.
            # Release the frames that can now never be delivered and drop
            # further submits, so a capture feeding a dead loop cannot
            # grow the buffer without bound (round-1 P2). The raise still
            # tells this caller the frame went nowhere.
            with self._lock:
                self._closed = True
                self._pending.clear()
                self._drain_scheduled = False
            raise

    def _drain(self) -> None:
        # Clear the flag and swap the batch out in ONE locked step: a
        # producer appending while this drain delivers then sees the flag
        # clear, schedules a fresh drain, and its frame rides that one.
        # The drain delivers only the frames present when it began
        # (round-1 P1): a producer keeping pace with delivery can no
        # longer keep this callback consuming the live buffer — the loop
        # gets control back after every batch, so timers, cancellation
        # and other callbacks run between drains.
        with self._lock:
            self._drain_scheduled = False
            batch = self._pending
            self._pending = deque()
        for frame in batch:
            try:
                self._deliver(frame)
            except Exception:
                logger.error("loop handoff: deliver failed", exc_info=True)
