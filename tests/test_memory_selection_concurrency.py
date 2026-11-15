"""Typed memory exclusion through the production commander and wire boundary.

Only the transport is fake. Serial cases exercise the serial CoreRadio subclass
at its normalized CI-V transport boundary, not a USB driver or physical radio.
Raw CI-V and physical changes of the selected slot are outside this contract.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable
from dataclasses import dataclass, field

import pytest

from _helpers import CIV_HEADER_SIZE, wrap_civ_in_udp
from rigplane.backends.ic705.serial import Ic705SerialRadio
from rigplane.commands import CONTROLLER_ADDR
from rigplane.commands._frame import build_civ_frame
from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.radio import IcomRadio
from rigplane.runtime.radio import CoreRadio
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from test_radio import MockTransport
from test_web_split_writes import _request

ACK = 0xFB
NAK = 0xFA
OPERATIONS = ("write", "clear", "to_vfo", "select")


class MemoryTransport(MockTransport):
    """Events control dispatch; absent replies use the real response timeout."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: defaultdict[int, asyncio.Event] = defaultdict(asyncio.Event)
        self.finished: defaultdict[int, asyncio.Event] = defaultdict(asyncio.Event)
        self.holds: dict[int, asyncio.Event] = {}
        self.replies: dict[int, int | None] = {}
        self.errors: dict[int, Exception] = {}

    async def send_tracked(self, data: bytes) -> None:
        await super().send_tracked(data)
        number = len(self.sent_packets)
        reply = self.replies.get(number, ACK)
        if reply is not None:
            self.queue_response(
                wrap_civ_in_udp(build_civ_frame(CONTROLLER_ADDR, 0xA4, reply))
            )
        self.seen[number].set()
        try:
            if number in self.holds:
                await self.holds[number].wait()
            if number in self.errors:
                raise self.errors[number]
        finally:
            self.finished[number].set()

    @property
    def frames(self) -> list[bytes]:
        return [packet[CIV_HEADER_SIZE:] for packet in self.sent_packets]


@dataclass
class MemoryRig:
    radio: CoreRadio
    transport: MemoryTransport
    tasks: list[asyncio.Task] = field(default_factory=list)

    def spawn(self, operation: Awaitable) -> asyncio.Task:
        task = asyncio.create_task(operation)
        self.tasks.append(task)
        return task

    async def attempt(self, operation: Awaitable) -> asyncio.Task:
        entered = asyncio.Event()

        async def run():
            entered.set()
            return await operation

        task = self.spawn(run())
        # run() proceeds into operation until its first suspension before this
        # waiter resumes. No timer decides whether the operation was attempted.
        await asyncio.wait_for(entered.wait(), 2)
        return task


def operation(radio: CoreRadio, name: str) -> Awaitable[None]:
    if name == "write":
        return radio.memory_write()
    if name == "clear":
        return radio.memory_clear(4)
    if name == "to_vfo":
        return radio.memory_to_vfo(4)
    return radio.set_memory_mode(4)


@pytest.fixture(params=["lan", "serial"])
async def memory_rig(request):
    radio = (
        IcomRadio("192.0.2.1", model="IC-705", timeout=1)
        if request.param == "lan"
        else Ic705SerialRadio(device="/dev/fake-memory-test", timeout=1)
    )
    transport = MemoryTransport()
    radio._civ_transport = radio._ctrl_transport = transport
    radio._connected = True
    radio._civ_stream_ready = True
    radio._last_civ_data_received = time.monotonic()
    radio._civ_min_interval = 0
    radio._civ_get_timeout = 1
    radio._civ_runtime.start_worker()
    rig = MemoryRig(radio, transport)
    try:
        yield rig
    finally:
        for task in rig.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*rig.tasks, return_exceptions=True)
        await radio._civ_runtime.stop_worker()
        await radio._civ_runtime.stop_pump()
        radio._connected = False


@pytest.mark.asyncio
@pytest.mark.parametrize("name", OPERATIONS)
async def test_typed_operation_cannot_enter_between_group_and_channel(memory_rig, name):
    rig = memory_rig
    transport = rig.transport
    release = transport.holds[1] = asyncio.Event()
    recall = rig.spawn(rig.radio.set_memory_mode(7, group=3))
    # ACK is available, but the group send is still executing in the real worker.
    await asyncio.wait_for(transport.seen[1].wait(), 2)
    competing = await rig.attempt(operation(rig.radio, name))
    assert not competing.done()
    assert rig.radio._commander._queue.qsize() == 0
    release.set()
    await asyncio.wait_for(asyncio.gather(recall, competing), 3)
    assert [frame[4:-1].hex() for frame in transport.frames[:2]] == [
        "08a00003",
        "080007",
    ]
    assert len(transport.frames) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("name", OPERATIONS)
async def test_typed_send_holds_lock_until_actual_dispatch_finishes(memory_rig, name):
    rig = memory_rig
    release = rig.transport.holds[1] = asyncio.Event()
    first = rig.spawn(operation(rig.radio, name))
    await asyncio.wait_for(rig.transport.seen[1].wait(), 2)
    recall = await rig.attempt(rig.radio.set_memory_mode(7, group=3))
    assert not first.done()  # wait_response=False still awaits transport dispatch
    assert not recall.done()
    assert rig.radio._commander._queue.qsize() == 0
    release.set()
    await asyncio.wait_for(asyncio.gather(first, recall), 3)
    assert [frame[4:-1].hex() for frame in rig.transport.frames[1:]] == [
        "08a00003",
        "080007",
    ]


async def assert_typed_operations_refused(rig: MemoryRig) -> None:
    count = len(rig.transport.frames)
    for name in OPERATIONS:
        with pytest.raises(CommandError, match="selection is uncertain"):
            await asyncio.wait_for(operation(rig.radio, name), 2)
    assert len(rig.transport.frames) == count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["group_nak", "channel_nak", "timeout", "disconnect"]
)
async def test_partial_or_unconfirmed_recall_blocks_writes_until_full_recovery(
    memory_rig, failure
):
    rig = memory_rig
    if failure == "group_nak":
        rig.transport.replies[1] = NAK
    elif failure == "channel_nak":
        rig.transport.replies[2] = NAK
    elif failure == "timeout":
        rig.transport.replies[2] = None
        rig.radio._civ_get_timeout = 0.02  # exercises the real bounded ACK wait
    else:
        rig.transport.replies[2] = None
        rig.transport.errors[2] = ConnectionError("fake transport disconnected")
    with pytest.raises((CommandError, TimeoutError, ConnectionError)):
        await rig.radio.set_memory_mode(7, group=3)
    assert len(rig.transport.frames) == (1 if failure == "group_nak" else 2)
    await assert_typed_operations_refused(rig)
    # Invalid recovery attempts must not reset an already-uncertain selection.
    with pytest.raises(ValueError):
        await rig.radio.set_memory_mode(7, group=-1)
    await assert_typed_operations_refused(rig)
    rig.radio._civ_get_timeout = 1
    before = len(rig.transport.frames)
    await rig.radio.set_memory_mode(9, group=2)
    await rig.radio.memory_write()
    assert [frame[4:-1].hex() for frame in rig.transport.frames[before:]] == [
        "08a00002",
        "080009",
        "09",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", ["before_dispatch", "group_sent", "channel_sent"])
async def test_cancelled_recall_keeps_uncertain_selection(memory_rig, cancel_at):
    rig = memory_rig
    if cancel_at == "before_dispatch":
        # Occupy the actual commander with an earlier typed operation. Cancelling
        # while waiting for the memory lock sent nothing and must not poison it.
        release = rig.transport.holds[1] = asyncio.Event()
        previous = rig.spawn(rig.radio.memory_write())
        await asyncio.wait_for(rig.transport.seen[1].wait(), 2)
        recall = await rig.attempt(rig.radio.set_memory_mode(7, group=3))
        recall.cancel()
        with pytest.raises(asyncio.CancelledError):
            await recall
        release.set()
        await asyncio.wait_for(previous, 2)
        await rig.radio.memory_write()
        assert [frame[4] for frame in rig.transport.frames] == [0x09, 0x09]
        return
    number = 1 if cancel_at == "group_sent" else 2
    rig.transport.holds[number] = asyncio.Event()
    recall = rig.spawn(rig.radio.set_memory_mode(7, group=3))
    await asyncio.wait_for(rig.transport.seen[number].wait(), 2)
    recall.cancel()
    with pytest.raises(asyncio.CancelledError):
        await recall
    await asyncio.wait_for(rig.transport.finished[number].wait(), 2)
    assert len(rig.transport.frames) == number
    await assert_typed_operations_refused(rig)
    await rig.radio.set_memory_mode(9, group=2)
    await rig.radio.memory_write()
    assert rig.transport.frames[-1][4] == 0x09


@pytest.mark.asyncio
async def test_invalid_initial_recall_does_not_poison_selection(memory_rig):
    rig = memory_rig
    for group, channel in [(-1, 7), (3, 100)]:
        with pytest.raises(ValueError):
            await rig.radio.set_memory_mode(channel, group=group)
    assert rig.transport.frames == []
    await rig.radio.memory_write()
    assert [frame[4] for frame in rig.transport.frames] == [0x09]


@pytest.mark.asyncio
async def test_web_poller_recall_failure_keeps_typed_write_blocked(memory_rig):
    rig = memory_rig
    web = WebServer(rig.radio, WebConfig(host="127.0.0.1", port=0))
    poller = RadioPoller(
        rig.radio, web.command_queue, state_store=web.command_state_store
    )
    web.command_state_store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=asyncio.get_running_loop().time(),
            max_age=60,
        )
    )
    rig.transport.replies[2] = NAK
    web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)

    async def consume():
        await web.command_queue.wait(2)
        entry = web.command_queue.take_entry()
        assert entry is not None
        await poller._execute_queued_entry(entry)

    consumer = rig.spawn(consume())
    try:
        _status, result = await _request(
            web, "http", "set_memory_mode", {"group": 3, "channel": 7}
        )
        await asyncio.wait_for(consumer, 2)
        assert result.get("ok") is not True
        assert len(rig.transport.frames) == 2
        await assert_typed_operations_refused(rig)
    finally:
        web._server.close()
        await web._server.wait_closed()
        web._server = None
        tasks = tuple(web._client_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_queued_recall_is_skipped_and_remains_uncertain(memory_rig):
    rig = memory_rig
    release = rig.transport.holds[1] = asyncio.Event()
    # Occupy the production worker without taking the typed-memory lock. This
    # is only a fake-wire scheduling fixture, not a raw-CI-V safety guarantee.
    previous = rig.spawn(
        rig.radio._commander.send(
            build_civ_frame(0xA4, CONTROLLER_ADDR, 0x09), wait_response=False
        )
    )
    await asyncio.wait_for(rig.transport.seen[1].wait(), 2)
    recall = await rig.attempt(rig.radio.set_memory_mode(7, group=3))
    assert rig.radio._commander._queue.qsize() == 1
    recall.cancel()
    with pytest.raises(asyncio.CancelledError):
        await recall
    release.set()
    await asyncio.wait_for(previous, 2)
    await assert_typed_operations_refused(rig)
    assert len(rig.transport.frames) == 1
    await rig.radio.set_memory_mode(9, group=2)
    await rig.radio.memory_write()
    assert [frame[4:-1].hex() for frame in rig.transport.frames] == [
        "09",
        "08a00002",
        "080009",
        "09",
    ]
