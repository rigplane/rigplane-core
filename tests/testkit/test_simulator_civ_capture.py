"""Opt-in inbound CI-V frame capture of MockIcomRadio.

``MockIcomRadio(capture_civ_frames=True)`` appends every well-formed CI-V
frame addressed to it to ``civ_frames_received`` in arrival order — whether
the command is implemented or answered with NAK. The default (False)
records nothing. The trace-replay integration test
(``tests/integration/test_rigctld_trace_replay_simulator.py``) uses the
capture to compare the exact bytes the production stack put on the wire
against the manual-transcribed oracle, without re-implementing the
production command builders.
"""

from __future__ import annotations

import asyncio
import time

from rigplane.radio import IcomRadio
from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect

# Manual-transcribed oracle row, tests/golden/wire_oracle/ic7610.toml
# (operating_freq set 14.074 MHz): the production set_freq builder renders
# exactly these bytes, checked byte for byte by test_profile_wire_oracle.
_FREQ_14074_FRAME = bytes.fromhex("FEFE98E0050040071400FD")


async def _wait_for_frames(
    sim: MockIcomRadio, count: int, *, timeout: float = 5.0
) -> list[bytes]:
    """Poll until the capture holds ``count`` frames, else raise."""
    deadline = time.monotonic() + timeout
    while len(sim.civ_frames_received) < count:
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"capture still holds {len(sim.civ_frames_received)} frames "
                f"after {timeout}s, wanted {count}"
            )
        await asyncio.sleep(0.01)
    return list(sim.civ_frames_received)


async def _connected_radio(sim: MockIcomRadio) -> IcomRadio:
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    with fast_connect():
        await radio.connect()
    assert radio.connected
    return radio


async def test_capture_off_by_default_records_nothing() -> None:
    """A default simulator records no frames even for real traffic."""
    sim = MockIcomRadio()
    assert sim.capture_civ_frames is False
    await sim.start()
    try:
        radio = await _connected_radio(sim)
        try:
            await asyncio.wait_for(radio.get_freq(), timeout=10.0)
            await radio.set_freq(14_074_000)
            assert sim.civ_frames_received == []
        finally:
            await radio.disconnect()
    finally:
        await sim.stop()


async def test_capture_records_set_and_naked_set_frames() -> None:
    """Capture holds the exact implemented SET frame and the NAKed PTT SET."""
    sim = MockIcomRadio(capture_civ_frames=True)
    await sim.start()
    try:
        radio = await _connected_radio(sim)
        try:
            sim.clear_captured_civ_frames()
            await radio.set_freq(14_074_000)
            frames = await _wait_for_frames(sim, 1)
            assert frames[-1] == _FREQ_14074_FRAME

            # 1C 00 01 (PTT on) has no simulator handler and is NAKed —
            # the capture still records what the radio received.
            before = len(sim.civ_frames_received)
            await radio.set_ptt(True)
            frames = await _wait_for_frames(sim, before + 1)
            assert frames[-1][4:-1] == bytes.fromhex("1C0001")

            sim.clear_captured_civ_frames()
            assert sim.civ_frames_received == []
        finally:
            await radio.disconnect()
    finally:
        await sim.stop()
