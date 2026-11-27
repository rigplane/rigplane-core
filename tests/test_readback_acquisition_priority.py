"""MOR-4735: readbacks arriving during a shared drain pass preempt polling."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core import acquisition_drain, acquisition_scheduler, state_store
from rigplane.core.acquisition_drain import AcquisitionDrain, InFlightLedger
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionQuery,
    AcquisitionScheduler,
    IcomCivAcquisitionExecutor,
)
from rigplane.core.civ import CivEvent, CivEventType, CivRequestKey, CivRequestTracker
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_pipeline_contracts import (
    AcquisitionClass,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessState, StateStore
from rigplane.testing.timing import VirtualClock
from rigplane.types import bcd_decode, bcd_encode
from rigplane_testkit.sim.profiles import load_sim_profile
from testkit.ic7300_fakes import ic7300_profile_fake

_ROOT = Path(__file__).parents[1]
_FREQ = FieldPath.active("main", "freq_mode", "freq_hz")
_FREQ_QUERY = AcquisitionQuery(command=0x03)
_METER_QUERY = AcquisitionQuery(command=0x15, sub=0x02)
_SOURCE = SourceMetadata(
    source="command_response", provider="icom_civ", transport="fake"
)


async def _finish_pass(
    drain: AcquisitionDrain, clock: VirtualClock, slot: float
) -> None:
    task = asyncio.create_task(drain.run_once())
    try:
        for _ in range(200):
            await clock.elapse(slot)
            if task.done():
                break
        assert task.done(), "one drain pass did not terminate under continuing writes"
        await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.timeout(10)
@pytest.mark.parametrize(
    "priority", [AcquisitionPriority.COMMAND, AcquisitionPriority.USER]
)
@pytest.mark.parametrize("writes", [1, 12, 100])
async def test_burst_readback_confirms_in_one_slot_at_ic7300_simulator_pace(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    priority: AcquisitionPriority,
    writes: int,
) -> None:
    for module in (acquisition_drain, acquisition_scheduler):
        virtual_clock.scope_to(monkeypatch, module)
    monkeypatch.setattr(state_store, "time", SimpleNamespace(monotonic=virtual_clock))
    sim_profile = load_sim_profile(
        _ROOT / "rigplane_testkit/sim/profiles/ic7300.toml", root=_ROOT
    )
    assert sim_profile.timing.reply_latency_ms is not None
    slot = sim_profile.timing.reply_latency_ms / 1000
    fake = ic7300_profile_fake()
    reader, writer = await fake.open_serial_connection()
    write_reader, write_writer = await fake.open_serial_connection()
    # A synthetic 47-request backlog isolates scheduling from profile cadence.
    # Every slot uses the simulator's actual S-meter reply and profile latency.
    background = tuple(
        FieldPath.global_("operator_controls", f"background_{i}") for i in range(47)
    )
    profile = RadioAcquisitionProfile(
        provider="icom_civ",
        capabilities=tuple(
            FieldCapability(path=p, polling=True) for p in (*background, _FREQ)
        ),
        field_policies={
            p: AcquisitionPolicy(cadence_seconds=float(i + 1))
            for i, p in enumerate(background)
        },
    )
    scheduler = AcquisitionScheduler(profile=profile)
    store = StateStore()
    store.apply(
        Observation(
            path=_FREQ,
            value=fake.frequency_hz,
            source=_SOURCE,
            timestamp_monotonic=virtual_clock(),
            max_age=slot / 2,
        )
    )
    for path in background:
        scheduler.ensure_fresh(
            path, max_age=10, priority="background", reason="policy-cadence"
        )
    queued_at: float | None = None
    confirmed_at: float | None = None
    readback_count = 0
    target = fake.frequency_hz + writes * 100

    async def send(
        query: AcquisitionQuery,
        request_priority: AcquisitionPriority,
        acquisition_class: AcquisitionClass,
    ) -> None:
        nonlocal queued_at, confirmed_at, readback_count
        writer.write(
            build_civ_frame(
                fake.civ_addr,
                CONTROLLER_ADDR,
                query.command,
                sub=query.sub,
                data=query.data,
            )
        )
        reply = parse_civ_frame(await reader.readuntil(b"\xfd"))
        if queued_at is None:
            # The writes complete between read slots. Their ACKs use a second
            # simulated connection; they cannot be mistaken for read replies.
            for index in range(1, writes + 1):
                write_writer.write(
                    build_civ_frame(
                        fake.civ_addr,
                        CONTROLLER_ADDR,
                        0x05,
                        data=bcd_encode(target - (writes - index) * 100),
                    )
                )
                scheduler.ensure_fresh(
                    _FREQ,
                    max_age=1e-9,
                    priority=priority,
                    reason="post_write:set_freq",
                    require_fresh_dispatch=True,
                )
            queued_at = virtual_clock()
            store.mark_stale_due(now=queued_at)
            assert store.snapshot().field(_FREQ).freshness is FreshnessState.STALE
            assert fake.frequency_hz == target
            pending_readbacks = [
                r for r in scheduler.pending_requests() if _FREQ in r.paths
            ]
            assert len(pending_readbacks) == 1
        elif query == _FREQ_QUERY:
            readback_count += 1
            assert reply.command == 0x03
            value = bcd_decode(reply.data)
            assert value == target
            changes = store.apply(
                Observation(
                    path=_FREQ,
                    value=value,
                    source=_SOURCE,
                    timestamp_monotonic=virtual_clock(),
                    max_age=10,
                )
            )
            pending = next(r for r in scheduler.pending_requests() if _FREQ in r.paths)
            assert scheduler.may_credit(pending, observation_timestamp=virtual_clock())
            scheduler.record_acquisition_result(pending, changes)
            confirmed_at = virtual_clock()

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail(f"unexpected drain failure: {kwargs}")

    executor = IcomCivAcquisitionExecutor(
        send, resolve_query=lambda p: _FREQ_QUERY if p == _FREQ else _METER_QUERY
    )
    drain = AcquisitionDrain(
        scheduler=lambda: scheduler,
        executor=lambda: executor,
        store=lambda: store,
        in_flight={},
        expired=lambda request, *, sent_at, now: False,
        dispatchable=lambda requests: requests,
        report_failure=unexpected,
        report_executor_missing=unexpected,
        report_executor_error=unexpected,
        report_sent=lambda *args, **kwargs: None,
    )
    try:
        await _finish_pass(drain, virtual_clock, slot)
        # The old frozen pass needs this second pass to see the readback.
        await _finish_pass(drain, virtual_clock, slot)
        assert queued_at is not None and confirmed_at is not None
        latency = confirmed_at - queued_at
        print(
            "MOR4735_LATENCY",
            json.dumps(
                dict(writes=writes, priority=priority, slot=slot, latency=latency)
            ),
        )
        assert latency <= slot + 1e-6
        assert readback_count == 1
        field = store.snapshot().field(_FREQ)
        assert field is not None and field.value == target
        assert field.freshness is FreshnessState.FRESH
        assert sum(f.command == 0x15 for f in fake.commands) == len(background)
        for _ in range(writes):
            ack = parse_civ_frame(await write_reader.readuntil(b"\xfd"))
            assert ack.command == 0xFB
    finally:
        writer.close()
        write_writer.close()
        await asyncio.gather(writer.wait_closed(), write_writer.wait_closed())


@pytest.mark.timeout(10)
async def test_readback_preempts_inside_a_multi_query_request_without_losing_aliases(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    for module in (acquisition_drain, acquisition_scheduler):
        virtual_clock.scope_to(monkeypatch, module)
    bg = tuple(
        FieldPath.global_("operator_controls", f"background_{i}") for i in range(3)
    )
    profile = RadioAcquisitionProfile(
        provider="icom_civ",
        capabilities=tuple(FieldCapability(path=p, polling=True) for p in (*bg, _FREQ)),
    )
    scheduler = AcquisitionScheduler(profile=profile)
    scheduler.ensure_fresh(
        bg, max_age=10, priority="background", reason="policy-cadence"
    )
    queries = {
        bg[0]: _METER_QUERY,
        bg[1]: AcquisitionQuery(command=0x19, sub=0),
        bg[2]: _METER_QUERY,
        _FREQ: _FREQ_QUERY,
    }
    sent: list[AcquisitionQuery] = []
    tracker = CivRequestTracker()

    async def send(
        query: AcquisitionQuery,
        priority: AcquisitionPriority,
        acquisition_class: AcquisitionClass,
    ) -> None:
        sent.append(query)
        key = CivRequestKey(command=query.command, sub=query.sub)
        sink = tracker.register_ack(wait=False, response_key=key, refused_read=key)
        assert isinstance(sink, int)
        tracker.mark_sent(virtual_clock(), token=sink)
        if len(sent) == 1:
            scheduler.ensure_fresh(
                _FREQ,
                max_age=1e-9,
                priority="command",
                reason="post_write:set_freq",
                require_fresh_dispatch=True,
            )
        if query == _FREQ_QUERY:
            # A delayed bare refusal belongs to the background read that
            # actually preceded this promoted readback (MOR-4455).
            await asyncio.sleep(0.5)
            nak = parse_civ_frame(build_civ_frame(CONTROLLER_ADDR, 0x94, 0xFA))
            assert tracker.resolve(
                CivEvent(type=CivEventType.NAK, frame=nak), ordered_ingress=True
            )
            assert tracker.take_refused_reads() == (
                CivRequestKey(command=_METER_QUERY.command, sub=_METER_QUERY.sub),
            )
            assert tracker.ack_sink_count == 1
        await asyncio.sleep(0.03)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail(f"unexpected drain failure: {kwargs}")

    executor = IcomCivAcquisitionExecutor(send, resolve_query=queries.get)
    ledger: InFlightLedger = {}
    drain = AcquisitionDrain(
        scheduler=lambda: scheduler,
        executor=lambda: executor,
        store=lambda: None,
        in_flight=ledger,
        expired=lambda request, *, sent_at, now: False,
        dispatchable=lambda requests: requests,
        report_failure=unexpected,
        report_executor_missing=unexpected,
        report_executor_error=unexpected,
        report_sent=lambda *args, **kwargs: None,
    )
    await _finish_pass(drain, virtual_clock, 0.03)
    assert sent == [_METER_QUERY, _FREQ_QUERY, queries[bg[1]]]
    background_request = next(
        r for r in scheduler.pending_requests() if bg[0] in r.paths
    )
    assert ledger[background_request.id][0] == frozenset(bg)


@pytest.mark.timeout(10)
async def test_continuing_writes_allow_a_background_slot_after_four_readbacks(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    for module in (acquisition_drain, acquisition_scheduler):
        virtual_clock.scope_to(monkeypatch, module)
    bg = tuple(
        FieldPath.global_("operator_controls", f"background_{i}") for i in range(12)
    )
    profile = RadioAcquisitionProfile(
        provider="icom_civ",
        capabilities=tuple(FieldCapability(path=p, polling=True) for p in (*bg, _FREQ)),
        field_policies={
            p: AcquisitionPolicy(cadence_seconds=float(i + 1)) for i, p in enumerate(bg)
        },
    )
    scheduler = AcquisitionScheduler(profile=profile)
    for path in bg:
        scheduler.ensure_fresh(
            path, max_age=10, priority="background", reason="policy-cadence"
        )
    sent: list[AcquisitionQuery] = []

    async def send(
        query: AcquisitionQuery,
        priority: AcquisitionPriority,
        acquisition_class: AcquisitionClass,
    ) -> None:
        sent.append(query)
        scheduler.ensure_fresh(
            _FREQ,
            max_age=1e-9,
            priority="command",
            reason="post_write:set_freq",
            require_fresh_dispatch=True,
        )
        await asyncio.sleep(0.03)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail(f"unexpected drain failure: {kwargs}")

    executor = IcomCivAcquisitionExecutor(
        send, resolve_query=lambda p: _FREQ_QUERY if p == _FREQ else _METER_QUERY
    )
    drain = AcquisitionDrain(
        scheduler=lambda: scheduler,
        executor=lambda: executor,
        store=lambda: None,
        in_flight={},
        expired=lambda request, *, sent_at, now: False,
        dispatchable=lambda requests: requests,
        report_failure=unexpected,
        report_executor_missing=unexpected,
        report_executor_error=unexpected,
        report_sent=lambda *args, **kwargs: None,
    )
    await _finish_pass(drain, virtual_clock, 0.03)
    assert sent[:6] == [_METER_QUERY, *([_FREQ_QUERY] * 4), _METER_QUERY]
    assert len(sent) <= len(bg) + 4
    assert sent.count(_METER_QUERY) >= 3
