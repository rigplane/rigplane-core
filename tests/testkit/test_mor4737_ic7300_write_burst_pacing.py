"""MOR-4737: split a burst of IC-7300 frequency writes into queue vs answer time.

The journey ``passband-hold`` sees one frequency write confirmed in SECONDS
on the IC-7300 simulator while the real radio confirms in ~45 ms. Two sides
can own that delay, and this file separates them instead of guessing:

* ``test_sim_answers_a_frequency_write_burst_at_profile_latency`` drives the
  simulator alone (no radio stack, no PTY, no scheduler): 30 frequency-set
  frames back to back, one ACK each. The shipped profile
  (``rigplane_testkit/sim/profiles/ic7300.toml``) promises
  ``reply_latency_ms = 30``; the generous per-write bound here (1.0 s, with
  a 0.25 s p95) fails only when the simulator itself serializes or stalls a
  burst. A failure names simulator pacing (lane decision ``a``).
* ``test_write_burst_under_poll_load_reports_queue_vs_answer_split`` runs
  the full stack the journey uses (``Ic7300SerialRadio`` over PTY against
  ``ic7300_profile_fake``, background polls running) and records, per
  write, ``queue_wait`` (enqueue -> first wire byte at the fake) and
  ``answer`` (wire -> the ``send_civ`` future resolving). It prints one
  ``MOR4737_MEASUREMENT`` JSON line with every row and both summaries. Only
  the ``answer`` side is asserted (<= 2.0 s, the tracker's own answer
  window); the queue side is reported, not gated: a slow queue with a fast
  answer is scheduler starvation (lane decision ``b``, owned by MOR-4735),
  and this lane makes no scheduler change.

Run on the Mac mini (never on the laptop)::

    mini-test tests/testkit/test_mor4737_ic7300_write_burst_pacing.py -x -q

or the Core ``focused.yml`` workflow with this file's node ids.
"""

from __future__ import annotations

import asyncio
import json
import math
import time

import pytest

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.commands.commander import BackgroundSendDropped, Priority
from rigplane.core.types import bcd_decode, bcd_encode
from rigplane_testkit.serial.endpoints import PtyEndpoint, pty_supported
from testkit.ic7300_fakes import ic7300_profile_fake

ADDR = 0x94  # rigs/ic7300.toml [identity].expected_ids
BASE_FREQ_HZ = 14_074_000
STEP_HZ = 100

# The simulator contract under test: the profile answers 30 ms after arrival
# (recorded median 30.9 ms). The bounds below are deliberately loose (a loaded
# CI runner still passes) and only catch orders-of-magnitude regressions.
SIM_BURST_N = 30
SIM_PER_WRITE_S = 1.0
SIM_P95_S = 0.25

STACK_BURST_N = 20
STACK_ANSWER_S = 2.0  # the tracker's own answer window (_civ_get_timeout)
QUEUE_STARVATION_HINT_S = 1.0  # report-only: above this, the queue owns it


def _freq_set(freq_hz: int) -> bytes:
    return build_civ_frame(ADDR, CONTROLLER_ADDR, 0x05, data=bcd_encode(freq_hz))


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


async def test_sim_answers_a_frequency_write_burst_at_profile_latency() -> None:
    """Thirty back-to-back frequency writes: every ACK lands at ~30 ms.

    No radio stack, no PTY, no scheduler -- the frames go straight into the
    fake, so ``write`` and ``wire`` coincide and the measured time is purely
    the simulator's answer time. Simultaneous arrivals must cluster at the
    profile latency (see ``test_without_new_keys_the_answers_take_the_reply_
    latency`` in ``test_sim_timing_keys.py``); a simulator that paced a burst
    serially would push the tail ACKs seconds out and fail here (MOR-4737
    decision ``a``).
    """
    fake = ic7300_profile_fake()
    reader, writer = await fake.opener(url="socket://p")
    ack = bytes(build_civ_frame(CONTROLLER_ADDR, ADDR, 0xFB))
    try:
        writes: list[tuple[int, float]] = []
        for index in range(SIM_BURST_N):
            freq = BASE_FREQ_HZ + index * STEP_HZ
            writer.write(_freq_set(freq))
            writes.append((freq, time.monotonic()))
        answers: list[float] = []
        for _ in range(SIM_BURST_N):
            chunk = await asyncio.wait_for(reader.readexactly(len(ack)), 10)
            assert chunk == ack, f"expected the write ACK, got {chunk.hex()}"
            answers.append(time.monotonic())
    finally:
        writer.close()

    per_write = [reply - sent for (_, sent), reply in zip(writes, answers)]
    summary = {
        "n": SIM_BURST_N,
        "profile_reply_latency_ms": 30,
        "max_answer_s": round(max(per_write), 4),
        "p95_answer_s": round(_p95(per_write), 4),
    }
    print("MOR4737_SIM_BURST", json.dumps(summary))
    assert max(per_write) <= SIM_PER_WRITE_S, (
        f"simulator paced a {SIM_BURST_N}-write burst: slowest ACK "
        f"{max(per_write):.2f}s after its write (profile: 30 ms) -- "
        f"MOR-4737 decision (a), fix the testkit pacing"
    )
    assert _p95(per_write) <= SIM_P95_S, (
        f"simulator burst p95 {_p95(per_write):.2f}s, expected ~30 ms"
    )


@pytest.mark.skipif(not pty_supported(), reason="requires a PTY")
async def test_write_burst_under_poll_load_reports_queue_vs_answer_split() -> None:
    """Twenty concurrent writes under background polls: queue vs answer split.

    The journey shape: background acquisition polls keep flowing while a
    burst of NORMAL-priority frequency writes goes through the same
    commander, wire pacer (25 ms min interval) and simulator (30 ms). Per
    write: ``queue_wait`` = enqueue -> frame observed at the fake (commander
    queue + pacing + in-flight predecessor), ``answer`` = wire -> ``send_civ``
    resolving (simulator + RX + tracker). Only ``answer`` is asserted; the
    queue numbers go to the ``MOR4737_MEASUREMENT`` JSON line (and to NOTES.md
    for MOR-4735 when the queue owns the delay).
    """
    fake = ic7300_profile_fake()
    endpoint = PtyEndpoint(fake)
    await endpoint.start()
    radio = Ic7300SerialRadio(
        device=endpoint.device, _enumerate_serial_ports_fn=lambda: []
    )
    wire_at: dict[int, float] = {}
    original_on_wire = fake._on_wire_frame

    def _spy(connection, frame: bytes) -> None:
        try:
            parsed = parse_civ_frame(frame)
            if (
                parsed.command == 0x05
                and parsed.data is not None
                and len(bytes(parsed.data)) == 5
            ):
                wire_at.setdefault(bcd_decode(bytes(parsed.data)), time.monotonic())
        except ValueError:
            pass
        return original_on_wire(connection, frame)

    fake._on_wire_frame = _spy  # type: ignore[method-assign]
    stop = asyncio.Event()
    bg_dropped = 0
    try:
        await asyncio.wait_for(radio.connect(), 30)
        # The connect sequence itself drives CI-V traffic: only burst frames
        # may carry wire stamps, so drop whatever connect left behind.
        wire_at.clear()

        async def _poll() -> None:
            nonlocal bg_dropped
            while not stop.is_set():
                try:
                    await radio.send_civ(
                        0x03,
                        priority=Priority.BACKGROUND,
                        wait_dispatch=False,
                    )
                except BackgroundSendDropped:
                    bg_dropped += 1
                await asyncio.sleep(0.05)

        poll_task = asyncio.create_task(_poll())
        try:

            async def _one(index: int) -> tuple[int, float, float]:
                freq = BASE_FREQ_HZ + index * STEP_HZ
                enqueued = time.monotonic()
                resp = await asyncio.wait_for(
                    radio.send_civ(0x05, data=bcd_encode(freq)), 30
                )
                replied = time.monotonic()
                assert resp is not None and resp.command == 0xFB, (
                    f"write of {freq} Hz was not ACKed: {resp!r}"
                )
                return freq, enqueued, replied

            burst = await asyncio.gather(
                *(_one(index) for index in range(STACK_BURST_N))
            )
        finally:
            stop.set()
            await poll_task

        rows = []
        for freq, enqueued, replied in burst:
            assert freq in wire_at, (
                f"write of {freq} Hz never reached the wire within the burst"
            )
            wired = wire_at[freq]
            rows.append(
                {
                    "freq_hz": freq,
                    "queue_wait_s": round(wired - enqueued, 4),
                    "answer_s": round(replied - wired, 4),
                    "confirm_s": round(replied - enqueued, 4),
                }
            )
        queues = [row["queue_wait_s"] for row in rows]
        answers = [row["answer_s"] for row in rows]
        measurement = {
            "n": STACK_BURST_N,
            "profile_reply_latency_ms": 30,
            "host_min_interval_ms": 25,
            "answer_window_s": 2.0,
            "bg_dropped": bg_dropped,
            "queue_wait_s": {
                "max": round(max(queues), 4),
                "p95": round(_p95(queues), 4),
            },
            "answer_s": {
                "max": round(max(answers), 4),
                "p95": round(_p95(answers), 4),
            },
            "rows": rows,
        }
        slow_queue = _p95(queues) > QUEUE_STARVATION_HINT_S
        slow_answer = max(answers) > STACK_ANSWER_S
        if slow_answer:
            measurement["verdict"] = "sim-pacing-suspect"
        elif slow_queue:
            measurement["verdict"] = "scheduler-starvation-suspect"
        else:
            measurement["verdict"] = "ok"
        print("MOR4737_MEASUREMENT", json.dumps(measurement))

        assert max(answers) <= STACK_ANSWER_S, (
            f"wire -> reply took {max(answers):.2f}s (profile: 30 ms, window: "
            f"2 s) while the queue stayed out of it -- MOR-4737 decision (a), "
            f"the simulator/transport owns the delay"
        )
    finally:
        fake._on_wire_frame = original_on_wire  # type: ignore[method-assign]
        await radio.disconnect()
        await endpoint.stop()
