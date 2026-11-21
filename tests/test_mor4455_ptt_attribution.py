"""MOR-4455: can a late CI-V FB/FA be misattributed to a PTT on/off write?

A bare ``FB``/``FA`` names no command. When a fire-and-forget request's
answer outlives its own answer window, its sink is expired
(``CivRuntime._cleanup_stale_civ_waiters`` ->
``CivRequestTracker.expire_ack_sinks``), and the late frame then meets a
younger waiter: ``CivRequestTracker.resolve`` hands an ``FA`` to the
oldest sent entry of any kind and an ``FB`` to the oldest sent write.
MOR-4455 asked whether a PTT write can take a stranger's answer the same
way the set_frequency write did.

The managed TX actuator is asymmetric by design (MOR-2860, pinned by
``tests/test_icom_managed_tx_actuator.py``): the key-on
(``ActuationOperation.PTT_ON``) is fire-and-forget -- ``actuate`` reports
the *send*, never a radio answer -- while the release
(``ActuationOperation.FORCE_RECEIVE``, PTT OFF) blocks on
``register_ack(wait=True, ...)``. So the exposures to pin are:

* PTT OFF wrongly ``REJECTED`` by a late ``FA`` of an expired read sink
  (the release actually succeeded; its own ``FB`` completes it);
* PTT OFF wrongly ``ACCEPTED`` by a late ``FB`` of an expired write sink
  while the radio refused the release (it must come back UNCERTAIN, or
  be confirmed only by the independent TX readback);
* PTT OFF with no answer at all timing out with no false success;
* PTT ON never reported rejected or radio-confirmed through a stranger's
  frame in any of the same interleavings.

The link and the radio are faked (``_WireRadio``, after
``tests/test_mor4384_wire_order.py``); the tracker, the runtime send path
and the actuator are the real ones. Everything that reads time runs on
one virtual clock, so the production 2 s answer windows cost no
wall-clock time.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core import civ as civ_module
from rigplane.core.civ import CivRequestTracker
from rigplane.core.exceptions import CommandError
from rigplane.radio import IcomRadio
from rigplane.runtime import _civ_rx
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.testing.timing import VirtualClock
from rigplane.types import CivFrame

from _helpers import CIV_HEADER_SIZE, observe_selected_receiver

#: The production answer window (``_civ_get_timeout``).
_WINDOW = 2.0
_ADDR = IC_7610_ADDR
#: How long after the window the stranger's late answer arrives.
_DELTA = 0.2
#: The PTT write's own answer delay, later than the stranger's frame.
_OWN_DELAY = 0.5

_READ_REQ = build_civ_frame(_ADDR, CONTROLLER_ADDR, 0x15, sub=0x02)
_WRITE_REQ = build_civ_frame(_ADDR, CONTROLLER_ADDR, 0x0F, data=b"\x01")


def _fb() -> CivFrame:
    return parse_civ_frame(build_civ_frame(CONTROLLER_ADDR, _ADDR, 0xFB))


def _fa() -> CivFrame:
    return parse_civ_frame(build_civ_frame(CONTROLLER_ADDR, _ADDR, 0xFA))


def _tx_state(keyed: bool) -> CivFrame:
    return parse_civ_frame(
        build_civ_frame(
            CONTROLLER_ADDR, _ADDR, 0x1C, sub=0x00, data=b"\x01" if keyed else b"\x00"
        )
    )


class _WireRadio:
    """A link to a radio that answers every frame in the order sent.

    ``plan`` holds, per send, the answer (``None`` = lost on the wire) and
    its delay; an answer is never delivered before an earlier one, the way
    a radio answering in command order puts them on the wire.
    """

    def __init__(self, clock: VirtualClock) -> None:
        self.ordered_ingress = True
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

    def sent_frames(self, command: int, sub: int | None = None) -> list[CivFrame]:
        return [
            frame
            for _, frame in self.sent
            if frame.command == command and (sub is None or frame.sub == sub)
        ]


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
        await self.clock.elapse(_WINDOW + 1.0)


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

    def make() -> _Link:
        wire = _WireRadio(virtual_clock)
        radio = IcomRadio("192.168.1.100", timeout=_WINDOW, model="IC-7610")
        radio._civ_transport = wire
        radio._ctrl_transport = wire
        radio._connected = True
        observe_selected_receiver(radio, 0)
        radio._civ_min_interval = 0.0
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


def _token() -> EffectToken:
    return EffectToken(1, 1, "mor4455")


async def _actuate(link: _Link, operation: ActuationOperation) -> ActuationResult:
    return await link.run(
        link.radio.actuate(_token(), operation, is_current=lambda: True)
    )


async def _send_fire_and_forget(link: _Link, request: bytes) -> None:
    await link.run(
        link.radio._civ_runtime._execute_civ_raw(request, wait_response=False)
    )


async def _past_the_window(link: _Link) -> None:
    """Move past every open sink's answer window (the expired-sink seam)."""
    await link.clock.elapse(_WINDOW + 0.1)


# ---------------------------------------------------------------------------
# 1. the late frame is the FA of a refused fire-and-forget READ
# ---------------------------------------------------------------------------


async def test_ptt_off_after_expired_read_sink_is_not_rejected_by_its_late_fa(
    link_factory: Callable[..., _Link],
    virtual_clock: VirtualClock,
) -> None:
    """A late refusal of an expired read must not reject the PTT release.

    The read's sink is expired before the release registers, so the late
    ``FA`` meets only the release's blocking ACK waiter. Correct: the
    release is NOT reported rejected -- its own ``FB`` (sent later on the
    wire) completes it.
    """
    link = link_factory()
    # The background read the radio will refuse, late (past its window).
    link.wire.plan.append((_fa(), _WINDOW + _DELTA))
    await _send_fire_and_forget(link, _READ_REQ)
    assert link.tracker.ack_sink_count == 1

    await _past_the_window(link)
    assert link.tracker.ack_sink_count == 1  # not yet expired: no frame since

    # The release's own answer is FB, after the stranger's FA.
    link.wire.plan.append((_fb(), _OWN_DELAY))
    result = await _actuate(link, ActuationOperation.FORCE_RECEIVE)

    # The read sink had to be gone before the release registered; the late
    # FA found nothing else but the release's own waiter.
    assert link.tracker.ack_sink_count == 0
    assert len(link.wire.sent_frames(0x1C, 0x00)) == 1
    assert result is ActuationResult.ACCEPTED, (
        "the PTT release took a stranger's late FA: reported "
        f"{result} while its own FB was on the wire"
    )
    await link.settle()


async def test_ptt_on_after_expired_read_sink_is_not_rejected_by_its_late_fa(
    link_factory: Callable[..., _Link],
    virtual_clock: VirtualClock,
) -> None:
    """The key-on is fire-and-forget: a stranger's FA cannot reject it.

    The late ``FA`` may retire the key-on's own sink, but the actuator
    reports the send (MOR-2860), never a radio refusal, and the stray
    frame leaves no refused-read record for the PTT.
    """
    link = link_factory()
    link.wire.plan.append((_fa(), _WINDOW + _DELTA))
    await _send_fire_and_forget(link, _READ_REQ)

    await _past_the_window(link)
    link.wire.plan.append((_fb(), _OWN_DELAY))
    result = await _actuate(link, ActuationOperation.PTT_ON)
    assert result is ActuationResult.ACCEPTED

    await link.settle()
    assert len(link.wire.sent_frames(0x1C, 0x00)) == 1
    assert link.tracker.take_refused_reads() == ()


# ---------------------------------------------------------------------------
# 2. the late frame is the FB of an earlier fire-and-forget WRITE
# ---------------------------------------------------------------------------


async def test_ptt_off_is_not_confirmed_by_a_late_fb_of_an_expired_write_sink(
    link_factory: Callable[..., _Link],
    virtual_clock: VirtualClock,
) -> None:
    """A stranger's late FB must not confirm a refused PTT release.

    An earlier fire-and-forget write loses its ``FB`` past its window; the
    release's own answer is ``FA`` (the radio refuses to unkey). The stray
    ``FB`` may claim the release's waiter, but the release may then only
    be settled by the independent TX readback -- which finds the radio
    still keyed -- never reported as confirmed.
    """
    link = link_factory()
    # The earlier fire-and-forget write whose OK arrives late.
    link.wire.plan.append((_fb(), _WINDOW + _DELTA))
    await _send_fire_and_forget(link, _WRITE_REQ)
    assert link.tracker.ack_sink_count == 1

    await _past_the_window(link)
    # The release's own answer: refused, and delivered after the stranger.
    link.wire.plan.append((_fa(), _OWN_DELAY))
    # The readback the actuator performs when the answer is ambiguous.
    link.wire.plan.append((_tx_state(keyed=True), 0.02))
    result = await _actuate(link, ActuationOperation.FORCE_RECEIVE)

    readbacks = [
        frame for frame in link.wire.sent_frames(0x1C, 0x00) if not frame.data
    ]
    assert readbacks, "the ambiguous release was never verified by a TX readback"
    assert result is ActuationResult.UNCERTAIN, (
        "the PTT release was confirmed by a stranger's late FB while the "
        "radio refused it (TX would stay keyed while Core reports a release)"
    )
    await link.settle()


async def test_ptt_on_is_not_confirmed_by_a_late_fb_of_an_expired_write_sink(
    link_factory: Callable[..., _Link],
    virtual_clock: VirtualClock,
) -> None:
    """A stranger's late FB cannot confirm or reject the fire-and-forget key-on.

    The key-on's report is the send itself; the stray ``FB`` retires at
    most a sink, and the PTT reply ledger the TX readback relies on stays
    clean (no owed debt answered, quiescence intact).
    """
    link = link_factory()
    link.wire.plan.append((_fb(), _WINDOW + _DELTA))
    await _send_fire_and_forget(link, _WRITE_REQ)

    await _past_the_window(link)
    # The key-on's own answer would be FA (the radio refuses to key).
    link.wire.plan.append((_fa(), _OWN_DELAY))
    result = await _actuate(link, ActuationOperation.PTT_ON)
    assert result is ActuationResult.ACCEPTED

    await link.settle()
    ledger = link.radio._civ_runtime._ptt_reply_ledger
    assert ledger.owed_count == 0
    assert ledger.eligible()
    assert len(link.wire.sent_frames(0x1C, 0x00)) == 1


# ---------------------------------------------------------------------------
# 3. the PTT write's own answer never comes
# ---------------------------------------------------------------------------


async def test_ptt_off_without_its_fb_times_out_with_no_false_success(
    link_factory: Callable[..., _Link],
    virtual_clock: VirtualClock,
) -> None:
    """A release whose FB never comes fails as today: no false success.

    The write runs out its answer window, the readback finds the radio
    still keyed, and the release is UNCERTAIN -- never ACCEPTED without
    the radio's own evidence.
    """
    link = link_factory()
    link.wire.plan.append((None, 0.0))  # the release's FB is lost
    link.wire.plan.append((_tx_state(keyed=True), 0.02))
    result = await _actuate(link, ActuationOperation.FORCE_RECEIVE)

    readbacks = [
        frame for frame in link.wire.sent_frames(0x1C, 0x00) if not frame.data
    ]
    assert readbacks, "the timed-out release was never verified by a TX readback"
    assert result is ActuationResult.UNCERTAIN, (
        f"the unanswered PTT release was reported {result}: a false success"
    )
    await link.settle()


async def test_ptt_on_without_its_fb_times_out_as_today(
    link_factory: Callable[..., _Link],
    virtual_clock: VirtualClock,
) -> None:
    """A key-on whose FB never comes stays what it is: a send, unconfirmed.

    The unanswered sink is expired once its window closes and the tracker
    notes the unclaimed ACK/NAK, so a later release stays suspicious of
    the key-on's late FB instead of trusting it.
    """
    link = link_factory()
    link.wire.plan.append((None, 0.0))  # the key-on's FB is lost
    result = await _actuate(link, ActuationOperation.PTT_ON)
    assert result is ActuationResult.ACCEPTED  # the send, not a confirmation
    assert link.tracker.ack_sink_count == 1

    await link.clock.elapse(_WINDOW + 0.1)
    link.radio._civ_runtime._cleanup_stale_civ_waiters()

    assert link.tracker.ack_sink_count == 0
    assert link.tracker.ack_unclaimed_at is not None, (
        "the lost key-on answer was not noted as unclaimed; a later "
        "release could trust a stranger's FB"
    )
    await link.settle()
