from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import pytest

from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.core.state_store import StateStore
from rigplane.runtime.managed_tx_authority import ShutdownResult
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    ManagedTxIntentKind,
)
from rigplane.web.api_contract import RESPONSE_FIELD_CONTRACTS
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
    assert status == 202 and body == dict(ok=True, operation=operation, result=result)


class _SecondOnGate:
    def __init__(self, radio, lane, monkeypatch, prewire=False):
        self.prewire, self.radio, self.transport = prewire, radio, radio._civ_transport
        self.claim = self.delivery = None
        self.running_at_entry = False
        self.entered, self.submitted, self.release, self.delivered, self.before_wire = (
            asyncio.Event() for _ in range(5)
        )
        self.frames = []
        self.received = self.submissions = 0
        original_send = radio._civ_transport.send_tracked

        async def send_tracked(data, **kwargs):
            frame = data[data.find(b"\xfe\xfe") :]
            self.submissions += frame[4:] == _ON
            second = frame[4:] == _ON and self.submissions == 2
            if second:
                self.claim = lane._claims[lane._slots["on"]]
                self.running_at_entry = not self.claim.provider.done()
                if self.prewire:
                    self.before_wire.set()
                    await self.release.wait()
            await original_send(data, **kwargs)
            if second:
                self.submitted.set()
                await self.release.wait()

        monkeypatch.setattr(radio._civ_transport, "send_tracked", send_tracked)

    def receive(self, frame, deliver):
        if frame[4:] in (_ON, _OFF):
            self.frames.append(frame[4:])
        self.received += frame[4:] == _ON
        if frame[4:] == _ON and self.received == 2:
            self.entered.set()

            async def finish_delivery():
                await self.release.wait()
                deliver()
                self.delivered.set()

            self.delivery = asyncio.create_task(finish_delivery())
            return
        deliver()


def _assert_wire_order(writes, never_wire=False):
    assert writes and writes[0] == _ON and writes[-1] == _OFF, writes
    if never_wire:
        assert writes.count(_ON) == 1 and set(writes[1:]) == {_OFF}, writes


async def _sequence(web, composition, ptt, off_count, gate, gap_ms):
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
        gate.release.set()
        if not gate.prewire:
            await asyncio.wait_for(gate.delivered.wait(), 1)
        assert await _until(lambda: not ptt() and _clean(authority), 8)
        _assert_wire_order(gate.frames, never_wire=gate.prewire)
        assert not gate.prewire or not (
            gate.submitted.is_set() or gate.entered.is_set()
        )
        assert gate.transport is gate.radio._civ_transport
        before = off_count()
        await _command(web, "force_off")
        assert await _until(
            lambda: off_count() > before and not ptt() and _clean(authority), 3
        )
        assert generation == authority.snapshot_nowait().provider_generation
        assert pending is None or pending.operation is ActuationOperation.FORCE_RECEIVE
        _assert_wire_order(gate.frames)
    finally:
        gate.release.set()
        try:
            if gate.delivery is not None:
                await asyncio.wait_for(gate.delivery, 1)
        finally:
            if provider := getattr(gate.claim, "provider", None):
                await asyncio.wait_for(
                    asyncio.gather(provider, return_exceptions=True), 1
                )


@pytest.mark.parametrize("gap_ms", (*_GAPS_MS, "never-wire"))
async def test_serial_force_off_survives_superseded_on(tmp_path, monkeypatch, gap_ms):
    fake = ic705_usb_fake()
    port = "civ-serial-fake"
    link = SerialCivLink(
        device=port, baudrate=115200, open_serial_connection=fake.open_serial_connection
    )
    radio = Ic705SerialRadio(
        device=port,
        baudrate=115200,
        civ_link=link,
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
        await _sequence(
            web,
            composition,
            lambda: fake.ptt,
            lambda: sum(
                (f.command, f.sub, f.data) == (0x1C, 0, b"\x00") for f in fake.commands
            ),
            gate,
            gap_ms,
        )
        assert _clean(composition.authority) and not fake.ptt, gate.frames
    finally:
        try:
            await _stop_composition(composition)
        finally:
            try:
                await asyncio.wait_for(web.stop(), 5)
            finally:
                await asyncio.wait_for(radio.disconnect(), 5)


@pytest.mark.mock_integration
@pytest.mark.parametrize("gap_ms", (*_GAPS_MS, "never-wire", "pending"))
async def test_lan_force_off_survives_superseded_on(tmp_path, monkeypatch, gap_ms):
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
        if gap_ms == "pending":
            await _offline_rearm(stack, monkeypatch)
            return
        await _sequence(
            stack.web,
            stack.composition,
            lambda: stack.sim.ptt_on,
            lambda: sum(frame[4:] == _OFF for frame in stack.sim.civ_frames_received),
            gate,
            gap_ms,
        )
        assert _clean(stack.composition.authority) and not stack.sim.ptt_on, gate.frames
    finally:
        try:
            await _stop_composition(stack.composition)
        finally:
            await _tear_down_stack(stack)


async def _offline_rearm(stack, monkeypatch):
    contract = RESPONSE_FIELD_CONTRACTS[_PATH]
    assert contract["result_values"] == ("accepted", "pending", "rejected")
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
    assert len(stack.sim.ptt_set_writes) > before
    assert stack.sim.ptt_set_writes[-1][1] is False


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
        self.writes, self.ptt, self.late = [], False, late

    async def actuate(self, token, operation, *, is_current):
        if operation is ActuationOperation.TRANSMIT_ON:
            self.current = is_current
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
            assert is_current()
            self.ptt = False
            self.writes.append(("OFF", token.provider_generation))
        return ActuationResult.ACCEPTED


async def test_shutdown_drains_after_displaced_on_exceeds_release_deadline(tmp_path):
    actuator = _BlockedActuator(late=True)
    composition = ManagedTxComposition(actuator, config_path=tmp_path / "tx.json")
    store = StateStore()
    store.begin_provider_generation()
    await composition.bind_state_store(store)
    await composition.transport_ready(object())
    authority = composition.authority
    authority._attempt_timeout = 0.2
    termination = asyncio.Event()
    try:
        await authority.submit_transmit_on()
        await asyncio.wait_for(actuator.started.wait(), 1)
        shutdown = asyncio.create_task(composition.shutdown(termination))
        assert await _until(lambda: bool(actuator.writes), 0.1)
        assert actuator.writes == [("OFF", 1)]
        assert not composition._retired_generations
        assert await _until(lambda: 1 in composition._retired_generations, 1)
        assert not actuator.release.is_set() and not shutdown.done()
        assert authority.snapshot_nowait().state.release_required
        assert await _until(
            lambda: authority.snapshot_nowait().provider_generation == 2, 1
        )
        with pytest.raises(RuntimeError, match="shutting down"):
            await authority.submit_transmit_on()
        actuator.release.set()
        result = await asyncio.wait_for(asyncio.shield(shutdown), 3)
        assert result is ShutdownResult.DRAINED
        assert ("ON", 1) in actuator.writes and actuator.writes[-1] == ("OFF", 2)
        assert not actuator.ptt and _clean(authority)
    finally:
        actuator.release.set()
        termination.set()
        await asyncio.wait_for(composition.shutdown(termination), 3)


@pytest.mark.parametrize("mode", ["quick", "late", "timeout", "terminate", "supersede"])
async def test_force_off_attempts_before_and_after_on_isolation(tmp_path, mode):
    late = mode not in ("quick", "supersede")
    actuator, poisoned = _BlockedActuator(late=late), asyncio.Event()
    retired = []

    async def isolate(generation):
        retired.append(generation)
        await composition._poison_tx_generation(generation)
        poisoned.set()

    composition = ManagedTxComposition(actuator, config_path=tmp_path / "tx.json")
    composition._effect_lane._poison_generation = isolate
    store, transport = StateStore(), object()
    store.begin_provider_generation()
    await composition.bind_state_store(store)
    await composition.transport_ready(transport)
    authority = composition.authority
    snapshot = authority.snapshot_nowait
    authority._attempt_timeout = 0.05 if mode in ("timeout", "terminate") else 0.2
    try:
        await authority.submit_transmit_on()
        await asyncio.wait_for(actuator.started.wait(), 1)
        if mode not in ("timeout", "terminate"):
            await authority.submit_force_off()
            assert await _until(lambda: bool(actuator.writes), 0.1)
            assert not actuator.release.is_set() and not actuator.ptt
            assert snapshot().state.release_required
            assert actuator.writes == [("OFF", 1)] and not poisoned.is_set()
        if mode in ("quick", "late"):
            actuator.release.set()
            assert await _until(lambda: _clean(authority) and not actuator.ptt, 8)
            assert snapshot().provider_generation == 1
            assert not composition._retired_generations and not retired
            expected = [("OFF", 1), ("ON", 1), ("OFF", 1)] if late else [("OFF", 1)] * 2
            assert actuator.writes == expected
            return
        await asyncio.wait_for(poisoned.wait(), 1)
        assert await _until(lambda: snapshot().provider_generation == 2, 1)
        await authority.force_off()
        assert snapshot().state.release_required
        assert await _until(lambda: authority._retry_due is not None, 1)
        assert snapshot().provider_generation == 2
        if mode == "terminate":
            termination = asyncio.Event()
            termination.set()
            await composition.shutdown(termination)
            actuator.release.set()
            assert await _until(composition._effect_lane._isolation[1].done, 1)
            assert composition._active_provider is None
            assert not composition._recovery_task or composition._recovery_task.done()
            return
        if mode == "timeout":
            actuator.release.set()
            assert await _until(lambda: _clean(authority) and not actuator.ptt, 3)
            assert actuator.writes[-2:] == [("ON", 1), ("OFF", 2)]
        assert composition._active_provider.transport_identity is transport
        assert not actuator.current()
        before = len(actuator.writes)
        await authority.force_off()
        expected_offs = 2 if mode == "supersede" else 1
        assert actuator.writes[before:] == [("OFF", 2)] * expected_offs
        if mode == "supersede":
            assert retired == [1] and not actuator.release.is_set()
            assert not actuator.ptt and snapshot().state.release_required
            assert snapshot().state.last_actuation.result is ActuationResult.UNCERTAIN
    finally:
        actuator.release.set()
        await _stop_composition(composition)
