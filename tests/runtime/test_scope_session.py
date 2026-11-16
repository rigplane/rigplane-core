import pytest
import asyncio

from rigplane.core.exceptions import CommandRejectedError, TimeoutError
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


async def test_initial_paused_demand_never_enables_panel_and_keeps_hub_count():
    writer = Writer()
    session = ScopeSession(writer.read, writer.write, ready=True)
    await session.set_demand(ScopeDemand(3, 0))
    assert writer.writes == []
    generation = session.generation
    await session.set_output(False, release=False)
    assert session.generation == generation
    await session.set_demand(ScopeDemand(3, 1))
    assert writer.writes == [(0x10, True), (0x11, True)]
    await session.set_demand(ScopeDemand(3, 0))
    assert writer.writes[-1] == (0x11, False)
    assert writer.writes.count((0x10, False)) == 0
    await session.set_demand(ScopeDemand(0, 0))
    assert writer.state == ScopeState(False, False)


async def test_invalid_view_write_does_not_poison_the_next_request():
    writes = []

    async def read():
        return ScopeState(False, False)

    async def write(name, value, receiver):
        writes.append((name, value))
        if name == "mode" and value == 99:
            raise ValueError("invalid mode")

    session = ScopeSession(read, write, ready=True)
    with pytest.raises(ValueError, match="invalid mode"):
        await session.configure(mode=99)
    await session.configure(span=4)
    assert writes == [("mode", 99), ("span", 4)]


async def test_restore_only_writes_view_settings_the_session_changed():
    writer = Writer()
    session = ScopeSession(
        writer.read, writer.write, ready=True, spans=(2500, 5000, 10000, 25000)
    )
    await session.set_output(True)
    await session.set_center(14100000, 25000, 0)
    assert writer.views == [("span", 3, 0)]
    await session.set_output(False)
    assert writer.views == [("span", 3, 0), ("span", 2, 0)]


async def test_shared_fixed_slot_is_actuated_again_after_other_receiver_changes_it():
    writer = Writer()
    session = ScopeSession(writer.read, writer.write, writer.read_fixed, ready=True)
    await session.set_output(True)
    first = ScopeFixedEdge(6, 1, 14000000, 14100000)
    second = ScopeFixedEdge(6, 1, 14100000, 14200000)
    await session.set_fixed(first, 0)
    await session.set_fixed(second, 1)
    await session.set_fixed(first, 0)
    assert [
        value for name, value, receiver in writer.views if name == "fixed_edge"
    ] == [first, second, first]


async def test_last_release_before_readiness_discards_the_pending_view():
    writer = Writer()
    session = ScopeSession(writer.read, writer.write)
    await session.set_demand(ScopeDemand(1, 1))
    await session.configure(mode=1, span=3)
    await session.set_demand(ScopeDemand(0, 0))
    await session.set_ready(True)
    assert writer.writes == [] and writer.views == []
    assert not session.active


async def test_restored_output_without_subscribers_has_no_menu_verdict(virtual_clock):
    writer = Writer()
    writer.state = ScopeState(True, True)
    session = ScopeSession(writer.read, writer.write, ready=True, clock=virtual_clock)
    await session.set_output(True)
    await session.set_output(False)
    virtual_clock.advance(60)
    assert session.observed.output_on is True
    assert session.health(civ_flowing=True).hidden is None


async def test_missing_frames_after_oor_use_the_arrival_deadline(virtual_clock):
    writer = Writer()
    session = ScopeSession(
        writer.read, writer.write, ready=True, clock=virtual_clock, silence_seconds=1
    )
    await session.set_output(True)
    session.on_frame(ScopeFrame(0, 1, 14000000, 14350000, b"", True))
    assert session.health(civ_flowing=True).hidden is False
    virtual_clock.advance(2)
    assert session.health(civ_flowing=True).hidden is True


async def test_restore_keeps_the_actual_view_observation_instead_of_cached_defaults():
    writer = Writer()
    writer.state = ScopeState(False, False, 3, 2, 1)

    async def read_flags():
        return ScopeState(False, False, 0, 2, 1)

    async def read_receiver(receiver):
        return writer.state

    session = ScopeSession(
        read_flags, writer.write, read_receiver=read_receiver, ready=True
    )
    await session.set_output(True)
    await session.configure(mode=1)
    await session.set_output(False)
    assert session.observed.mode == writer.state.mode == 3
    await session.configure(mode=0)
    assert writer.state.mode == 0


async def test_matching_fixed_slot_is_not_programmed_or_restored_again():
    writer = Writer()
    writer.state = ScopeState(False, False, 1, 2, 1)
    session = ScopeSession(writer.read, writer.write, writer.read_fixed, ready=True)
    await session.set_output(True)
    await session.set_fixed(ScopeFixedEdge(6, 1, 14000000, 14350000), 0)
    assert writer.views == []
    await session.set_output(False)
    assert writer.views == []


async def test_fixed_readback_updates_the_shared_slot_across_receivers():
    writer = Writer()
    session = ScopeSession(writer.read, writer.write, ready=True)
    desired = ScopeFixedEdge(6, 1, 14000000, 14350000)
    await session.configure(receiver=1, fixed_edge=desired)
    session.observe("fixed_edge", ScopeFixedEdge(6, 1, 14100000, 14300000), 0)
    await session.configure(receiver=1, fixed_edge=desired)
    assert writer.views == [("fixed_edge", desired, 1), ("fixed_edge", desired, 1)]


# ── MOR-4385 gate round 2 (fix-4565): a retired snapshot must not outlive
# the desired key. configure() pops desired keys (FIX/CENTER switches, a
# same-slot retirement under another receiver) and _put() failure paths pop
# desired keys while the restore snapshot stays behind; a later external
# change that supersedes Core's last applied value must release that
# snapshot, or the last unsubscribe restores the stale value over the
# operator's own («следовать за рацией»).


async def test_external_span_releases_snapshot_retired_by_fix_configure():
    """Path a: configure(mode=1, fixed_edge=...) pops desired span while the
    span restore snapshot stays behind; an external span change that
    supersedes Core's last applied span releases it, so the last unsubscribe
    never restores the stale span."""
    writer = Writer()
    writer.state = ScopeState(True, True, 0, 3, 1)
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    await session.configure(span=4)
    assert writer.views[-1] == ("span", 4, 0)
    fixed = ScopeFixedEdge(6, 1, 14050000, 14150000)
    await session.configure(mode=1, fixed_edge=fixed, edge=1)
    assert (0, "span") not in session._desired
    assert session._previous.get((0, "span")) == 3
    session.observe("span", 5)
    assert (0, "span") not in session._previous
    await session.set_demand(ScopeDemand(0, 0))
    assert ("span", 3, 0) not in writer.views


async def test_external_edge_releases_snapshot_retired_by_center_configure():
    """Path b: configure(mode=0, span=...) pops desired fixed_edge/edge while
    the edge restore snapshot stays behind; an external edge change that
    supersedes Core's last applied edge releases it."""
    writer = Writer()
    writer.state = ScopeState(True, True, 0, 3, 1)
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    fixed = ScopeFixedEdge(6, 1, 14050000, 14150000)
    await session.configure(mode=1, fixed_edge=fixed, edge=2)
    assert (0, "edge") in session._desired
    await session.configure(mode=0, span=4)
    assert (0, "edge") not in session._desired
    assert (0, "fixed_edge") not in session._desired
    assert session._previous.get((0, "edge")) == 1
    session.observe("edge", 3)
    assert (0, "edge") not in session._previous
    await session.set_demand(ScopeDemand(0, 0))
    assert ("edge", 1, 0) not in writer.views


async def test_external_rewrite_releases_slot_retired_for_other_receiver():
    """Path c: configuring the same fixed slot under receiver 1 deletes
    receiver 0's desired key while the shared per-slot snapshot and receiver
    0's applied value stay behind; an external rewrite of that slot still
    releases the slot snapshot."""
    writer = Writer()
    writer.state = ScopeState(True, True, 0, 3, 1)

    async def write(name, value, receiver):
        if name == "fixed_edge" and receiver == 1:
            raise CommandRejectedError("refused")
        await writer.write(name, value, receiver)

    session = ScopeSession(writer.read, write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    slot_value = ScopeFixedEdge(6, 1, 14050000, 14150000)
    await session.configure(receiver=0, fixed_edge=slot_value, edge=1, mode=1)
    assert session._fixed.get((6, 1)) is not None
    with pytest.raises(CommandRejectedError, match="refused"):
        await session.configure(receiver=1, fixed_edge=slot_value)
    # Path c retired receiver 0's desired key; the rejected receiver-1 write
    # left receiver 0's applied value and the shared slot snapshot behind.
    assert (0, "fixed_edge") not in session._desired
    assert session._fixed.get((6, 1)) is not None
    session.observe("fixed_edge", ScopeFixedEdge(6, 1, 14060000, 14160000), 0)
    assert (6, 1) not in session._fixed
    await session.set_demand(ScopeDemand(0, 0))
    assert ScopeFixedEdge(6, 1, 14000000, 14350000) not in [
        value for name, value, _ in writer.views if name == "fixed_edge"
    ]


async def test_ambiguous_span_write_keeps_snapshot_despite_later_observation():
    """Path d: a timed-out (ambiguous) write pops desired but keeps
    _uncertain; a later observation differing from the last applied value
    must NOT release the restore snapshot, so the last unsubscribe still
    restores it."""
    writer = Writer()
    writer.state = ScopeState(True, True, 0, 3, 1)
    failing = True

    async def write(name, value, receiver):
        if failing and name == "span":
            raise TimeoutError("no ack")
        await writer.write(name, value, receiver)

    session = ScopeSession(writer.read, write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    with pytest.raises(TimeoutError, match="no ack"):
        await session.configure(span=4)
    assert (0, "span") not in session._desired
    assert (0, "span") in session._uncertain
    assert session._previous.get((0, "span")) == 3
    failing = False
    session.observe("span", 5)
    assert session._previous.get((0, "span")) == 3
    await session.set_demand(ScopeDemand(0, 0))
    assert ("span", 3, 0) in writer.views


class _LosingAckSpanWriter(Writer):
    """A writer whose first span SET lands on the radio but loses its ACK.

    The write is applied to the radio state BEFORE the raise, so the radio
    sits on the written value while Core never sees the acknowledgement —
    the exact ambiguous-write shape of the #4565 pre-review (item 4).
    """

    def __init__(self):
        super().__init__()
        self.state = ScopeState(True, True, 0, 3, 1)
        self.span_writes: list[tuple[object, int]] = []
        self.lost = True

    async def write(self, name, value, receiver):
        if name != "span":
            await super().write(name, value, receiver)
            return
        self.span_writes.append((value, receiver))
        self.state = replace(self.state, span=value)
        if self.lost:
            raise TimeoutError("ack lost")
        self.views.append((name, value, receiver))


async def test_lost_span_ack_keeps_cached_preset_untrusted():
    """#4565 pre-review (scope_session._put): a SET that reaches the radio
    but loses its ACK leaves the cached span stale — the cache predates the
    ambiguous write, so a later request for that cached preset must still
    write instead of trusting it (the radio may sit on the unacknowledged
    value). The resent SET re-establishes certainty."""
    writer = _LosingAckSpanWriter()
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    with pytest.raises(TimeoutError, match="ack lost"):
        await session.configure(span=4)
    assert writer.state.span == 4  # the radio moved; Core never saw the ACK
    assert session._applied.get((0, "span")) == 3  # the stale cache
    assert (0, "span") in session._uncertain
    # Requesting the CACHED preset must not trust it: the SET goes out
    # (and its acknowledgement re-establishes certainty).
    writer.lost = False
    await session.configure(span=3)
    assert writer.span_writes == [(4, 0), (3, 0)]
    assert writer.state.span == 3
    assert (0, "span") not in session._uncertain


async def test_lost_span_ack_restore_writes_despite_stale_cache():
    """#4565 pre-review (restore loop): with observations withheld after a
    lost ACK, the last unsubscribe must still send the snapshot's span SET
    instead of reporting a clean restore while the radio keeps the
    unacknowledged value."""
    writer = _LosingAckSpanWriter()
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    with pytest.raises(TimeoutError, match="ack lost"):
        await session.configure(span=4)
    writer.lost = False
    await session.set_demand(ScopeDemand(0, 0))
    assert writer.span_writes == [(4, 0), (3, 0)]
    assert writer.state.span == 3


async def test_stale_span_echo_after_lost_ack_still_writes():
    """A report equal to the stale cache cannot prove the ambiguous write
    did not land (it may be a pre-write echo), so the cached span stays
    untrusted and the same-value request still writes."""
    writer = _LosingAckSpanWriter()
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    with pytest.raises(TimeoutError, match="ack lost"):
        await session.configure(span=4)
    session.observe("span", 3)  # a stale echo of the pre-write state
    assert (0, "span") in session._uncertain
    writer.lost = False
    await session.configure(span=3)
    assert writer.span_writes == [(4, 0), (3, 0)]
    assert writer.state.span == 3


async def test_post_ambiguity_span_report_renews_cached_currency():
    """A report that DIFFERS from the stale cache cannot be a pre-write
    echo — the ambiguous write landed (or the radio moved on by itself) —
    so it renews the cache's currency: the key leaves _uncertain at
    observation time and the same-value request writes nothing (the
    adopted value is never re-asserted). RED on the old head, which never
    renews currency before a same-value _put."""
    writer = _LosingAckSpanWriter()
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    with pytest.raises(TimeoutError, match="ack lost"):
        await session.configure(span=4)
    session.observe("span", 4)  # fresh report: the radio shows the value
    assert (0, "span") not in session._uncertain
    await session.configure(span=4)
    assert writer.span_writes == [(4, 0)]


async def test_external_fixed_rewrite_releases_slot_after_center_retires_it():
    """Path e: the per-slot _fixed snapshot follows the same rule as
    _previous — once CENTER retires the desired fixed_edge key, an external
    rewrite of that slot releases the slot snapshot so close restores
    nothing for it."""
    writer = Writer()
    writer.state = ScopeState(True, True, 0, 3, 1)
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    requested = ScopeFixedEdge(6, 1, 14050000, 14150000)
    await session.configure(mode=1, fixed_edge=requested, edge=1)
    assert session._fixed.get((6, 1)) == ScopeFixedEdge(6, 1, 14000000, 14350000)
    await session.configure(mode=0, span=4)
    assert (0, "fixed_edge") not in session._desired
    assert session._fixed.get((6, 1)) is not None
    session.observe("fixed_edge", ScopeFixedEdge(6, 1, 14060000, 14160000))
    assert (6, 1) not in session._fixed
    await session.set_demand(ScopeDemand(0, 0))
    assert ScopeFixedEdge(6, 1, 14000000, 14350000) not in [
        value for name, value, _ in writer.views if name == "fixed_edge"
    ]


async def test_in_flight_write_blocks_external_release_until_it_lands():
    """Ambiguous-write guard: an observation arriving while the span write is
    still in flight (uncertain) keeps the snapshot; once the write lands, the
    new value is owned and close still restores the pre-write snapshot."""
    writer = Writer()
    writer.state = ScopeState(True, True, 0, 3, 1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def write(name, value, receiver):
        if name == "span":
            entered.set()
            await release.wait()
        await writer.write(name, value, receiver)

    session = ScopeSession(writer.read, write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    task = asyncio.create_task(session.configure(span=4))
    await asyncio.wait_for(entered.wait(), 5)
    assert (0, "span") in session._uncertain
    session.observe("span", 3)
    assert session._previous.get((0, "span")) == 3
    release.set()
    await task
    assert session._applied.get((0, "span")) == 4
    assert session._previous.get((0, "span")) == 3
    await session.set_demand(ScopeDemand(0, 0))
    assert ("span", 3, 0) in writer.views


async def test_reconnect_unchanged_mode_keeps_restore():
    """Gate round 2 P1 (fix-4565): an unchanged mode report after a
    connection loss keeps restore ownership — the cleared applied map
    must not read as an external change."""
    writer = Writer()
    writer.state = ScopeState(True, True, 2, 3, 1)
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    await session.configure(receiver=0, mode=0)
    assert session._previous.get((0, "mode")) == 2
    session.invalidate_now()
    assert session._applied.get((0, "mode")) is None
    session.observe("mode", 0)
    assert session._previous.get((0, "mode")) == 2
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(0, 0))
    assert ("mode", 2, 0) in writer.views


async def test_reconnect_unchanged_edge_and_fixed_edge_keep_restore():
    """Gate round 2 P1 (fix-4565): unchanged edge and fixed-edge reports
    after a connection loss keep restore ownership, same as span/mode."""
    writer = Writer()
    writer.state = ScopeState(True, True, 0, 3, 1)
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    await session.configure(receiver=0, edge=2)
    assert session._previous.get((0, "edge")) == 1
    session.invalidate_now()
    session.observe("edge", 2)
    assert session._previous.get((0, "edge")) == 1
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(0, 0))
    assert ("edge", 1, 0) in writer.views

    fixed_writer = Writer()
    fixed_writer.state = ScopeState(True, True, 0, 3, 1)
    fixed = ScopeFixedEdge(6, 1, 14_050_000, 14_150_000)
    captured = ScopeFixedEdge(6, 1, 14_000_000, 14_350_000)
    fixed_session = ScopeSession(
        fixed_writer.read, fixed_writer.write, fixed_writer.read_fixed
    )
    await fixed_session.set_inputs(ready=True, tx=False)
    await fixed_session.set_demand(ScopeDemand(1, 1))
    await fixed_session.configure(receiver=0, fixed_edge=fixed)
    assert fixed_session._fixed.get((6, 1)) == captured
    fixed_session.invalidate_now()
    fixed_session.observe("fixed_edge", fixed)
    assert fixed_session._fixed.get((6, 1)) == captured
    await fixed_session.set_inputs(ready=True, tx=False)
    await fixed_session.set_demand(ScopeDemand(0, 0))
    assert ("fixed_edge", captured, 0) in fixed_writer.views


async def test_reconnect_unchanged_dual_keeps_restore():
    """Gate round 2 P1 (fix-4565): an unchanged dual report after a
    connection loss keeps restore ownership under the radio-global key."""
    writer = Writer()
    writer.state = ScopeState(True, True, 0, 3, 1)
    session = ScopeSession(writer.read, writer.write, writer.read_fixed)
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(1, 1))
    await session.configure(receiver=0, dual=True)
    assert session._previous.get((0, "dual")) is False
    session.invalidate_now()
    session.observe("dual", True)
    assert session._previous.get((0, "dual")) is False
    await session.set_inputs(ready=True, tx=False)
    await session.set_demand(ScopeDemand(0, 0))
    assert ("dual", False, 0) in writer.views
