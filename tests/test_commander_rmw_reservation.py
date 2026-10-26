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
