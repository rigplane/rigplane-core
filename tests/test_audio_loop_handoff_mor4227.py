"""MOR-4227: coalesced thread→loop handoff for per-frame audio delivery.

Deterministic harness: a fake loop records ``call_soon_threadsafe``
callbacks instead of running them, so every ordering/loss/latency property
is driven by explicit ``run_scheduled`` calls — no wall-clock sleeps. The
plain-handoff semantics these tests pin are what a per-frame
``loop.call_soon_threadsafe(deliver, frame)`` guaranteed before MOR-4227.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

from rigplane.audio.loop_handoff import CoalescedLoopHandoff


class FakeLoop:
    """Records threadsafe callbacks; the test decides when they run."""

    def __init__(self) -> None:
        self.scheduled: list[Callable[[], None]] = []
        self.closed = False

    def call_soon_threadsafe(self, callback: Callable[[], None], *args: Any) -> None:
        if self.closed:
            raise RuntimeError("Event loop is closed")
        if args:

            def run() -> None:
                callback(*args)

            self.scheduled.append(run)
        else:
            self.scheduled.append(callback)

    def run_scheduled(self) -> int:
        pending, self.scheduled = self.scheduled, []
        for callback in pending:
            callback()
        return len(pending)


def test_idle_submit_delivers_in_first_scheduled_callback() -> None:
    """Idle traffic keeps the plain handoff's latency: one submit → one
    drain → the frame leaves in that very callback, no extra round."""
    loop = FakeLoop()
    delivered: list[bytes] = []
    handoff = CoalescedLoopHandoff(loop, delivered.append)

    handoff.submit(b"f1")
    assert len(loop.scheduled) == 1
    assert delivered == []

    assert loop.run_scheduled() == 1
    assert delivered == [b"f1"]
    assert loop.scheduled == []


def test_burst_coalesces_into_one_drain() -> None:
    loop = FakeLoop()
    delivered: list[bytes] = []
    handoff = CoalescedLoopHandoff(loop, delivered.append)

    handoff.submit(b"f1")
    handoff.submit(b"f2")
    handoff.submit(b"f3")
    assert len(loop.scheduled) == 1

    assert loop.run_scheduled() == 1
    assert delivered == [b"f1", b"f2", b"f3"]
    assert loop.scheduled == []


def test_frame_after_drain_schedules_new_drain() -> None:
    loop = FakeLoop()
    delivered: list[bytes] = []
    handoff = CoalescedLoopHandoff(loop, delivered.append)

    handoff.submit(b"f1")
    loop.run_scheduled()
    handoff.submit(b"f2")
    assert len(loop.scheduled) == 1
    loop.run_scheduled()
    assert delivered == [b"f1", b"f2"]


def test_submit_from_producer_thread_mid_drain_is_not_lost() -> None:
    """The drain clears its flag BEFORE taking its batch: a producer
    appending while the drain delivers schedules a fresh drain, so its
    frame is delivered exactly once — by that next drain, after every
    frame of the running batch (it arrived after all of them)."""
    loop = FakeLoop()
    delivered: list[bytes] = []

    def deliver(frame: bytes) -> None:
        delivered.append(frame)
        if frame == b"f1":
            # A producer lands mid-drain, during the batch's delivery.
            thread = threading.Thread(target=handoff.submit, args=(b"late",))
            thread.start()
            thread.join()

    handoff = CoalescedLoopHandoff(loop, deliver)
    handoff.submit(b"f1")
    handoff.submit(b"f2")

    # The drain delivers ONLY the frames present when it started; the
    # late frame cannot join that batch (round-1 P1: bounded batches).
    assert loop.run_scheduled() == 1
    assert delivered == [b"f1", b"f2"]
    # The producer's own scheduling delivers it exactly once, in order.
    assert loop.run_scheduled() == 1
    assert delivered == [b"f1", b"f2", b"late"]
    # Nothing pending, nothing further scheduled.
    assert loop.scheduled == []


def test_drain_is_bounded_under_a_producer_that_keeps_pace() -> None:
    """Round-1 P1: a drain delivers only the frames present when it
    started and then returns. The old ``while True`` loop kept consuming
    the live buffer, so a producer appending during delivery — sustained
    load — never gave the event loop back. Now such frames ride the next
    scheduled drain, so other loop callbacks run between drains."""
    loop = FakeLoop()
    delivered: list[bytes] = []
    order: list[str] = []
    handoff: CoalescedLoopHandoff | None = None

    def deliver(frame: bytes) -> None:
        delivered.append(frame)
        # Sustained producer: every delivered frame is replaced mid-drain.
        if frame in (b"a", b"b"):
            assert handoff is not None
            handoff.submit(frame + b"+")

    handoff = CoalescedLoopHandoff(loop, deliver)
    handoff.submit(b"a")
    handoff.submit(b"b")
    # A bystander loop callback queued behind the drain.
    loop.call_soon_threadsafe(lambda: order.append("bystander"))

    # One drain callback: exactly the two frames present at its start,
    # then control back — the bystander runs in the same loop pass.
    assert loop.run_scheduled() == 2
    assert delivered == [b"a", b"b"]
    assert order == ["bystander"]

    # Nothing lost or duplicated: the sustained frames ride the next drain.
    assert loop.run_scheduled() == 1
    assert delivered == [b"a", b"b", b"a+", b"b+"]
    assert loop.scheduled == []


def test_many_bursts_never_lose_or_reorder() -> None:
    loop = FakeLoop()
    delivered: list[bytes] = []
    handoff = CoalescedLoopHandoff(loop, delivered.append)

    expected: list[bytes] = []
    for burst in range(50):
        frames = [f"{burst}-{i}".encode() for i in range(burst % 5 + 1)]
        for frame in frames:
            handoff.submit(frame)
            expected.append(frame)
        if burst % 3 == 0:
            loop.run_scheduled()
    while loop.scheduled:
        loop.run_scheduled()

    assert delivered == expected


def test_deliver_exception_does_not_lose_remaining_frames() -> None:
    loop = FakeLoop()
    delivered: list[bytes] = []

    def deliver(frame: bytes) -> None:
        delivered.append(frame)
        if frame == b"f2":
            raise ValueError("boom")

    handoff = CoalescedLoopHandoff(loop, deliver)
    for frame in (b"f1", b"f2", b"f3"):
        handoff.submit(frame)

    # The drain isolates the failure like one call_soon callback per frame.
    assert loop.run_scheduled() == 1
    assert delivered == [b"f1", b"f2", b"f3"]


def test_closed_loop_is_terminal_frames_released_and_submits_dropped() -> None:
    """Round-1 P2: a closed asyncio loop never reopens, so the first
    failed scheduling is terminal — the buffered frames (which can now
    never be delivered) are released and further submits are dropped
    instead of growing the buffer forever."""
    loop = FakeLoop()
    delivered: list[bytes] = []
    handoff = CoalescedLoopHandoff(loop, delivered.append)

    loop.closed = True
    try:
        handoff.submit(b"f1")
    except RuntimeError:
        pass
    else:
        raise AssertionError("closed loop must raise RuntimeError")

    # Terminal: later captures are dropped silently and nothing is kept.
    handoff.submit(b"f2")
    handoff.submit(b"f3")

    # Even a (non-asyncio) reopen must not resurrect the dropped frames.
    loop.closed = False
    loop.run_scheduled()
    assert delivered == []
    assert loop.scheduled == []


async def test_rx_stream_receive_coalesces_worker_thread_bursts(
    monkeypatch: Any,
) -> None:
    """End-to-end through UsbAudioDriver._stream_rx_callback: frames pushed
    from a non-loop thread arrive on the loop in order, every one exactly
    once, with at least one and at most one handoff wake-up per frame."""
    from rigplane.audio.backend import FakeAudioBackend
    from rigplane.audio.usb_driver import UsbAudioDriver

    # Count only the handoff's own wake-ups, through a wrapper on the
    # handoff itself (round-1 P2): patching the loop's
    # ``call_soon_threadsafe`` also counted unrelated callers — the
    # executor completion of ``asyncio.to_thread`` — so the upper bound
    # was flaky. Every executed ``_drain`` is exactly one handoff
    # scheduling.
    drains = 0
    real_drain = CoalescedLoopHandoff._drain

    def counting_drain(self: CoalescedLoopHandoff) -> None:
        nonlocal drains
        drains += 1
        real_drain(self)

    monkeypatch.setattr(CoalescedLoopHandoff, "_drain", counting_drain)

    driver = UsbAudioDriver(backend=FakeAudioBackend())
    received: list[bytes] = []
    driver._rx_callback = received.append
    receive = driver._stream_rx_callback(frame_ms=20)

    frames = [f"frame-{i}".encode() for i in range(6)]
    await asyncio.to_thread(lambda: [receive(frame) for frame in frames])

    def all_delivered() -> bool:
        return len(received) == len(frames)

    for _ in range(100):
        if all_delivered():
            break
        await asyncio.sleep(0)
    assert received == frames
    assert 1 <= drains <= len(frames)

    # In-loop delivery stays synchronous: no wake-up, immediate order.
    drains = 0
    receive(b"in-loop")
    assert received[-1] == b"in-loop"
    assert drains == 0
