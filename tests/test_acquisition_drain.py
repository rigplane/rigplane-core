"""Tests for src/rigplane/core/acquisition_drain.py (MOR-2293).

Everything that differs between the two dispatching seats reaches the drain
as an injected collaborator, so these tests drive it with stubs and assert
on what those collaborators were handed. The seats' own behaviour is pinned
in ``tests/test_rigctld_server.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from rigplane.core.acquisition_drain import AcquisitionDrain
from rigplane.core.acquisition_scheduler import (
    AcquisitionExecutionResult,
    AcquisitionPriority,
    AcquisitionRequest,
)
from rigplane.core.state_acquisition_policy import AcquisitionPolicy
from rigplane.core.state_pipeline_contracts import FieldPath
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
    """Only the two methods the drain calls on a scheduler."""

    def __init__(self, pending: tuple[AcquisitionRequest, ...]) -> None:
        self.pending = pending
        self.tx_active_calls: list[bool] = []

    def note_tx_active(self, tx_active: bool) -> None:
        self.tx_active_calls.append(tx_active)

    def dispatchable_requests(self) -> tuple[AcquisitionRequest, ...]:
        return self.pending


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

    def __init__(self) -> None:
        self.failures: list[tuple[str, str, frozenset[FieldPath]]] = []
        self.executor_missing: list[str] = []
        self.executor_errors: list[tuple[str, str]] = []
        self.sent: list[tuple[str, tuple[FieldPath, ...], int]] = []

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
        deadline and ``sent_at + timeout``; the web poller expires it
        ``max_age`` (or ``timeout``) seconds after the SEND, and its own
        comment calls the ``min`` form a false timeout. A request that sat
        queued past its enqueue deadline and was answered half a second
        after dispatch separates them.
        """

        request = _request(deadline=1.0, max_age=1.0, timeout=None)
        rigctld_expired = RigctldServer._acquisition_request_expired  # noqa: SLF001
        web_expired = RadioPoller._acquisition_request_expired  # noqa: SLF001

        assert rigctld_expired(request, sent_at=5.0, now=5.5) is True
        assert web_expired(cast(Any, None), request, sent_at=5.0, now=5.5) is False

        # Control: once the send-relative window itself elapses the two rules
        # agree, so the split above is the rule and not the fixture.
        assert rigctld_expired(request, sent_at=5.0, now=6.5) is True
        assert web_expired(cast(Any, None), request, sent_at=5.0, now=6.5) is True


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
