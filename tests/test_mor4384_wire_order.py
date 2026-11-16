"""MOR-4384: a CI-V answer goes to the oldest request on the wire that can take it.

Every tracked request -- a blocking waiter or a fire-and-forget sink, a read
or a write -- is stamped when its frame leaves this side, and an inbound
answer goes to the oldest stamped open entry that can accept it: a data
frame to the oldest entry whose key matches, an ``FB`` to the oldest write,
an ``FA`` to the oldest entry of any kind. An entry leaves only when its own
answer window (from its stamp) closes, or, on an ordered transport, when a
newer entry claims a directed answer: then every older stamped entry is
lost. Nothing drains, nothing is presumed lost on a budget.

T1 is the model property (no loss: every answer lands on its own request),
T2 and T3 pin the one-loss cases with and without a keyed answer, T5 the
lost read followed by an acked and a refused write, T6 a tight commander
deadline behind an open read sink, T7 the two-argument executor contract.
T4 (``tests/integration/test_mor4384_ack_window.py``) and T8
(``tests/integration/test_ptt_release_readback_api.py``) run through HTTP.

The link and the radio are faked (``_WireRadio``), and everything that reads
time -- the tracker's stamps and windows, the runtime's sends, the answer
deadlines -- runs on one virtual clock, so the windows are the production
2 s and the runs take no wall-clock time.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commander import IcomCommander
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core import civ as civ_module
from rigplane.core.civ import (
    CivEvent,
    CivEventType,
    CivRequestKey,
    CivRequestTracker,
    read_identity_from_frame,
)
from rigplane.core.exceptions import CommandError, CommandRejectedError
from rigplane.core.exceptions import TimeoutError as CivTimeoutError

from rigplane.radio import IcomRadio
from rigplane.runtime import _civ_rx
from rigplane.testing.timing import VirtualClock
from rigplane.types import CivFrame

from _helpers import CIV_HEADER_SIZE, observe_selected_receiver

#: The production answer window (``_civ_get_timeout``).
_WINDOW = 2.0
_ADDR = IC_7610_ADDR


def _bcd(value: int, width: int) -> bytes:
    """Little-endian packed BCD, ``width`` bytes (CI-V frequency layout)."""
    out = bytearray()
    for _ in range(width):
        out.append((value % 10) | ((value // 10 % 10) << 4))
        value //= 100
    return bytes(out)


def _level(value: int) -> bytes:
    """Big-endian packed BCD ``0000``-``0255`` (CI-V level layout)."""
    return bytes([value // 100, (value // 10 % 10) << 4 | value % 10])


def _request(command: int, sub: int | None = None, data: bytes = b"") -> bytes:
    return build_civ_frame(_ADDR, CONTROLLER_ADDR, command, sub=sub, data=data)


def _answer(command: int, sub: int | None = None, data: bytes = b"") -> CivFrame:
    return parse_civ_frame(
        build_civ_frame(CONTROLLER_ADDR, _ADDR, command, sub=sub, data=data)
    )


def _fb() -> CivFrame:
    return _answer(0xFB)


def _fa() -> CivFrame:
    return _answer(0xFA)


#: Three read keys; same-key repeats are what a late answer can confuse.
_READS: dict[str, tuple[int, int | None, Callable[[int], bytes]]] = {
    "freq": (0x03, None, lambda n: _bcd(7_000_000 + 10 * n, 5)),
    "af": (0x14, 0x01, _level),
    "smeter": (0x15, 0x02, _level),
}
#: The write: RF gain (no read above shares its key).
_WRITE = (0x14, 0x02)


class _WireRadio:
    """A link to a radio that answers every frame in the order it was sent.

    ``plan`` holds, per send, the answer (``None`` = lost on the wire) and
    its delay; an answer is never delivered before an earlier one, the way a
    radio answering in command order puts them on the wire. ``ordered`` is
    the transport's ``ordered_ingress`` contract (serial vs LAN): delivery
    is in order either way, only the evidence rule reads the flag.
    """

    def __init__(self, clock: VirtualClock, *, ordered: bool) -> None:
        self.ordered_ingress = ordered
        self.connected = True
        self.my_id = 0x00010001
        self.remote_id = 0xDEADBEEF
        self.send_seq = 0
        self.rx_packet_count = 0
        self.sent: list[tuple[float, CivFrame]] = []
        self.plan: deque[tuple[CivFrame | None, float]] = deque()
        self.outbox: asyncio.Queue[tuple[float, CivFrame]] = asyncio.Queue()
        self._clock = clock
        self._last_due = float("-inf")

    async def send_tracked(
        self, data: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        if is_current is not None and not is_current():
            raise CommandError("Tracked write is no longer current")
        now = self._clock()
        self.sent.append((now, parse_civ_frame(bytes(data[CIV_HEADER_SIZE:]))))
        self.send_seq += 1
        answer, delay = self.plan.popleft() if self.plan else (None, 0.0)
        if answer is not None:
            due = max(now + delay, self._last_due)
            self._last_due = due
            self.outbox.put_nowait((due, answer))

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        await asyncio.sleep(timeout)
        raise asyncio.TimeoutError

    def start_ping_loop(self) -> None:
        pass

    def start_retransmit_loop(self) -> None:
        pass


class _ClockTime:
    """A ``time`` module view whose ``monotonic`` reads the virtual clock."""

    def __init__(self, clock: VirtualClock) -> None:
        self.monotonic = clock

    def __getattr__(self, name: str) -> object:
        return getattr(time, name)


@dataclass
class _Link:
    radio: IcomRadio
    wire: _WireRadio
    clock: VirtualClock

    @property
    def tracker(self) -> CivRequestTracker:
        return self.radio._civ_request_tracker

    async def run(self, coro: object, *, limit: float = 60.0) -> object:
        """Drive ``coro`` to completion on the virtual clock."""
        task = asyncio.ensure_future(coro)  # type: ignore[arg-type]
        spent = 0.0
        while not task.done():
            assert spent < limit, "the virtual run never finished"
            await self.clock.elapse(0.01)
            spent += 0.01
        return task.result()

    async def settle(self) -> None:
        """Let every planned answer reach the runtime."""
        await self.clock.elapse(_WINDOW + 0.5)


@pytest.fixture
async def link_factory(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Callable[..., _Link]]:
    # The loop's timers already run on the clock (the fixture); the
    # tracker's and the runtime's own ``time.monotonic()`` read it too.
    clock_time = _ClockTime(virtual_clock)
    monkeypatch.setattr(civ_module, "time", clock_time)
    monkeypatch.setattr(_civ_rx, "time", clock_time)
    links: list[tuple[_Link, asyncio.Task[None]]] = []

    def make(*, ordered: bool, min_interval: float = 0.0) -> _Link:
        wire = _WireRadio(virtual_clock, ordered=ordered)
        radio = IcomRadio("192.168.1.100", timeout=_WINDOW, model="IC-7610")
        radio._civ_transport = wire
        radio._ctrl_transport = wire
        radio._connected = True
        observe_selected_receiver(radio, 0)
        radio._civ_min_interval = min_interval
        radio._civ_get_timeout = _WINDOW
        radio._civ_last_waiter_gc_monotonic = virtual_clock()
        radio._last_civ_send_monotonic = virtual_clock() - 1.0
        link = _Link(radio, wire, virtual_clock)

        async def deliver() -> None:
            while True:
                due, frame = await wire.outbox.get()
                if due > virtual_clock():
                    await asyncio.sleep(due - virtual_clock())
                await radio._civ_runtime._route_civ_frame(
                    frame, generation=radio._civ_epoch
                )

        links.append((link, asyncio.ensure_future(deliver())))
        return link

    with patch.object(_civ_rx.CivRuntime, "start_pump"):
        yield make
        for link, delivery in links:
            delivery.cancel()
            await asyncio.gather(delivery, return_exceptions=True)
            if link.radio._commander is not None:
                await link.radio._civ_runtime.stop_worker()
            link.radio._civ_transport = None
            link.radio._connected = False


@dataclass
class _Op:
    request: bytes
    answer: CivFrame | None
    delay: float
    blocking: bool
    read: bool
    refused: bool

    @property
    def identity(self) -> CivRequestKey:
        return read_identity_from_frame(parse_civ_frame(self.request))


def _read(name: str, n: int, *, blocking: bool, refused: bool, delay: float) -> _Op:
    command, sub, payload = _READS[name]
    return _Op(
        request=_request(command, sub),
        answer=_fa() if refused else _answer(command, sub, payload(n)),
        delay=delay,
        blocking=blocking,
        read=True,
        refused=refused,
    )


def _write(n: int, *, blocking: bool, refused: bool, delay: float) -> _Op:
    return _Op(
        request=_request(*_WRITE, data=_level(n)),
        answer=_fa() if refused else _fb(),
        delay=delay,
        blocking=blocking,
        read=False,
        refused=refused,
    )


async def _send(link: _Link, op: _Op, *, lost: bool = False) -> object:
    """One worker step: send ``op``; a blocking one returns its outcome."""
    link.wire.plan.append((None if lost else op.answer, op.delay))
    runtime = link.radio._civ_runtime
    if not op.blocking:
        return await runtime._execute_civ_raw(op.request, wait_response=False)
    try:
        return await runtime._execute_civ_raw(op.request, wait_response=True)
    except Exception as exc:  # noqa: BLE001 - the outcome is the assertion
        return exc


def _assert_own_outcome(op: _Op, outcome: object) -> None:
    if op.read and op.refused:
        assert isinstance(outcome, CommandRejectedError), (op, outcome)
    else:
        assert outcome is op.answer, (op, outcome)


def _random_ops(seed: int, count: int = 18) -> list[_Op]:
    rng = random.Random(seed)
    ops = []
    for n in range(count):
        blocking = rng.random() < 0.5
        refused = rng.random() < 0.25
        # Below the window; the long ones outlast the retired 0.5 s drain.
        delay = rng.choice((0.0, 0.01, 0.2, 0.8, 1.5))
        if rng.random() < 0.6:
            name = rng.choice(tuple(_READS))
            ops.append(_read(name, n, blocking=blocking, refused=refused, delay=delay))
        else:
            ops.append(_write(n, blocking=blocking, refused=refused, delay=delay))
    return ops


@pytest.mark.timeout(60)
@pytest.mark.parametrize("ordered", [True, False], ids=["ordered", "unordered"])
@pytest.mark.parametrize("seed", range(6))
async def test_t1_every_in_order_answer_lands_on_its_own_request(
    link_factory: Callable[..., _Link],
    virtual_clock: VirtualClock,
    ordered: bool,
    seed: int,
) -> None:
    """T1: with no answer lost, attribution is exact whatever the timing."""
    link = link_factory(ordered=ordered)
    ops = _random_ops(seed)

    async def worker() -> list[tuple[_Op, object]]:
        outcomes = []
        for op in ops:
            outcome = await _send(link, op)
            if op.blocking:
                outcomes.append((op, outcome))
        return outcomes

    outcomes = await link.run(worker())
    await link.settle()

    for op, outcome in outcomes:  # type: ignore[attr-defined]
        _assert_own_outcome(op, outcome)
    assert link.tracker.take_refused_reads() == tuple(
        op.identity for op in ops if op.read and op.refused and not op.blocking
    )
    assert link.tracker.timeout_count == 0
    assert link.tracker.pending_count == 0


@pytest.mark.timeout(30)
async def test_t2_a_directed_keyed_answer_retires_every_older_entry(
    link_factory: Callable[..., _Link],
    virtual_clock: VirtualClock,
) -> None:
    """T2: after one lost answer, the next directed keyed answer heals.

    Read sink A loses its answer. Blocking read B's own answer (directed,
    keyed) proves on an ordered transport that A's answer is gone: A is
    retired then and there, so the refusal of the following write C lands
    on C and names no read as refused.
    """
    link = link_factory(ordered=True)
    a = _read("af", 1, blocking=False, refused=False, delay=0.0)
    b = _read("freq", 2, blocking=True, refused=False, delay=0.05)
    c = _write(3, blocking=True, refused=True, delay=0.05)

    async def worker() -> tuple[object, int, object]:
        await _send(link, a, lost=True)
        outcome_b = await _send(link, b)
        open_after_b = link.tracker.ack_sink_count
        return outcome_b, open_after_b, await _send(link, c)

    outcome_b, open_after_b, outcome_c = await link.run(worker())  # type: ignore[misc]

    _assert_own_outcome(b, outcome_b)
    assert open_after_b == 0, "B's directed answer left the older lost sink open"
    _assert_own_outcome(c, outcome_c)
    assert link.tracker.take_refused_reads() == ()


@pytest.mark.asyncio
async def test_t2_an_older_blocking_waiter_passed_by_an_answer_is_lost() -> None:
    """T2: the rule covers blocking waiters too, not only sinks.

    A raw transaction's ACK waiter W went out before read sink R; R's
    directed answer on an ordered transport proves W's answer was lost,
    so W fails as lost instead of later taking somebody else's FB.
    """
    tracker = CivRequestTracker()
    waiter = tracker.register_ack(wait=True, consume_backlog=False)
    assert isinstance(waiter, asyncio.Future)
    tracker.mark_sent(10.0, futures=(waiter,))
    key = CivRequestKey(command=0x14, sub=0x01)
    sink = tracker.register_ack(wait=False, response_key=key, refused_read=key)
    assert isinstance(sink, int)
    tracker.mark_sent(11.0, token=sink)

    assert tracker.resolve(
        CivEvent(type=CivEventType.RESPONSE, frame=_answer(0x14, 0x01, _level(5))),
        ordered_ingress=True,
    )

    assert waiter.done() and isinstance(waiter.exception(), asyncio.TimeoutError)
    assert tracker.pending_count == 0


@pytest.mark.timeout(30)
@pytest.mark.parametrize("ordered", [True, False], ids=["ordered", "unordered"])
async def test_t3_without_keyed_traffic_the_off_by_one_ends_with_the_window(
    link_factory: Callable[..., _Link],
    virtual_clock: VirtualClock,
    ordered: bool,
) -> None:
    """T3: one lost bare answer and no keyed answer to heal it.

    Write sink A loses its FB. Blocking write B, sent inside A's window,
    is answered with an FB that the oldest write -- A -- takes: B runs out
    its window and fails as a timeout (the radio did apply it). From
    there attribution is exact: write C gets its own FB. And once a lost
    sink's window has closed (A2), the next write (B2) is exact at once.
    """
    link = link_factory(ordered=ordered)
    a = _write(1, blocking=False, refused=False, delay=0.0)
    b = _write(2, blocking=True, refused=False, delay=0.05)
    c = _write(3, blocking=True, refused=True, delay=0.05)
    a2 = _write(4, blocking=False, refused=False, delay=0.0)
    b2 = _write(5, blocking=True, refused=False, delay=0.05)

    async def inside_window() -> tuple[object, int, object]:
        await _send(link, a, lost=True)
        outcome_b = await _send(link, b)
        open_after_b = link.tracker.ack_sink_count
        return outcome_b, open_after_b, await _send(link, c)

    outcome_b, open_after_b, outcome_c = await link.run(inside_window())  # type: ignore[misc]
    assert isinstance(outcome_b, CivTimeoutError), outcome_b
    assert open_after_b == 0, "A's sink did not take the FB it was the oldest for"
    _assert_own_outcome(c, outcome_c)

    await link.run(_send(link, a2, lost=True))
    await link.clock.elapse(_WINDOW + 0.1)
    _assert_own_outcome(b2, await link.run(_send(link, b2)))
    assert link.tracker.timeout_count == 1
    assert link.tracker.pending_count == 0


@pytest.mark.timeout(30)
@pytest.mark.parametrize("b_blocking", [True, False], ids=["b-blocking", "b-sink"])
async def test_t5_lost_read_then_acked_write_then_refused_write(
    link_factory: Callable[..., _Link],
    virtual_clock: VirtualClock,
    b_blocking: bool,
) -> None:
    """T5: write C gets its own refusal; lost read A is not marked refused.

    B's FB goes to the oldest write (B: an FB never answers a read), and
    on an ordered transport that directed answer to a newer entry retires
    the older read sink A. C's FA then has nobody older to land on.
    """
    link = link_factory(ordered=True)
    a = _read("af", 1, blocking=False, refused=False, delay=0.0)
    b = _write(2, blocking=b_blocking, refused=False, delay=0.05)
    c = _write(3, blocking=True, refused=True, delay=0.3)

    async def worker() -> tuple[object, object]:
        await _send(link, a, lost=True)
        outcome_b = await _send(link, b)
        return outcome_b, await _send(link, c)

    outcome_b, outcome_c = await link.run(worker())  # type: ignore[misc]

    if b_blocking:
        _assert_own_outcome(b, outcome_b)
    _assert_own_outcome(c, outcome_c)
    assert link.tracker.take_refused_reads() == ()
    assert link.tracker.timeout_count == 0


@pytest.mark.timeout(30)
async def test_t6_tight_commander_deadline_goes_out_at_once_behind_an_open_sink(
    link_factory: Callable[..., _Link],
    virtual_clock: VirtualClock,
) -> None:
    """T6: a 0.5 s caller behind an open read sink is sent immediately.

    Nothing waits for older sinks any more, so the call reaches the wire
    within the pacing gap plus one scheduling tick.
    """
    gap = 0.025
    link = link_factory(ordered=True, min_interval=gap)
    runtime = link.radio._civ_runtime
    sink = _read("af", 1, blocking=False, refused=False, delay=0.0)
    await link.run(_send(link, sink, lost=True))
    runtime.start_worker()
    probe = _read("freq", 2, blocking=True, refused=False, delay=0.01)
    link.wire.plan.append((probe.answer, probe.delay))

    called_at = link.clock()
    result = await link.run(
        link.radio._send_civ_raw(probe.request, timeout=0.5), limit=5.0
    )

    assert result is probe.answer
    sent_at = link.wire.sent[-1][0]
    assert sent_at - called_at <= gap + 0.01, (
        f"the 0.5 s caller reached the wire {sent_at - called_at:.3f} s after the call"
    )


async def test_t7_a_two_argument_executor_takes_a_timed_send() -> None:
    """T7: ``send(..., timeout=...)`` keeps the two-argument executor contract."""
    answer = _fb()
    seen: list[tuple[bytes, bool]] = []

    async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
        seen.append((cmd, wait_response))
        return answer

    commander = IcomCommander(execute)
    commander.start()
    try:
        assert await commander.send(b"\x01", timeout=1) is answer
    finally:
        await commander.stop()
    assert seen == [(b"\x01", True)]
