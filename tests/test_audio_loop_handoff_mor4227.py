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

    def call_soon_threadsafe(
        self, callback: Callable[[], None], *args: Any
    ) -> None:
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
    """The drain clears its flag BEFORE popping: a producer appending while
    the drain is mid-delivery schedules a fresh drain, so its frame is
    delivered exactly once — here by the still-running drain's pops."""
    loop = FakeLoop()
    delivered: list[bytes] = []

    def deliver(frame: bytes) -> None:
        delivered.append(frame)
        if frame == b"f1":
            # A producer lands mid-drain, between two pops.
            thread = threading.Thread(target=handoff.submit, args=(b"late",))
            thread.start()
            thread.join()

    handoff = CoalescedLoopHandoff(loop, deliver)
    handoff.submit(b"f1")
    handoff.submit(b"f2")

    loop.run_scheduled()
    # Exactly-once, in order; the late frame joined the running drain.
    assert delivered == [b"f1", b"f2", b"late"]
    # The producer scheduled a second drain that may or may not find work;
    # running it must be harmless (no duplicate delivery).
    loop.run_scheduled()
    assert delivered == [b"f1", b"f2", b"late"]


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


def test_closed_loop_raises_and_recovers_without_losing_the_frame() -> None:
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

    loop.closed = False
    handoff.submit(b"f2")
    assert len(loop.scheduled) == 1
    loop.run_scheduled()
    assert delivered == [b"f1", b"f2"]


async def test_rx_stream_receive_coalesces_worker_thread_bursts() -> None:
    """End-to-end through UsbAudioDriver._stream_rx_callback: frames pushed
    from a non-loop thread arrive on the loop in order, every one exactly
    once, with at least one and at most one wake-up per frame."""
    from rigplane.audio.backend import FakeAudioBackend
    from rigplane.audio.usb_driver import UsbAudioDriver

    loop = asyncio.get_running_loop()
    wakeups = 0
    original = loop.call_soon_threadsafe

    def counting_call_soon_threadsafe(callback: Any, *args: Any) -> None:
        nonlocal wakeups
        wakeups += 1
        original(callback, *args)

    loop.call_soon_threadsafe = counting_call_soon_threadsafe  # type: ignore[method-assign]
    try:
        driver = UsbAudioDriver(backend=FakeAudioBackend())
        received: list[bytes] = []
        driver._rx_callback = received.append
        receive = driver._stream_rx_callback(frame_ms=20)

        frames = [f"frame-{i}".encode() for i in range(6)]
        await asyncio.to_thread(
            lambda: [receive(frame) for frame in frames]
        )

        def all_delivered() -> bool:
            return len(received) == len(frames)

        for _ in range(100):
            if all_delivered():
                break
            await asyncio.sleep(0)
        assert received == frames
        assert 1 <= wakeups <= len(frames)

        # In-loop delivery stays synchronous: no wake-up, immediate order.
        wakeups = 0
        receive(b"in-loop")
        assert received[-1] == b"in-loop"
        assert wakeups == 0
    finally:
        loop.call_soon_threadsafe = original  # type: ignore[method-assign]
