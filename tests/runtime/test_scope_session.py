import pytest
import asyncio

from rigplane.core.types import ScopeFixedEdge
from rigplane.runtime.scope_session import ScopeSession, ScopeState
from rigplane.scope.models import ScopeDemand
from rigplane.scope import ScopeFrame
from dataclasses import replace


class Writer:
    def __init__(self):
        self.state = ScopeState(False, False)
        self.views = []
        self.writes = []
        self.fail = False

    async def read(self):
        return self.state

    async def write(self, name, value, receiver):
        if name in ("scope_on", "output_on"):
            self.writes.append((0x10 if name == "scope_on" else 0x11, value))
        else:
            self.views.append((name, value, receiver))
        if self.fail:
            raise RuntimeError("no acknowledgement")
        if name != "fixed_edge":
            self.state = replace(self.state, **{name: value})

    async def read_fixed(self, range_index, edge):
        return ScopeFixedEdge(range_index, edge, 14000000, 14350000)


async def test_one_enable_exact_restore_once():
    writer = Writer()
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    await session.set_demand(ScopeDemand(2, 2))
    assert session.enabled
    assert writer.writes == [(0x10, True), (0x11, True)]
    await session.set_demand(ScopeDemand(1, 1))
    await session.set_demand(ScopeDemand(0, 0))
    await session.set_demand(ScopeDemand(0, 0))
    assert writer.writes[-2:] == [(0x10, False), (0x11, False)]
    assert len(writer.writes) == 4
    assert not session.enabled


async def test_deferred_enable_and_tx_have_no_writes():
    writer = Writer()
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_demand(ScopeDemand(1, 1))
    assert not session.enabled
    assert not writer.writes
    await session.set_inputs(ready=True, tx=True)
    assert not writer.writes
    await session.set_inputs(ready=True, tx=False)
    await session.set_inputs(ready=True, tx=False)
    assert writer.writes == [(0x10, True), (0x11, True)]


async def test_enabled_requires_acknowledgement():
    writer = Writer()
    writer.fail = True
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    with pytest.raises(RuntimeError, match="acknowledgement"):
        await session.set_demand(ScopeDemand(1, 1))
    assert not session.enabled


async def test_pause_only_output_resume_once_and_release_restores(virtual_clock):
    writer = Writer()
    writer.state = ScopeState(True, False)
    session = ScopeSession(
        writer.read, writer.write, writer.read_fixed, clock=virtual_clock
    )
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(2, 2))
    await session.set_demand(ScopeDemand(2, 0))
    await session.set_demand(ScopeDemand(1, 0))
    assert writer.writes == [(0x11, True), (0x11, False)]
    started = virtual_clock()
    await session.set_demand(ScopeDemand(1, 1))
    assert virtual_clock() - started <= 0.1
    assert writer.writes[-1] == (0x11, True)
    await session.set_demand(ScopeDemand(0, 0))
    assert writer.state == ScopeState(True, False)
    assert writer.writes == [(0x11, True), (0x11, False), (0x11, True), (0x11, False)]


async def test_view_and_touched_fixed_slot_restored():
    writer = Writer()
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    fixed = ScopeFixedEdge(7, 1, 14050000, 14150000)
    await session.set_fixed(fixed, 0)
    await session.set_fixed(fixed, 0)
    assert writer.views == [("fixed_edge", fixed, 0), ("mode", 1, 0)]
    await session.set_demand(ScopeDemand(0, 0))
    assert ("mode", 0, 0) in writer.views[2:]
    assert ("fixed_edge", ScopeFixedEdge(7, 1, 14000000, 14350000), 0) in writer.views[
        2:
    ]


async def test_oor_is_arrival_never_hidden(virtual_clock):
    session = ScopeSession(
        Writer().read, Writer().write, clock=virtual_clock, silence_seconds=1
    )
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    session.on_frame(ScopeFrame(0, 1, 14000000, 14350000, b"", True))
    assert session.health(civ_flowing=True).hidden is False


async def test_release_grace_cancelled_by_resume(virtual_clock):
    writer = Writer()
    session = ScopeSession(
        writer.read,
        writer.write,
        writer.read_fixed,
        clock=virtual_clock,
        disable_grace_seconds=0.25,
    )
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    await session.set_demand(ScopeDemand(0, 0))
    assert len(writer.writes) == 2
    virtual_clock.advance(0.1)
    await session.set_demand(ScopeDemand(1, 1))
    virtual_clock.advance(1)
    await session.reconcile()
    assert len(writer.writes) == 2
    await session.set_demand(ScopeDemand(0, 0))
    virtual_clock.advance(0.25)
    await session.reconcile()
    assert writer.writes[-2:] == [(0x10, False), (0x11, False)]


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
    await session.set_ready(True)
    await session.set_ready(False)
    await session.set_demand(ScopeDemand(1, 1))
    assert not writes and not session.enabled
    await session.set_ready(True)
    assert writes == [("scope_on", True, 0), ("output_on", True, 0)]
    await session.set_demand(ScopeDemand(2, 2))
    assert len(writes) == 2
    await session.configure(mode=1, span=4)
    await session.set_demand(ScopeDemand(1, 1))
    assert session.enabled
    await session.set_demand(ScopeDemand(0, 0))
    assert session.observed == original
    restored = list(writes)
    await session.set_demand(ScopeDemand(0, 0))
    assert writes == restored


@pytest.mark.asyncio
async def test_no_writes_during_tx_and_enabled_waits_for_ack():

    ack = asyncio.Event()
    writes = []

    async def read():
        return ScopeState(False, False, 0, 0, 1)

    async def write(name, value, receiver):
        writes.append(name)
        if name == "output_on" and value:
            await ack.wait()

    session = ScopeSession(read, write)
    await session.set_ready(True)
    await session.set_tx(True)
    await session.set_demand(ScopeDemand(1, 1))
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
    await session.set_ready(True)
    await session.set_demand(ScopeDemand(1, 1))
    await session.invalidate()
    await session.set_ready(True)
    await session.set_demand(ScopeDemand(0, 0))
    assert captures == 1
    assert current == original


async def test_sdk_demand_survives_web_pause_and_last_owner_restores():
    writer = Writer()
    session = ScopeSession(writer.read, writer.write, ready=True)
    await session.set_demand(ScopeDemand(1, 1), owner="sdk")
    await session.set_demand(ScopeDemand(3, 0))
    assert session.enabled
    assert writer.writes == [(0x10, True), (0x11, True)]
    await session.set_demand(ScopeDemand(0, 0), owner="sdk")
    assert writer.writes[-1] == (0x11, False)
    assert session.active
    await session.set_demand(ScopeDemand(0, 0))
    assert writer.state == ScopeState(False, False)
    assert not session.active


async def test_lost_output_ack_still_restores_the_uncertain_write():
    original = ScopeState(False, False)
    current = original
    writes = []

    async def read():
        return original

    async def write(name, value, receiver):
        nonlocal current
        writes.append((name, value))
        current = replace(current, **{name: value})
        if name == "output_on" and value:
            raise RuntimeError("lost ack")

    session = ScopeSession(read, write, ready=True)
    with pytest.raises(RuntimeError, match="lost ack"):
        await session.set_demand(ScopeDemand(1, 1))
    assert not session.enabled
    await session.set_demand(ScopeDemand(0, 0))
    assert current == original
    assert writes[-1] == ("output_on", False)
