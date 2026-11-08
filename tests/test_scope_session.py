"""Scope ownership and acknowledged state transitions."""

from dataclasses import replace

import pytest

from rigplane.runtime.scope_session import ScopeSession, ScopeState


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["serial", "lan"])
async def test_shared_demand_ack_restore_and_pause(transport):
    original = ScopeState(False, False, 0, 2, 1)
    writes = []

    async def read():
        return original

    async def write(name, value, receiver):
        writes.append((name, value, receiver))

    session = ScopeSession(read, write)
    await session.set_ready(False)
    await session.demand("web", True)
    assert not writes and not session.enabled
    await session.set_ready(True)
    assert writes == [("scope_on", True, 0), ("output_on", True, 0)]
    await session.demand("sdk", True)
    assert len(writes) == 2
    await session.configure(mode=1, span=4)
    await session.demand("web", False)
    assert session.enabled
    await session.demand("sdk", False)
    assert session.observed == original
    restored = list(writes)
    await session.demand("sdk", False)
    assert writes == restored


@pytest.mark.asyncio
async def test_no_writes_during_tx_and_enabled_waits_for_ack():
    import asyncio

    ack = asyncio.Event()
    writes = []

    async def read():
        return ScopeState(False, False, 0, 0, 1)

    async def write(name, value, receiver):
        writes.append(name)
        if name == "output_on" and value:
            await ack.wait()

    session = ScopeSession(read, write)
    await session.set_tx(True)
    await session.demand("web", True)
    assert not writes
    task = asyncio.create_task(session.set_tx(False))
    from rigplane.testing.timing import await_until

    assert await await_until(lambda: "output_on" in writes)
    assert not session.enabled
    ack.set()
    await task
    assert session.enabled


@pytest.mark.asyncio
async def test_reconnect_retains_original_capture():
    original = ScopeState(False, False, 0, 1, 1)
    current = original
    captures = 0

    async def read():
        nonlocal captures
        captures += 1
        return current

    async def write(name, value, receiver):
        nonlocal current
        current = replace(current, **{name: value})

    session = ScopeSession(read, write)
    await session.demand("web", True)
    await session.invalidate()
    await session.set_ready(True)
    await session.demand("web", False)
    assert captures == 1
    assert current == original
