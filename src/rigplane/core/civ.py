"""CI-V event routing and request tracking utilities."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from .types import CivFrame

_REQUEST_DATA_PREFIX_COMMANDS = frozenset({0x07, 0x25, 0x26})
#: CI-V broadcast address: a transceive frame sent to it answers no request.
_BROADCAST_ADDR = 0x00
_REQUEST_DATA_PREFIX_LENGTHS: dict[tuple[int, int | None], tuple[int, ...]] = {
    (0x1A, 0x00): (3, 4),
}

__all__ = [
    "CivEvent",
    "CivEventType",
    "CivRequestKey",
    "CivRequestTracker",
    "PTT_REPLY_QUIESCENCE_S",
    "OwedReplyEntry",
    "PttReplyLedger",
    "civ_response_matches",
    "is_directed_ptt_reply",
    "iter_civ_frames",
    "read_identity_from_frame",
    "request_key_from_frame",
]

PTT_REPLY_QUIESCENCE_S: float = 2.0


def is_directed_ptt_reply(frame: CivFrame, radio_addr: int) -> bool:
    return (
        frame.to_addr == 0xE0
        and frame.from_addr == radio_addr
        and frame.command == 0x1C
        and frame.sub == 0x00
        and len(frame.data) == 1
        and frame.data[0] in (0x00, 0x01)
    )


@dataclass(slots=True)
class OwedReplyEntry:
    dispatched_at: float | None = None
    answered_by: CivFrame | None = None


class PttReplyLedger:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        answer_window: float,
        quiescence: float = PTT_REPLY_QUIESCENCE_S,
    ) -> None:
        self._clock = clock
        self._lifetime = answer_window + quiescence
        self._quiescence = quiescence
        self._owed: deque[OwedReplyEntry] = deque()
        self._orphan_count = 0
        self._quarantine_until = 0.0

    def _expire(self, now: float) -> None:
        self._owed = deque(
            entry
            for entry in self._owed
            if entry.dispatched_at is None
            or now <= entry.dispatched_at + self._lifetime
        )

    def note_dispatch(self, frame: CivFrame) -> OwedReplyEntry | None:
        now = self._clock()
        self._expire(now)
        if frame.command != 0x1C or frame.sub != 0x00 or frame.data:
            return None
        entry = OwedReplyEntry()
        self._owed.append(entry)
        return entry

    def note_written(self, entry: OwedReplyEntry | None) -> None:
        if entry is not None and entry.dispatched_at is None:
            entry.dispatched_at = self._clock()

    def discard(self, entry: OwedReplyEntry | None) -> None:
        self._owed = deque(owed for owed in self._owed if owed is not entry)

    def note_reply(self, frame: CivFrame) -> OwedReplyEntry | None:
        """Account for a reply already classified by the runtime's D1 gate."""
        now = self._clock()
        self._expire(now)
        # A reply can arrive while the sender is still awaiting serial drain.
        entry = self._owed.popleft() if self._owed else None
        if entry is not None:
            entry.answered_by = frame
            return entry
        self._orphan_count += 1
        self._quarantine_until = now + self._quiescence
        return None

    def eligible(self) -> bool:
        now = self._clock()
        self._expire(now)
        return not self._owed and now >= self._quarantine_until

    @property
    def lifetime(self) -> float:
        return self._lifetime

    @property
    def owed_count(self) -> int:
        return len(self._owed)

    @property
    def orphan_count(self) -> int:
        return self._orphan_count

    @property
    def quarantine_until(self) -> float:
        return self._quarantine_until


class CivEventType(StrEnum):
    """Classified CI-V event categories."""

    ACK = "ack"
    NAK = "nak"
    RESPONSE = "response"


@dataclass(frozen=True, slots=True)
class CivEvent:
    """One routed CI-V event."""

    type: CivEventType
    frame: CivFrame | None = None


@dataclass(frozen=True, slots=True)
class CivRequestKey:
    """Request matcher key for CI-V responses."""

    command: int
    sub: int | None
    receiver: int | None = None
    data_prefix: bytes = b""


@dataclass(slots=True)
class _AckWaiter:
    future: asyncio.Future[CivFrame] | None
    token: int
    created_monotonic: float
    generation: int
    nak_only: bool = False
    response_key: CivRequestKey | None = None
    refused_read: CivRequestKey | None = None
    write_identity: CivRequestKey | None = None
    # Set by ``mark_sent`` when the frame leaves this side (MOR-4384):
    # ``sent_order`` is the wire order, ``sent_monotonic`` opens the
    # answer window. An entry not yet sent claims nothing.
    sent_monotonic: float | None = None
    sent_order: int = 0
    # The data waiter of the same read (a NAK-only waiter's partner).
    pair: asyncio.Future[CivFrame] | None = None


@dataclass(slots=True)
class _PendingRequest:
    key: CivRequestKey
    future: asyncio.Future[CivFrame]
    created_monotonic: float
    generation: int
    sent_monotonic: float | None = None
    sent_order: int = 0
    # The NAK-only waiter of the same read, when it has one.
    pair: asyncio.Future[CivFrame] | None = None


_Entry = _AckWaiter | _PendingRequest

logger = logging.getLogger(__name__)


def _log_sink(event: str, waiter: _AckWaiter, now: float) -> None:
    """Debug record of how a sink left: ``civ.sink.answered|expired|retired``."""
    key = waiter.refused_read or waiter.write_identity or waiter.response_key
    sent = waiter.sent_monotonic if waiter.sent_monotonic is not None else now
    logger.debug("%s key=%s age_s=%.3f", event, key, now - sent)


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
        data_prefix=(
            frame.data
            if (
                frame.command in _REQUEST_DATA_PREFIX_COMMANDS
                or len(frame.data)
                in _REQUEST_DATA_PREFIX_LENGTHS.get((frame.command, frame.sub), ())
            )
            else b""
        ),
    )


def read_identity_from_frame(frame: CivFrame) -> CivRequestKey:
    """The whole identity of an outgoing read, every selector byte kept.

    MOR-4058: :func:`request_key_from_frame` keeps only the selector bytes a
    reply must echo to be matched, so two ``1A 05`` menu reads share one
    response key. A refusal (bare ``FA``) has no payload to tell them apart:
    it must name the read by everything the read carried.
    """
    return CivRequestKey(
        command=frame.command,
        sub=frame.sub,
        receiver=frame.receiver,
        data_prefix=frame.data,
    )


def civ_response_matches(key: CivRequestKey, frame: CivFrame) -> bool:
    """Whether *frame* answers the request described by *key*.

    The one pure CI-V attribution predicate: command and sub-command
    must agree, a keyed receiver must agree, and a keyed data prefix
    must prefix the reply. :meth:`CivRequestTracker._matches` delegates
    here, and single-flight probers reuse it instead of reimplementing
    it; stricter probers add their own selector checks on top.
    """
    if frame.command != key.command:
        return False
    if frame.sub != key.sub:
        return False
    if key.receiver is not None and frame.receiver != key.receiver:
        return False
    if key.data_prefix and not frame.data.startswith(key.data_prefix):
        return False
    return True


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
        self._send_order = 0
        self._generation = 0
        self._stale_ttl = stale_ttl
        self._ack_backlog_size = max(0, ack_backlog_size)
        self._ack_backlog_ttl = max(0.0, ack_backlog_ttl)
        self._stale_cleaned_total = 0
        self._timeout_count = 0
        self._ack_backlog_hits = 0
        self._ack_backlog_drops = 0
        self._ack_orphans = 0
        self._ack_unclaimed_at: float | None = None
        # MOR-4058: reads a refusal (bare FA) retired, oldest first, until
        # ``take_refused_reads`` hands them to the acquisition scheduler.
        self._refused_reads: list[CivRequestKey] = []

    @property
    def pending_count(self) -> int:
        """Number of unresolved pending requests."""
        return len(self._ack_waiters) + len(self._response_waiters)

    def response_pending_count(
        self,
        *,
        now_monotonic: float,
        max_age_s: float,
    ) -> int:
        """Waiters expecting a data response: tracked GETs + keyed poll sinks.

        MOR-2861: the serial link-death watchdog uses this as its "polls are
        outstanding" signal. Plain ACK sinks of fire-and-forget *set* sends
        (``response_key is None``) are excluded: on a healthy but quiet radio
        they would linger until stale-TTL cleanup (10 s) and make pending
        read true long after the last real poll resolved. GET response
        waiters and keyed poll sinks (a read dispatched fire-and-forget, the
        web poller's BACKGROUND shape) resolve within the answer window when
        the radio answers, so their presence while no frame parses is the
        honest "the radio owes us data" evidence — but only while younger
        than ``max_age_s``: a waiter older than the answer window is a lost
        poll (its answer had a full window to arrive), not evidence the
        radio still owes us data, so it must not hold the watchdog's
        silence clock until the 10 s stale cleanup retires it.
        """
        cutoff = now_monotonic - max_age_s
        return sum(
            1 for w in self._response_waiters if w.created_monotonic > cutoff
        ) + sum(
            1
            for waiter in self._ack_waiters
            if waiter.response_key is not None and waiter.created_monotonic > cutoff
        )

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

    @property
    def ack_unclaimed_at(self) -> float | None:
        """Monotonic time a bare FB/FA was last left able to arrive unclaimed.

        An ACK/NAK frame names no command and ``resolve`` hands it to the
        first eligible waiter, so an answer whose waiter is gone — a
        write's FB/FA (MOR-2860) or a read's FA, since a refused read is
        answered with one — can still settle a later waiter.
        """
        return self._ack_unclaimed_at

    def note_ack_unclaimed(self) -> None:
        """Record that a bare FB/FA can still arrive with no waiter."""
        self._ack_unclaimed_at = time.monotonic()

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

    def register_ack(
        self,
        wait: bool = True,
        *,
        consume_backlog: bool = True,
        nak_only: bool = False,
        response_key: CivRequestKey | None = None,
        refused_read: CivRequestKey | None = None,
        write_identity: CivRequestKey | None = None,
        paired_with: asyncio.Future[CivFrame] | None = None,
    ) -> asyncio.Future[CivFrame] | int:
        """Register a pending request that expects an ACK/NAK.

        ``response_key`` is read for a sink (``wait=False``) only; ``resolve``
        says when a data response retires that sink. ``refused_read`` (sinks
        only, :func:`read_identity_from_frame`) is what
        :meth:`take_refused_reads` reports when a refusal retires the sink.
        ``write_identity`` (:func:`read_identity_from_frame` of the outgoing
        write) is what :meth:`resolve_write_echo` matches the radio's echo
        against; it is stored for blocking waiters and sinks alike.
        ``paired_with`` (a blocking NAK-only waiter only) is the data
        waiter of the same read: the two are one request, so when either
        settles the other leaves in the same step (MOR-4384).

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
            if consume_backlog:
                cached = self._pop_matching_ack_backlog(nak_only=nak_only)
            else:
                cached = None
            if cached is not None:
                future.set_result(cached.frame)
                self._ack_backlog_hits += 1
                return future
            self._ack_waiters.append(
                _AckWaiter(
                    future=future,
                    token=token,
                    created_monotonic=created,
                    generation=self._generation,
                    nak_only=nak_only,
                    write_identity=write_identity,
                    pair=paired_with,
                )
            )
            for pending in self._response_waiters:
                if pending.future is paired_with:
                    pending.pair = future
            return future

        self._ack_waiters.append(
            _AckWaiter(
                future=None,
                token=token,
                created_monotonic=created,
                generation=self._generation,
                response_key=response_key,
                refused_read=refused_read,
                write_identity=write_identity,
            )
        )
        return token

    def take_refused_reads(self) -> tuple[CivRequestKey, ...]:
        """Return and forget the reads a refusal retired since the last call.

        MOR-4058: a fire-and-forget read waits on a sink carrying its
        ``response_key``. When a bare ``0xFA`` retires such a sink, the
        radio has answered that read with a refusal: a terminal answer, not
        a lost one. Radios answer in order, so the refusal belongs to the
        oldest sink still open — the same attribution every ACK/NAK here
        gets.
        """
        refused, self._refused_reads = tuple(self._refused_reads), []
        return refused

    def restore_refused_reads(self, keys: Iterable[CivRequestKey]) -> None:
        """Re-queue refused reads no consumer could deliver (MOR-4384).

        :meth:`take_refused_reads` hands refusals to the acquisition
        scheduler; a refusal naming no declared path — or taken where no
        scheduler is installed — is information, not garbage. Dropping it
        erases the only record of which read the radio refused, so
        undelivered keys queue again for the next taker, deduplicated so
        a repeatedly refused read keeps one entry per generation.
        """
        for key in keys:
            if key not in self._refused_reads:
                self._refused_reads.append(key)

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

    def mark_sent(
        self,
        sent_monotonic: float,
        *,
        token: int | None = None,
        futures: tuple[asyncio.Future[CivFrame], ...] = (),
    ) -> None:
        """Stamp a sink (``token``) or blocking waiters as sent (MOR-4384).

        Stamps are taken at the transport submission point, so their order
        is the wire order ``resolve`` attributes by; the time opens the
        entry's answer window.
        """
        self._send_order += 1
        entries: list[_Entry] = [
            *(
                w
                for w in self._ack_waiters
                if (w.token == token if w.future is None else w.future in futures)
            ),
            *(p for p in self._response_waiters if p.future in futures),
        ]
        for entry in entries:
            entry.sent_monotonic = sent_monotonic
            entry.sent_order = self._send_order

    def expire_ack_sinks(
        self, *, answer_window: float, now_monotonic: float | None = None
    ) -> int:
        """Drop fire-and-forget sinks whose own answer window has closed.

        A sink is owed its answer for ``answer_window`` from its send
        stamp (registration until sent); only an ordered answer that passed
        it (``resolve``) retires it sooner. Its late FB/FA could still
        settle a later waiter, so every expiry calls
        :meth:`note_ack_unclaimed` (read by the PTT-release readback check).

        Returns:
            Number of sink entries expired.
        """
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        kept: list[_AckWaiter] = []
        for waiter in self._ack_waiters:
            sent = waiter.sent_monotonic
            sent = waiter.created_monotonic if sent is None else sent
            if waiter.future is not None or now < sent + answer_window:
                kept.append(waiter)
            else:
                _log_sink("civ.sink.expired", waiter, now)
                self.note_ack_unclaimed()
        expired = len(self._ack_waiters) - len(kept)
        self._ack_waiters = kept
        return expired

    def drop_ack_backlog(self) -> int:
        """Drop orphan ACK/NAK backlog without touching active waiters."""
        dropped = len(self._ack_backlog)
        self._ack_backlog.clear()
        self._ack_backlog_drops += dropped
        return dropped

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

    def _pop_matching_ack_backlog(self, *, nak_only: bool) -> _AckBacklogEntry | None:
        """Pop the oldest backlog entry matching an ACK waiter filter."""
        if not self._ack_backlog:
            return None
        if not nak_only:
            return self._ack_backlog.popleft()

        for index, entry in enumerate(self._ack_backlog):
            if entry.frame.command == 0xFA:
                del self._ack_backlog[index]
                return entry
        return None

    def advance_generation(self, exc: Exception | None = None) -> int:
        """Advance generation and fail all pending waiters."""
        if exc is None:
            exc = RuntimeError("CI-V tracker generation advanced")
        self.fail_all(exc)
        self._generation += 1
        return self._generation

    def resolve(
        self,
        event: CivEvent,
        *,
        generation: int | None = None,
        ordered_ingress: bool = False,
    ) -> bool:
        """Resolve a pending request from an incoming event.

        MOR-4384: the answer goes to the oldest sent entry (``mark_sent``
        order, blocking waiters and sinks alike) that can take it: data to
        the oldest key match, FB to the oldest write, FA to the oldest of
        any kind. ``ordered_ingress`` is the transport's contract that
        frames arrive in the order the radio sent them; only then does a
        directed data/FB answer prove every older SINK's answer lost
        (MOR-4209); blocking waiters leave only at their own window or when
        claimed. An FA passes nobody; a broadcast proves nothing.
        """
        if generation is not None and generation != self._generation:
            return False

        frame = event.frame
        if frame is None:
            return False

        current = self._generation
        if event.type in (CivEventType.ACK, CivEventType.NAK):
            self._prune_ack_backlog()
            self._ack_waiters = [
                w for w in self._ack_waiters if w.generation == current
            ]
            winner = self._oldest_sent(
                w
                for w in self._ack_waiters
                if event.type is CivEventType.NAK
                or (not w.nak_only and (w.future is not None or w.response_key is None))
            )
            if winner is not None:
                return self._claim(winner, event, ordered_ingress)

            # Park short-lived orphan ACK/NAK frames, bounded and TTL-pruned.
            # No waiter in ``src/`` claims them: every ``register_ack`` call
            # site there passes ``consume_backlog=False`` or takes a sink
            # token (MOR-1977 unit A).  The deque survives because
            # ``consume_backlog=True`` remains ``register_ack``'s default for
            # callers that do want it, and for the ``ack_backlog_drops``
            # accounting below.  ``ack_orphans`` is counted either way -- it
            # is incremented before the deque is touched at all.
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
            self._response_waiters = [
                p for p in self._response_waiters if p.generation == current
            ]
            # An unsolicited broadcast (transceive) can settle a blocking read
            # it matches, but never a sink (MOR-4209): the read's own answer
            # would later find nothing to retire.
            sinks = [] if frame.to_addr == _BROADCAST_ADDR else self._ack_waiters
            winner = self._oldest_sent(
                [p for p in self._response_waiters if self._matches(p.key, frame)]
                + [
                    w
                    for w in sinks
                    if w.response_key is not None
                    and self._matches(w.response_key, frame)
                ]
            )
            return winner is not None and self._claim(winner, event, ordered_ingress)

        return False

    @staticmethod
    def _oldest_sent(entries: Iterable[_Entry]) -> _Entry | None:
        """The entry sent first; an entry not yet sent claims nothing."""
        sent = [e for e in entries if e.sent_monotonic is not None]
        return min(sent, key=lambda e: e.sent_order) if sent else None

    def _claim(self, winner: _Entry, event: CivEvent, ordered_ingress: bool) -> bool:
        """Settle ``winner`` with the event's frame; apply ordered evidence."""
        frame = event.frame
        assert frame is not None
        self._remove(winner)
        if (
            ordered_ingress
            and frame.to_addr != _BROADCAST_ADDR
            and event.type is not CivEventType.NAK
        ):
            self._retire_sinks_older_than(winner.sent_order)
        if isinstance(winner, _AckWaiter) and winner.future is None:
            _log_sink("civ.sink.answered", winner, time.monotonic())
        if winner.future is not None:
            if not winner.future.done():
                winner.future.set_result(frame)
        elif (
            isinstance(winner, _AckWaiter)
            and event.type is CivEventType.NAK
            and winner.refused_read is not None
        ):
            self._refused_reads.append(winner.refused_read)
        return True

    def _remove(self, entry: _Entry) -> None:
        """Take ``entry`` and the other half of its read off the lists."""
        if isinstance(entry, _AckWaiter):
            self._ack_waiters.remove(entry)
        else:
            self._response_waiters.remove(entry)
        if entry.pair is not None:
            self.unregister(entry.pair)

    def _retire_sinks_older_than(self, order: int) -> None:
        """Retire every sent sink older than ``order``: its answer is lost.

        A blocking waiter is never retired here: it leaves at its own
        window or when an answer claims it (a raw transaction can reach the
        radio after a later send). A write sink notes that its FB/FA can no
        longer be told from a later one.
        """
        kept: list[_AckWaiter] = []
        for waiter in self._ack_waiters:
            if (
                waiter.future is not None
                or waiter.sent_monotonic is None
                or waiter.sent_order >= order
            ):
                kept.append(waiter)
                continue
            _log_sink("civ.sink.retired", waiter, time.monotonic())
            if waiter.response_key is None:
                self.note_ack_unclaimed()
        self._ack_waiters = kept

    def resolve_write_echo(
        self,
        frame: CivFrame,
        *,
        generation: int | None = None,
        ordered_ingress: bool = False,
    ) -> bool:
        """Claim a pending write with the radio's byte-identical echo.

        Some radios answer a SET over LAN with the write's own frame
        (radio to controller) instead of FB. The echo acks the oldest
        current-generation, non-``nak_only`` waiter whose
        ``write_identity`` it matches exactly; a blocking waiter's
        future then resolves with a synthesized FB.

        Any pending read wins: when the frame matches a blocking
        response waiter or a sink carrying ``response_key``, it is not
        an echo and this returns False. An echo names its write: it is
        settled through ``_claim`` like any directed answer (MOR-4384).
        """
        if generation is not None and generation != self._generation:
            return False
        if frame.command in (0xFB, 0xFA):
            return False
        if frame.to_addr == _BROADCAST_ADDR:
            return False
        for pending in self._response_waiters:
            if pending.generation == self._generation and self._matches(
                pending.key, frame
            ):
                return False
        for waiter in self._ack_waiters:
            if (
                waiter.generation == self._generation
                and waiter.response_key is not None
                and self._matches(waiter.response_key, frame)
            ):
                return False
        winner = self._oldest_sent(
            w
            for w in self._ack_waiters
            if w.generation == self._generation
            and not w.nak_only
            and (identity := w.write_identity) is not None
            and frame.command == identity.command
            and frame.sub == identity.sub
            and frame.receiver == identity.receiver
            and frame.data == identity.data_prefix
        )
        if winner is None:
            return False
        ack = CivFrame(to_addr=frame.to_addr, from_addr=frame.from_addr, command=0xFB)
        return self._claim(
            winner, CivEvent(type=CivEventType.ACK, frame=ack), ordered_ingress
        )

    def fail_all(self, exc: Exception) -> None:
        """Fail all pending requests and clear the tracker."""
        for w in self._ack_waiters:
            if w.future is not None and not w.future.done():
                w.future.set_exception(exc)
        self._ack_waiters.clear()
        self._ack_backlog.clear()
        self._refused_reads.clear()

        for pending in self._response_waiters:
            if not pending.future.done():
                pending.future.set_exception(exc)
        self._response_waiters.clear()

    @staticmethod
    def _matches(key: CivRequestKey, frame: CivFrame) -> bool:
        return civ_response_matches(key, frame)
