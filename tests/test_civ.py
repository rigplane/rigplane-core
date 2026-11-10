from __future__ import annotations

import asyncio
import time

import pytest

from rigplane.civ import (
    CivEvent,
    CivEventType,
    CivRequestKey,
    CivRequestTracker,
    request_key_from_frame,
)
from rigplane.types import CivFrame


def _ack_frame() -> CivFrame:
    return CivFrame(
        to_addr=0xE0,
        from_addr=0x98,
        command=0xFB,
        sub=None,
        data=b"",
    )


def _ack_frame_from(addr: int) -> CivFrame:
    return CivFrame(
        to_addr=0xE0,
        from_addr=addr,
        command=0xFB,
        sub=None,
        data=b"",
    )


def _response_frame(command: int, data: bytes) -> CivFrame:
    return CivFrame(
        to_addr=0xE0,
        from_addr=0x98,
        command=command,
        sub=None,
        data=data,
    )


def test_request_key_preserves_outgoing_data_prefix() -> None:
    frame = _response_frame(0x25, b"\x00")

    assert request_key_from_frame(frame) == CivRequestKey(
        command=0x25,
        sub=None,
        data_prefix=b"\x00",
    )


def test_request_key_ignores_data_for_non_selector_commands() -> None:
    frame = CivFrame(
        to_addr=0xE0,
        from_addr=0x98,
        command=0x27,
        sub=0x1F,
        data=b"\x00",
    )

    assert request_key_from_frame(frame) == CivRequestKey(
        command=0x27,
        sub=0x1F,
    )


@pytest.mark.parametrize(
    ("sub", "data", "prefix"),
    [
        (0x00, b"\x02\x00\x01", b"\x02\x00\x01"),
        (0x00, b"\x00\x00\x00\x04", b"\x00\x00\x00\x04"),
        (0x00, b"\x00" * 114, b""),
        (0x00, b"\x00\x01", b""),
        (0x00, b"\x00" * 5, b""),
        (0x01, b"\x02\x00\x01", b""),
        (0x05, b"\x00\x00\x00\x04", b""),
    ],
)
def test_request_key_prefixes_only_address_only_memory_reads(
    sub: int, data: bytes, prefix: bytes
) -> None:
    frame = CivFrame(0xA4, 0xE0, 0x1A, sub=sub, data=data)
    assert request_key_from_frame(frame).data_prefix == prefix


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected_prefix", "wrong_prefix"),
    [
        (0x25, b"\x00", b"\x01"),
        (0x26, b"\x00", b"\x01"),
        (0x07, b"\xc2", b"\xd2"),
    ],
)
async def test_tracker_distinguishes_response_data_prefixes(
    command: int,
    expected_prefix: bytes,
    wrong_prefix: bytes,
) -> None:
    tracker = CivRequestTracker()
    pending = tracker.register_response(
        CivRequestKey(command=command, sub=None, data_prefix=expected_prefix)
    )

    wrong = CivEvent(
        type=CivEventType.RESPONSE,
        frame=_response_frame(command, wrong_prefix + b"\xaa"),
    )
    assert tracker.resolve(wrong) is False
    assert not pending.done()

    expected = _response_frame(command, expected_prefix + b"\xbb")
    assert tracker.resolve(CivEvent(type=CivEventType.RESPONSE, frame=expected)) is True
    assert pending.result() == expected


@pytest.mark.asyncio
async def test_tracker_empty_data_prefix_preserves_legacy_matching() -> None:
    tracker = CivRequestTracker()
    pending = tracker.register_response(CivRequestKey(command=0x25, sub=None))
    response = _response_frame(0x25, b"\x01\xaa")

    assert tracker.resolve(CivEvent(type=CivEventType.RESPONSE, frame=response)) is True
    assert pending.result() == response


@pytest.mark.asyncio
async def test_tracker_cleanup_stale_waiters() -> None:
    tracker = CivRequestTracker(stale_ttl=10.0)

    ack_future = tracker.register_ack(wait=True)
    token_or_future = tracker.register_ack(wait=False)
    assert isinstance(token_or_future, int)
    resp_future = tracker.register_response(CivRequestKey(command=0x03, sub=None))
    created = time.monotonic()

    cleaned = tracker.cleanup_stale(now_monotonic=created + 11.0)
    assert cleaned == 3
    assert tracker.pending_count == 0
    assert tracker.stale_cleaned_total == 3
    assert ack_future.done()
    assert isinstance(ack_future.exception(), asyncio.TimeoutError)
    assert resp_future.done()
    assert isinstance(resp_future.exception(), asyncio.TimeoutError)


@pytest.mark.asyncio
async def test_tracker_generation_rejects_old_epoch_responses() -> None:
    tracker = CivRequestTracker()

    pending = tracker.register_ack(wait=True)
    old_gen = tracker.generation
    new_gen = tracker.advance_generation(RuntimeError("reconnect"))
    assert new_gen == old_gen + 1
    assert pending.done()

    event = CivEvent(type=CivEventType.ACK, frame=_ack_frame())
    assert tracker.resolve(event, generation=old_gen) is False

    fresh = tracker.register_ack(wait=True)
    assert tracker.resolve(event, generation=new_gen) is True
    assert fresh.done()


@pytest.mark.asyncio
async def test_tracker_preserves_orphan_ack_in_backlog() -> None:
    tracker = CivRequestTracker(ack_backlog_size=4, ack_backlog_ttl=1.0)
    ack = _ack_frame()
    event = CivEvent(type=CivEventType.ACK, frame=ack)

    assert tracker.resolve(event) is True

    pending = tracker.register_ack(wait=True)
    assert pending.done()
    assert pending.result() == ack

    stats = tracker.snapshot_stats()
    assert stats["ack_orphans"] == 1
    assert stats["ack_backlog_hits"] == 1


@pytest.mark.asyncio
async def test_tracker_ack_backlog_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("rigplane.civ.time.monotonic", lambda: now[0])

    tracker = CivRequestTracker(ack_backlog_size=4, ack_backlog_ttl=0.5)
    ack = _ack_frame()
    event = CivEvent(type=CivEventType.ACK, frame=ack)

    assert tracker.resolve(event) is True

    now[0] += 1.0  # expire backlog entry
    pending = tracker.register_ack(wait=True)
    assert not pending.done()

    stats = tracker.snapshot_stats()
    assert stats["ack_backlog_drops"] == 1


@pytest.mark.asyncio
async def test_tracker_ack_backlog_is_bounded_fifo() -> None:
    tracker = CivRequestTracker(ack_backlog_size=2, ack_backlog_ttl=10.0)
    e1 = CivEvent(type=CivEventType.ACK, frame=_ack_frame_from(0x90))
    e2 = CivEvent(type=CivEventType.ACK, frame=_ack_frame_from(0x91))
    e3 = CivEvent(type=CivEventType.ACK, frame=_ack_frame_from(0x92))

    assert tracker.resolve(e1) is True
    assert tracker.resolve(e2) is True
    assert tracker.resolve(e3) is True  # oldest should be dropped

    first = tracker.register_ack(wait=True)
    second = tracker.register_ack(wait=True)
    assert first.done() and second.done()
    assert first.result().from_addr == 0x91
    assert second.result().from_addr == 0x92

    stats = tracker.snapshot_stats()
    assert stats["ack_orphans"] == 3
    assert stats["ack_backlog_drops"] == 1
    assert stats["ack_backlog_hits"] == 2


def _nak_frame() -> CivFrame:
    return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFA, sub=None, data=b"")


@pytest.mark.asyncio
async def test_refusal_names_the_oldest_open_read_sink() -> None:
    """MOR-4058: a FA that retires a read's sink names that read."""
    tracker = CivRequestTracker()
    answered = CivRequestKey(command=0x03, sub=None)
    refused = CivRequestKey(command=0x1C, sub=0x01)
    later = CivRequestKey(command=0x1C, sub=0x03)
    for key in (answered, refused, later):
        tracker.register_ack(wait=False, response_key=key, refused_read=key)
    tracker.resolve(
        CivEvent(type=CivEventType.RESPONSE, frame=_response_frame(0x03, b"\x00"))
    )

    assert tracker.resolve(CivEvent(type=CivEventType.NAK, frame=_nak_frame()))

    assert tracker.take_refused_reads() == (refused,)
    assert tracker.take_refused_reads() == ()


#: CI-V addresses of two radios on the shared tracker (MOR-4209): IC-705, IC-7300.
_RADIO_ADDRS = pytest.mark.parametrize(
    "radio_addr", [0xA4, 0x94], ids=["ic705", "ic7300"]
)


def _directed_response(
    radio_addr: int, command: int, data: bytes, *, sub: int | None = None
) -> CivFrame:
    return CivFrame(
        to_addr=0xE0, from_addr=radio_addr, command=command, sub=sub, data=data
    )


def _nak_from(radio_addr: int) -> CivFrame:
    return CivFrame(
        to_addr=0xE0, from_addr=radio_addr, command=0xFA, sub=None, data=b""
    )


def _register_reads(tracker: CivRequestTracker, *keys: CivRequestKey) -> None:
    for key in keys:
        tracker.register_ack(wait=False, response_key=key, refused_read=key)


_FREQ = CivRequestKey(command=0x03, sub=None)
_S_METER = CivRequestKey(command=0x15, sub=0x02)
_PTT = CivRequestKey(command=0x1C, sub=0x00)
_TUNER = CivRequestKey(command=0x1C, sub=0x01)


@_RADIO_ADDRS
@pytest.mark.asyncio
async def test_a_refusal_after_a_lost_answer_names_the_refused_read(
    radio_addr: int,
) -> None:
    """MOR-4209: on an ordered transport a read whose answer was lost does
    not take a later refusal.

    The radio answers in order. Once it has answered a read sent after an
    older still-open one, the older one's answer is not coming, so a bare FA
    that follows belongs to a read the radio has not answered yet — not to
    the lost one. Before the fix the lost read's sink stayed the oldest and
    took the refusal: a frequency read was reported refused (and withheld)
    while the tuner read the radio really refused timed out.
    """
    tracker = CivRequestTracker()
    _register_reads(tracker, _FREQ, _S_METER, _TUNER)

    tracker.resolve(
        CivEvent(
            type=CivEventType.RESPONSE,
            frame=_directed_response(radio_addr, 0x15, b"\x00\x40", sub=0x02),
        ),
        ordered_ingress=True,
    )
    assert tracker.resolve(
        CivEvent(type=CivEventType.NAK, frame=_nak_from(radio_addr)),
        ordered_ingress=True,
    )

    assert tracker.take_refused_reads() == (_TUNER,)
    assert tracker.ack_sink_count == 0


@_RADIO_ADDRS
@pytest.mark.asyncio
async def test_an_unordered_transport_keeps_older_sinks_open(radio_addr: int) -> None:
    """MOR-4209: on a transport that does not keep the radio's order, the
    read B answered first does not prove read A's answer lost. A's
    retransmitted FA arriving after B's answer refuses A, and the later read C
    stays open instead of being refused. (Both CI-V transports keep the order
    since MOR-4205; this pins the tracker's side of the contract.)"""
    tracker = CivRequestTracker()
    _register_reads(tracker, _FREQ, _S_METER, _TUNER)

    tracker.resolve(
        CivEvent(
            type=CivEventType.RESPONSE,
            frame=_directed_response(radio_addr, 0x15, b"\x00\x40", sub=0x02),
        ),
        ordered_ingress=False,
    )
    tracker.resolve(
        CivEvent(type=CivEventType.NAK, frame=_nak_from(radio_addr)),
        ordered_ingress=False,
    )

    assert tracker.take_refused_reads() == (_FREQ,)
    assert tracker.ack_sink_count == 1  # C still waits for its own answer


@_RADIO_ADDRS
@pytest.mark.asyncio
async def test_an_answer_also_retires_an_older_write_sink(radio_addr: int) -> None:
    """MOR-4209: a write's FB/FA would have arrived before a later read's
    answer, so that answer retires the older write sink too (its ACK is
    lost), and the next FA reaches the read the radio is answering."""
    tracker = CivRequestTracker()
    tracker.register_ack(wait=False)  # a fire-and-forget write, ACK lost
    _register_reads(tracker, _FREQ, _TUNER)

    tracker.resolve(
        CivEvent(
            type=CivEventType.RESPONSE,
            frame=_directed_response(radio_addr, 0x03, b"\x00\x40\x07\x14\x00"),
        ),
        ordered_ingress=True,
    )
    assert tracker.ack_sink_count == 1
    assert tracker.ack_unclaimed_at is not None
    tracker.resolve(
        CivEvent(type=CivEventType.NAK, frame=_nak_from(radio_addr)),
        ordered_ingress=True,
    )

    assert tracker.take_refused_reads() == (_TUNER,)


@_RADIO_ADDRS
@pytest.mark.asyncio
async def test_a_broadcast_takes_no_sink(radio_addr: int) -> None:
    """MOR-4209: an unsolicited broadcast (to 0x00) answers no request.

    A 1C 00 transceive broadcast must neither consume the pending PTT read's
    sink nor retire the older sinks. When the PTT read's own directed answer
    then arrives it retires the PTT sink and the lost frequency read before
    it, so the following FA names only the tuner read.
    """
    tracker = CivRequestTracker()
    _register_reads(tracker, _FREQ, _PTT, _TUNER)

    broadcast = CivFrame(
        to_addr=0x00, from_addr=radio_addr, command=0x1C, sub=0x00, data=b"\x00"
    )
    assert not tracker.resolve(
        CivEvent(type=CivEventType.RESPONSE, frame=broadcast), ordered_ingress=True
    )
    assert tracker.ack_sink_count == 3

    tracker.resolve(
        CivEvent(
            type=CivEventType.RESPONSE,
            frame=_directed_response(radio_addr, 0x1C, b"\x00", sub=0x00),
        ),
        ordered_ingress=True,
    )
    tracker.resolve(
        CivEvent(type=CivEventType.NAK, frame=_nak_from(radio_addr)),
        ordered_ingress=True,
    )

    assert tracker.take_refused_reads() == (_TUNER,)
    assert tracker.ack_sink_count == 0


@_RADIO_ADDRS
@pytest.mark.asyncio
async def test_an_answer_leaves_an_older_blocking_waiter_alone(radio_addr: int) -> None:
    """MOR-4209: only fire-and-forget sinks follow the wire order the
    commander gives them; a blocking waiter (a raw transaction registers
    before its own pacing gap) is never retired by someone else's answer."""
    tracker = CivRequestTracker()
    blocking = tracker.register_ack(wait=True, consume_backlog=False)
    assert isinstance(blocking, asyncio.Future)
    _register_reads(tracker, _FREQ)

    tracker.resolve(
        CivEvent(
            type=CivEventType.RESPONSE,
            frame=_directed_response(radio_addr, 0x03, b"\x00\x40\x07\x14\x00"),
        ),
        ordered_ingress=True,
    )
    assert not blocking.done()
    tracker.resolve(
        CivEvent(type=CivEventType.ACK, frame=_ack_frame_from(radio_addr)),
        ordered_ingress=True,
    )
    assert blocking.done()


@pytest.mark.asyncio
async def test_only_a_refused_read_sink_is_named() -> None:
    tracker = CivRequestTracker()
    tracker.register_ack(wait=False)  # a fire-and-forget write
    read = CivRequestKey(command=0x03, sub=None)
    tracker.register_ack(wait=False, response_key=read, refused_read=read)
    tracker.resolve(CivEvent(type=CivEventType.NAK, frame=_nak_frame()))
    tracker.resolve(CivEvent(type=CivEventType.ACK, frame=_ack_frame()))

    assert tracker.take_refused_reads() == ()


@pytest.mark.asyncio
async def test_generation_advance_forgets_refused_reads() -> None:
    tracker = CivRequestTracker()
    tuner = CivRequestKey(command=0x1C, sub=0x01)
    tracker.register_ack(wait=False, response_key=tuner, refused_read=tuner)
    tracker.resolve(CivEvent(type=CivEventType.NAK, frame=_nak_frame()))
    tracker.advance_generation(RuntimeError("reconnect"))

    assert tracker.take_refused_reads() == ()


def test_expire_ack_sinks_drops_only_what_each_window_closed() -> None:
    """MOR-4384: a sink is owed its answer for one window from its send.

    The write below was sent before the read but both windows open at
    their own send stamps, so at t=96 only the write's window (90 + 5)
    has closed; the read's (100 + 5) keeps it claimable.
    """
    tracker = CivRequestTracker()
    read = tracker.register_ack(
        wait=False, response_key=_TUNER, refused_read=_TUNER
    )
    write = tracker.register_ack(wait=False)
    tracker.mark_ack_sink_sent(read, sent_monotonic=100.0)
    tracker.mark_ack_sink_sent(write, sent_monotonic=90.0)

    assert tracker.expire_ack_sinks(answer_window=5.0, now_monotonic=96.0) == 1
    assert tracker.ack_unclaimed_at is not None  # the write's FB can still arrive

    assert tracker.expire_ack_sinks(answer_window=5.0, now_monotonic=110.0) == 1
    assert tracker.ack_sink_count == 0


def test_an_expired_read_sink_also_flags_an_unclaimed_answer() -> None:
    """MOR-4384: a refused read is answered with a bare ``FA`` too.

    An expired sink never got the answer the radio owed it. For a write
    that answer is a bare FB/FA; for a read it is the data answer, or an
    ``FA`` when the radio refuses it. Whichever arrives late names no
    command and can settle a later waiter, so a dropped read sink must
    record the ambiguity just like a write's.
    """
    tracker = CivRequestTracker()
    sink = tracker.register_ack(
        wait=False, response_key=_S_METER, refused_read=_S_METER
    )
    tracker.mark_ack_sink_sent(sink, sent_monotonic=100.0)
    assert tracker.ack_unclaimed_at is None

    assert tracker.expire_ack_sinks(answer_window=5.0, now_monotonic=105.0) == 1

    assert tracker.ack_unclaimed_at is not None


@pytest.mark.asyncio
async def test_a_read_within_its_window_keeps_the_refusal_off_the_next_write() -> None:
    """MOR-4384: while a read is still owed its answer window, a refusal
    lands on that read — not on the next write's waiter, and not on an
    s_meter sink registered after it."""
    tracker = CivRequestTracker()
    tuner = tracker.register_ack(
        wait=False, response_key=_TUNER, refused_read=_TUNER
    )
    s_meter = tracker.register_ack(
        wait=False, response_key=_S_METER, refused_read=_S_METER
    )
    tracker.mark_ack_sink_sent(tuner, sent_monotonic=100.0)
    tracker.mark_ack_sink_sent(s_meter, sent_monotonic=100.0)
    write = tracker.register_ack(wait=True, consume_backlog=False)

    assert tracker.expire_ack_sinks(answer_window=5.0, now_monotonic=101.0) == 0
    assert tracker.resolve(CivEvent(type=CivEventType.NAK, frame=_nak_frame()))

    assert tracker.take_refused_reads() == (_TUNER,)
    assert not write.done(), "the read's refusal settled the write's waiter"
    assert tracker.ack_sink_count == 1  # the s_meter sink still waits
    write.cancel()


def test_restore_refused_reads_requeues_undelivered_once() -> None:
    """MOR-4384: a taken refusal no consumer took is not lost.

    ``_record_refused_reads`` takes every refusal off the tracker and
    hands the declared ones to the scheduler; whatever it cannot deliver
    goes back, so the record of which read the radio refused survives
    for the owner. A repeatedly refused read still keeps one entry.
    """
    tracker = CivRequestTracker()
    tracker.register_ack(wait=False, response_key=_TUNER, refused_read=_TUNER)
    assert tracker.resolve(CivEvent(type=CivEventType.NAK, frame=_nak_frame()))

    taken = tracker.take_refused_reads()
    assert taken == (_TUNER,)
    tracker.restore_refused_reads(taken)

    assert tracker.take_refused_reads() == (_TUNER,)

    tracker.restore_refused_reads((_TUNER, _TUNER))
    assert tracker.take_refused_reads() == (_TUNER,)
    assert tracker.take_refused_reads() == ()
