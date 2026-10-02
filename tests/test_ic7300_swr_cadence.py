"""SWR client reads must not postpone unread IC-7300 TX-meter siblings."""

from __future__ import annotations

from typing import Any

import pytest

from rigplane.core.acquisition_scheduler import (
    AcquisitionScheduler,
    MeterObservationCoalescer,
    RadioStateModelService,
    StateFreshnessService,
    civ_transport_budget_hz,
)
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import FreshnessClock, FreshnessState
from rigplane.runtime.radio import IcomRadio
from rigplane.types import CivFrame
from rigplane.web.radio_poller import CommandQueue, RadioPoller


@pytest.mark.asyncio
async def test_swr_client_reads_do_not_starve_other_ic7300_tx_meters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FreshnessClock(start=100.0)
    monkeypatch.setattr("time.monotonic", clock.now)
    radio = IcomRadio("192.0.2.1", model="IC-7300")
    store = radio.state_store
    store._freshness_clock = clock
    acquisition = radio.profile.state_acquisition
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
    model = RadioStateModelService(store=store, scheduler=scheduler, clock=clock)
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    swr = FieldPath.global_("meters", "swr")
    ptt = FieldPath.global_("tx_state", "ptt")
    required = {0x11: "power", 0x13: "alc", 0x16: "id"}
    tx_subs = {0x11, 0x12, 0x13, 0x14, 0x16}
    sends: list[tuple[float, int, int | None]] = []
    keyed = False

    async def receive(command: int, sub: int, data: bytes) -> None:
        radio._last_civ_data_received = clock.now()
        await radio._civ_runtime._route_civ_frame(
            CivFrame(0xE0, radio.profile.civ_addr, command, sub, data),
            generation=radio._civ_epoch,
            store_provider_generation=store.provider_generation,
        )

    async def wire(command: int, **kwargs: Any) -> None:
        sub = kwargs.get("sub")
        sends.append((clock.now(), command, sub))
        if command == 0x1C and sub == 0x00:
            await receive(command, sub, bytes([int(keyed)]))
        elif command == 0x15 and sub in tx_subs:
            await receive(command, sub, b"\x00\x50")

    monkeypatch.setattr(poller, "_civ", wire)

    async def client_swr_read() -> None:
        # Use the singleton request made by RigctldHandler._ensure_fresh
        # with RigctldConfig's default 0.2-second cache TTL.
        model.ensure_fresh(
            (swr,),
            max_age=0.2,
            priority="user",
            reason="rigctld.get_level.swr",
        )
        await poller._send_scheduler_requests()

    await receive(0x1C, 0x00, b"\x00")
    service.tick(now=clock.now())
    await poller._send_scheduler_requests()
    # An SWR answer before the first observed PTT=true must not satisfy
    # the unread sibling meters in its shared cadence group.
    await client_swr_read()
    keyed = True
    await receive(0x1C, 0x00, b"\x01")
    sends.clear()

    for step in range(1, 61):
        clock.advance(0.05)
        if step % 10 == 0:
            await client_swr_read()
        service.tick(now=clock.now())
        await poller._send_scheduler_requests()
        assert store.snapshot().field(ptt).freshness is FreshnessState.FRESH

    sent_subs = {sub for _, command, sub in sends if command == 0x15}
    assert required.keys() <= sent_subs, (
        "healthy SWR-only client reads postponed the other TX-meter queries",
        sorted(sub for sub in sent_subs if sub is not None),
    )
    snapshot = store.snapshot()
    for name in required.values():
        field = snapshot.field(FieldPath.global_("meters", name))
        assert field.freshness is FreshnessState.FRESH
        assert field.last_observed_monotonic > 100.0
