"""Backend-neutral acquisition scheduling for state freshness repair."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal

from rigplane.core.state_acquisition_policy import (
    AcquisitionPolicy,
    ExternalCatPauseBehavior,
    FieldAvailability,
    FieldCapability,
    RadioAcquisitionProfile,
)
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import (
    FieldSnapshot,
    FreshnessClock,
    FreshnessState,
    StateStore,
)

__all__ = [
    "AcquisitionMethod",
    "AcquisitionPriority",
    "AcquisitionRequest",
    "AcquisitionScheduler",
    "AcquisitionStatus",
    "EnsureFreshResult",
    "RadioStateModelService",
]


AcquisitionMethod = Literal["poll", "command_response", "wait_for_unsolicited"]


class AcquisitionPriority(StrEnum):
    """Scheduler priority classes for backend acquisition requests."""

    BACKGROUND = "background"
    RECONCILIATION = "reconciliation"
    NORMAL = "normal"
    COMMAND = "command"
    USER = "user"


class AcquisitionStatus(StrEnum):
    """Result of an ensure-fresh request."""

    FRESH = "fresh"
    QUEUED = "queued"
    DEFERRED = "deferred"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AcquisitionRequest:
    """One backend-neutral acquisition request emitted by the scheduler."""

    id: str
    paths: tuple[FieldPath, ...]
    priority: AcquisitionPriority
    reason: str
    reasons: tuple[str, ...]
    requested_at_monotonic: float
    deadline_monotonic: float
    max_age: float
    timeout: float | None
    provider: str
    acquisition_method: AcquisitionMethod
    policy: AcquisitionPolicy
    capability_ids: tuple[str, ...]
    external_cat_paused: bool = False
    external_cat_owner: str | None = None
    source_metadata: dict[str, str | None] | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a stable serializable projection for diagnostics/executors."""

        return {
            "id": self.id,
            "paths": [str(path) for path in self.paths],
            "priority": self.priority.value,
            "reason": self.reason,
            "reasons": list(self.reasons),
            "requestedAtMonotonic": self.requested_at_monotonic,
            "deadlineMonotonic": self.deadline_monotonic,
            "maxAge": self.max_age,
            "timeout": self.timeout,
            "provider": self.provider,
            "acquisitionMethod": self.acquisition_method,
            "policy": self.policy.to_dict(),
            "capabilityIds": list(self.capability_ids),
            "externalCatPaused": self.external_cat_paused,
            "externalCatOwner": self.external_cat_owner,
            "sourceMetadata": self.source_metadata,
        }


@dataclass(frozen=True, slots=True)
class EnsureFreshResult:
    """Model-service result for a freshness request."""

    status: AcquisitionStatus
    fields: tuple[FieldSnapshot, ...] = ()
    request: AcquisitionRequest | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class _PendingEnsureFresh:
    paths: tuple[FieldPath, ...]
    max_age: float
    priority: AcquisitionPriority
    reason: str
    timeout: float | None
    requested_at_monotonic: float
    external_cat_owner: str | None


_PRIORITY_RANK: dict[AcquisitionPriority, int] = {
    AcquisitionPriority.BACKGROUND: 0,
    AcquisitionPriority.RECONCILIATION: 1,
    AcquisitionPriority.NORMAL: 2,
    AcquisitionPriority.COMMAND: 3,
    AcquisitionPriority.USER: 4,
}


class AcquisitionScheduler:
    """Minimal priority/dedupe queue for backend-neutral acquisition reads."""

    __slots__ = (
        "_clock",
        "_deferred",
        "_external_cat_owner",
        "_external_cat_paused",
        "_external_cat_reason",
        "_next_id",
        "_profile",
        "_requests_by_key",
    )

    def __init__(
        self,
        *,
        profile: RadioAcquisitionProfile,
        clock: FreshnessClock | None = None,
    ) -> None:
        self._profile = profile
        self._clock = clock or FreshnessClock()
        self._requests_by_key: dict[tuple[FieldPath, ...], AcquisitionRequest] = {}
        self._deferred: dict[tuple[FieldPath, ...], _PendingEnsureFresh] = {}
        self._next_id = 1
        self._external_cat_paused = False
        self._external_cat_owner: str | None = None
        self._external_cat_reason = ""

    def ensure_fresh(
        self,
        paths: FieldPath | str | Iterable[FieldPath | str],
        *,
        max_age: float,
        priority: AcquisitionPriority | str,
        reason: str,
        timeout: float | None = None,
    ) -> EnsureFreshResult:
        """Queue acquisition for one or more field paths if policy allows it."""

        normalized_paths = _normalize_paths(paths)
        _validate_positive(max_age, label="max_age")
        normalized_priority = AcquisitionPriority(str(priority))
        now = self._clock.now()

        availability = self._availability_for(normalized_paths)
        if availability is not None:
            return availability

        if self._external_cat_paused and self._must_defer_for_external_cat(
            normalized_paths
        ):
            self._defer(
                _PendingEnsureFresh(
                    paths=normalized_paths,
                    max_age=max_age,
                    priority=normalized_priority,
                    reason=reason,
                    timeout=timeout,
                    requested_at_monotonic=now,
                    external_cat_owner=self._external_cat_owner,
                )
            )
            return EnsureFreshResult(
                status=AcquisitionStatus.DEFERRED,
                message=self._external_cat_reason or "external CAT ownership active",
            )

        request = self._queue(
            paths=normalized_paths,
            max_age=max_age,
            priority=normalized_priority,
            reason=reason,
            timeout=timeout,
            requested_at=now,
            external_cat_owner=self._external_cat_owner
            if self._external_cat_paused
            else None,
        )
        return EnsureFreshResult(status=AcquisitionStatus.QUEUED, request=request)

    def pending_requests(self) -> tuple[AcquisitionRequest, ...]:
        """Return queued backend acquisition requests in execution order."""

        return tuple(
            sorted(
                self._requests_by_key.values(),
                key=lambda request: (
                    -_PRIORITY_RANK[request.priority],
                    request.deadline_monotonic,
                    request.requested_at_monotonic,
                    request.id,
                ),
            )
        )

    def pause_external_cat(
        self,
        *,
        owner: str | None = None,
        reason: str = "",
    ) -> None:
        """Pause conflicting polling while an external CAT owner has control."""

        self._external_cat_paused = True
        self._external_cat_owner = owner
        self._external_cat_reason = reason

    def resume_external_cat(self) -> tuple[AcquisitionRequest, ...]:
        """Resume acquisition and queue any deferred freshness requests."""

        owner = self._external_cat_owner
        self._external_cat_paused = False
        self._external_cat_owner = None
        self._external_cat_reason = ""
        deferred = tuple(
            sorted(
                self._deferred.values(),
                key=lambda item: (
                    -_PRIORITY_RANK[item.priority],
                    item.requested_at_monotonic,
                    ",".join(str(path) for path in item.paths),
                ),
            )
        )
        self._deferred.clear()
        queued: list[AcquisitionRequest] = []
        for item in deferred:
            queued.append(
                self._queue(
                    paths=item.paths,
                    max_age=item.max_age,
                    priority=item.priority,
                    reason=item.reason,
                    timeout=item.timeout,
                    requested_at=self._clock.now(),
                    external_cat_owner=item.external_cat_owner or owner,
                )
            )
        return tuple(queued)

    def _queue(
        self,
        *,
        paths: tuple[FieldPath, ...],
        max_age: float,
        priority: AcquisitionPriority,
        reason: str,
        timeout: float | None,
        requested_at: float,
        external_cat_owner: str | None,
    ) -> AcquisitionRequest:
        key = paths
        existing = self._requests_by_key.get(key)
        if existing is not None:
            request = self._coalesce(
                existing,
                max_age=max_age,
                priority=priority,
                reason=reason,
                timeout=timeout,
                requested_at=requested_at,
            )
            self._requests_by_key[key] = request
            return request

        request = self._new_request(
            paths=paths,
            max_age=max_age,
            priority=priority,
            reason=reason,
            timeout=timeout,
            requested_at=requested_at,
            external_cat_owner=external_cat_owner,
        )
        self._requests_by_key[key] = request
        return request

    def _new_request(
        self,
        *,
        paths: tuple[FieldPath, ...],
        max_age: float,
        priority: AcquisitionPriority,
        reason: str,
        timeout: float | None,
        requested_at: float,
        external_cat_owner: str | None,
    ) -> AcquisitionRequest:
        capabilities = tuple(self._profile.capability_for(path) for path in paths)
        request_id = f"acq-{self._next_id}"
        self._next_id += 1
        method = _select_method(capabilities)
        return AcquisitionRequest(
            id=request_id,
            paths=paths,
            priority=priority,
            reason=reason,
            reasons=(reason,),
            requested_at_monotonic=requested_at,
            deadline_monotonic=requested_at + max_age,
            max_age=max_age,
            timeout=timeout,
            provider=self._profile.provider,
            acquisition_method=method,
            policy=self._merged_policy(paths),
            capability_ids=tuple(str(path) for path in paths),
            external_cat_paused=self._external_cat_paused,
            external_cat_owner=external_cat_owner,
            source_metadata={
                "provider": self._profile.provider,
                "capabilityId": ",".join(str(path) for path in paths),
            },
        )

    def _coalesce(
        self,
        existing: AcquisitionRequest,
        *,
        max_age: float,
        priority: AcquisitionPriority,
        reason: str,
        timeout: float | None,
        requested_at: float,
    ) -> AcquisitionRequest:
        priority_to_keep = (
            priority
            if _PRIORITY_RANK[priority] > _PRIORITY_RANK[existing.priority]
            else existing.priority
        )
        reasons = existing.reasons
        if reason not in reasons:
            reasons = (*reasons, reason)
        deadline = min(existing.deadline_monotonic, requested_at + max_age)
        return replace(
            existing,
            priority=priority_to_keep,
            max_age=min(existing.max_age, max_age),
            timeout=_min_optional_timeout(existing.timeout, timeout),
            deadline_monotonic=deadline,
            reasons=reasons,
        )

    def _defer(self, item: _PendingEnsureFresh) -> None:
        existing = self._deferred.get(item.paths)
        if existing is None:
            self._deferred[item.paths] = item
            return
        priority = (
            item.priority
            if _PRIORITY_RANK[item.priority] > _PRIORITY_RANK[existing.priority]
            else existing.priority
        )
        max_age = min(existing.max_age, item.max_age)
        timeout = _min_optional_timeout(existing.timeout, item.timeout)
        reason = existing.reason if existing.reason == item.reason else item.reason
        self._deferred[item.paths] = _PendingEnsureFresh(
            paths=item.paths,
            max_age=max_age,
            priority=priority,
            reason=reason,
            timeout=timeout,
            requested_at_monotonic=min(
                existing.requested_at_monotonic,
                item.requested_at_monotonic,
            ),
            external_cat_owner=item.external_cat_owner or existing.external_cat_owner,
        )

    def _availability_for(
        self,
        paths: Sequence[FieldPath],
    ) -> EnsureFreshResult | None:
        for path in paths:
            capability = self._profile.capability_for(path)
            if capability.availability in (
                FieldAvailability.UNSUPPORTED,
                FieldAvailability.UNKNOWN,
            ):
                return EnsureFreshResult(
                    status=AcquisitionStatus.UNAVAILABLE,
                    message=capability.diagnostic
                    or f"{path}: acquisition capability unavailable",
                )
            if not _has_acquisition_hook(capability):
                return EnsureFreshResult(
                    status=AcquisitionStatus.UNAVAILABLE,
                    message=f"{path}: no acquisition hook is declared",
                )
        return None

    def _must_defer_for_external_cat(self, paths: Sequence[FieldPath]) -> bool:
        for path in paths:
            behavior = self._profile.policy_for(path).external_cat_pause
            if behavior is ExternalCatPauseBehavior.CONTINUE:
                continue
            if behavior is ExternalCatPauseBehavior.COALESCE_METERS_ONLY:
                if all(candidate.family.value == "meters" for candidate in paths):
                    continue
            return True
        return False

    def _merged_policy(self, paths: Sequence[FieldPath]) -> AcquisitionPolicy:
        return self._profile.policy_for(paths[0])


class RadioStateModelService:
    """Small service API joining StateStore freshness and scheduler requests."""

    __slots__ = ("_clock", "_scheduler", "_store")

    def __init__(
        self,
        *,
        store: StateStore,
        scheduler: AcquisitionScheduler,
        clock: FreshnessClock | None = None,
    ) -> None:
        self._store = store
        self._scheduler = scheduler
        self._clock = clock or FreshnessClock()

    def ensure_fresh(
        self,
        paths: FieldPath | str | Iterable[FieldPath | str],
        *,
        max_age: float,
        priority: AcquisitionPriority | str,
        reason: str,
        timeout: float | None = None,
    ) -> EnsureFreshResult:
        """Return fresh snapshots or queue acquisition through the scheduler."""

        normalized_paths = _normalize_paths(paths)
        _validate_positive(max_age, label="max_age")
        snapshot = self._store.snapshot()
        fields: list[FieldSnapshot] = []
        now = self._clock.now()
        for path in normalized_paths:
            try:
                field = snapshot.field(path)
            except KeyError:
                break
            if field.freshness is not FreshnessState.FRESH:
                break
            if now - field.last_observed_monotonic > max_age:
                break
            fields.append(field)
        else:
            return EnsureFreshResult(
                status=AcquisitionStatus.FRESH,
                fields=tuple(fields),
            )

        return self._scheduler.ensure_fresh(
            normalized_paths,
            max_age=max_age,
            priority=priority,
            reason=reason,
            timeout=timeout,
        )


def _normalize_paths(
    paths: FieldPath | str | Iterable[FieldPath | str],
) -> tuple[FieldPath, ...]:
    if isinstance(paths, FieldPath):
        normalized = (paths,)
    elif isinstance(paths, str):
        normalized = (FieldPath.parse(paths),)
    else:
        normalized = tuple(
            FieldPath.parse(path) if isinstance(path, str) else path for path in paths
        )
    if not normalized:
        raise ValueError("ensure_fresh requires at least one field path")
    return tuple(sorted(normalized, key=str))


def _validate_positive(value: float, *, label: str) -> None:
    if value <= 0:
        raise ValueError(f"{label} must be positive")


def _has_acquisition_hook(capability: FieldCapability) -> bool:
    return bool(
        capability.can_poll
        or capability.command_response_observable
        or capability.unsolicited_push
    )


def _select_method(capabilities: Sequence[FieldCapability]) -> AcquisitionMethod:
    if any(capability.can_poll for capability in capabilities):
        return "poll"
    if any(capability.command_response_observable for capability in capabilities):
        return "command_response"
    return "wait_for_unsolicited"


def _min_optional_timeout(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)
