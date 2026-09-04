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
