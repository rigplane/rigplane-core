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
        self.claimant_by_request: dict[str, object] = {}
        self.claim_generation_by_request: dict[str, int] = {}

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

    def release_claim(self, request_id: str, *, claimant: object) -> None:
        if self.claimant_by_request.get(request_id) is not claimant:
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
