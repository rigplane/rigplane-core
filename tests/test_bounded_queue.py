"""Unit tests for ``icom_lan._bounded_queue.BoundedQueue``."""

from __future__ import annotations

import asyncio

import pytest

from icom_lan.core._bounded_queue import BoundedQueue


def test_put_get_preserves_fifo_order() -> None:
    q: BoundedQueue[int] = BoundedQueue(maxsize=4)
    for i in range(4):
        q.put_nowait(i)
    out = [q.get_nowait() for _ in range(4)]
    assert out == [0, 1, 2, 3]


def test_qsize_full_empty_maxsize() -> None:
    q: BoundedQueue[str] = BoundedQueue(maxsize=2)
    assert q.empty() is True
    assert q.full() is False
    assert q.qsize() == 0
    assert q.maxsize == 2

    q.put_nowait("a")
    assert q.empty() is False
    assert q.full() is False
    assert q.qsize() == 1

    q.put_nowait("b")
    assert q.full() is True
    assert q.qsize() == 2


def test_put_nowait_raises_when_full() -> None:
    q: BoundedQueue[int] = BoundedQueue(maxsize=1)
    q.put_nowait(1)
    with pytest.raises(asyncio.QueueFull):
        q.put_nowait(2)


def test_get_nowait_raises_when_empty() -> None:
    q: BoundedQueue[int] = BoundedQueue(maxsize=1)
    with pytest.raises(asyncio.QueueEmpty):
        q.get_nowait()


def test_put_drop_oldest_evicts_when_full() -> None:
    q: BoundedQueue[int] = BoundedQueue(maxsize=3)
    for i in range(3):
        q.put_nowait(i)
    assert q.full()

    q.put_drop_oldest(99)
    assert q.full()
    assert q.qsize() == 3
    out = [q.get_nowait() for _ in range(3)]
    assert out == [1, 2, 99]


def test_put_drop_oldest_does_not_evict_when_not_full() -> None:
    q: BoundedQueue[int] = BoundedQueue(maxsize=3)
    q.put_nowait(1)
    q.put_drop_oldest(2)
    assert q.qsize() == 2
    assert q.get_nowait() == 1
    assert q.get_nowait() == 2


@pytest.mark.asyncio
async def test_async_get_returns_item() -> None:
    q: BoundedQueue[str] = BoundedQueue(maxsize=2)
    q.put_nowait("hello")
    assert await q.get() == "hello"


@pytest.mark.asyncio
async def test_async_put_blocks_until_space() -> None:
    q: BoundedQueue[int] = BoundedQueue(maxsize=1)
    q.put_nowait(1)

    async def consume_after_delay() -> None:
        await asyncio.sleep(0.01)
        assert q.get_nowait() == 1

    consumer = asyncio.create_task(consume_after_delay())
    await q.put(2)
    await consumer
    assert q.get_nowait() == 2


@pytest.mark.asyncio
async def test_task_done_allows_join() -> None:
    q: BoundedQueue[int] = BoundedQueue(maxsize=2)
    q.put_nowait(1)
    q.put_nowait(2)
    assert q.get_nowait() == 1
    q.task_done()
    assert q.get_nowait() == 2
    q.task_done()
    await asyncio.wait_for(q._queue.join(), timeout=0.1)
