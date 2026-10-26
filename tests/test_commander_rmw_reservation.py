"""Split-mode read-modify-write must not admit another commander frame.

CI-V ``0x26`` split-mode writes that omit DATA or filter
(``DualRxRuntimeMixin.write_relative_vfo_mode``, rigctld ``X``) read the
target VFO and then SET. Those are separate ``IcomCommander`` exchanges.
A second command queued in that gap used to run before the SET and the
SET then wrote the stale DATA/filter back.
"""

from __future__ import annotations

import asyncio

import pytest

from rigplane.commands import parse_civ_frame
from rigplane.commands.commander import IcomCommander, Priority
from rigplane.core.exceptions import CommandRejectedError
from rigplane.core.types import CivFrame
from rigplane.runtime.radio import IcomRadio

_MODELS = ("IC-7300", "IC-705", "IC-7610")


def _reply(addr: int, payload: str) -> CivFrame:
    return parse_civ_frame(bytes.fromhex(f"FE FE E0 {addr:02X} {payload} FD"))


class _SplitBackend:
    """CI-V peer whose preservation read waits until the test says so."""

    def __init__(self, addr: int) -> None:
        self.addr = addr
        self.frames: list[bytes] = []
        self.filter = 3
        self._mode_reads = 0
        self.preservation_entered = asyncio.Event()
        self.release_preservation = asyncio.Event()

    async def __call__(
        self,
        payload: bytes,
        wait_response: bool = True,
        is_current: object = None,
    ) -> CivFrame:
        del wait_response, is_current
        self.frames.append(payload)
        frame = parse_civ_frame(payload)
        if frame.command == 0x26 and len(frame.data) == 1:
            self._mode_reads += 1
            selector = frame.data[0]
            if self._mode_reads == 1:
                self.preservation_entered.set()
                await self.release_preservation.wait()
            return _reply(
                self.addr,
                f"26 {selector:02X} 01 01 {self.filter:02X}",
            )
        if frame.command == 0x26 and len(frame.data) > 1:
            self.filter = frame.data[-1]
            return _reply(self.addr, "FB")
        if frame.command == 0x25 and len(frame.data) == 1:
            selector = frame.data[0]
            return _reply(self.addr, f"25 {selector:02X} 00 40 07 14 00")
        return _reply(self.addr, "FB")


def _mode_sets(frames: list[bytes]) -> list[bytes]:
    sets: list[bytes] = []
    for frame in frames:
        parsed = parse_civ_frame(frame)
        if parsed.command == 0x26 and len(parsed.data) > 1:
            sets.append(parsed.data)
    return sets


@pytest.mark.parametrize("model", _MODELS)
async def test_split_mode_read_modify_write_excludes_foreign_command(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """A queued mode write cannot land between the preservation read and the SET."""
    radio = IcomRadio("127.0.0.1", model=model)
    backend = _SplitBackend(radio.profile.civ_addr)
    commander = IcomCommander(backend)
    radio._commander = commander
    radio._connected = True
    radio._civ_transport = object()  # type: ignore[assignment]
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    commander.start()
    foreign: asyncio.Task[CivFrame | None] | None = None
    entered = asyncio.Event()

    async def competing() -> None:
        entered.set()
        # Selector 01, USB, DATA on, filter 1 — the newer split-VFO write.
        await radio.send_civ(0x26, data=bytes([0x01, 0x01, 0x01, 0x01]))

    try:
        write = asyncio.create_task(radio.write_relative_vfo_mode(0x01, selected=False))
        await asyncio.wait_for(backend.preservation_entered.wait(), 1)
        foreign = asyncio.create_task(competing())
        await asyncio.wait_for(entered.wait(), 1)
        # ``send`` queues the frame before awaiting its result, and this read
        # still owns the worker, so the frame is queued once ``entered`` wakes.
        assert commander._queue is not None and commander._queue.qsize() == 1
        assert not any(
            parse_civ_frame(frame).command == 0x26
            and len(parse_civ_frame(frame).data) > 1
            for frame in backend.frames
        )
        backend.release_preservation.set()
        await asyncio.wait_for(write, 1)
        await asyncio.wait_for(foreign, 1)
    finally:
        backend.release_preservation.set()
        radio._connected = False
        if foreign is not None and not foreign.done():
            foreign.cancel()
        await commander.stop()

    sets = _mode_sets(backend.frames)
    assert [item[-1] for item in sets] == [3, 1]
    assert backend.filter == 1


@pytest.mark.parametrize("model", _MODELS)
async def test_selection_during_preserved_write_does_not_run_before_rejection(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """A VFO select queued during the read changes the epoch and does not SET.

    ``CoreRadio._set_vfo_wire`` shifts ``_vfo_selection_epoch`` before its
    send. The reservation keeps that frame queued, so the mode write sees
    the even epoch and rejects before the SET.
    """
    radio = IcomRadio("127.0.0.1", model=model)
    backend = _SplitBackend(radio.profile.civ_addr)
    commander = IcomCommander(backend)
    radio._commander = commander
    radio._connected = True
    radio._civ_transport = object()  # type: ignore[assignment]
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    commander.start()
    select: asyncio.Task[None] | None = None
    entered = asyncio.Event()

    async def change_selection() -> None:
        entered.set()
        await radio._set_vfo_wire("B")

    try:
        write = asyncio.create_task(radio.write_relative_vfo_mode(0x01, selected=False))
        await asyncio.wait_for(backend.preservation_entered.wait(), 1)
        select = asyncio.create_task(change_selection())
        await asyncio.wait_for(entered.wait(), 1)
        assert radio._vfo_selection_epoch % 2 == 0
        assert commander._queue is not None and commander._queue.qsize() == 1
        backend.release_preservation.set()
        with pytest.raises(CommandRejectedError, match="selection changed"):
            await asyncio.wait_for(write, 1)
        await asyncio.wait_for(select, 1)
    finally:
        backend.release_preservation.set()
        radio._connected = False
        if select is not None and not select.done():
            select.cancel()
        await commander.stop()

    assert _mode_sets(backend.frames) == []
    assert any(parse_civ_frame(frame).command == 0x07 for frame in backend.frames)


class _Hold:
    def __init__(self) -> None:
        self.order: list[bytes] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(
        self,
        payload: bytes,
        wait_response: bool = True,
        is_current: object = None,
    ) -> None:
        del wait_response, is_current
        self.order.append(payload)
        if payload == b"R":
            self.entered.set()
            await self.release.wait()


async def _await_queued(
    entered: asyncio.Event, commander: IcomCommander, n: int
) -> None:
    """``send`` queues before it waits, so the frame is queued once ``entered`` wakes."""
    await entered.wait()
    queue = commander._queue
    assert queue is not None and queue.qsize() == n


async def test_reservation_runs_abort_tiers_and_defers_other_priorities() -> None:
    hold = _Hold()
    commander = IcomCommander(hold)
    commander.start()
    tasks: list[asyncio.Task[object]] = []
    try:

        async def owner() -> None:
            async with commander.exclusive():
                await commander.send(b"R")
                await commander.send(b"W")

        owner_task = asyncio.create_task(owner())
        tasks.append(owner_task)
        await asyncio.wait_for(hold.entered.wait(), 1)

        async def competing(
            payload: bytes, priority: Priority, entered: asyncio.Event
        ) -> None:
            entered.set()
            await commander.send(payload, priority=priority)

        queued = 0
        for payload, priority in (
            (b"N", Priority.NORMAL),
            (b"I", Priority.IMMEDIATE),
            (b"A", Priority.ABORT),
            (b"F", Priority.FORCE_RELEASE),
        ):
            entered = asyncio.Event()
            task = asyncio.create_task(competing(payload, priority, entered))
            tasks.append(task)
            queued += 1
            await asyncio.wait_for(_await_queued(entered, commander, queued), 1)
        hold.release.set()
        await asyncio.wait_for(owner_task, 1)
        await asyncio.wait_for(asyncio.gather(*tasks[1:]), 1)
        assert hold.order == [b"R", b"F", b"A", b"W", b"I", b"N"]
    finally:
        hold.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await commander.stop()


async def test_cancelled_reservation_allows_the_next_command() -> None:
    hold = _Hold()
    commander = IcomCommander(hold)
    commander.start()
    owner: asyncio.Task[None] | None = None
    try:

        async def reserved() -> None:
            async with commander.exclusive():
                await commander.send(b"R")
                await asyncio.Event().wait()

        owner = asyncio.create_task(reserved())
        await asyncio.wait_for(hold.entered.wait(), 1)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        await asyncio.wait_for(commander.send(b"Z"), 1)
        assert hold.order == [b"R", b"Z"]
    finally:
        hold.release.set()
        if owner is not None and not owner.done():
            owner.cancel()
        await commander.stop()


async def test_owner_dedupe_never_joins_a_deferred_foreign_item() -> None:
    """The owner's keyed dedupe send must not join a deferred foreign item.

    A foreign task's keyed ``BACKGROUND`` send cannot run until the hold
    ends, and only the owner ends the hold: joining that future would wait
    forever (MOR-3743). The owner enqueues its own frame instead, and both
    frames run — the owner's during the hold, the foreign one after it.
    """
    hold = _Hold()
    commander = IcomCommander(hold)
    commander.start()
    tasks: list[asyncio.Task[object]] = []
    foreign: asyncio.Task[None] | None = None
    try:

        async def owner() -> None:
            async with commander.exclusive():
                await commander.send(b"R")
                await commander.send(b"Q", key="k", dedupe=True)
                # Still inside the hold: the owner's frame ran, the foreign
                # frame has not.
                assert hold.order == [b"R", b"Q"]
                assert foreign is not None and not foreign.done()

        owner_task = asyncio.create_task(owner())
        tasks.append(owner_task)
        await asyncio.wait_for(hold.entered.wait(), 1)

        foreign_entered = asyncio.Event()

        async def foreign_send() -> None:
            foreign_entered.set()
            await commander.send(
                b"Q", key="k", dedupe=True, priority=Priority.BACKGROUND
            )

        foreign = asyncio.create_task(foreign_send())
        tasks.append(foreign)
        await asyncio.wait_for(_await_queued(foreign_entered, commander, 1), 1)

        hold.release.set()
        await asyncio.wait_for(owner_task, 1)
        await asyncio.wait_for(foreign, 1)
        assert hold.order == [b"R", b"Q", b"Q"]
        # No stale key entry: a fresh keyed send enqueues and runs a new item.
        await asyncio.wait_for(commander.send(b"Q", key="k", dedupe=True), 1)
        assert hold.order == [b"R", b"Q", b"Q", b"Q"]
        queue = commander._queue
        assert queue is not None
        await asyncio.wait_for(queue.join(), 1)
        assert commander._pending_by_key == {}
    finally:
        hold.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await commander.stop()


async def test_foreign_dedupe_joins_the_owners_pending_item_during_the_hold() -> None:
    """A foreign keyed send may join the owner's pending item.

    The owner's item runs during the hold, so the foreign joiner resolves
    before the hold ends and only one frame reaches the wire.
    """
    hold = _Hold()
    commander = IcomCommander(hold)
    commander.start()
    tasks: list[asyncio.Task[object]] = []
    joiner: asyncio.Task[None] | None = None
    try:

        async def owner() -> None:
            async with commander.exclusive():
                await commander.send(b"R", wait_dispatch=False)
                await commander.send(b"Q", key="k", dedupe=True)
                assert hold.order == [b"R", b"Q"]
                assert joiner is not None
                await asyncio.wait_for(joiner, 1)

        owner_task = asyncio.create_task(owner())
        tasks.append(owner_task)
        await asyncio.wait_for(hold.entered.wait(), 1)

        async def join_send() -> None:
            await commander.send(b"Q", key="k", dedupe=True)

        joiner = asyncio.create_task(join_send())
        tasks.append(joiner)
        await asyncio.sleep(0)
        hold.release.set()
        await asyncio.wait_for(owner_task, 1)
        await asyncio.wait_for(joiner, 1)
        assert hold.order == [b"R", b"Q"]
    finally:
        hold.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await commander.stop()


async def test_unreserved_dedupe_still_joins_across_tasks() -> None:
    """Without a reservation, keyed dedupe still joins one wire frame."""
    hold = _Hold()
    commander = IcomCommander(hold)
    commander.start()
    tasks: list[asyncio.Task[object]] = []
    try:
        gate = asyncio.create_task(commander.send(b"R"))
        tasks.append(gate)
        await asyncio.wait_for(hold.entered.wait(), 1)

        first_entered = asyncio.Event()

        async def first() -> None:
            first_entered.set()
            await commander.send(b"Q", key="k", dedupe=True)

        first_task = asyncio.create_task(first())
        tasks.append(first_task)
        await asyncio.wait_for(_await_queued(first_entered, commander, 1), 1)

        second_entered = asyncio.Event()

        async def second() -> None:
            second_entered.set()
            await commander.send(b"Q", key="k", dedupe=True)

        second_task = asyncio.create_task(second())
        tasks.append(second_task)
        await asyncio.wait_for(second_entered.wait(), 1)

        hold.release.set()
        await asyncio.wait_for(gate, 1)
        await asyncio.wait_for(first_task, 1)
        await asyncio.wait_for(second_task, 1)
        assert hold.order == [b"R", b"Q"]
    finally:
        hold.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await commander.stop()


async def test_owner_dedupe_joins_its_own_pending_item_during_the_hold() -> None:
    """The owner's keyed dedupe still joins its own pending item."""
    hold = _Hold()
    commander = IcomCommander(hold)
    commander.start()
    owner: asyncio.Task[None] | None = None
    second_entered = asyncio.Event()
    try:

        async def reserved() -> None:
            async with commander.exclusive():
                await commander.send(b"R", wait_dispatch=False)
                await commander.send(b"Q", key="k", dedupe=True, wait_dispatch=False)
                second_entered.set()
                await commander.send(b"Q", key="k", dedupe=True)
                assert hold.order == [b"R", b"Q"]

        owner = asyncio.create_task(reserved())
        await asyncio.wait_for(hold.entered.wait(), 1)
        await asyncio.wait_for(second_entered.wait(), 1)

        hold.release.set()
        await asyncio.wait_for(owner, 1)
        assert hold.order == [b"R", b"Q"]
    finally:
        hold.release.set()
        if owner is not None and not owner.done():
            owner.cancel()
        await commander.stop()
