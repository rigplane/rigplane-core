
import pytest

from rigplane.core.types import ScopeFixedEdge
from rigplane.runtime.scope_session import ScopeSession, ScopeState, ScopeView
from rigplane.scope.models import ScopeDemand
from rigplane.scope import ScopeFrame


class Writer:
    def __init__(self):
        self.state = ScopeState(False, False)
        self.views = []
        self.writes = []
        self.fail = False

    async def _scope_read_state(self):
        return self.state

    async def _scope_write_state(self, previous, desired, *, policy, timeout):
        if previous.scope_on != desired.scope_on:
            self.writes.append((0x10, desired.scope_on))
        if previous.output_on != desired.output_on:
            self.writes.append((0x11, desired.output_on))
        if self.fail:
            raise RuntimeError("no acknowledgement")
        self.state = desired

    async def _scope_read_view(self):
        return ScopeView(0, 0, 2, 1)

    async def _scope_write_view(self, view):
        self.views.append(view)

    async def _scope_read_fixed(self, range_index, edge):
        return ScopeFixedEdge(range_index, edge, 14000000, 14350000)


async def test_one_enable_exact_restore_once():
    writer = Writer()
    session = ScopeSession(writer)
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
    session = ScopeSession(writer)
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
    session = ScopeSession(writer)
    await session.set_inputs(ready=True, tx=False)
    with pytest.raises(RuntimeError, match="acknowledgement"):
        await session.set_demand(ScopeDemand(1, 1))
    assert not session.enabled


async def test_pause_only_output_resume_once_and_release_restores(virtual_clock):
    writer = Writer()
    writer.state = ScopeState(True, False)
    session = ScopeSession(writer, clock=virtual_clock)
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
    session = ScopeSession(writer)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    fixed = ScopeFixedEdge(7, 1, 14050000, 14150000)
    await session.set_fixed(fixed, 0)
    await session.set_fixed(fixed, 0)
    assert len(writer.views) == 1
    await session.set_demand(ScopeDemand(0, 0))
    assert writer.views[-1] == ScopeView(0, 0, 2, 1)
    assert writer.views[-2].fixed_edge == ScopeFixedEdge(7, 1, 14000000, 14350000)


async def test_oor_is_arrival_never_hidden(virtual_clock):
    session = ScopeSession(Writer(), clock=virtual_clock, silence_seconds=1)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    session.on_frame(ScopeFrame(0, 1, 14000000, 14350000, b"", True))
    assert session.health(civ_flowing=True).hidden is False


async def test_release_grace_cancelled_by_resume(virtual_clock):
    writer = Writer()
    session = ScopeSession(writer, clock=virtual_clock, disable_grace_seconds=0.25)
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
