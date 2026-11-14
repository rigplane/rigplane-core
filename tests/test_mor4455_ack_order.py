"""MOR-4455: bare FB/FA attributed by send order, not by clock.

Proven defect: on a slow link a blocking write registers its waiter after
``_drain_ack_sinks_before_blocking`` presumed the still-open read sinks lost
(fixed 0.5 s budget) and dropped them. The late FA of the refused read then
reached ``CivRequestTracker.resolve``, which handed the bare FA to the oldest
eligible waiter — the set_frequency write — raising ``CommandRejectedError``.

Failing-first: every test below fails on the pre-fix head (the sink is dropped
at the drain, so the late FA/FB settles the wrong waiter, and the new
tombstone/RTT APIs do not exist) and passes once presumed-lost sinks become
tombstones attributed by send time with the read/write kind rule.
"""

from __future__ import annotations

import pytest

from rigplane.civ import (
    CivEvent,
    CivEventType,
    CivRequestKey,
    CivRequestTracker,
)
from rigplane.types import CivFrame

_CTRL = 0xE0
_RADIO = 0x98
_FOREIGN_CTRL = 0xE1

_READ = CivRequestKey(command=0x03, sub=None)
_METER = CivRequestKey(command=0x15, sub=0x02)
_WRITE_KEY = CivRequestKey(command=0x05, sub=None)
_PTT = CivRequestKey(command=0x1C, sub=0x00)


def _fb(*, to_addr: int = _CTRL) -> CivFrame:
    return CivFrame(to_addr=to_addr, from_addr=_RADIO, command=0xFB, sub=None, data=b"")


def _fa(*, to_addr: int = _CTRL) -> CivFrame:
    return CivFrame(to_addr=to_addr, from_addr=_RADIO, command=0xFA, sub=None, data=b"")


def _response(
    command: int, data: bytes = b"", *, sub: int | None = None, to_addr: int = _CTRL
) -> CivFrame:
    return CivFrame(
        to_addr=to_addr, from_addr=_RADIO, command=command, sub=sub, data=data
    )


def _clock(monkeypatch: pytest.MonkeyPatch, start: float = 1000.0) -> list[float]:
    now = [start]
    monkeypatch.setattr("rigplane.civ.time.monotonic", lambda: now[0])
    return now


def _tombstone_read(
    tracker: CivRequestTracker, key: CivRequestKey, sent_at: float
) -> None:
    token = tracker.register_ack(wait=False, response_key=key, refused_read=key)
    assert isinstance(token, int)
    tracker.mark_ack_sink_sent(token, sent_monotonic=sent_at)


@pytest.mark.asyncio
async def test_late_fa_after_drain_budget_does_not_reject_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refused read's late FA retires its tombstone, not the write."""
    now = _clock(monkeypatch)
    tracker = CivRequestTracker()

    _tombstone_read(tracker, _READ, now[0])
    now[0] += 0.6  # past the 0.5 s drain budget on a 300 ms link
    assert tracker.expire_ack_sinks(answer_window=0.5, now_monotonic=now[0]) == 1
    assert tracker.tombstone_count == 1
    assert tracker.ack_sink_count == 0

    write = tracker.register_ack(wait=True)
    tracker.mark_ack_waiter_sent(write, sent_monotonic=now[0])

    assert tracker.resolve(CivEvent(type=CivEventType.NAK, frame=_fa())) is True
    assert not write.done()
    assert tracker.take_refused_reads() == (_READ,)
    assert tracker.tombstone_count == 0

    assert tracker.resolve(CivEvent(type=CivEventType.ACK, frame=_fb())) is True
    assert write.done()
    assert write.result().command == 0xFB


@pytest.mark.asyncio
async def test_tombstone_clears_on_next_data_reply_ordered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A data reply to a newer request proves the tombstoned answer lost."""
    now = _clock(monkeypatch)
    tracker = CivRequestTracker()

    _tombstone_read(tracker, _READ, now[0])
    _tombstone_read(tracker, _METER, now[0] + 0.5)
    now[0] += 0.6
    assert tracker.expire_ack_sinks(answer_window=0.5, now_monotonic=now[0]) == 1
    assert tracker.tombstone_count == 1

    reply = _response(0x15, b"\x00\x40", sub=0x02)
    assert (
        tracker.resolve(
            CivEvent(type=CivEventType.RESPONSE, frame=reply),
            ordered_ingress=True,
        )
        is True
    )
    assert tracker.tombstone_count == 0
    assert tracker.take_refused_reads() == ()
    assert tracker.ack_unclaimed_at is None


@pytest.mark.asyncio
async def test_tombstone_hard_ceiling_records_unclaimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tombstones are bounded: overflow drops the oldest and notes ambiguity."""
    now = _clock(monkeypatch)
    tracker = CivRequestTracker()

    for _ in range(25):
        _tombstone_read(tracker, _READ, now[0])
    assert tracker.expire_ack_sinks(answer_window=0.0, now_monotonic=now[0]) == 25
    assert 0 < tracker.tombstone_count < 25
    assert tracker.ack_unclaimed_at is not None


@pytest.mark.asyncio
async def test_blocking_write_never_takes_older_lost_fb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late FB for a fire-and-forget write must not succeed a newer write."""
    now = _clock(monkeypatch)
    tracker = CivRequestTracker()

    token = tracker.register_ack(wait=False, write_key=_WRITE_KEY)
    assert isinstance(token, int)
    tracker.mark_ack_sink_sent(token, sent_monotonic=now[0])
    now[0] += 0.6
    assert tracker.expire_ack_sinks(answer_window=0.5, now_monotonic=now[0]) == 1

    blocking = tracker.register_ack(wait=True, write_key=_WRITE_KEY)
    tracker.mark_ack_waiter_sent(blocking, sent_monotonic=now[0])

    assert tracker.resolve(CivEvent(type=CivEventType.ACK, frame=_fb())) is True
    assert not blocking.done()
    assert tracker.tombstone_count == 0

    assert tracker.resolve(CivEvent(type=CivEventType.ACK, frame=_fb())) is True
    assert blocking.done()
    assert blocking.result().command == 0xFB
    assert tracker.ack_unclaimed_at is None


@pytest.mark.asyncio
async def test_ptt_interleaved_with_refused_reads_and_lost_fb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PTT writes keep their own answers in both interleavings."""
    now = _clock(monkeypatch)

    # Direction 1: a refused read's FA must not reject the PTT write.
    tracker = CivRequestTracker()
    _tombstone_read(tracker, _READ, now[0])
    now[0] += 0.6
    tracker.expire_ack_sinks(answer_window=0.5, now_monotonic=now[0])
    ptt_off = tracker.register_ack(wait=True, write_key=_PTT)
    tracker.mark_ack_waiter_sent(ptt_off, sent_monotonic=now[0])
    assert tracker.resolve(CivEvent(type=CivEventType.NAK, frame=_fa())) is True
    assert not ptt_off.done()
    assert tracker.take_refused_reads() == (_READ,)
    assert tracker.resolve(CivEvent(type=CivEventType.ACK, frame=_fb())) is True
    assert ptt_off.done()

    # Direction 2: a lost PTT-ON FB must not succeed the PTT-OFF write.
    tracker = CivRequestTracker()
    token = tracker.register_ack(wait=False, write_key=_PTT)
    assert isinstance(token, int)
    tracker.mark_ack_sink_sent(token, sent_monotonic=now[0])
    now[0] += 0.6
    tracker.expire_ack_sinks(answer_window=0.5, now_monotonic=now[0])
    ptt_off = tracker.register_ack(wait=True, write_key=_PTT)
    tracker.mark_ack_waiter_sent(ptt_off, sent_monotonic=now[0])
    assert tracker.resolve(CivEvent(type=CivEventType.ACK, frame=_fb())) is True
    assert not ptt_off.done()
    assert tracker.resolve(CivEvent(type=CivEventType.ACK, frame=_fb())) is True
    assert ptt_off.done()


@pytest.mark.asyncio
async def test_write_echo_confirms_write_and_retires_older_tombstones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On an echo-ack radio the echo is the write's self-identifying answer."""
    now = _clock(monkeypatch)
    tracker = CivRequestTracker()
    _tombstone_read(tracker, _READ, now[0])
    token = tracker.register_ack(wait=False, write_key=_WRITE_KEY)
    assert isinstance(token, int)
    tracker.mark_ack_sink_sent(token, sent_monotonic=now[0] + 0.5)
    now[0] += 0.6
    tracker.expire_ack_sinks(answer_window=0.5, now_monotonic=now[0])
    assert tracker.tombstone_count == 1

    echo = _response(0x05, b"\x00\x26\x36\x00\x14")
    assert (
        tracker.resolve(
            CivEvent(type=CivEventType.RESPONSE, frame=echo),
            ordered_ingress=True,
        )
        is True
    )
    assert tracker.ack_sink_count == 0
    assert tracker.tombstone_count == 0
    assert tracker.take_refused_reads() == ()
    assert tracker.ack_unclaimed_at is None

    # Unordered: the echo still confirms the write but proves nothing older.
    tracker = CivRequestTracker()
    _tombstone_read(tracker, _READ, now[0])
    token = tracker.register_ack(wait=False, write_key=_WRITE_KEY)
    assert isinstance(token, int)
    tracker.mark_ack_sink_sent(token, sent_monotonic=now[0] + 0.5)
    now[0] += 0.6
    tracker.expire_ack_sinks(answer_window=0.5, now_monotonic=now[0])
    assert (
        tracker.resolve(
            CivEvent(type=CivEventType.RESPONSE, frame=echo),
            ordered_ingress=False,
        )
        is True
    )
    assert tracker.ack_sink_count == 0
    assert tracker.tombstone_count == 1

    # A transceive broadcast answers nothing: it must not confirm the write.
    tracker = CivRequestTracker()
    token = tracker.register_ack(wait=False, write_key=_WRITE_KEY)
    assert isinstance(token, int)
    tracker.mark_ack_sink_sent(token, sent_monotonic=now[0])
    assert (
        tracker.resolve(
            CivEvent(
                type=CivEventType.RESPONSE,
                frame=_response(0x05, b"\x00\x26\x36\x00\x14", to_addr=0x00),
            ),
            ordered_ingress=True,
        )
        is False
    )
    assert tracker.ack_sink_count == 1


@pytest.mark.asyncio
async def test_fb_fa_to_other_controller_is_ignored() -> None:
    """Bare FB/FA names no command: only ours may settle a waiter."""
    tracker = CivRequestTracker()
    write = tracker.register_ack(wait=True)

    assert (
        tracker.resolve(
            CivEvent(type=CivEventType.ACK, frame=_fb(to_addr=_FOREIGN_CTRL))
        )
        is False
    )
    assert not write.done()
    assert tracker.snapshot_stats()["ack_orphans"] == 0

    assert (
        tracker.resolve(CivEvent(type=CivEventType.NAK, frame=_fa(to_addr=0x00)))
        is False
    )
    assert not write.done()

    assert tracker.resolve(CivEvent(type=CivEventType.ACK, frame=_fb())) is True
    assert write.done()


@pytest.mark.asyncio
async def test_rtt_sizes_wait_budgets_not_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Send-to-answer samples scale budgets; attribution stays order-only."""
    now = _clock(monkeypatch, start=2000.0)
    tracker = CivRequestTracker()
    assert tracker.rtt_high_quantile == 0.0
    assert tracker.scaled_wait_budget(0.5) == 0.5

    pending = tracker.register_response(_READ)
    tracker.mark_response_sent(pending, sent_monotonic=now[0])
    now[0] += 0.3
    reply = _response(0x03, b"\x00\x26\x36\x00\x14")
    assert tracker.resolve(CivEvent(type=CivEventType.RESPONSE, frame=reply)) is True
    assert pending.result() == reply

    assert tracker.rtt_high_quantile == pytest.approx(0.3)
    assert tracker.scaled_wait_budget(0.5) == pytest.approx(0.9)
    assert tracker.scaled_wait_budget(2.0) == 2.0
