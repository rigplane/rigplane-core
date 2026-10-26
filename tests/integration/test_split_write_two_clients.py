"""MOR-3743: two concurrent clients racing one split-mode write, end to end.

The whole user path runs on the production stack ``_Arena`` stands up —
the real ``WebServer`` + ``RigctldServer`` + managed TX authority + an
IC-7610 ``IcomRadio`` over the ``MockIcomRadio`` LAN simulator — with two
(and in one scenario three) concurrent clients. Client A's ``X``
split-mode write runs its preservation read -> SET -> readback through
the real commander while a competing write from client B (rigctld TCP
``w`` or the HTTP raw CI-V transaction) lands between A's preserved
values and A's SET. Pinned (MOR-3743's optimistic interference
detection):

1. the lost update stays fixed: B's newer DATA/filter survive A's write —
   A retries from a fresh preservation read instead of putting a SET
   composed from the pre-B values on the wire;
2. persistent interference is honest: three interfered attempts end in a
   rigctld error line and never a stale SET;
3. an unopposed write takes exactly one preservation read and one SET
   per attempt;
4. an interference scope changes no dispatch order or priority: a
   PTT-off queued at FORCE_RELEASE priority while the owner's write is
   mid-flight is dispatched in plain (priority, seq) order right after
   the in-flight exchange;
5. a competitor landing while the owner's SET ACK is pending buys no
   retry: the behind-the-send staleness surfaces as ``RPRT -6`` and no
   second SET (composed from a re-read) reaches the wire.

Two facts about the real wire shape this file's model (learned from the
Mac mini traces at head a9db58531):

* The IC-7610 poller reads the SUB receiver's mode with the SAME CI-V
  ``26 01`` GET the split write's preservation step sends
  (``runtime/_state_queries.py`` maps ``receiver.sub.active.freq_mode.mode``
  to ``get_unselected_mode``). Those background GETs are honest traffic,
  but they land in the exact wire asserted on here and the read hold can
  grab one (which is how the first version of these tests wedged). Each
  scenario therefore stands acquisition polling down through the
  scheduler's existing external-CAT pause — the mechanism
  ``rigs/ic7610.toml``'s ``external_cat_pause = "pause_polling"`` policy
  describes — after which every ``26 01`` GET on the wire belongs to the
  write under test. Command-queue writes (rigctld ``X``/``w``, HTTP)
  keep draining; only cadence polling defers.
* The per-attempt preservation read count N is DERIVED, never
  hard-coded: every scenario first runs one unopposed control write in
  the same stack and counts the ``26 01`` GETs between the write's start
  and its SET. With polling stood down that count is the write's own (1
  at HEAD, matching ``tests/test_commander_rmw_reservation.py``'s
  radio-level ``mode_reads == 2`` pin), and the race choreography holds
  the LAST — the Nth — preservation GET of an attempt, so B's write
  lands after A has read its preserved values and before A's SET. That
  is the lost-update window: without ``interference_scope`` (the
  ``origin/main`` commander) A's first SET carries the stale preserved
  fields, dispatches after B's SET by queue order, and overwrites B's
  DATA/filter — scenario 1/2 reduce to that stale-overwrite assertion.

Scenario 4 of the first version (an unmanaged de-key reaching the wire
during a held read) is replaced by the dispatch-order pin above: this
stack runs under managed TX authority, and the HTTP raw transaction
endpoint refuses a TX-family frame by payload alone there —
``web/server.py: _HttpCommandExecutor.execute`` raises
``TxInterlockRefusal("managed_tx_refused")`` → HTTP 403 — because in
managed mode PTT moves only through the authority. The honest wire path
for an unmanaged de-key is intentionally unreachable, so what this PR
actually claims is pinned instead: no delay, no reordering. The one
wait that remains — any queued frame waits for the commander's current
exchange to finish — is the pre-existing single-exchange serialisation,
not anything ``interference_scope`` adds.

FAKE RADIO ONLY — the simulator; nothing here can key a real radio.
"""

from __future__ import annotations

import asyncio
import struct
import time
from pathlib import Path
from collections.abc import Callable
from types import SimpleNamespace

import pytest

# All tests in this module use MockIcomRadio and require no real hardware.
pytestmark = pytest.mark.mock_integration

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame  # noqa: E402
from rigplane.commands._frame import decode_wire_tuple  # noqa: E402
from rigplane.commands.commander import Priority  # noqa: E402
from rigplane.core.acquisition_scheduler import (  # noqa: E402
    AcquisitionScheduler,
)
from rigplane.runtime.radio import IcomRadio  # noqa: E402
from rigplane.types import Mode  # noqa: E402
from rigplane_testkit.icom import MockIcomRadio  # noqa: E402
from rigplane_testkit.icom.simulator import _CIV_HEADER_SIZE  # noqa: E402

from test_tx_under_fault_matrix import (  # noqa: E402
    _http_json,
    _RigctldKeyClient,
)
from test_multi_session_tx_lanes import _Arena  # noqa: E402

# Bounded waits: every event, reply line and wire observation the tests
# await uses these budgets (asyncio.wait_for / monotonic deadlines).
_EVENT_WAIT_S = 5.0
_WIRE_WAIT_S = 5.0

# A held round nobody releases (a lost test) must not wedge the simulator
# past teardown: the deferred reply is dropped after this bound.
_HOLD_RELEASE_TIMEOUT_S = 10.0

# After the acquisition stand-down, the wire must stay silent this long
# before a scenario starts: polls already dispatched when the pause hit
# drain within this window, so the first asserted frame is never theirs.
_WIRE_QUIET_S = 0.15

# FE FE <addr> E0 26 01 FD — the unselected-receiver mode GET.
_GET_LEN = 7


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


def _is_unselected_mode_set(packet: bytes, radio_addr: int) -> bool:
    """True for a DATA packet whose CI-V frame is a ``26 01`` SET.

    Same parse as :func:`_is_unselected_mode_get`; the frame carries the
    unselected-receiver selector plus the three-byte mode/DATA/filter
    payload — ``FE FE <addr> E0 26 01 <mode> <DATA> <filter> FD``, ten
    bytes with the terminator — so holding it parks the radio's ACK to
    that SET.
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
        and len(civ) == 10
        and civ[5] == 0x01
    )


def _frames_debug(sim: MockIcomRadio) -> str:
    """Every CI-V frame the simulator has seen, as one hex dump."""
    return (
        "frames seen by the simulator: ["
        + ", ".join(frame.hex(" ").upper() for frame in sim.civ_frames_received)
        + "]"
    )


def _get_frame(addr: int) -> bytes:
    """``26 01`` GET — the unselected-receiver mode read."""
    return bytes((0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, 0xFD))


def _set_frame(addr: int, mode: int, data_mode: int, filter_num: int) -> bytes:
    """``26 01 <mode> <data> <filter>`` SET on the unselected receiver."""
    return bytes(
        (
            0xFE,
            0xFE,
            addr,
            0xE0,
            0x26,
            0x01,
            mode,
            data_mode,
            filter_num,
            0xFD,
        )
    )


def _ptt_off_frame(addr: int) -> bytes:
    """``1C 00 00`` — the PTT-off frame the urgent lane sends."""
    return bytes((0xFE, 0xFE, addr, 0xE0, 0x1C, 0x00, 0x00, 0xFD))


def _pause_acquisition(arena: _Arena) -> None:
    """Stand the stack's acquisition polling down for this test.

    The scheduler's external-CAT stand-down
    (``core/acquisition_scheduler.py: pause_external_cat``) is the
    existing mechanism behind ``external_cat_pause = "pause_polling"``:
    while an external CAT owner has the wire, every cadence group
    defers. These tests' rigctld/HTTP clients are that owner. Pausing
    keeps the poller's SUB-mode ``26 01`` GETs
    (``receiver.sub.active.freq_mode.mode`` → ``get_unselected_mode``)
    off the asserted wire and out of the read hold; the command queue —
    rigctld ``X``/``w`` and HTTP writes — keeps draining, so the system
    under test is unchanged. Every scheduler seat the arena exposes is
    paused: the web poller's and the rigctld server's normally share one
    instance, but pausing both is idempotent and seat-independent.
    """
    poller = getattr(arena.web, "_radio_poller", None)
    seats: list[AcquisitionScheduler] = []
    for candidate in (
        None if poller is None else getattr(poller, "_acquisition_scheduler", None),
        getattr(arena.stack.rigctld, "_acquisition_scheduler", None),
    ):
        if isinstance(candidate, AcquisitionScheduler) and not any(
            candidate is seat for seat in seats
        ):
            seats.append(candidate)
    assert seats, "the arena exposes no acquisition scheduler to stand down"
    for scheduler in seats:
        scheduler.pause_external_cat(
            owner="mor3743-e2e", reason="deterministic split-mode wire"
        )


async def _drain_in_flight_polls(arena: _Arena) -> None:
    """Bounded wait until polls dispatched before the pause have drained."""
    sim = arena.sim
    deadline = time.monotonic() + _WIRE_WAIT_S
    quiet_from = time.monotonic() + _WIRE_QUIET_S
    last_seen = len(sim.civ_frames_received)
    while time.monotonic() < quiet_from:
        if time.monotonic() >= deadline:
            raise AssertionError(
                "the simulator wire never went quiet after the "
                f"acquisition pause; {_frames_debug(sim)}"
            )
        await asyncio.sleep(0.02)
        seen = len(sim.civ_frames_received)
        if seen != last_seen:
            last_seen = seen
            quiet_from = time.monotonic() + _WIRE_QUIET_S


class _SplitModeFrameHold:
    """Hold the simulator's processing of chosen ``26 01`` frames.

    Wraps ``MockIcomRadio._handle_civ_data`` — the simulator's parsed
    CI-V reply funnel — with no production change. Frames matching
    ``match`` (by default the unselected-receiver mode GET) are counted
    in arrival order; the frames whose 1-based ordinal is in
    ``ordinals`` are received but not processed: each held frame fires
    its ``received`` event the moment it arrives (so the test knows what
    owns the wire — a read parked waiting for its reply, or a SET whose
    ACK has not gone out — even when the frame itself is not captured
    until release) and is only processed — LAN echo, capture, state,
    reply — by the original handler after the test sets that round's
    ``release`` event. Every other frame passes straight through, so
    traffic the test did not plan for can never wedge the radio.
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sim: MockIcomRadio,
        *,
        ordinals: tuple[int, ...],
        match: Callable[[bytes, int], bool] = _is_unselected_mode_get,
    ) -> None:
        assert ordinals, "the hold needs at least one GET ordinal"
        assert list(ordinals) == sorted(ordinals), "ordinals must be sorted"
        self._sim = sim
        self.rounds = {
            ordinal: SimpleNamespace(received=asyncio.Event(), release=asyncio.Event())
            for ordinal in ordinals
        }
        self._seen = 0
        radio_addr = sim._radio_addr  # noqa: SLF001
        original = sim._handle_civ_data  # noqa: SLF001

        def held(data: bytes, addr: object, sender_id: int, proto: object) -> None:
            if match(data, radio_addr):
                self._seen += 1
                round_ = self.rounds.get(self._seen)
                if round_ is not None:
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

    async def wait_read(self, ordinal: int) -> None:
        """Bounded wait for the held frame at ``ordinal`` to arrive."""
        try:
            await asyncio.wait_for(self.rounds[ordinal].received.wait(), _EVENT_WAIT_S)
        except asyncio.TimeoutError:
            raise AssertionError(
                f"held frame #{ordinal} never reached the simulator; "
                f"{_frames_debug(self._sim)}"
            ) from None

    def release(self, ordinal: int) -> None:
        self.rounds[ordinal].release.set()

    def release_all(self) -> None:
        for round_ in self.rounds.values():
            round_.release.set()


class _CommanderEnqueueProbe:
    """One event per awaited raw frame, set when it enters the commander queue.

    The event fires inside the ``commander.send`` wrapper before the real
    send proceeds; from there to the queue ``put`` there is no await (the
    commander's queue is unbounded), so observing the event means the
    frame is already queued ahead of anything the paused client sends
    later. Register the payloads with :meth:`expect` before sending them.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, radio: object) -> None:
        commander = getattr(radio, "_commander", None)  # noqa: SLF001
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


async def _await_probe(event: asyncio.Event, sim: MockIcomRadio, claim: str) -> None:
    """Bounded probe wait; a timeout reports the whole wire."""
    try:
        await asyncio.wait_for(event.wait(), _EVENT_WAIT_S)
    except asyncio.TimeoutError:
        raise AssertionError(f"{claim}; {_frames_debug(sim)}") from None


async def _await_wire_frame(sim: MockIcomRadio, frame: bytes, claim: str) -> None:
    """Bounded wait until ``frame`` is captured by the simulator."""
    deadline = time.monotonic() + _WIRE_WAIT_S
    while frame not in sim.civ_frames_received:
        if time.monotonic() >= deadline:
            raise AssertionError(f"{claim}; {_frames_debug(sim)}") from None
        await asyncio.sleep(0.02)


async def _await_line(task: asyncio.Task[str], sim: MockIcomRadio, claim: str) -> str:
    """Bounded reply wait; a timeout reports the whole wire."""
    try:
        return await asyncio.wait_for(task, _EVENT_WAIT_S)
    except asyncio.TimeoutError:
        raise AssertionError(f"{claim} never replied; {_frames_debug(sim)}") from None


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


def _split_mode_frames(sim: MockIcomRadio, start: int = 0) -> list[bytes]:
    """Captured ``0x26`` frames for the unselected receiver, in wire order.

    The stack stands the simulator up with ``capture_civ_frames=True``;
    a held read is captured when its deferred processing runs (at
    release), which is exactly the order the radio saw replies in.
    ``start`` indexes ``civ_frames_received`` so a scenario asserts only
    on its own window of the wire.
    """
    return [
        frame
        for frame in sim.civ_frames_received[start:]
        if len(frame) >= 7 and frame[4] == 0x26 and frame[5] == 0x01
    ]


async def _settle(task: asyncio.Task[str]) -> None:  # type: ignore[type-arg]
    """Cancel-and-drain a reply task a failing test may have left pending."""
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _control_write(arena: _Arena, rig: _RigctldKeyClient) -> int:
    """One unopposed ``X RTTY 0`` write; returns N, its per-attempt GET count.

    N — the number of ``26 01`` preservation GETs one attempt sends
    before its SET — is derived from the wire, never hard-coded: the
    scenario races run right after this control in the same stack, so
    whatever the path in front of ``write_relative_vfo_mode`` contributes
    today is measured, not assumed. The seed pins the SET bytes (RTTY
    keeping the seeded DATA off / FIL3) and the readback, so this doubles
    as the scenario-4 control: one attempt, one SET, one readback GET.
    The write also leaves the unselected receiver at (RTTY, 0, 3) — the
    stale values an ``origin/main`` commander would put on the wire in
    the races that follow.
    """
    addr = arena.stack.radio.profile.civ_addr
    start = len(arena.sim.civ_frames_received)
    reply = await _await_line(
        asyncio.create_task(arena.tcp_line(rig, "X RTTY 0")),
        arena.sim,
        "the control write",
    )
    assert reply == "RPRT 0\n", _frames_debug(arena.sim)
    window = _split_mode_frames(arena.sim, start)
    set_index = next(
        (index for index, frame in enumerate(window) if len(frame) > _GET_LEN),
        None,
    )
    assert set_index is not None, _frames_debug(arena.sim)
    n = set_index
    assert window == [_get_frame(addr)] * n + [
        _set_frame(addr, Mode.RTTY.value, 0x00, 0x03),
        _get_frame(addr),
    ], _frames_debug(arena.sim)
    assert _unselected_state(arena.sim) == (
        Mode.RTTY.value,
        0x00,
        0x03,
    ), _frames_debug(arena.sim)
    return n


async def test_two_rigctld_clients_composing_write_keeps_bs_newer_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two rigctld TCP clients: B's write survives A's paused split write.

    This scenario is the MOR-3743 lost-update repro. A's ``X RTTY 0``
    omits DATA and filter, so its SET is composed from the preservation
    read; N is derived from a control write in this same stack, and the
    hold parks A on the Nth — the LAST — preservation GET of its first
    attempt, so B's competing raw ``w`` write (FM, DATA on, FIL1) is
    queued after A has read its preserved values and before A's SET.
    Without ``interference_scope`` (the ``origin/main`` commander) A's
    first SET — composed from the pre-B read (RTTY, DATA off, FIL3) —
    dispatches after B's SET by queue order and overwrites B's
    DATA/filter; the test reduces to the stale-overwrite assertion on
    the final receiver state and on A's SET bytes.
    """
    async with _Arena(tmp_path) as arena:
        _pause_acquisition(arena)
        await _drain_in_flight_polls(arena)
        addr = arena.stack.radio.profile.civ_addr
        _seed_unselected(arena.sim, Mode.USB.value, 0x00, 0x03)
        a, b = await arena.tcp(), await arena.tcp()
        n = await _control_write(arena, a)
        start = len(arena.sim.civ_frames_received)
        hold = _SplitModeFrameHold(monkeypatch, arena.sim, ordinals=(n,))
        probe = _CommanderEnqueueProbe(monkeypatch, arena.stack.radio)
        # B's competing unselected-receiver write: FM, DATA on, FIL1.
        b_set = _set_frame(addr, Mode.FM.value, 0x01, 0x01)
        probe.expect(b_set)
        reply_a = asyncio.create_task(arena.tcp_line(a, "X RTTY 0"))
        reply_b: asyncio.Task[str] | None = None
        try:
            # A has read its preserved values (reads 1..N done, the Nth
            # held) and is parked before its SET.
            await hold.wait_read(n)
            reply_b = asyncio.create_task(
                arena.tcp_line(b, f"w {b_set.hex(' ').upper()}")
            )
            await _await_probe(
                probe.expect(b_set),
                arena.sim,
                "B's competing SET never entered the commander queue",
            )
            hold.release(n)
            assert await _await_line(reply_a, arena.sim, "A's write") == "RPRT 0\n", (
                _frames_debug(arena.sim)
            )
            assert (
                await _await_line(reply_b, arena.sim, "B's write")
                == f"FE FE E0 {addr:02X} FB FD\n"
            ), _frames_debug(arena.sim)
        finally:
            hold.release_all()
            await _settle(reply_a)
            if reply_b is not None:
                await _settle(reply_b)
        # A's requested mode with B's newer DATA/filter — not the stale
        # (DATA off, FIL3) A read before B's write.
        assert _unselected_state(arena.sim) == (
            Mode.RTTY.value,
            0x01,
            0x01,
        ), _frames_debug(arena.sim)
        a_set = _set_frame(addr, Mode.RTTY.value, 0x01, 0x01)
        get = _get_frame(addr)
        window = _split_mode_frames(arena.sim, start)
        assert window.count(a_set) == 1, _frames_debug(arena.sim)
        assert window == [get] * n + [b_set] + [get] * n + [
            a_set,
            get,
        ], _frames_debug(arena.sim)


async def test_http_raw_transaction_competitor_keeps_bs_newer_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client B competes through ``POST /api/v1/civ/transaction``.

    The raw transaction reaches the wire through the direct dispatch
    seam without entering the commander queue, so it lands while A's
    LAST preservation read is still held (the reply to the POST
    returning is the deterministic proof the frame was sent and ACKed)
    and marks A's interference scope like any queued frame: A's stale
    SET is refused before the wire, the retry re-reads, and B's
    DATA/filter survive (MOR-3743).
    """
    async with _Arena(tmp_path) as arena:
        _pause_acquisition(arena)
        await _drain_in_flight_polls(arena)
        addr = arena.stack.radio.profile.civ_addr
        _seed_unselected(arena.sim, Mode.USB.value, 0x00, 0x03)
        a = await arena.tcp()
        n = await _control_write(arena, a)
        start = len(arena.sim.civ_frames_received)
        hold = _SplitModeFrameHold(monkeypatch, arena.sim, ordinals=(n,))
        reply_a = asyncio.create_task(arena.tcp_line(a, "X RTTY 0"))
        try:
            await hold.wait_read(n)
            try:
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
            except asyncio.TimeoutError:
                raise AssertionError(
                    f"the HTTP transaction never completed; {_frames_debug(arena.sim)}"
                ) from None
            assert (status, body.get("ok"), body.get("status")) == (
                200,
                True,
                "ack",
            ), f"body={body}; {_frames_debug(arena.sim)}"
            hold.release(n)
            assert await _await_line(reply_a, arena.sim, "A's write") == "RPRT 0\n", (
                _frames_debug(arena.sim)
            )
        finally:
            hold.release_all()
            await _settle(reply_a)
        assert _unselected_state(arena.sim) == (
            Mode.RTTY.value,
            0x01,
            0x01,
        ), _frames_debug(arena.sim)
        a_set = _set_frame(addr, Mode.RTTY.value, 0x01, 0x01)
        get = _get_frame(addr)
        b_set = _set_frame(addr, Mode.FM.value, 0x01, 0x01)
        window = _split_mode_frames(arena.sim, start)
        assert window.count(a_set) == 1, _frames_debug(arena.sim)
        # The direct-path SET is captured during the hold, so it sits
        # before the held GET's release-time capture: reads 1..N-1, B's
        # SET, the held Nth read, the retry's N reads, A's one SET, the
        # readback.
        assert window == [get] * (n - 1) + [b_set, get] + [get] * n + [
            a_set,
            get,
        ], _frames_debug(arena.sim)


async def test_competitor_during_a_held_set_ack_never_buys_a_second_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A competitor landing while the SET's ACK is pending buys no retry.

    MOR-3743 P1-A regression: the owner's first SET is on the wire with
    its ACK held at the simulator, and a competing HTTP raw CI-V write —
    the direct dispatch seam, invisible to the commander queue — lands
    in that window and marks the scope. When the SET's ACK exchange then
    completes, the SET's currency check fires BEHIND the send: the SET
    may already be applied, so the write must surface the I/O error
    (``RPRT -6``) instead of retrying. Exactly one owner SET reaches the
    wire, and no second SET composed from a re-read can overwrite the
    competitor's values. On the unfixed write path the owner swallowed
    that behind-the-send error and sent a second SET, buying ``RPRT 0``
    with the competitor's DATA/filter.
    """
    async with _Arena(tmp_path) as arena:
        _pause_acquisition(arena)
        await _drain_in_flight_polls(arena)
        addr = arena.stack.radio.profile.civ_addr
        _seed_unselected(arena.sim, Mode.USB.value, 0x00, 0x03)
        a = await arena.tcp()
        n = await _control_write(arena, a)
        start = len(arena.sim.civ_frames_received)
        hold = _SplitModeFrameHold(
            monkeypatch, arena.sim, ordinals=(1,), match=_is_unselected_mode_set
        )
        reply_a = asyncio.create_task(arena.tcp_line(a, "X RTTY 0"))
        competitor: asyncio.Task[tuple[int, dict[str, object]]] | None = None
        try:
            # The owner's first SET arrived; the simulator has not
            # processed it, so its ACK is pending.
            await hold.wait_read(1)
            competitor = asyncio.create_task(
                _http_json(
                    arena.port,
                    "POST",
                    "/api/v1/civ/transaction",
                    {
                        "command": 0x26,
                        "data": "01050101",
                        "expect": "ack",
                        "timeout_ms": 5000,
                    },
                )
            )
            # The competitor's SET on the wire proves the scope was
            # marked at the direct seam. Its ACK settles the owner's
            # ACK waiter first (registration order), so the owner's
            # currency check now fires behind its own send.
            await _await_wire_frame(
                arena.sim,
                _set_frame(addr, Mode.FM.value, 0x01, 0x01),
                "B's competing SET never reached the wire",
            )
            # Releasing processes the owner's parked SET (its ACK is the
            # one the competitor's transaction waits for).
            hold.release(1)
            assert await _await_line(reply_a, arena.sim, "A's write") == "RPRT -6\n", (
                _frames_debug(arena.sim)
            )
            status, body = await asyncio.wait_for(competitor, _EVENT_WAIT_S)
            assert (status, body.get("ok"), body.get("status")) == (
                200,
                True,
                "ack",
            ), f"body={body}; {_frames_debug(arena.sim)}"
        finally:
            hold.release_all()
            await _settle(reply_a)
            if competitor is not None and not competitor.done():
                competitor.cancel()
            if competitor is not None:
                await asyncio.gather(competitor, return_exceptions=True)
        a_set = _set_frame(addr, Mode.RTTY.value, 0x00, 0x03)
        b_set = _set_frame(addr, Mode.FM.value, 0x01, 0x01)
        get = _get_frame(addr)
        window = _split_mode_frames(arena.sim, start)
        # One owner SET — composed from the pre-competitor values — plus
        # the competitor's SET, and nothing after: no retry reads, no
        # second SET, no readback; the write failed behind its applied
        # SET. The owner's SET is captured (and applied) at the release,
        # after the competitor's capture — the hold's artifact, not wire
        # order; the regression signal is the absence of every frame a
        # retry would have added.
        assert window == [get] * n + [b_set, a_set], _frames_debug(arena.sim)


async def test_persistent_interference_fails_the_write_without_a_stale_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interference on every attempt: an honest error, never a stale SET.

    B repeats its write once per attempt — after THAT attempt's LAST
    (Nth) preservation read is held, three attempts in total. Every SET
    is refused before the wire, so A's reply is a rigctld error line —
    not a success bought by overwriting B — and the simulator keeps B's
    last change.
    """
    async with _Arena(tmp_path) as arena:
        _pause_acquisition(arena)
        await _drain_in_flight_polls(arena)
        addr = arena.stack.radio.profile.civ_addr
        _seed_unselected(arena.sim, Mode.USB.value, 0x00, 0x03)
        a, b = await arena.tcp(), await arena.tcp()
        n = await _control_write(arena, a)
        hold_ordinals = (n, 2 * n, 3 * n)
        start = len(arena.sim.civ_frames_received)
        hold = _SplitModeFrameHold(monkeypatch, arena.sim, ordinals=hold_ordinals)
        probe = _CommanderEnqueueProbe(monkeypatch, arena.stack.radio)
        # Three distinct competing writes; the last one must win.
        b_sets = [
            _set_frame(addr, mode, data, filt)
            for mode, data, filt in (
                (Mode.FM.value, 0x01, 0x01),
                (Mode.LSB.value, 0x01, 0x02),
                (Mode.FM.value, 0x00, 0x03),
            )
        ]
        b_lines = [f"w {b_set.hex(' ').upper()}" for b_set in b_sets]
        for payload in b_sets:
            probe.expect(payload)
        reply_a = asyncio.create_task(arena.tcp_line(a, "X RTTY 0"))
        replies_b: list[asyncio.Task[str]] = []
        try:
            for ordinal, payload, line in zip(hold_ordinals, b_sets, b_lines):
                await hold.wait_read(ordinal)
                replies_b.append(asyncio.create_task(arena.tcp_line(b, line)))
                await _await_probe(
                    probe.expect(payload),
                    arena.sim,
                    f"B's SET #{ordinal // n} never entered the commander queue",
                )
                hold.release(ordinal)
            assert await _await_line(reply_a, arena.sim, "A's write") == "RPRT -11\n", (
                _frames_debug(arena.sim)
            )
            for reply_b in replies_b:
                assert (
                    await _await_line(reply_b, arena.sim, "B's write")
                    == f"FE FE E0 {addr:02X} FB FD\n"
                ), _frames_debug(arena.sim)
        finally:
            hold.release_all()
            await _settle(reply_a)
            for reply_b in replies_b:
                await _settle(reply_b)
        get = _get_frame(addr)
        assert _split_mode_frames(arena.sim, start) == [get] * n + [
            b_sets[0],
        ] + [get] * n + [b_sets[1]] + [get] * n + [
            b_sets[2],
        ], _frames_debug(arena.sim)
        # B's last change stands; A's data never reached the wire.
        assert _unselected_state(arena.sim) == (
            Mode.FM.value,
            0x00,
            0x03,
        ), _frames_debug(arena.sim)


async def test_unopposed_split_write_reads_once_and_sets_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: with nobody competing, N reads, one SET, one readback.

    The per-attempt read count N is derived here (and in every scenario)
    by counting the ``26 01`` GETs before the SET — see
    ``_control_write``; with acquisition stood down the count is the
    write's own.
    """
    async with _Arena(tmp_path) as arena:
        _pause_acquisition(arena)
        await _drain_in_flight_polls(arena)
        _seed_unselected(arena.sim, Mode.USB.value, 0x00, 0x03)
        a = await arena.tcp()
        reads_per_attempt = await _control_write(arena, a)
        assert reads_per_attempt >= 1, _frames_debug(arena.sim)


async def test_ptt_off_keeps_plain_dispatch_order_across_a_held_split_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scope owner's write changes nothing about dispatch order or priority.

    This replaces the first version's unmanaged-de-key scenario. That
    wire path is not reachable in this stack: the arena runs under
    managed TX authority, and the HTTP raw CI-V transaction endpoint
    refuses a TX-family frame by payload alone there
    (``web/server.py: _HttpCommandExecutor.execute`` →
    ``TxInterlockRefusal("managed_tx_refused")`` → HTTP 403), because in
    managed mode PTT moves only through the authority. So the claim this
    PR actually makes is pinned instead, at the seam it is made at: an
    ``interference_scope`` neither delays nor reorders anything. A
    FORCE_RELEASE-priority PTT-off queued while the owner's write is
    parked on its held preservation read is dispatched in plain
    (priority, seq) order — immediately after the in-flight exchange
    finishes and before the owner's SET, which is only enqueued later by
    the parked owner. The single wait that remains — the queued frame
    waits for the commander's current exchange — is the pre-existing
    single-exchange serialisation, not something the scope adds. The
    PTT-off marks the scope (detection, never delay), so the owner still
    finishes cleanly on its retry.
    """
    async with _Arena(tmp_path) as arena:
        _pause_acquisition(arena)
        await _drain_in_flight_polls(arena)
        addr = arena.stack.radio.profile.civ_addr
        _seed_unselected(arena.sim, Mode.USB.value, 0x00, 0x03)
        rig = await arena.tcp()
        n = await _control_write(arena, rig)
        start = len(arena.sim.civ_frames_received)
        hold = _SplitModeFrameHold(monkeypatch, arena.sim, ordinals=(n,))
        probe = _CommanderEnqueueProbe(monkeypatch, arena.stack.radio)
        radio = arena.stack.radio
        ptt_off = _ptt_off_frame(addr)
        probe.expect(ptt_off)
        reply_a = asyncio.create_task(
            radio.write_relative_vfo_mode(Mode.RTTY.value, selected=False)
        )
        ptt_off_send: asyncio.Task[object] | None = None
        try:
            await hold.wait_read(n)
            ptt_off_send = asyncio.create_task(
                radio.send_civ(
                    0x1C,
                    sub=0x00,
                    data=b"\x00",
                    priority=Priority.FORCE_RELEASE,
                    wait_response=False,
                )
            )
            await _await_probe(
                probe.expect(ptt_off),
                arena.sim,
                "the PTT-off never entered the commander queue",
            )
            # Pre-existing single-exchange serialisation: the commander
            # is still finishing the held read, so the queued PTT-off has
            # not reached the wire. This is the only wait, and the scope
            # did not add it.
            assert ptt_off not in arena.sim.civ_frames_received, _frames_debug(
                arena.sim
            )
            hold.release(n)
            try:
                await asyncio.wait_for(reply_a, _EVENT_WAIT_S)
            except asyncio.TimeoutError:
                raise AssertionError(
                    f"the owner's write never completed; {_frames_debug(arena.sim)}"
                ) from None
        finally:
            hold.release_all()
            await _settle(reply_a)  # type: ignore[arg-type]
            if ptt_off_send is not None:
                await _settle(ptt_off_send)  # type: ignore[arg-type]
        window = _split_mode_frames(arena.sim, start)
        race = arena.sim.civ_frames_received[start:]
        off_index = race.index(ptt_off)
        get = _get_frame(addr)
        # Exactly the owner's N preservation reads were captured before
        # the PTT-off: A read its preserved values and nothing else.
        assert sum(1 for frame in race[:off_index] if frame == get) == n, _frames_debug(
            arena.sim
        )
        # Dispatched immediately after the in-flight (held) exchange —
        # plain (priority, seq) order, no delay — and before the owner's
        # SET, which the parked owner only enqueues later.
        assert race[off_index - 1] == get, _frames_debug(arena.sim)
        a_set = _set_frame(addr, Mode.RTTY.value, 0x00, 0x03)
        assert race.index(a_set) > off_index, _frames_debug(arena.sim)
        assert window == [get] * (2 * n) + [
            a_set,
            get,
        ], _frames_debug(arena.sim)


def test_unselected_mode_set_matcher_accepts_the_production_ten_byte_set() -> None:
    """The SET hold matcher must match the SET the runtime really sends.

    ``_is_unselected_mode_set`` required a nine-byte CI-V frame, but the
    SET the runtime composes — profile wire tuple ``set_unselected_mode``
    decoded and built through ``write_relative_vfo_mode`` →
    ``build_civ_frame``, then wrapped by ``_civ_rx._wrap_civ`` — is ten
    bytes: the extracted frame includes the terminating ``FD``. With the
    nine-byte pin the held-ACK regression never held its owner SET and
    ``hold.wait_read(1)`` timed out (MOR-3743 round 4). Built here with
    the production builders so the matcher cannot drift from the wire
    again.
    """
    radio = IcomRadio("127.0.0.1", model="IC-7610")
    addr = radio.profile.civ_addr
    cmd_map = radio.profile.command_map
    assert cmd_map is not None
    opcode, sub, prefix = decode_wire_tuple(cmd_map.get("set_unselected_mode"))
    set_frame = build_civ_frame(
        addr,
        CONTROLLER_ADDR,
        opcode,
        sub=sub,
        data=prefix + bytes([Mode.RTTY.value, 0x00, 0x03]),
    )
    assert set_frame == bytes(
        (0xFE, 0xFE, addr, 0xE0, 0x26, 0x01, Mode.RTTY.value, 0x00, 0x03, 0xFD)
    )
    radio._civ_transport = SimpleNamespace(my_id=0x00010001, remote_id=addr)
    wrap = radio._civ_runtime._wrap_civ  # noqa: SLF001
    assert _is_unselected_mode_set(wrap(set_frame), addr)
    # The nine-byte truncation (terminator dropped) and the GET never
    # hold anything.
    assert not _is_unselected_mode_set(wrap(set_frame[:9]), addr)
    assert not _is_unselected_mode_set(wrap(_get_frame(addr)), addr)
