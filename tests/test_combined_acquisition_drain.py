"""Cross-seat acquisition-flight contracts for MOR-2292.

Web and rigctld retain separate drains and seat policies.  These tests pin the
shared scheduler as the one atomic owner of request-flight identity when both
seats are attached to the same radio services.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from rigplane.core.acquisition_drain import AcquisitionDrain
from rigplane.core.acquisition_scheduler import (
    AcquisitionExecutionResult,
    AcquisitionPriority,
    AcquisitionRequest,
    AcquisitionScheduler,
    AcquisitionStatus,
)
from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_pipeline_contracts import (
    ChangeSet,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore

_FREQ = FieldPath.active("main", "freq_mode", "freq_hz")
_MODE = FieldPath.active("main", "freq_mode", "mode")
_PTT = FieldPath.global_("tx_state", "ptt")
_SWR = FieldPath.global_("meters", "swr")


def _profile(
    *paths: FieldPath,
    field_policies: dict[FieldPath, AcquisitionPolicy] | None = None,
) -> RadioAcquisitionProfile:
    return RadioAcquisitionProfile(
        provider="icom_civ",
        capabilities=tuple(FieldCapability(path=path, polling=True) for path in paths),
        default_policy=AcquisitionPolicy(),
        field_policies=field_policies or {},
    )


def _queued(
    scheduler: AcquisitionScheduler,
    path: FieldPath = _FREQ,
    *,
    priority: AcquisitionPriority = AcquisitionPriority.USER,
    reason: str = "combined-seat-test",
) -> AcquisitionRequest:
    result = scheduler.ensure_fresh(
        path,
        max_age=5.0,
        priority=priority,
        reason=reason,
    )
    assert result.status is AcquisitionStatus.QUEUED
    assert result.request is not None
    return result.request


def _settlement() -> ChangeSet:
    return ChangeSet(
        revision=0,
        freshness_revision=0,
        observation_seq=0,
        changes=(),
        timestamp_monotonic=10.0,
        sources=(
            SourceMetadata(
                source="poll_response",
                provider="combined_seat_test",
                transport="fake",
            ),
        ),
    )


@dataclass
class _Reports:
    seat: str
    expiries: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    sent: list[tuple[str, tuple[FieldPath, ...]]] = field(default_factory=list)
    forgotten: list[str] = field(default_factory=list)
    propagate_executor_error: bool = False

    def failure(
        self,
        scheduler: AcquisitionScheduler,
        request: AcquisitionRequest,
        *,
        reason: str,
        failed_paths: tuple[FieldPath, ...] | frozenset[FieldPath],
        now: float,
    ) -> None:
        self.failures.append(reason)
        scheduler.record_acquisition_failure(
            request,
            reason=reason,
            failed_paths=failed_paths,
            now=now,
            link_healthy=False,
        )

    def missing(
        self,
        scheduler: AcquisitionScheduler,
        request: AcquisitionRequest,
        *,
        now: float,
    ) -> None:
        self.failure(
            scheduler,
            request,
            reason="acquisition_executor_missing",
            failed_paths=request.paths,
            now=now,
        )

    def error(
        self,
        scheduler: AcquisitionScheduler,
        request: AcquisitionRequest,
        *,
        error: BaseException,
        sent_paths: frozenset[FieldPath],
        now: float,
    ) -> None:
        self.errors.append(str(error))
        scheduler.record_acquisition_failure(
            request,
            reason="acquisition_executor_error",
            failed_paths=tuple(
                path for path in request.paths if path not in sent_paths
            ),
            now=now,
            link_healthy=False,
        )
        if self.propagate_executor_error:
            raise error

    def sent_report(
        self,
        request: AcquisitionRequest,
        *,
        paths: tuple[FieldPath, ...],
        pending_request_count: int,
    ) -> None:
        self.sent.append((request.id, paths))

    def expiry(
        self,
        scheduler: AcquisitionScheduler,
        request: AcquisitionRequest,
        *,
        sent_paths: frozenset[FieldPath],
        now: float,
    ) -> bool:
        self.expiries.append(request.id)
        self.failure(
            scheduler,
            request,
            reason="acquisition_request_timeout",
            failed_paths=sent_paths or frozenset(request.paths),
            now=now,
        )
        return True

    def forget(self, request_id: str) -> None:
        self.forgotten.append(request_id)


class _Executor:
    def __init__(
        self,
        seat: str,
        entries: list[str],
        *,
        release: asyncio.Event | None = None,
        error: BaseException | None = None,
        before_await: Callable[[], None] | None = None,
    ) -> None:
        self.seat = seat
        self.entries = entries
        self.release = release
        self.error = error
        self.before_await = before_await
        self.calls: list[tuple[AcquisitionRequest, frozenset[FieldPath]]] = []

    async def execute(
        self,
        request: AcquisitionRequest,
        *,
        already_sent_paths: frozenset[FieldPath],
    ) -> AcquisitionExecutionResult:
        self.entries.append(self.seat)
        self.calls.append((request, already_sent_paths))
        if self.before_await is not None:
            self.before_await()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return AcquisitionExecutionResult(
            sent_paths=tuple(
                path for path in request.paths if path not in already_sent_paths
            )
        )


def _drain(
    scheduler: AcquisitionScheduler,
    store: StateStore,
    executor: _Executor,
    reports: _Reports,
    *,
    in_flight: dict[str, tuple[frozenset[FieldPath], float]] | None = None,
    expired: Callable[..., bool] | None = None,
) -> AcquisitionDrain:
    return AcquisitionDrain(
        scheduler=lambda: scheduler,
        executor=lambda: executor,
        store=lambda: store,
        in_flight={} if in_flight is None else in_flight,
        expired=(lambda request, *, sent_at, now: False)
        if expired is None
        else expired,
        dispatchable=lambda pending: pending,
        report_failure=reports.failure,
        report_executor_missing=reports.missing,
        report_executor_error=reports.error,
        report_sent=reports.sent_report,
        report_expiry=reports.expiry,
        on_forget=reports.forget,
    )


@pytest.mark.parametrize("winning_seat", ["web", "rigctld"])
async def test_shared_scheduler_allows_one_seat_to_enter_executor_before_release(
    winning_seat: str,
) -> None:
    scheduler = AcquisitionScheduler(profile=_profile(_FREQ))
    request = _queued(scheduler)
    store = StateStore()
    release = asyncio.Event()
    entries: list[str] = []
    loser = "rigctld" if winning_seat == "web" else "web"
    winning_reports = _Reports(winning_seat)
    losing_reports = _Reports(loser)
    winner = _drain(
        scheduler,
        store,
        _Executor(winning_seat, entries, release=release),
        winning_reports,
    )
    losing_executor = _Executor(loser, entries)
    losing = _drain(scheduler, store, losing_executor, losing_reports)

    winning_task = asyncio.create_task(winner.run_once())
    try:
        while not entries:
            await asyncio.sleep(0)
        await losing.run_once()
        assert entries == [winning_seat], "both seats entered one shared request"
        assert losing_executor.calls == []
    finally:
        release.set()
        await winning_task

    scheduler.record_acquisition_result(request, _settlement())
    assert scheduler.pending_requests() == ()
    assert winning_reports.sent == [(request.id, (_FREQ,))]
    assert losing_reports.sent == []


async def test_claim_exists_before_the_executor_reaches_its_first_await() -> None:
    scheduler = AcquisitionScheduler(profile=_profile(_FREQ))
    _queued(scheduler)
    store = StateStore()
    checked: list[bool] = []

    def assert_claimed() -> None:
        assert scheduler.diagnostics()["claimedRequestCount"] == 1
        checked.append(True)

    executor = _Executor("web", [], before_await=assert_claimed)
    await _drain(scheduler, store, executor, _Reports("web")).run_once()
    assert checked == [True]


async def test_independent_schedulers_remain_independent_control() -> None:
    entries: list[str] = []
    for seat in ("web", "rigctld"):
        scheduler = AcquisitionScheduler(profile=_profile(_FREQ))
        _queued(scheduler)
        await _drain(
            scheduler,
            StateStore(),
            _Executor(seat, entries),
            _Reports(seat),
        ).run_once()
    assert entries == ["web", "rigctld"]


async def test_winning_seat_owns_coalesced_path_growth() -> None:
    scheduler = AcquisitionScheduler(profile=_profile(_FREQ, _MODE))
    request = _queued(scheduler, _FREQ)
    store = StateStore()
    entries: list[str] = []
    web_executor = _Executor("web", entries)
    rigctld_executor = _Executor("rigctld", entries)
    web = _drain(scheduler, store, web_executor, _Reports("web"))
    rigctld = _drain(scheduler, store, rigctld_executor, _Reports("rigctld"))

    await web.run_once()
    grown = _queued(scheduler, _MODE, reason="coalesced-mode")
    assert grown.id == request.id
    assert grown.paths == (_FREQ, _MODE)

    await rigctld.run_once()
    await web.run_once()

    assert rigctld_executor.calls == []
    assert [call[1] for call in web_executor.calls] == [
        frozenset(),
        frozenset({_FREQ}),
    ]
    assert entries == ["web", "web"]


async def test_only_winning_seat_applies_expiry_and_forget_policy() -> None:
    scheduler = AcquisitionScheduler(profile=_profile(_FREQ))
    request = _queued(scheduler)
    store = StateStore()
    winner_reports = _Reports("web")
    loser_reports = _Reports("rigctld")
    in_flight: dict[str, tuple[frozenset[FieldPath], float]] = {}
    winner = _drain(
        scheduler,
        store,
        _Executor("web", []),
        winner_reports,
        in_flight=in_flight,
        expired=lambda request, *, sent_at, now: bool(in_flight),
    )
    loser_executor = _Executor("rigctld", [])
    loser = _drain(scheduler, store, loser_executor, loser_reports)

    await winner.run_once()
    await loser.run_once()
    await winner.run_once()

    assert loser_executor.calls == []
    assert winner_reports.expiries == [request.id]
    assert winner_reports.forgotten == [request.id]
    assert loser_reports.expiries == []
    assert scheduler.diagnostics()["claimedRequestCount"] == 0


async def test_cancellation_releases_claim_for_the_other_seat() -> None:
    scheduler = AcquisitionScheduler(profile=_profile(_FREQ))
    _queued(scheduler)
    store = StateStore()
    first = _drain(
        scheduler,
        store,
        _Executor("web", [], error=asyncio.CancelledError()),
        _Reports("web"),
    )
    second_executor = _Executor("rigctld", [])
    second = _drain(scheduler, store, second_executor, _Reports("rigctld"))

    with pytest.raises(asyncio.CancelledError):
        await first.run_once()
    assert scheduler.diagnostics()["claimedRequestCount"] == 0
    await second.run_once()
    assert len(second_executor.calls) == 1


async def test_web_propagated_executor_failure_releases_claim() -> None:
    scheduler = AcquisitionScheduler(profile=_profile(_FREQ))
    _queued(scheduler)
    store = StateStore()
    error = RuntimeError("web send failed")
    web_reports = _Reports("web", propagate_executor_error=True)
    web = _drain(
        scheduler,
        store,
        _Executor("web", [], error=error),
        web_reports,
    )

    with pytest.raises(RuntimeError, match="web send failed") as caught:
        await web.run_once()
    assert caught.value is error
    assert scheduler.diagnostics()["claimedRequestCount"] == 0

    # The failure callback completed this request. A newly queued request must
    # be claimable by the other seat rather than inheriting the dead flight.
    _queued(scheduler, reason="retry-after-web-error")
    rigctld_executor = _Executor("rigctld", [])
    await _drain(
        scheduler,
        store,
        rigctld_executor,
        _Reports("rigctld"),
    ).run_once()
    assert len(rigctld_executor.calls) == 1


async def test_provider_replacement_rejects_old_executor_settlement() -> None:
    scheduler = AcquisitionScheduler(profile=_profile(_FREQ))
    request = _queued(scheduler)
    store = StateStore()
    release = asyncio.Event()
    entries: list[str] = []
    old_reports = _Reports("web")
    new_reports = _Reports("rigctld")
    old = _drain(
        scheduler,
        store,
        _Executor("web", entries, release=release),
        old_reports,
    )
    new = _drain(
        scheduler,
        store,
        _Executor("rigctld", entries),
        new_reports,
    )

    old_task = asyncio.create_task(old.run_once())
    try:
        while not entries:
            await asyncio.sleep(0)
        assert store.begin_provider_generation() == 1
        await new.run_once()
    finally:
        release.set()
        await old_task

    assert entries == ["web", "rigctld"]
    assert old_reports.sent == [], "old-generation executor result settled"
    assert new_reports.sent == [(request.id, (_FREQ,))]


async def test_old_executor_error_cannot_delete_replacement_generation_claim() -> None:
    scheduler = AcquisitionScheduler(profile=_profile(_FREQ))
    request = _queued(scheduler)
    store = StateStore()
    old_release = asyncio.Event()
    replacement_release = asyncio.Event()
    entries: list[str] = []
    old_error = RuntimeError("old Web executor failed")
    old_reports = _Reports("web", propagate_executor_error=True)
    replacement_reports = _Reports("rigctld")
    old = _drain(
        scheduler,
        store,
        _Executor("web", entries, release=old_release, error=old_error),
        old_reports,
    )
    replacement = _drain(
        scheduler,
        store,
        _Executor("rigctld", entries, release=replacement_release),
        replacement_reports,
    )

    old_task = asyncio.create_task(old.run_once())
    while entries != ["web"]:
        await asyncio.sleep(0)
    assert store.begin_provider_generation() == 1
    replacement_task = asyncio.create_task(replacement.run_once())
    while entries != ["web", "rigctld"]:
        await asyncio.sleep(0)

    old_release.set()
    with pytest.raises(RuntimeError, match="old Web executor failed") as caught:
        await old_task
    assert caught.value is old_error
    assert scheduler.pending_requests() == (request,)
    assert scheduler.diagnostics()["claimedRequestCount"] == 1

    intruder = _Executor("extra", entries)
    await _drain(
        scheduler,
        store,
        intruder,
        _Reports("extra"),
    ).run_once()
    assert intruder.calls == []

    replacement_release.set()
    await replacement_task
    assert replacement_reports.sent == [(request.id, (_FREQ,))]
    scheduler.record_acquisition_result(request, _settlement())


async def test_pending_but_not_dispatchable_request_is_not_claimed() -> None:
    policy = AcquisitionPolicy(tx_only=True)
    scheduler = AcquisitionScheduler(
        profile=_profile(_SWR, field_policies={_SWR: policy})
    )
    _queued(
        scheduler,
        _SWR,
        priority=AcquisitionPriority.RECONCILIATION,
        reason="stale",
    )
    rx_store = StateStore()
    executor = _Executor("web", [])
    await _drain(scheduler, rx_store, executor, _Reports("web")).run_once()

    assert len(scheduler.pending_requests()) == 1
    assert scheduler.dispatchable_requests() == ()
    assert executor.calls == []
    assert scheduler.diagnostics()["claimedRequestCount"] == 0

    tx_store = StateStore()
    tx_store.apply(
        Observation(
            path=_PTT,
            value=True,
            source=SourceMetadata(
                source="poll_response",
                provider="combined_seat_test",
                transport="fake",
            ),
            timestamp_monotonic=1.0,
            max_age=100.0,
        )
    )
    await _drain(scheduler, tx_store, executor, _Reports("rigctld")).run_once()
    assert len(executor.calls) == 1
