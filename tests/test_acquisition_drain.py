"""Tests for src/rigplane/core/acquisition_drain.py (MOR-2293).

Everything that differs between the two dispatching seats reaches the drain
as an injected collaborator, so these tests drive it with stubs and assert
on what those collaborators were handed. The seats' own behaviour is pinned
in ``tests/test_rigctld_server.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import replace
from typing import Any, cast
from unittest.mock import patch

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
    FieldChange,
    FieldPath,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.rigctld.server import RigctldServer
from rigplane.web.radio_poller import RadioPoller

_FREQ = FieldPath.active("main", "freq_mode", "freq_hz")
_MODE = FieldPath.active("main", "freq_mode", "mode")


def _request(
    *,
    request_id: str = "acq-1",
    paths: tuple[FieldPath, ...] = (_FREQ,),
    reasons: tuple[str, ...] = ("policy-cadence",),
    deadline: float = 100.0,
    max_age: float = 1.0,
    timeout: float | None = None,
) -> AcquisitionRequest:
    return AcquisitionRequest(
        id=request_id,
        paths=paths,
        priority=AcquisitionPriority.BACKGROUND,
        reason=reasons[0],
        reasons=reasons,
        requested_at_monotonic=0.0,
        deadline_monotonic=deadline,
        max_age=max_age,
        timeout=timeout,
        provider="icom_civ",
        acquisition_method="poll",
        policy=AcquisitionPolicy(),
        capability_ids=tuple(str(path) for path in paths),
    )


def _never_expired(request: AcquisitionRequest, *, sent_at: float, now: float) -> bool:
    return False


def _always_expired(request: AcquisitionRequest, *, sent_at: float, now: float) -> bool:
    return True


class _StubScheduler:
    def __init__(self, pending: tuple[AcquisitionRequest, ...]) -> None:
        self.pending = pending
        self.tx_active_calls: list[bool] = []
        self.claimant_by_request: dict[str, object] = {}
        self.claim_generation_by_request: dict[str, int] = {}
        self.dispatches: list[tuple[str, tuple[FieldPath, ...], float]] = []
        self.executing: list[str] = []

    def note_execute_started(
        self, request_id: str, *, now: float | None = None
    ) -> None:
        self.executing.append(request_id)

    def note_execute_finished(self, request_id: str) -> None:
        self.executing.remove(request_id)

    def record_dispatch(
        self,
        request_id: str,
        *,
        paths: Iterable[FieldPath],
        now: float,
    ) -> None:
        self.dispatches.append((request_id, tuple(paths), now))

    def note_tx_active(self, tx_active: bool) -> None:
        self.tx_active_calls.append(tx_active)

    def dispatchable_requests(self) -> tuple[AcquisitionRequest, ...]:
        return self.pending

    def try_claim(
        self,
        request: AcquisitionRequest,
        *,
        claimant: object,
        provider_generation: int,
    ) -> bool:
        existing = self.claimant_by_request.get(request.id)
        if existing is not None and existing is not claimant:
            return False
        self.claimant_by_request[request.id] = claimant
        self.claim_generation_by_request[request.id] = provider_generation
        return True

    def claim_is_current(
        self,
        request: AcquisitionRequest,
        *,
        claimant: object,
        provider_generation: int,
    ) -> bool:
        return (
            self.claimant_by_request.get(request.id) is claimant
            and self.claim_generation_by_request.get(request.id) == provider_generation
        )

    def release_claim(
        self,
        request_id: str,
        *,
        claimant: object,
        provider_generation: int,
    ) -> None:
        if (
            self.claimant_by_request.get(request_id) is not claimant
            or self.claim_generation_by_request.get(request_id) != provider_generation
        ):
            return
        self.claimant_by_request.pop(request_id, None)
        self.claim_generation_by_request.pop(request_id, None)


class _StubExecutor:
    def __init__(
        self,
        *,
        result: AcquisitionExecutionResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result or AcquisitionExecutionResult(sent_paths=(_FREQ,))
        self.error = error
        self.calls: list[tuple[AcquisitionRequest, frozenset[FieldPath]]] = []

    async def execute(
        self,
        request: AcquisitionRequest,
        *,
        already_sent_paths: frozenset[FieldPath],
    ) -> AcquisitionExecutionResult:
        self.calls.append((request, already_sent_paths))
        if self.error is not None:
            raise self.error
        return self.result


class _Reports:
    """Collects everything the drain hands back to its seat."""

    def __init__(self, *, expiry_is_terminal: bool = True) -> None:
        self.failures: list[tuple[str, str, frozenset[FieldPath]]] = []
        self.executor_missing: list[str] = []
        self.executor_errors: list[tuple[str, str]] = []
        self.sent: list[tuple[str, tuple[FieldPath, ...], int]] = []
        self.expiries: list[tuple[str, frozenset[FieldPath]]] = []
        self.forgotten: list[str] = []
        self._expiry_is_terminal = expiry_is_terminal

    def expiry(
        self,
        scheduler: Any,
        request: AcquisitionRequest,
        *,
        sent_paths: frozenset[FieldPath],
        now: float,
    ) -> bool:
        self.expiries.append((request.id, sent_paths))
        return self._expiry_is_terminal

    def forget(self, request_id: str) -> None:
        self.forgotten.append(request_id)

    def failure(
        self,
        scheduler: Any,
        request: AcquisitionRequest,
        *,
        reason: str,
        failed_paths: Any,
        now: float,
    ) -> None:
        self.failures.append((request.id, reason, frozenset(failed_paths)))

    def missing(
        self, scheduler: Any, request: AcquisitionRequest, *, now: float
    ) -> None:
        self.executor_missing.append(request.id)

    def error(
        self,
        scheduler: Any,
        request: AcquisitionRequest,
        *,
        error: BaseException,
        sent_paths: frozenset[FieldPath],
        now: float,
    ) -> None:
        self.executor_errors.append((request.id, type(error).__name__))

    def sent_report(
        self,
        request: AcquisitionRequest,
        *,
        paths: tuple[FieldPath, ...],
        pending_request_count: int,
    ) -> None:
        self.sent.append((request.id, paths, pending_request_count))


def _drain(
    *,
    scheduler: _StubScheduler,
    reports: _Reports,
    expired: Any = _never_expired,
    in_flight: dict[str, tuple[frozenset[FieldPath], float]] | None = None,
    executor: _StubExecutor | None = None,
    dispatchable: Any = None,
    seat_expiry: bool = False,
) -> AcquisitionDrain:
    return AcquisitionDrain(
        scheduler=lambda: cast(Any, scheduler),
        executor=lambda: cast(Any, executor),
        store=lambda: None,
        in_flight=in_flight if in_flight is not None else {},
        expired=expired,
        dispatchable=dispatchable if dispatchable is not None else (lambda pend: pend),
        report_failure=reports.failure,
        report_executor_missing=reports.missing,
        report_executor_error=reports.error,
        report_sent=reports.sent_report,
        report_expiry=reports.expiry if seat_expiry else None,
        on_forget=reports.forget,
    )


class TestAcquisitionDrainExpiry:
    async def test_an_expired_request_is_reported_and_dropped_from_flight(
        self,
    ) -> None:
        request = _request()
        scheduler = _StubScheduler((request,))
        reports = _Reports()
        in_flight = {request.id: (frozenset({_FREQ}), 10.0)}
        executor = _StubExecutor()
        drain = _drain(
            scheduler=scheduler,
            reports=reports,
            expired=_always_expired,
            in_flight=in_flight,
            executor=executor,
        )

        await drain.run_once()

        assert reports.failures == [
            (request.id, "acquisition_request_timeout", frozenset({_FREQ}))
        ]
        assert in_flight == {}
        assert executor.calls == [], "a timed-out request must not be re-sent"

    async def test_a_late_dispatched_request_expires_for_rigctld_but_not_for_web(
        self,
    ) -> None:
        """The injected expiry rule is load-bearing, not decoration.

        rigctld expires a dispatched request at the earlier of its enqueue
        deadline and ``sent_at + timeout``; the web poller expires it one
        short answer window (``_ACQUISITION_ANSWER_WINDOW_SECONDS``) after
        the SEND — MOR-2614 — and its own comment calls the ``min`` form a
        false timeout. A request that sat queued past its enqueue deadline
        and was answered within the window after dispatch separates them.
        """
        from rigplane.web.radio_poller import _ACQUISITION_ANSWER_WINDOW_SECONDS

        request = _request(deadline=1.0, max_age=1.0, timeout=None)
        rigctld_expired = RigctldServer._acquisition_request_expired  # noqa: SLF001
        web_expired = RadioPoller._acquisition_request_expired  # noqa: SLF001

        inside = 5.0 + _ACQUISITION_ANSWER_WINDOW_SECONDS / 2.0
        assert rigctld_expired(request, sent_at=5.0, now=inside) is True
        assert web_expired(cast(Any, None), request, sent_at=5.0, now=inside) is False

        # Control: once the send-relative window itself elapses the two rules
        # agree, so the split above is the rule and not the fixture.
        outside = 5.0 + _ACQUISITION_ANSWER_WINDOW_SECONDS + 0.1
        assert rigctld_expired(request, sent_at=5.0, now=outside) is True
        assert web_expired(cast(Any, None), request, sent_at=5.0, now=outside) is True


class TestAcquisitionDrainDispatchEligibility:
    async def test_an_ineligible_request_is_not_sent_but_keeps_its_ledger_entry(
        self,
    ) -> None:
        """Eligibility gates dispatch only.

        Ledger pruning runs over the unfiltered pending view, so a request
        the seat declines to send this pass is not mistaken for one the
        scheduler has completed.
        """

        cadence = _request(request_id="acq-cadence")
        on_demand = _request(
            request_id="acq-user", reasons=("policy-cadence", "user_read")
        )
        scheduler = _StubScheduler((cadence, on_demand))
        reports = _Reports()
        executor = _StubExecutor()
        in_flight = {cadence.id: (frozenset({_FREQ}), 10.0)}
        drain = _drain(
            scheduler=scheduler,
            reports=reports,
            in_flight=in_flight,
            executor=executor,
            dispatchable=lambda pending: tuple(
                request for request in pending if request.reasons != ("policy-cadence",)
            ),
        )

        await drain.run_once()

        assert [call[0].id for call in executor.calls] == ["acq-user"]
        assert cadence.id in in_flight, (
            "the cadence request is still pending; declining to send it this "
            "pass must not evict its in-flight record"
        )


class TestAcquisitionDrainExecutor:
    async def test_missing_executor_is_reported_and_nothing_is_sent(self) -> None:
        request = _request()
        scheduler = _StubScheduler((request,))
        reports = _Reports()
        drain = _drain(scheduler=scheduler, reports=reports, executor=None)

        await drain.run_once()

        assert reports.executor_missing == [request.id]
        assert reports.sent == []

    async def test_executor_exception_is_reported_and_the_ledger_entry_dropped(
        self,
    ) -> None:
        request = _request()
        scheduler = _StubScheduler((request,))
        reports = _Reports()
        in_flight = {request.id: (frozenset(), 10.0)}
        drain = _drain(
            scheduler=scheduler,
            reports=reports,
            in_flight=in_flight,
            executor=_StubExecutor(error=RuntimeError("port closed")),
        )

        await drain.run_once()

        assert reports.executor_errors == [(request.id, "RuntimeError")]
        assert in_flight == {}

    async def test_executor_cancellation_propagates(self) -> None:
        """Cancellation is the drain task stopping, not a request failing."""

        request = _request()
        scheduler = _StubScheduler((request,))
        reports = _Reports()
        drain = _drain(
            scheduler=scheduler,
            reports=reports,
            executor=_StubExecutor(error=asyncio.CancelledError()),
        )

        with pytest.raises(asyncio.CancelledError):
            await drain.run_once()
        assert reports.executor_errors == []

    async def test_sent_paths_accumulate_and_the_sent_report_counts_dispatchable(
        self,
    ) -> None:
        request = _request(paths=(_FREQ, _MODE))
        scheduler = _StubScheduler((request,))
        reports = _Reports()
        in_flight: dict[str, tuple[frozenset[FieldPath], float]] = {}
        drain = _drain(
            scheduler=scheduler,
            reports=reports,
            in_flight=in_flight,
            executor=_StubExecutor(
                result=AcquisitionExecutionResult(
                    sent_paths=(_FREQ,),
                    failed_paths=(_MODE,),
                    failure_reason="no_civ_query_mapping",
                )
            ),
        )

        await drain.run_once()

        assert reports.sent == [(request.id, (_FREQ,), 1)]
        assert in_flight[request.id][0] == frozenset({_FREQ})
        assert reports.failures == [
            (request.id, "no_civ_query_mapping", frozenset({_MODE}))
        ]
        # Only the path that went out is dispatched; the one the executor
        # could not map is not.
        assert scheduler.dispatches == [
            (request.id, (_FREQ,), in_flight[request.id][1])
        ]

    async def test_a_send_is_reported_to_the_scheduler_as_a_dispatch(self) -> None:
        request = _request()
        scheduler = _StubScheduler((request,))
        drain = _drain(
            scheduler=scheduler, reports=_Reports(), executor=_StubExecutor()
        )

        await drain.run_once()

        assert [entry[:2] for entry in scheduler.dispatches] == [(request.id, (_FREQ,))]

    async def test_a_request_that_sent_nothing_is_not_reported_as_dispatched(
        self,
    ) -> None:
        request = _request()
        scheduler = _StubScheduler((request,))
        drain = _drain(
            scheduler=scheduler,
            reports=_Reports(),
            executor=_StubExecutor(
                result=AcquisitionExecutionResult(
                    sent_paths=(),
                    failed_paths=(_FREQ,),
                    failure_reason="no_civ_query_mapping",
                )
            ),
        )

        await drain.run_once()

        assert scheduler.dispatches == []

    async def test_a_request_whose_executor_raised_is_not_reported_as_dispatched(
        self,
    ) -> None:
        request = _request()
        scheduler = _StubScheduler((request,))
        drain = _drain(
            scheduler=scheduler,
            reports=_Reports(),
            executor=_StubExecutor(error=RuntimeError("port closed")),
        )

        await drain.run_once()

        assert scheduler.dispatches == []


class TestAcquisitionDrainLedger:
    async def test_a_request_the_scheduler_no_longer_pends_is_pruned(self) -> None:
        scheduler = _StubScheduler(())
        reports = _Reports()
        in_flight = {"acq-gone": (frozenset({_FREQ}), 10.0)}
        drain = _drain(
            scheduler=scheduler,
            reports=reports,
            expired=_always_expired,
            in_flight=in_flight,
        )

        await drain.run_once()

        assert in_flight == {}
        assert reports.failures == [], "a credited request is not a failure"

    async def test_tx_active_is_refreshed_from_the_store_every_pass(self) -> None:
        """MOR-1532: the drain, not the seat, keeps the cached fact current."""

        scheduler = _StubScheduler(())
        drain = _drain(scheduler=scheduler, reports=_Reports())

        await drain.run_once()

        assert scheduler.tx_active_calls == [False]


class TestAcquisitionDrainSeatOwnedExpiry:
    """The non-terminal expiry seam, whose caller is MOR-874's grace (3b).

    A seat may report a fired deadline itself and say the request is not
    finished with: the web poller holds a healthy-link expiry in flight so a
    late answer can still credit it. A seat that injects no ``report_expiry``
    keeps the terminal rule pinned in ``TestAcquisitionDrainExpiry``.
    """

    async def test_a_non_terminal_expiry_keeps_the_ledger_entry(self) -> None:
        request = _request()
        scheduler = _StubScheduler((request,))
        reports = _Reports(expiry_is_terminal=False)
        in_flight = {request.id: (frozenset({_FREQ}), 10.0)}
        executor = _StubExecutor()
        drain = _drain(
            scheduler=scheduler,
            reports=reports,
            expired=_always_expired,
            in_flight=in_flight,
            executor=executor,
            seat_expiry=True,
        )

        await drain.run_once()

        assert reports.expiries == [(request.id, frozenset({_FREQ}))]
        assert in_flight == {request.id: (frozenset({_FREQ}), 10.0)}
        assert reports.forgotten == []
        assert executor.calls == [], "every path was already sent; nothing to re-send"
        assert reports.failures == [], (
            "the seat reported this expiry itself; the drain must not also "
            "report it through the ordinary failure hook"
        )

    async def test_a_non_terminal_expiry_still_sends_the_paths_never_dispatched(
        self,
    ) -> None:
        """Holding a request in flight is not the same as skipping the pass.

        Only the paths already on the wire are withheld. This is the branch
        the terminal rule cannot reach, because it ``continue``s first.
        """

        request = _request(paths=(_FREQ, _MODE))
        scheduler = _StubScheduler((request,))
        reports = _Reports(expiry_is_terminal=False)
        in_flight = {request.id: (frozenset({_FREQ}), 10.0)}
        executor = _StubExecutor(result=AcquisitionExecutionResult(sent_paths=(_MODE,)))
        drain = _drain(
            scheduler=scheduler,
            reports=reports,
            expired=_always_expired,
            in_flight=in_flight,
            executor=executor,
            seat_expiry=True,
        )

        await drain.run_once()

        assert [call[1] for call in executor.calls] == [frozenset({_FREQ})]
        assert in_flight[request.id][0] == frozenset({_FREQ, _MODE})

    async def test_a_terminal_seat_expiry_drops_the_entry_without_a_second_report(
        self,
    ) -> None:
        request = _request()
        scheduler = _StubScheduler((request,))
        reports = _Reports(expiry_is_terminal=True)
        in_flight = {request.id: (frozenset({_FREQ}), 10.0)}
        executor = _StubExecutor()
        drain = _drain(
            scheduler=scheduler,
            reports=reports,
            expired=_always_expired,
            in_flight=in_flight,
            executor=executor,
            seat_expiry=True,
        )

        await drain.run_once()

        assert in_flight == {}
        assert reports.forgotten == [request.id]
        assert reports.failures == []
        assert executor.calls == [], "a timed-out request must not be re-sent"


class TestAcquisitionDrainForgetHook:
    """``on_forget`` fires wherever the drain drops a ledger entry.

    The web seat keeps a second per-request map (the MOR-874 grace clock)
    alongside the ledger; an entry the drain drops for any reason must not
    leave a stale clock behind. Each drop site is exercised separately
    because they are three different statements in ``run_once``.
    """

    async def test_a_pruned_request_is_forgotten(self) -> None:
        scheduler = _StubScheduler(())
        reports = _Reports()
        in_flight = {"acq-gone": (frozenset({_FREQ}), 10.0)}
        drain = _drain(scheduler=scheduler, reports=reports, in_flight=in_flight)

        await drain.run_once()

        assert reports.forgotten == ["acq-gone"]

    async def test_a_timed_out_request_is_forgotten(self) -> None:
        request = _request()
        scheduler = _StubScheduler((request,))
        reports = _Reports()
        in_flight = {request.id: (frozenset({_FREQ}), 10.0)}
        drain = _drain(
            scheduler=scheduler,
            reports=reports,
            expired=_always_expired,
            in_flight=in_flight,
            executor=_StubExecutor(),
        )

        await drain.run_once()

        assert reports.forgotten == [request.id]

    async def test_a_request_whose_executor_raised_is_forgotten(self) -> None:
        request = _request()
        scheduler = _StubScheduler((request,))
        reports = _Reports()
        in_flight = {request.id: (frozenset(), 10.0)}
        drain = _drain(
            scheduler=scheduler,
            reports=reports,
            in_flight=in_flight,
            executor=_StubExecutor(error=RuntimeError("port closed")),
        )

        await drain.run_once()

        assert reports.forgotten == [request.id]

    async def test_a_seat_that_injects_no_forget_hook_still_drains(self) -> None:
        """rigctld injects neither new hook; the default must be inert."""

        request = _request()
        scheduler = _StubScheduler((request,))
        reports = _Reports()
        in_flight = {request.id: (frozenset({_FREQ}), 10.0)}
        drain = AcquisitionDrain(
            scheduler=lambda: cast(Any, scheduler),
            executor=lambda: cast(Any, _StubExecutor()),
            store=lambda: None,
            in_flight=in_flight,
            expired=_always_expired,
            dispatchable=lambda pending: pending,
            report_failure=reports.failure,
            report_executor_missing=reports.missing,
            report_executor_error=reports.error,
            report_sent=reports.sent_report,
        )

        await drain.run_once()

        assert in_flight == {}
        assert reports.failures == [
            (request.id, "acquisition_request_timeout", frozenset({_FREQ}))
        ]
        assert reports.forgotten == []


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[FieldPath, ...]] = []

    async def execute(
        self,
        request: AcquisitionRequest,
        *,
        already_sent_paths: frozenset[FieldPath],
    ) -> AcquisitionExecutionResult:
        del already_sent_paths
        self.calls.append(request.paths)
        return AcquisitionExecutionResult(sent_paths=request.paths)


def _queued_tx_meter() -> tuple[AcquisitionScheduler, AcquisitionRequest, StateStore]:
    clock = FreshnessClock(start=800.0)
    swr = FieldPath.global_("meters", "swr")
    store = StateStore(freshness_clock=clock)
    scheduler = AcquisitionScheduler(
        profile=RadioAcquisitionProfile(
            provider="test_provider",
            capabilities=(FieldCapability(path=swr, polling=True),),
            field_policies={
                swr: AcquisitionPolicy(
                    cadence_seconds=None,
                    freshness_ttl_seconds=2.0,
                    tx_only=True,
                ),
            },
        ),
        clock=clock,
    )
    result = scheduler.ensure_fresh(
        swr,
        max_age=2.0,
        priority=AcquisitionPriority.RECONCILIATION,
        reason="stale",
    )
    assert result.status is AcquisitionStatus.QUEUED
    assert result.request is not None
    scheduler.note_tx_active(False)
    return scheduler, result.request, store


def _hint_drain(
    scheduler: AcquisitionScheduler,
    store: StateStore,
    executor: _RecordingExecutor,
    hint: dict[str, bool],
) -> AcquisitionDrain:
    reports = _Reports()

    def tx_active_hint() -> bool:
        return hint["keyed"]

    return AcquisitionDrain(
        scheduler=lambda: scheduler,
        executor=lambda: executor,
        store=lambda: store,
        in_flight={},
        expired=_never_expired,
        dispatchable=lambda pending: pending,
        report_failure=reports.failure,
        report_executor_missing=reports.missing,
        report_executor_error=reports.error,
        report_sent=reports.sent_report,
        tx_active_hint=tx_active_hint,
    )


class TestManagedTxHint:
    async def test_a_true_hint_dispatches_tx_meter_without_observed_ptt(self) -> None:
        """MOR-2616: the drain ORs the hint with observed PTT before note_tx_active."""

        hint = {"keyed": True}
        scheduler, request, store = _queued_tx_meter()
        executor = _RecordingExecutor()
        drain = _hint_drain(scheduler, store, executor, hint)

        await drain.run_once()

        assert executor.calls == [request.paths]
        assert request.policy.tx_only

        hint["keyed"] = False
        released_scheduler, released_request, released_store = _queued_tx_meter()
        released_executor = _RecordingExecutor()
        released = _hint_drain(
            released_scheduler, released_store, released_executor, hint
        )
        await released.run_once()

        assert released_executor.calls == []
        assert released_scheduler.dispatchable_requests() == ()
        assert released_request.policy.tx_only


_TX_METER_NAMES = ("alc", "comp", "power", "swr")


def _tx_meter_changeset(path: FieldPath, *, at: float) -> ChangeSet:
    return ChangeSet(
        revision=1,
        freshness_revision=1,
        observation_seq=1,
        changes=(FieldChange(path=path, previous=None, current=1.0),),
        timestamp_monotonic=at,
        sources=(
            SourceMetadata(
                source="command_response",
                provider="icom_civ",
                transport="civ",
            ),
        ),
        observed_paths=(path,),
    )


class _EarlyAnswerExecutor:
    """Delivers answers for every path but the last while execute is still running."""

    def __init__(self, scheduler: AcquisitionScheduler, *, pass_now: float) -> None:
        self._scheduler = scheduler
        self._pass_now = pass_now
        self.credited: list[str] = []

    async def execute(
        self,
        request: AcquisitionRequest,
        *,
        already_sent_paths: frozenset[FieldPath],
    ) -> AcquisitionExecutionResult:
        del already_sent_paths
        observed_at = self._pass_now + 0.01
        for path in request.paths[:-1]:
            pending = next(
                (
                    item
                    for item in self._scheduler.pending_requests()
                    if item.id == request.id and path in item.paths
                ),
                None,
            )
            assert pending is not None
            matched = replace(pending, paths=(path,))
            if self._scheduler.may_credit(matched, observation_timestamp=observed_at):
                self._scheduler.record_acquisition_result(
                    matched,
                    _tx_meter_changeset(path, at=observed_at),
                )
                self.credited.append(path.name)
        return AcquisitionExecutionResult(sent_paths=request.paths)


def test_an_answer_that_arrives_during_execute_completes_the_request() -> None:
    """MOR-2617: early TX-meter answers must not leave the key pending until expiry."""

    pass_now = 100.0
    clock = FreshnessClock(start=pass_now)
    paths = tuple(FieldPath.global_("meters", name) for name in _TX_METER_NAMES)
    policy = AcquisitionPolicy(
        cadence_seconds=0.25,
        freshness_ttl_seconds=2.0,
        tx_only=True,
    )
    scheduler = AcquisitionScheduler(
        profile=RadioAcquisitionProfile(
            provider="test_provider",
            capabilities=tuple(
                FieldCapability(path=path, polling=True, stream_like=True)
                for path in paths
            ),
            field_policies={path: policy for path in paths},
        ),
        clock=clock,
    )
    queued = scheduler.due_requests(now=pass_now, tx_active=True)
    assert len(queued) == 1
    request = queued[0]
    assert request.paths == paths

    executor = _EarlyAnswerExecutor(scheduler, pass_now=pass_now)
    reports = _Reports()
    store = StateStore(freshness_clock=clock)
    drain = AcquisitionDrain(
        scheduler=lambda: scheduler,
        executor=lambda: cast(Any, executor),
        store=lambda: store,
        in_flight={},
        expired=_never_expired,
        dispatchable=lambda pending: pending,
        report_failure=reports.failure,
        report_executor_missing=reports.missing,
        report_executor_error=reports.error,
        report_sent=reports.sent_report,
        tx_active_hint=lambda: True,
    )

    with patch("rigplane.core.acquisition_drain.time.monotonic", return_value=pass_now):
        asyncio.run(drain.run_once())

    last = paths[-1]
    pending_last = next(
        (
            item
            for item in scheduler.pending_requests()
            if item.id == request.id and last in item.paths
        ),
        None,
    )
    if pending_last is not None:
        scheduler.record_acquisition_result(
            replace(pending_last, paths=(last,)),
            _tx_meter_changeset(last, at=pass_now + 0.02),
        )

    assert scheduler.pending_requests() == ()
    assert executor.credited == [path.name for path in paths[:-1]]
    again = scheduler.due_requests(now=pass_now + 0.25, tx_active=True)
    assert len(again) == 1
    assert again[0].id != request.id
    assert again[0].paths == paths
