"""Coalesced thread→event-loop handoff for per-frame audio callbacks.

A plain per-frame ``loop.call_soon_threadsafe(deliver, frame)`` wakes the
event loop once per frame — each call writes the loop's self-pipe
(``_write_to_self`` in py-spy profiles, MOR-4227). :class:`CoalescedLoopHandoff`
keeps that handoff's latency when the loop is idle but collapses bursts: while
a drain is already scheduled or draining, arriving frames only append to a
buffer, so one wake-up delivers every frame that arrived meanwhile.
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
    - A raising deliverable does not lose the remaining frames: each frame
      is isolated like its own ``call_soon_threadsafe`` callback would be
      (logged, not propagated into the loop's exception handler).

    Thread-safe for any number of producer threads. Producer code already
    running on the loop's thread should call its deliverable directly
    instead — that path needs no wake-up at all.
    """

    __slots__ = ("_deliver", "_drain_scheduled", "_loop", "_lock", "_pending")

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

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The loop frames are drained on."""
        return self._loop

    def submit(self, frame: bytes) -> None:
        """Buffer *frame* and schedule one drain if none is pending."""
        with self._lock:
            self._pending.append(frame)
            if self._drain_scheduled:
                return
            self._drain_scheduled = True
        try:
            self._loop.call_soon_threadsafe(self._drain)
        except RuntimeError:
            # The loop closed: no drain can ever run. Undo the scheduled
            # state so a later submit against a live loop still schedules.
            with self._lock:
                self._drain_scheduled = False
            raise

    def _drain(self) -> None:
        # Clear the flag BEFORE popping: a producer appending while this
        # drain is still popping then schedules a fresh drain, so its frame
        # is delivered either by this drain's remaining pops or by that one.
        with self._lock:
            self._drain_scheduled = False
        while True:
            with self._lock:
                if not self._pending:
                    return
                frame = self._pending.popleft()
            try:
                self._deliver(frame)
            except Exception:
                logger.error("loop handoff: deliver failed", exc_info=True)
