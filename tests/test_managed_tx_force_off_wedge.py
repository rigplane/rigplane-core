from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.core.state_store import StateStore
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority, RadioTxOrigin
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    ManagedTxIntentKind,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import attach_managed_tx_composition
from integration.test_tx_under_fault_matrix import (
    _http_json,
    _stand_up_stack,
    _tear_down_stack,
)
from testkit.ic705_fakes import ic705_usb_fake

_PATH = "/api/v1/managed-transmit/command"
_GAPS_MS = (0, 20, 50, 100, 300)
_ON, _OFF = b"\x1c\x00\x01\xfd", b"\x1c\x00\x00\xfd"


async def _until(predicate: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while not predicate():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.05, remaining))
    return True


def _clean(authority):
    state = authority.snapshot_nowait().state
    return (
        state.intent.kind is ManagedTxIntentKind.RX
        and not state.release_required
        and state.pending_effect is None
    )


async def _command(web, operation, result="accepted"):
    status, body = await _http_json(web.port, "POST", _PATH, {"operation": operation})
    assert (status, body) == (
        202,
        {"ok": True, "operation": operation, "result": result},
    )


class _SecondOnGate:
    def __init__(self, radio, lane, monkeypatch, prewire=False):
        self.prewire, self.radio, self.transport = prewire, radio, radio._civ_transport
        self.claim = None
        self.running_at_entry = False
        self.entered, self.submitted, self.release, self.delivered, self.before_wire = (
            asyncio.Event() for _ in range(5)
        )
        self.frames = []
        self.delivery = None
        self.received = self.submissions = 0
        original_send = radio._civ_transport.send_tracked

        async def send_tracked(data, **kwargs):
            frame = data[data.find(b"\xfe\xfe") :]
            second = False
            if frame[4:] == _ON:
                self.submissions += 1
                second = self.submissions == 2
                if second:
                    self.claim = next(
                        (
                            claim
                            for claim in lane._claims.values()
                            if claim.operation is ActuationOperation.TRANSMIT_ON
                            and claim.started
                            and claim.provider is not None
                            and not claim.provider.done()
                        ),
                        None,
                    )
                    self.running_at_entry = self.claim is not None
                    if self.prewire:
                        self.before_wire.set()
                        await self.release.wait()
            await original_send(data, **kwargs)
            if second:
                self.submitted.set()
                await self.release.wait()

        monkeypatch.setattr(radio._civ_transport, "send_tracked", send_tracked)

    def receive(self, frame, deliver):
        ptt = frame[4:] in (_ON, _OFF)
        if ptt:
            self.frames.append((time.monotonic(), "ingress", frame[4:]))
        if frame[4:] == _ON:
            self.received += 1
            if self.received == 2:
                self.entered.set()

                async def finish_delivery():
                    await self.release.wait()
                    deliver()
                    self.frames.append((time.monotonic(), "applied", frame[4:]))
                    self.delivered.set()

                self.delivery = asyncio.create_task(finish_delivery())
                return
        deliver()
        if ptt:
            self.frames.append((time.monotonic(), "applied", frame[4:]))

    async def close(self):
        self.release.set()
        try:
            if self.delivery is not None:
                await asyncio.wait_for(self.delivery, 1)
        finally:
            if self.claim is not None and self.claim.provider is not None:
                await asyncio.wait_for(
                    asyncio.gather(self.claim.provider, return_exceptions=True), 1
                )


def _assert_wire_order(frames, never_wire=False):
    writes = [
        frame
        for _, stage, frame in frames
        if stage == "ingress" and frame in (_ON, _OFF)
    ]
    assert writes and writes[0] == _ON and writes[-1] == _OFF, frames
    if never_wire:
        assert writes.count(_ON) == 1 and set(writes[1:]) == {_OFF}, frames


async def _sequence(web, composition, ptt, off_count, gate, gap_ms, caplog):
    caplog.set_level(logging.DEBUG, logger="rigplane.runtime")
    authority = composition.authority
    try:
        await _command(web, "transmit_on")
        assert await _until(ptt, 1), "first ON did not key"
        await _command(web, "force_off")
        assert await _until(lambda: _clean(authority) and not ptt(), 1)
        await _command(web, "transmit_on")
        if gate.prewire:
            await asyncio.wait_for(gate.before_wire.wait(), 1)
            assert not gate.entered.is_set() and not gate.submitted.is_set()
        else:
            await asyncio.wait_for(gate.entered.wait(), 1)
            await asyncio.wait_for(gate.submitted.wait(), 1)
        await asyncio.sleep(0 if gate.prewire else gap_ms / 1000)
        assert gate.claim is not None and gate.claim.started and gate.running_at_entry
        assert not gate.release.is_set()
        generation = authority.snapshot_nowait().provider_generation
        await _command(web, "force_off")
        pending = authority.snapshot_nowait().state.pending_effect
        generation_after = authority.snapshot_nowait().provider_generation
        gate.release.set()
        if not gate.prewire:
            await asyncio.wait_for(gate.delivered.wait(), 1)
        recovered = await _until(lambda: not ptt() and _clean(authority), 8)
        _assert_wire_order(gate.frames, never_wire=gate.prewire)
        assert not gate.prewire or not (
            gate.submitted.is_set() or gate.entered.is_set()
        )
        assert gate.transport is gate.radio._civ_transport
        before = off_count()
        await _command(web, "force_off")
        executed = await _until(
            lambda: off_count() > before and not ptt() and _clean(authority), 3
        )
        assert executed and generation == generation_after
        assert generation == authority.snapshot_nowait().provider_generation
        assert pending is None or pending.operation is ActuationOperation.FORCE_RECEIVE
        _assert_wire_order(gate.frames)
        return recovered
    except (AssertionError, TimeoutError) as error:
        pytest.fail(
            f"{error}\ngap={gap_ms} PTT={ptt()} state={authority.snapshot_nowait()} "
            f"retry={authority._retry_due} frames={gate.frames}\n{caplog.text}",
            pytrace=False,
        )
    finally:
        await gate.close()


@pytest.mark.parametrize("gap_ms", (*_GAPS_MS, "never-wire"))
async def test_serial_force_off_survives_superseded_on(
    tmp_path, caplog, monkeypatch, gap_ms
):
    fake = ic705_usb_fake()
    device = "civ-serial-fake"
    radio = Ic705SerialRadio(
        device=device,
        baudrate=115200,
        civ_link=SerialCivLink(
            device=device,
            baudrate=115200,
            open_serial_connection=fake.open_serial_connection,
        ),
        _enumerate_serial_ports_fn=lambda: [],
    )
    composition = ManagedTxComposition(radio, config_path=tmp_path / "tx.json")
    install_managed_tx_composition(radio, composition)
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
    web.command_state_store.begin_provider_generation()
    await composition.bind_state_store(web.command_state_store)
    attach_managed_tx_composition(web, composition)
    original_wire = fake._on_wire_frame
    try:
        await radio.connect()
        await web.start()
        gate = _SecondOnGate(
            radio, composition._effect_lane, monkeypatch, gap_ms == "never-wire"
        )
        monkeypatch.setattr(
            fake,
            "_on_wire_frame",
            lambda connection, frame: gate.receive(
                frame, lambda: original_wire(connection, frame)
            ),
        )
        assert await _sequence(
            web,
            composition,
            lambda: fake.ptt,
            lambda: sum(
                (f.command, f.sub, f.data) == (0x1C, 0, b"\x00") for f in fake.commands
            ),
            gate,
            gap_ms,
            caplog,
        ), (gap_ms, gate.frames, caplog.text)
    finally:
        await _finish(
            lambda: _stop_composition(composition),
            lambda: asyncio.wait_for(web.stop(), 5),
            lambda: asyncio.wait_for(radio.disconnect(), 5),
        )


@pytest.mark.mock_integration
@pytest.mark.parametrize("gap_ms", (*_GAPS_MS, "never-wire", "pending", "late-radio"))
async def test_lan_force_off_survives_superseded_on(
    tmp_path, caplog, monkeypatch, gap_ms
):
    stack = await _stand_up_stack(
        tmp_path,
        loss="hold",
        tot_seconds=30,
        unsolicited=False,
        sim_options={"capture_civ_frames": True},
    )
    gate = _SecondOnGate(
        stack.radio, stack.composition._effect_lane, monkeypatch, gap_ms == "never-wire"
    )
    original_handle = stack.sim._handle_civ_data

    def receive(data, addr, sender_id, proto):
        length = int.from_bytes(data[0x11:0x13], "little")
        gate.receive(
            data[0x15 : 0x15 + length],
            lambda: original_handle(data, addr, sender_id, proto),
        )

    monkeypatch.setattr(stack.sim, "_handle_civ_data", receive)
    try:
        if gap_ms in ("pending", "late-radio"):
            await (_offline_rearm if gap_ms == "pending" else _late_radio_tx)(
                stack, monkeypatch
            )
            return
        assert await _sequence(
            stack.web,
            stack.composition,
            lambda: stack.sim.ptt_on,
            lambda: sum(frame[4:] == _OFF for frame in stack.sim.civ_frames_received),
            gate,
            gap_ms,
            caplog,
        ), (gap_ms, gate.frames, caplog.text)
    finally:
        await _finish(
            lambda: _stop_composition(stack.composition),
            lambda: _tear_down_stack(stack),
        )


async def _offline_rearm(stack, monkeypatch):
    composition = stack.composition
    entered, release = asyncio.Event(), asyncio.Event()
    prepare = composition._prepare_provider

    async def hold_rearm(event):
        entered.set()
        await release.wait()
        await prepare(event)

    monkeypatch.setattr(composition, "_prepare_provider", hold_rearm)
    await composition._poison_tx_generation(
        composition.authority.snapshot_nowait().provider_generation
    )
    before = len(stack.sim.ptt_set_writes)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert composition.authority.snapshot_nowait().provider_generation is None
        await _command(stack.web, "force_off", "pending")
        assert composition.authority.snapshot_nowait().state.release_required
    finally:
        release.set()
        await asyncio.wait_for(composition._recovery_task, 2)
    assert _clean(composition.authority)
    assert (
        len(stack.sim.ptt_set_writes) > before
        and stack.sim.ptt_set_writes[-1][1] is False
    )


async def _late_radio_tx(stack, monkeypatch):
    await _command(stack.web, "transmit_on")
    assert await _until(lambda: stack.sim.ptt_on, 1)
    ptt = FieldPath.global_("tx_state", "ptt")

    def observed_on():
        field = stack.web.command_state_store.snapshot().field(ptt)
        return (
            field.value is True
            and field.last_observed_monotonic > stack.composition.authority._keyed_at
        )

    assert await _until(observed_on, 2)
    honour = stack.sim._set_simulated_ptt
    flipped, observed = asyncio.Event(), asyncio.Event()
    probe = stack.composition.radio_tx_since

    def observe(since):
        origin = probe(since)
        if flipped.is_set() and origin is RadioTxOrigin.UNRELEASED:
            observed.set()
        return origin

    monkeypatch.setattr(stack.composition, "radio_tx_since", observe)

    def flip_once(on, *, wire_set):
        honour(on, wire_set=wire_set)
        if wire_set and not on and not flipped.is_set():
            honour(True, wire_set=False)
            flipped.set()

    monkeypatch.setattr(stack.sim, "_set_simulated_ptt", flip_once)
    await _command(stack.web, "force_off")
    await asyncio.wait_for(flipped.wait(), 1)
    await asyncio.wait_for(observed.wait(), 3)
    assert await _until(
        lambda: not stack.sim.ptt_on and _clean(stack.composition.authority), 4
    )


async def _finish(*steps):
    failing, errors = sys.exception() is not None, []
    for step in steps:
        try:
            await step()
        except Exception as error:
            errors.append(error)
    if errors and not failing:
        raise errors[0]


async def _stop_composition(composition):
    termination = asyncio.Event()
    shutdown = asyncio.create_task(composition.shutdown(termination))
    try:
        await asyncio.wait_for(asyncio.shield(shutdown), 0.5)
    except TimeoutError:
        termination.set()
        await asyncio.wait_for(asyncio.shield(shutdown), 3)


class _BlockedActuator:
    def __init__(self, late=False):
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.ptt, self.late = False, late
        self.writes, self.entries = [], []

    async def actuate(self, token, operation, *, is_current):
        if operation is ActuationOperation.TRANSMIT_ON:
            self.ptt = not self.late
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                await self.release.wait()
            if not self.late and not is_current():
                raise ConnectionError("managed TX attempt is stale")
            self.ptt = True
            self.writes.append(("ON", token.provider_generation))
        elif operation is ActuationOperation.FORCE_RECEIVE:
            self.entries.append(is_current())
            assert is_current()
            self.ptt = False
            self.writes.append(("OFF", token.provider_generation))
        return ActuationResult.ACCEPTED


async def test_authority_force_off_uses_current_claim_after_superseded_on(tmp_path):
    actuator, poisoned = _BlockedActuator(), []

    async def poison(generation):
        poisoned.append(generation)

    authority = ManagedTxAuthority(
        ManagedTxEffectLane(actuator, poison_generation=poison),
        ManagedTxTotConfigStore(tmp_path / "tx.json"),
        TxAbortFence(),
        provider_generation=7,
    )
    try:
        await authority.submit_transmit_on()
        await asyncio.wait_for(actuator.started.wait(), 1)
        await authority.submit_force_off()
        await asyncio.sleep(0)
        held = not actuator.entries
        generation = authority.snapshot_nowait().provider_generation
        actuator.release.set()
        recovered = await _until(
            lambda: bool(actuator.writes) and _clean(authority) and not actuator.ptt, 8
        )
        await authority.force_off()
        assert (
            recovered
            and held
            and not poisoned
            and generation == authority.snapshot_nowait().provider_generation == 7
        )
        assert actuator.writes == [("OFF", 7), ("OFF", 7)] and all(actuator.entries)
    finally:
        actuator.release.set()
        termination = asyncio.Event()
        termination.set()
        await asyncio.wait_for(
            authority.shutdown(
                retire_provider=lambda _: asyncio.sleep(0), termination=termination
            ),
            3,
        )


@pytest.mark.parametrize("terminate", [False, True])
async def test_timed_out_on_rearms_same_transport_and_later_force_off(
    tmp_path: Path, terminate: bool
):
    actuator, poisoned = _BlockedActuator(late=True), asyncio.Event()

    async def retire(event):
        if event.provider_generation == 1:
            poisoned.set()

    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / "tx.json", retire_provider=retire
    )
    store, transport = StateStore(), object()
    store.begin_provider_generation()
    await composition.bind_state_store(store)
    await composition.transport_ready(transport)
    authority = composition.authority
    authority._attempt_timeout = 0.05
    try:
        await authority.submit_transmit_on()
        await asyncio.wait_for(actuator.started.wait(), 1)
        pending = authority.snapshot_nowait().state.pending_effect
        assert composition._effect_lane._claims[
            (pending.token, pending.operation)
        ].started
        await asyncio.wait_for(poisoned.wait(), 1)
        await authority.force_off()
        assert authority.snapshot_nowait().state.release_required
        assert await _until(
            lambda: (
                authority.snapshot_nowait().provider_generation == 2
                and authority._retry_due is not None
            ),
            1,
        )
        if terminate:
            termination = asyncio.Event()
            termination.set()
            await composition.shutdown(termination)
            actuator.release.set()
            assert await _until(composition._effect_lane._isolation[1].done, 1)
            assert composition._active_provider is None
            assert not composition._recovery_task or composition._recovery_task.done()
        else:
            actuator.release.set()
            assert await _until(lambda: _clean(authority) and not actuator.ptt, 3)
            assert actuator.writes == [("ON", 1), ("OFF", 2)]
            assert composition._active_provider.transport_identity is transport
            await authority.force_off()
            assert actuator.writes == [("ON", 1), ("OFF", 2), ("OFF", 2)]
    finally:
        actuator.release.set()
        await _stop_composition(composition)
