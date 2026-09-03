"""One acquisition drain for the seats that dispatch scheduler requests.

MOR-2293 (slice 3 of MOR-2260). ``AcquisitionScheduler`` decides what needs
reading; a drain turns its ``dispatchable_requests()`` into backend sends,
keeps the in-flight ledger, and reports what came back. The web poller and
the rigctld seat each grew their own copy of that loop.

Injected, because the seats differ deliberately:

* ``expired`` — rigctld expires a dispatched request at the earlier of its
  enqueue deadline and ``sent_at + timeout``; the web poller measures from
  the send and its own comment calls the ``min`` form a false timeout.
* ``dispatchable`` — which pending requests this seat may put on the wire
  this pass. rigctld stands the profile cadence down while an external CAT
  session owns the byte stream; no deadline rule can express that.

The reporting hooks are injected for the same reason: the seats record
different diagnostic sources and different executor-failure reasons.

Two hooks are optional, and rigctld injects neither. ``report_expiry`` lets
a seat report a fired deadline itself and answer whether the request is
finished with: MOR-874's healthy-link grace holds a web request in flight so
a late answer can still credit it, where rigctld's every timeout is
terminal. ``on_forget`` names the request ids the drain drops from the
ledger, which is what keeps the web seat's grace clock from outliving them.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, MutableMapping, Sequence
from typing import Protocol

from .acquisition_scheduler import (
    AcquisitionExecutor,
    AcquisitionRequest,
    AcquisitionScheduler,
    derive_tx_active,
)
from .state_pipeline_contracts import FieldPath
from .state_store import StateStore

__all__ = [
    "AcquisitionDrain",
    "AcquisitionExpiryPolicy",
    "AcquisitionExpiryReport",
    "InFlightLedger",
]

#: request id -> (paths already sent for it, monotonic time of that send).
InFlightLedger = MutableMapping[str, tuple[frozenset[FieldPath], float]]


class AcquisitionExpiryPolicy(Protocol):
    """The seat's rule for a request that has already been dispatched."""

    def __call__(
        self,
        request: AcquisitionRequest,
        *,
        sent_at: float,
        now: float,
    ) -> bool: ...


class AcquisitionFailureReport(Protocol):
    def __call__(
        self,
        scheduler: AcquisitionScheduler,
        request: AcquisitionRequest,
        *,
        reason: str,
        failed_paths: tuple[FieldPath, ...] | frozenset[FieldPath],
        now: float,
    ) -> None: ...


class AcquisitionExecutorMissingReport(Protocol):
    def __call__(
        self,
        scheduler: AcquisitionScheduler,
        request: AcquisitionRequest,
        *,
        now: float,
    ) -> None: ...


class AcquisitionExecutorErrorReport(Protocol):
    def __call__(
        self,
        scheduler: AcquisitionScheduler,
        request: AcquisitionRequest,
        *,
        error: BaseException,
        sent_paths: frozenset[FieldPath],
        now: float,
    ) -> None: ...


class AcquisitionExpiryReport(Protocol):
    """Report one fired deadline; return whether it ends the request."""

    def __call__(
        self,
        scheduler: AcquisitionScheduler,
        request: AcquisitionRequest,
        *,
        sent_paths: frozenset[FieldPath],
        now: float,
    ) -> bool: ...


class AcquisitionSentReport(Protocol):
    def __call__(
        self,
        request: AcquisitionRequest,
        *,
        paths: tuple[FieldPath, ...],
        pending_request_count: int,
    ) -> None: ...


class AcquisitionDrain:
    """Dispatch one pass over the scheduler's dispatchable requests."""

    def __init__(
        self,
        *,
        scheduler: Callable[[], AcquisitionScheduler | None],
        executor: Callable[[], AcquisitionExecutor | None],
        store: Callable[[], StateStore | None],
        in_flight: InFlightLedger,
        expired: AcquisitionExpiryPolicy,
        dispatchable: Callable[
            [Sequence[AcquisitionRequest]], Sequence[AcquisitionRequest]
        ],
        report_failure: AcquisitionFailureReport,
        report_executor_missing: AcquisitionExecutorMissingReport,
        report_executor_error: AcquisitionExecutorErrorReport,
        report_sent: AcquisitionSentReport,
        report_expiry: AcquisitionExpiryReport | None = None,
        on_forget: Callable[[str], None] | None = None,
        claimant: object | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._executor = executor
        self._store = store
        self._in_flight = in_flight
        self._expired = expired
        self._dispatchable = dispatchable
        self._report_failure = report_failure
        self._report_executor_missing = report_executor_missing
        self._report_executor_error = report_executor_error
        self._report_sent = report_sent
        self._report_expiry = report_expiry
        self._on_forget = on_forget
        self._claimant = self if claimant is None else claimant

    def _forget(self, scheduler: AcquisitionScheduler, request_id: str) -> None:
        """Drop one ledger entry and tell the seat it is gone."""

        scheduler.release_claim(request_id, claimant=self._claimant)
        self._in_flight.pop(request_id, None)
        if self._on_forget is not None:
            self._on_forget(request_id)

    def _expiry_is_terminal(
        self,
        scheduler: AcquisitionScheduler,
        request: AcquisitionRequest,
        *,
        sent_paths: frozenset[FieldPath],
        now: float,
    ) -> bool:
        if self._report_expiry is not None:
            return self._report_expiry(
                scheduler, request, sent_paths=sent_paths, now=now
            )
        self._report_failure(
            scheduler,
            request,
            reason="acquisition_request_timeout",
            failed_paths=sent_paths or frozenset(request.paths),
            now=now,
        )
        return True

    async def run_once(self) -> None:
        scheduler = self._scheduler()
        if scheduler is None:
            return
        now = time.monotonic()
        store = self._store()
        # MOR-1532: keep the scheduler's tx_active cache current on this drain
        # path too. Same ``derive_tx_active`` over the same canonical store as
        # the freshness tick's own cadence call, so the two writers cannot
        # disagree about one store state.
        scheduler.note_tx_active(False if store is None else derive_tx_active(store))
        # MOR-1533: dispatch must use the tx_active-gated view; crediting an
        # already-sent answer (runtime._civ_rx, driven by the radio's own CI-V
        # pump) uses the unfiltered pending_requests() instead, so an answer
        # landing after de-key is never blinded by this gate.
        pending = scheduler.dispatchable_requests()
        pending_ids = {request.id for request in pending}
        for request_id in tuple(self._in_flight):
            if request_id not in pending_ids:
                self._forget(scheduler, request_id)

        # Eligibility is asked once per pass, over the unfiltered pending view
        # above: a request the seat declines to send is still pending, and its
        # ledger entry must survive the pass.
        for request in self._dispatchable(pending):
            provider_generation = 0 if store is None else store.provider_generation
            if not scheduler.try_claim(
                request,
                claimant=self._claimant,
                provider_generation=provider_generation,
            ):
                continue

            sent_paths: frozenset[FieldPath] = frozenset()
            existing = self._in_flight.get(request.id)
            if existing is not None:
                sent_paths, sent_at = existing
                if self._expired(request, sent_at=sent_at, now=now):
                    # A seat may hold the request in flight instead (MOR-874).
                    # Falling through then re-sends only what never went out,
                    # which is what the intersection below leaves.
                    if self._expiry_is_terminal(
                        scheduler, request, sent_paths=sent_paths, now=now
                    ):
                        self._forget(scheduler, request.id)
                        continue
                sent_paths = sent_paths.intersection(request.paths)

            if all(path in sent_paths for path in request.paths):
                continue

            executor = self._executor()
            if executor is None:
                try:
                    self._report_executor_missing(scheduler, request, now=now)
                finally:
                    self._forget(scheduler, request.id)
                continue

            try:
                result = await executor.execute(
                    request,
                    already_sent_paths=sent_paths,
                )
            except asyncio.CancelledError:
                self._forget(scheduler, request.id)
                raise
            except Exception as exc:
                try:
                    self._report_executor_error(
                        scheduler,
                        request,
                        error=exc,
                        sent_paths=sent_paths,
                        now=now,
                    )
                finally:
                    self._forget(scheduler, request.id)
                continue

            current_store = self._store()
            generation_is_current = (
                current_store is store
                and (
                    current_store is None
                    or current_store.provider_generation == provider_generation
                )
            )
            if not generation_is_current or not scheduler.claim_is_current(
                request,
                claimant=self._claimant,
                provider_generation=provider_generation,
            ):
                self._forget(scheduler, request.id)
                continue

            failed_paths = tuple(result.failed_paths)
            if failed_paths:
                self._report_failure(
                    scheduler,
                    request,
                    reason=result.failure_reason or "acquisition_request_failed",
                    failed_paths=failed_paths,
                    now=now,
                )

            newly_sent = tuple(result.sent_paths)
            if newly_sent:
                self._in_flight[request.id] = (sent_paths.union(newly_sent), now)
                self._report_sent(
                    request,
                    paths=newly_sent,
                    # MOR-1533: dispatchable_requests(), matching this drain's
                    # own dispatch view -- not the unfiltered
                    # pending_requests(), which would also count entries this
                    # drain will never send (withheld tx_only hints).
                    pending_request_count=len(scheduler.dispatchable_requests()),
                )
