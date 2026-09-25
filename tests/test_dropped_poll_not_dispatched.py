"""A poll the commander drops at its cap is not reported as dispatched.

MOR-2602. The commander drops a fire-and-forget BACKGROUND send once 64 are
already in flight, and that drop used to look exactly like a send that was
enqueued: the executor appended the path to ``sent``, and the drain recorded
a dispatch. The field then sat stale for max_age plus the healthy-link grace.

These tests drive the real executor and the real drain against a real
commander sitting at the cap. The commander and the poller's sender are not
mocked.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from rigplane.commands.command_map import CommandMap
from rigplane.commands.commander import IcomCommander, Priority, _MAX_BG_INFLIGHT
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionScheduler,
    AcquisitionStatus,
    StateFreshnessService,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_diagnostics import StateDiagnosticsRecorder
from rigplane.core.radio_state import RadioState
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.types import CivFrame
from rigplane.profiles import RadioProfile
from rigplane.runtime._poller_types import CommandQueue
from rigplane.web.radio_poller import RadioPoller

_FREQ = FieldPath.active("main", "freq_mode", "freq_hz")


class _CapRadio:
    """A radio whose CI-V lane is a real commander held at the background cap."""

    def __init__(self, commander: IcomCommander) -> None:
        self._commander = commander
        self.seen: list[Priority] = []
        self.profile = RadioProfile(
            id="cap-test",
            model="Cap Test",
            civ_addr=0x98,
            receiver_count=1,
            capabilities=frozenset(),
            cmd29_routes=frozenset(),
            command_map=CommandMap({"get_selected_freq": (0x25, 0x00)}),
        )
        self.capabilities: set[str] = set()
        self.connected = True
        self.radio_ready = True

    async def send_civ(
        self,
        command: int,
        sub: int | None = None,
        data: bytes | None = None,
        *,
        wait_response: bool = True,
        priority: Priority = Priority.NORMAL,
        wait_dispatch: bool = True,
    ) -> CivFrame | None:
        self.seen.append(priority)
        payload = bytes([command])
        if sub is not None:
            payload += bytes([sub])
        if data:
            payload += data
        return await self._commander.send(
            payload,
            priority=priority,
            wait_response=wait_response,
            wait_dispatch=wait_dispatch,
        )


_CADENCE = 1.0  # LIVE class nominal cadence for an active freq_hz path


def _profile() -> RadioAcquisitionProfile:
    return RadioAcquisitionProfile(
        provider="icom_civ",
        capabilities=(FieldCapability(path=_FREQ, polling=True),),
        default_policy=AcquisitionPolicy(),
    )


def _scheduler(*, clock: FreshnessClock | None = None) -> AcquisitionScheduler:
    return AcquisitionScheduler(profile=_profile(), clock=clock)


async def _fill_background_cap(commander: IcomCommander) -> None:
    for index in range(_MAX_BG_INFLIGHT):
        dropped = await commander.send(
            f"fill-{index}".encode(),
            priority=Priority.BACKGROUND,
            wait_response=False,
            wait_dispatch=False,
        )
        assert dropped is None


@pytest.mark.asyncio
async def test_a_poll_dropped_at_the_commander_cap_is_not_dispatched(
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        if cmd == b"gate":
            started.set()
            await release.wait()
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    commander = IcomCommander(execute, min_interval=0.0)
    commander.start()
    try:
        gate = asyncio.create_task(commander.send(b"gate", priority=Priority.NORMAL))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await _fill_background_cap(commander)

        clock = FreshnessClock(start=100.0)
        scheduler = _scheduler(clock=clock)
        store = StateStore(freshness_clock=clock)
        service = StateFreshnessService(store=store, scheduler=scheduler)
        radio = _CapRadio(commander)
        radio._acquisition_scheduler = scheduler  # type: ignore[attr-defined]
        recorder = StateDiagnosticsRecorder(enabled=True)
        poller = RadioPoller(
            radio,  # type: ignore[arg-type]
            CommandQueue(),
            radio_state=RadioState(),
            state_store=store,
            diagnostics=recorder,
        )

        service.tick(now=clock.now())
        with caplog.at_level(logging.DEBUG):
            await poller._send_query()  # noqa: SLF001

        dropped = scheduler.pending_requests()
        assert dropped == ()
        assert poller._acquisition_in_flight == {}  # noqa: SLF001
        assert [
            event.kind
            for event in recorder.events()
            if event.kind == "acquisition_request_sent"
        ] == []
        assert caplog.records == []

        # One cadence later the dropped path goes out again, well before
        # max_age (5 s) plus the 6 s healthy-link grace. Free one cap slot
        # first: the worker is still parked on the gate item.
        release.set()
        await gate
        await commander.stop()
        commander.start()
        clock.advance(_CADENCE + 0.01)
        service.tick(now=clock.now())
        assert scheduler.diagnostics()["cadenceByGroup"] == {}
        await poller._send_query()  # noqa: SLF001
        in_flight = poller._acquisition_in_flight  # noqa: SLF001
        assert len(in_flight) == 1
        assert _FREQ in next(iter(in_flight.values()))[0]
    finally:
        release.set()
        await gate
        await commander.stop()


@pytest.mark.asyncio
async def test_a_command_priority_request_reaches_the_commander_as_normal() -> None:
    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        return CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")

    commander = IcomCommander(execute, min_interval=0.0)
    commander.start()
    try:
        scheduler = _scheduler()
        queued = scheduler.ensure_fresh(
            _FREQ,
            max_age=5.0,
            priority=AcquisitionPriority.COMMAND,
            reason="post_write:set_freq",
        )
        assert queued.status is AcquisitionStatus.QUEUED
        radio = _CapRadio(commander)
        radio._acquisition_scheduler = scheduler  # type: ignore[attr-defined]
        poller = RadioPoller(
            radio,  # type: ignore[arg-type]
            CommandQueue(),
            radio_state=RadioState(),
        )

        await poller._send_query()  # noqa: SLF001
    finally:
        await commander.stop()

    assert radio.seen == [Priority.NORMAL]
