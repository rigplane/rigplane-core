"""Tests for AudioBus pub/sub audio distribution."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.audio import AudioPacket
from rigplane.audio_bus import AudioBus, AudioSubscription


def _parked_on_queue_get(task: asyncio.Task[object]) -> bool:
    """True when ``task`` is suspended awaiting a Future at the bottom of
    its coroutine chain — the queue getter the consumer parks on when the
    queue is empty. Cancelling after this check lands on a genuinely
    pending receive on every supported Python version, not on one that
    already finished. In CPython ``await future`` bottoms out at the
    C-level ``FutureIter`` wrapper rather than the Future itself, so the
    FutureIter shape counts as parked-on-a-future too (nothing can have
    woken the consumer at the points this is used).
    """
    coro: object = task.get_coro()
    for _ in range(8):
        nxt = getattr(coro, "cr_await", None)
        if nxt is None:
            return False
        if isinstance(nxt, asyncio.Future):
            return not nxt.done()
        if type(nxt).__name__ == "FutureIter":
            return not task.done()
        coro = nxt
    return False


@pytest.fixture
def mock_radio():
    radio = SimpleNamespace()
    radio.start_audio_rx_opus = AsyncMock()
    radio.stop_audio_rx_opus = AsyncMock()
    return radio


@pytest.fixture
def bus(mock_radio):
    return AudioBus(mock_radio)


# ---------------------------------------------------------------------------
# AudioBus basics
# ---------------------------------------------------------------------------


def test_bus_init(bus):
    assert bus.subscriber_count == 0
    assert not bus.rx_active
    assert bus.stats["subscriber_count"] == 0


async def test_bus_subscribe_creates_inactive_subscription(bus):
    sub = bus.subscribe(name="test")
    assert isinstance(sub, AudioSubscription)
    assert not sub.active
    assert bus.subscriber_count == 0  # not registered until start()


# ---------------------------------------------------------------------------
# Subscription lifecycle
# ---------------------------------------------------------------------------


async def test_subscription_start_registers(bus, mock_radio):
    sub = bus.subscribe(name="s1")
    await sub.start()
    assert sub.active
    assert bus.subscriber_count == 1
    # First subscriber triggers RX start
    mock_radio.start_audio_rx_opus.assert_awaited_once()
    assert bus.rx_active


async def test_subscription_stop_unregisters(bus, mock_radio):
    sub = bus.subscribe(name="s1")
    await sub.start()
    assert sub.stop() is None
    assert not sub.active
    await sub.aclose()
    assert bus.subscriber_count == 0
    mock_radio.stop_audio_rx_opus.assert_awaited_once()
    assert not bus.rx_active


async def test_subscription_aclose_unregisters(bus, mock_radio):
    sub = bus.subscribe(name="s1")
    await sub.start()

    await sub.aclose()

    assert not sub.active
    assert bus.subscriber_count == 0
    mock_radio.stop_audio_rx_opus.assert_awaited_once()
    assert not bus.rx_active


async def test_subscription_double_start(bus, mock_radio):
    sub = bus.subscribe(name="s1")
    await sub.start()
    await sub.start()  # no-op
    assert bus.subscriber_count == 1
    assert mock_radio.start_audio_rx_opus.await_count == 1


async def test_subscription_double_stop(bus, mock_radio):
    sub = bus.subscribe(name="s1")
    await sub.start()
    sub.stop()
    sub.stop()  # no-op
    await sub.aclose()
    assert bus.subscriber_count == 0


# ---------------------------------------------------------------------------
# Multiple subscribers
# ---------------------------------------------------------------------------


async def test_multiple_subscribers(bus, mock_radio):
    s1 = bus.subscribe(name="s1")
    s2 = bus.subscribe(name="s2")
    await s1.start()
    await s2.start()
    assert bus.subscriber_count == 2
    # RX started only once
    assert mock_radio.start_audio_rx_opus.await_count == 1

    # Remove first — RX still active
    await s1.aclose()
    assert bus.rx_active
    assert bus.subscriber_count == 1

    # Remove second — RX stopped
    await s2.aclose()
    assert not bus.rx_active
    mock_radio.stop_audio_rx_opus.assert_awaited_once()


async def test_aclose_blocks_rapid_restart_until_rx_stop_completes(bus, mock_radio):
    stop_entered = asyncio.Event()
    release_stop = asyncio.Event()
    stop_finished = False
    start_count = 0

    async def start_audio_rx_opus(*_args, **_kwargs):
        nonlocal start_count
        start_count += 1
        if start_count == 2:
            assert stop_finished

    async def stop_audio_rx_opus():
        nonlocal stop_finished
        stop_entered.set()
        await release_stop.wait()
        stop_finished = True

    mock_radio.start_audio_rx_opus.side_effect = start_audio_rx_opus
    mock_radio.stop_audio_rx_opus.side_effect = stop_audio_rx_opus

    old_sub = bus.subscribe(name="old")
    await old_sub.start()

    close_task = asyncio.create_task(old_sub.aclose())
    await asyncio.wait_for(stop_entered.wait(), timeout=1.0)
    assert bus.subscriber_count == 0

    new_sub = bus.subscribe(name="new")
    start_task = asyncio.create_task(new_sub.start())
    await asyncio.sleep(0)
    assert not start_task.done()

    release_stop.set()
    await close_task
    await start_task

    assert new_sub.active
    assert bus.subscriber_count == 1
    assert mock_radio.start_audio_rx_opus.await_count == 2

    await new_sub.aclose()


# ---------------------------------------------------------------------------
# Packet distribution
# ---------------------------------------------------------------------------


async def test_packet_delivery(bus, mock_radio):
    s1 = bus.subscribe(name="s1")
    s2 = bus.subscribe(name="s2")
    await s1.start()
    await s2.start()

    # Simulate radio callback
    pkt = AudioPacket(ident=0x80, send_seq=1, data=b"\x01\x02\x03")
    bus._on_opus_packet(pkt)

    # Both subscribers should receive it
    assert s1._received == 1
    assert s2._received == 1
    r1 = s1.get_nowait()
    r2 = s2.get_nowait()
    assert r1 is pkt
    assert r2 is pkt

    await s1.aclose()
    await s2.aclose()


async def test_packet_delivery_none_gap(bus, mock_radio):
    sub = bus.subscribe(name="s1")
    await sub.start()

    bus._on_opus_packet(None)
    result = sub.get_nowait()
    assert result is None
    assert sub._received == 1

    await sub.aclose()


async def test_inactive_subscriber_ignores_packets(bus, mock_radio):
    sub = bus.subscribe(name="s1")
    # Not started — deliver should be a no-op
    sub.deliver(AudioPacket(ident=0x80, send_seq=0, data=b""))
    assert sub._received == 0


# ---------------------------------------------------------------------------
# Queue overflow (sliding window)
# ---------------------------------------------------------------------------


async def test_queue_overflow_drops_oldest(bus, mock_radio):
    sub = bus.subscribe(name="s1", queue_size=2)
    await sub.start()

    pkt1 = AudioPacket(ident=0x80, send_seq=1, data=b"pkt1")
    pkt2 = AudioPacket(ident=0x80, send_seq=2, data=b"pkt2")
    pkt3 = AudioPacket(ident=0x80, send_seq=3, data=b"pkt3")
    bus._on_opus_packet(pkt1)
    bus._on_opus_packet(pkt2)
    bus._on_opus_packet(pkt3)  # should drop pkt1

    assert sub._dropped == 1
    assert sub._received == 3
    # Queue should have pkt2 and pkt3
    assert sub.get_nowait() is pkt2
    assert sub.get_nowait() is pkt3

    await sub.aclose()


# ---------------------------------------------------------------------------
# Async iteration
# ---------------------------------------------------------------------------


async def test_async_iteration(bus, mock_radio):
    sub = bus.subscribe(name="s1")
    await sub.start()

    pkts = [
        AudioPacket(ident=0x80, send_seq=i, data=f"pkt{i}".encode()) for i in range(3)
    ]
    for p in pkts:
        bus._on_opus_packet(p)

    # Stop after delivering — iteration should end
    await sub.aclose()

    collected = []
    async for pkt in sub:
        collected.append(pkt)
    assert collected == pkts


async def test_cancelled_async_iteration_raises_cancelled_error(bus, mock_radio):
    sub = bus.subscribe(name="s1")
    await sub.start()

    async def consume() -> None:
        async for _pkt in sub:
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await sub.aclose()


async def test_next_cancel_while_pending_on_empty_queue_ends_cancelled(bus, mock_radio):
    """A cancel of a consumer genuinely parked on the empty queue must end
    the iteration as cancelled on every supported Python (MOR-3575).
    """
    sub = bus.subscribe(name="pending-cancel")
    await sub.start()

    task = asyncio.create_task(sub.__anext__())
    await asyncio.sleep(0)  # the consumer runs to its first real suspension
    assert _parked_on_queue_get(task), "consumer is not parked on the queue get"
    task.cancel()

    done, _pending = await asyncio.wait({task}, timeout=2.0)
    assert task in done, "consumer never finished: cancel was swallowed"
    assert task.cancelled(), "a cancel of a parked consumer was not delivered"

    await sub.aclose()


async def test_next_cancel_racing_a_delivered_packet_ends_cancelled(bus, mock_radio):
    """A cancel arriving in the same loop pass as packet delivery must win
    (MOR-3575): the iteration ends cancelled and the packet stays queued
    for the next consumer instead of being lost.

    This is the race the serial soak teardowns deadlocked in, restated
    deterministically: the packet resolves the getter, and the cancel is
    requested before the consumer's wakeup runs — exactly one wakeup, no
    scheduling lottery.
    """
    sub = bus.subscribe(name="racing-cancel")
    await sub.start()

    task = asyncio.create_task(sub.__anext__())
    await asyncio.sleep(0)  # the consumer runs to its first real suspension
    assert _parked_on_queue_get(task), "consumer is not parked on the queue get"
    bus._on_opus_packet(AudioPacket(ident=0x80, send_seq=1, data=b"x"))
    # No yield here: the getter is resolved but the consumer has not run,
    # so the cancel is requested into the same wakeup the packet scheduled.
    task.cancel()

    done, _pending = await asyncio.wait({task}, timeout=2.0)
    assert task in done, "consumer never finished: cancel was swallowed"
    assert task.cancelled(), "a cancel racing a delivered packet was swallowed"
    assert sub._queue.get_nowait() == AudioPacket(ident=0x80, send_seq=1, data=b"x"), (
        "the racing packet was consumed or lost"
    )

    await sub.aclose()


async def test_next_during_cancellation_cleanup_returns_the_packet(bus, mock_radio):
    """A consumer that caught CancelledError and awaits an already queued
    packet during cleanup must get that packet, not a fresh CancelledError
    (MOR-3575). The old ``Task.cancelling()`` guard broke exactly this:
    the historical count made every later receive look cancelled.
    """
    sub = bus.subscribe(name="cleanup-receive")
    await sub.start()
    packet = AudioPacket(ident=0x80, send_seq=1, data=b"cleanup")
    sub._queue.put_nowait(packet)

    async def cleanup_consumer() -> AudioPacket | None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass  # cleanup begins; the cancel request count stays raised
        return await sub.__anext__()

    task = asyncio.create_task(cleanup_consumer())
    await asyncio.sleep(0)  # the consumer parks in the sleep
    assert not task.done()
    task.cancel()

    got = await task
    assert got == packet
    assert not task.cancelled()

    await sub.aclose()


async def test_next_timeout_while_active_returns_none(bus, mock_radio):
    sub = bus.subscribe(name="timeout-none")
    await sub.start()

    pkt = await sub.__anext__()  # 1 s poll window on an active subscription

    assert pkt is None

    await sub.aclose()


async def test_stopped_empty_subscription_ends_async_iteration(bus, mock_radio):
    sub = bus.subscribe(name="s1")
    await sub.start()
    await sub.aclose()

    collected = [pkt async for pkt in sub]
    assert collected == []


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


async def test_context_manager(bus, mock_radio):
    async with bus.subscribe(name="ctx") as sub:
        assert sub.active
        assert bus.subscriber_count == 1

        pkt = AudioPacket(ident=0x80, send_seq=1, data=b"context")
        bus._on_opus_packet(pkt)
        result = sub.get_nowait()
        assert result is pkt

    # After exit, unsubscribed via awaited context-manager teardown.
    assert not sub.active
    assert bus.subscriber_count == 0


# ---------------------------------------------------------------------------
# Bus stop
# ---------------------------------------------------------------------------


async def test_bus_stop_all(bus, mock_radio):
    s1 = bus.subscribe(name="s1")
    s2 = bus.subscribe(name="s2")
    await s1.start()
    await s2.start()

    await bus.stop()
    assert not s1.active
    assert not s2.active
    assert bus.subscriber_count == 0
    assert not bus.rx_active


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


async def test_subscription_stats(bus, mock_radio):
    sub = bus.subscribe(name="test-sub")
    await sub.start()

    bus._on_opus_packet(AudioPacket(ident=0x80, send_seq=1, data=b"stat1"))
    bus._on_opus_packet(AudioPacket(ident=0x80, send_seq=2, data=b"stat2"))

    stats = sub.stats
    assert stats["name"] == "test-sub"
    assert stats["active"] is True
    assert stats["received"] == 2
    assert stats["dropped"] == 0
    assert stats["queued"] == 2

    await sub.aclose()


async def test_bus_stats(bus, mock_radio):
    s1 = bus.subscribe(name="a")
    s2 = bus.subscribe(name="b")
    await s1.start()
    await s2.start()

    stats = bus.stats
    assert stats["rx_active"] is True
    assert stats["subscriber_count"] == 2
    assert len(stats["subscribers"]) == 2

    await s1.aclose()
    await s2.aclose()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_rx_start_failure_raises_to_first_subscriber(mock_radio):
    """[BC] MOR-582: the demanding subscriber's RX-start failure PROPAGATES.

    Pre-MOR-582 the bus swallowed the error and left the subscriber
    registered-but-silent (dead air). Now ``start()`` raises (RuntimeError
    chained from the radio error) and the registration is rolled back.
    """
    mock_radio.start_audio_rx_opus = AsyncMock(
        side_effect=ConnectionError("not connected")
    )
    bus = AudioBus(mock_radio)
    sub = bus.subscribe(name="s1")
    with pytest.raises(RuntimeError, match="radio RX failed to start") as excinfo:
        await sub.start()
    assert isinstance(excinfo.value.__cause__, ConnectionError)
    assert not bus.rx_active
    assert not sub.active  # never falsely "attached"
    assert bus.subscriber_count == 0  # registration rolled back

    # No poisoned state: repair the radio and the same subscription recovers.
    mock_radio.start_audio_rx_opus = AsyncMock()
    await sub.start()
    assert bus.rx_active
    assert sub.active
    assert bus.subscriber_count == 1
    await sub.aclose()


async def test_get_with_timeout(bus, mock_radio):
    sub = bus.subscribe(name="s1")
    await sub.start()

    with pytest.raises(asyncio.TimeoutError):
        await sub.get(timeout=0.01)

    await sub.aclose()


async def test_remove_nonexistent_subscriber(bus):
    sub = AudioSubscription(bus, name="ghost")
    # Should not raise
    await bus._remove_subscriber(sub)


# ---------------------------------------------------------------------------
# Neutral AudioTransport surface (MOR-542)
# ---------------------------------------------------------------------------


class _NeutralRadio:
    """Fake exposing the neutral surface; ``start_rx`` takes callback only."""

    def __init__(self):
        self.start_calls: list[tuple] = []
        self.stop_calls = 0
        self.start_audio_rx_opus = AsyncMock()
        self.stop_audio_rx_opus = AsyncMock()

    async def start_rx(self, callback):
        self.start_calls.append((callback,))

    async def stop_rx(self):
        self.stop_calls += 1


class _NeutralJitterRadio(_NeutralRadio):
    """Fake whose ``start_rx`` accepts the optional ``jitter_depth`` kwarg."""

    async def start_rx(self, callback, *, jitter_depth=5):
        self.start_calls.append((callback, jitter_depth))


async def test_bus_prefers_neutral_surface_over_legacy():
    radio = _NeutralRadio()
    bus = AudioBus(radio)
    sub = bus.subscribe(name="s1")
    await sub.start()
    assert radio.start_calls == [(bus._on_opus_packet,)]
    radio.start_audio_rx_opus.assert_not_awaited()

    await sub.aclose()
    assert radio.stop_calls == 1
    radio.stop_audio_rx_opus.assert_not_awaited()


async def test_neutral_start_rx_threads_jitter_depth_when_accepted():
    radio = _NeutralJitterRadio()
    bus = AudioBus(radio, jitter_depth=9)
    sub = bus.subscribe(name="s1")
    await sub.start()
    assert radio.start_calls == [(bus._on_opus_packet, 9)]
    await sub.aclose()


async def test_restart_rx_rearm_uses_neutral_surface():
    radio = _NeutralRadio()
    bus = AudioBus(radio)
    sub = bus.subscribe(name="s1")
    await sub.start()

    await bus.restart_rx()  # MOR-506 re-arm after a TX cycle

    assert radio.start_calls == [(bus._on_opus_packet,)] * 2
    assert bus.rx_active
    radio.start_audio_rx_opus.assert_not_awaited()
    await sub.aclose()


async def test_restart_rx_noop_without_subscribers_neutral():
    radio = _NeutralRadio()
    bus = AudioBus(radio)
    await bus.restart_rx()
    assert radio.start_calls == []
    assert not bus.rx_active


async def test_restart_rx_failure_nonfatal_but_observable():
    """MOR-582: a re-arm failure must not crash an established session,
    but must not be masked as success — ``rx_active`` reflects reality."""
    radio = _NeutralRadio()
    bus = AudioBus(radio)
    sub = bus.subscribe(name="s1")
    await sub.start()
    assert bus.rx_active

    async def _failing_start_rx(callback):
        raise ConnectionError("re-arm hiccup")

    radio.start_rx = _failing_start_rx
    await bus.restart_rx()  # non-fatal: must NOT raise

    assert not bus.rx_active  # observable (step-14 watchdog input)
    assert sub.active  # established subscriber survives
    assert bus.subscriber_count == 1
    await sub.aclose()
