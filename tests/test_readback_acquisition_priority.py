"""MOR-4735: readbacks arriving during a shared drain pass preempt polling."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.commands.commander import IcomCommander, Priority, _MAX_BG_INFLIGHT
from rigplane.core import acquisition_drain, acquisition_scheduler, state_store
from rigplane.core.acquisition_drain import AcquisitionDrain, InFlightLedger
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionQuery,
    AcquisitionRequest,
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
from rigplane.profiles.rig_loader import load_rig
from rigplane.runtime._state_queries import acquisition_query_resolver_for_profile
from rigplane.testing.timing import VirtualClock
from rigplane.types import bcd_decode, bcd_encode
from rigplane_testkit.sim.profiles import load_sim_profile
from rigplane.web.radio_poller import RadioPoller
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
            p: AcquisitionPolicy(
                cadence_seconds=float(i + 1), freshness_ttl_seconds=2.0 * (i + 1)
            )
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
            p: AcquisitionPolicy(
                cadence_seconds=float(i + 1), freshness_ttl_seconds=2.0 * (i + 1)
            )
            for i, p in enumerate(bg)
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


@pytest.mark.parametrize("seat_expiry", [None, True, False])
@pytest.mark.parametrize("fully_sent", [True, False])
@pytest.mark.parametrize("forget_hook", [True, False])
async def test_expiry_does_not_readmit_a_terminal_request_in_the_same_pass(
    seat_expiry: bool | None,
    fully_sent: bool,
    forget_hook: bool,
) -> None:
    sibling = FieldPath.global_("operator_controls", "sibling")
    other = FieldPath.global_("operator_controls", "other")
    scheduler = AcquisitionScheduler(
        profile=RadioAcquisitionProfile(
            provider="icom_civ",
            capabilities=tuple(
                FieldCapability(path=p, polling=True) for p in (_FREQ, sibling, other)
            ),
        )
    )
    scheduler.ensure_fresh(
        (_FREQ, sibling), max_age=10, priority="background", reason="policy-cadence"
    )
    request = scheduler.pending_requests()[0]
    scheduler.ensure_fresh(
        other, max_age=10, priority="background", reason="policy-cadence"
    )
    sent_paths = frozenset(request.paths if fully_sent else (_FREQ,))
    ledger: InFlightLedger = {request.id: (sent_paths, 10.0)}
    reports: list[str] = []
    forgotten: list[str] = []
    sent: list[AcquisitionQuery] = []
    queries = {
        _FREQ: _FREQ_QUERY,
        sibling: AcquisitionQuery(command=0x19),
        other: _METER_QUERY,
    }

    async def send(
        query: AcquisitionQuery,
        priority: AcquisitionPriority,
        acquisition_class: AcquisitionClass,
    ) -> None:
        sent.append(query)

    def failure(
        scheduler: AcquisitionScheduler,
        request: AcquisitionRequest,
        *,
        reason: str,
        failed_paths: tuple[FieldPath, ...] | frozenset[FieldPath],
        now: float,
    ) -> None:
        assert reason == "acquisition_request_timeout"
        assert frozenset(failed_paths) == sent_paths
        reports.append(request.id)

    def expiry(
        scheduler: AcquisitionScheduler,
        request: AcquisitionRequest,
        *,
        sent_paths: frozenset[FieldPath],
        now: float,
    ) -> bool:
        reports.append(request.id)
        return seat_expiry is True

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail(f"unexpected drain failure: {kwargs}")

    executor = IcomCivAcquisitionExecutor(send, resolve_query=queries.get)
    drain = AcquisitionDrain(
        scheduler=lambda: scheduler,
        executor=lambda: executor,
        store=lambda: None,
        in_flight=ledger,
        expired=lambda candidate, *, sent_at, now: (
            candidate.id == request.id and sent_at == 10.0
        ),
        dispatchable=lambda requests: requests,
        report_failure=failure,
        report_executor_missing=unexpected,
        report_executor_error=unexpected,
        report_sent=lambda *args, **kwargs: None,
        report_expiry=expiry if seat_expiry is not None else None,
        on_forget=forgotten.append if forget_hook else None,
    )

    await drain.run_once()

    assert reports == [request.id]
    assert any(pending.id == request.id for pending in scheduler.pending_requests())
    assert _METER_QUERY in sent
    terminal = seat_expiry is not False
    if terminal:
        assert request.id not in ledger
        assert sent == [_METER_QUERY]
        assert forgotten == ([request.id] if forget_hook else [])
        assert not scheduler.claim_is_current(
            request, claimant=drain, provider_generation=0
        )
        await drain.run_once()
        assert len(sent) == 3
        assert set(sent[1:]) == {_FREQ_QUERY, queries[sibling]}
        assert ledger[request.id][0] == frozenset(request.paths)
    else:
        assert ledger[request.id][0] == frozenset(request.paths)
        assert _FREQ_QUERY not in sent
        assert (queries[sibling] in sent) is not fully_sent
        assert forgotten == []


def _incremental_drain(
    scheduler: AcquisitionScheduler,
    executor: IcomCivAcquisitionExecutor,
    ledger: InFlightLedger,
) -> AcquisitionDrain:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail(f"unexpected drain failure: {kwargs}")

    return AcquisitionDrain(
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


@pytest.mark.timeout(10)
@pytest.mark.parametrize("slot", [0.03, 0.01], ids=["simulator", "lan-default"])
async def test_readback_bound_applies_on_wire_across_drain_passes(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch, slot: float
) -> None:
    for module in (acquisition_drain, acquisition_scheduler):
        virtual_clock.scope_to(monkeypatch, module)
    reads = tuple(
        FieldPath.active("main", "freq_mode", f"readback_{i}") for i in range(16)
    )
    background = tuple(
        FieldPath.global_("operator_controls", f"background_{i}") for i in range(3)
    )
    profile = RadioAcquisitionProfile(
        provider="icom_civ",
        capabilities=tuple(
            FieldCapability(path=p, polling=True) for p in (*reads, *background)
        ),
        field_policies={
            p: AcquisitionPolicy(
                cadence_seconds=float(i + 1), freshness_ttl_seconds=2.0 * (i + 1)
            )
            for i, p in enumerate((*reads, *background))
        },
    )
    scheduler = AcquisitionScheduler(profile=profile)
    queries = {
        p: AcquisitionQuery(command=0x03, data=bytes([i])) for i, p in enumerate(reads)
    }
    queries.update(
        {
            p: AcquisitionQuery(command=0x15, data=bytes([i]))
            for i, p in enumerate(background)
        }
    )
    release = asyncio.Event()
    entered = asyncio.Event()
    wire: list[bytes] = []
    timestamps: list[float] = []

    async def execute(payload: bytes, wait_response: bool) -> None:
        assert not wait_response
        if payload == b"gate":
            entered.set()
            await release.wait()
            return
        await asyncio.sleep(slot)
        wire.append(payload)
        timestamps.append(virtual_clock())

    commander = IcomCommander(execute)
    commander.start()
    gate = asyncio.create_task(commander.send(b"gate", wait_response=False))

    async def civ(command: int, **kwargs: Any) -> None:
        await commander.send(
            bytes([command]) + kwargs["data"],
            priority=kwargs["priority"],
            wait_response=False,
            wait_dispatch=kwargs["wait_dispatch"],
        )

    poller = object.__new__(RadioPoller)
    poller._radio_state = None
    poller._civ = civ
    executor = IcomCivAcquisitionExecutor(
        poller._send_one_state_query, resolve_query=queries.get
    )
    drain = _incremental_drain(scheduler, executor, {})
    try:
        await virtual_clock.elapse(0)
        assert entered.is_set()
        for batch in range(2):
            for path in reads[batch * 8 : (batch + 1) * 8]:
                scheduler.ensure_fresh(
                    path, max_age=10, priority="user", reason="post_write"
                )
            for path in background[batch * 2 : (batch + 1) * 2]:
                scheduler.ensure_fresh(
                    path, max_age=10, priority="background", reason="cadence"
                )
            await _finish_pass(drain, virtual_clock, slot)
        assert wire == []
        await commander.send(
            b"write", priority=Priority.NORMAL, wait_response=False, wait_dispatch=False
        )
        await commander.send(
            b"urgent",
            priority=Priority.IMMEDIATE,
            wait_response=False,
            wait_dispatch=False,
        )
        release.set()
        await virtual_clock.elapse(22 * slot)
        await gate
        assert wire[:2] == [b"urgent", b"write"]
        pending_background = len(background)
        streak = 0
        for payload in wire[2:]:
            if payload[0] == 0x15:
                assert streak <= 4
                pending_background -= 1
                streak = 0
            else:
                streak += 1
                if pending_background:
                    assert streak <= 4
        assert pending_background == 0
        assert len(wire) == len(reads) + len(background) + 2
        assert [p for p in wire if p[0] == 0x15] == [bytes([0x15, i]) for i in range(3)]
        assert all(
            b - a == pytest.approx(slot) for a, b in zip(timestamps, timestamps[1:])
        )
        assert commander._queue is not None
        await commander._queue.join()
        assert commander._bg_inflight == 0
    finally:
        release.set()
        await commander.stop()
        await asyncio.gather(gate, return_exceptions=True)


@pytest.mark.timeout(10)
@pytest.mark.parametrize("alias", [False, True], ids=["real-profile", "query-alias"])
async def test_promoted_target_precedes_background_siblings_and_keeps_aliases(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch, alias: bool
) -> None:
    for module in (acquisition_drain, acquisition_scheduler):
        virtual_clock.scope_to(monkeypatch, module)
    profile = load_rig(_ROOT / "rigs/ic7300.toml").to_profile()
    assert profile.state_acquisition is not None
    paths = tuple(
        FieldPath.receiver("main", "operator_controls", name)
        for name in ("af_level", "rf_gain", "squelch")
    )
    scheduler = AcquisitionScheduler(profile=profile.state_acquisition)
    scheduler.ensure_fresh(paths, max_age=10, priority="background", reason="cadence")
    promoted = scheduler.ensure_fresh(
        paths[2],
        max_age=1e-9,
        priority="user",
        reason="post_write",
        require_fresh_dispatch=True,
    ).request
    assert promoted is not None
    assert len(scheduler.pending_requests()) == 1
    resolve = acquisition_query_resolver_for_profile(profile)
    target = resolve(paths[2])
    assert target is not None
    sent: list[AcquisitionQuery] = []

    async def send(
        query: AcquisitionQuery, priority: AcquisitionPriority, klass: AcquisitionClass
    ) -> None:
        sent.append(query)
        await asyncio.sleep(0.03)

    ledger: InFlightLedger = {}
    executor = IcomCivAcquisitionExecutor(
        send,
        resolve_query=lambda p: target if alias and p == paths[1] else resolve(p),
    )
    drain = _incremental_drain(scheduler, executor, ledger)
    await _finish_pass(drain, virtual_clock, 0.03)
    assert sent[0] == target
    assert sent.count(target) == 1
    assert len(sent) == (2 if alias else 3)
    assert ledger[promoted.id][0] == frozenset(paths)


@pytest.mark.timeout(10)
@pytest.mark.parametrize("slot", [0.03, 0.01], ids=["simulator", "lan-default"])
async def test_mid_envelope_backpressure_retains_unsent_path_for_retry(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch, slot: float
) -> None:
    for module in (acquisition_drain, acquisition_scheduler):
        virtual_clock.scope_to(monkeypatch, module)
    paths = tuple(
        FieldPath.receiver("main", "operator_controls", name)
        for name in ("af_level", "squelch")
    )
    scheduler = AcquisitionScheduler(
        profile=RadioAcquisitionProfile(
            provider="icom_civ",
            capabilities=tuple(FieldCapability(path=p, polling=True) for p in paths),
        )
    )
    request = scheduler.ensure_fresh(
        paths, max_age=10, priority="background", reason="cadence"
    ).request
    assert request is not None
    queries = {p: AcquisitionQuery(command=0x14, sub=i) for i, p in enumerate(paths)}
    release = asyncio.Event()
    entered = asyncio.Event()
    wire: list[bytes] = []

    async def execute(payload: bytes, wait_response: bool) -> None:
        if payload == b"gate":
            entered.set()
            await release.wait()
            return
        await asyncio.sleep(slot)
        wire.append(payload)

    commander = IcomCommander(execute)
    commander.start()
    gate = asyncio.create_task(commander.send(b"gate", wait_response=False))

    async def send(
        query: AcquisitionQuery, priority: AcquisitionPriority, klass: AcquisitionClass
    ) -> None:
        assert query.sub is not None
        await commander.send(
            bytes([query.command, query.sub]),
            priority=Priority.BACKGROUND,
            wait_response=False,
            wait_dispatch=False,
        )

    ledger: InFlightLedger = {}
    drain = _incremental_drain(
        scheduler, IcomCivAcquisitionExecutor(send, resolve_query=queries.get), ledger
    )
    try:
        await virtual_clock.elapse(0)
        assert entered.is_set()
        for _ in range(_MAX_BG_INFLIGHT - 1):
            await commander.send(
                b"filler",
                priority=Priority.BACKGROUND,
                wait_response=False,
                wait_dispatch=False,
            )
        await _finish_pass(drain, virtual_clock, slot)
        assert ledger[request.id][0] == frozenset({paths[0]})
        assert scheduler.pending_requests() == (request,)
        assert set(scheduler._dispatch_by_request_id[request.id]) == {paths[0]}
        await _finish_pass(drain, virtual_clock, slot)
        assert ledger[request.id][0] == frozenset({paths[0]})
        release.set()
        await virtual_clock.elapse((_MAX_BG_INFLIGHT + 1) * slot)
        await gate
        await _finish_pass(drain, virtual_clock, slot)
        await virtual_clock.elapse(2 * slot)
        assert ledger[request.id][0] == frozenset(paths)
        assert wire.count(b"\x14\x00") == wire.count(b"\x14\x01") == 1
        assert set(scheduler._dispatch_by_request_id[request.id]) == set(paths)
    finally:
        release.set()
        await commander.stop()
        await asyncio.gather(gate, return_exceptions=True)
