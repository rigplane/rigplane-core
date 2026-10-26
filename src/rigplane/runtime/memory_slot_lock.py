"""Serialize memory-slot CI-V/CAT operations on one radio (MOR-3768).

Grouped recall (group then channel) and per-channel memory commands must not
interleave on the wire. Every backend that touches memory slots owns a
``_memory_select_lock`` and enters it through :func:`memory_slot_operation`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

import asyncio


class MemorySlotLockHost(Protocol):
    _memory_select_lock: asyncio.Lock


@asynccontextmanager
async def memory_slot_operation(radio: MemorySlotLockHost) -> AsyncIterator[None]:
    async with radio._memory_select_lock:
        yield
