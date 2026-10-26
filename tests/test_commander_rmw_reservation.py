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
from rigplane.commands.commander import IcomCommander
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
        write = asyncio.create_task(
            radio.write_relative_vfo_mode(0x01, selected=False)
        )
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
        if foreign is not None and not foreign.done():
            foreign.cancel()
        commander_stop = commander.stop()
        await commander_stop

    sets = _mode_sets(backend.frames)
    assert [item[-1] for item in sets] == [3, 1]
    assert backend.filter == 1
