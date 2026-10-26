"""MOR-3743: two concurrent clients racing one split-mode write, end to end.

The whole user path runs on the production stack ``_Arena`` stands up —
the real ``WebServer`` + ``RigctldServer`` + managed TX authority + an
IC-7610 ``IcomRadio`` over the ``MockIcomRadio`` LAN simulator — with two
(and in one scenario three) concurrent clients. Client A's ``X``
split-mode write runs its preservation read -> SET -> readback through
the real commander while a competing write from client B (rigctld TCP
``w``, the HTTP raw CI-V transaction, or a third client's release) lands
between A's read and A's SET. Pinned (MOR-3743's optimistic interference
detection):

1. the lost update stays fixed: B's newer DATA/filter survive A's write —
   A retries from a fresh preservation read instead of putting a SET
   composed from the pre-B values on the wire;
2. persistent interference is honest: three interfered attempts end in a
   rigctld error line and never a stale SET;
3. an unopposed write still takes exactly one preservation read and one
   SET;
4. a TX release is never delayed by a split write: the PTT-off frame
   reaches the simulator while A's preservation read is still held.

The race is deterministic with no production hook: a test-local wrapper
on the simulator's CI-V reply funnel holds the reply to the unselected
receiver's ``0x26`` mode read (the read a split write's preservation step
sends), and a wrapper on ``commander.send`` reports the moment a
competing frame is queued.

FAKE RADIO ONLY — the simulator; nothing here can key a real radio.
"""

from __future__ import annotations

import asyncio
import struct
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

# All tests in this module use MockIcomRadio and require no real hardware.
pytestmark = pytest.mark.mock_integration

from rigplane.radio import IcomRadio  # noqa: E402
from rigplane.types import Mode  # noqa: E402
from rigplane_testkit.icom import MockIcomRadio  # noqa: E402
from rigplane_testkit.icom.simulator import _CIV_HEADER_SIZE  # noqa: E402

from test_tx_under_fault_matrix import (  # noqa: E402
    _KEY_DEADLINE_S,
    _http_json,
    _wait_ptt,
)
from test_multi_session_tx_lanes import _Arena  # noqa: E402

# Bounded waits: every event, reply line and wire observation the tests
# await uses these budgets (asyncio.wait_for / monotonic deadlines).
_EVENT_WAIT_S = 5.0
_WIRE_WAIT_S = 5.0

# A held round nobody releases (a lost test) must not wedge the simulator
# past teardown: the deferred reply is dropped after this bound.
_HOLD_RELEASE_TIMEOUT_S = 10.0


def _is_unselected_mode_get(packet: bytes, radio_addr: int) -> bool:
    """True for a DATA packet whose CI-V frame is a ``26 01`` GET.

    Mirrors the parse ``MockIcomRadio._handle_civ_data`` performs (it is
    the method this test wraps): datalen at 0x11, frame at
    ``_CIV_HEADER_SIZE``, addressed to the radio, command ``0x26`` with
    the one-byte unselected-receiver selector and nothing else.
    """
    datalen = struct.unpack_from("<H", packet, 0x11)[0]
    end = _CIV_HEADER_SIZE + datalen
    if end > len(packet):
        return False
    civ = packet[_CIV_HEADER_SIZE:end]
    return (
        len(civ) >= 6
        and civ[:2] == b"\xfe\xfe"
        and civ[-1:] == b"\xfd"
        and civ[2] == radio_addr
        and civ[4] == 0x26
        and civ[5:-1] == b"\x01"
    )


class _SplitModeReadHold:
    """Hold the simulator's replies to the unselected receiver's mode reads.

    Wraps ``MockIcomRadio._handle_civ_data`` — the simulator's parsed
    CI-V reply funnel — with no production change. The first ``rounds``
    CI-V ``26 01`` GETs are received but not answered: each held read
    fires its ``received`` event the moment the frame arrives (so the
    test knows the owning client is paused waiting for its preservation
    readback), and is only processed — LAN echo, capture, state, reply —
    by the original handler after the test sets that round's ``release``
    event. Matching frames beyond ``rounds`` pass straight through, so a
    read the test did not plan for can never wedge the radio.
    """

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, sim: MockIcomRadio, *, rounds: int
    ) -> None:
        self.rounds = [
            SimpleNamespace(received=asyncio.Event(), release=asyncio.Event())
            for _ in range(rounds)
        ]
        self._held = 0
        radio_addr = sim._radio_addr  # noqa: SLF001
        original = sim._handle_civ_data  # noqa: SLF001

        def held(data: bytes, addr: object, sender_id: int, proto: object) -> None:
            if self._held < len(self.rounds) and _is_unselected_mode_get(
                data, radio_addr
            ):
                round_ = self.rounds[self._held]
                self._held += 1
                round_.received.set()

                async def deliver() -> None:
                    try:
                        await asyncio.wait_for(
                            round_.release.wait(), _HOLD_RELEASE_TIMEOUT_S
                        )
                        original(data, addr, sender_id, proto)
                    except Exception:
                        pass  # a lost test round must not wedge teardown

                asyncio.get_running_loop().create_task(deliver())
                return
            original(data, addr, sender_id, proto)

        monkeypatch.setattr(sim, "_handle_civ_data", held)

    async def wait_read(self, index: int) -> None:
        """Bounded wait for the ``index``-th held read to arrive."""
        await asyncio.wait_for(self.rounds[index].received.wait(), _EVENT_WAIT_S)

    def release(self, index: int) -> None:
        self.rounds[index].release.set()

    def release_all(self) -> None:
        for round_ in self.rounds:
            round_.release.set()


class _CommanderEnqueueProbe:
    """One event per awaited raw frame, set when it enters the commander queue.

    The event fires inside the ``commander.send`` wrapper before the real
    send proceeds; from there to the queue ``put`` there is no await (the
    commander's queue is unbounded), so observing the event means the
    frame is already queued ahead of anything the paused client sends
    later. Register the payloads with :meth:`expect` before sending them.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, radio: IcomRadio) -> None:
        commander = radio._commander  # noqa: SLF001
        assert commander is not None, "the stack's radio has no commander"
        self.events: dict[bytes, asyncio.Event] = {}
        send = commander.send

        async def observing_send(frame: bytes, **kwargs: object) -> object:
            event = self.events.get(frame)
            if event is not None:
                event.set()
            return await send(frame, **kwargs)

        monkeypatch.setattr(commander, "send", observing_send)

    def expect(self, payload: bytes) -> asyncio.Event:
        return self.events.setdefault(payload, asyncio.Event())


def _seed_unselected(
    sim: MockIcomRadio, mode: int, data_mode: int, filter_num: int
) -> None:
    """Put the simulator's unselected receiver in a known distinct state."""
    sim._unselected_mode = mode  # noqa: SLF001
    sim._unselected_data_mode = data_mode  # noqa: SLF001
    sim._unselected_filter = filter_num  # noqa: SLF001


def _unselected_state(sim: MockIcomRadio) -> tuple[int, int, int]:
    """The simulator's unselected receiver as ``(mode, DATA, filter)``."""
    return (
        sim._unselected_mode,  # noqa: SLF001
        sim._unselected_data_mode,  # noqa: SLF001
        sim._unselected_filter,  # noqa: SLF001
    )


def _split_mode_frames(sim: MockIcomRadio) -> list[bytes]:
    """Captured ``0x26`` frames for the unselected receiver, in wire order.

    The stack stands the simulator up with ``capture_civ_frames=True``;
    a held read is captured when its deferred processing runs (at
    release), which is exactly the order the radio saw replies in.
    """
    return [
        frame
        for frame in sim.civ_frames_received
        if len(frame) >= 7 and frame[4] == 0x26 and frame[5] == 0x01
    ]


async def _wait_until(claim: Callable[[], bool], message: str) -> None:
    """Bounded wait for ``claim()`` to become truthy."""
    end = time.monotonic() + _WIRE_WAIT_S
    while not claim():
        assert time.monotonic() < end, message
        await asyncio.sleep(0.02)


async def _settle(task: asyncio.Task[str]) -> None:  # type: ignore[type-arg]
    """Cancel-and-drain a reply task a failing test may have left pending."""
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_two_rigctld_clients_composing_write_keeps_bs_newer_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two rigctld TCP clients: B's write survives A's paused split write.

    This scenario is the MOR-3743 lost-update repro: A's ``X RTTY 0``
    omits DATA and filter, so its SET is composed from the preservation
    read. The simulator holds the reply to A's read; while A is paused,
    client B's raw ``w`` mode write (FM, DATA on, FIL1) is queued ahead of
    A's SET, and the hold is released only after B's frame is queued.
    Without ``interference_scope`` (the ``origin/main`` commander) A's
    first SET — composed from the pre-B read — dispatches after B's SET by
    queue order and overwrites B's DATA/filter with the stale values; the
    test reduces to the stale-overwrite assertion on the final receiver
    state and on A's SET bytes.
    """
    async with _Arena(tmp_path) as arena:
        addr = arena.stack.radio.profile.civ_addr
        _seed_unselected(arena.sim, Mode.USB.value, 0x00, 0x03)
        hold = _SplitModeReadHold(monkeypatch, arena.sim, rounds=1)
        probe = _CommanderEnqueueProbe(monkeypatch, arena.stack.radio)
        # B's competing unselected-receiver write: FM, DATA on, FIL1.
        b_set = bytes(
            (0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, Mode.FM.value, 0x01, 0x01, 0xFD)
        )
        b_line = f"w {b_set.hex(' ').upper()}"
        probe.expect(b_set)
        a, b = await arena.tcp(), await arena.tcp()
        reply_b: asyncio.Task[str] | None = None
        reply_a = asyncio.create_task(arena.tcp_line(a, "X RTTY 0"))
        try:
            await hold.wait_read(0)
            reply_b = asyncio.create_task(arena.tcp_line(b, b_line))
            await asyncio.wait_for(probe.expect(b_set).wait(), _EVENT_WAIT_S)
            hold.release(0)
            assert await asyncio.wait_for(reply_a, _EVENT_WAIT_S) == "RPRT 0\n"
            assert await asyncio.wait_for(reply_b, _EVENT_WAIT_S) == (
                f"FE FE E0 {addr:02X} FB FD\n"
            )
        finally:
            hold.release_all()
            await _settle(reply_a)
            if reply_b is not None:
                await _settle(reply_b)
        # A's requested mode with B's newer DATA/filter — not the stale
        # (DATA off, FIL3) A read before B's write.
        assert _unselected_state(arena.sim) == (Mode.RTTY.value, 0x01, 0x01)
        get = bytes((0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, 0xFD))
        assert _split_mode_frames(arena.sim) == [
            get,  # A's preservation read (held, processed at release)
            b_set,  # B's competing SET dispatched ahead of A's
            get,  # A's retry preservation read: it re-reads B's values
            bytes(
                (0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, Mode.RTTY.value, 0x01, 0x01, 0xFD)
            ),  # A's one and only SET — the retry's, after B's
            get,  # A's readback
        ]


async def test_http_raw_transaction_competitor_keeps_bs_newer_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client B competes through ``POST /api/v1/civ/transaction``.

    The raw transaction reaches the wire through the direct dispatch seam
    without entering the commander queue, so it lands while A's
    preservation read is still held (the reply to the POST returning is
    the deterministic proof the frame was sent and ACKed) and must mark
    A's interference scope like any queued frame: A's stale SET is
    refused before the wire, the retry re-reads, and B's DATA/filter
    survive (MOR-3743).
    """
    async with _Arena(tmp_path) as arena:
        addr = arena.stack.radio.profile.civ_addr
        _seed_unselected(arena.sim, Mode.USB.value, 0x00, 0x03)
        hold = _SplitModeReadHold(monkeypatch, arena.sim, rounds=1)
        a = await arena.tcp()
        reply_a = asyncio.create_task(arena.tcp_line(a, "X RTTY 0"))
        try:
            await hold.wait_read(0)
            status, body = await asyncio.wait_for(
                _http_json(
                    arena.port,
                    "POST",
                    "/api/v1/civ/transaction",
                    {
                        "command": 0x26,
                        "data": "01050101",
                        "expect": "ack",
                        "timeout_ms": 1000,
                    },
                ),
                _EVENT_WAIT_S,
            )
            assert (status, body.get("ok"), body.get("status")) == (200, True, "ack")
            hold.release(0)
            assert await asyncio.wait_for(reply_a, _EVENT_WAIT_S) == "RPRT 0\n"
        finally:
            hold.release_all()
            await _settle(reply_a)
        assert _unselected_state(arena.sim) == (Mode.RTTY.value, 0x01, 0x01)
        get = bytes((0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, 0xFD))
        assert _split_mode_frames(arena.sim) == [
            bytes(
                (0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, Mode.FM.value, 0x01, 0x01, 0xFD)
            ),  # B's direct-path SET, on the wire during the hold
            get,  # A's preservation read (held, processed at release)
            get,  # A's retry preservation read
            bytes(
                (0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, Mode.RTTY.value, 0x01, 0x01, 0xFD)
            ),  # A's one SET — the retry's, after B's
            get,  # A's readback
        ]


async def test_persistent_interference_fails_the_write_without_a_stale_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interference on every attempt: an honest error, never a stale SET.

    B repeats its write on each of A's three attempts (each preservation
    read is held until that round's competing write is queued). All three
    SETs are refused before the wire, so A's reply is a rigctld error
    line — not a success bought by overwriting B — and the simulator
    keeps B's last change.
    """
    async with _Arena(tmp_path) as arena:
        addr = arena.stack.radio.profile.civ_addr
        _seed_unselected(arena.sim, Mode.USB.value, 0x00, 0x03)
        hold = _SplitModeReadHold(monkeypatch, arena.sim, rounds=3)
        probe = _CommanderEnqueueProbe(monkeypatch, arena.stack.radio)
        # Three distinct competing writes; the last one must win.
        b_sets = [
            bytes((0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, mode, data, filt))
            for mode, data, filt in (
                (Mode.FM.value, 0x01, 0x01),
                (Mode.LSB.value, 0x01, 0x02),
                (Mode.FM.value, 0x00, 0x03),
            )
        ]
        b_lines = [f"w {b_set.hex(' ').upper()}" for b_set in b_sets]
        for payload in b_sets:
            probe.expect(payload)
        a, b = await arena.tcp(), await arena.tcp()
        reply_a = asyncio.create_task(arena.tcp_line(a, "X RTTY 0"))
        replies_b: list[asyncio.Task[str]] = []
        try:
            for index, (payload, line) in enumerate(zip(b_sets, b_lines)):
                await hold.wait_read(index)
                replies_b.append(asyncio.create_task(arena.tcp_line(b, line)))
                await asyncio.wait_for(probe.expect(payload).wait(), _EVENT_WAIT_S)
                hold.release(index)
            assert await asyncio.wait_for(reply_a, _EVENT_WAIT_S) == "RPRT -11\n"
            for reply_b in replies_b:
                assert await asyncio.wait_for(reply_b, _EVENT_WAIT_S) == (
                    f"FE FE E0 {addr:02X} FB FD\n"
                )
        finally:
            hold.release_all()
            await _settle(reply_a)
            for reply_b in replies_b:
                await _settle(reply_b)
        get = bytes((0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, 0xFD))
        assert _split_mode_frames(arena.sim) == [
            get,
            b_sets[0],
            get,
            b_sets[1],
            get,
            b_sets[2],
        ]
        # B's last change stands; A's data never reached the wire.
        assert _unselected_state(arena.sim) == (Mode.FM.value, 0x00, 0x03)


async def test_unopposed_split_write_reads_once_and_sets_once(
    tmp_path: Path,
) -> None:
    """Control: with nobody competing, one read, one SET, one readback."""
    async with _Arena(tmp_path) as arena:
        addr = arena.stack.radio.profile.civ_addr
        _seed_unselected(arena.sim, Mode.USB.value, 0x00, 0x03)
        a = await arena.tcp()
        assert (
            await asyncio.wait_for(arena.tcp_line(a, "X RTTY 0"), _EVENT_WAIT_S)
            == "RPRT 0\n"
        )
        get = bytes((0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, 0xFD))
        assert _split_mode_frames(arena.sim) == [
            get,  # the one preservation read
            bytes(
                (0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, Mode.RTTY.value, 0x00, 0x03, 0xFD)
            ),  # the one SET, preserving DATA off / FIL3
            get,  # the readback
        ]
        assert _unselected_state(arena.sim) == (Mode.RTTY.value, 0x00, 0x03)


async def test_ptt_release_is_not_delayed_by_a_held_split_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TX release lands on the wire while A's preservation read is held.

    Client C keys, then A's ``X`` parks at its held preservation read,
    then C ends the transmission through the HTTP raw CI-V transaction —
    the unmanaged de-key path the stack allows (every managed actuation
    serializes through the commander queue behind the held read; the raw
    transaction's direct dispatch does not). The PTT-off frame must reach
    the simulator before A's read is released, and the radio must end in
    RX. The de-key also marks A's scope, so A still finishes cleanly on
    its retry (MOR-3743: detection, never delay).
    """
    async with _Arena(tmp_path) as arena:
        addr = arena.stack.radio.profile.civ_addr
        _seed_unselected(arena.sim, Mode.USB.value, 0x00, 0x03)
        hold = _SplitModeReadHold(monkeypatch, arena.sim, rounds=1)
        a, c = await arena.tcp(), await arena.tcp()
        assert await c.key()
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        reply_a = asyncio.create_task(arena.tcp_line(a, "X RTTY 0"))
        try:
            await hold.wait_read(0)

            def ptt_off_frame() -> bool:
                return any(
                    len(frame) == 8 and frame[4] == 0x1C and frame[6] == 0x00
                    for frame in arena.sim.civ_frames_received
                )

            status, body = await asyncio.wait_for(
                _http_json(
                    arena.port,
                    "POST",
                    "/api/v1/civ/transaction",
                    {
                        "command": 0x1C,
                        "sub": 0x00,
                        "data": "00",
                        "expect": "ack",
                        "timeout_ms": 1000,
                    },
                ),
                _EVENT_WAIT_S,
            )
            assert (status, body.get("ok"), body.get("status")) == (200, True, "ack")
            await _wait_until(
                ptt_off_frame,
                "the PTT-off frame never reached the simulator while the "
                "split write was held",
            )
            hold.release(0)
            assert await asyncio.wait_for(reply_a, _EVENT_WAIT_S) == "RPRT 0\n"
        finally:
            hold.release_all()
            await _settle(reply_a)
        frames = arena.sim.civ_frames_received
        off_index = next(
            index
            for index, frame in enumerate(frames)
            if len(frame) == 8 and frame[4] == 0x1C and frame[6] == 0x00
        )
        split = _split_mode_frames(arena.sim)
        a_set = bytes(
            (0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, Mode.RTTY.value, 0x01, 0x01, 0xFD)
        )
        assert split.count(a_set) == 1
        # A's one SET exists only after the PTT-off frame reached the wire.
        assert frames.index(a_set) > off_index
        get = bytes((0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, 0xFD))
        assert split == [get, get, a_set, get]
        # The holder's own release still settles; the radio ends in RX.
        assert (
            await asyncio.wait_for(arena.tcp_line(c, "T 0"), _EVENT_WAIT_S)
            == "RPRT 0\n"
        )
        assert arena.sim.ptt_on is False
