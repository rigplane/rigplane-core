"""Check IC-7300 TX-meter progress through the existing software pipeline.

Uses the shipped profile, clock, scheduler, Web drain/executor, CI-V decoder,
canonical store and public projector. Only the physical wire send is replaced.
No transport, background service, device or server is started.
"""

from __future__ import annotations

from typing import Any

import pytest

from rigplane.core.acquisition_scheduler import (
    AcquisitionScheduler,
    MeterObservationCoalescer,
    StateFreshnessService,
    civ_transport_budget_hz,
    derive_tx_active,
)
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import FreshnessClock, FreshnessState
from rigplane.runtime.radio import IcomRadio
from rigplane.types import CivFrame
from rigplane.web.radio_poller import CommandQueue, RadioPoller
from rigplane.web.runtime_helpers import (
    build_public_state_payload_from_snapshot,
    snapshot_field_status_inputs,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_order", ["during_execute", "after_execute_reversed"])
async def test_ic7300_tx_meters_recover_through_real_pipeline(
    monkeypatch: pytest.MonkeyPatch, reply_order: str
) -> None:
    clock = FreshnessClock(start=100.0)
    # All production timestamps share the existing controllable monotonic domain.
    monkeypatch.setattr("time.monotonic", clock.now)
    radio = IcomRadio("192.0.2.1", model="IC-7300")
    store = radio._state_store
    store._freshness_clock = clock
    profile = radio._profile
    acquisition = profile.state_acquisition
    assert acquisition is not None
    scheduler = AcquisitionScheduler(
        profile=acquisition,
        clock=clock,
        transport_budget_hz=civ_transport_budget_hz(radio),
    )
    radio._acquisition_scheduler = scheduler
    radio._meter_observation_coalescer = MeterObservationCoalescer()
    service = StateFreshnessService(store=store, scheduler=scheduler, radio=radio)
    radio._state_freshness_service = service
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    assert poller._acquisition_executor is not None
    ptt_path = FieldPath.global_("tx_state", "ptt")
    meter_keys = {"power": "powerMeter", "alc": "alcMeter", "id": "idMeter"}
    paths = {name: FieldPath.global_("meters", name) for name in meter_keys}
    tx_subs = {0x11, 0x12, 0x13, 0x14, 0x16}
    replies: list[tuple[CivFrame, int, int]] = []
    sends: list[tuple[float, int, int | None]] = []
    events: list[tuple[str, dict[str, Any]]] = []
    radio._on_state_change = lambda name, data: events.append((name, data))
    keyed = False
    answer_ptt = True
    answer_meters = True

    def frame(command: int, sub: int, data: bytes) -> CivFrame:
        return CivFrame(0xE0, profile.civ_addr, command, sub, data)

    async def route(item: tuple[CivFrame, int, int]) -> None:
        response, civ_generation, store_generation = item
        # The real RX pump stamps link liveness before routing the parsed frame.
        radio._last_civ_data_received = clock.now()
        await radio._civ_runtime._route_civ_frame(
            response,
            generation=civ_generation,
            store_provider_generation=store_generation,
        )

    async def observe_ptt(value: bool) -> None:
        await route(
            (
                frame(0x1C, 0x00, bytes([int(value)])),
                radio._civ_epoch,
                store.provider_generation,
            )
        )

    async def fake_wire(command: int, **kwargs: Any) -> None:
        sub = kwargs.get("sub")
        sends.append((clock.now(), command, sub))
        response = None
        if command == 0x1C and sub == 0x00 and answer_ptt:
            response = frame(command, sub, bytes([int(keyed)]))
        elif command == 0x15 and sub in tx_subs and answer_meters:
            response = frame(command, sub, b"\x00\x50")
        if response is None:
            return
        item = (response, radio._civ_epoch, store.provider_generation)
        if reply_order == "during_execute":
            await route(item)
        else:
            replies.append(item)

    # Keep RadioPoller's real query envelope, executor and drain; replace wire only.
    monkeypatch.setattr(poller, "_civ", fake_wire)

    def payload() -> dict[str, Any]:
        snapshot = store.snapshot()
        availability, declared = snapshot_field_status_inputs(acquisition, snapshot)
        return build_public_state_payload_from_snapshot(
            snapshot,
            radio=None,
            receiver_count=1,
            availability=availability,
            declared=declared,
        )

    def assert_present(*, fresh: bool) -> None:
        public = payload()
        for name, key in meter_keys.items():
            field = store.snapshot().field(paths[name])
            assert public[key] is not None, (clock.now(), key, public["fieldStatus"][key])
            assert public[key] == field.value
            assert public["fieldStatus"][key]["storePath"] == str(paths[name])
            if fresh:
                assert field.freshness is FreshnessState.FRESH
                assert field.provider_generation == store.provider_generation

    async def pump(steps: int, *, keep_present: bool = False) -> None:
        for _ in range(steps):
            clock.advance(0.05)
            service.tick(now=clock.now())
            await poller._send_query()
            # Reverse the batch to exercise meter-before-PTT and partial credit.
            for item in reversed(replies):
                await route(item)
            replies.clear()
            service.tick(now=clock.now())
            if keep_present:
                assert derive_tx_active(store)
                assert_present(fresh=True)

    # RX: availability discards all five TX-only meters, leaving public NULLs.
    await observe_ptt(False)
    await pump(2)
    assert all(payload()[key] is None for key in meter_keys.values())
    assert not any(command == 0x15 and sub in tx_subs for _, command, sub in sends)

    # First TX acquisition loses its meter answers; PTT continues answering.
    keyed = True
    answer_meters = False
    await observe_ptt(True)
    await pump(1)
    assert derive_tx_active(store)
    assert any(command == 0x15 and sub in tx_subs for _, command, sub in sends)
    assert all(payload()[key] is None for key in meter_keys.values())
    answer_meters = True
    await pump(32)  # 1.6 s includes answer deadline, healthy grace and retry.
    assert_present(fresh=True)
    # Real ingress must credit every member, including responses during execute.
    assert not any(
        set(request.paths) & set(paths.values())
        for request in scheduler.pending_requests()
    )
    await pump(44, keep_present=True)  # More than one meter TTL, with fresh PTT.

    # Missing PTT readbacks close cadence gating, but do not erase meter values.
    answer_ptt = False
    await pump(76)
    assert payload()["ptt"] is True
    assert store.snapshot().field(ptt_path).freshness is FreshnessState.STALE
    assert not derive_tx_active(store)
    assert_present(fresh=False)
    assert all(
        store.snapshot().field(path).freshness is FreshnessState.STALE
        for path in paths.values()
    )
    withheld = {
        request.id
        for request in scheduler.pending_requests()
        if set(request.paths) & set(paths.values())
    }
    assert withheld
    assert not withheld.intersection(
        request.id for request in scheduler.dispatchable_requests()
    )
    before = len(
        [1 for _, command, sub in sends if command == 0x15 and sub in tx_subs]
    )
    await pump(10)
    assert before == len(
        [1 for _, command, sub in sends if command == 0x15 and sub in tx_subs]
    )

    # SAME true PTT renews observation time, retaining semantic state revision.
    before_revision = store.snapshot().state_revision
    renewed_at = clock.now()
    answer_ptt = True
    await observe_ptt(True)
    assert store.snapshot().state_revision == before_revision
    assert derive_tx_active(store)
    await pump(32)
    assert_present(fresh=True)
    assert all(
        store.snapshot().field(path).last_observed_monotonic >= renewed_at
        for path in paths.values()
    )

    # An RX transition really removes values; a quick re-key must acquire anew.
    keyed = False
    await observe_ptt(False)
    service.tick(now=clock.now())
    assert all(payload()[key] is None for key in meter_keys.values())
    keyed = True
    await observe_ptt(True)
    await pump(32)
    assert_present(fresh=True)

    # A current-provider reply may restore data; old-provider replies may not.
    old_civ, old_store = radio._civ_epoch, store.provider_generation
    radio._civ_runtime.advance_generation("mor3116-fake-reset")
    assert all(payload()[key] is None for key in meter_keys.values())
    for sub in (0x11, 0x13, 0x16):
        await route((frame(0x15, sub, b"\x00\x50"), old_civ, old_store))
    assert all(payload()[key] is None for key in meter_keys.values())
    await observe_ptt(True)
    await pump(32)
    assert_present(fresh=True)

    # Fresh SAME samples now wake delivery without manufacturing semantic change.
    before_revision = store.snapshot().state_revision
    events.clear()
    await observe_ptt(True)
    assert store.snapshot().state_revision == before_revision
    assert any(
        name == "state_store_changed" and str(ptt_path) in data["paths"]
        for name, data in events
    )
