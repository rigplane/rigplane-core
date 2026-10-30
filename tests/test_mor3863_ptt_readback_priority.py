"""MOR-3863: the PTT read-back must not wait behind queued background reads.

The beta.10 TX smoke raw trace showed PTT reads every 0.336 s but gaps of
1.088 s with ~36 other reads in between. Every acquisition read is a
fire-and-forget send into the commander's single BACKGROUND lane, which is
FIFO, so a KEYING-class read queued after a burst of other reads waited for
the whole burst to clear the (paced) wire.

The mechanism under test is shared: the acquisition class table already ranks
KEYING first; the commander now gives that class its own read lane ahead of
BACKGROUND. These tests drive the real scheduler, drain, executor, the web
poller's sender and a real commander over a fake transport that answers each
frame after a fixed delay, for several radio profiles.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from rigplane.commands.commander import IcomCommander, Priority
from rigplane.core.acquisition_drain import AcquisitionDrain
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionQuery,
    AcquisitionScheduler,
    IcomCivAcquisitionExecutor,
)
from rigplane.core.exceptions import BackgroundSendDropped
from rigplane.core.state_pipeline_contracts import (
    AcquisitionClass,
    FieldPath,
    acquisition_class_for_path,
)
from rigplane.core.state_store import StateStore
from rigplane.profiles.rig_loader import load_rig
from rigplane.runtime._state_queries import acquisition_query_resolver_for_profile
from rigplane.web.radio_poller import RadioPoller

_RIGS_DIR = Path(__file__).parents[1] / "rigs"
_PTT = FieldPath.global_("tx_state", "ptt")
_PTT_FRAME = b"\x1c\x00"
#: The frame delay of the acceptance scenario (50 ms per response).
_FRAME_DELAY = 0.05
#: KEYING cadence ceiling (0.5 s): the answer bound the issue asks for.
_PTT_BOUND = 0.5


class _TimedWire:
    """Fake transport: every frame occupies the wire for ``delay`` seconds."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.frames: list[bytes] = []
        self.sent_at: dict[bytes, float] = {}

    async def execute(self, payload: bytes, wait_response: bool) -> None:
        self.frames.append(payload)
        self.sent_at.setdefault(payload, time.monotonic())
        await asyncio.sleep(self.delay)


def _lane_depth(commander: IcomCommander) -> int:
    queue = commander._queue  # noqa: SLF001
    assert queue is not None
    return queue.qsize()


def _poller_sending_through(commander: IcomCommander) -> RadioPoller:
    """A real ``RadioPoller`` whose ``_civ`` hands frames to *commander*."""

    async def civ(
        command: int,
        *,
        sub: int | None = None,
        data: bytes = b"",
        priority: Priority = Priority.NORMAL,
        wait_dispatch: bool = True,
        **_: Any,
    ) -> None:
        payload = bytes([command]) + (b"" if sub is None else bytes([sub])) + data
        await commander.send(
            payload,
            priority=priority,
            wait_dispatch=wait_dispatch,
            wait_response=False,
        )

    poller = object.__new__(RadioPoller)
    poller._radio_state = None  # noqa: SLF001
    poller._civ = civ  # type: ignore[method-assign]  # noqa: SLF001
    return poller


def _drain(
    scheduler: AcquisitionScheduler,
    executor: IcomCivAcquisitionExecutor,
    store: StateStore,
) -> AcquisitionDrain:
    return AcquisitionDrain(
        scheduler=lambda: scheduler,
        executor=lambda: executor,
        store=lambda: store,
        in_flight={},
        expired=lambda request, *, sent_at, now: False,
        dispatchable=lambda pending: pending,
        report_failure=lambda *args, **kwargs: None,
        report_executor_missing=lambda *args, **kwargs: None,
        report_executor_error=lambda *args, **kwargs: None,
        report_sent=lambda request, *, paths, pending_request_count: None,
    )


@pytest.mark.parametrize("rig_file", ["ic7610.toml", "ic7300.toml", "ic705.toml"])
async def test_ptt_readback_is_not_starved_behind_a_background_burst(
    rig_file: str,
) -> None:
    """40-ish queued background reads + 1 PTT read: PTT is on the wire in time."""
    profile = load_rig(_RIGS_DIR / rig_file).to_profile()
    acquisition = profile.state_acquisition
    assert acquisition is not None
    scheduler = AcquisitionScheduler(profile=acquisition)
    store = StateStore()

    wire = _TimedWire(_FRAME_DELAY)
    commander = IcomCommander(wire.execute)
    commander.start()
    poller = _poller_sending_through(commander)
    executor = IcomCivAcquisitionExecutor(
        poller._send_one_state_query,  # noqa: SLF001
        resolve_query=acquisition_query_resolver_for_profile(profile),
    )
    drain = _drain(scheduler, executor, store)
    try:
        # Pass 1: every other pollable field is due and goes out as a burst.
        background = [p for p in acquisition.pollable_paths() if p != _PTT]
        for path in background:
            scheduler.ensure_fresh(
                path, max_age=5.0, priority="background", reason="policy-cadence"
            )
        await drain.run_once()
        queued_before_ptt = len(wire.frames) + _lane_depth(commander)
        assert queued_before_ptt >= 20, "scenario needs a real burst in the lane"

        # The PTT cadence comes due only now, behind the burst.
        scheduler.ensure_fresh(
            _PTT, max_age=5.0, priority="background", reason="policy-cadence"
        )
        asked_at = time.monotonic()
        await drain.run_once()

        async with asyncio.timeout(5.0):
            while _PTT_FRAME not in wire.sent_at:
                await asyncio.sleep(0.005)
        latency = wire.sent_at[_PTT_FRAME] - asked_at
        assert latency <= _PTT_BOUND, (
            f"PTT read waited {latency:.3f}s behind the background burst "
            f"({queued_before_ptt} reads queued, {_FRAME_DELAY}s each)"
        )
    finally:
        await commander.stop()


async def test_background_reads_still_progress_while_keying_reads_repeat() -> None:
    """The KEYING lane must not starve BACKGROUND the other way round.

    PTT reads are interleaved with the burst (one every five background
    reads); each is one frame, so the background lane keeps its share of the
    wire, drains completely and stays in order.
    """
    wire = _TimedWire(0.01)
    commander = IcomCommander(wire.execute)
    commander.start()
    poller = _poller_sending_through(commander)
    try:
        background = [bytes([0x14, sub]) for sub in range(1, 31)]
        for index, payload in enumerate(background):
            await commander.send(
                payload,
                priority=Priority.BACKGROUND,
                wait_dispatch=False,
                wait_response=False,
            )
            if index % 5 == 0:
                # PTT cadence arrives interleaved with the burst.
                await poller._send_one_state_query(  # noqa: SLF001
                    AcquisitionQuery(command=0x1C, sub=0x00),
                    AcquisitionPriority.BACKGROUND,
                    AcquisitionClass.KEYING,
                )
        async with asyncio.timeout(5.0):
            while not all(payload in wire.sent_at for payload in background):
                await asyncio.sleep(0.01)
        # Background order is preserved among themselves.
        sent_background = [f for f in wire.frames if f in set(background)]
        assert sent_background == background
        assert wire.frames.count(_PTT_FRAME) == 6
    finally:
        await commander.stop()


def test_the_ptt_path_is_the_keying_class() -> None:
    """The lane keys off the shared class table, not a per-radio rule."""
    assert acquisition_class_for_path(_PTT) is AcquisitionClass.KEYING


async def test_keying_read_lane_is_a_known_read_for_interference_scopes() -> None:
    """A KEYING read is a poll, not a state change: it must not mark scopes."""
    wire = _TimedWire(0.0)
    commander = IcomCommander(wire.execute)
    commander.start()
    try:
        async with commander.interference_scope() as scope:
            commander.note_foreign_dispatch(None, Priority.KEYING)
            assert not scope.interfered
            commander.note_foreign_dispatch(None, Priority.NORMAL)
            assert scope.interfered
    finally:
        await commander.stop()


async def test_keying_reads_are_not_dropped_at_the_background_cap() -> None:
    """The BACKGROUND in-flight cap never sheds the PTT read."""
    from rigplane.commands import commander as commander_module

    gate = asyncio.Event()

    async def blocked(payload: bytes, wait_response: bool) -> None:
        await gate.wait()

    commander = IcomCommander(blocked)
    commander.start()
    try:
        for index in range(commander_module._MAX_BG_INFLIGHT):  # noqa: SLF001
            await commander.send(
                bytes([0x14, index]),
                priority=Priority.BACKGROUND,
                wait_dispatch=False,
            )
        with pytest.raises(BackgroundSendDropped):
            await commander.send(
                b"\x14\xff", priority=Priority.BACKGROUND, wait_dispatch=False
            )
        await commander.send(_PTT_FRAME, priority=Priority.KEYING, wait_dispatch=False)
    finally:
        gate.set()
        await commander.stop()


def test_lane_order_is_between_user_commands_and_background_polls() -> None:
    assert Priority.NORMAL < Priority.KEYING < Priority.BACKGROUND


@pytest.mark.parametrize(
    ("priority", "klass", "lane"),
    [
        (AcquisitionPriority.BACKGROUND, AcquisitionClass.KEYING, Priority.KEYING),
        (AcquisitionPriority.RECONCILIATION, AcquisitionClass.KEYING, Priority.KEYING),
        (AcquisitionPriority.BACKGROUND, AcquisitionClass.SETTING, Priority.BACKGROUND),
        (AcquisitionPriority.BACKGROUND, None, Priority.BACKGROUND),
        # A write confirmation keeps its user-command lane, whatever the class.
        (AcquisitionPriority.COMMAND, AcquisitionClass.KEYING, Priority.NORMAL),
        (AcquisitionPriority.USER, AcquisitionClass.SETTING, Priority.NORMAL),
    ],
)
async def test_poller_sender_maps_priority_and_class_to_a_lane(
    priority: AcquisitionPriority,
    klass: AcquisitionClass | None,
    lane: Priority,
) -> None:
    lanes: list[Priority] = []

    async def civ(command: int, **kwargs: Any) -> None:
        lanes.append(kwargs["priority"])

    poller = object.__new__(RadioPoller)
    poller._radio_state = None  # noqa: SLF001
    poller._civ = civ  # type: ignore[method-assign]  # noqa: SLF001
    await poller._send_one_state_query(  # noqa: SLF001
        AcquisitionQuery(command=0x1C, sub=0x00), priority, klass
    )
    assert lanes == [lane]
