"""CI-V event routing and request tracking utilities."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from .types import CivFrame

__all__ = [
    "CivEvent",
    "CivEventType",
    "CivRequestKey",
    "CivRequestTracker",
    "iter_civ_frames",
    "request_key_from_frame",
]


class CivEventType(StrEnum):
    """Classified CI-V event categories."""

    ACK = "ack"
    NAK = "nak"
    RESPONSE = "response"
    SCOPE_CHUNK = "scope_chunk"
    SCOPE_FRAME = "scope_frame"


@dataclass(frozen=True, slots=True)
class CivEvent:
    """One routed CI-V event."""

    type: CivEventType
    frame: CivFrame | None = None
    receiver: int | None = None


@dataclass(frozen=True, slots=True)
class CivRequestKey:
    """Request matcher key for CI-V responses."""

    command: int
    sub: int | None
    receiver: int | None = None


@dataclass(slots=True)
class _AckWaiter:
    future: asyncio.Future[CivFrame] | None
    token: int
    created_monotonic: float
    generation: int


@dataclass(slots=True)
class _PendingRequest:
    key: CivRequestKey
    future: asyncio.Future[CivFrame]
    created_monotonic: float
    generation: int


@dataclass(slots=True)
class _AckBacklogEntry:
    frame: CivFrame
    created_monotonic: float
    generation: int


def request_key_from_frame(frame: CivFrame) -> CivRequestKey:
    """Build request key from an outgoing CI-V frame."""
    return CivRequestKey(
        command=frame.command,
        sub=frame.sub,
        receiver=frame.receiver,
    )


def iter_civ_frames(payload: bytes) -> Iterator[bytes]:
    """Yield CI-V frames found in an arbitrary payload buffer."""
    idx = 0
    while idx < len(payload) - 4:
        if payload[idx] != 0xFE or payload[idx + 1] != 0xFE:
            idx += 1
            continue
        fd_pos = payload.find(b"\xfd", idx + 4)
        if fd_pos < 0:
            break
        yield payload[idx : fd_pos + 1]
        idx = fd_pos + 1


class CivRequestTracker:
    """Tracks pending requests and resolves matching CI-V responses."""

    def __init__(
        self,
        *,
        stale_ttl: float = 10.0,
        ack_backlog_size: int = 16,
        ack_backlog_ttl: float = 1.0,
    ) -> None:
        self._ack_waiters: list[_AckWaiter] = []
        self._response_waiters: list[_PendingRequest] = []
        self._ack_backlog: deque[_AckBacklogEntry] = deque()
        self._next_ack_token = 1
        self._generation = 0
        self._stale_ttl = stale_ttl
        self._ack_backlog_size = max(0, ack_backlog_size)
        self._ack_backlog_ttl = max(0.0, ack_backlog_ttl)
        self._stale_cleaned_total = 0
        self._timeout_count = 0
        self._ack_backlog_hits = 0
        self._ack_backlog_drops = 0
        self._ack_orphans = 0

    @property
    def pending_count(self) -> int:
        """Number of unresolved pending requests."""
        return len(self._ack_waiters) + len(self._response_waiters)

    @property
    def ack_sink_count(self) -> int:
        """Number of fire-and-forget ACK sink waiters currently tracked."""
        return sum(1 for w in self._ack_waiters if w.future is None)

    @property
    def generation(self) -> int:
        """Current generation for CI-V request lifecycle."""
        return self._generation

    @property
    def stale_cleaned_total(self) -> int:
        """Number of stale waiters removed by tracker GC."""
        return self._stale_cleaned_total

    @property
    def timeout_count(self) -> int:
        """Total timeout count observed by tracker/request flow."""
        return self._timeout_count

    def note_timeout(self) -> None:
        """Record one timeout in tracker statistics."""
        self._timeout_count += 1

    def snapshot_stats(self) -> dict[str, int]:
        """Return tracker counters for monitoring."""
        return {
            "active_waiters": self.pending_count,
            "stale_cleaned": self._stale_cleaned_total,
            "timeouts": self._timeout_count,
            "generation": self._generation,
            "ack_backlog_hits": self._ack_backlog_hits,
            "ack_backlog_drops": self._ack_backlog_drops,
            "ack_orphans": self._ack_orphans,
        }

    def register_ack(self, wait: bool = True) -> asyncio.Future[CivFrame] | int:
        """Register a pending request that expects an ACK/NAK.

        Returns:
            - Future when ``wait=True`` (caller awaits ACK/NAK)
            - Integer sink token when ``wait=False`` (fire-and-forget sink)
        """
        token = self._next_ack_token
        self._next_ack_token += 1
        created = time.monotonic()
        self._prune_ack_backlog(now_monotonic=created)

        if wait:
            future: asyncio.Future[CivFrame] = (
                asyncio.get_running_loop().create_future()
            )
            if self._ack_backlog:
                cached = self._ack_backlog.popleft()
                future.set_result(cached.frame)
                self._ack_backlog_hits += 1
                return future
            self._ack_waiters.append(
                _AckWaiter(
                    future=future,
                    token=token,
                    created_monotonic=created,
                    generation=self._generation,
                )
            )
            return future

        self._ack_waiters.append(
            _AckWaiter(
                future=None,
                token=token,
                created_monotonic=created,
                generation=self._generation,
            )
        )
        return token

    def register_response(self, key: CivRequestKey) -> asyncio.Future[CivFrame]:
        """Register a pending request that expects a specific data response."""
        created = time.monotonic()
        future: asyncio.Future[CivFrame] = asyncio.get_running_loop().create_future()
        self._response_waiters.append(
            _PendingRequest(
                key=key,
                future=future,
                created_monotonic=created,
                generation=self._generation,
            )
        )
        return future

    def unregister(self, future: asyncio.Future[CivFrame]) -> None:
        """Remove a request future from pending list."""
        self._ack_waiters = [w for w in self._ack_waiters if w.future is not future]
        self._response_waiters = [
            w for w in self._response_waiters if w.future is not future
        ]

    def unregister_ack_sink(self, token: int) -> bool:
        """Remove a fire-and-forget ACK sink by token."""
        for i, waiter in enumerate(self._ack_waiters):
            if waiter.token == token and waiter.future is None:
                self._ack_waiters.pop(i)
                return True
        return False

    def drop_ack_sinks(self) -> int:
        """Drop all fire-and-forget ACK sinks.

        Returns:
            Number of dropped sink entries.
        """
        before = len(self._ack_waiters)
        self._ack_waiters = [w for w in self._ack_waiters if w.future is not None]
        return before - len(self._ack_waiters)

    def cleanup_stale(self, *, now_monotonic: float | None = None) -> int:
        """Drop stale waiters that exceeded the TTL budget."""
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        cutoff = now - self._stale_ttl
        cleaned = 0

        fresh_ack: list[_AckWaiter] = []
        for waiter in self._ack_waiters:
            if waiter.created_monotonic > cutoff:
                fresh_ack.append(waiter)
                continue
            cleaned += 1
            if waiter.future is not None and not waiter.future.done():
                waiter.future.set_exception(
                    asyncio.TimeoutError("CI-V ACK waiter expired")
                )
                self._timeout_count += 1
        self._ack_waiters = fresh_ack

        fresh_response: list[_PendingRequest] = []
        for pending in self._response_waiters:
            if pending.created_monotonic > cutoff:
                fresh_response.append(pending)
                continue
            cleaned += 1
            if not pending.future.done():
                pending.future.set_exception(
                    asyncio.TimeoutError("CI-V response waiter expired")
                )
                self._timeout_count += 1
        self._response_waiters = fresh_response

        self._stale_cleaned_total += cleaned
        return cleaned

    def _prune_ack_backlog(self, *, now_monotonic: float | None = None) -> None:
        """Drop expired/backlevel ACK backlog entries."""
        if not self._ack_backlog:
            return

        now = now_monotonic if now_monotonic is not None else time.monotonic()
        cutoff = now - self._ack_backlog_ttl
        while self._ack_backlog:
            head = self._ack_backlog[0]
            if head.generation != self._generation:
                self._ack_backlog.popleft()
                self._ack_backlog_drops += 1
                continue
            if self._ack_backlog_ttl > 0.0 and head.created_monotonic > cutoff:
                break
            self._ack_backlog.popleft()
            self._ack_backlog_drops += 1

    def advance_generation(self, exc: Exception | None = None) -> int:
        """Advance generation and fail all pending waiters."""
        if exc is None:
            exc = RuntimeError("CI-V tracker generation advanced")
        self.fail_all(exc)
        self._generation += 1
        return self._generation

    def resolve(self, event: CivEvent, *, generation: int | None = None) -> bool:
        """Resolve a pending request from an incoming event."""
        if generation is not None and generation != self._generation:
            return False

        frame = event.frame
        if frame is None:
            return False

        if event.type in (CivEventType.ACK, CivEventType.NAK):
            self._prune_ack_backlog()
            while self._ack_waiters:
                waiter = self._ack_waiters.pop(0)
                if waiter.generation != self._generation:
                    continue
                if waiter.future is not None and not waiter.future.done():
                    waiter.future.set_result(frame)
                    return True
                elif waiter.future is None:
                    # Successfully sunk an ACK for a fire-and-forget request
                    return True

            # Keep short-lived orphan ACKs so strict waiters can consume them.
            self._ack_orphans += 1
            if self._ack_backlog_size <= 0:
                self._ack_backlog_drops += 1
                return False

            if len(self._ack_backlog) >= self._ack_backlog_size:
                self._ack_backlog.popleft()
                self._ack_backlog_drops += 1
            self._ack_backlog.append(
                _AckBacklogEntry(
                    frame=frame,
                    created_monotonic=time.monotonic(),
                    generation=self._generation,
                )
            )
            return True

        if event.type == CivEventType.RESPONSE:
            i = 0
            while i < len(self._response_waiters):
                pending = self._response_waiters[i]
                if pending.generation != self._generation:
                    self._response_waiters.pop(i)
                    continue
                if self._matches(pending.key, frame):
                    self._response_waiters.pop(i)
                    if not pending.future.done():
                        pending.future.set_result(frame)
                    return True
                i += 1
            return False

        return False

    def fail_all(self, exc: Exception) -> None:
        """Fail all pending requests and clear the tracker."""
        for w in self._ack_waiters:
            if w.future is not None and not w.future.done():
                w.future.set_exception(exc)
        self._ack_waiters.clear()
        self._ack_backlog.clear()

        for pending in self._response_waiters:
            if not pending.future.done():
                pending.future.set_exception(exc)
        self._response_waiters.clear()

    @staticmethod
    def _matches(key: CivRequestKey, frame: CivFrame) -> bool:
        if frame.command != key.command:
            return False
        if frame.sub != key.sub:
            return False
        if key.receiver is not None and frame.receiver != key.receiver:
            return False
        return True
