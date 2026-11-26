"""Opt-in MOR-4846 measurement; copy this file unchanged to the baseline."""

from __future__ import annotations

import asyncio
import importlib
import json
import math
import os
import statistics
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from _helpers import wrap_civ_in_udp
from rigplane.commands import parse_civ_frame
from rigplane.commands.commander import IcomCommander
from rigplane.core.civ import PttReplyLedger
from rigplane.runtime._civ_rx import CIV_HEADER_SIZE
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import ActuationOperation, ActuationResult
from rigplane.runtime.radio import IcomRadio
from rigplane.testing.timing import virtual_clock
from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.icom.faults import FaultPlan, FaultRule
from test_radio import MockTransport

pytestmark = pytest.mark.skipif(
    os.environ.get("MOR4846_PROBE") != "1", reason="set MOR4846_PROBE=1"
)


@pytest.mark.parametrize("profile", ["wifi15", "wifi40"])
@pytest.mark.parametrize("case", ["completed_key", "before_key_ack"])
@virtual_clock
async def test_release_latency_probe(clock, monkeypatch, tmp_path, profile, case):
    _scope_clocks(clock, monkeypatch)
    samples = []
    for cycle in range(50):

        async def scenario():
            async with _probe_rig(clock, tmp_path, profile, cycle) as rig:
                return await _release_cycle(rig, case)

        samples.append(await _drive(clock, scenario()))
    _print_distribution(profile, case, samples)


def _scope_clocks(clock, monkeypatch):
    # Keep sleeps timer-driven: scope_to's eager sleep would skip concurrent
    # link timers. Only module-local time names use its clock view here.
    names = (
        "rigplane.runtime.radio",
        "rigplane.runtime._civ_rx",
        "rigplane.core.civ",
        "rigplane.runtime.managed_tx_effect_lane",
        "rigplane.runtime.managed_tx_authority",
        "rigplane_testkit.icom.simulator",
        "rigplane_testkit.icom.simulator_audio",
    )
    for name in names:
        module = importlib.import_module(name)
        clock.scope_to(monkeypatch, module)
        monkeypatch.setattr(module, "asyncio", asyncio)
    store = importlib.import_module("rigplane.core.state_store")
    radio_module = importlib.import_module("rigplane.runtime.radio")
    monkeypatch.setattr(store, "time", radio_module.time)


async def _drive(clock, scenario):
    task = asyncio.create_task(scenario)
    try:
        await clock.elapse(10)
        assert task.done(), "probe did not finish within its virtual-time budget"
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class _DelayedCivTransport(MockTransport):
    def __init__(self, clock, simulator, profile, cycle):
        super().__init__()
        self.clock = clock
        self.sim = simulator
        low, jitter = {"wifi15": (0.015, 0.025), "wifi40": (0.010, 0.060)}[profile]
        self.plan = FaultPlan(
            seed=484600 + cycle,
            civ_from_client=FaultRule(
                delay_s=low, jitter_s=jitter, preserve_order=True
            ),
            civ_to_client=FaultRule(delay_s=low, jitter_s=jitter, preserve_order=True),
        )
        self.events = []
        self.handles = []
        self.key_sent = asyncio.Event()
        self.key_applied = asyncio.Event()
        self.key_ack_received = asyncio.Event()
        self.drop_key_ack = False

    def _event(self, direction, request_id, frame):
        self.events.append(
            {
                "t": self.clock(),
                "direction": direction,
                "id": request_id,
                "frame": frame.hex(),
            }
        )

    def _schedule(self, direction, packet, callback):
        emissions = self.plan.decide("civ", direction, packet, now=self.clock())
        assert emissions is not None and len(emissions) == 1
        delay, payload = emissions[0]
        self.handles.append(
            asyncio.get_running_loop().call_later(delay, callback, payload)
        )

    async def send_tracked(self, packet, *, is_current=None):
        await super().send_tracked(packet, is_current=is_current)
        request_id = len(self.sent_packets)
        raw = packet[CIV_HEADER_SIZE:]
        parse_civ_frame(raw)  # Fail at the boundary if the LAN wrapper changes.
        self._event("tx", request_id, raw)
        if _ptt_payload(self.events[-1]) == b"\x00\x01":
            self.key_sent.set()
        self._schedule(
            "from_client",
            raw,
            lambda payload: self._at_radio(request_id, payload),
        )

    def _at_radio(self, request_id, raw):
        frame = parse_civ_frame(raw)
        payload = (bytes([frame.sub]) if frame.sub is not None else b"") + frame.data
        reply = self.sim._dispatch_civ(frame.command, payload, frame.from_addr)
        self._event("radio", request_id, raw)
        key = frame.command == 0x1C and payload == b"\x00\x01"
        if key:
            self.key_applied.set()
        if reply is None or (key and self.drop_key_ack):
            return

        def receive(answer):
            self._event("rx", request_id, answer)
            self.queue_response(wrap_civ_in_udp(answer, seq=request_id))
            if key:
                self.key_ack_received.set()

        self._schedule("to_client", reply, receive)

    def close(self):
        for handle in self.handles:
            handle.cancel()


@asynccontextmanager
async def _probe_rig(clock, tmp_path, profile, cycle):
    existing_tasks = asyncio.all_tasks()
    radio = IcomRadio("probe.invalid", model="IC-705")
    sim = MockIcomRadio(
        radio_addr=radio._radio_addr,
        stateful_ptt=True,
        unsolicited_civ=False,
        capture_tx_audio=True,
    )
    wire = _DelayedCivTransport(clock, sim, profile, cycle)
    radio._civ_transport = wire
    radio._connected = True
    # Preserve product pacing, response deadline, and readback settle delay.
    radio._civ_runtime._ptt_ledger = PttReplyLedger(
        clock=clock, answer_window=radio._civ_get_timeout
    )
    radio._commander = IcomCommander(radio._civ_runtime.execute_civ_raw)
    radio._commander.start()
    composition = ManagedTxComposition(
        radio, config_path=tmp_path / f"{profile}-{cycle}.json"
    )
    install_managed_tx_composition(radio, composition)
    try:
        await composition.bind_state_store(radio._state_store)
        await composition.transport_ready(wire)
        assert composition.authority.snapshot_nowait().provider_generation is not None
        yield SimpleNamespace(
            clock=clock,
            radio=radio,
            sim=sim,
            wire=wire,
            composition=composition,
            authority=composition.authority,
        )
        await composition.authority.close()
    finally:
        wire.close()
        # This context runs alone on the decorator's private loop. Join all
        # rig-owned tasks, including nested providers on a failed scenario.
        owned = asyncio.all_tasks() - existing_tasks - {asyncio.current_task()}
        for task in owned:
            task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        radio._connected = False
        radio._commander = None
        radio._civ_transport = None


async def _release_cycle(rig, case):
    key = await rig.authority.submit_ptt(True, "probe")
    key_wait = asyncio.create_task(key.wait_settlement())
    try:
        await rig.wire.key_applied.wait()
        if case == "completed_key":
            settled_key = await key_wait
            assert settled_key.result is ActuationResult.ACCEPTED
            await rig.wire.key_ack_received.wait()
        else:
            assert not rig.wire.key_ack_received.is_set()
        assert rig.sim.ptt_on, "no simulated TX to release"
        pending_at_release = not key.settlement_done
        start = rig.clock()
        event_start = len(rig.wire.events)
        release = await rig.authority.submit_ptt(False, "probe")
        settled = await release.wait_settlement()
        end = rig.clock()
        assert settled.operation is ActuationOperation.FORCE_RECEIVE
        assert settled.result is ActuationResult.ACCEPTED, settled
        assert not rig.sim.ptt_on, "managed settlement did not leave simulator in RX"
        events = rig.wire.events[event_start:]
        tx = [event for event in events if event["direction"] == "tx"]
        rx = [event for event in events if event["direction"] == "rx"]
        sent_ids = {event["id"] for event in tx}
        received_ids = {event["id"] for event in rx}
        offs = [
            event
            for event in events
            if event["direction"] == "radio" and _ptt_payload(event) == b"\x00\x00"
        ]
        assert offs, "no release frame reached the simulator"
        return {
            "confirmed_rx_ms": round((end - start) * 1000, 6),
            "simulator_rx_ms": round((offs[0]["t"] - start) * 1000, 6),
            "pacing_ms": rig.radio._civ_min_interval * 1000,
            "response_timeout_ms": rig.radio._civ_get_timeout * 1000,
            "readback_settle_ms": rig.radio._PTT_READBACK_SETTLE_S * 1000,
            "key_wait_pending_at_release": pending_at_release,
            "tx_frames": len(tx),
            "rx_frames": len(rx),
            "round_trips": len(sent_ids & received_ids),
            "carry_in_rx_frames": sum(e["id"] not in sent_ids for e in rx),
            "off_writes": sum(_ptt_payload(e) == b"\x00\x00" for e in tx),
            "ptt_reads": sum(_ptt_payload(e) == b"\x00" for e in tx),
            "release_frames": [
                {
                    "direction": e["direction"],
                    "frame": e["frame"],
                    "ms": round((e["t"] - start) * 1000, 6),
                }
                for e in events
                if e["direction"] in ("tx", "rx")
            ],
        }
    finally:
        if not key_wait.done():
            key_wait.cancel()
        await asyncio.gather(key_wait, return_exceptions=True)


def _ptt_payload(event):
    frame = parse_civ_frame(bytes.fromhex(event["frame"]))
    if frame.command != 0x1C:
        return None
    return (bytes([frame.sub]) if frame.sub is not None else b"") + frame.data


def _stats(values):
    ordered = sorted(values)
    return {
        "min": min(values),
        "median": statistics.median(values),
        "p90": ordered[math.ceil(len(ordered) * 0.9) - 1],
        "max": max(values),
    }


def _print_distribution(profile, case, samples):
    print(
        json.dumps(
            {
                "probe": "MOR4846_RELEASE",
                "revision": os.getenv("MOR4846_REVISION", "unset"),
                "profile": profile,
                "case": case,
                "cycles": len(samples),
                "seed_base": 484600,
                "model": "IC-705",
                "one_way_ms": [15, 40] if profile == "wifi15" else [10, 70],
                "p90_method": "nearest_rank",
                "confirmed_rx_ms": _stats([s["confirmed_rx_ms"] for s in samples]),
                "simulator_rx_ms": _stats([s["simulator_rx_ms"] for s in samples]),
                "samples": samples,
            },
            sort_keys=True,
        ),
        flush=True,
    )
