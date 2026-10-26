"""Split-mode read-modify-write detects concurrent commands (MOR-3743).

CI-V ``0x26`` split-mode writes that omit DATA or filter
(``DualRxRuntimeMixin.write_relative_vfo_mode``, rigctld ``X``) read the
target VFO and then SET. Those are separate ``IcomCommander`` exchanges.
A second command dispatched in that gap used to overwrite the preserved
fields with stale values. Nothing is deferred or reordered now: the write
runs inside a commander interference scope, any concurrent non-poll
dispatch marks the scope, the SET is refused before the wire, and the
whole write retries from the preservation read.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.commands.commander import IcomCommander, Priority
from rigplane.core.exceptions import CommandError, ConnectionError
from rigplane.core.types import CivFrame
from rigplane.runtime.radio import IcomRadio
from rigplane.transport import IcomTransport

if TYPE_CHECKING:
    from rigplane.commands.commander import InterferenceScope

_MODELS = ("IC-7300", "IC-705", "IC-7610")


def _reply(addr: int, payload: str) -> CivFrame:
    return parse_civ_frame(bytes.fromhex(f"FE FE E0 {addr:02X} {payload} FD"))


class _SplitBackend:
    """CI-V peer for split-mode read-modify-write tests.

    Tracks the unselected VFO's ``(mode, data, filter)`` triple. Relative
    mode reads (``0x26`` with a one-byte selector) report the tracked
    state and can run a per-read hook while the read holds the commander
    worker, so a test can queue foreign frames inside the gap the write
    must survive. Honors the wire-seam ``is_current`` contract exactly as
    ``CivRuntime._execute_civ_raw`` does: a stale frame raises before it
    is recorded, so a refused SET never reaches the wire.
    """

    def __init__(self, addr: int) -> None:
        self.addr = addr
        self.frames: list[bytes] = []
        self.mode = 0x01
        self.data = 0x01
        self.filter = 0x03
        self.mode_reads = 0
        # One-shot gate consumed by the first 0x25 read (tests that need
        # the worker busy before the write starts).
        self.freq_read_release: asyncio.Event | None = None
        self.on_mode_read: Callable[[int], Awaitable[None]] | None = None

    async def __call__(
        self,
        payload: bytes,
        wait_response: bool = True,
        is_current: object = None,
    ) -> CivFrame:
        del wait_response
        if is_current is not None and not is_current():
            raise ConnectionError("managed TX attempt is stale")
        self.frames.append(payload)
        frame = parse_civ_frame(payload)
        if frame.command == 0x25 and len(frame.data) == 1:
            gate = self.freq_read_release
            if gate is not None:
                self.freq_read_release = None
                await gate.wait()
            return _reply(self.addr, f"25 {frame.data[0]:02X} 00 40 07 14 00")
        if frame.command == 0x26 and len(frame.data) == 1:
            self.mode_reads += 1
            if self.on_mode_read is not None:
                await self.on_mode_read(self.mode_reads)
            return _reply(
                self.addr,
                f"26 {frame.data[0]:02X} {self.mode:02X} "
                f"{self.data:02X} {self.filter:02X}",
            )
        if frame.command == 0x26 and len(frame.data) > 1:
            self.mode = frame.data[1]
            self.data = frame.data[2]
            self.filter = frame.data[3]
            return _reply(self.addr, "FB")
        return _reply(self.addr, "FB")


def _mode_sets(frames: list[bytes]) -> list[bytes]:
    sets: list[bytes] = []
    for frame in frames:
        parsed = parse_civ_frame(frame)
        if parsed.command == 0x26 and len(parsed.data) > 1:
            sets.append(parsed.data)
    return sets


def _find_wire_frame(
    frames: list[bytes], command: int, sub: int | None, data: bytes
) -> int:
    """Index of the first recorded wire frame with exactly this shape."""
    for index, raw in enumerate(frames):
        parsed = parse_civ_frame(raw)
        if parsed.command == command and parsed.sub == sub and parsed.data == data:
            return index
    raise AssertionError(
        f"no {command:#04x} sub={sub!r} data={data.hex()} frame reached the wire"
    )


def _first_mode_set(frames: list[bytes]) -> int:
    """Index of the first mode SET (0x26 write) among recorded frames."""
    for index, raw in enumerate(frames):
        parsed = parse_civ_frame(raw)
        if parsed.command == 0x26 and len(parsed.data) > 1:
            return index
    raise AssertionError("no mode SET reached the wire")


async def _await_queued(
    entered: asyncio.Event, commander: IcomCommander, n: int
) -> None:
    """``send`` queues before it waits, so the frame is queued once ``entered`` wakes."""
    await entered.wait()
    queue = commander._queue
    assert queue is not None and queue.qsize() == n


@pytest.mark.parametrize("model", _MODELS)
async def test_split_mode_write_rechecks_preservation_after_foreign_write(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """A foreign write dispatched before the SET is refused and retried.

    The owner pauses inside its first preservation read while a foreign
    NORMAL mode write queues behind it. Queue order runs the foreign
    write first, which marks the scope; the owner's stale SET is refused
    before the wire and the retry composes from the foreign values, so
    the final state keeps both changes (MOR-3743).
    """
    radio = IcomRadio("127.0.0.1", model=model)
    backend = _SplitBackend(radio.profile.civ_addr)
    commander = IcomCommander(backend)
    radio._commander = commander
    radio._connected = True
    radio._civ_transport = object()  # type: ignore[assignment]
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    commander.start()
    foreign: asyncio.Task[None] | None = None
    try:

        async def on_mode_read(index: int) -> None:
            nonlocal foreign
            if index != 1:
                return
            entered = asyncio.Event()

            async def competing() -> None:
                entered.set()
                # Selector 01, FM, DATA on, filter 1 — the newer write.
                await radio.send_civ(0x26, data=bytes([0x01, 0x05, 0x01, 0x01]))

            foreign = asyncio.create_task(competing())
            await asyncio.wait_for(_await_queued(entered, commander, 1), 1)

        backend.on_mode_read = on_mode_read
        write = asyncio.create_task(radio.write_relative_vfo_mode(0x01, selected=False))
        observed = await asyncio.wait_for(write, 1)
    finally:
        radio._connected = False
        if foreign is not None:
            if not foreign.done():
                foreign.cancel()
            await asyncio.gather(foreign, return_exceptions=True)
        await commander.stop()

    # Exactly two SETs: the foreign write, then the owner's retry. The
    # owner's first (stale) SET never reached the wire.
    assert _mode_sets(backend.frames) == [
        bytes([0x01, 0x05, 0x01, 0x01]),
        bytes([0x01, 0x01, 0x01, 0x01]),
    ]
    # Final state: the foreign filter survives, the owner's mode wins.
    assert (backend.mode, backend.data, backend.filter) == (0x01, 0x01, 0x01)
    assert observed.data_mode == 1 and observed.filter_num == 1
    # One preservation read per attempt plus the readback.
    assert backend.mode_reads == 3


@pytest.mark.parametrize("model", _MODELS)
async def test_split_mode_write_fails_after_persistent_interference(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """Three interfered attempts fail the write without a stale SET."""
    radio = IcomRadio("127.0.0.1", model=model)
    backend = _SplitBackend(radio.profile.civ_addr)
    commander = IcomCommander(backend)
    radio._commander = commander
    radio._connected = True
    radio._civ_transport = object()  # type: ignore[assignment]
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    commander.start()
    foreign_rounds: list[asyncio.Task[None]] = []
    try:

        async def on_mode_read(index: int) -> None:
            del index
            entered = asyncio.Event()

            async def competing() -> None:
                entered.set()
                # FM distinguishes the foreign SETs from the owner's USB.
                await radio.send_civ(0x26, data=bytes([0x01, 0x05, 0x00, 0x02]))

            foreign_rounds.append(asyncio.create_task(competing()))
            await asyncio.wait_for(_await_queued(entered, commander, 1), 1)

        backend.on_mode_read = on_mode_read
        write = asyncio.create_task(radio.write_relative_vfo_mode(0x01, selected=False))
        with pytest.raises(CommandError, match="interfered with a concurrent command"):
            await asyncio.wait_for(write, 1)
    finally:
        radio._connected = False
        await asyncio.gather(*foreign_rounds, return_exceptions=True)
        await commander.stop()

    # Every preservation read was met by a foreign write; no readback ran.
    assert backend.mode_reads == 3
    assert [item for item in _mode_sets(backend.frames) if item[1] == 0x05] == [
        bytes([0x01, 0x05, 0x00, 0x02])
    ] * 3
    # The owner's SET never reached the wire.
    assert [item for item in _mode_sets(backend.frames) if item[1] == 0x01] == []


@pytest.mark.parametrize("model", _MODELS)
async def test_split_mode_write_without_interference_writes_once(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """Without interference the write is one read, one SET, one readback."""
    radio = IcomRadio("127.0.0.1", model=model)
    backend = _SplitBackend(radio.profile.civ_addr)
    commander = IcomCommander(backend)
    radio._commander = commander
    radio._connected = True
    radio._civ_transport = object()  # type: ignore[assignment]
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    commander.start()
    try:
        write = asyncio.create_task(radio.write_relative_vfo_mode(0x01, selected=False))
        observed = await asyncio.wait_for(write, 1)
    finally:
        radio._connected = False
        await commander.stop()

    assert backend.mode_reads == 2
    assert _mode_sets(backend.frames) == [bytes([0x01, 0x01, 0x01, 0x03])]
    assert observed.data_mode == 1 and observed.filter_num == 3


@pytest.mark.parametrize("model", _MODELS)
async def test_background_frames_do_not_interfere_with_split_write(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """A BACKGROUND poll from another task never marks the scope."""
    radio = IcomRadio("127.0.0.1", model=model)
    backend = _SplitBackend(radio.profile.civ_addr)
    commander = IcomCommander(backend)
    radio._commander = commander
    radio._connected = True
    radio._civ_transport = object()  # type: ignore[assignment]
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    commander.start()
    foreign: asyncio.Task[None] | None = None
    try:

        async def on_mode_read(index: int) -> None:
            nonlocal foreign
            if index != 1 or foreign is not None:
                return
            entered = asyncio.Event()

            async def poll() -> None:
                entered.set()
                await radio.send_civ(
                    0x25,
                    data=b"\x00",
                    priority=Priority.BACKGROUND,
                    wait_dispatch=False,
                )

            foreign = asyncio.create_task(poll())
            await asyncio.wait_for(_await_queued(entered, commander, 1), 1)

        backend.on_mode_read = on_mode_read
        write = asyncio.create_task(radio.write_relative_vfo_mode(0x01, selected=False))
        observed = await asyncio.wait_for(write, 1)
        assert foreign is not None
        await asyncio.wait_for(foreign, 1)
        queue = commander._queue
        assert queue is not None
        await asyncio.wait_for(queue.join(), 1)
    finally:
        radio._connected = False
        if foreign is not None and not foreign.done():
            foreign.cancel()
            await asyncio.gather(foreign, return_exceptions=True)
        await commander.stop()

    # The write succeeded on its first attempt: no retry read.
    assert backend.mode_reads == 2
    assert _mode_sets(backend.frames) == [bytes([0x01, 0x01, 0x01, 0x03])]
    assert observed.filter_num == 3


@pytest.mark.parametrize("model", _MODELS)
async def test_ptt_frames_keep_priority_order_during_split_write(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """PTT frames queued mid-write dispatch in (priority, seq) order.

    A raw NORMAL PTT-on queues while the owner is mid-read, then an
    IMMEDIATE ``set_ptt(False)`` queues behind it. Neither is delayed or
    reordered: the IMMEDIATE de-key dispatches first (priority order,
    exactly as origin/main's queue orders them), both mark the scope, and
    the owner's first SET is refused and retried (MOR-3743 round-2 P0).
    """
    radio = IcomRadio("127.0.0.1", model=model)
    backend = _SplitBackend(radio.profile.civ_addr)
    commander = IcomCommander(backend)
    radio._commander = commander
    radio._connected = True
    radio._civ_transport = object()  # type: ignore[assignment]
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    commander.start()
    tasks: list[asyncio.Task[None]] = []
    try:

        async def on_mode_read(index: int) -> None:
            if index != 1:
                return
            key_entered = asyncio.Event()

            async def key_on() -> None:
                key_entered.set()
                await radio.send_civ(0x1C, 0x00, b"\x01", wait_response=False)

            tasks.append(asyncio.create_task(key_on()))
            await asyncio.wait_for(_await_queued(key_entered, commander, 1), 1)
            dekey_entered = asyncio.Event()

            async def dekey() -> None:
                dekey_entered.set()
                await radio.set_ptt(False)

            tasks.append(asyncio.create_task(dekey()))
            await asyncio.wait_for(_await_queued(dekey_entered, commander, 2), 1)

        backend.on_mode_read = on_mode_read
        write = asyncio.create_task(radio.write_relative_vfo_mode(0x01, selected=False))
        await asyncio.wait_for(write, 1)
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
    finally:
        radio._connected = False
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await commander.stop()

    dekey_at = _find_wire_frame(backend.frames, 0x1C, 0x00, b"\x00")
    key_at = _find_wire_frame(backend.frames, 0x1C, 0x00, b"\x01")
    set_at = _first_mode_set(backend.frames)
    assert dekey_at < key_at < set_at
    # Both PTT frames marked the scope: one refused SET, then a clean retry.
    assert backend.mode_reads == 3
    assert _mode_sets(backend.frames) == [bytes([0x01, 0x01, 0x01, 0x03])]


@pytest.mark.parametrize("model", _MODELS)
async def test_ptt_frames_queued_before_the_write_are_not_delayed(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """PTT frames queued before the write keep their queue order.

    With the worker busy on an unrelated read, a NORMAL PTT-on and an
    IMMEDIATE PTT-off queue, then the write starts behind them. Wire
    order is the plain (priority, seq) order of origin/main — the de-key
    first, the key-on behind it, both ahead of the write's SET; nothing
    waits out the write (MOR-3743 round-2 P0).
    """
    radio = IcomRadio("127.0.0.1", model=model)
    backend = _SplitBackend(radio.profile.civ_addr)
    commander = IcomCommander(backend)
    radio._commander = commander
    radio._connected = True
    radio._civ_transport = object()  # type: ignore[assignment]
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    commander.start()
    tasks: list[asyncio.Task[None]] = []
    gate = asyncio.Event()
    backend.freq_read_release = gate
    write: asyncio.Task[object] | None = None
    try:
        helper_entered = asyncio.Event()

        async def helper_read() -> None:
            helper_entered.set()
            await radio.send_civ(0x25, data=b"\x00")

        tasks.append(asyncio.create_task(helper_read()))
        await asyncio.wait_for(helper_entered.wait(), 1)

        key_entered = asyncio.Event()

        async def key_on() -> None:
            key_entered.set()
            await radio.send_civ(0x1C, 0x00, b"\x01", wait_response=False)

        tasks.append(asyncio.create_task(key_on()))
        await asyncio.wait_for(_await_queued(key_entered, commander, 1), 1)
        dekey_entered = asyncio.Event()

        async def dekey() -> None:
            dekey_entered.set()
            await radio.set_ptt(False)

        tasks.append(asyncio.create_task(dekey()))
        await asyncio.wait_for(_await_queued(dekey_entered, commander, 2), 1)

        write = asyncio.create_task(radio.write_relative_vfo_mode(0x01, selected=False))
        gate.set()
        await asyncio.wait_for(write, 1)
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
    finally:
        gate.set()
        radio._connected = False
        if write is not None and not write.done():
            write.cancel()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await commander.stop()

    dekey_at = _find_wire_frame(backend.frames, 0x1C, 0x00, b"\x00")
    key_at = _find_wire_frame(backend.frames, 0x1C, 0x00, b"\x01")
    set_at = _first_mode_set(backend.frames)
    assert dekey_at < key_at < set_at


@pytest.mark.parametrize("model", _MODELS)
async def test_tx_releases_run_unhindered_and_mark_the_scope(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """TX releases dispatch at their own priority and count as interference.

    ``set_ptt(False)``, ``stop_cw_text``, ``set_tuner_status(0)`` and a
    raw de-key queued while the write's preservation read is in flight
    dispatch before the owner's SET without waiting for the write, in
    their natural (priority, seq) order, and they mark the scope so the
    stale SET is refused (MOR-3743).
    """
    radio = IcomRadio("127.0.0.1", model=model)
    backend = _SplitBackend(radio.profile.civ_addr)
    commander = IcomCommander(backend)
    radio._commander = commander
    radio._connected = True
    radio._civ_transport = object()  # type: ignore[assignment]
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    commander.start()
    tasks: list[asyncio.Task[None]] = []
    try:

        async def on_mode_read(index: int) -> None:
            if index != 1:
                return
            queued = 0

            async def spawn(entered: asyncio.Event, call) -> None:
                entered.set()
                await call()

            for call in (
                lambda: radio.set_ptt(False),
                radio.stop_cw_text,
                lambda: radio.set_tuner_status(0),
            ):
                entered = asyncio.Event()
                tasks.append(asyncio.create_task(spawn(entered, call)))
                queued += 1
                await asyncio.wait_for(_await_queued(entered, commander, queued), 1)

            dekey = build_civ_frame(
                radio._radio_addr, CONTROLLER_ADDR, 0x28, sub=0x00, data=b"\x00"
            )
            entered_raw = asyncio.Event()

            async def raw_release() -> None:
                entered_raw.set()
                await radio.send_civ_raw_fire_and_forget(dekey)

            tasks.append(asyncio.create_task(raw_release()))
            queued += 1
            await asyncio.wait_for(_await_queued(entered_raw, commander, queued), 1)

        backend.on_mode_read = on_mode_read
        write = asyncio.create_task(radio.write_relative_vfo_mode(0x01, selected=False))
        await asyncio.wait_for(write, 1)
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
    finally:
        radio._connected = False
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await commander.stop()

    # Natural (priority, seq) order: IMMEDIATE PTT-off and CW stop first
    # (queue order among equals), then the NORMAL tuner-off and raw de-key.
    ptt_at = _find_wire_frame(backend.frames, 0x1C, 0x00, b"\x00")
    cw_at = _find_wire_frame(backend.frames, 0x17, None, b"\xff")
    tuner_at = _find_wire_frame(backend.frames, 0x1C, 0x01, b"\x00")
    dekey_at = _find_wire_frame(backend.frames, 0x28, None, b"\x00\x00")
    set_at = _first_mode_set(backend.frames)
    assert ptt_at < cw_at < tuner_at < dekey_at < set_at
    # The four releases marked the scope: one refused SET, then a retry.
    assert backend.mode_reads == 3
    assert _mode_sets(backend.frames) == [bytes([0x01, 0x01, 0x01, 0x03])]


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


async def test_scope_marks_interference_from_foreign_frames() -> None:
    """Any foreign non-BACKGROUND dispatch marks the active scope."""
    hold = _Hold()
    commander = IcomCommander(hold)
    commander.start()
    tasks: list[asyncio.Task[object]] = []
    inside = asyncio.Event()
    scopes: list[InterferenceScope] = []
    try:

        async def owner() -> None:
            async with commander.interference_scope() as scope:
                scopes.append(scope)
                inside.set()
                await asyncio.Event().wait()

        owner_task = asyncio.create_task(owner())
        tasks.append(owner_task)
        await asyncio.wait_for(inside.wait(), 1)

        async def foreign(payload: bytes, priority: Priority) -> None:
            await commander.send(payload, priority=priority)

        for payload, priority in (
            (b"W", Priority.NORMAL),
            (b"I", Priority.IMMEDIATE),
            (b"A", Priority.ABORT),
            (b"F", Priority.FORCE_RELEASE),
        ):
            await asyncio.wait_for(asyncio.create_task(foreign(payload, priority)), 1)
        assert scopes[0].interfered is True
        assert scopes[0].is_current() is False
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await commander.stop()
    assert commander._interference_scopes == set()


async def test_scope_ignores_foreign_background_frames() -> None:
    """A BACKGROUND frame from another task never marks the scope."""
    hold = _Hold()
    commander = IcomCommander(hold)
    commander.start()
    tasks: list[asyncio.Task[object]] = []
    inside = asyncio.Event()
    scopes: list[InterferenceScope] = []
    try:

        async def owner() -> None:
            async with commander.interference_scope() as scope:
                scopes.append(scope)
                inside.set()
                await asyncio.Event().wait()

        owner_task = asyncio.create_task(owner())
        tasks.append(owner_task)
        await asyncio.wait_for(inside.wait(), 1)

        async def poll() -> None:
            await commander.send(b"P", priority=Priority.BACKGROUND)

        await asyncio.wait_for(asyncio.create_task(poll()), 1)
        assert scopes[0].interfered is False
        assert scopes[0].is_current() is True
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await commander.stop()
    assert commander._interference_scopes == set()


async def test_scope_deregisters_on_exit_error_and_cancellation() -> None:
    """Normal exit, an exception and cancellation all deregister the scope."""
    hold = _Hold()
    commander = IcomCommander(hold)
    commander.start()

    async def clean() -> None:
        async with commander.interference_scope():
            pass

    await asyncio.wait_for(clean(), 1)
    assert commander._interference_scopes == set()

    async def boom() -> None:
        async with commander.interference_scope():
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await asyncio.wait_for(boom(), 1)
    assert commander._interference_scopes == set()

    entered = asyncio.Event()
    dead: list[InterferenceScope] = []

    async def parked() -> None:
        async with commander.interference_scope() as scope:
            dead.append(scope)
            entered.set()
            await asyncio.Event().wait()

    parked_task = asyncio.create_task(parked())
    await asyncio.wait_for(entered.wait(), 1)
    assert len(commander._interference_scopes) == 1
    parked_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parked_task
    assert commander._interference_scopes == set()

    # A later foreign frame does not touch the deregistered scope.
    await asyncio.wait_for(commander.send(b"W"), 1)
    assert dead[0].interfered is False
    assert commander._interference_scopes == set()
    await commander.stop()


async def test_concurrent_scopes_mark_each_other() -> None:
    """Two live scopes each see the other's frames as interference."""
    hold = _Hold()
    commander = IcomCommander(hold)
    commander.start()
    entered_a, entered_b = asyncio.Event(), asyncio.Event()
    sent_a, sent_b = asyncio.Event(), asyncio.Event()
    scopes: dict[str, InterferenceScope] = {}

    async def side_a() -> None:
        async with commander.interference_scope() as scope:
            scopes["a"] = scope
            entered_a.set()
            await entered_b.wait()
            await commander.send(b"WA")
            sent_a.set()
            await sent_b.wait()

    async def side_b() -> None:
        async with commander.interference_scope() as scope:
            scopes["b"] = scope
            entered_b.set()
            await entered_a.wait()
            await commander.send(b"WB")
            sent_b.set()
            await sent_a.wait()

    await asyncio.wait_for(asyncio.gather(side_a(), side_b()), 1)
    assert scopes["a"].interfered is True
    assert scopes["b"].interfered is True
    assert commander._interference_scopes == set()
    await commander.stop()


async def test_scope_currency_dies_with_the_scope() -> None:
    """A finished scope is never current again, even without interference.

    The SET's retransmit guard in ``IcomTransport._tx_guards`` outlives the
    scope's ``async with`` block, so a scope that exited normally or by
    cancellation must answer ``is_current()`` False forever: a delayed
    retransmit request for its SET is answered with the idle packet, not
    the stale tuple (MOR-3743).
    """
    hold = _Hold()
    commander = IcomCommander(hold)
    commander.start()
    scopes: list[InterferenceScope] = []

    async def clean() -> None:
        async with commander.interference_scope() as scope:
            scopes.append(scope)
            assert scope.is_current() is True

    await asyncio.wait_for(clean(), 1)
    assert scopes[0].closed is True
    assert scopes[0].interfered is False
    assert scopes[0].is_current() is False

    async def boom() -> None:
        async with commander.interference_scope() as scope:
            scopes.append(scope)
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await asyncio.wait_for(boom(), 1)
    assert scopes[1].closed is True
    assert scopes[1].is_current() is False

    entered = asyncio.Event()

    async def parked() -> None:
        async with commander.interference_scope() as scope:
            scopes.append(scope)
            entered.set()
            await asyncio.Event().wait()

    parked_task = asyncio.create_task(parked())
    await asyncio.wait_for(entered.wait(), 1)
    parked_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parked_task
    assert scopes[2].closed is True
    assert scopes[2].interfered is False
    assert scopes[2].is_current() is False
    await commander.stop()


async def test_note_foreign_dispatch_marks_scopes_like_the_worker() -> None:
    """The direct-dispatch marking rule equals the worker's rule.

    Raw CI-V transactions reach the wire without entering the commander
    queue; ``note_foreign_dispatch`` is the one public seam that applies
    the worker's marking rule to them: a non-BACKGROUND frame from
    another task marks every live scope, the owner's own task is exempt,
    and BACKGROUND never marks (MOR-3743).
    """
    hold = _Hold()
    commander = IcomCommander(hold)
    commander.start()
    inside = asyncio.Event()
    scopes: list[InterferenceScope] = []

    async def owner() -> None:
        async with commander.interference_scope() as scope:
            scopes.append(scope)
            inside.set()
            await asyncio.Event().wait()

    owner_task = asyncio.create_task(owner())
    await asyncio.wait_for(inside.wait(), 1)
    me = asyncio.current_task()
    assert me is not None
    try:
        commander.note_foreign_dispatch(me, Priority.NORMAL)
        assert scopes[0].interfered is True

        scopes[0].interfered = False
        commander.note_foreign_dispatch(owner_task, Priority.NORMAL)
        assert scopes[0].interfered is False

        commander.note_foreign_dispatch(me, Priority.BACKGROUND)
        assert scopes[0].interfered is False
    finally:
        owner_task.cancel()
        await asyncio.gather(owner_task, return_exceptions=True)
        await commander.stop()


class _RetransmitBackend:
    """CI-V peer whose sends go through a real ``IcomTransport``.

    Mirrors ``CivRuntime._execute_civ_raw``'s wire seam: the caller's
    ``is_current`` predicate is enforced before the wire and handed to
    ``IcomTransport.send_tracked``, which stores it in ``_tx_guards`` as
    the retransmit guard for that sequence. Replies are canned relative
    VFO state, so a real ``write_relative_vfo_mode`` drives the SET.
    """

    def __init__(self, addr: int) -> None:
        self.addr = addr
        self.transport = IcomTransport()
        self.transport.my_id = 0x00010001
        self.transport.remote_id = 0xAABBCCDD
        self.wire: list[bytes] = []
        self.transport._raw_send = self.wire.append
        self.mode = 0x01
        self.data = 0x01
        self.filter = 0x03
        self.mode_reads = 0
        self.set_seqs: list[int] = []
        self.on_mode_read: Callable[[int], Awaitable[None]] | None = None

    def _wrap(self, civ_frame: bytes) -> bytes:
        total = 0x15 + len(civ_frame)
        pkt = bytearray(total)
        struct.pack_into("<I", pkt, 0, total)
        struct.pack_into("<H", pkt, 4, 0x00)
        struct.pack_into("<I", pkt, 8, self.transport.my_id)
        struct.pack_into("<I", pkt, 0x0C, self.transport.remote_id)
        pkt[0x10] = 0xC1
        struct.pack_into(">H", pkt, 0x11, len(civ_frame))
        pkt[0x15:] = civ_frame
        return bytes(pkt)

    async def __call__(
        self,
        payload: bytes,
        wait_response: bool = True,
        is_current: object = None,
    ) -> CivFrame:
        del wait_response
        if is_current is not None and not is_current():
            raise ConnectionError("managed TX attempt is stale")
        frame = parse_civ_frame(payload)
        if frame.command == 0x25 and len(frame.data) == 1:
            await self.transport.send_tracked(
                self._wrap(payload), is_current=is_current
            )
            return _reply(self.addr, f"25 {frame.data[0]:02X} 00 40 07 14 00")
        if frame.command == 0x26 and len(frame.data) == 1:
            self.mode_reads += 1
            if self.on_mode_read is not None:
                await self.on_mode_read(self.mode_reads)
            await self.transport.send_tracked(
                self._wrap(payload), is_current=is_current
            )
            return _reply(
                self.addr,
                f"26 {frame.data[0]:02X} {self.mode:02X} "
                f"{self.data:02X} {self.filter:02X}",
            )
        await self.transport.send_tracked(self._wrap(payload), is_current=is_current)
        if frame.command == 0x26 and len(frame.data) > 1:
            self.set_seqs.append((self.transport.send_seq - 1) & 0xFFFF)
            self.mode = frame.data[1]
            self.data = frame.data[2]
            self.filter = frame.data[3]
        return _reply(self.addr, "FB")


def _setup_retransmit_radio(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> tuple[IcomRadio, _RetransmitBackend, IcomCommander]:
    radio = IcomRadio("127.0.0.1", model=model)
    backend = _RetransmitBackend(radio.profile.civ_addr)
    commander = IcomCommander(backend)
    radio._commander = commander
    radio._connected = True
    radio._civ_transport = object()  # type: ignore[assignment]
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    commander.start()
    return radio, backend, commander


def _assert_idle_reply(backend: _RetransmitBackend, seq: int) -> None:
    """The retransmit request for ``seq`` was answered with the idle packet."""
    idle = backend.transport._build_control(ptype=0x00, seq=seq)
    assert backend.wire[-1] == idle
    assert backend.wire[-1] != backend.transport.tx_buffer[seq]


@pytest.mark.parametrize("model", _MODELS)
async def test_retired_scope_set_is_not_retransmitted_after_completion(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """After a normal write the SET's sequence is never resent (MOR-3743).

    The write completes, the scope retires, another client may have
    changed the radio since — a delayed retransmit request for the old
    SET sequence must be answered with the idle packet, not the stale
    SET tuple the scope's predicate was still vouching for.
    """
    radio, backend, commander = _setup_retransmit_radio(monkeypatch, model)
    try:
        write = asyncio.create_task(radio.write_relative_vfo_mode(0x01, selected=False))
        observed = await asyncio.wait_for(write, 1)
    finally:
        radio._connected = False
        await commander.stop()

    assert observed.filter_num == 3
    assert backend.set_seqs, "no mode SET reached the wire"
    seq = backend.set_seqs[0]
    sent_before = len(backend.wire)
    backend.transport._retransmit(seq)
    assert len(backend.wire) == sent_before + 1
    _assert_idle_reply(backend, seq)


@pytest.mark.parametrize("model", _MODELS)
async def test_retired_scope_set_is_not_retransmitted_after_cancellation(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    """A cancelled write's SET sequence is never resent (MOR-3743).

    The owner is parked at its readback with the SET already buffered;
    cancelling it retires the scope, so the retransmit guard must refuse
    the old SET even though no interference ever happened.
    """
    radio, backend, commander = _setup_retransmit_radio(monkeypatch, model)
    parked = asyncio.Event()
    release = asyncio.Event()

    async def on_mode_read(index: int) -> None:
        if index == 2:
            parked.set()
            await release.wait()

    backend.on_mode_read = on_mode_read
    write = asyncio.create_task(radio.write_relative_vfo_mode(0x01, selected=False))
    try:
        await asyncio.wait_for(parked.wait(), 1)
        assert backend.set_seqs, "no mode SET reached the wire"
    finally:
        release.set()
        radio._connected = False
        write.cancel()
        with pytest.raises(asyncio.CancelledError):
            await write
        await commander.stop()

    seq = backend.set_seqs[0]
    sent_before = len(backend.wire)
    backend.transport._retransmit(seq)
    assert len(backend.wire) == sent_before + 1
    _assert_idle_reply(backend, seq)
