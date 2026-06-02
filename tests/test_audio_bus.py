"""Tests for AudioBus pub/sub audio distribution."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.audio import AudioPacket
from rigplane.audio_bus import AudioBus, AudioSubscription


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


async def test_rx_start_failure_handled(mock_radio):
    mock_radio.start_audio_rx_opus = AsyncMock(
        side_effect=ConnectionError("not connected")
    )
    bus = AudioBus(mock_radio)
    sub = bus.subscribe(name="s1")
    await sub.start()
    # Should not crash, rx_active stays False
    assert not bus.rx_active


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
